"""Fail-closed Streamable HTTP MCP client for approved external actions."""

from __future__ import annotations

import json
import os
from typing import Any


class MCPActionError(RuntimeError):
    """Raised when an MCP action is rejected, unavailable, or fails."""


class MCPActionClient:
    """Short-lived MCP client that invokes only approved action tools."""

    def __init__(self, url: str | None = None, auth_token: str | None = None) -> None:
        self.url = url or os.getenv("MCP_TOOL_GATEWAY_URL", "http://localhost:8002/mcp")
        self.auth_token = auth_token or os.getenv("MCP_TOOL_GATEWAY_TOKEN")

    async def call_action(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tenant_id: str,
        trace_id: str | None,
        approval_id: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, Any]:
        """Call one MCP action and reject protocol/tool-level failures."""
        if not approval_id or not idempotency_key:
            raise MCPActionError("approval_id and idempotency_key are required")

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise MCPActionError("MCP SDK is not installed") from exc

        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else None
        payload = {
            **arguments,
            "tenant_id": tenant_id,
            "trace_id": trace_id,
            "approval_id": approval_id,
            "idempotency_key": idempotency_key,
            "actor": actor,
        }
        try:
            async with streamablehttp_client(self.url, headers=headers) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, payload)
        except MCPActionError:
            raise
        except Exception as exc:
            raise MCPActionError(f"MCP action gateway unavailable: {exc}") from exc

        if getattr(result, "isError", False):
            raise MCPActionError(_content_text(result) or f"MCP action {tool_name} failed")

        data = getattr(result, "structuredContent", None)
        if not isinstance(data, dict):
            text = _content_text(result)
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError as exc:
                raise MCPActionError("MCP action returned an invalid response") from exc

        if not data.get("success", False):
            raise MCPActionError(str(data.get("error") or f"MCP action {tool_name} failed"))
        return data


def _content_text(result: Any) -> str:
    """Extract textual MCP content without depending on concrete SDK models."""
    return "".join(
        str(getattr(item, "text", ""))
        for item in (getattr(result, "content", None) or [])
        if getattr(item, "text", None)
    )


_CLIENT: MCPActionClient | None = None


def get_mcp_action_client() -> MCPActionClient:
    """Return the process-wide MCP action client."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = MCPActionClient()
    return _CLIENT


__all__ = ["MCPActionClient", "MCPActionError", "get_mcp_action_client"]
