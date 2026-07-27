"""Temporal activities for the NPS campaign workflow.

All nondeterministic work (DB writes, survey creation, marking sent) lives here.
The workflow only coordinates. Sending the invitation email reuses the existing
signal path is out of scope here: for the first implementation the campaign
creates + marks surveys sent, and the outreach agent / email happen through the
normal signal/compliance path when wired. Kept deliberately small and testable.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity

from packages.knowledge_service.src import nps
from packages.knowledge_service.src.customers import list_customers
from packages.observability.src.tracer import observe


@activity.defn
async def select_nps_candidates(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return customers eligible for an NPS survey (simple: all, capped).

    A richer policy (skip recently surveyed, only healthy-enough, etc.) can be
    layered later; the deterministic selection keeps the first campaign simple.
    """
    with observe(
        "workflow.nps.select_candidates",
        attributes={"tenant_id": tenant_id, "limit": limit},
        kind="chain",
    ) as span:
        customers = await list_customers(tenant_id=tenant_id, limit=limit)
        candidates = [
            {"customer_id": customer["id"], "name": customer.get("name")}
            for customer in customers
            if customer.get("id")
        ]
        span.set("candidate_count", len(candidates))
        return candidates


@activity.defn
async def create_and_send_survey(tenant_id: str, customer_id: str) -> dict[str, Any]:
    """Create a survey for one customer and mark it sent (mock send).

    Returns the survey id and response URL. The actual email send is a
    compliance-reviewed external write in the signal path; for the campaign's
    first implementation the survey is created and marked sent so the response
    flow can be exercised end to end.
    """
    with observe(
        "workflow.nps.create_survey",
        attributes={"tenant_id": tenant_id, "customer_id": customer_id},
        kind="chain",
    ) as span:
        survey = await nps.create_survey(
            tenant_id=tenant_id,
            customer_id=customer_id,
        )
        if survey is None:
            span.set("created", False)
            return {"created": False, "customer_id": customer_id}
        await nps.mark_survey_sent(
            tenant_id=tenant_id,
            survey_id=survey["id"],
        )
        span.set("created", True)
        span.set("survey_id", survey["id"])
        return {
            "created": True,
            "customer_id": customer_id,
            "survey_id": survey["id"],
        }


#: NPS activity function objects registered on the worker.
NPS_ACTIVITIES = [
    select_nps_candidates,
    create_and_send_survey,
]


__all__ = ["select_nps_candidates", "create_and_send_survey", "NPS_ACTIVITIES"]
