"""Offline tests for the evaluation harness (cases, judge, metrics, regression)."""

from __future__ import annotations

import pytest

from packages.evaluation.src.cases import ALL_CASES, cases_by_category
from packages.evaluation.src.judge import JudgeScore, judge_response
from packages.evaluation.src.metrics import RunSample, build_metrics, _percentile, _phase_of
from packages.evaluation.src.regression import (
    build_optimization_suggestions,
    detect_regressions,
)
from packages.observability.src.collector import SpanRecord


# ── Curated case set ──────────────────────────────────────────────────────────

def test_case_set_is_large_and_unique():
    assert len(ALL_CASES) >= 50
    ids = [c.id for c in ALL_CASES]
    assert len(ids) == len(set(ids))  # unique ids
    cats = cases_by_category()
    for required in ("ambiguous", "edge", "adversarial", "tool_error", "policy"):
        assert required in cats and cats[required], required


# ── Judge (heuristic fallback + hard override) ────────────────────────────────

@pytest.mark.asyncio
async def test_hard_violation_forces_fail(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    case = next(c for c in ALL_CASES if c.must_not_mention)
    bad = case.must_not_mention[0]
    score = await judge_response(case, f"sure, here it is: {bad}")
    assert score.hard_violation == bad
    assert score.accuracy == 1.0
    assert score.passed is False


@pytest.mark.asyncio
async def test_heuristic_judge_scores_without_llm(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    case = next(c for c in ALL_CASES if c.category == "ambiguous")
    score = await judge_response(case, "Which part specifically is not working for you?")
    assert score.method == "heuristic"
    assert 1.0 <= score.mean <= 5.0


# ── Metrics ───────────────────────────────────────────────────────────────────

def test_percentile_and_phase_classification():
    assert _percentile([], 95) == 0.0
    assert _percentile([10, 20, 30, 40, 100], 95) == 100
    assert _phase_of("tool.query_health") == "tool"
    assert _phase_of("compliance.review") == "llm"
    assert _phase_of("planner.build") == "reasoning"


def test_build_metrics_breakdown_and_p95():
    samples = [
        RunSample(
            case_id="a",
            latency_ms=1000.0,
            tokens=500,
            success=True,
            spans=[
                SpanRecord("planner.build", 200.0, "ok"),
                SpanRecord("tool.query_playbooks", 300.0, "ok"),
                SpanRecord("compliance.review", 500.0, "ok"),
            ],
        ),
        RunSample(case_id="b", latency_ms=3000.0, tokens=0, success=False, spans=[]),
    ]
    m = build_metrics(samples).to_dict()
    assert m["count"] == 2 and m["successes"] == 1
    assert m["tokens_per_successful_task"] == 500.0
    assert m["latency_ms"]["p95"] == 3000.0
    # 200 reasoning / 300 tool / 500 llm = 1000 total
    assert m["time_breakdown_pct"]["llm"] == 50.0
    assert m["time_breakdown_pct"]["tool"] == 30.0
    assert m["time_breakdown_pct"]["reasoning"] == 20.0


# ── Regression detection ──────────────────────────────────────────────────────

def test_detect_regressions_flags_case_and_pass_rate_drop():
    baseline = {
        "summary": {"pass_rate": 0.9, "dimension_means": {"accuracy": 4.5}},
        "metrics": {"latency_ms": {"p95": 1000.0}},
        "results": [{"case_id": "x", "score": {"mean": 4.5}}],
    }
    current = {
        "summary": {"pass_rate": 0.7, "dimension_means": {"accuracy": 4.4}},
        "metrics": {"latency_ms": {"p95": 1050.0}},
        "results": [{"case_id": "x", "score": {"mean": 3.0}}],
    }
    regs = detect_regressions(current=current, baseline=baseline)
    kinds = {r["kind"] for r in regs}
    assert "case" in kinds  # 4.5 -> 3.0 drop
    assert "pass_rate" in kinds  # 0.9 -> 0.7 drop


def test_detect_regressions_empty_without_baseline():
    assert detect_regressions(current={"results": []}, baseline=None) == []


def test_optimization_suggestions_react_to_weak_scores():
    summary = {
        "dimension_means": {"relevance": 4.0, "accuracy": 2.5, "completeness": 4.0, "usefulness": 4.0},
        "by_category": {"adversarial": {"pass_rate": 0.4}},
    }
    metrics = {"time_breakdown_pct": {"llm": 80.0, "tool": 10.0, "reasoning": 10.0}, "latency_ms": {"p95": 25000}}
    results = [{"case_id": "adv-01", "score": {"hard_violation": "password"}}]
    sug = build_optimization_suggestions(summary=summary, metrics=metrics, results=results)
    text = " ".join(sug).lower()
    assert "accuracy" in text  # weakest dimension flagged
    assert "adversarial" in text  # failing category flagged
    assert "hard safety" in text  # hard violation surfaced
