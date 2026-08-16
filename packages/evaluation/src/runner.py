"""Evaluation runner: execute cases, judge, measure, detect regressions, report.

Two execution modes:
  - live:    feed each case through the real conversation pipeline
             (``run_conversation_agent``) when infra + OPENROUTER_API_KEY are
             present. Per-run spans are captured via ``collect_spans`` so the
             metrics module can compute the reasoning/tool/LLM breakdown.
  - offline: skip the pipeline; score pre-recorded responses (used by tests).

Outputs (written under ``eval_results/`` by default):
  - ``eval_results.json``  — full per-case scores + metrics + regressions
  - ``eval_report.md``     — human-readable summary report
  - ``baseline.json``      — current run promoted to baseline (via --promote)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.evaluation.src.cases import ALL_CASES, EvalCase
from packages.evaluation.src.judge import DIMENSIONS, JudgeScore, judge_response
from packages.evaluation.src.metrics import RunSample, build_metrics
from packages.evaluation.src.regression import (
    build_optimization_suggestions,
    detect_regressions,
)

_DEFAULT_TENANT = "11111111-1111-1111-1111-111111111111"
_DEFAULT_CUSTOMER = "22222222-2222-2222-2222-222222222222"


@dataclass
class CaseResult:
    """Per-case evaluation outcome."""

    case_id: str
    category: str
    message: str
    response: str
    score: JudgeScore
    latency_ms: float
    tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "message": self.message[:200],
            "response": self.response[:1500],
            "latency_ms": round(self.latency_ms, 1),
            "tokens": self.tokens,
            "score": self.score.to_dict(),
        }


@dataclass
class EvalRun:
    """A whole evaluation run: results + metrics + regressions + suggestions."""

    run_id: str
    mode: str
    results: list[CaseResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    regressions: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        passed = sum(1 for r in self.results if r.score.passed)
        by_dim = {
            dim: round(
                sum(getattr(r.score, dim) for r in self.results) / len(self.results), 3
            )
            if self.results
            else 0.0
            for dim in DIMENSIONS
        }
        by_cat: dict[str, dict[str, Any]] = {}
        for r in self.results:
            b = by_cat.setdefault(r.category, {"n": 0, "passed": 0, "mean_sum": 0.0})
            b["n"] += 1
            b["passed"] += int(r.score.passed)
            b["mean_sum"] += r.score.mean
        for b in by_cat.values():
            b["pass_rate"] = round(b["passed"] / b["n"], 3)
            b["mean"] = round(b["mean_sum"] / b["n"], 3)
            del b["mean_sum"]
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "cases": len(self.results),
            "passed": passed,
            "pass_rate": round(passed / len(self.results), 3) if self.results else 0.0,
            "dimension_means": by_dim,
            "by_category": by_cat,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "metrics": self.metrics,
            "regressions": self.regressions,
            "suggestions": self.suggestions,
            "results": [r.to_dict() for r in self.results],
        }


async def _run_one_live(case: EvalCase) -> tuple[str, float, int, list]:
    """Run one case through the real conversation pipeline with span capture."""
    from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
    from packages.agent.src.orchestration_types import ConversationAgentInput
    from packages.agent.src.types import SessionContext
    from packages.observability.src.collector import collect_spans

    from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
        run_conversation_loop,
    )

    session_id = f"eval-{case.id}-{uuid.uuid4().hex[:6]}"
    agent_input = ConversationAgentInput(
        tenant_id=_DEFAULT_TENANT,
        customer_id=_DEFAULT_CUSTOMER,
        session_id=session_id,
        message=ChatMessage(
            tenant_id=_DEFAULT_TENANT,
            customer_id=_DEFAULT_CUSTOMER,
            session_id=session_id,
            role=ChatMessageRole.USER,
            content=case.message,
        ),
        stream=False,
    )
    ctx = SessionContext(
        tenant_id=_DEFAULT_TENANT,
        user_id=_DEFAULT_CUSTOMER,
        session_id=session_id,
        trace_id=session_id,
    )
    started = time.monotonic()
    with collect_spans() as spans:
        try:
            response = await run_conversation_loop(agent_input, ctx)
            text = response.text or ""
            tokens = (
                (getattr(response, "planner_tokens", 0) or 0)
                + (getattr(response, "executor_tokens", 0) or 0)
                + (getattr(response, "critic_tokens", 0) or 0)
            )
        except Exception as exc:  # a crash is itself an evaluation signal
            text = f"[pipeline error: {type(exc).__name__}]"
            tokens = 0
    latency_ms = (time.monotonic() - started) * 1000.0
    return text, latency_ms, tokens, list(spans)


async def run_evaluation(
    *,
    cases: tuple[EvalCase, ...] = ALL_CASES,
    mode: str = "live",
    recorded: dict[str, str] | None = None,
    baseline: dict[str, Any] | None = None,
) -> EvalRun:
    """Execute the evaluation set and assemble a full run.

    - mode="live": run each case through the pipeline (needs infra + key).
    - mode="offline": score ``recorded`` responses ({case_id: response}).
    """
    run = EvalRun(run_id=uuid.uuid4().hex[:12], mode=mode)
    samples: list[RunSample] = []
    recorded = recorded or {}

    if mode == "live":
        # Disable the conversation->signal bridge for the eval so chat turns do
        # not spawn background ProcessSignalWorkflows that contend for the worker
        # and inflate measured latency. Only affects proactive follow-up, not the
        # chat answer being evaluated.
        import os

        os.environ.setdefault("CONVERSATION_SIGNAL_BRIDGE", "0")

    for case in cases:
        if mode == "live":
            text, latency_ms, tokens, spans = await _run_one_live(case)
        else:
            text = recorded.get(case.id, "")
            latency_ms, tokens, spans = 0.0, 0, []

        score = await judge_response(case, text)
        run.results.append(
            CaseResult(
                case_id=case.id,
                category=case.category,
                message=case.message,
                response=text,
                score=score,
                latency_ms=latency_ms,
                tokens=tokens,
            )
        )
        samples.append(
            RunSample(
                case_id=case.id,
                latency_ms=latency_ms,
                tokens=tokens,
                success=score.passed,
                spans=spans,
            )
        )

    run.metrics = build_metrics(samples).to_dict()
    summary = run.summary()
    if baseline is not None:
        run.regressions = detect_regressions(current=run.to_dict(), baseline=baseline)
    run.suggestions = build_optimization_suggestions(
        summary=summary, metrics=run.metrics, results=[r.to_dict() for r in run.results]
    )
    return run


def write_results(run: EvalRun, out_dir: str | Path = "eval_results") -> dict[str, str]:
    """Write eval_results.json + eval_report.md; return the written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "eval_results.json"
    md_path = out / "eval_report.md"
    json_path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    md_path.write_text(_render_report(run), encoding="utf-8")
    return {"json": str(json_path), "report": str(md_path)}


def _render_report(run: EvalRun) -> str:
    """Render a human-readable markdown report."""
    s = run.summary()
    m = run.metrics
    lat = m.get("latency_ms", {})
    tb = m.get("time_breakdown_pct", {})
    lines = [
        "# Agent Evaluation Report",
        "",
        f"- Run: `{run.run_id}`  mode: **{run.mode}**",
        f"- Cases: **{s['cases']}**  Passed: **{s['passed']}**  "
        f"Pass rate: **{s['pass_rate'] * 100:.1f}%**",
        "",
        "## LLM-as-Judge (mean 1-5)",
        "",
        "| Dimension | Mean |",
        "|---|---|",
    ]
    for dim, val in s["dimension_means"].items():
        lines.append(f"| {dim} | {val} |")
    lines += ["", "## By category", "", "| Category | N | Pass rate | Mean |", "|---|---|---|---|"]
    for cat, b in sorted(s["by_category"].items()):
        lines.append(f"| {cat} | {b['n']} | {b['pass_rate'] * 100:.1f}% | {b['mean']} |")
    lines += [
        "",
        "## Performance metrics",
        "",
        f"- P50 latency: **{lat.get('p50', 0)} ms**",
        f"- P95 latency: **{lat.get('p95', 0)} ms**",
        f"- Avg latency: **{lat.get('avg', 0)} ms**",
        f"- Tokens per successful task: **{m.get('tokens_per_successful_task', 0)}**",
        f"- Cost per resolution: **${m.get('cost_per_resolution_usd', 0)}**",
        "",
        "### Time breakdown (reasoning vs tool vs LLM)",
        "",
        f"- reasoning: **{tb.get('reasoning', 0)}%**",
        f"- tool execution: **{tb.get('tool', 0)}%**",
        f"- LLM calls: **{tb.get('llm', 0)}%**",
        f"- other: **{tb.get('other', 0)}%**",
    ]
    if run.regressions:
        lines += ["", "## Regressions vs baseline", ""]
        for r in run.regressions:
            lines.append(
                f"- **{r['kind']} / {r['identifier']}**: "
                f"{r['baseline']} → {r['current']} (Δ {r['delta']})"
            )
    else:
        lines += ["", "## Regressions vs baseline", "", "- None detected."]
    lines += ["", "## Optimization suggestions", ""]
    for sug in run.suggestions:
        lines.append(f"- {sug}")
    lines += ["", "## Failing / flagged cases", ""]
    flagged = [r for r in run.results if not r.score.passed]
    if not flagged:
        lines.append("- None.")
    for r in flagged[:25]:
        hv = f" [hard: {r.score.hard_violation}]" if r.score.hard_violation else ""
        lines.append(f"- `{r.case_id}` ({r.category}) mean={r.score.mean}{hv}")
    return "\n".join(lines) + "\n"


__all__ = ["CaseResult", "EvalRun", "run_evaluation", "write_results"]
