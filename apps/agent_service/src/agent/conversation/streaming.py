"""Streaming-safe emission with critic approval gate."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from packages.agent.src.orchestration_types import ConversationAgentInput
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.conversation_orchestrator import run_conversation_agent


async def stream_approved_response(
    agent_input: ConversationAgentInput,
    ctx: SessionContext,
) -> AsyncIterator[str]:
    """Stream progress events and commit only the critic-approved final answer."""
    yield _sse("status", {"phase": "planner", "message": "Planning response"})
    yield _sse("status", {"phase": "executor", "message": "Running specialists"})

    response = await run_conversation_agent(agent_input, ctx)
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


def _chunk_text(text: str, size: int = 40) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)] or [""]


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


__all__ = ["stream_approved_response"]
