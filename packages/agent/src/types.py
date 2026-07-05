"""Core Pydantic data models shared across the agent engine.

These dependency-light types can be imported by orchestrators, subagents,
runtime components, and workflow code without pulling in provider SDKs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from packages.agent.src.subagent_types import SubagentResult


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(str, Enum):
    """Legacy low-level task type retained during migration."""

    QUERY = "query"
    MUTATION = "mutation"


class CustomerSignal(BaseModel):
    """Business event that triggers a proactive signal-agent run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    customer_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def health_score(self) -> float | None:
        value = self.payload.get("health_score")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @property
    def signal_text(self) -> str:
        """Human-readable description of the signal for planner input."""
        type_labels = {
            "usage_drop": f"Usage dropped {self.payload.get('pct', '?')}% week-over-week",
            "nps_change": (
                f"NPS score changed from {self.payload.get('from_nps')} "
                f"to {self.payload.get('to_nps')}"
            ),
            "renewal_due": (
                f"Renewal in {self.payload.get('days', '?')} days, "
                f"health score {self.payload.get('health_score')}"
            ),
            "support_ticket": f"New support ticket: {self.payload.get('subject', '')}",
        }
        return type_labels.get(self.type, str(self.payload))


class TaskPlan(BaseModel):
    """Legacy low-level task model retained until orchestrators migrate fully."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    description: str
    skill: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    type: TaskType = TaskType.QUERY
    estimated_duration_seconds: int = 5

    def is_ready(self, completed: set[str]) -> bool:
        """Return True when every dependency has already completed."""
        return all(dep_id in completed for dep_id in self.depends_on)


class TaskResult(BaseModel):
    """Legacy low-level task result retained during migration."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    tokens_used: int = 0


class LLMUsage(BaseModel):
    """Token usage from a single LLM call."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AgentResponse(BaseModel):
    """Final response returned by the agent engine."""

    model_config = ConfigDict(extra="forbid")

    text: str
    subagent_results: list[SubagentResult] = Field(default_factory=list)
    planner_tokens: int = 0
    executor_tokens: int = 0
    critic_tokens: int = 0
    approved: bool = False
    final_decision: Any | None = None
    feedback: str | None = None


class SessionContext(BaseModel):
    """Runtime context passed through the agent engine."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    user_id: str
    session_id: str
    signal_id: str | None = None
    trace_id: str | None = None


__all__ = [
    "TaskType",
    "CustomerSignal",
    "TaskPlan",
    "TaskResult",
    "LLMUsage",
    "AgentResponse",
    "SessionContext",
]
