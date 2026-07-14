"""PlaybookRetrievalAgent: playbook and knowledge (RAG) specialist.

Ephemeral read-only subagent that retrieves and ranks tenant-scoped playbooks
or knowledge snippets relevant to the current signal, then hands the ranked
evidence to downstream drafting or chat subagents.
"""

from __future__ import annotations

from apps.agent_service.src.agent.subagents.base import ReActSubagent
from packages.agent.src.subagent_types import AgentRole

ROLE = AgentRole.PLAYBOOK_RETRIEVAL

#: Read-only knowledge retrieval only. This role never writes.
DEFAULT_ALLOWED_TOOLS = ["query_playbooks"]

#: One-line fallback persona; full SOP in
#: skills/<tenant>/playbook_retrieval/SKILL.md (role-matched injection).
ROLE_BRIEF = (
    "You are PlaybookRetrievalAgent. Use only query_playbooks to retrieve and "
    "rank tenant-scoped playbooks relevant to the signal/turn and prior health "
    "findings. Read-only; never cross tenants."
)


class PlaybookRetrievalAgent(ReActSubagent):
    """Read-only ReAct subagent specialized for playbook/RAG retrieval."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = ROLE_BRIEF


__all__ = ["PlaybookRetrievalAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "ROLE_BRIEF"]
