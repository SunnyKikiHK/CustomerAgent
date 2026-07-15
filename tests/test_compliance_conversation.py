"""Offline tests for compliance-critic conversation soften rules."""

from __future__ import annotations

from packages.agent.src.orchestration_types import ComplianceFinding, ComplianceReview
from packages.agent.src.subagent_types import AgentRole, SubagentResult


def test_parse_review_accepts_fenced_json():
    from apps.agent_service.src.agent.subagents.compliance_critic import _parse_review

    text = """```json
{"approved": true, "findings": [], "feedback": "ok"}
```"""
    review = _parse_review(text, [])
    assert review.approved is True
    assert review.feedback == "ok"


def test_hard_safety_finding_detects_pii_blocker():
    from apps.agent_service.src.agent.subagents.compliance_critic import _has_hard_safety_finding

    review = ComplianceReview(
        approved=False,
        findings=[
            ComplianceFinding(
                code="pii_leak",
                severity="blocker",
                message="Exposed a card number",
            )
        ],
        feedback="blocked",
    )
    assert _has_hard_safety_finding(review) is True


def test_missing_order_id_is_not_hard_safety():
    from apps.agent_service.src.agent.subagents.compliance_critic import _has_hard_safety_finding

    review = ComplianceReview(
        approved=False,
        findings=[
            ComplianceFinding(
                code="missing_order_id",
                severity="medium",
                message="Refund help without an order id yet",
            )
        ],
        feedback="needs order id",
    )
    assert _has_hard_safety_finding(review) is False


def test_conversation_soft_approve_path_logic():
    """Mirror the soft-approve gate used after a non-hard conversation reject."""
    from apps.agent_service.src.agent.subagents.compliance_critic import _has_hard_safety_finding

    results = [
        SubagentResult(
            task_id="billing",
            role=AgentRole.BILLING,
            success=True,
            markdown="Please share your order number so I can help with the refund.",
        )
    ]
    review = ComplianceReview(
        approved=False,
        findings=[
            ComplianceFinding(
                code="ungrounded_refund",
                severity="medium",
                message="No order id yet",
            )
        ],
        feedback="reject",
    )
    proposed_external_writes: list = []
    is_conversation = True
    should_soft_approve = (
        is_conversation
        and not proposed_external_writes
        and all(result.success for result in results)
        and not review.approved
        and not _has_hard_safety_finding(review)
    )
    assert should_soft_approve is True
