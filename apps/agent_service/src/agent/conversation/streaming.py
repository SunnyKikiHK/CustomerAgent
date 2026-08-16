"""Streaming-safe emission for the GeneralAgent conversation loop."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from packages.agent.src.orchestration_types import ConversationAgentInput
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.conversation_orchestrator import (
    stream_conversation_loop,
)

logger = logging.getLogger(__name__)


async def stream_approved_response(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AsyncIterator[str]:
    """Stream the GeneralAgent loop's final answer as SSE events.

    Yields a single ``status`` event, then forwards each real text delta from
    ``stream_conversation_loop`` as a ``token`` event, and closes with a ``done``
    event carrying the full accumulated text. Any unexpected failure (LLM outage,
    DNS, timeout) is converted into an SSE ``error`` event so the UI can leave the
    "Planning response" spinner instead of hanging forever when the ASGI stream
    aborts.
    """
    yield _sse("status", {"phase": "orchestrator", "message": "Planning response"})
    chunks: list[str] = []
    try:
        async for delta in stream_conversation_loop(agent_input, ctx):
            chunks.append(delta)
            yield _sse("token", {"text": delta})
    except Exception as exc:  # pragma: no cover - live network failures
        logger.exception("chat stream failed for session %s", ctx.session_id)
        yield _sse(
            "error",
            {
                "approved": False,
                "message": _user_facing_error(exc),
                "action": "stream_failed",
                "error_type": type(exc).__name__,
            },
        )
        return

    yield _sse("done", {"approved": True, "text": "".join(chunks)})


def _user_facing_error(exc: Exception) -> str:
    """Map provider/network exceptions to a short, non-sensitive UI message."""
    name = type(exc).__name__
    text = str(exc).lower()
    if "name resolution" in text or "connect" in name.lower() or "connection" in text:
        return (
            "The assistant could not reach the LLM provider "
            "(network/DNS). Check OpenRouter connectivity from the API container."
        )
    if "timeout" in text or "timed out" in text:
        return "The assistant timed out waiting for the LLM provider. Please try again."
    if "auth" in text or "401" in text or "api key" in text:
        return "The LLM provider rejected the API key. Check OPENROUTER_API_KEY."
    return f"The assistant failed while generating a reply ({name}). Please try again."


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


__all__ = ["stream_approved_response"]
