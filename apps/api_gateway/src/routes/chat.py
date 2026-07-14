"""Authenticated tenant-scoped chat endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole, ChatRequest
from packages.agent.src.memory import get_conversation_memory
from packages.agent.src.types import SessionContext

from apps.agent_service.src.agent.conversation.chat_handler import handle_chat_turn

# NOTE: full paths are set on each route rather than via APIRouter(prefix=...):
# the installed Starlette drops prefixed sub-routes on include_router.
router = APIRouter(tags=["chat"])


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    session_id: str
    content: str
    stream: bool = True
    metadata: dict = Field(default_factory=dict)


@router.get("/chat/messages")
async def recent_chat_messages(
    tenant_id: str = Query(...),
    customer_id: str = Query(...),
    limit: int = Query(default=5, ge=1, le=5),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Return up to five recent messages for one tenant-scoped customer."""
    if x_tenant_id is not None and x_tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    messages = await get_conversation_memory().get_recent_messages(
        tenant_id=tenant_id,
        customer_id=customer_id,
        limit=limit,
    )
    return {"messages": [message.model_dump(mode="json") for message in messages]}


@router.post("/chat/turn")
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
