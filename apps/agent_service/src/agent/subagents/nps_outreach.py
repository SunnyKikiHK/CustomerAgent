"""NpsOutreachAgent: customer-scoped NPS survey outreach specialist.

Ephemeral signal-side subagent. It reads a customer's survey history and health,
decides whether an NPS survey is appropriate, creates the survey record, and
drafts a short survey invitation email. It proposes the email as a
compliance-reviewed external write; it never sends directly. Score submission
and aggregate NPS are deterministic (API + nps module), never inferred here.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.NPS_OUTREACH

#: Read/light-write NPS tools plus health context. Email send is proposed as a
#: compliance-reviewed external write, not called here, so send_email is not in
#: the allow-list (drafting emits proposed_external_writes like OutreachDraft).
DEFAULT_ALLOWED_TOOLS = [
    "query_health",
    "query_nps_history",
    "create_nps_survey",
    "calculate_tenant_nps",
]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/nps_outreach/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are NpsOutreachAgent. Decide whether an NPS survey is appropriate for "
    "the customer, create the survey, and draft a short, neutral survey "
    "invitation. Never fabricate scores; aggregate NPS is computed "
    "deterministically. Emit the email as proposed_external_writes for review."
)


class NpsOutreachAgent(ReActSubagent):
    """ReAct subagent specialized for NPS survey outreach."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["NpsOutreachAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
