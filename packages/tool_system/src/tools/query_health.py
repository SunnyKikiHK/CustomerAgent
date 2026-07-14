"""Customer health lookup tool schema and DB-backed executor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from packages.db.src import fetch_one

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
    # Chat-derived signals from customer_profiles (populated by the conversation
    # system); empty when the customer has no conversation-distilled profile yet.
    sentiment_signals: list[str] = Field(default_factory=list)
    risk_signals: list[str] = Field(default_factory=list)
    last_sentiment: str | None = None
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
    """Return a customer health profile from PostgreSQL."""
    parsed = params if isinstance(params, QueryHealthInput) else QueryHealthInput.model_validate(params)
    if ctx is not None and parsed.tenant_id != ctx.tenant_id:
        return QueryHealthOutput(
            found=False,
            customer_id=parsed.customer_id,
            error="tenant_id does not match session context",
        )

    row = await fetch_one(
        """
        select
            c.id::text as customer_id,
            c.health_score,
            c.usage_trend,
            c.nps,
            c.mrr,
            c.renewal_date,
            p.sentiment_signals,
            p.risk_signals,
            p.last_sentiment,
            (
                select count(*)::int
                from interactions i
                where i.customer_id = c.id
                  and i.type = 'support_ticket'
            ) as support_ticket_count
        from customers c
        left join customer_profiles p on p.customer_id = c.id
        where c.id::text = $1
        """,
        parsed.customer_id,
        tenant_id=parsed.tenant_id,
    )
    if row is None:
        return QueryHealthOutput(found=False, customer_id=parsed.customer_id)

    return QueryHealthOutput(
        found=True,
        customer_id=row["customer_id"],
        health_score=float(row["health_score"]) if row["health_score"] is not None else None,
        usage_trend=_json_dict(row["usage_trend"]),
        support_ticket_count=row["support_ticket_count"],
        nps=row["nps"],
        mrr=float(row["mrr"]) if row["mrr"] is not None else None,
        renewal_date=row["renewal_date"].isoformat() if row["renewal_date"] is not None else None,
        sentiment_signals=_json_list(row["sentiment_signals"]),
        risk_signals=_json_list(row["risk_signals"]),
        last_sentiment=row["last_sentiment"],
    )


def _json_list(value: Any) -> list[str]:
    """Coerce a JSONB column (list or json-encoded string) into a list[str]."""
    import json

    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _json_dict(value: Any) -> dict[str, Any]:
    """Coerce a JSONB column (dict or json-encoded string) into a dict."""
    import json

    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["QueryHealthInput", "QueryHealthOutput", "TOOL_DEFINITION", "execute_query_health"]
