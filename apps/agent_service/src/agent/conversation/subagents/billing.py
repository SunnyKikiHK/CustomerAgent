"""BillingAgent: billing / refund / subscription conversation specialist."""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.BILLING

DEFAULT_ALLOWED_TOOLS = ["query_health", "query_playbooks", "process_refund"]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/billing_support/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are the Billing specialist. Be accurate and conservative about charges, "
    "refunds, invoices, and subscriptions; route real money movement to human "
    "review. Read-only, no external writes."
)


class BillingAgent(ReActSubagent):
    """ReAct subagent for billing-related chat turns."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["BillingAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
