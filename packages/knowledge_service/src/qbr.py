"""QBR aggregation, scoring, and persistence (Postgres).

The rule QBR follows: **SQL calculates facts; the LLM only explains them.** This
module owns the deterministic half — it aggregates a tenant's portfolio into a
metrics snapshot (customer counts, MRR/ARR, health distribution, renewal
pipeline, NPS, open signals) and persists the generated report. The narrative is
produced separately by the QBR subagent from this snapshot.

All queries are tenant-scoped through the RLS-aware db helpers. Every aggregate
degrades to a zero/empty value when the DB is unavailable so a QBR run never
crashes on missing data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from packages.db.src import PostgresConfigError, execute, fetch_all, fetch_one
from packages.knowledge_service.src.nps import calculate_nps

#: Health bands used for the portfolio health distribution.
_HEALTHY_MIN = 70.0
_AT_RISK_MIN = 50.0


def _health_band(score: float | None) -> str:
    """Classify a health score into healthy / at_risk / critical / unknown."""
    if score is None:
        return "unknown"
    if score >= _HEALTHY_MIN:
        return "healthy"
    if score >= _AT_RISK_MIN:
        return "at_risk"
    return "critical"


async def aggregate_portfolio(*, tenant_id: str) -> dict[str, Any]:
    """Aggregate the deterministic QBR facts for a tenant.

    Returns a JSON-safe metrics snapshot. Never raises on DB issues: each section
    degrades to its empty form so the QBR agent still gets a well-formed snapshot.
    """
    customers = await _safe_rows(
        "select health_score, mrr, renewal_date from customers", tenant_id
    )
    total = len(customers)
    mrr_total = sum(float(r["mrr"]) for r in customers if r["mrr"] is not None)
    distribution = {"healthy": 0, "at_risk": 0, "critical": 0, "unknown": 0}
    for row in customers:
        score = float(row["health_score"]) if row["health_score"] is not None else None
        distribution[_health_band(score)] += 1

    renewals = _renewal_pipeline(customers)
    nps_summary = await _tenant_nps(tenant_id)
    signals = await _open_signal_summary(tenant_id)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "customers": total,
        "mrr_total": round(mrr_total, 2),
        "arr_total": round(mrr_total * 12, 2),
        "health_distribution": distribution,
        "renewal_pipeline": renewals,
        "nps": nps_summary,
        "open_signals": signals,
    }


def _renewal_pipeline(customers: list[Any]) -> dict[str, int]:
    """Bucket renewals into the next 30/60/90 day windows (deterministic)."""
    today = date.today()
    buckets = {"next_30": 0, "next_60": 0, "next_90": 0}
    for row in customers:
        renewal = row["renewal_date"]
        if renewal is None:
            continue
        days = (renewal - today).days
        if days < 0:
            continue
        if days <= 30:
            buckets["next_30"] += 1
        elif days <= 60:
            buckets["next_60"] += 1
        elif days <= 90:
            buckets["next_90"] += 1
    return buckets


async def _tenant_nps(tenant_id: str) -> dict[str, Any]:
    rows = await _safe_rows("select score from nps_responses", tenant_id)
    return calculate_nps([r["score"] for r in rows])


async def _open_signal_summary(tenant_id: str) -> dict[str, Any]:
    """Count open (non-done) signals grouped by type."""
    rows = await _safe_rows(
        "select type, count(*) as n from signals"
        " where status in ('queued', 'processing', 'failed') group by type",
        tenant_id,
    )
    by_type = {row["type"]: int(row["n"]) for row in rows}
    return {"total": sum(by_type.values()), "by_type": by_type}


async def _safe_rows(query: str, tenant_id: str) -> list[Any]:
    try:
        return await fetch_all(query, tenant_id=tenant_id)
    except PostgresConfigError:
        return []
    except Exception:
        return []


async def create_report(
    *,
    tenant_id: str,
    period_start: date,
    period_end: date,
    metrics_snapshot: dict[str, Any],
    workflow_id: str | None = None,
) -> dict[str, Any] | None:
    """Insert a draft QBR report with its metrics snapshot; return it."""
    import json

    try:
        row = await fetch_one(
            """
            insert into qbr_reports
                (tenant_id, period_start, period_end, status, metrics_snapshot, workflow_id)
            values ($1::uuid, $2, $3, 'draft', $4::jsonb, $5)
            returning id::text, status
            """,
            tenant_id,
            period_start,
            period_end,
            json.dumps(metrics_snapshot),
            workflow_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    if row is None:
        return None
    return {"id": row["id"], "status": row["status"]}


async def attach_narrative(
    *, tenant_id: str, report_id: str, report_markdown: str
) -> bool:
    """Store the generated narrative and stamp generated_at."""
    try:
        status = await execute(
            "update qbr_reports set report_markdown = $2, generated_at = now()"
            " where id = $1::uuid",
            report_id,
            report_markdown,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return False
    return status.rsplit(" ", 1)[-1] != "0"


async def mark_report_status(
    *,
    tenant_id: str,
    report_id: str,
    status: str,
    recipient_email: str | None = None,
    error: str | None = None,
) -> bool:
    """Transition a report's status (approved/delivered/failed)."""
    try:
        result = await execute(
            """
            update qbr_reports
            set status = $2,
                recipient_email = coalesce($3, recipient_email),
                error = $4,
                delivered_at = case when $2 = 'delivered' then now() else delivered_at end
            where id = $1::uuid
            """,
            report_id,
            status,
            recipient_email,
            error,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return False
    return result.rsplit(" ", 1)[-1] != "0"


async def get_report(*, tenant_id: str, report_id: str) -> dict[str, Any] | None:
    """Return one QBR report row (JSON-safe)."""
    try:
        row = await fetch_one(
            """
            select id::text, period_start, period_end, status, metrics_snapshot,
                   report_markdown, recipient_email, error,
                   generated_at, delivered_at, created_at
            from qbr_reports where id = $1::uuid
            """,
            report_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    return _row_to_report(row) if row else None


async def list_reports(*, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent QBR reports for a tenant (newest first)."""
    try:
        rows = await fetch_all(
            """
            select id::text, period_start, period_end, status, metrics_snapshot,
                   report_markdown, recipient_email, error,
                   generated_at, delivered_at, created_at
            from qbr_reports order by created_at desc limit $1
            """,
            limit,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    return [_row_to_report(row) for row in rows]


def _row_to_report(row: Any) -> dict[str, Any]:
    import json

    snapshot = row["metrics_snapshot"]
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            snapshot = {}
    return {
        "id": row["id"],
        "period_start": row["period_start"].isoformat() if row["period_start"] else None,
        "period_end": row["period_end"].isoformat() if row["period_end"] else None,
        "status": row["status"],
        "metrics_snapshot": snapshot or {},
        "report_markdown": row["report_markdown"],
        "recipient_email": row["recipient_email"],
        "error": row["error"],
        "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
        "delivered_at": row["delivered_at"].isoformat() if row["delivered_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


__all__ = [
    "aggregate_portfolio",
    "create_report",
    "attach_narrative",
    "mark_report_status",
    "get_report",
    "list_reports",
]
