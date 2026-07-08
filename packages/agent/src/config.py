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

    tenant_id: str                              # unique identifier for the tenant
    name: str                                   # human-readable tenant/agent name
    instructions: str                           # system prompt injected into every agent turn
    model: str                                  # LLM used by worker subagents
    planner_model: str                          # LLM used by the planner (may differ from worker model)
    tools: list[str] = Field(default_factory=list)  # tool names this tenant is allowed to invoke
    skip_critic_for_simple: bool = True         # bypass compliance critic on low-risk, single-step tasks
    max_replan_attempts: int = 2                # how many times the orchestrator may replan before failing
    memory_enabled: bool = True                 # whether long-term memory retrieval is active for this tenant
    pii_masking_enabled: bool = True            # whether PII is masked before sending data to the LLM


__all__ = ["AgentConfig"]
