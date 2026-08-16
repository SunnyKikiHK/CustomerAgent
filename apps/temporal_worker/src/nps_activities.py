"""Temporal activities for the NPS campaign workflow.

All nondeterministic work (DB writes, survey creation, email send) lives here.
The workflow only coordinates. The survey invitation is sent through the gated
``send_email`` action path (approval -> MCP gateway -> provider) so it is never
a silent no-op; with ``EMAIL_PROVIDER=console`` it logs locally, with ``google``
it fails closed until Gmail credentials exist.
"""

from __future__ import annotations

import os
from typing import Any

from temporalio import activity

from packages.knowledge_service.src import nps
from packages.knowledge_service.src.customers import list_customers
from packages.observability.src.tracer import observe

NPS_RECENT_SURVEY_DAYS = int(os.getenv("NPS_RECENT_SURVEY_DAYS", "90"))


def _survey_url(survey_id: str) -> str:
    """Build the frontend URL where a customer submits their score."""
    base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/nps/{survey_id}"


@activity.defn
async def select_nps_candidates(tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return customers eligible for an NPS survey, capped at ``limit``.

    Skips customers surveyed within ``NPS_RECENT_SURVEY_DAYS`` days and those
    missing an email address. Over-fetches (``limit * 2``) to compensate for the
    filtering before slicing the final list to ``limit``.
    """
    with observe(
        "workflow.nps.select_candidates",
        attributes={"tenant_id": tenant_id, "limit": limit},
        kind="chain",
    ) as span:
        customers = await list_customers(tenant_id=tenant_id, limit=limit * 2)
        recently = await nps.recently_surveyed_customer_ids(
            tenant_id=tenant_id, within_days=NPS_RECENT_SURVEY_DAYS
        )
        candidates = [
            {
                "customer_id": customer["id"],
                "name": customer.get("name"),
                "email": customer.get("email"),
            }
            for customer in customers
            if customer.get("id")
            and customer.get("email")
            and customer["id"] not in recently
        ][:limit]
        span.set("candidate_count", len(candidates))
        return candidates


@activity.defn
async def create_and_send_survey(
    tenant_id: str, customer_id: str, recipient_email: str | None = None
) -> dict[str, Any]:
    """Create a survey for one customer and send the invitation email.

    Returns the survey id and response URL. The invitation is sent through the
    gated ``send_email`` path; if the customer has no email address (or the send
    fails), the survey is created but reported ``sent=False`` so the campaign
    does not claim delivery that never happened.
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

        survey_id = survey["id"]
        span.set("created", True)
        span.set("survey_id", survey_id)

        if not recipient_email:
            return {
                "created": True,
                "customer_id": customer_id,
                "survey_id": survey_id,
                "sent": False,
                "reason": "no recipient email",
            }

        from apps.temporal_worker.src.email_delivery import send_approved_email

        try:
            await send_approved_email(
                {
                    "tenant_id": tenant_id,
                    "customer_id": customer_id,
                    "recipient_email": recipient_email,
                    "subject": "How did we do? A quick 1-minute survey",
                    "body": (
                        f"Hi, we would love your feedback. Please take a moment "
                        f"to rate your experience: {_survey_url(survey_id)}"
                    ),
                },
                trace_id=f"nps:{tenant_id}:{customer_id}:{survey_id}",
            )
        except Exception as exc:  # best-effort: survey still created, send marked failed
            return {
                "created": True,
                "customer_id": customer_id,
                "survey_id": survey_id,
                "sent": False,
                "reason": type(exc).__name__,
            }

        await nps.mark_survey_sent(
            tenant_id=tenant_id,
            survey_id=survey_id,
        )
        return {
            "created": True,
            "customer_id": customer_id,
            "survey_id": survey_id,
            "sent": True,
        }


#: NPS activity function objects registered on the worker.
NPS_ACTIVITIES = [
    select_nps_candidates,
    create_and_send_survey,
]


__all__ = ["select_nps_candidates", "create_and_send_survey", "NPS_ACTIVITIES"]
