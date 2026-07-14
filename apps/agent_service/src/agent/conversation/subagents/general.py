"""GeneralAgent: default conversational specialist.

Handles greetings, general questions, feedback, and anything not routed to a
domain specialist. Also the fallback when a specialist path is unavailable.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.GENERAL

#: Read-only lookups only; never sends email/slack.
DEFAULT_ALLOWED_TOOLS = ["query_health", "query_playbooks"]

#: One-line fallback persona. The full role SOP lives in
#: skills/<tenant>/general_support/SKILL.md and is injected by the SkillManager
#: (role-matched). ROLE_BRIEF is only used when the skills dir is unavailable.
ROLE_BRIEF = (
    "You are the General customer-support assistant. Answer grounded in context; "
    "defer to a specialist when you lack the facts. Read-only, no external writes."
)


class GeneralAgent(ReActSubagent):
    """ReAct subagent for general customer conversation."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["GeneralAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
