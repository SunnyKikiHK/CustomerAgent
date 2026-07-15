"""Regression detection and optimization-suggestion generation.

Given the current run's per-case judge scores + aggregate metrics, and an
optional prior baseline (the last saved run), this module:

  - detects regressions: cases whose mean score dropped by more than a threshold,
    plus aggregate drops (pass-rate, per-dimension means, P95 latency).
  - generates optimization suggestions: deterministic, rule-based recommendations
    derived from the weakest dimensions, slowest phase, and failing categories.

Everything here is pure/deterministic so it runs offline and is testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A per-case mean-score drop beyond this (out of 5) is flagged a regression.
CASE_REGRESSION_DELTA = 0.75
#: An aggregate pass-rate drop beyond this fraction is flagged.
PASS_RATE_REGRESSION_DELTA = 0.05
#: A P95 latency increase beyond this fraction is flagged.
LATENCY_REGRESSION_PCT = 0.20


@dataclass
class Regression:
    """One detected regression versus the baseline."""

    kind: str  # "case" | "pass_rate" | "dimension" | "latency"
    identifier: str
    baseline: float
    current: float
    delta: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "baseline": round(self.baseline, 3),
            "current": round(self.current, 3),
            "delta": round(self.delta, 3),
        }


def _case_mean(case: dict[str, Any]) -> float:
    """Read a case's mean judge score from either flat or nested shape."""
    if "mean" in case:
        return float(case.get("mean", 0.0))
    return float(case.get("score", {}).get("mean", 0.0))


def detect_regressions(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Compare a current run (``run.to_dict()``) to a baseline of the same shape.

    Returns a list of regression dicts (JSON-ready) so the runner can serialize
    them directly. Empty list when there is no baseline or no regression.
    """
    if not baseline:
        return []

    out: list[Regression] = []

    # Per-case mean-score drops (results carry nested score.mean).
    base_cases = {c["case_id"]: c for c in baseline.get("results", [])}
    for case in current.get("results", []):
        cid = case["case_id"]
        prior = base_cases.get(cid)
        if prior is None:
            continue
        b_mean = _case_mean(prior)
        c_mean = _case_mean(case)
        if b_mean - c_mean >= CASE_REGRESSION_DELTA:
            out.append(Regression("case", cid, b_mean, c_mean, c_mean - b_mean))

    # Aggregate pass-rate.
    b_pass = float(baseline.get("summary", {}).get("pass_rate", 0.0))
    c_pass = float(current.get("summary", {}).get("pass_rate", 0.0))
    if b_pass - c_pass >= PASS_RATE_REGRESSION_DELTA:
        out.append(Regression("pass_rate", "overall", b_pass, c_pass, c_pass - b_pass))

    # Per-dimension means.
    b_dims = baseline.get("summary", {}).get("dimension_means", {})
    c_dims = current.get("summary", {}).get("dimension_means", {})
    for dim, b_val in b_dims.items():
        c_val = float(c_dims.get(dim, 0.0))
        if float(b_val) - c_val >= CASE_REGRESSION_DELTA / 2:
            out.append(Regression("dimension", dim, float(b_val), c_val, c_val - float(b_val)))

    # P95 latency increase.
    b_p95 = float(baseline.get("metrics", {}).get("latency_ms", {}).get("p95", 0.0))
    c_p95 = float(current.get("metrics", {}).get("latency_ms", {}).get("p95", 0.0))
    if b_p95 > 0 and (c_p95 - b_p95) / b_p95 >= LATENCY_REGRESSION_PCT:
        out.append(Regression("latency", "p95_latency_ms", b_p95, c_p95, c_p95 - b_p95))

    return [r.to_dict() for r in out]


def build_optimization_suggestions(
    *,
    summary: dict[str, Any],
    metrics: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[str]:
    """Produce rule-based optimization suggestions from a run's parts.

    Args mirror what the runner has on hand: the ``summary`` dict (dimension
    means + by_category), the aggregate ``metrics`` dict, and the per-case
    ``results`` list (each with nested ``score``).
    """
    suggestions: list[str] = []
    dims = summary.get("dimension_means", {})
    by_cat = {cat: b.get("pass_rate", 1.0) for cat, b in summary.get("by_category", {}).items()}

    # Weakest dimension.
    if dims:
        weakest = min(dims, key=lambda k: dims[k])
        if float(dims[weakest]) < 3.5:
            advice = {
                "relevance": "Tighten intent routing / clarifying-question prompts so answers target the actual ask.",
                "accuracy": "Strengthen grounding: require playbook citations and block unsupported claims in the critic.",
                "completeness": "Add checklists to specialist skills (e.g. always request order id) so answers cover required parts.",
                "usefulness": "Prompt specialists to end with a concrete next step or question.",
            }.get(weakest, f"Improve {weakest}.")
            suggestions.append(f"Weakest dimension '{weakest}' ({dims[weakest]}): {advice}")

    # Failing categories.
    for cat, rate in sorted(by_cat.items(), key=lambda kv: kv[1]):
        if float(rate) < 0.7:
            suggestions.append(
                f"Category '{cat}' pass-rate {rate}: review those cases; "
                f"{'harden refusals / injection handling' if cat == 'adversarial' else 'add targeted skill guidance'}."
            )

    # Hard violations are always worth surfacing explicitly.
    hard = [r["case_id"] for r in results if r.get("score", {}).get("hard_violation")]
    if hard:
        suggestions.append(
            f"{len(hard)} case(s) tripped a hard safety rule ({', '.join(hard[:5])}): "
            "review the compliance critic / must-not-leak rules for those inputs."
        )

    # Latency / phase breakdown.
    breakdown = metrics.get("time_breakdown_pct", {})
    if breakdown:
        slowest = max(breakdown, key=lambda k: breakdown[k])
        if float(breakdown.get(slowest, 0)) >= 50.0:
            phase_advice = {
                "llm": "LLM calls dominate: keep reasoning off for routing/critic, cache, or use a smaller worker model.",
                "reasoning": "Planning dominates: consider the deterministic planner fast-path for simple turns.",
                "tool": "Tool execution dominates: cache retrieval and parallelize independent tool calls.",
                "other": "Uncategorized time dominates: add spans to locate the cost.",
            }.get(slowest, "")
            suggestions.append(f"'{slowest}' is {breakdown[slowest]}% of measured time: {phase_advice}")

    p95 = metrics.get("latency_ms", {}).get("p95", 0.0)
    if float(p95) > 20000:
        suggestions.append(
            f"P95 latency {p95}ms is high (>20s): reduce max_react_steps, disable reasoning on the hot path, "
            "or shrink the planner/critic model."
        )

    if not suggestions:
        suggestions.append("No high-priority issues detected; scores and latency are within thresholds.")
    return suggestions


__all__ = [
    "Regression",
    "detect_regressions",
    "build_optimization_suggestions",
    "CASE_REGRESSION_DELTA",
    "PASS_RATE_REGRESSION_DELTA",
    "LATENCY_REGRESSION_PCT",
]
