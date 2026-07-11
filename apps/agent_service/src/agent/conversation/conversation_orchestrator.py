"""ConversationOrchestrator for customer-facing chat turns."""

from __future__ import annotations

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext, get_conversation_memory
from packages.agent.src.orchestration_types import ConversationAgentInput, FinalDecision, OrchestratorPlan
from packages.agent.src.types import AgentResponse, LLMUsage, SessionContext

from apps.agent_service.src.agent.conversation.conversation_planner import build_conversation_plan
from apps.agent_service.src.agent.conversation.intent import IntentResult, get_intent_recognizer
from apps.agent_service.src.agent.orchestrator.base import AgentInput, BaseOrchestrator
from apps.agent_service.src.agent.orchestrator.policy import DEFAULT_TENANT_CONSTRAINTS


class ConversationOrchestrator(BaseOrchestrator):
    """Top-level orchestrator for synchronous customer chat."""

    supports_external_writes = False

    def __init__(self) -> None:
        self._intent = get_intent_recognizer()
        self._memory = get_conversation_memory()
        self._last_intent: IntentResult | None = None

    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        return AgentConfig(
            tenant_id=ctx.tenant_id,
            name="conversation-agent",
            instructions="Customer-facing conversation assistant",
            model="deepseek/deepseek-v4-flash",
            planner_model="deepseek/deepseek-v4-flash",
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
        return build_conversation_plan(
            message=agent_input.message.content,
            intent=self._last_intent,
            config=config,
            tenant_constraints=tenant_constraints,
            memory_excerpt=memory_excerpt,
        )

    async def on_approved(
        self,
        agent_input: AgentInput,
        decision: FinalDecision,
        ctx: SessionContext,
    ) -> None:
        if not isinstance(agent_input, ConversationAgentInput):
            return
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
            await self._memory.update_profile(
                tenant_id=agent_input.tenant_id,
                customer_id=agent_input.customer_id,
                session_id=agent_input.session_id,
                profile_data={
                    "last_intent": self._last_intent.intent.value,
                    "entities": self._last_intent.entities,
                },
            )


async def run_conversation_agent(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AgentResponse:
    """Entry point for a conversation orchestrator run."""
    orchestrator = ConversationOrchestrator()
    return await orchestrator.run(agent_input, ctx)


__all__ = ["ConversationOrchestrator", "run_conversation_agent"]
