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

#: One-line fallback persona; full SOP in
#: skills/<tenant>/health_analysis/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are HealthAnalysisAgent. Use only query_health to assess health score, "
    "usage trend, support load, NPS, MRR, and renewal window, then classify risk. "
    "Read-only; no customer-facing content or external writes."
)


class HealthAnalysisAgent(ReActSubagent):
    """Read-only ReAct subagent specialized for health/risk assessment."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["HealthAnalysisAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
