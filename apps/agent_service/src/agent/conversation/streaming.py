"""Streaming-safe emission with critic approval gate."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from packages.agent.src.orchestration_types import ConversationAgentInput
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.conversation_orchestrator import run_conversation_agent

logger = logging.getLogger(__name__)


async def stream_approved_response(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AsyncIterator[str]:
    """Stream progress events and commit only the critic-approved final answer.

    Any unexpected failure (LLM outage, DNS, timeout) is converted into an SSE
    ``error`` event so the UI can leave the "Planning response" spinner instead
    of hanging forever when the ASGI stream aborts.
    """
    yield _sse("status", {"phase": "planner", "message": "Planning response"})
    try:
        yield _sse("status", {"phase": "executor", "message": "Running specialists"})
        response = await run_conversation_agent(agent_input, ctx)
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

    if not response.approved:
        yield _sse(
            "error",
            {
                "approved": False,
                "message": response.text,
                "feedback": response.feedback,
                "action": (
                    response.final_decision.action
                    if response.final_decision is not None
                    else "blocked"
                ),
            },
        )
        return

    yield _sse("status", {"phase": "reflector", "message": "Critic approved response"})
    for chunk in _chunk_text(response.text):
        yield _sse("token", {"text": chunk})
    yield _sse("done", {"approved": True, "text": response.text})


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


def _chunk_text(text: str, size: int = 40) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


__all__ = ["stream_approved_response"]
