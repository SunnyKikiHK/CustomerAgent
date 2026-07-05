"""Conversation-domain Pydantic models for customer chat turns."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatMessageRole(str, Enum):
    """Allowed speaker roles for conversation memory and inbound turns."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ChatMessage(BaseModel):
    """A single message in a customer conversation thread."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    customer_id: str
    session_id: str
    role: ChatMessageRole = ChatMessageRole.USER
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class ChatRequest(BaseModel):
    """API/workflow request for a conversation orchestrator turn."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    session_id: str
    message: ChatMessage
    stream: bool = True


class ChatResponse(BaseModel):
    """Response returned for a completed non-streaming chat turn."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    session_id: str
    message: ChatMessage
    approved: bool = False
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ChatMessageRole",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
]
