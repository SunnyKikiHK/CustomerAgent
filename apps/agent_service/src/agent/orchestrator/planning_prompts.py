"""Role prompt helpers for planner-produced subagent tasks."""

from __future__ import annotations

from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole


ROLE_SKILL_PROMPTS: dict[AgentRole, str] = {
    AgentRole.HEALTH_ANALYSIS: "Analyze customer health using only scoped account-health inputs.",
    AgentRole.PLAYBOOK_RETRIEVAL: "Retrieve and rank playbooks relevant to the signal and tenant policy.",
    AgentRole.OUTREACH_DRAFT: "Draft safe customer outreach using prior subagent markdown as evidence.",
    AgentRole.CUSTOMER_CHAT: "Answer the chat turn using bounded memory and approved read-only tools.",
    AgentRole.COMPLIANCE_CRITIC: "Review aggregated subagent outputs for policy, safety, and grounding.",
    AgentRole.ACTION_EXECUTION: "Prepare approved action payloads without bypassing reflector review.",
}


def attach_skill_prompts(plan: OrchestratorPlan) -> OrchestratorPlan:
    """Fill missing skill prompts using the shared role prompt map."""
    tasks = [
        task.model_copy(update={"skill_prompt": task.skill_prompt or ROLE_SKILL_PROMPTS[task.role]})
        for task in plan.tasks
    ]
    return plan.model_copy(update={"tasks": tasks})


__all__ = ["ROLE_SKILL_PROMPTS", "attach_skill_prompts"]
