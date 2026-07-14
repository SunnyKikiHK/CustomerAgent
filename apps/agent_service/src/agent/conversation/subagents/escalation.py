"""EscalationAgent: handles explicit escalation / critical-urgency turns."""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.ESCALATION

DEFAULT_ALLOWED_TOOLS = ["query_health", "query_playbooks", "check_human_availability"]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/escalation_handling/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are the Escalation specialist. De-escalate empathetically, summarize the "
    "issue accurately, and set the expectation that a human CSM will follow up. "
    "Do not over-promise. Read-only, no external writes."
)


class EscalationAgent(ReActSubagent):
    """ReAct subagent for escalation / critical chat turns."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["EscalationAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
