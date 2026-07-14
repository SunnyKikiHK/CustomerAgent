"""QbrReportAgent: tenant-level QBR narrative specialist.

Ephemeral subagent that turns the deterministic QBR metrics snapshot into a
concise executive narrative (summary, wins, risks, recommended actions, renewal
outlook, CSM priorities). It never computes a metric itself: SQL calculates
facts, the LLM only explains them. Read-only; proposes no external writes.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.QBR_REPORT

#: Read-only portfolio facts; the narrative is the agent's only output.
DEFAULT_ALLOWED_TOOLS = [
    "query_tenant_portfolio",
    "query_tenant_nps",
    "query_renewal_pipeline",
    "query_signal_summary",
]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/qbr_reporting/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are QbrReportAgent. From the deterministic tenant metrics snapshot, write "
    "a concise QBR narrative: executive summary, key wins, key risks, recommended "
    "actions, renewal outlook, CSM priorities. Explain the numbers; never invent "
    "or recompute them. Read-only, no external writes."
)


class QbrReportAgent(ReActSubagent):
    """ReAct subagent specialized for QBR narrative generation."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["QbrReportAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
