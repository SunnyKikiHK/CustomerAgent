"""Customer health lookup tool schema and Phase 1 in-process executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


class QueryHealthInput(BaseModel):
    """Input schema for querying customer health data."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(description="Customer UUID from the platform")
    tenant_id: str = Field(description="Tenant UUID for data isolation")


class QueryHealthOutput(BaseModel):
    """Customer health profile returned by the query_health tool."""

    model_config = ConfigDict(extra="forbid")

    found: bool
    customer_id: str | None = None
    health_score: float | None = None
    usage_trend: dict[str, Any] | None = None
    support_ticket_count: int | None = None
    nps: int | None = None
    mrr: float | None = None
    renewal_date: str | None = None
    error: str | None = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_health",
        "description": (
            "Query a customer's health profile. Returns health score, usage trend, "
            "support ticket count, NPS, MRR, and renewal date. Call this first "
            "to understand a customer's situation before taking action."
        ),
        "parameters": QueryHealthInput.model_json_schema(),
    },
}


async def execute_query_health(
    params: QueryHealthInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> QueryHealthOutput:
    """Return a placeholder health profile until DB-backed execution is wired."""
    parsed = params if isinstance(params, QueryHealthInput) else QueryHealthInput.model_validate(params)
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return QueryHealthOutput(
            found=False,
            customer_id=parsed.customer_id,
            error="tenant_id does not match session context",
        )

    return QueryHealthOutput(
        found=False,
        customer_id=parsed.customer_id,
        error="query_health Phase 1 executor is not connected to the database yet",
    )


__all__ = ["QueryHealthInput", "QueryHealthOutput", "TOOL_DEFINITION", "execute_query_health"]
