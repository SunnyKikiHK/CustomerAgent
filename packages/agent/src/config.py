"""Per-tenant agent configuration.

Loaded from the tenants DB table at startup and cached in Redis with a
5-minute TTL. This module only defines the schema; loading/caching lives in the
data layer so this stays dependency-free.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentConfig(BaseModel):
    """Per-tenant agent configuration."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    name: str
    instructions: str
    model: str
    planner_model: str
    tools: list[str] = Field(default_factory=list)
    skip_critic_for_simple: bool = True
    max_replan_attempts: int = 2
    memory_enabled: bool = True
    pii_masking_enabled: bool = True


__all__ = ["AgentConfig"]
