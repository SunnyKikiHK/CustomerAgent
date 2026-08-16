"""Approved email delivery through the gated ``send_email`` action path.

Shared by the QBR and NPS campaign workflows so their customer-facing emails go
through the same approval -> MCP gateway -> provider path as the signal system,
instead of being silently marked delivered/sent without a real send.

Provider selection is owned by the tool gateway (``EMAIL_PROVIDER`` env): with
``console`` the message is logged locally, with ``mock`` it is a no-op, and with
``google`` it fails closed until a CSM Gmail integration is configured.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Default display name used when a caller does not supply ``sender_name``.
DEFAULT_SENDER_NAME = "Customer Success Agent"


def _digest(value: dict[str, Any]) -> str:
    """Build a deterministic opaque id without exposing payload details."""
    canonical = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _arguments(payload: dict[str, Any], *, sender_name: str) -> dict[str, Any]:
    """Normalize a raw payload into the ``send_email`` tool arguments."""
    return {
        "tenant_id": str(payload["tenant_id"]),
        "customer_id": str(payload["customer_id"]),
        "recipient_email": str(payload["recipient_email"]),
        "subject": str(payload["subject"])[:200],
        "body": str(payload["body"]),
        "sender_name": str(payload.get("sender_name") or sender_name),
    }


async def send_approved_email(
    payload: dict[str, Any],
    *,
    trace_id: str,
    sender_name: str = DEFAULT_SENDER_NAME,
) -> dict[str, Any]:
    """Persist an approval and send one email through the gated MCP action path.

    Mirrors ``SignalOrchestrator.on_approved``: the write is persisted as an
    approval, then executed through the MCP gateway which verifies the approval,
    dedupes by idempotency key, and dispatches to the configured email provider.
    """
    from apps.agent_service.src.agent.runtime.tool_dispatch import execute_mcp_action
    from apps.tool_gateway.src.approval import persist_action_approval
    from packages.agent.src.types import SessionContext

    arguments = _arguments(payload, sender_name=sender_name)
    tenant_id = arguments["tenant_id"]
    approval_id = _digest(
        {
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "action": "send_email",
            "recipient_email": arguments["recipient_email"],
            "subject": arguments["subject"],
        }
    )
    idempotency_key = _digest(
        {"tenant_id": tenant_id, "trace_id": trace_id, "action": "send_email", "arguments": arguments}
    )
    ctx = SessionContext(
        tenant_id=tenant_id,
        user_id=arguments["customer_id"],
        session_id=f"email:{trace_id}",
        trace_id=trace_id,
    )
    await persist_action_approval(
        approval_id=approval_id,
        tenant_id=tenant_id,
        action_name="send_email",
        trace_id=trace_id,
        payload=arguments,
    )
    return await execute_mcp_action(
        "send_email",
        arguments,
        ctx,
        approval_id=approval_id,
        idempotency_key=idempotency_key,
    )


__all__ = ["send_approved_email", "DEFAULT_SENDER_NAME"]
