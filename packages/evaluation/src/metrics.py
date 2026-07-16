"""Aggregate performance metrics for an evaluation run.

Computes the metrics the project plan calls for:
  - P95 (and P50) end-to-end latency
  - tokens per successful task
  - cost per resolution (successful task)
  - time-spent breakdown: reasoning/planning vs tool execution vs LLM calls

Per-phase timing comes from the span collector (``packages.observability.src
.collector``): each pipeline run is wrapped in ``collect_spans()`` and the
resulting ``SpanRecord`` list is classified into phases by span name.

Cost uses a simple per-1K-token price from ``EVAL_COST_PER_1K_TOKENS`` (USD;
default 0.0 so offline runs report 0 rather than a fabricated number).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

from packages.observability.src.collector import SpanRecord

#: Outer wrapper spans that *contain* the phase spans below. Counting them in
#: the breakdown would double-count all nested time, so they are excluded from
#: the reasoning/tool/LLM split (their duration is still the end-to-end latency).
#: ``executor.delegate`` wraps the subagent + tool spans, so it is a wrapper too.
_WRAPPER_PREFIXES = ("conversation.run", "signal.process", "qbr.generate", "executor.delegate")

#: Span-name prefixes grouped into the three reported phases (leaf spans only).
_REASONING_PREFIXES = ("planner.",)
_TOOL_PREFIXES = ("tool.",)
_LLM_PREFIXES = ("compliance.review", "subagent", "evaluation.judge")


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0,100]); 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    rank = max(1, min(len(ordered), int(round(pct / 100.0 * len(ordered) + 0.5))))
    return round(ordered[rank - 1], 2)


def _phase_of(span_name: str) -> str:
    """Classify a span into wrapper / reasoning / tool / llm / other.

    ``wrapper`` spans are excluded from the phase breakdown (they contain the
    other spans and would double-count). Wrapper is checked first.
    """
    if span_name.startswith(_WRAPPER_PREFIXES):
        return "wrapper"
    if span_name.startswith(_TOOL_PREFIXES):
        return "tool"
    if span_name.startswith(_LLM_PREFIXES):
        return "llm"
    if span_name.startswith(_REASONING_PREFIXES):
        return "reasoning"
    return "other"


def cost_per_1k_tokens() -> float:
    """USD per 1K tokens from ``EVAL_COST_PER_1K_TOKENS`` (default 0.0)."""
    raw = (os.getenv("EVAL_COST_PER_1K_TOKENS") or "0").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


@dataclass
class RunSample:
    """One evaluated case's measured performance."""

    case_id: str
    latency_ms: float
    tokens: int
    success: bool
    spans: list[SpanRecord] = field(default_factory=list)


@dataclass
class MetricsReport:
    """Aggregate metrics across all samples."""

    count: int
    successes: int
    p50_latency_ms: float
    p95_latency_ms: float
    avg_latency_ms: float
    tokens_per_successful_task: float
    cost_per_resolution_usd: float
    total_tokens: int
    phase_ms: dict[str, float]
    phase_pct: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "successes": self.successes,
            "success_rate": round(self.successes / self.count, 3) if self.count else 0.0,
            "latency_ms": {
                "p50": self.p50_latency_ms,
                "p95": self.p95_latency_ms,
                "avg": self.avg_latency_ms,
            },
            "tokens_per_successful_task": self.tokens_per_successful_task,
            "cost_per_resolution_usd": self.cost_per_resolution_usd,
            "total_tokens": self.total_tokens,
            "time_breakdown_ms": self.phase_ms,
            "time_breakdown_pct": self.phase_pct,
        }


def build_metrics(samples: Iterable[RunSample]) -> MetricsReport:
    """Aggregate a set of run samples into the reported metrics."""
    samples = list(samples)
    count = len(samples)
    latencies = [s.latency_ms for s in samples]
    successes = [s for s in samples if s.success]
    success_tokens = sum(s.tokens for s in successes)
    total_tokens = sum(s.tokens for s in samples)

    tokens_per_task = round(success_tokens / len(successes), 1) if successes else 0.0
    price = cost_per_1k_tokens()
    cost_per_resolution = (
        round((success_tokens / 1000.0) * price / len(successes), 6)
        if successes and price > 0
        else 0.0
    )

    # Phase breakdown across every collected span in every sample.
    phase_ms: dict[str, float] = {"reasoning": 0.0, "tool": 0.0, "llm": 0.0, "other": 0.0}
    for sample in samples:
        for span in sample.spans:
            phase = _phase_of(span.name)
            if phase == "wrapper":
                continue  # excluded: contains the leaf spans below
            phase_ms[phase] += span.duration_ms
    phase_ms = {k: round(v, 1) for k, v in phase_ms.items()}
    total_phase = sum(phase_ms.values())
    phase_pct = (
        {k: round(100.0 * v / total_phase, 1) for k, v in phase_ms.items()}
        if total_phase > 0
        else {k: 0.0 for k in phase_ms}
    )

    return MetricsReport(
        count=count,
        successes=len(successes),
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        avg_latency_ms=round(sum(latencies) / count, 2) if count else 0.0,
        tokens_per_successful_task=tokens_per_task,
        cost_per_resolution_usd=cost_per_resolution,
        total_tokens=total_tokens,
        phase_ms=phase_ms,
        phase_pct=phase_pct,
    )


__all__ = ["RunSample", "MetricsReport", "build_metrics", "cost_per_1k_tokens", "_percentile", "_phase_of"]
