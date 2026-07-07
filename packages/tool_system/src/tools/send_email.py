"""Email sending tool schema and Phase 1 in-process executor."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SendEmailInput(BaseModel):
    """Input schema for a customer outreach email."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for rate limiting and audit")
    customer_id: str = Field(description="Customer UUID for tracking and audit")
    recipient_email: str = Field(description="Validated recipient email address")
    subject: str = Field(min_length=1, max_length=200, description="Email subject line")
    body: str = Field(min_length=1, description="Email body in markdown format")
    sender_name: str = Field(min_length=1, description="Display name of the sender")


class SendEmailOutput(BaseModel):
    """Result of a send_email request."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    provider_message_id: str | None = None
    error: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "Send a personalized email to a customer. Requires recipient_email, "
            "subject, body, sender_name, tenant_id, and customer_id. Do not call "
            "with raw sensitive PII beyond the approved recipient address."
        ),
        "parameters": SendEmailInput.model_json_schema(),
    },
}


async def execute_send_email(
    params: SendEmailInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> SendEmailOutput:
    """Validate and accept an email request without contacting a provider."""
    parsed = params if isinstance(params, SendEmailInput) else SendEmailInput.model_validate(params)
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return SendEmailOutput(accepted=False, error="tenant_id does not match session context")
    if not _EMAIL_RE.match(parsed.recipient_email):
        return SendEmailOutput(accepted=False, error="recipient_email is invalid")

    return SendEmailOutput(
        accepted=True,
        provider_message_id=f"phase1-email-{parsed.customer_id}",
    )


__all__ = ["SendEmailInput", "SendEmailOutput", "TOOL_DEFINITION", "execute_send_email"]
