"""Specialist subagent implementations and the role-to-subagent factory."""

from __future__ import annotations

from packages.agent.src.config import AgentConfig
from packages.agent.src.subagent_types import AgentRole, SubagentContextPacket
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.subagents.base import BaseSubagent, ReActSubagent
from apps.agent_service.src.agent.subagents.customer_chat import CustomerChatAgent
from apps.agent_service.src.agent.subagents.health_analysis import HealthAnalysisAgent
from apps.agent_service.src.agent.subagents.outreach_draft import OutreachDraftAgent
from apps.agent_service.src.agent.subagents.playbook_retrieval import PlaybookRetrievalAgent

#: Maps a planner-assigned role to its ReAct specialist implementation. The
#: compliance critic is intentionally absent: it is the non-ReAct Reflector
#: phase invoked directly by the orchestrator, not delegated as a task here.
ROLE_TO_SUBAGENT: dict[AgentRole, type[ReActSubagent]] = {
    AgentRole.HEALTH_ANALYSIS: HealthAnalysisAgent,
    AgentRole.PLAYBOOK_RETRIEVAL: PlaybookRetrievalAgent,
    AgentRole.OUTREACH_DRAFT: OutreachDraftAgent,
    AgentRole.CUSTOMER_CHAT: CustomerChatAgent,
}


def build_subagent(
    *,
    packet: SubagentContextPacket,
    ctx: SessionContext,
    config: AgentConfig,
) -> BaseSubagent:
    """Instantiate the specialist subagent for a task's role.

    Falls back to the generic :class:`ReActSubagent` for roles without a
    dedicated specialist (for example ACTION_EXECUTION in Phase 1).
    """
    subagent_cls = ROLE_TO_SUBAGENT.get(packet.task.role, ReActSubagent)
    return subagent_cls(packet=packet, ctx=ctx, config=config)


__all__ = [
    "BaseSubagent",
    "ReActSubagent",
    "HealthAnalysisAgent",
    "PlaybookRetrievalAgent",
    "OutreachDraftAgent",
    "CustomerChatAgent",
    "ROLE_TO_SUBAGENT",
    "build_subagent",
]
