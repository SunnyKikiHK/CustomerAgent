"""Shared Planner -> Executor -> Reflector base for top-level orchestrators.

`SignalOrchestrator` and `ConversationOrchestrator` subclass `BaseOrchestrator`
and supply only domain-specific pieces (config/constraint/memory loading and
plan construction). The shared lifecycle -- delegation, compliance review,
finalization, and response assembly -- lives here so it is not duplicated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from packages.agent.src.config import AgentConfig
from packages.agent.src.memory import MemoryContext
from packages.agent.src.orchestration_types import (
    ComplianceReview,
    ConversationAgentInput,
    FinalDecision,
    OrchestratorPlan,
    SignalAgentInput,
)
from packages.agent.src.subagent_types import SubagentResult
from packages.agent.src.types import AgentResponse, LLMUsage, SessionContext

from apps.agent_service.src.agent.orchestrator.policy import plan_requires_critic
from apps.agent_service.src.agent.orchestrator.reducer import (
    extract_proposed_external_writes,
    finalize_decision,
)
from apps.agent_service.src.agent.runtime.delegation import execute_tasks
from apps.agent_service.src.agent.subagents.compliance_critic import run_compliance_critic

AgentInput = SignalAgentInput | ConversationAgentInput

#: Decision action emitted only when outputs/writes may actually be released.
EMITTED_ACTION = "emit_or_execute_approved_payload"
SAFE_COMPLIANCE_FALLBACK = (
    "I’m unable to safely complete that request here based on your request. "
    "Please contact support through the approved channel."
)
_RETRY_COMPLIANCE_CONSTRAINT = (
    "A previous response draft was rejected during compliance review. Produce a "
    "new, fully grounded response that contains no personal data, credentials, "
    "secrets, raw system errors, or unsupported claims."
)


class BaseOrchestrator(ABC):
    """Coordinates the shared P-E-R lifecycle for one agent input."""

    #: Whether this orchestrator proposes external writes (signal path does).
    supports_external_writes: bool = False

    #: Domain used by the delegation factory to restrict which specialists this
    #: orchestrator may instantiate ("signal" or "conversation").
    domain: str | None = None

    @abstractmethod
    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        """Load the per-tenant agent configuration."""

    @abstractmethod
    async def load_tenant_constraints(self, ctx: SessionContext) -> list[str]:
        """Load tenant-scoped global constraints."""

    @abstractmethod
    async def load_memory_excerpt(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> str | None:
        """Load the memory slice made available to subagents."""

    @abstractmethod
    async def build_plan(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
        tenant_constraints: list[str],
        memory_excerpt: str | None,
    ) -> tuple[OrchestratorPlan, LLMUsage]:
        """Build the domain-specific plan (Planner phase)."""

    def customer_id(self, agent_input: AgentInput) -> str:
        """Return the customer id for the given input."""
        return agent_input.customer_id

    async def on_approved(
        self,
        agent_input: AgentInput,
        decision: FinalDecision,
        ctx: SessionContext,
    ) -> list[dict[str, Any]]:
        """Release approved writes and return external execution results."""
        return []

    async def on_complete(
        self,
        agent_input: AgentInput,
        plan: OrchestratorPlan,
        results: list[SubagentResult],
        review: ComplianceReview,
        decision: FinalDecision,
        ctx: SessionContext,
    ) -> None:
        """Hook for audit logging, always invoked at the end of a run."""

    async def load_execution_memory_context(
        self,
        agent_input: AgentInput,
        ctx: SessionContext,
        config: AgentConfig,
    ) -> MemoryContext | None:
        """Load structured memory for role-specific delegation slices."""
        return None

    async def run(self, agent_input: AgentInput, ctx: SessionContext) -> AgentResponse:
        """Execute Planner -> Executor -> Reflector and assemble the response."""
        config = await self.load_config(ctx)
        tenant_constraints = await self.load_tenant_constraints(ctx)
        memory_excerpt = await self.load_memory_excerpt(agent_input, ctx, config)
        memory_context = await self.load_execution_memory_context(agent_input, ctx, config)

        plan, planner_usage = await self.build_plan(
            agent_input=agent_input,
            ctx=ctx,
            config=config,
            tenant_constraints=tenant_constraints,
            memory_excerpt=memory_excerpt,
        )

        results = await execute_tasks(
            plan=plan,
            ctx=ctx,
            config=config,
            customer_id=self.customer_id(agent_input),
            tenant_constraints=tenant_constraints,
            memory_excerpt=memory_excerpt,
            memory_context=memory_context,
            domain=self.domain,
        )

        proposed_external_writes = (
            extract_proposed_external_writes(results)
            if self.supports_external_writes
            else []
        )

        review, critic_usage = await self._reflect(
            agent_input=agent_input,
            plan=plan,
            results=results,
            ctx=ctx,
            config=config,
            proposed_external_writes=proposed_external_writes,
        )

        retry_usage = LLMUsage()
        if not review.approved:
            retry_plan = plan.model_copy(
                update={
                    "global_constraints": [
                        *plan.global_constraints,
                        _RETRY_COMPLIANCE_CONSTRAINT,
                    ],
                    "reasoning_summary": (
                        f"{plan.reasoning_summary}; retrying after compliance rejection"
                    ),
                }
            )
            retry_results = await execute_tasks(
                plan=retry_plan,
                ctx=ctx,
                config=config,
                customer_id=self.customer_id(agent_input),
                tenant_constraints=tenant_constraints,
                memory_excerpt=memory_excerpt,
                memory_context=memory_context,
                domain=self.domain,
            )
            retry_writes = (
                extract_proposed_external_writes(retry_results)
                if self.supports_external_writes
                else []
            )
            retry_review, retry_usage = await self._reflect(
                agent_input=agent_input,
                plan=retry_plan,
                results=retry_results,
                ctx=ctx,
                config=config,
                proposed_external_writes=retry_writes,
            )
            if retry_review.approved:
                plan = retry_plan
                results = retry_results
                proposed_external_writes = retry_writes
                review = retry_review
            else:
                decision = self._safe_compliance_fallback(
                    retry_results,
                    retry_review,
                )
                await self.on_complete(
                    agent_input,
                    retry_plan,
                    retry_results,
                    retry_review,
                    decision,
                    ctx,
                )
                return AgentResponse(
                    text=decision.response_text,
                    subagent_results=retry_results,
                    planner_tokens=planner_usage.total,
                    executor_tokens=sum(
                        result.tokens_used for result in retry_results
                    ),
                    critic_tokens=critic_usage.total + retry_usage.total,
                    approved=False,
                    final_decision=decision,
                    feedback=retry_review.feedback,
                )

        decision = finalize_decision(results, review, proposed_external_writes)

        # Gate emission on the finalized decision, not the raw review: the critic
        # may approve while the reducer still blocks (for example when redactions
        # intersected customer-visible content and a replan is required).
        emitted = decision.action == EMITTED_ACTION
        action_results: list[dict[str, Any]] = []
        if emitted:
            action_results = await self.on_approved(agent_input, decision, ctx)
            decision.external_action_results = action_results
            if decision.approved_external_writes and not all(
                bool(result.get("success")) for result in action_results
            ):
                emitted = False
                decision.action = "external_action_failed"
                decision.reasoning_summary = (
                    "Policy approved the payload, but one or more external actions failed."
                )
        await self.on_complete(agent_input, plan, results, review, decision, ctx)

        return AgentResponse(
            text=decision.response_text,
            subagent_results=results,
            planner_tokens=planner_usage.total,
            executor_tokens=sum(result.tokens_used for result in results),
            critic_tokens=critic_usage.total + retry_usage.total,
            approved=emitted,
            final_decision=decision,
            feedback=review.feedback,
        )

    def _safe_compliance_fallback(
        self,
        results: list[SubagentResult],
        review: ComplianceReview,
    ) -> FinalDecision:
        """Return the fixed response after the bounded compliance retry fails."""
        return FinalDecision(
            action="compliance_retry_exhausted",
            response_text=SAFE_COMPLIANCE_FALLBACK,
            approved_external_writes=[],
            subagent_results=results,
            compliance_review=review,
            reasoning_summary="Compliance review rejected both response attempts.",
        )

    async def _reflect(
        self,
        *,
        agent_input: AgentInput,
        plan: OrchestratorPlan,
        results: list[SubagentResult],
        ctx: SessionContext,
        config: AgentConfig,
        proposed_external_writes: list[dict[str, Any]],
    ) -> tuple[ComplianceReview, LLMUsage]:
        """Run the Reflector phase, honoring skip-critic policy for safe plans."""
        if config.skip_critic_for_simple and not plan_requires_critic(plan):
            review = ComplianceReview(
                approved=all(result.success for result in results),
                feedback="Critic skipped for read-only plan with no customer-visible writes.",
            )
            return review, LLMUsage()

        return await run_compliance_critic(
            agent_input=agent_input,
            plan=plan,
            results=results,
            ctx=ctx,
            config=config,
            proposed_external_writes=proposed_external_writes,
        )


__all__ = [
    "BaseOrchestrator",
    "AgentInput",
    "EMITTED_ACTION",
    "SAFE_COMPLIANCE_FALLBACK",
]
