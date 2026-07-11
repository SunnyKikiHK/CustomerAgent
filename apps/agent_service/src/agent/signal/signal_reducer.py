"""Signal-specific result reduction helpers."""

from __future__ import annotations

from typing import Any

from packages.agent.src.orchestration_types import ComplianceReview, FinalDecision
from packages.agent.src.subagent_types import SubagentResult

from apps.agent_service.src.agent.orchestrator.reducer import finalize_decision


def reduce_signal_decision(
    results: list[SubagentResult],
    review: ComplianceReview,
    proposed_external_writes: list[dict[str, Any]],
) -> FinalDecision:
    """Reduce signal subagent evidence into a proactive action decision."""
    decision = finalize_decision(results, review, proposed_external_writes)
    if decision.action == "emit_or_execute_approved_payload":
        decision.reasoning_summary = (
            "Signal reducer approved proactive outreach or escalation payload."
        )
    return decision


__all__ = ["reduce_signal_decision"]
