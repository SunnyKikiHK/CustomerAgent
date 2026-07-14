"""High-level chat turn handler."""

from __future__ import annotations

from packages.agent.src.chat_types import ChatRequest, ChatResponse, ChatMessage, ChatMessageRole
from packages.agent.src.orchestration_types import ConversationAgentInput
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.conversation_orchestrator import run_conversation_agent
from apps.agent_service.src.agent.conversation.streaming import stream_approved_response


async def handle_chat_turn(request: ChatRequest, ctx: SessionContext):
    """Handle one customer chat turn, optionally as an SSE stream."""
    agent_input = ConversationAgentInput(
        tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        session_id=request.session_id,
        message=request.message,
        stream=request.stream,
    )
    if request.stream:
        return stream_approved_response(agent_input, ctx)
    response = await run_conversation_agent(agent_input, ctx)
    return ChatResponse(
        tenant_id=request.tenant_id,
        customer_id=request.customer_id,
        session_id=request.session_id,
        message=ChatMessage(
            tenant_id=request.tenant_id,
            customer_id=request.customer_id,
            session_id=request.session_id,
            role=ChatMessageRole.ASSISTANT,
            content=response.text,
        ),
        approved=response.approved,
        trace_id=ctx.trace_id,
        metadata={
            "feedback": response.feedback,
            "action": (
                response.final_decision.action
                if response.final_decision is not None
                else None
            ),
        },
    )


__all__ = ["handle_chat_turn"]
