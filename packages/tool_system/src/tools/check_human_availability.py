"""check_human_availability tool: read-only human-agent availability probe.

Prototype scope: this always reports that no human is available. It performs no
network call and no real availability lookup yet. The conversation escalation
subagent calls it in-process (INTERNAL boundary, like query_health) and uses the
result as evidence to produce the customer-facing fallback, so "no human
available" can appear in the same chat reply without crossing the external-write
boundary. Real, networked human paging is a separate future MCP action
(escalate_to_human, MCP_ACTION) and is not invoked from a conversation turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext

#: Deterministic prototype response surfaced to the customer this milestone.
_UNAVAILABLE_MESSAGE = "No human support representative is currently available."


class CheckHumanAvailabilityInput(BaseModel):
    """Input schema for a human-availability check."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for data isolation and audit")
    customer_id: str = Field(description="Customer UUID for tracking and audit")
    reason: str | None = Field(
        default=None,
        description="Short reason a human escalation is being considered",
    )


class CheckHumanAvailabilityOutput(BaseModel):
    """Result of a human-availability check."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    message: str
    checked_at: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "check_human_availability",
        "description": (
            "Read-only check for whether a human support representative is "
            "currently available to take over. Call this before telling a customer "
            "a human will assist, so the reply reflects real availability. "
            "Prototype: this currently always reports that no human is available; "
            "it does not page anyone. If unavailable, tell the customer plainly and "
            "summarize the issue for later human pickup."
        ),
        "parameters": CheckHumanAvailabilityInput.model_json_schema(),
    },
}


async def execute_check_human_availability(
    params: CheckHumanAvailabilityInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> CheckHumanAvailabilityOutput:
    """Return the deterministic prototype availability result.

    Always reports no human available. Never contacts an external system. A
    tenant mismatch returns the same unavailable result rather than leaking any
    other tenant's state.
    """
    parsed = (
        params
        if isinstance(params, CheckHumanAvailabilityInput)
        else CheckHumanAvailabilityInput.model_validate(params)
    )
    # Session identity is authoritative; a mismatch still yields the safe,
    # information-free "unavailable" answer.
    _ = ctx.tenant_id if ctx is not None else parsed.tenant_id
    return CheckHumanAvailabilityOutput(
        available=False,
        message=_UNAVAILABLE_MESSAGE,
    )


__all__ = [
    "CheckHumanAvailabilityInput",
    "CheckHumanAvailabilityOutput",
    "TOOL_DEFINITION",
    "execute_check_human_availability",
]
