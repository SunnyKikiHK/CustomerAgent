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
async def deliver_qbr_email(
    tenant_id: str,
    report_id: str,
    report_markdown: str,
    recipient_email: str | None,
) -> dict[str, Any]:
    """Send the QBR report via the gated email path, then mark it delivered.

    Routes the report through ``send_approved_email`` (approval -> MCP gateway ->
    provider) instead of only stamping the report delivered. A missing recipient
    or a failed send marks the report failed with the reason so delivery state
    always reflects whether an email actually went out.
    """
    from apps.temporal_worker.src.email_delivery import send_approved_email
    from packages.knowledge_service.src import qbr

    if not recipient_email:
        await qbr.mark_report_status(
            tenant_id=tenant_id,
            report_id=report_id,
            status="failed",
            error="no QBR recipient resolved",
        )
        return {"status": "failed", "reason": "no recipient"}
    try:
        result = await send_approved_email(
            {
                "tenant_id": tenant_id,
                "customer_id": tenant_id,
                "recipient_email": recipient_email,
                "subject": f"Your quarterly business review (report {report_id[:8]})",
                "body": report_markdown,
            },
            trace_id=f"qbr:{tenant_id}:{report_id}",
        )
    except Exception as exc:  # best-effort: a failed send must not crash the workflow
        await qbr.mark_report_status(
            tenant_id=tenant_id,
            report_id=report_id,
            status="failed",
            error=f"email delivery failed: {type(exc).__name__}",
        )
        return {"status": "failed", "reason": type(exc).__name__}
    await qbr.mark_report_status(
        tenant_id=tenant_id,
        report_id=report_id,
        status="delivered",
        recipient_email=recipient_email,
    )
    return {"status": "delivered", "provider_message_id": result.get("provider_message_id")}


#: QBR activity function objects registered on the worker.
QBR_ACTIVITIES = [
    generate_tenant_qbr,
    resolve_qbr_recipient,
    deliver_qbr_email,
]


__all__ = [
    "generate_tenant_qbr",
    "resolve_qbr_recipient",
    "deliver_qbr_email",
    "QBR_ACTIVITIES",
]
