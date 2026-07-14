"""Conversation-only specialist subagents.

These replace the former single CustomerChatAgent with intent-routed
specialists adapted from the reference project's General/Technical/Billing
agents (English-only, tenant-safe). They answer a single chat turn using
bounded conversation memory and approved read-only tools; their output is
still gated by the ComplianceCriticAgent before it reaches the customer. None
of them perform external writes.
"""

from __future__ import annotations

from apps.agent_service.src.agent.conversation.subagents.billing import BillingAgent
from apps.agent_service.src.agent.conversation.subagents.escalation import EscalationAgent
from apps.agent_service.src.agent.conversation.subagents.general import GeneralAgent
from apps.agent_service.src.agent.conversation.subagents.technical import TechnicalAgent

__all__ = ["GeneralAgent", "TechnicalAgent", "BillingAgent", "EscalationAgent"]
