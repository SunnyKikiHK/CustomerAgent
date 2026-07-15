"""Compliance critic subagent used as the Reflector phase."""

from __future__ import annotations

import json
import re
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

from apps.agent_service.src.agent.runtime.skills import get_skill_manager

#: Skill role that owns the reviewer persona (skills/<tenant>/compliance_critic/).
_COMPLIANCE_CRITIC_ROLE = "compliance_critic"

#: Code-owned fallback persona, used only when no compliance_critic SKILL.md is
#: available (for example when the tenant skills dir is missing offline).
COMPLIANCE_CRITIC_BRIEF = (
    "You are ComplianceCriticAgent, the Reflector phase. Review aggregated "
    "subagent outputs before any external write, state mutation, or customer-visible "
    "output. Validate tenant isolation, PII leakage, security, business policy, "
    "factual support, and tone. Return only JSON matching the provided schema. "
    "For conversation turns with no external writes, approve conservative help that "
    "asks for missing order/account details instead of blocking."
)


def _critic_persona(tenant_id: str) -> str:
    """Load the reviewer persona from the tenant skills dir, with a code fallback."""
    try:
        persona = get_skill_manager(tenant_id).persona_for(_COMPLIANCE_CRITIC_ROLE)
    except Exception:
        persona = ""
    return persona or COMPLIANCE_CRITIC_BRIEF


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
    is_conversation = isinstance(agent_input, ConversationAgentInput)
    payload = {
        "input_kind": "signal" if isinstance(agent_input, SignalAgentInput) else "conversation",
        "tenant_id": ctx.tenant_id,
        "customer_id": agent_input.customer_id,
        "plan": plan.model_dump(mode="json"),
        "subagent_results": [result.model_dump(mode="json") for result in results],
        "proposed_external_writes": proposed_external_writes,
        "schema": ComplianceReview.model_json_schema(),
        "review_rules": {
            "conversation_no_writes_default": (
                "If input_kind is conversation and proposed_external_writes is empty, "
                "approve grounded, helpful customer replies. Asking the customer for an "
                "order/transaction id, clarifying refund next steps, or explaining that "
                "verification is required is APPROVED. Only block for real safety issues "
                "(cross-tenant data, PII/secrets leakage, guaranteed refund promises, "
                "fabricated account facts, or unsafe instructions)."
            ),
            "refund_help_is_allowed": True,
            "require_order_id_before_process_refund_only": True,
        },
    }
    messages = [
        LLMMessage(role="system", content=_critic_persona(ctx.tenant_id)),
        LLMMessage(role="user", content=json.dumps(payload, default=str)),
    ]

    response = await client.complete(
        messages,
        model=config.planner_model,
        temperature=0.0,
        max_tokens=800,
        # Follow LLM_REASONING_DEFAULT (off by default) so chat stays fast.
        reasoning=None,
        trace_id=ctx.trace_id,
        name="compliance_critic_reflector",
        metadata={"signal_id": ctx.signal_id, "phase": "reflector"},
    )
    usage = LLMUsage(
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )
    review = _parse_review(response.text, results)
    # Soften over-strict conversation blocks when there is no external write and
    # every subagent succeeded: only real safety findings should force a fallback.
    if (
        is_conversation
        and not proposed_external_writes
        and all(result.success for result in results)
        and not review.approved
        and not _has_hard_safety_finding(review)
    ):
        review = ComplianceReview(
            approved=True,
            findings=review.findings,
            pii_detected=review.pii_detected,
            redactions=review.redactions,
            blocked_external_writes=[],
            feedback=(
                "Approved for conversation: no external writes and no hard safety "
                "findings (tenant/PII/secrets/unsafe promise)."
            ),
        )
    return review, usage


_HARD_SAFETY_CODES = {
    "tenant_isolation",
    "cross_tenant",
    "pii",
    "pii_leak",
    "pii_detected",
    "secret",
    "credential",
    "security",
    "injection",
    "unsafe_promise",
    "fabricated_fact",
}


def _has_hard_safety_finding(review: ComplianceReview) -> bool:
    """Return True when the critic reported a real safety/policy hard stop."""
    if review.pii_detected:
        return True
    for finding in review.findings:
        code = (finding.code or "").lower()
        message = (finding.message or "").lower()
        if finding.severity == "blocker" and any(
            token in code or token in message
            for token in (
                "tenant",
                "pii",
                "password",
                "secret",
                "credential",
                "card",
                "ssn",
                "injection",
                "guaranteed refund",
                "already refunded",
                "fabricat",
            )
        ):
            return True
        if any(token in code for token in _HARD_SAFETY_CODES):
            return True
    return False


def _extract_json_object(text: str) -> str | None:
    """Pull the first JSON object out of a model response (fences allowed)."""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fence:
        return fence.group(1)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return None


def _parse_review(text: str, results: list[SubagentResult]) -> ComplianceReview:
    payload = _extract_json_object(text)
    if payload is not None:
        try:
            return ComplianceReview.model_validate_json(payload)
        except Exception:
            try:
                data = json.loads(payload)
                if isinstance(data, dict) and "approved" in data:
                    data.setdefault("feedback", "")
                    data.setdefault("findings", [])
                    return ComplianceReview.model_validate(data)
            except Exception:
                pass

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
                message="Compliance critic did not return valid review JSON.",
            )
        ],
        feedback="Compliance review blocked because the critic response was invalid.",
    )


__all__ = ["run_compliance_critic", "COMPLIANCE_CRITIC_BRIEF"]
