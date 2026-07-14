"""NPS surveys, responses, and deterministic scoring (Postgres).

Two concepts kept strictly separate:

- A *response score* is a single survey answer, 0-10.
- The *aggregate NPS* is ``%promoters - %detractors`` over a set of responses,
  a value in ``-100..100``. It is computed deterministically here (never by an
  LLM) and never stored on the customer row.

Classification (standard NPS):
    promoters  = scores 9-10
    passives   = scores 7-8
    detractors = scores 0-6
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from packages.db.src import PostgresConfigError, execute, fetch_all, fetch_one

#: A response at or below this score is a detractor (0-6).
DETRACTOR_MAX = 6
#: A response at or above this score is a promoter (9-10).
PROMOTER_MIN = 9


def classify_score(score: int) -> str:
    """Return 'promoter' | 'passive' | 'detractor' for a 0-10 answer."""
    if score >= PROMOTER_MIN:
        return "promoter"
    if score <= DETRACTOR_MAX:
        return "detractor"
    return "passive"


def calculate_nps(scores: list[int]) -> dict[str, Any]:
    """Compute the aggregate NPS from a list of 0-10 response scores.

    Pure and deterministic (unit-testable without a DB). Returns the score,
    the promoter/passive/detractor counts, and the response total. NPS is
    ``round(%promoters - %detractors)``; an empty list yields nps=None.
    """
    valid = [s for s in scores if isinstance(s, int) and 0 <= s <= 10]
    total = len(valid)
    if total == 0:
        return {
            "nps": None,
            "responses": 0,
            "promoters": 0,
            "passives": 0,
            "detractors": 0,
        }
    promoters = sum(1 for s in valid if s >= PROMOTER_MIN)
    detractors = sum(1 for s in valid if s <= DETRACTOR_MAX)
    passives = total - promoters - detractors
    nps = round((promoters - detractors) / total * 100)
    return {
        "nps": nps,
        "responses": total,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
    }


async def create_survey(
    *,
    tenant_id: str,
    customer_id: str,
    expires_in_days: int = 14,
    workflow_id: str | None = None,
) -> dict[str, Any] | None:
    """Create an NPS survey row in status='created'; return it (or None on DB error)."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    try:
        row = await fetch_one(
            """
            insert into nps_surveys (tenant_id, customer_id, status, expires_at, workflow_id)
            values ($1::uuid, $2::uuid, 'created', $3, $4)
            returning id::text, customer_id::text, status, expires_at, workflow_id
            """,
            tenant_id,
            customer_id,
            expires_at,
            workflow_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    if row is None:
        return None
    return {
        "id": row["id"],
        "customer_id": row["customer_id"],
        "status": row["status"],
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
        "workflow_id": row["workflow_id"],
    }


async def mark_survey_sent(*, tenant_id: str, survey_id: str) -> bool:
    """Transition a survey to status='sent' and stamp sent_at."""
    try:
        status = await execute(
            "update nps_surveys set status = 'sent', sent_at = now()"
            " where id = $1::uuid and status = 'created'",
            survey_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return False
    return status.rsplit(" ", 1)[-1] != "0"


async def record_response(
    *,
    tenant_id: str,
    survey_id: str,
    score: int,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """Record a 0-10 response for a survey and mark the survey responded.

    Score submission comes directly through the API (never an LLM). Returns the
    stored response plus its classification, or None on DB error/invalid input.
    """
    if not (isinstance(score, int) and 0 <= score <= 10):
        raise ValueError("score must be an integer in 0..10")
    try:
        survey = await fetch_one(
            "select customer_id::text from nps_surveys where id = $1::uuid",
            survey_id,
            tenant_id=tenant_id,
        )
        if survey is None:
            return None
        row = await fetch_one(
            """
            insert into nps_responses (tenant_id, survey_id, customer_id, score, comment)
            values ($1::uuid, $2::uuid, $3::uuid, $4, $5)
            returning id::text, score, comment
            """,
            tenant_id,
            survey_id,
            survey["customer_id"],
            score,
            comment,
            tenant_id=tenant_id,
        )
        await execute(
            "update nps_surveys set status = 'responded', responded_at = now()"
            " where id = $1::uuid",
            survey_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    if row is None:
        return None
    return {
        "id": row["id"],
        "survey_id": survey_id,
        "customer_id": survey["customer_id"],
        "score": row["score"],
        "classification": classify_score(row["score"]),
        "comment": row["comment"],
    }


async def survey_history(*, tenant_id: str, customer_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return a customer's survey + response history (newest first)."""
    try:
        rows = await fetch_all(
            """
            select s.id::text as survey_id, s.status, s.sent_at, s.responded_at,
                   r.score, r.comment
            from nps_surveys s
            left join nps_responses r on r.survey_id = s.id
            where s.customer_id = $1::uuid
            order by s.created_at desc
            limit $2
            """,
            customer_id,
            limit,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    return [
        {
            "survey_id": row["survey_id"],
            "status": row["status"],
            "sent_at": row["sent_at"].isoformat() if row["sent_at"] else None,
            "responded_at": row["responded_at"].isoformat() if row["responded_at"] else None,
            "score": row["score"],
            "classification": classify_score(row["score"]) if row["score"] is not None else None,
            "comment": row["comment"],
        }
        for row in rows
    ]


async def tenant_nps(*, tenant_id: str) -> dict[str, Any]:
    """Compute the deterministic aggregate NPS across a tenant's responses."""
    try:
        rows = await fetch_all(
            "select score from nps_responses",
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return calculate_nps([])
    return calculate_nps([row["score"] for row in rows])


__all__ = [
    "DETRACTOR_MAX",
    "PROMOTER_MIN",
    "classify_score",
    "calculate_nps",
    "create_survey",
    "mark_survey_sent",
    "record_response",
    "survey_history",
    "tenant_nps",
]
