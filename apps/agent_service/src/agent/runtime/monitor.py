"""Performance monitor with routing-penalty feedback loop."""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Deque

from packages.observability.src.tracer import trace_event


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    severity: Severity
    metric: str
    message: str
    value: float
    threshold: float
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RoleStats:
    total: int = 0
    success: int = 0
    total_latency_ms: float = 0.0
    routing_penalty: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total else 0.0


class AnomalyDetector:
    """Sliding-window z-score anomaly detection."""

    def __init__(self, window: int = 60, sensitivity: float = 2.5) -> None:
        self._window = window
        self._sensitivity = sensitivity
        self._history: dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=window))

    def record(self, metric: str, value: float) -> dict[str, Any] | None:
        buf = self._history[metric]
        buf.append(value)
        if len(buf) < self._window // 2:
            return None
        mean = statistics.mean(buf)
        stdev = statistics.stdev(buf) if len(buf) > 1 else 0.0
        if stdev == 0:
            return None
        z_score = abs(value - mean) / stdev
        if z_score > self._sensitivity:
            return {"metric": metric, "value": value, "mean": mean, "z_score": round(z_score, 2)}
        return None


class PerformanceMonitor:
    """Collect role/tool stats and publish routing penalties."""

    THRESHOLDS = {
        "role_success_rate": (0.90, Severity.ERROR, "less_than"),
        "tool_success_rate": (0.95, Severity.WARNING, "less_than"),
        "role_avg_ms": (3000, Severity.WARNING, "greater_than"),
    }

    def __init__(self) -> None:
        self._role_stats: dict[str, RoleStats] = {}
        self._detector = AnomalyDetector()
        self._alerts: list[Alert] = []
        self._routing_penalties: dict[str, float] = {}

    def record_role_result(
        self,
        role: str,
        *,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record one subagent execution."""
        stats = self._role_stats.setdefault(role, RoleStats())
        stats.total += 1
        if success:
            stats.success += 1
        stats.total_latency_ms += latency_ms

    def update_tool_stats(self, tool_stats: dict[str, dict[str, Any]]) -> None:
        """Ingest MCP tool stats and emit alerts when needed."""
        for tool_name, payload in tool_stats.items():
            success_rate = float(payload.get("success_rate", 1.0))
            self._check_threshold("tool_success_rate", success_rate, tool_name)
            consecutive_fails = int(payload.get("consecutive_fails", 0))
            if consecutive_fails >= 3:
                trace_event(
                    "monitor.tool.degraded",
                    {"tool": tool_name, "consecutive_fails": consecutive_fails},
                )

    def refresh_penalties(self, tool_stats: dict[str, dict[str, Any]] | None = None) -> dict[str, float]:
        """Recompute routing penalties from current role and tool stats."""
        penalties: dict[str, float] = {}
        for role, stats in self._role_stats.items():
            penalty = self._routing_penalty(stats.success_rate, stats.avg_ms)
            stats.routing_penalty = penalty
            penalties[role] = penalty
            for metric, value in [
                ("role_success_rate", stats.success_rate),
                ("role_avg_ms", stats.avg_ms),
            ]:
                anomaly = self._detector.record(f"{metric}:{role}", value)
                if anomaly:
                    trace_event("monitor.anomaly", {"role": role, **anomaly})
                self._check_threshold(metric, value, role)

        if tool_stats:
            self.update_tool_stats(tool_stats)

        self._routing_penalties = penalties
        return penalties

    def get_routing_penalty(self, role: str) -> float:
        """Return the current routing penalty for a role."""
        return self._routing_penalties.get(role, self._role_stats.get(role, RoleStats()).routing_penalty)

    def get_role_stats(self) -> dict[str, dict[str, Any]]:
        """Return role stats for diagnostics."""
        return {
            role: {
                "total": stats.total,
                "success_rate": round(stats.success_rate, 3),
                "avg_ms": round(stats.avg_ms, 1),
                "routing_penalty": round(stats.routing_penalty, 3),
            }
            for role, stats in self._role_stats.items()
        }

    def summary(self) -> dict[str, Any]:
        """Return monitor summary for APIs and tests."""
        return {
            "role_stats": self.get_role_stats(),
            "routing_penalties": dict(self._routing_penalties),
            "active_alerts": [alert.__dict__ for alert in self._alerts[-10:]],
        }

    @staticmethod
    def _routing_penalty(success_rate: float, avg_ms: float) -> float:
        penalty = 0.0
        if success_rate < 0.90:
            penalty += min(0.5, (0.90 - success_rate) * 2)
        if avg_ms > 3000:
            penalty += min(0.4, (avg_ms - 3000) / 10000)
        return min(penalty, 0.9)

    def _check_threshold(self, metric: str, value: float, label: str) -> None:
        if metric not in self.THRESHOLDS:
            return
        threshold, severity, operator = self.THRESHOLDS[metric]
        triggered = (operator == "less_than" and value < threshold) or (
            operator == "greater_than" and value > threshold
        )
        if not triggered:
            return
        alert = Alert(
            severity=severity,
            metric=f"{metric}:{label}",
            message=f"{label} {metric}={value:.3f} threshold={threshold}",
            value=value,
            threshold=threshold,
        )
        self._alerts.append(alert)
        trace_event("monitor.alert", alert.__dict__)


_MONITOR: PerformanceMonitor | None = None


def get_performance_monitor() -> PerformanceMonitor:
    """Return the process-wide performance monitor."""
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = PerformanceMonitor()
    return _MONITOR


__all__ = ["PerformanceMonitor", "get_performance_monitor", "RoleStats", "Alert", "Severity"]
