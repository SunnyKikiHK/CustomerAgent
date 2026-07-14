"""Temporal workflow for an NPS survey campaign.

Deterministic coordination only: select eligible customers, then create + send
one survey per customer. Waiting for responses/expiration and detractor
follow-up happen through the normal response API + signal path; the first
implementation keeps the campaign to one survey per selected customer.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from apps.temporal_worker.src import nps_activities

_QUICK_RETRY = RetryPolicy(maximum_attempts=5)


@workflow.defn
class NpsCampaignWorkflow:
    """Create and send NPS surveys to a tenant's eligible customers."""

    @workflow.run
    async def run(self, tenant_id: str) -> dict[str, Any]:
        candidates = await workflow.execute_activity(
            nps_activities.select_nps_candidates,
            tenant_id,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=_QUICK_RETRY,
        )

        sent: list[str] = []
        for candidate in candidates:
            result = await workflow.execute_activity(
                nps_activities.create_and_send_survey,
                args=[tenant_id, candidate["customer_id"]],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_QUICK_RETRY,
            )
            if result.get("created"):
                sent.append(result["survey_id"])

        return {"tenant_id": tenant_id, "candidates": len(candidates), "sent": len(sent)}


__all__ = ["NpsCampaignWorkflow"]
