"""Playbook retrieval tool schema and Phase 1 in-process executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


class QueryPlaybooksInput(BaseModel):
    """Input schema for retrieving tenant playbooks."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for knowledge isolation")
    customer_id: str | None = Field(default=None, description="Customer UUID for contextual filtering")
    signal_type: str | None = Field(default=None, description="Signal type such as usage_drop or renewal_due")
    query: str = Field(description="Natural-language retrieval query")
    limit: int = Field(default=3, ge=1, le=10, description="Maximum number of playbooks to return")


class PlaybookMatch(BaseModel):
    """Single retrieved playbook candidate."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryPlaybooksOutput(BaseModel):
    """Retrieved tenant playbooks."""

    model_config = ConfigDict(extra="forbid")

    matches: list[PlaybookMatch] = Field(default_factory=list)
    error: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_playbooks",
        "description": (
            "Retrieve tenant-scoped customer-success playbooks or knowledge snippets. "
            "Use after understanding the customer situation. Do not retrieve or expose "
            "playbooks from another tenant."
        ),
        "parameters": QueryPlaybooksInput.model_json_schema(),
    },
}


async def execute_query_playbooks(
    params: QueryPlaybooksInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> QueryPlaybooksOutput:
    """Return no matches until vector/DB-backed retrieval is wired."""
    parsed = params if isinstance(params, QueryPlaybooksInput) else QueryPlaybooksInput.model_validate(params)
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return QueryPlaybooksOutput(error="tenant_id does not match session context")

    return QueryPlaybooksOutput(
        matches=[],
        error="query_playbooks Phase 1 executor is not connected to knowledge retrieval yet",
    )


__all__ = [
    "PlaybookMatch",
    "QueryPlaybooksInput",
    "QueryPlaybooksOutput",
    "TOOL_DEFINITION",
    "execute_query_playbooks",
]
