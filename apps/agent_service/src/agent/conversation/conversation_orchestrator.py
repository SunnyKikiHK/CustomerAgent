"""
ConversationOrchestrator for customer-facing chat turns.

Special:
ConversationOrchestrator.on_approved() writes the conversation profile 
asynchronously, so it does not block the chat response. 
It supplies intent-derived fields and ConversationMemory.update_profile() adds LLM-extracted profile signals such as risk_signals, sentiment, adoption barriers, and communication preferences.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext, get_conversation_memory
from packages.agent.src.orchestration_types import ConversationAgentInput, FinalDecision, OrchestratorPlan
from packages.agent.src.types import AgentResponse, LLMUsage, SessionContext

from apps.agent_service.src.agent.conversation.conversation_planner import build_conversation_plan
from apps.agent_service.src.agent.conversation.intent import (
    IntentCategory,
    IntentResult,
    UrgencyLevel,
    get_intent_recognizer,
)
from apps.agent_service.src.agent.orchestrator.base import AgentInput, BaseOrchestrator
from apps.agent_service.src.agent.orchestrator.policy import DEFAULT_TENANT_CONSTRAINTS
from packages.agent.src.models import planner_model, worker_model

logger = logging.getLogger(__name__)

#: Intents that indicate an unhappy / escalation-worthy turn for the bridge.
_NEGATIVE_INTENTS = {IntentCategory.COMPLAINT, IntentCategory.ESCALATION}

#: Strong refs to in-flight background profile updates so the event loop does not
#: garbage-collect them mid-run (asyncio holds only weak refs to tasks).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro: Awaitable[None], *, label: str) -> None:
    """Fire-and-forget a coroutine without blocking or crashing the turn.

    Holds a strong reference until completion and logs (never raises) any error,
    so a slow/failed profile update cannot fail or delay the chat response.
    """
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(finished: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(finished)
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.warning("background %s failed: %s", label, exc)

    task.add_done_callback(_done)


def _sentiment_label(intent: IntentResult) -> str:
    """Coarse sentiment label derived from intent + urgency."""
    if intent.intent in _NEGATIVE_INTENTS:
        return "negative"
    if intent.intent == IntentCategory.FEEDBACK:
        return "positive"
    return "neutral"


def _should_bridge_to_signal(intent: IntentResult) -> bool:
    """Whether this chat turn should queue a proactive negative-sentiment signal."""
    return intent.intent in _NEGATIVE_INTENTS or intent.urgency >= UrgencyLevel.HIGH


#: Per-turn signal lists surfaced by intent extraction that belong at the top
#: level of the durable profile (customer_profiles), not inside the nested
#: `entities` bucket. The remaining transactional entity keys stay under
#: `entities`.
_PROFILE_SIGNAL_KEYS = ("preferences", "risk_signals", "sentiment_signals")


def _profile_data_from_intent(intent: IntentResult, sentiment: str) -> dict[str, object]:
    """Build the profile_data merged into customer_profiles for this turn.

    Lifts the chat-derived ``preferences`` / ``risk_signals`` /
    ``sentiment_signals`` out of ``intent.entities`` to the top level (where the
    profile merger reads list fields) and leaves the transactional entities under
    ``entities``.
    """
    entities = dict(intent.entities or {})
    profile_data: dict[str, object] = {
        "last_intent": intent.intent.value,
        "last_sentiment": sentiment,
    }
    for key in _PROFILE_SIGNAL_KEYS:
        value = entities.pop(key, [])
        if value:
            profile_data[key] = value
    profile_data["entities"] = entities
    return profile_data


class ConversationOrchestrator(BaseOrchestrator):
    """Top-level orchestrator for synchronous customer chat."""

    supports_external_writes = False
    domain = "conversation"

    def __init__(self) -> None:
        self._intent = get_intent_recognizer()
        self._memory = get_conversation_memory()
        self._last_intent: IntentResult | None = None

    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        return AgentConfig(
            tenant_id=ctx.tenant_id,
            name="conversation-agent",
            instructions="Customer-facing conversation assistant",
            model=worker_model(),
            planner_model=planner_model(),
            tools=["query_health", "query_playbooks"],
            skip_critic_for_simple=False,
        )

    async def load_tenant_constraints(self, ctx: SessionContext) -> list[str]:
        return list(DEFAULT_TENANT_CONSTRAINTS)

    async def load_memory_excerpt(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> str | None:
        if not isinstance(agent_input, ConversationAgentInput) or not config.memory_enabled:
            return None
        memory_context = await self._memory.get_context(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=agent_input.session_id,
            query=agent_input.message.content,
        )
        return memory_context.to_prompt_text()

    async def load_execution_memory_context(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> MemoryContext | None:
        if not isinstance(agent_input, ConversationAgentInput) or not config.memory_enabled:
            return None
        return await self._memory.get_context(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=agent_input.session_id,
            query=agent_input.message.content,
        )

    async def build_plan(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
        tenant_constraints: list[str],
        memory_excerpt: str | None,
    ) -> tuple[OrchestratorPlan, LLMUsage]:
        if not isinstance(agent_input, ConversationAgentInput):
            raise TypeError("ConversationOrchestrator requires ConversationAgentInput")

        history = []
        memory_context = await self.load_execution_memory_context(agent_input, ctx, config)
        if memory_context:
            history = [
                {"role": message.role.value, "content": message.content}
                for message in memory_context.recent_messages[-3:]
            ]

        self._last_intent = await self._intent.recognize(
            agent_input.message.content,
            history=history,
        )
        return await build_conversation_plan(
            message=agent_input.message.content,
            intent=self._last_intent,
            config=config,
            tenant_constraints=tenant_constraints,
            memory_excerpt=memory_excerpt,
            history=history,
        )

    async def on_approved(
        self,
        agent_input: AgentInput,
        decision: FinalDecision,
        ctx: SessionContext,
    ) -> list[dict[str, object]]:
        if not isinstance(agent_input, ConversationAgentInput):
            return []
        await self._memory.add_message(agent_input.message)
        assistant_message = ChatMessage(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=agent_input.session_id,
            role=ChatMessageRole.ASSISTANT,
            content=decision.response_text,
        )
        await self._memory.add_message(assistant_message)
        if self._last_intent is not None:
            sentiment = _sentiment_label(self._last_intent)
            # Non-blocking: the profile update runs an LLM distill + DB writes.
            # Do not make the customer wait for it; fire-and-forget so the reply
            # returns immediately (mirrors the reference project's
            # asyncio.create_task(update_profile(...))).
            _spawn_background(
                self._memory.update_profile(
                    tenant_id=agent_input.tenant_id,
                    customer_id=agent_input.customer_id,
                    session_id=agent_input.session_id,
                    profile_data=_profile_data_from_intent(self._last_intent, sentiment),
                ),
                label="conversation profile update",
            )
            # Conversation -> signal bridge: an unhappy or escalation turn queues
            # a proactive signal so the signal system can analyze / apologize /
            # email. The chat answer itself stays bounded (no inline outreach).
            if _should_bridge_to_signal(self._last_intent):
                await self._enqueue_negative_sentiment_signal(agent_input, sentiment)

        return []

    async def _enqueue_negative_sentiment_signal(
        self,
        agent_input: ConversationAgentInput,
        sentiment: str,
    ) -> None:
        from apps.agent_service.src.signals.queue import enqueue_signal

        intent = self._last_intent
        try:
            await enqueue_signal(
                {
                    "tenant_id": agent_input.tenant_id,
                    "customer_id": agent_input.customer_id,
                    "type": "negative_sentiment",
                    "severity": "high",
                    "source": "chat_bridge",
                    "payload": {
                        "reason": f"{intent.intent.value} intent in chat" if intent else "negative sentiment",
                        "message": agent_input.message.content[:500],
                        "sentiment": sentiment,
                        "session_id": agent_input.session_id,
                    },
                }
            )
        except Exception:
            # Bridging is best-effort; never fail the chat turn because of it.
            return


async def run_conversation_agent(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AgentResponse:
    """Entry point for a conversation orchestrator run."""
    orchestrator = ConversationOrchestrator()
    return await orchestrator.run(agent_input, ctx)


__all__ = ["ConversationOrchestrator", "run_conversation_agent"]
