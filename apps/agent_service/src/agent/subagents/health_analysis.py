"""HealthAnalysisAgent: customer health and risk specialist.

Ephemeral read-only subagent that assesses a customer's health signals
(health score, usage trend, NPS, MRR, renewal window) and produces a concise
risk summary for downstream playbook selection and outreach drafting.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.HEALTH_ANALYSIS

#: Read-only tools this role may call. Health analysis never writes.
DEFAULT_ALLOWED_TOOLS = ["query_health"]

SKILL = (
    "You are HealthAnalysisAgent, a customer-success health and risk specialist. "
    "Use only scoped account-health inputs and the query_health tool. Assess the "
    "customer's health score, usage trend, support load, NPS, MRR, and renewal "
    "window, then classify overall risk. Do not draft customer-facing content or "
    "propose external writes. Return a concise markdown summary plus structured "
    "data with keys such as risk_tier, health_score, and key_drivers."
)


class HealthAnalysisAgent(ReActSubagent):
    """Read-only ReAct subagent specialized for health/risk assessment."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = SKILL


__all__ = ["HealthAnalysisAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "SKILL"]
