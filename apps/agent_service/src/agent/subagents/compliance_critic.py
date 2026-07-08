"""Compliance critic subagent used as the Reflector phase."""

from __future__ import annotations

import json
from typing import Any

from packages.agent.src.config import AgentConfig
from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage
from packages.agent.src.orchestration_types import (
    ComplianceFinding,
    ComplianceReview,
    ConversationAgentInput,
    OrchestratorPlan,
    SignalAgentInput,
)
from packages.agent.src.subagent_types import SubagentResult
from packages.agent.src.types import LLMUsage, SessionContext


async def run_compliance_critic(
    *,
    agent_input: SignalAgentInput | ConversationAgentInput,
    plan: OrchestratorPlan,
    results: list[SubagentResult],
    ctx: SessionContext,
    config: AgentConfig,
    proposed_external_writes: list[dict[str, Any]],
    llm_client: LLMClient | None = None,
) -> tuple[ComplianceReview, LLMUsage]:
    """Review aggregated subagent output before writes or visible output."""
    client = llm_client or LLMClient(default_model=config.planner_model)
    payload = {
        "input_kind": "signal" if isinstance(agent_input, SignalAgentInput) else "conversation",
        "tenant_id": ctx.tenant_id,
        "customer_id": agent_input.customer_id,
        "plan": plan.model_dump(mode="json"),
        "subagent_results": [result.model_dump(mode="json") for result in results],
        "proposed_external_writes": proposed_external_writes,
        "schema": ComplianceReview.model_json_schema(),
    }
    messages = [
        LLMMessage(
            role="system",
            content=(
                "You are ComplianceCriticAgent, the Reflector phase. Review aggregated "
                "subagent outputs before any external write, state mutation, or customer-visible "
                "output. Validate tenant isolation, PII leakage, security, business policy, "
                "factual support, and tone. Return only JSON matching the provided schema."
            ),
        ),
        LLMMessage(role="user", content=json.dumps(payload, default=str)),
    ]

    response = await client.complete(
        messages,
        model=config.planner_model,
        trace_id=ctx.trace_id,
        name="compliance_critic_reflector",
        metadata={"signal_id": ctx.signal_id, "phase": "reflector"},
    )
    usage = LLMUsage(
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    return _parse_review(response.text, results), usage


def _parse_review(text: str, results: list[SubagentResult]) -> ComplianceReview:
    try:
        return ComplianceReview.model_validate_json(text)
    except Exception as exc:
        # fallback diagnostic message if the critic response is invalid
        failed_results = [result.task_id for result in results if not result.success]
        if failed_results:
            return ComplianceReview(
                approved=False,
                findings=[
                    ComplianceFinding(
                        code="subagent_failure",
                        severity="blocker",
                        message="One or more subagents failed before compliance review completed.",
                        affected_task_ids=failed_results,
                    )
                ],
                feedback="Compliance review blocked because one or more subagents failed.",
            )
        return ComplianceReview(
            approved=False,
            findings=[
                ComplianceFinding(
                    code="invalid_critic_response",
                    severity="blocker",
                    message=f"Compliance critic did not return valid review JSON: {exc}",
                )
            ],
            feedback="Compliance review blocked because the critic response was invalid.",
        )


__all__ = ["run_compliance_critic"]
