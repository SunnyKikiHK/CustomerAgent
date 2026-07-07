"""Slack sending tool schema and Phase 1 in-process executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


class SendSlackInput(BaseModel):
    """Input schema for Slack notifications."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for rate limiting and audit")
    customer_id: str = Field(description="Customer UUID related to the notification")
    channel_id: str = Field(description="Slack channel/user ID approved for this tenant")
    message: str = Field(min_length=1, max_length=4000, description="Slack message text")
    urgency: Literal["low", "normal", "high", "critical"] = "normal"


class SendSlackOutput(BaseModel):
    """Result of a send_slack request."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    provider_message_id: str | None = None
    error: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_slack",
        "description": (
            "Send a Slack notification to an approved tenant channel or CSM user. "
            "Use for escalation or internal follow-up. Do not send customer-visible "
            "content unless the ComplianceCriticAgent has approved it."
        ),
        "parameters": SendSlackInput.model_json_schema(),
    },
}


async def execute_send_slack(
    params: SendSlackInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> SendSlackOutput:
    """Validate and accept a Slack request without contacting Slack."""
    parsed = params if isinstance(params, SendSlackInput) else SendSlackInput.model_validate(params)
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return SendSlackOutput(accepted=False, error="tenant_id does not match session context")
    if not parsed.channel_id.startswith(("C", "G", "D", "U")):
        return SendSlackOutput(accepted=False, error="channel_id is not a recognized Slack ID")

    return SendSlackOutput(
        accepted=True,
        provider_message_id=f"phase1-slack-{parsed.customer_id}",
    )


__all__ = ["SendSlackInput", "SendSlackOutput", "TOOL_DEFINITION", "execute_send_slack"]
