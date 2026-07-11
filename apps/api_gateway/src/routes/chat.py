"""Authenticated tenant-scoped chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole, ChatRequest
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.chat_handler import handle_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    session_id: str
    content: str
    stream: bool = True
    metadata: dict = Field(default_factory=dict)


@router.post("/turn")
async def chat_turn(
    body: ChatTurnRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Handle one customer chat turn."""
    tenant_id = x_tenant_id or body.tenant_id
    if tenant_id != body.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    request = ChatRequest(
        tenant_id=tenant_id,
        customer_id=body.customer_id,
        session_id=body.session_id,
        message=ChatMessage(
            tenant_id=tenant_id,
            customer_id=body.customer_id,
            session_id=body.session_id,
            role=ChatMessageRole.USER,
            content=body.content,
            metadata=body.metadata,
        ),
        stream=body.stream,
    )
    ctx = SessionContext(
        tenant_id=tenant_id,
        user_id=body.customer_id,
        session_id=body.session_id,
        trace_id=body.session_id,
    )
    result = await handle_chat_turn(request, ctx)
    if body.stream and hasattr(result, "__aiter__"):
        return StreamingResponse(result, media_type="text/event-stream")
    return result


__all__ = ["router"]
