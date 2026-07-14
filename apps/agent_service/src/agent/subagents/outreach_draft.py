"""OutreachDraftAgent: customer-facing outreach draft specialist.

Ephemeral subagent that drafts safe, grounded customer outreach (email or
Slack) using prior subagent evidence. It proposes external writes as payloads
for the Reflector phase; it never releases customer-visible content on its own.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.OUTREACH_DRAFT

#: Drafting proposes structured writes; only the post-compliance release hook executes them.
DEFAULT_ALLOWED_TOOLS: list[str] = []

#: One-line fallback persona; full SOP in
#: skills/<tenant>/outreach_drafting/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are OutreachDraftAgent. Draft safe, grounded, personalized outreach from "
    "prior subagent evidence; every claim must be supported. Do not send — emit "
    "proposed_external_writes for compliance review."
)


class OutreachDraftAgent(ReActSubagent):
    """ReAct subagent specialized for drafting customer outreach."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["OutreachDraftAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
