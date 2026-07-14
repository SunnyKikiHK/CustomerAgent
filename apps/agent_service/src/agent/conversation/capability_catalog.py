"""Trusted capability catalog for LLM-driven conversation role selection.

The catalog gives the LLM planner a compact, trusted description of each eligible
conversation answering role so it can pick specialists semantically. It is built
from code-owned metadata plus each role's short skill description (frontmatter),
NOT the full SKILL.md body: full skill documents are execution SOPs that stay
injected only into the selected subagent at execution time.

Only conversation answering roles appear here. PLAYBOOK_RETRIEVAL is a retrieval
helper selected via a boolean, never an answer role. Signal-only roles
(HEALTH_ANALYSIS, OUTREACH_DRAFT) are never in this catalog, so the LLM planner
cannot select them.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.agent.src.subagent_types import AgentRole

from apps.agent_service.src.agent.conversation.subagents.billing import ROLE_BRIEF as BILLING_BRIEF
from apps.agent_service.src.agent.conversation.subagents.escalation import (
    ROLE_BRIEF as ESCALATION_BRIEF,
)
from apps.agent_service.src.agent.conversation.subagents.general import ROLE_BRIEF as GENERAL_BRIEF
from apps.agent_service.src.agent.conversation.subagents.technical import (
    ROLE_BRIEF as TECHNICAL_BRIEF,
)
from apps.agent_service.src.agent.runtime.skills import get_skill_manager

#: Conversation answering roles the LLM planner may choose from (the allowlist).
CONVERSATION_ROLES: tuple[AgentRole, ...] = (
    AgentRole.GENERAL,
    AgentRole.TECHNICAL,
    AgentRole.BILLING,
    AgentRole.ESCALATION,
)

#: Code-owned fallback brief per role (used if the skill description is missing).
_ROLE_BRIEF: dict[AgentRole, str] = {
    AgentRole.GENERAL: GENERAL_BRIEF,
    AgentRole.TECHNICAL: TECHNICAL_BRIEF,
    AgentRole.BILLING: BILLING_BRIEF,
    AgentRole.ESCALATION: ESCALATION_BRIEF,
}

#: Skill folder that carries each role's persona (for description lookup).
_ROLE_SKILL_DIR: dict[AgentRole, str] = {
    AgentRole.GENERAL: "general_support",
    AgentRole.TECHNICAL: "technical_support",
    AgentRole.BILLING: "billing_support",
    AgentRole.ESCALATION: "escalation_handling",
}

#: Static, trusted routing scope / exclusions / examples per role. These sharpen
#: the LLM's selection without exposing execution SOPs.
_ROLE_SCOPE: dict[AgentRole, dict[str, object]] = {
    AgentRole.GENERAL: {
        "scope": "General questions, greetings, product info, how-to, and anything not clearly owned by another specialist.",
        "exclusions": "Do not use for billing/refunds, technical faults, or explicit human-handoff requests.",
        "examples": ["How do I use this feature?", "What are your hours?", "Thanks for the help"],
    },
    AgentRole.TECHNICAL: {
        "scope": "Technical faults: errors, crashes, login failures, bugs, integration/API problems, outages.",
        "exclusions": "Do not use for pure billing questions or non-technical requests.",
        "examples": ["I get a 500 error on checkout", "The app keeps crashing", "I cannot log in"],
    },
    AgentRole.BILLING: {
        "scope": "Charges, refunds, invoices, payments, subscriptions, renewals, and fee disputes.",
        "exclusions": "Do not use for technical faults or general product questions.",
        "examples": ["I was charged twice", "I want a refund for order 123", "Cancel my subscription"],
    },
    AgentRole.ESCALATION: {
        "scope": "Customer explicitly wants a human/manager, is highly upset, threatens churn/legal, or the turn is time-critical.",
        "exclusions": "Do not use for routine questions a specialist can answer directly.",
        "examples": ["Get me a human now", "This is unacceptable, I want a manager", "I'll cancel everything"],
    },
}


@dataclass(frozen=True)
class RoleCapability:
    """One eligible answering role, described for the LLM planner."""

    role: AgentRole
    description: str
    scope: str
    exclusions: str
    examples: list[str]


def _skill_description(tenant_id: str, role: AgentRole) -> str:
    """Return the short skill description for a role, or "" if unavailable."""
    folder = _ROLE_SKILL_DIR.get(role)
    if not folder:
        return ""
    try:
        summary = get_skill_manager(tenant_id).summary()
    except Exception:
        return ""
    for entry in summary.get("skills", []):
        path = str(entry.get("path", ""))
        if f"/{folder}/" in path.replace("\\", "/"):
            return str(entry.get("description") or "").strip()
    return ""


def build_capability_catalog(tenant_id: str) -> list[RoleCapability]:
    """Build the trusted per-role capability catalog for a tenant."""
    catalog: list[RoleCapability] = []
    for role in CONVERSATION_ROLES:
        description = _skill_description(tenant_id, role) or _ROLE_BRIEF.get(role, "")
        scope = _ROLE_SCOPE.get(role, {})
        catalog.append(
            RoleCapability(
                role=role,
                description=description,
                scope=str(scope.get("scope", "")),
                exclusions=str(scope.get("exclusions", "")),
                examples=list(scope.get("examples", [])),  # type: ignore[arg-type]
            )
        )
    return catalog


def render_catalog_for_prompt(catalog: list[RoleCapability]) -> str:
    """Render the catalog as a compact enumerated block for the planner prompt."""
    lines: list[str] = []
    for cap in catalog:
        examples = "; ".join(cap.examples)
        lines.append(
            f"- role: {cap.role.value}\n"
            f"  description: {cap.description}\n"
            f"  scope: {cap.scope}\n"
            f"  exclusions: {cap.exclusions}\n"
            f"  examples: {examples}"
        )
    return "\n".join(lines)


__all__ = [
    "CONVERSATION_ROLES",
    "RoleCapability",
    "build_capability_catalog",
    "render_catalog_for_prompt",
]
