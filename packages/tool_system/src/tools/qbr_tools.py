"""QBR internal tools: tenant-level portfolio facts for the QBR agent.

All INTERNAL-boundary, tenant-scoped reads over the app DB (plus one light-write
persist). Deterministic aggregation lives in ``packages.knowledge_service.src.qbr``;
these tools never ask an LLM to compute a metric. The QBR subagent calls them to
gather facts, then writes only the narrative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from packages.knowledge_service.src import qbr

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


class _TenantOnlyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID to aggregate")


QUERY_TENANT_PORTFOLIO_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_tenant_portfolio",
        "description": (
            "Return the deterministic QBR metrics snapshot for a tenant: customer "
            "count, MRR/ARR, health distribution, renewal pipeline (30/60/90), NPS, "
            "and open-signal summary. Use this as the factual basis for the QBR."
        ),
        "parameters": _TenantOnlyInput.model_json_schema(),
    },
}


async def execute_query_tenant_portfolio(
    params: _TenantOnlyInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = params if isinstance(params, _TenantOnlyInput) else _TenantOnlyInput.model_validate(params)
    return await qbr.aggregate_portfolio(tenant_id=parsed.tenant_id)


QUERY_TENANT_NPS_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_tenant_nps",
        "description": "Return the deterministic aggregate NPS and counts for a tenant.",
        "parameters": _TenantOnlyInput.model_json_schema(),
    },
}


async def execute_query_tenant_nps(
    params: _TenantOnlyInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = params if isinstance(params, _TenantOnlyInput) else _TenantOnlyInput.model_validate(params)
    snapshot = await qbr.aggregate_portfolio(tenant_id=parsed.tenant_id)
    return snapshot["nps"]


QUERY_RENEWAL_PIPELINE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_renewal_pipeline",
        "description": "Return the count of renewals in the next 30/60/90 day windows.",
        "parameters": _TenantOnlyInput.model_json_schema(),
    },
}


async def execute_query_renewal_pipeline(
    params: _TenantOnlyInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = params if isinstance(params, _TenantOnlyInput) else _TenantOnlyInput.model_validate(params)
    snapshot = await qbr.aggregate_portfolio(tenant_id=parsed.tenant_id)
    return snapshot["renewal_pipeline"]


QUERY_SIGNAL_SUMMARY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_signal_summary",
        "description": "Return the count of open (non-done) signals grouped by type for a tenant.",
        "parameters": _TenantOnlyInput.model_json_schema(),
    },
}


async def execute_query_signal_summary(
    params: _TenantOnlyInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = params if isinstance(params, _TenantOnlyInput) else _TenantOnlyInput.model_validate(params)
    snapshot = await qbr.aggregate_portfolio(tenant_id=parsed.tenant_id)
    return snapshot["open_signals"]


__all__ = [
    "QUERY_TENANT_PORTFOLIO_DEFINITION",
    "QUERY_TENANT_NPS_DEFINITION",
    "QUERY_RENEWAL_PIPELINE_DEFINITION",
    "QUERY_SIGNAL_SUMMARY_DEFINITION",
    "execute_query_tenant_portfolio",
    "execute_query_tenant_nps",
    "execute_query_renewal_pipeline",
    "execute_query_signal_summary",
]
