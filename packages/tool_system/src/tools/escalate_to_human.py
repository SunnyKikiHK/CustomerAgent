"""escalate_to_human tool schema: real (future) human-escalation MCP action.

This is the MCP_ACTION-boundary counterpart to the read-only
check_human_availability probe. It represents actually paging a human support
representative over the network, so it runs only through the tool gateway (the
side-effecting boundary), gated by approval + idempotency like send_email /
send_slack. This milestone ships only the schema, gateway tool, and a mock
provider; the conversation turn does not invoke it and conversation external
writes stay disabled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


class EscalateToHumanInput(BaseModel):
    """Input schema for paging a human support representative."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for rate limiting and audit")
    customer_id: str = Field(description="Customer UUID related to the escalation")
    reason: str = Field(min_length=1, max_length=500, description="Why a human is needed")
    summary: str = Field(min_length=1, max_length=2000, description="Issue summary for the human")
    urgency: Literal["low", "normal", "high", "critical"] = "high"


class EscalateToHumanOutput(BaseModel):
    """Result of an escalate_to_human request."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    provider_message_id: str | None = None
    error: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "escalate_to_human",
        "description": (
            "Page a human support representative for this customer. Side-effecting "
            "external action: runs only through the approved MCP gateway path, "
            "never inline in a chat turn. Requires reason and an accurate summary."
        ),
        "parameters": EscalateToHumanInput.model_json_schema(),
    },
}


async def execute_escalate_to_human(
    params: EscalateToHumanInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> EscalateToHumanOutput:
    """Validate an escalation request without contacting a provider (Phase 1)."""
    parsed = (
        params
        if isinstance(params, EscalateToHumanInput)
        else EscalateToHumanInput.model_validate(params)
    )
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return EscalateToHumanOutput(accepted=False, error="tenant_id does not match session context")
    return EscalateToHumanOutput(
        accepted=True,
        provider_message_id=f"phase1-escalation-{parsed.customer_id}",
    )


__all__ = [
    "EscalateToHumanInput",
    "EscalateToHumanOutput",
    "TOOL_DEFINITION",
    "execute_escalate_to_human",
]
