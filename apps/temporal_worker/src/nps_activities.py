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


@activity.defn
async def select_nps_candidates(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return customers eligible for an NPS survey (simple: all, capped).

    A richer policy (skip recently surveyed, only healthy-enough, etc.) can be
    layered later; the deterministic selection keeps the first campaign simple.
    """
    customers = await list_customers(tenant_id=tenant_id, limit=limit)
    return [{"customer_id": c["id"], "name": c.get("name")} for c in customers if c.get("id")]


@activity.defn
async def create_and_send_survey(tenant_id: str, customer_id: str) -> dict[str, Any]:
    """Create a survey for one customer and mark it sent (mock send).

    Returns the survey id and response URL. The actual email send is a
    compliance-reviewed external write in the signal path; for the campaign's
    first implementation the survey is created and marked sent so the response
    flow can be exercised end to end.
    """
    survey = await nps.create_survey(tenant_id=tenant_id, customer_id=customer_id)
    if survey is None:
        return {"created": False, "customer_id": customer_id}
    await nps.mark_survey_sent(tenant_id=tenant_id, survey_id=survey["id"])
    return {"created": True, "customer_id": customer_id, "survey_id": survey["id"]}


#: NPS activity function objects registered on the worker.
NPS_ACTIVITIES = [
    select_nps_candidates,
    create_and_send_survey,
]


__all__ = ["select_nps_candidates", "create_and_send_survey", "NPS_ACTIVITIES"]
