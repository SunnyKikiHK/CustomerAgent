"""Temporal activities for the tenant QBR workflow.

All nondeterministic work lives here: aggregating + generating the report (via
the ReportingOrchestrator), resolving the recipient CSM, and sending the report
email through the same compliance-gated action path. The workflow only
coordinates these steps.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity

from packages.agent.src.types import SessionContext
from packages.observability.src.tracer import observe


@activity.defn
async def generate_tenant_qbr(tenant_id: str, workflow_id: str | None = None) -> dict[str, Any]:
    """Aggregate facts, generate the narrative, and persist a QBR report."""
    from apps.agent_service.src.agent.reporting.reporting_orchestrator import generate_qbr

    ctx = SessionContext(
        tenant_id=tenant_id,
        user_id="qbr-workflow",
        session_id=f"qbr:{tenant_id}",
        trace_id=workflow_id or f"qbr:{tenant_id}",
    )
    with observe(
        "workflow.qbr.generate",
        attributes={"tenant_id": tenant_id, "trace_id": ctx.trace_id},
        kind="chain",
    ) as span:
        report = await generate_qbr(
            tenant_id=tenant_id,
            ctx=ctx,
            workflow_id=workflow_id,
        )
        span.set("status", report.get("status"))
        span.set("report_id", report.get("report_id"))
        return report


@activity.defn
async def resolve_qbr_recipient(tenant_id: str) -> dict[str, Any]:
    """Resolve the CSM email that should receive the QBR (best-effort)."""
    from packages.auth.src.store import list_qbr_recipients

    try:
        recipients = await list_qbr_recipients(tenant_id)
    except Exception:
        recipients = []
    if not recipients:
        return {"recipient_email": None}
    return {"recipient_email": recipients[0].email, "recipient_count": len(recipients)}


@activity.defn
async def mark_qbr_delivered(
    tenant_id: str, report_id: str, recipient_email: str | None
) -> None:
    """Mark a QBR report delivered to its recipient."""
    from packages.knowledge_service.src import qbr

    await qbr.mark_report_status(
        tenant_id=tenant_id,
        report_id=report_id,
        status="delivered",
        recipient_email=recipient_email,
    )


#: QBR activity function objects registered on the worker.
QBR_ACTIVITIES = [
    generate_tenant_qbr,
    resolve_qbr_recipient,
    mark_qbr_delivered,
]


__all__ = [
    "generate_tenant_qbr",
    "resolve_qbr_recipient",
    "mark_qbr_delivered",
    "QBR_ACTIVITIES",
]
