"""TechnicalAgent: technical-support conversation specialist."""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.TECHNICAL

DEFAULT_ALLOWED_TOOLS = ["query_health", "query_playbooks"]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/technical_support/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are the Technical Support specialist. Give reproducible, step-by-step "
    "troubleshooting grounded in context; escalate backend operations. Read-only, "
    "no external writes."
)


class TechnicalAgent(ReActSubagent):
    """ReAct subagent for technical-support chat turns."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["TechnicalAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
