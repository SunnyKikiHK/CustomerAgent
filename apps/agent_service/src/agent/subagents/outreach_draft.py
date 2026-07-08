"""OutreachDraftAgent: customer-facing outreach draft specialist.

Ephemeral subagent that drafts safe, grounded customer outreach (email or
Slack) using prior subagent evidence. It proposes external writes as payloads
for the Reflector phase; it never releases customer-visible content on its own.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.OUTREACH_DRAFT

#: Outreach may propose writes, but release is gated by the compliance critic.
DEFAULT_ALLOWED_TOOLS = ["send_email", "send_slack"]

SKILL_PROMPT = (
    "You are OutreachDraftAgent, a customer-facing outreach specialist. Draft "
    "safe, personalized, factually grounded outreach using prior subagent "
    "markdown as evidence. Every customer-visible claim must be supported by "
    "provided context. Do not invent data, do not include raw PII beyond the "
    "approved recipient, and do not send anything directly: emit proposed "
    "external writes as structured payloads for compliance review. Return a "
    "markdown draft plus structured data under proposed_external_writes."
)


class OutreachDraftAgent(ReActSubagent):
    """ReAct subagent specialized for drafting customer outreach."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill_prompt = SKILL_PROMPT


__all__ = ["OutreachDraftAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "SKILL_PROMPT"]
