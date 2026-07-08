"""CustomerChatAgent: customer conversation specialist.

Ephemeral subagent that answers a single customer chat turn using bounded
conversation memory and approved read-only tools. Its output is still routed
through the compliance critic before it reaches the customer.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.CUSTOMER_CHAT

#: Chat answers using read-only lookups; it does not send email/slack itself.
DEFAULT_ALLOWED_TOOLS = ["query_health", "query_playbooks"]

SKILL_PROMPT = (
    "You are CustomerChatAgent, a customer conversation specialist. Answer the "
    "current chat turn using the bounded memory excerpt, prior subagent evidence, "
    "and approved read-only tools. Stay grounded in provided context, keep a "
    "helpful professional tone, and never expose another tenant's data or raw "
    "secrets. Do not perform external writes. Return a markdown reply plus "
    "structured data capturing any follow-up actions you recommend."
)


class CustomerChatAgent(ReActSubagent):
    """ReAct subagent specialized for answering a customer chat turn."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill_prompt = SKILL_PROMPT


__all__ = ["CustomerChatAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "SKILL_PROMPT"]
