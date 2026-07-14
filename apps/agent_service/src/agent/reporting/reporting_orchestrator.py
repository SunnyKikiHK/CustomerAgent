"""ReportingOrchestrator: tenant-level QBR generation.

QBR is not customer-scoped — it operates over a whole tenant portfolio — so it
does not fit the customer-keyed Planner->Executor->Reflector base. This is a
lighter, purpose-built flow:

    1. Aggregate deterministic portfolio facts (SQL, never an LLM).
    2. Persist a draft report row carrying the metrics snapshot (reproducible).
    3. Run the QBR subagent to write the narrative from that snapshot.
    4. Compliance-review the narrative before it is persisted/sent.
    5. On approval, attach the narrative and mark the report generated;
       otherwise mark it failed with the reason.

Sending the report by email is a separate, compliance-gated step owned by the
QBR workflow (so a draft can be reviewed before delivery).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from packages.agent.src.config import AgentConfig
from packages.agent.src.models import planner_model, worker_model
from packages.agent.src.orchestration_types import OrchestratorPlan, SignalAgentInput
from packages.agent.src.subagent_types import (
    AgentRole,
    SubagentContextPacket,
    SubagentTask,
)
from packages.agent.src.types import CustomerSignal, SessionContext
from packages.knowledge_service.src import qbr
from packages.observability.src.tracer import observe

from apps.agent_service.src.agent.subagents import build_subagent
from apps.agent_service.src.agent.subagents.compliance_critic import run_compliance_critic
from apps.agent_service.src.agent.subagents.qbr_report import DEFAULT_ALLOWED_TOOLS, ROLE_BRIEF


def _qbr_config(tenant_id: str) -> AgentConfig:
    """Config for the QBR run: critic always on (a report is customer-visible)."""
    return AgentConfig(
        tenant_id=tenant_id,
        name="qbr-reporting",
        instructions="Tenant quarterly business review generation",
        model=worker_model(),
        planner_model=planner_model(),
        tools=list(DEFAULT_ALLOWED_TOOLS),
        skip_critic_for_simple=False,
    )


async def generate_qbr(
    *,
    tenant_id: str,
    ctx: SessionContext,
    period_days: int = 90,
    workflow_id: str | None = None,
) -> dict[str, Any]:
    """Generate a QBR for one tenant and return the persisted report summary.

    Returns a dict with the report id, status ('generated' or 'failed'), and the
    metrics snapshot. Never raises on a subagent/critic failure: the report row
    is marked failed with the reason instead.
    """
    with observe("qbr.generate", attributes={"tenant_id": tenant_id, "trace_id": ctx.trace_id}):
        period_end = date.today()
        period_start = period_end - timedelta(days=period_days)

        with observe("qbr.aggregate_metrics", attributes={"tenant_id": tenant_id}):
            snapshot = await qbr.aggregate_portfolio(tenant_id=tenant_id)

        report = await qbr.create_report(
            tenant_id=tenant_id,
            period_start=period_start,
            period_end=period_end,
            metrics_snapshot=snapshot,
            workflow_id=workflow_id,
        )
        if report is None:
            return {"status": "failed", "error": "database unavailable", "snapshot": snapshot}
        report_id = report["id"]

        # Run the QBR subagent to write the narrative from the snapshot.
        task = SubagentTask(
            id="qbr_narrative",
            role=AgentRole.QBR_REPORT,
            objective="Write the tenant QBR narrative from the metrics snapshot",
            skill=ROLE_BRIEF,
            input={"tenant_id": tenant_id, "metrics_snapshot": snapshot},
            allowed_tools=list(DEFAULT_ALLOWED_TOOLS),
        )
        packet = SubagentContextPacket(
            tenant_id=tenant_id,
            customer_id=tenant_id,  # tenant-level run: no single customer
            trace_id=ctx.trace_id,
            task=task,
            dependency_data={"metrics_snapshot": snapshot},
        )
        config = _qbr_config(tenant_id)

        with observe("subagent.qbr_report", attributes={"tenant_id": tenant_id}):
            subagent = build_subagent(packet=packet, ctx=ctx, config=config, domain="reporting")
            result = await subagent.run()

        if not result.success or not result.markdown:
            await qbr.mark_report_status(
                tenant_id=tenant_id,
                report_id=report_id,
                status="failed",
                error="QBR narrative generation failed",
            )
            return {"status": "failed", "report_id": report_id, "error": "narrative generation failed"}

        # Compliance review before the narrative is persisted as the report body.
        plan = OrchestratorPlan(
            goal="Generate tenant QBR",
            tasks=[task],
            requires_critic=True,
            reasoning_summary="QBR narrative from deterministic metrics snapshot",
        )
        review_input = SignalAgentInput(
            tenant_id=tenant_id,
            customer_id=tenant_id,
            signal=CustomerSignal(tenant_id=tenant_id, customer_id=tenant_id, type="qbr_report"),
        )
        with observe("compliance.review", attributes={"tenant_id": tenant_id, "phase": "qbr"}):
            review, _ = await run_compliance_critic(
                agent_input=review_input,
                plan=plan,
                results=[result],
                ctx=ctx,
                config=config,
                proposed_external_writes=[],
            )

        if not review.approved:
            await qbr.mark_report_status(
                tenant_id=tenant_id,
                report_id=report_id,
                status="failed",
                error=f"compliance blocked: {review.feedback[:300]}",
            )
            return {"status": "failed", "report_id": report_id, "error": "compliance blocked"}

        await qbr.attach_narrative(
            tenant_id=tenant_id, report_id=report_id, report_markdown=result.markdown
        )
        return {
            "status": "generated",
            "report_id": report_id,
            "snapshot": snapshot,
            "report_markdown": result.markdown,
        }


__all__ = ["generate_qbr"]
