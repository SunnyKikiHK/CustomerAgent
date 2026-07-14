"""Specialist subagent implementations and the domain-aware role factory.

Roles are partitioned by domain so neither top-level system can instantiate the
other's specialists:

- Signal-only:       HEALTH_ANALYSIS, OUTREACH_DRAFT
- Conversation-only: GENERAL, TECHNICAL, BILLING, ESCALATION
- Shared:            PLAYBOOK_RETRIEVAL

The compliance critic is intentionally absent: it is the non-ReAct Reflector
phase invoked directly by the orchestrator, not delegated as a task.

Conversation specialists are imported lazily inside the factory. They live under
``conversation/subagents/`` and import :class:`ReActSubagent` from
``subagents.base``; importing them eagerly here would create a package-init
cycle (base <-> conversation-subagent).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from packages.agent.src.config import AgentConfig
from packages.agent.src.subagent_types import AgentRole, SubagentContextPacket
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.subagents.base import BaseSubagent, ReActSubagent
from apps.agent_service.src.agent.subagents.health_analysis import HealthAnalysisAgent
from apps.agent_service.src.agent.subagents.outreach_draft import OutreachDraftAgent
from apps.agent_service.src.agent.subagents.playbook_retrieval import PlaybookRetrievalAgent

Domain = Literal["signal", "conversation"]


@lru_cache(maxsize=1)
def _conversation_specialists() -> dict[AgentRole, type[ReActSubagent]]:
    """Import conversation specialists lazily to avoid a package-init cycle."""
    from apps.agent_service.src.agent.conversation.subagents.billing import BillingAgent
    from apps.agent_service.src.agent.conversation.subagents.escalation import EscalationAgent
    from apps.agent_service.src.agent.conversation.subagents.general import GeneralAgent
    from apps.agent_service.src.agent.conversation.subagents.technical import TechnicalAgent

    return {
        AgentRole.GENERAL: GeneralAgent,
        AgentRole.TECHNICAL: TechnicalAgent,
        AgentRole.BILLING: BillingAgent,
        AgentRole.ESCALATION: EscalationAgent,
    }


def _signal_specialists() -> dict[AgentRole, type[ReActSubagent]]:
    return {
        AgentRole.HEALTH_ANALYSIS: HealthAnalysisAgent,
        AgentRole.OUTREACH_DRAFT: OutreachDraftAgent,
    }


def _shared_specialists() -> dict[AgentRole, type[ReActSubagent]]:
    return {AgentRole.PLAYBOOK_RETRIEVAL: PlaybookRetrievalAgent}


def role_map_for_domain(domain: Domain | None) -> dict[AgentRole, type[ReActSubagent]]:
    """Return the specialist mapping a given domain is allowed to instantiate."""
    shared = _shared_specialists()
    if domain == "signal":
        return {**_signal_specialists(), **shared}
    if domain == "conversation":
        return {**_conversation_specialists(), **shared}
    return {**_signal_specialists(), **_conversation_specialists(), **shared}


def build_subagent(
    *,
    packet: SubagentContextPacket,
    ctx: SessionContext,
    config: AgentConfig,
    domain: Domain | None = None,
) -> BaseSubagent:
    """Instantiate the specialist subagent for a task's role.

    When ``domain`` is provided, only that domain's specialists are eligible so
    a conversation run can never spin up a signal specialist and vice versa.
    Falls back to the generic :class:`ReActSubagent` for roles without a
    dedicated specialist in the selected domain.
    """
    role_map = role_map_for_domain(domain)
    subagent_cls = role_map.get(packet.task.role, ReActSubagent)
    return subagent_cls(packet=packet, ctx=ctx, config=config)


# Convenience module-level maps (built on demand; safe after package import).
def __getattr__(name: str):  # pragma: no cover - thin accessor
    if name == "ROLE_TO_SUBAGENT":
        return role_map_for_domain(None)
    if name == "SIGNAL_ROLE_TO_SUBAGENT":
        return role_map_for_domain("signal")
    if name == "CONVERSATION_ROLE_TO_SUBAGENT":
        return role_map_for_domain("conversation")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseSubagent",
    "ReActSubagent",
    "HealthAnalysisAgent",
    "OutreachDraftAgent",
    "PlaybookRetrievalAgent",
    "role_map_for_domain",
    "build_subagent",
    "ROLE_TO_SUBAGENT",
    "SIGNAL_ROLE_TO_SUBAGENT",
    "CONVERSATION_ROLE_TO_SUBAGENT",
]
