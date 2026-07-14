"""Streamable HTTP MCP server for sandboxed external action tools."""

from __future__ import annotations

import hmac
import os
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp import FastMCP

from apps.tool_gateway.src.contracts import ActionContext
from apps.tool_gateway.src.service import get_action_service


class SharedTokenVerifier:
    """Validate the internal service bearer token for local deployments."""

    def __init__(self, expected_token: str) -> None:
        self.expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="customer-agent-service",
            scopes=["actions:execute"],
            subject="agent-service",
        )


def _build_server() -> FastMCP:
    token = os.getenv("MCP_TOOL_GATEWAY_TOKEN")
    return FastMCP(
        "CustomerAgent Action Gateway",
        host=os.getenv("MCP_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_GATEWAY_PORT", "8002")),
        stateless_http=True,
        token_verifier=SharedTokenVerifier(token) if token else None,
    )


mcp = _build_server()


def _context(
    tenant_id: str,
    trace_id: str | None,
    approval_id: str,
    idempotency_key: str,
    actor: str,
) -> ActionContext:
    """Build validated trusted metadata shared by all action tools."""
    return ActionContext(
        tenant_id=tenant_id,
        trace_id=trace_id,
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        actor=actor,
    )


@mcp.tool()
async def send_email(
    tenant_id: str,
    customer_id: str,
    recipient_email: str,
    subject: str,
    body: str,
    sender_name: str,
    approval_id: str,
    idempotency_key: str,
    actor: str = "agent",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Send an approved customer email through the configured provider adapter."""
    result = await get_action_service().execute(
        "send_email",
        {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "recipient_email": recipient_email,
            "subject": subject,
            "body": body,
            "sender_name": sender_name,
        },
        _context(tenant_id, trace_id, approval_id, idempotency_key, actor),
    )
    return result.model_dump(mode="json")


@mcp.tool()
async def send_slack(
    tenant_id: str,
    customer_id: str,
    channel_id: str,
    message: str,
    approval_id: str,
    idempotency_key: str,
    urgency: str = "normal",
    actor: str = "agent",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Send an approved Slack notification through the configured provider adapter."""
    result = await get_action_service().execute(
        "send_slack",
        {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "channel_id": channel_id,
            "message": message,
            "urgency": urgency,
        },
        _context(tenant_id, trace_id, approval_id, idempotency_key, actor),
    )
    return result.model_dump(mode="json")


@mcp.tool()
async def escalate_to_human(
    tenant_id: str,
    customer_id: str,
    reason: str,
    summary: str,
    approval_id: str,
    idempotency_key: str,
    urgency: str = "high",
    actor: str = "agent",
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Page a human support representative through the configured provider adapter."""
    result = await get_action_service().execute(
        "escalate_to_human",
        {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "reason": reason,
            "summary": summary,
            "urgency": urgency,
        },
        _context(tenant_id, trace_id, approval_id, idempotency_key, actor),
    )
    return result.model_dump(mode="json")


def main() -> None:
    """Run the gateway using MCP Streamable HTTP transport."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()


__all__ = [
    "SharedTokenVerifier",
    "mcp",
    "main",
    "send_email",
    "send_slack",
    "escalate_to_human",
]
