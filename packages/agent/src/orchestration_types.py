"""Top-level orchestration Pydantic models.

These models form the shared contract between signal/conversation
orchestrators, delegation, compliance review, and API/workflow callers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.agent.src.chat_types import ChatMessage
from packages.agent.src.subagent_types import SubagentResult, SubagentTask
from packages.agent.src.types import CustomerSignal


class AgentInputType(str, Enum):
    """Inbound domain selected by the dispatch layer."""

    SIGNAL = "signal"
    CONVERSATION = "conversation"


class SignalAgentInput(BaseModel):
    """Input for proactive signal automation."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    signal: CustomerSignal
    requested_by_user_id: str | None = None


class ConversationAgentInput(BaseModel):
    """Input for a customer conversation turn."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    session_id: str
    message: ChatMessage
    stream: bool = True


class AgentDispatchInput(BaseModel):
    """Routing envelope used only before selecting the top-level orchestrator."""

    model_config = ConfigDict(extra="forbid")

    type: AgentInputType
    signal_input: SignalAgentInput | None = None
    conversation_input: ConversationAgentInput | None = None

    @model_validator(mode="after")
    def exactly_one_routed_input(self) -> "AgentDispatchInput":
        """Validate that the envelope contains exactly one matching input."""
        if self.type == AgentInputType.SIGNAL and self.signal_input is None:
            raise ValueError("signal dispatch requires SignalAgentInput")
        if self.type == AgentInputType.CONVERSATION and self.conversation_input is None:
            raise ValueError("conversation dispatch requires ConversationAgentInput")
        if self.signal_input is not None and self.conversation_input is not None:
            raise ValueError("dispatch input cannot include both signal and conversation inputs")
        return self


class OrchestratorPhase(str, Enum):
    """Planner -> Executor -> Reflector lifecycle phase."""

    PLANNER = "planner"
    EXECUTOR = "executor"
    REFLECTOR = "reflector"


class OrchestratorPlan(BaseModel):
    """Planner output: ordered role-based subagent tasks."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    tasks: list[SubagentTask] = Field(min_length=1, max_length=8)
    requires_critic: bool = True
    global_constraints: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(description="Brief non-sensitive planning rationale")


class ComplianceFinding(BaseModel):
    """Single policy, safety, grounding, or tenant-isolation finding."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["low", "medium", "high", "blocker"]
    message: str
    affected_task_ids: list[str] = Field(default_factory=list)


class ComplianceReview(BaseModel):
    """Reflector-phase review before output, writes, or streaming completion."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    findings: list[ComplianceFinding] = Field(default_factory=list)
    pii_detected: bool = False
    redactions: dict[str, str] = Field(default_factory=dict)
    blocked_external_writes: list[dict[str, Any]] = Field(default_factory=list)
    feedback: str


class FinalDecision(BaseModel):
    """Approved final response and external write payloads."""

    model_config = ConfigDict(extra="forbid")

    action: str
    response_text: str
    approved_external_writes: list[dict[str, Any]] = Field(default_factory=list)
    subagent_results: list[SubagentResult] = Field(default_factory=list)
    compliance_review: ComplianceReview
    reasoning_summary: str


__all__ = [
    "AgentInputType",
    "SignalAgentInput",
    "ConversationAgentInput",
    "AgentDispatchInput",
    "OrchestratorPhase",
    "OrchestratorPlan",
    "ComplianceFinding",
    "ComplianceReview",
    "FinalDecision",
]
