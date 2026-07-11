"""SignalOrchestrator for proactive customer-success automation."""

from __future__ import annotations

from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext, get_conversation_memory
from packages.agent.src.orchestration_types import FinalDecision, OrchestratorPlan, SignalAgentInput
from packages.agent.src.types import LLMUsage, SessionContext

from apps.agent_service.src.agent.orchestrator.base import AgentInput, BaseOrchestrator
from apps.agent_service.src.agent.orchestrator.policy import DEFAULT_TENANT_CONSTRAINTS
from apps.agent_service.src.agent.signal.signal_planner import build_signal_plan
from apps.agent_service.src.agent.signal.signal_reducer import reduce_signal_decision


class SignalOrchestrator(BaseOrchestrator):
    """Top-level orchestrator for typed backend customer signals."""

    supports_external_writes = True

    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        return AgentConfig(
            tenant_id=ctx.tenant_id,
            name="signal-agent",
            instructions="Proactive customer-success automation",
            model="deepseek/deepseek-v4-flash",
            planner_model="deepseek/deepseek-v4-flash",
            tools=["query_health", "query_playbooks", "send_email", "send_slack"],
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
        if not isinstance(agent_input, SignalAgentInput) or not config.memory_enabled:
            return None
        memory = get_conversation_memory()
        context = await memory.get_context(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=ctx.session_id,
            query=agent_input.signal.signal_text,
        )
        return context.profile_excerpt()

    async def load_execution_memory_context(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> MemoryContext | None:
        if not isinstance(agent_input, SignalAgentInput) or not config.memory_enabled:
            return None
        memory = get_conversation_memory()
        return await memory.get_context(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=ctx.session_id,
            query=agent_input.signal.signal_text,
        )

    async def build_plan(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
        tenant_constraints: list[str],
        memory_excerpt: str | None,
    ) -> tuple[OrchestratorPlan, LLMUsage]:
        if not isinstance(agent_input, SignalAgentInput):
            raise TypeError("SignalOrchestrator requires SignalAgentInput")
        return build_signal_plan(
            signal=agent_input.signal,
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
        if not isinstance(agent_input, SignalAgentInput):
            return
        memory = get_conversation_memory()
        await memory.update_profile(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=ctx.session_id,
            profile_data={
                "last_signal_type": agent_input.signal.type,
                "last_signal_summary": agent_input.signal.signal_text,
            },
        )


async def run_signal_agent(
    agent_input: SignalAgentInput,
    ctx: SessionContext,
) -> Any:
    """Entry point used by the RQ worker."""
    orchestrator = SignalOrchestrator()
    return await orchestrator.run(agent_input, ctx)


__all__ = ["SignalOrchestrator", "run_signal_agent"]
