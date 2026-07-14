"""process_refund tool: prototype refund initiation for the billing agent.

Prototype scope: this always reports the refund as initiated. It performs no
network call, no payment-provider integration, and moves no real money. The
conversation billing subagent calls it in-process (INTERNAL boundary, like
query_health) when the customer explicitly requests a refund, and relays the
returned message so the customer sees the success in the same reply. Real refund
execution (payment provider, human/finance review) is deferred.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext

#: Deterministic prototype message surfaced to the customer this milestone.
_REFUND_MESSAGE = "Your refund has been initiated and will be processed shortly."


class ProcessRefundInput(BaseModel):
    """Input schema for a refund request."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for data isolation and audit")
    customer_id: str = Field(description="Customer UUID for tracking and audit")
    order_id: str = Field(min_length=1, description="Order or transaction reference to refund")
    amount: float | None = Field(default=None, description="Refund amount, if specified")
    reason: str | None = Field(default=None, description="Short reason for the refund")


class ProcessRefundOutput(BaseModel):
    """Result of a process_refund request."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    status: str
    refund_id: str
    message: str


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "process_refund",
        "description": (
            "Initiate a refund for a customer's order. Call this when the customer "
            "explicitly requests a refund and an order reference is available. "
            "Prototype: this currently always succeeds and returns a refund id; it "
            "does not contact a payment provider. Relay the returned message to the "
            "customer."
        ),
        "parameters": ProcessRefundInput.model_json_schema(),
    },
}


async def execute_process_refund(
    params: ProcessRefundInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> ProcessRefundOutput:
    """Return the deterministic prototype refund result.

    Always reports success. Never contacts an external system. Uses the
    session-authoritative tenant id for the stable, non-sensitive refund id.
    """
    parsed = (
        params
        if isinstance(params, ProcessRefundInput)
        else ProcessRefundInput.model_validate(params)
    )
    tenant_id = ctx.tenant_id if ctx is not None else parsed.tenant_id
    material = f"{tenant_id}:{parsed.customer_id}:{parsed.order_id}"
    refund_id = f"refund-{hashlib.sha256(material.encode()).hexdigest()[:16]}"
    return ProcessRefundOutput(
        success=True,
        status="refund_initiated",
        refund_id=refund_id,
        message=_REFUND_MESSAGE,
    )


__all__ = [
    "ProcessRefundInput",
    "ProcessRefundOutput",
    "TOOL_DEFINITION",
    "execute_process_refund",
]
