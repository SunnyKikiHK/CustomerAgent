"""Tests for bounded compliance-review retry behavior."""

from __future__ import annotations

from apps.agent_service.src.agent.orchestrator.base import (
    BaseOrchestrator,
    SAFE_COMPLIANCE_FALLBACK,
)
from packages.agent.src.config import AgentConfig
from packages.agent.src.orchestration_types import ComplianceReview
from packages.agent.src.subagent_types import AgentRole, SubagentResult, SubagentTask
from packages.agent.src.types import LLMUsage, SessionContext


class _TestOrchestrator(BaseOrchestrator):
    async def load_config(self, ctx: SessionContext) -> AgentConfig:
        return AgentConfig(
            tenant_id=ctx.tenant_id,
            name="test",
            instructions="test",
            model="test",
            planner_model="test",
        )

    async def load_tenant_constraints(self, ctx: SessionContext) -> list[str]:
        return []

    async def load_memory_excerpt(self, agent_input, ctx, config) -> str | None:
        return None

    async def build_plan(self, agent_input, ctx, config, tenant_constraints, memory_excerpt):
        raise NotImplementedError


def _result() -> SubagentResult:
    return SubagentResult(
        task_id="answer",
        role=AgentRole.GENERAL,
        success=True,
        markdown="Unsafe draft",
    )


def _review() -> ComplianceReview:
    return ComplianceReview(approved=False, feedback="Unsafe content")


def test_safe_fallback_is_used_after_second_rejection():
    decision = _TestOrchestrator()._safe_compliance_fallback([_result()], _review())

    assert decision.action == "compliance_retry_exhausted"
    assert decision.response_text == SAFE_COMPLIANCE_FALLBACK
    assert decision.approved_external_writes == []
    assert decision.compliance_review.approved is False
