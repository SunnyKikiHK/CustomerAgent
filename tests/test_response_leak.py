"""Regression tests for customer-facing response leaks.

Three bugs the evaluation surfaced, all fixed without architecture change:

1. Code-fenced JSON (```json ... ```) from the model leaked verbatim to the
   customer because ``json.loads`` failed on the fence.
2. A subagent that returned valid JSON with empty ``markdown`` fell back to the
   raw JSON blob as the reply.
3. When no answer-role markdown existed, the reducer used ``review.feedback``
   (an internal critic note) as the customer reply.
"""

from __future__ import annotations

from apps.agent_service.src.agent.runtime.react_loop import ReActLoop, _strip_code_fence
from apps.agent_service.src.agent.orchestrator.reducer import (
    _EMPTY_ANSWER_FALLBACK,
    customer_facing_markdown,
    finalize_decision,
)
from packages.agent.src.orchestration_types import ComplianceReview
from packages.agent.src.subagent_types import AgentRole, SubagentResult


# ── 1. Code-fence stripping ──────────────────────────────────────────────────

def test_strip_code_fence_json():
    fenced = '```json\n{"markdown": "hi", "tool_calls": []}\n```'
    assert _strip_code_fence(fenced).startswith("{")
    assert "```" not in _strip_code_fence(fenced)


def test_parse_fenced_json_extracts_markdown():
    parsed = ReActLoop._parse_model_response(
        '```json\n{"markdown": "Hello there", "data": {}, "tool_calls": []}\n```'
    )
    assert parsed["markdown"] == "Hello there"
    assert parsed["tool_calls"] == []


def test_parse_non_json_returns_defenced_text():
    # A raw ```json blob that is not valid JSON must not surface verbatim.
    parsed = ReActLoop._parse_model_response("```\nplain text reply\n```")
    assert parsed["markdown"] == "plain text reply"
    assert "```" not in parsed["markdown"]


def test_parse_prose_plus_tool_call_json_recovers_tool_calls():
    # Reasoning prose followed by a raw JSON tool call must execute the tool,
    # not leak the raw blob to the customer.
    raw = (
        "I'll delegate this refund request to the Billing specialist.\n\n"
        '{"tool_calls": [{"name": "delegate_billing", "arguments": {"task": "refund"}}],'
        ' "markdown": "", "data": {}}'
    )
    parsed = ReActLoop._parse_model_response(raw)
    assert parsed["tool_calls"] == [
        {"name": "delegate_billing", "arguments": {"task": "refund"}}
    ]
    assert "tool_calls" not in parsed["markdown"]


def test_parse_prose_plus_trailing_json_strips_blob():
    # Prose with a trailing non-tool JSON blob: only the prose is the reply.
    raw = 'Here is your answer.\n\n{"some": "data", "extra": 1}'
    parsed = ReActLoop._parse_model_response(raw)
    assert parsed["tool_calls"] == []
    assert parsed["markdown"] == "Here is your answer."
    assert "{" not in parsed["markdown"]


# ── 2. Empty-markdown must not leak raw JSON ─────────────────────────────────

def test_valid_json_empty_markdown_yields_empty_not_blob():
    parsed = ReActLoop._parse_model_response('{"markdown": "", "tool_calls": []}')
    assert parsed["markdown"] == ""  # empty, not the JSON blob


# ── 3. Reducer never leaks internal critic text ──────────────────────────────

def _approved_review() -> ComplianceReview:
    return ComplianceReview(approved=True, feedback="INTERNAL: approved, no writes")


def test_reducer_uses_fallback_not_feedback_when_no_answer():
    # No answer-role markdown -> must use the safe fallback, not review.feedback.
    results = [
        SubagentResult(
            task_id="playbook",
            role=AgentRole.PLAYBOOK_RETRIEVAL,
            success=True,
            markdown="internal playbook evidence",
        )
    ]
    decision = finalize_decision(results, _approved_review(), [])
    assert decision.response_text == _EMPTY_ANSWER_FALLBACK
    assert "INTERNAL" not in decision.response_text


def test_reducer_uses_answer_markdown_when_present():
    results = [
        SubagentResult(
            task_id="answer",
            role=AgentRole.GENERAL,
            success=True,
            markdown="Here is your grounded answer.",
        )
    ]
    decision = finalize_decision(results, _approved_review(), [])
    assert decision.response_text == "Here is your grounded answer."


def test_customer_facing_markdown_excludes_retrieval_roles():
    results = [
        SubagentResult(task_id="pb", role=AgentRole.PLAYBOOK_RETRIEVAL, success=True, markdown="pb"),
        SubagentResult(task_id="a", role=AgentRole.GENERAL, success=True, markdown="answer"),
    ]
    assert customer_facing_markdown(results) == "answer"
