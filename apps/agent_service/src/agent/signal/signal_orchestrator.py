"""SignalOrchestrator for proactive customer-success automation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext, get_conversation_memory
from packages.agent.src.orchestration_types import FinalDecision, OrchestratorPlan, SignalAgentInput
from packages.agent.src.types import LLMUsage, SessionContext

from apps.agent_service.src.agent.orchestrator.base import AgentInput, BaseOrchestrator
from apps.agent_service.src.agent.orchestrator.policy import DEFAULT_TENANT_CONSTRAINTS
from apps.agent_service.src.agent.runtime.tool_dispatch import execute_mcp_action
from apps.agent_service.src.agent.signal.signal_planner import build_signal_plan
from apps.tool_gateway.src.approval import persist_action_approval
from packages.agent.src.models import planner_model, worker_model


class SignalOrchestrator(BaseOrchestrator):
    """Top-level orchestrator for typed backend customer signals."""

    supports_external_writes = True
    domain = "signal"

    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        return AgentConfig(
            tenant_id=ctx.tenant_id,
            name="signal-agent",
            instructions="Proactive customer-success automation",
            model=worker_model(),
            planner_model=planner_model(),
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
        context = await get_conversation_memory().get_context(
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
        return await get_conversation_memory().get_context(
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
    ) -> list[dict[str, Any]]:
        """Persist internal state, then release only compliance-approved writes."""
        if not isinstance(agent_input, SignalAgentInput):
            return []
        await get_conversation_memory().update_profile(
            tenant_id=agent_input.tenant_id,
            customer_id=agent_input.customer_id,
            session_id=ctx.session_id,
            profile_data={
                "last_signal_type": agent_input.signal.type,
                "last_signal_summary": agent_input.signal.signal_text,
            },
        )

        results: list[dict[str, Any]] = []
        for index, write in enumerate(decision.approved_external_writes):
            action_name, arguments = _parse_approved_write(write)
            approval_id = _stable_digest(
                {
                    "tenant_id": ctx.tenant_id,
                    "trace_id": ctx.trace_id,
                    "decision": decision.action,
                    "action": action_name,
                    "index": index,
                }
            )
            idempotency_key = _stable_digest(
                {
                    "tenant_id": ctx.tenant_id,
                    "trace_id": ctx.trace_id,
                    "action": action_name,
                    "index": index,
                    "arguments": arguments,
                }
            )
            try:
                await persist_action_approval(
                    approval_id=approval_id,
                    tenant_id=ctx.tenant_id,
                    action_name=action_name,
                    trace_id=ctx.trace_id,
                    payload=arguments,
                )
                result = await execute_mcp_action(
                    action_name,
                    arguments,
                    ctx,
                    approval_id=approval_id,
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                result = {
                    "success": False,
                    "status": "failed",
                    "idempotency_key": idempotency_key,
                    "error": f"{type(exc).__name__}: external action failed",
                }
            results.append(result)
        return results


def _parse_approved_write(write: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize reducer output without trusting tenant identity from the draft."""
    action_name = str(write.get("tool") or write.get("name") or write.get("action") or "")
    raw = write.get("arguments", write.get("params", write.get("payload", write)))
    if not action_name or not isinstance(raw, dict):
        raise ValueError("approved write must contain an action name and object arguments")
    arguments = dict(raw)
    for key in ("tool", "name", "action"):
        arguments.pop(key, None)
    return action_name, arguments


def _stable_digest(value: dict[str, Any]) -> str:
    """Build a deterministic opaque ID without exposing action payload details."""
    canonical = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_signal_agent(agent_input: SignalAgentInput, ctx: SessionContext) -> Any:
    """Entry point used by the RQ worker."""
    return await SignalOrchestrator().run(agent_input, ctx)


__all__ = ["SignalOrchestrator", "run_signal_agent"]
