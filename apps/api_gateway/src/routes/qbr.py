"""QBR API: generate, list, and fetch tenant quarterly business reports.

Generation runs the ``GenerateTenantQbrWorkflow`` via Temporal, falling back to
the ReportingOrchestrator inline when Temporal is unreachable (so the endpoint
works in local/offline dev). All routes require tenant-authorized access; only a
write role may trigger generation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from packages.auth.src.models import AuthContext
from packages.knowledge_service.src import qbr

from apps.api_gateway.src.security import require_write, tenant_context

router = APIRouter(tags=["qbr"])


@router.post("/qbr/generate")
async def generate_qbr_route(auth: AuthContext = Depends(require_write)) -> dict[str, Any]:
    """Trigger QBR generation for the authorized tenant.

    Starts the Temporal workflow; if Temporal is unreachable, generates inline
    through the ReportingOrchestrator so the report is still produced.
    """
    tenant_id = auth.tenant_id
    try:
        from temporalio.client import Client

        from apps.temporal_worker.src.client import task_queue, temporal_namespace, temporal_target
        from apps.temporal_worker.src.qbr_workflows import GenerateTenantQbrWorkflow

        client = await Client.connect(temporal_target(), namespace=temporal_namespace())
        handle = await client.start_workflow(
            GenerateTenantQbrWorkflow.run,
            tenant_id,
            id=f"qbr:{tenant_id}:{_period_key()}",
            task_queue=task_queue(),
        )
        return {"mode": "temporal", "workflow_id": handle.id}
    except Exception:
        return await _generate_inline(tenant_id)


async def _generate_inline(tenant_id: str) -> dict[str, Any]:
    """Fallback: generate the QBR inline through the ReportingOrchestrator."""
    from packages.agent.src.types import SessionContext

    from apps.agent_service.src.agent.reporting.reporting_orchestrator import generate_qbr

    ctx = SessionContext(
        tenant_id=tenant_id,
        user_id="qbr-api",
        session_id=f"qbr:{tenant_id}",
        trace_id=f"qbr:{tenant_id}",
    )
    result = await generate_qbr(tenant_id=tenant_id, ctx=ctx)
    return {"mode": "inprocess", **result}


@router.get("/qbr/reports")
async def list_qbr_reports(
    limit: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(tenant_context),
) -> dict[str, Any]:
    """List recent QBR reports for the authorized tenant."""
    return {"reports": await qbr.list_reports(tenant_id=auth.tenant_id, limit=limit)}


@router.get("/qbr/reports/{report_id}")
async def get_qbr_report(
    report_id: str,
    auth: AuthContext = Depends(tenant_context),
) -> dict[str, Any]:
    """Fetch one QBR report (metrics snapshot + narrative)."""
    report = await qbr.get_report(tenant_id=auth.tenant_id, report_id=report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report


def _period_key() -> str:
    """Coarse period key so repeat generations in a quarter share a workflow id."""
    from datetime import date

    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    return f"{today.year}Q{quarter}"


__all__ = ["router"]
