"""Tests for customer-visible response assembly."""

from __future__ import annotations

from packages.agent.src.orchestration_types import ComplianceReview
from packages.agent.src.subagent_types import AgentRole, SubagentResult


def test_customer_facing_markdown_excludes_playbook_retrieval():
    from apps.agent_service.src.agent.orchestrator.reducer import (
        customer_facing_markdown,
        finalize_decision,
    )

    results = [
        SubagentResult(
            task_id="playbooks",
            role=AgentRole.PLAYBOOK_RETRIEVAL,
            success=True,
            markdown="## Playbook Retrieval Results\n\nInternal ranking only.",
        ),
        SubagentResult(
            task_id="billing",
            role=AgentRole.BILLING,
            success=True,
            markdown="Usage-based charges are generally non-refundable once delivered.",
        ),
    ]
    text = customer_facing_markdown(results)
    assert "Playbook Retrieval Results" not in text
    assert "non-refundable" in text

    review = ComplianceReview(approved=True, feedback="ok")
    decision = finalize_decision(results, review, [])
    assert "Playbook Retrieval Results" not in decision.response_text
    assert "non-refundable" in decision.response_text


def test_customer_facing_markdown_excludes_health_analysis():
    from apps.agent_service.src.agent.orchestrator.reducer import customer_facing_markdown

    results = [
        SubagentResult(
            task_id="health",
            role=AgentRole.HEALTH_ANALYSIS,
            success=True,
            markdown="Internal health score analysis for CSM only.",
        ),
        SubagentResult(
            task_id="outreach",
            role=AgentRole.OUTREACH_DRAFT,
            success=True,
            markdown="Hi Acme, we noticed usage dropped and want to help.",
        ),
    ]
    text = customer_facing_markdown(results)
    assert "Internal health" not in text
    assert "usage dropped" in text
