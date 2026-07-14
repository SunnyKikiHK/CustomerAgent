"""Conversation-specific plan construction (LLM-planned, deterministic fallback).

An LLM planner semantically selects the answering specialist(s) from a trusted
allowlist (general / technical / billing / escalation); trusted code alone then
converts that selection into the plan and owns tools, dependencies, fan-out, and
policy. A deterministic intent/keyword router (`_route_roles`) is the safe
fallback whenever the LLM planner fails, times out, or returns invalid output, and
critical-urgency / explicit-human-handoff turns are forced to ESCALATION in
trusted code regardless of the model. Compound turns fan out to parallel
specialists; a playbook task is added when the turn is policy/rule-bound.
Health/outreach specialists are signal-only and never appear here.
"""

from __future__ import annotations

import os

from packages.agent.src.config import AgentConfig
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole, SubagentTask
from packages.agent.src.types import LLMUsage

from apps.agent_service.src.agent.conversation.capability_catalog import (
    build_capability_catalog,
    render_catalog_for_prompt,
)
from apps.agent_service.src.agent.conversation.intent import IntentCategory, IntentResult, UrgencyLevel
from apps.agent_service.src.agent.conversation.llm_planner import PlannerDecision, select_roles
from apps.agent_service.src.agent.conversation.subagents.billing import ROLE_BRIEF as BILLING_BRIEF
from apps.agent_service.src.agent.conversation.subagents.escalation import ROLE_BRIEF as ESCALATION_BRIEF
from apps.agent_service.src.agent.conversation.subagents.general import ROLE_BRIEF as GENERAL_BRIEF
from apps.agent_service.src.agent.conversation.subagents.technical import ROLE_BRIEF as TECHNICAL_BRIEF
from apps.agent_service.src.agent.llm_client import LLMClient
from apps.agent_service.src.agent.orchestrator.policy import build_global_constraints
from apps.agent_service.src.agent.runtime.monitor import get_performance_monitor
from apps.agent_service.src.agent.subagents.playbook_retrieval import ROLE_BRIEF as PLAYBOOK_BRIEF

#: Simple turns answered by the general agent alone (fast path).
_FAST_PATH_INTENTS = {IntentCategory.GREETING, IntentCategory.FEEDBACK, IntentCategory.QUERY}

#: Intent -> primary conversation specialist role.
_INTENT_ROUTING: dict[IntentCategory, AgentRole] = {
    IntentCategory.TECHNICAL: AgentRole.TECHNICAL,
    IntentCategory.BILLING: AgentRole.BILLING,
    IntentCategory.ACCOUNT: AgentRole.BILLING,
    IntentCategory.ESCALATION: AgentRole.ESCALATION,
    IntentCategory.COMPLAINT: AgentRole.ESCALATION,
}

#: Short fallback persona per role. The full SOP is injected at prompt-build time
#: by the SkillManager from skills/<tenant>/<role>_support/SKILL.md; this brief is
#: only used when the skills dir is unavailable.
_ROLE_BRIEF: dict[AgentRole, str] = {
    AgentRole.GENERAL: GENERAL_BRIEF,
    AgentRole.TECHNICAL: TECHNICAL_BRIEF,
    AgentRole.BILLING: BILLING_BRIEF,
    AgentRole.ESCALATION: ESCALATION_BRIEF,
}

#: Read-only tools each answering role may use. Trusted code owns this map; the
#: planner (deterministic or LLM) never chooses tools. Defaults to the shared
#: read-only pair; escalation/billing add their prototype specialist tools.
_DEFAULT_ANSWER_TOOLS = ["query_health", "query_playbooks"]
_ROLE_TOOLS: dict[AgentRole, list[str]] = {
    AgentRole.ESCALATION: [*_DEFAULT_ANSWER_TOOLS, "check_human_availability"],
    AgentRole.BILLING: [*_DEFAULT_ANSWER_TOOLS, "process_refund"],
}


def _tools_for_role(role: AgentRole) -> list[str]:
    """Return the trusted allowed-tool list for an answering role."""
    return list(_ROLE_TOOLS.get(role, _DEFAULT_ANSWER_TOOLS))


#: Keyword hints that a turn touches a second domain (compound collaboration).
_TECHNICAL_KEYWORDS = ("error", "crash", "bug", "500", "401", "cannot log in", "login", "broken")
_BILLING_KEYWORDS = ("refund", "invoice", "charge", "charged", "billing", "payment", "subscription")

#: Terms that indicate the answer is governed by a documented rule/policy, so a
#: playbook retrieval task should feed the specialist.
_PLAYBOOK_HINT_KEYWORDS = (
    "refund",
    "policy",
    "cancel",
    "renewal",
    "warranty",
    "return",
    "eligible",
    "entitled",
)


def _llm_planner_enabled() -> bool:
    """Whether LLM semantic role selection is on (default on; opt-out via env)."""
    return os.getenv("CONVERSATION_LLM_PLANNER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def build_conversation_plan(
    *,
    message: str,
    intent: IntentResult,
    config: AgentConfig,
    tenant_constraints: list[str],
    memory_excerpt: str | None,
    history: list[dict[str, str]] | None = None,
    llm_client: LLMClient | None = None,
) -> tuple[OrchestratorPlan, LLMUsage]:
    """Build a conversation plan via LLM role selection with deterministic fallback."""
    monitor = get_performance_monitor()
    monitor.refresh_penalties({})
    constraints = build_global_constraints(config, tenant_constraints)

    # Fast path: simple, low-urgency turns go straight to the general agent
    # (cheap deterministic path, no LLM planner call).
    if intent.intent in _FAST_PATH_INTENTS and intent.urgency <= UrgencyLevel.MEDIUM:
        answer = _answer_task(AgentRole.GENERAL, message, intent, depends_on=[])
        return (
            OrchestratorPlan(
                goal="Answer a simple customer chat turn",
                tasks=[answer],
                requires_critic=True,
                global_constraints=constraints,
                reasoning_summary="Conversation fast path routed to GeneralAgent only",
            ),
            LLMUsage(),
        )

    roles, needs_playbook, source = await _select_roles(
        message=message,
        intent=intent,
        config=config,
        history=history,
        llm_client=llm_client,
    )
    return (
        _assemble_plan(
            message=message,
            intent=intent,
            roles=roles,
            needs_playbook=needs_playbook,
            constraints=constraints,
            source=source,
        ),
        LLMUsage(),
    )


async def _select_roles(
    *,
    message: str,
    intent: IntentResult,
    config: AgentConfig,
    history: list[dict[str, str]] | None,
    llm_client: LLMClient | None,
) -> tuple[list[AgentRole], bool, str]:
    """Select answering roles + playbook need, with trusted-code safety rails.

    Returns (roles, needs_playbook, source) where source labels how the roles
    were chosen for the plan's reasoning summary.
    """
    # Forced escalation in trusted code: critical urgency or explicit escalation
    # intent always routes to ESCALATION, regardless of any model output.
    if intent.urgency == UrgencyLevel.CRITICAL or intent.intent == IntentCategory.ESCALATION:
        return [AgentRole.ESCALATION], _needs_playbook(message, intent), "forced-escalation"

    if _llm_planner_enabled():
        decision = await _try_llm_selection(
            message=message,
            intent=intent,
            config=config,
            history=history,
            llm_client=llm_client,
        )
        if decision is not None:
            return decision.roles, decision.needs_playbook, "llm"

    # Deterministic fallback (also used when the LLM planner is disabled).
    return _route_roles(message, intent), _needs_playbook(message, intent), "deterministic"


async def _try_llm_selection(
    *,
    message: str,
    intent: IntentResult,
    config: AgentConfig,
    history: list[dict[str, str]] | None,
    llm_client: LLMClient | None,
) -> PlannerDecision | None:
    """Run the LLM planner; return None on any failure so the caller falls back."""
    try:
        catalog = build_capability_catalog(config.tenant_id)
        catalog_text = render_catalog_for_prompt(catalog)
        client = llm_client or LLMClient(default_model=config.planner_model)
        return await select_roles(
            message=message,
            history=history,
            intent=intent,
            catalog_text=catalog_text,
            llm_client=client,
            model=config.planner_model,
        )
    except Exception:  # noqa: BLE001 - any planner error must fall back
        return None


def _assemble_plan(
    *,
    message: str,
    intent: IntentResult,
    roles: list[AgentRole],
    needs_playbook: bool,
    constraints: list[str],
    source: str,
) -> OrchestratorPlan:
    """Convert a validated role selection into the trusted OrchestratorPlan.

    Trusted code owns tools, dependencies, and fan-out here: a playbook retrieval
    task is added when requested, all answer tasks depend on it when present, and
    multiple answer tasks stay mutually independent so the runtime runs them
    concurrently.
    """
    tasks: list[SubagentTask] = []
    depends_on: list[str] = []

    if needs_playbook:
        tasks.append(
            SubagentTask(
                id="playbook",
                role=AgentRole.PLAYBOOK_RETRIEVAL,
                objective="Retrieve playbook/policy guidance relevant to the chat turn",
                skill=PLAYBOOK_BRIEF,
                input={"message": message, "query": message},
                allowed_tools=["query_playbooks"],
            )
        )
        depends_on = ["playbook"]

    for role in roles:
        task_id = f"answer_{role.value}" if len(roles) > 1 else "answer"
        tasks.append(_answer_task(role, message, intent, depends_on=depends_on, task_id=task_id))

    escalated = AgentRole.ESCALATION in roles or intent.urgency == UrgencyLevel.CRITICAL
    return OrchestratorPlan(
        goal="Answer a customer chat turn with selected specialists",
        tasks=tasks,
        requires_critic=True,
        global_constraints=constraints,
        reasoning_summary=(
            f"Conversation planner ({source}) selected "
            f"{[role.value for role in roles]}"
            + (" with playbook" if depends_on else "")
            + (" (escalation)" if escalated else "")
        ),
    )


def _route_roles(message: str, intent: IntentResult) -> list[AgentRole]:
    """Return the ordered specialist role set for a turn (primary + compound)."""
    if intent.urgency == UrgencyLevel.CRITICAL:
        return [AgentRole.ESCALATION]

    primary = _INTENT_ROUTING.get(intent.intent, AgentRole.GENERAL)
    roles = [primary]

    # Compound detection: a message that also clearly touches a second domain
    # fans out to that specialist too (e.g. "login broken AND double charged").
    lowered = message.lower()
    touches_technical = any(keyword in lowered for keyword in _TECHNICAL_KEYWORDS)
    touches_billing = any(keyword in lowered for keyword in _BILLING_KEYWORDS)
    if touches_technical and AgentRole.TECHNICAL not in roles:
        roles.append(AgentRole.TECHNICAL)
    if touches_billing and AgentRole.BILLING not in roles:
        roles.append(AgentRole.BILLING)

    return roles


def _needs_playbook(message: str, intent: IntentResult) -> bool:
    lowered = message.lower()
    if any(keyword in lowered for keyword in _PLAYBOOK_HINT_KEYWORDS):
        return True
    return intent.intent in {IntentCategory.BILLING, IntentCategory.ACCOUNT}


def _answer_task(
    role: AgentRole,
    message: str,
    intent: IntentResult,
    *,
    depends_on: list[str],
    task_id: str = "answer",
) -> SubagentTask:
    return SubagentTask(
        id=task_id,
        role=role,
        objective=f"Compose the customer-facing answer for: {message}",
        skill=_ROLE_BRIEF.get(role, GENERAL_BRIEF),
        input={
            "message": message,
            "intent": intent.intent.value,
            "urgency": intent.urgency.value,
            "entities": intent.entities,
        },
        allowed_tools=_tools_for_role(role),
        depends_on=list(depends_on),
    )


__all__ = ["build_conversation_plan"]
