"""Validated contracts shared by MCP action tools and provider adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionContext(BaseModel):
    """Server-validated authorization and tracing context for one action."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    trace_id: str | None = None
    approval_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    actor: str = Field(min_length=1)


class ActionResult(BaseModel):
    """Audit-safe result returned across the MCP boundary."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    status: Literal["executed", "duplicate", "rejected", "failed"]
    idempotency_key: str
    provider_message_id: str | None = None
    error: str | None = None
    retryable: bool = False


__all__ = ["ActionContext", "ActionResult"]
