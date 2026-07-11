"""Signal-specific plan construction."""

from __future__ import annotations

from packages.agent.src.config import AgentConfig
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole, SubagentTask
from packages.agent.src.types import CustomerSignal, LLMUsage

from apps.agent_service.src.agent.orchestrator.policy import build_global_constraints
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.subagents.health_analysis import SKILL as HEALTH_SKILL
from apps.agent_service.src.agent.subagents.outreach_draft import SKILL as OUTREACH_SKILL
from apps.agent_service.src.agent.subagents.playbook_retrieval import SKILL as PLAYBOOK_SKILL


def build_signal_plan(
    *,
    signal: CustomerSignal,
    config: AgentConfig,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
) -> tuple[OrchestratorPlan, LLMUsage]:
    """Map a customer signal to a dependency-aware subagent plan."""
    monitor = get_performance_monitor()
    monitor.refresh_penalties({})
    constraints = build_global_constraints(config, tenant_constraints)

    health_id = "health"
    playbook_id = "playbook"
    outreach_id = "outreach"
    tasks: list[SubagentTask] = []

    if monitor.get_routing_penalty(AgentRole.HEALTH_ANALYSIS.value) < 0.9:
        tasks.append(
            SubagentTask(
                id=health_id,
                role=AgentRole.HEALTH_ANALYSIS,
                objective=f"Analyze account health for signal: {signal.signal_text}",
                skill=HEALTH_SKILL,
                input={"signal_type": signal.type, "payload": signal.payload},
                allowed_tools=["query_health"],
            )
        )

    if monitor.get_routing_penalty(AgentRole.PLAYBOOK_RETRIEVAL.value) < 0.9:
        tasks.append(
            SubagentTask(
                id=playbook_id,
                role=AgentRole.PLAYBOOK_RETRIEVAL,
                objective=f"Retrieve playbooks for signal: {signal.signal_text}",
                skill=PLAYBOOK_SKILL,
                input={"signal_type": signal.type, "query": signal.signal_text},
                allowed_tools=["query_playbooks"],
                depends_on=[health_id] if any(task.id == health_id for task in tasks) else [],
            )
        )

    outreach_depends = [task.id for task in tasks]
    if monitor.get_routing_penalty(AgentRole.OUTREACH_DRAFT.value) < 0.9:
        tasks.append(
            SubagentTask(
                id=outreach_id,
                role=AgentRole.OUTREACH_DRAFT,
                objective="Draft proactive outreach using health and playbook evidence",
                skill=OUTREACH_SKILL,
                input={"signal_type": signal.type, "memory_excerpt": memory_excerpt},
                allowed_tools=["send_email", "send_slack"],
                depends_on=outreach_depends,
            )
        )

    if not tasks:
        tasks.append(
            SubagentTask(
                id="fallback_health",
                role=AgentRole.HEALTH_ANALYSIS,
                objective=f"Fallback health analysis for signal: {signal.signal_text}",
                skill=HEALTH_SKILL,
                input={"signal_type": signal.type},
                allowed_tools=["query_health"],
            )
        )

    return OrchestratorPlan(
        goal=f"Respond to signal {signal.type}",
        tasks=tasks,
        requires_critic=True,
        global_constraints=constraints,
        reasoning_summary=f"Signal planner selected {len(tasks)} tasks for {signal.type}",
    ), LLMUsage()


__all__ = ["build_signal_plan"]
