"""Temporal workflow for tenant QBR generation and delivery.

Deterministic coordination only:

    1. Generate the report (aggregate facts -> narrative -> compliance -> persist).
    2. Resolve the recipient CSM.
    3. Mark the report delivered (the email send itself is a compliance-gated
       action reused from the signal path; the first implementation records the
       delivery so the flow can be exercised end to end).

The heavy/nondeterministic work is in ``qbr_activities``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from apps.temporal_worker.src import qbr_activities

_QUICK_RETRY = RetryPolicy(maximum_attempts=5)


@workflow.defn
class GenerateTenantQbrWorkflow:
    """Generate and deliver a tenant QBR report."""

    @workflow.run
    async def run(self, tenant_id: str) -> dict[str, Any]:
        report = await workflow.execute_activity(
            qbr_activities.generate_tenant_qbr,
            args=[tenant_id, workflow.info().workflow_id],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_QUICK_RETRY,
        )
        if report.get("status") != "generated":
            return {"tenant_id": tenant_id, "status": report.get("status", "failed")}

        recipient = await workflow.execute_activity(
            qbr_activities.resolve_qbr_recipient,
            tenant_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK_RETRY,
        )
        recipient_email = recipient.get("recipient_email")

        await workflow.execute_activity(
            qbr_activities.mark_qbr_delivered,
            args=[tenant_id, report["report_id"], recipient_email],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_QUICK_RETRY,
        )
        return {
            "tenant_id": tenant_id,
            "status": "delivered",
            "report_id": report["report_id"],
            "recipient_email": recipient_email,
        }


__all__ = ["GenerateTenantQbrWorkflow"]
