"""Tests for the reflector-critic gate: skip low-risk turns, never billing/escalation."""

from __future__ import annotations

from apps.agent_service.src.agent.orchestrator.policy import plan_requires_critic
from packages.agent.src.orchestration_types import OrchestratorPlan
from packages.agent.src.subagent_types import AgentRole, SubagentTask


def _task(role: AgentRole) -> SubagentTask:
    return SubagentTask(
        id="answer",
        role=role,
        objective="x",
        skill="x",
        allowed_tools=[],
    )


def _plan(role: AgentRole, requires_critic: bool) -> OrchestratorPlan:
    return OrchestratorPlan(
        goal="g",
        tasks=[_task(role)],
        requires_critic=requires_critic,
        global_constraints=[],
        reasoning_summary="r",
    )


def test_general_plan_can_skip_critic():
    # General answer, plan opted out -> critic may be skipped.
    assert plan_requires_critic(_plan(AgentRole.GENERAL, requires_critic=False)) is False


def test_technical_plan_can_skip_critic():
    assert plan_requires_critic(_plan(AgentRole.TECHNICAL, requires_critic=False)) is False


def test_billing_always_forces_critic():
    # Even when the plan opts out, billing must be reviewed.
    assert plan_requires_critic(_plan(AgentRole.BILLING, requires_critic=False)) is True


def test_escalation_always_forces_critic():
    assert plan_requires_critic(_plan(AgentRole.ESCALATION, requires_critic=False)) is True


def test_outreach_write_always_forces_critic():
    assert plan_requires_critic(_plan(AgentRole.OUTREACH_DRAFT, requires_critic=False)) is True


def test_requires_critic_true_always_wins():
    # A plan that explicitly requires the critic is always reviewed.
    assert plan_requires_critic(_plan(AgentRole.GENERAL, requires_critic=True)) is True
