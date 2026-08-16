"""
ConversationOrchestrator for customer-facing chat turns.

The live path is the GeneralAgent Orchestrator-Workers loop
(``run_conversation_loop`` / ``stream_conversation_loop``), not P-E-R planning.
Post-turn side effects (message persistence, fire-and-forget profile update, and
the conversation->signal bridge) run in ``_apply_loop_side_effects`` so they never
block the chat response.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Awaitable

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext, get_conversation_memory
from packages.agent.src.orchestration_types import (
    ComplianceReview,
    ConversationAgentInput,
    FinalDecision,
    OrchestratorPlan,
)
from packages.agent.src.types import AgentResponse, LLMUsage, SessionContext

from apps.agent_service.src.agent.conversation.conversation_loop import ConversationLoop
from apps.agent_service.src.agent.conversation.intent import (
    IntentCategory,
    IntentResult,
    UrgencyLevel,
    get_intent_recognizer,
)
from apps.agent_service.src.agent.orchestrator.base import AgentInput, BaseOrchestrator, EMITTED_ACTION
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


def _signal_bridge_enabled() -> bool:
    """Whether the conversation->signal bridge is active.

    On by default. Set ``CONVERSATION_SIGNAL_BRIDGE=0`` to disable it — used by
    the evaluation harness so eval chat turns do not spawn background
    ProcessSignalWorkflows that contend with the eval for the worker and inflate
    measured latency. Disabling only affects the proactive background follow-up;
    the chat answer itself is unchanged.
    """
    return os.getenv("CONVERSATION_SIGNAL_BRIDGE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _should_bridge_to_signal(intent: IntentResult) -> bool:
    """Whether this chat turn should queue a proactive negative-sentiment signal."""
    if not _signal_bridge_enabled():
        return False
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
            # Allow skipping the reflector critic on low-risk informational turns
            # (General/Technical, no billing/escalation/policy) to remove one LLM
            # round-trip. The planner marks those plans requires_critic=False and
            # plan_requires_critic() still forces the critic for billing/escalation
            # /write roles, so money-movement and human-handoff turns are unaffected.
            skip_critic_for_simple=True,
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
        """Unused: the conversation path runs the GeneralAgent loop, not P-E-R planning.

        Kept only to satisfy ``BaseOrchestrator``'s abstract ``build_plan``. The live
        conversation path is ``run_conversation_loop`` / ``stream_conversation_loop``,
        and its post-turn side effects live in ``_apply_loop_side_effects``.
        """
        raise NotImplementedError(
            "Conversation path uses the GeneralAgent Orchestrator-Workers loop; "
            "P-E-R planning was removed."
        )

    async def _enqueue_negative_sentiment_signal(
        self,
        agent_input: ConversationAgentInput,
        sentiment: str,
    ) -> None:
        from apps.temporal_worker.src.client import start_signal_workflow

        intent = self._last_intent
        # Escalation / explicit human-flag turns raise nps_detractor so the signal
        # path can notify the CSM inbox (EMAIL_FROM). Other unhappy turns stay on
        # negative_sentiment for customer-facing recovery outreach.
        signal_type = (
            "nps_detractor"
            if intent is not None and intent.intent == IntentCategory.ESCALATION
            else "negative_sentiment"
        )
        try:
            await start_signal_workflow(
                {
                    "tenant_id": agent_input.tenant_id,
                    "customer_id": agent_input.customer_id,
                    "type": signal_type,
                    "severity": "high",
                    "source": "chat_bridge",
                    "payload": {
                        "reason": (
                            f"{intent.intent.value} intent in chat" if intent else "negative sentiment"
                        ),
                        "message": agent_input.message.content[:500],
                        "sentiment": sentiment,
                        "session_id": agent_input.session_id,
                        # No survey score for chat-bridged escalations; CSM draft still works.
                        "score": None,
                        "comment": agent_input.message.content[:500],
                    },
                }
            )
        except Exception:
            # Bridging is best-effort; never fail the chat turn because of it.
            return


async def _build_loop(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> tuple[ConversationOrchestrator, ConversationLoop]:
    """Shared setup for the new GeneralAgent Orchestrator-Workers path.

    Loads config, tenant constraints, memory excerpt, and recent history in the
    same order as ``BaseOrchestrator.run``, recognises intent for the profile
    update / signal bridge, and returns the orchestrator (for side effects) plus
    a ready-to-run ``ConversationLoop``.
    """
    orchestrator = ConversationOrchestrator()
    config = await orchestrator.load_config(ctx)
    tenant_constraints = await orchestrator.load_tenant_constraints(ctx)
    memory_excerpt = await orchestrator.load_memory_excerpt(agent_input, ctx, config)
    memory_context = await orchestrator.load_execution_memory_context(agent_input, ctx, config)

    history: list[dict[str, str]] = []
    if memory_context is not None:
        history = [
            {"role": message.role.value, "content": message.content}
            for message in memory_context.recent_messages[-3:]
        ]

    orchestrator._last_intent = await orchestrator._intent.recognize(
        agent_input.message.content,
        history=history,
    )

    loop = ConversationLoop(
        message=agent_input.message.content,
        ctx=ctx,
        config=config,
        memory_excerpt=memory_excerpt,
        history=history,
        tenant_constraints=tenant_constraints,
    )
    return orchestrator, loop


async def _apply_loop_side_effects(
    orchestrator: ConversationOrchestrator,
    agent_input: ConversationAgentInput,
    final_text: str,
) -> None:
    """Run the same post-approval side effects as ``on_approved`` for the loop path.

    The GeneralAgent has no separate critic, so once it has produced an answer we
    apply the identical side effects the P-E-R path runs on approval: persist the
    user + assistant messages, fire-and-forget the profile update, and (for
    negative/escalation turns) enqueue the conversation->signal bridge.
    """
    await orchestrator._memory.add_message(agent_input.message)
    assistant_message = ChatMessage(
        tenant_id=agent_input.tenant_id,
        customer_id=agent_input.customer_id,
        session_id=agent_input.session_id,
        role=ChatMessageRole.ASSISTANT,
        content=final_text,
    )
    await orchestrator._memory.add_message(assistant_message)

    if orchestrator._last_intent is None:
        return
    sentiment = _sentiment_label(orchestrator._last_intent)
    _spawn_background(
        orchestrator._memory.update_profile(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=agent_input.session_id,
            profile_data=_profile_data_from_intent(orchestrator._last_intent, sentiment),
        ),
        label="conversation profile update",
    )
    if _should_bridge_to_signal(orchestrator._last_intent):
        await orchestrator._enqueue_negative_sentiment_signal(agent_input, sentiment)


async def run_conversation_loop(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AgentResponse:
    """Run the GeneralAgent Orchestrator-Workers loop for one non-streamed turn.

    The GeneralAgent is its own critic (compliance rules live in its SKILL.md),
    so there is no separate Reflector phase: the produced answer is always
    approved and the same post-approval side effects run as on the P-E-R path.
    """
    orchestrator, loop = await _build_loop(agent_input, ctx)

    chunks: list[str] = []
    async for delta in loop.run_stream():
        chunks.append(delta)
    final_text = "".join(chunks)

    await _apply_loop_side_effects(orchestrator, agent_input, final_text)

    decision = FinalDecision(
        action=EMITTED_ACTION,
        response_text=final_text,
        compliance_review=ComplianceReview(
            approved=True,
            feedback=(
                "GeneralAgent loop has no separate critic; embedded compliance "
                "rules applied."
            ),
        ),
        reasoning_summary="GeneralAgent orchestrator produced the final answer.",
    )
    return AgentResponse(
        text=final_text,
        approved=True,
        final_decision=decision,
    )


async def stream_conversation_loop(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AsyncIterator[str]:
    """Stream the GeneralAgent loop's final answer as raw text deltas.

    Yields each delta from ``ConversationLoop.run_stream`` as it arrives, then
    runs the same post-approval side effects as :func:`run_conversation_loop`
    once the loop finishes. Yields raw text only; SSE framing is applied in
    ``streaming.py``.
    """
    orchestrator, loop = await _build_loop(agent_input, ctx)

    chunks: list[str] = []
    async for delta in loop.run_stream():
        chunks.append(delta)
        yield delta

    await _apply_loop_side_effects(orchestrator, agent_input, "".join(chunks))


__all__ = [
    "ConversationOrchestrator",
    "run_conversation_loop",
    "stream_conversation_loop",
]
