"""Conversation-specific plan construction."""

from __future__ import annotations

from packages.agent.src.config import AgentConfig
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole, SubagentTask
from packages.agent.src.types import LLMUsage

from apps.agent_service.src.agent.conversation.intent import IntentCategory, IntentResult, UrgencyLevel
from apps.agent_service.src.agent.orchestrator.policy import build_global_constraints
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.subagents.customer_chat import SKILL as CHAT_SKILL
from apps.agent_service.src.agent.subagents.health_analysis import SKILL as HEALTH_SKILL
from apps.agent_service.src.agent.subagents.playbook_retrieval import SKILL as PLAYBOOK_SKILL

_FAST_PATH_INTENTS = {IntentCategory.GREETING, IntentCategory.FEEDBACK, IntentCategory.QUERY}
_SPECIALIST_INTENTS = {
    IntentCategory.TECHNICAL,
    IntentCategory.BILLING,
    IntentCategory.ACCOUNT,
    IntentCategory.COMPLAINT,
    IntentCategory.ESCALATION,
}


def build_conversation_plan(
    *,
    message: str,
    intent: IntentResult,
    config: AgentConfig,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
) -> tuple[OrchestratorPlan, LLMUsage]:
    """Build a fast-path or specialist conversation plan from intent."""
    monitor = get_performance_monitor()
    monitor.refresh_penalties({})
    constraints = build_global_constraints(config, tenant_constraints)
    tasks: list[SubagentTask] = []
    chat_id = "chat"

    if intent.intent in _FAST_PATH_INTENTS and intent.urgency <= UrgencyLevel.MEDIUM:
        tasks.append(
            SubagentTask(
                id=chat_id,
                role=AgentRole.CUSTOMER_CHAT,
                objective=f"Answer the customer message: {message}",
                skill=CHAT_SKILL,
                input={"message": message, "intent": intent.intent.value, "entities": intent.entities},
                allowed_tools=["query_health", "query_playbooks"],
            )
        )
        return OrchestratorPlan(
            goal="Answer a simple customer chat turn",
            tasks=tasks,
            requires_critic=True,
            global_constraints=constraints,
            reasoning_summary="Conversation fast path routed to CustomerChatAgent only",
        ), LLMUsage()

    specialist_ids: list[str] = []
    if intent.intent in _SPECIALIST_INTENTS and monitor.get_routing_penalty(AgentRole.HEALTH_ANALYSIS.value) < 0.9:
        specialist_ids.append("health")
        tasks.append(
            SubagentTask(
                id="health",
                role=AgentRole.HEALTH_ANALYSIS,
                objective="Gather account-health evidence for the chat turn",
                skill=HEALTH_SKILL,
                input={"message": message, "intent": intent.intent.value},
                allowed_tools=["query_health"],
            )
        )

    if intent.intent in _SPECIALIST_INTENTS and monitor.get_routing_penalty(AgentRole.PLAYBOOK_RETRIEVAL.value) < 0.9:
        specialist_ids.append("playbook")
        tasks.append(
            SubagentTask(
                id="playbook",
                role=AgentRole.PLAYBOOK_RETRIEVAL,
                objective="Retrieve playbook guidance for the chat turn",
                skill=PLAYBOOK_SKILL,
                input={"message": message, "query": message},
                allowed_tools=["query_playbooks"],
            )
        )

    tasks.append(
        SubagentTask(
            id=chat_id,
            role=AgentRole.CUSTOMER_CHAT,
            objective=f"Compose the customer-facing answer for: {message}",
            skill=CHAT_SKILL,
            input={"message": message, "intent": intent.intent.value, "entities": intent.entities},
            allowed_tools=["query_health", "query_playbooks"],
            depends_on=specialist_ids,
        )
    )

    return OrchestratorPlan(
        goal="Answer a customer chat turn with optional specialists",
        tasks=tasks,
        requires_critic=True,
        global_constraints=constraints,
        reasoning_summary=(
            f"Conversation planner selected {len(tasks)} tasks for intent {intent.intent.value}"
        ),
    ), LLMUsage()


__all__ = ["build_conversation_plan"]
