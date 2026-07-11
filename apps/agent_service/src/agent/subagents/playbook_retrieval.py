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

SKILL = (
    "You are PlaybookRetrievalAgent, a retrieval-augmented knowledge specialist. "
    "Use only the query_playbooks tool to retrieve tenant-scoped playbooks and "
    "knowledge snippets relevant to the signal and prior health findings. Never "
    "retrieve or expose playbooks from another tenant. Rank matches by relevance "
    "and return a concise markdown summary plus structured data with a "
    "ranked list of playbook matches (id, title, why_relevant)."
)


class PlaybookRetrievalAgent(ReActSubagent):
    """Read-only ReAct subagent specialized for playbook/RAG retrieval."""

    role = ROLE
    default_allowed_tools = DEFAULT_ALLOWED_TOOLS
    skill = SKILL


__all__ = ["PlaybookRetrievalAgent", "ROLE", "DEFAULT_ALLOWED_TOOLS", "SKILL"]
