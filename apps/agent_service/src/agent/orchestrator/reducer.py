"""Reducers for turning subagent evidence into final decisions."""

from __future__ import annotations

from typing import Any

from packages.agent.src.orchestration_types import ComplianceReview, FinalDecision
from packages.agent.src.subagent_types import SubagentResult

from apps.agent_service.src.agent.orchestrator.policy import is_customer_visible_field


def extract_proposed_external_writes(results: list[SubagentResult]) -> list[dict[str, Any]]:
    """Collect proposed write payloads from subagent result data."""
    writes: list[dict[str, Any]] = []
    for result in results:
        proposed = result.data.get("proposed_external_writes", [])
        if isinstance(proposed, list):
            writes.extend(item for item in proposed if isinstance(item, dict))
    return writes


def finalize_decision(
    results: list[SubagentResult],
    review: ComplianceReview,
    proposed_external_writes: list[dict[str, Any]],
) -> FinalDecision:
    """Build the durable final decision after compliance review."""
    if not review.approved:
        return FinalDecision(
            action="blocked_or_escalated",
            response_text=review.feedback,
            approved_external_writes=[],
            subagent_results=results,
            compliance_review=review,
            reasoning_summary="ComplianceCriticAgent blocked output or mutation.",
        )

    redacted_writes, visible_violations = apply_redactions(
        proposed_external_writes, review
    )

    # Redaction must never silently mask customer-visible content. If a flagged
    # value lands in a customer-facing field, block and force a replan instead
    # of shipping "[REDACTED]" to the customer.
    if visible_violations:
        offending = ", ".join(sorted(visible_violations))
        return FinalDecision(
            action="replan_required",
            response_text=(
                "Blocked: sensitive content was flagged inside customer-visible "
                f"fields ({offending}). The draft must be rewritten rather than masked."
            ),
            approved_external_writes=[],
            subagent_results=results,
            compliance_review=review,
            reasoning_summary=(
                "Reflector redactions intersected customer-visible content; "
                "replanning instead of masking."
            ),
        )

    markdown_sections = [result.markdown for result in results if result.success and result.markdown]
    return FinalDecision(
        action="emit_or_execute_approved_payload",
        response_text="\n\n".join(markdown_sections) if markdown_sections else review.feedback,
        approved_external_writes=redacted_writes,
        subagent_results=results,
        compliance_review=review,
        reasoning_summary="Planner, delegated subagents, and reflector completed successfully.",
    )


def apply_redactions(
    writes: list[dict[str, Any]],
    review: ComplianceReview,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Apply redactions to non-customer-visible fields only.

    Returns the redacted write payloads and the set of customer-visible field
    names where a redaction target was found. Customer-visible fields are never
    masked in place; a non-empty violation set signals the caller to replan.
    """
    if not review.redactions:
        return [_deep_copy(write) for write in writes], set()

    violations: set[str] = set()
    redacted = [
        _redact_node(write, review.redactions, violations, in_visible=False)
        for write in writes
    ]
    return redacted, violations


def _redact_node(
    node: Any,
    redactions: dict[str, str],
    violations: set[str],
    *,
    in_visible: bool,
    field_name: str | None = None,
) -> Any:
    """Recursively redact non-visible string values, flag visible hits."""
    if isinstance(node, dict):
        return {
            key: _redact_node(
                value,
                redactions,
                violations,
                in_visible=in_visible or is_customer_visible_field(str(key)),
                field_name=str(key),
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            _redact_node(item, redactions, violations, in_visible=in_visible, field_name=field_name)
            for item in node
        ]
    if isinstance(node, str):
        return _redact_string(node, redactions, violations, in_visible, field_name)
    return node


def _redact_string(
    value: str,
    redactions: dict[str, str],
    violations: set[str],
    in_visible: bool,
    field_name: str | None,
) -> str:
    """
    Mask a non-visible string; record (but do not mask) visible hits.
    
    In a customer-visible field: record the field name in violations and skip masking. This is what prevents the customer from ever receiving [REDACTED_PHONE] in an email body.
    In an internal field: replace raw with the masked value.
    """
    result = value
    for raw, replacement in redactions.items():
        if raw and raw in result:
            if in_visible:
                violations.add(field_name or "customer_visible")
                continue # do NOT mask if the section is visible to the customer
            result = result.replace(raw, replacement)
    return result


def _deep_copy(node: Any) -> Any:
    if isinstance(node, dict):
        return {key: _deep_copy(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_deep_copy(item) for item in node]
    return node


_extract_proposed_external_writes = extract_proposed_external_writes
_finalize_decision = finalize_decision
_apply_redactions = apply_redactions


__all__ = [
    "extract_proposed_external_writes",
    "finalize_decision",
    "apply_redactions",
    "_extract_proposed_external_writes",
    "_finalize_decision",
    "_apply_redactions",
]
