"""NPS internal tools: query history, create survey, record response, tenant NPS.

All are INTERNAL-boundary (read/light-write against the app DB, no external
network), callable in-process by the NpsOutreach subagent. Deterministic scoring
lives in ``packages.knowledge_service.src.nps``; these tools never ask an LLM to
compute a score. The actual survey score submission comes through the API
(``POST /nps/surveys/{id}/response``), not through a tool call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from packages.knowledge_service.src import nps

if TYPE_CHECKING:
    from packages.agent.src.types import SessionContext


# ── query_nps_history ─────────────────────────────────────────────────────────

class QueryNpsHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for data isolation")
    customer_id: str = Field(description="Customer UUID whose survey history to fetch")
    limit: int = Field(default=20, ge=1, le=100)


QUERY_NPS_HISTORY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_nps_history",
        "description": (
            "Return a customer's NPS survey and response history (status, scores, "
            "comments). Use before deciding whether to send another survey."
        ),
        "parameters": QueryNpsHistoryInput.model_json_schema(),
    },
}


async def execute_query_nps_history(
    params: QueryNpsHistoryInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = (
        params
        if isinstance(params, QueryNpsHistoryInput)
        else QueryNpsHistoryInput.model_validate(params)
    )
    history = await nps.survey_history(
        tenant_id=parsed.tenant_id, customer_id=parsed.customer_id, limit=parsed.limit
    )
    return {"customer_id": parsed.customer_id, "surveys": history, "count": len(history)}


# ── create_nps_survey ─────────────────────────────────────────────────────────

class CreateNpsSurveyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for data isolation")
    customer_id: str = Field(description="Customer UUID to survey")
    expires_in_days: int = Field(default=14, ge=1, le=90)


CREATE_NPS_SURVEY_DEFINITION = {
    "type": "function",
    "function": {
        "name": "create_nps_survey",
        "description": (
            "Create an NPS survey for a customer and return its id plus the "
            "frontend response URL. Does not send the email; the outreach agent "
            "proposes the send as a compliance-reviewed external write."
        ),
        "parameters": CreateNpsSurveyInput.model_json_schema(),
    },
}


async def execute_create_nps_survey(
    params: CreateNpsSurveyInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = (
        params
        if isinstance(params, CreateNpsSurveyInput)
        else CreateNpsSurveyInput.model_validate(params)
    )
    survey = await nps.create_survey(
        tenant_id=parsed.tenant_id,
        customer_id=parsed.customer_id,
        expires_in_days=parsed.expires_in_days,
    )
    if survey is None:
        return {"created": False, "error": "survey could not be created"}
    survey_url = _survey_url(survey["id"])
    return {"created": True, "survey_id": survey["id"], "survey_url": survey_url, **survey}


# ── record_nps_response ───────────────────────────────────────────────────────

class RecordNpsResponseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID for data isolation")
    survey_id: str = Field(description="Survey UUID being answered")
    score: int = Field(ge=0, le=10, description="Single NPS answer, 0-10")
    comment: str | None = Field(default=None, max_length=2000)


RECORD_NPS_RESPONSE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "record_nps_response",
        "description": (
            "Record a 0-10 NPS response for a survey and return its classification "
            "(promoter/passive/detractor). Scores are stored verbatim, never inferred."
        ),
        "parameters": RecordNpsResponseInput.model_json_schema(),
    },
}


async def execute_record_nps_response(
    params: RecordNpsResponseInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = (
        params
        if isinstance(params, RecordNpsResponseInput)
        else RecordNpsResponseInput.model_validate(params)
    )
    result = await nps.record_response(
        tenant_id=parsed.tenant_id,
        survey_id=parsed.survey_id,
        score=parsed.score,
        comment=parsed.comment,
    )
    if result is None:
        return {"recorded": False, "error": "survey not found or DB unavailable"}
    return {"recorded": True, **result}


# ── calculate_tenant_nps ──────────────────────────────────────────────────────

class CalculateTenantNpsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant UUID to aggregate NPS for")


CALCULATE_TENANT_NPS_DEFINITION = {
    "type": "function",
    "function": {
        "name": "calculate_tenant_nps",
        "description": (
            "Compute the deterministic aggregate NPS (%promoters - %detractors) "
            "across a tenant's recorded responses. Returns the score and counts."
        ),
        "parameters": CalculateTenantNpsInput.model_json_schema(),
    },
}


async def execute_calculate_tenant_nps(
    params: CalculateTenantNpsInput | dict[str, Any],
    ctx: "SessionContext" | None = None,
) -> dict[str, Any]:
    parsed = (
        params
        if isinstance(params, CalculateTenantNpsInput)
        else CalculateTenantNpsInput.model_validate(params)
    )
    return await nps.tenant_nps(tenant_id=parsed.tenant_id)


def _survey_url(survey_id: str) -> str:
    """Build the frontend URL where a customer submits their score."""
    import os

    base = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/nps/{survey_id}"


__all__ = [
    "QueryNpsHistoryInput",
    "CreateNpsSurveyInput",
    "RecordNpsResponseInput",
    "CalculateTenantNpsInput",
    "QUERY_NPS_HISTORY_DEFINITION",
    "CREATE_NPS_SURVEY_DEFINITION",
    "RECORD_NPS_RESPONSE_DEFINITION",
    "CALCULATE_TENANT_NPS_DEFINITION",
    "execute_query_nps_history",
    "execute_create_nps_survey",
    "execute_record_nps_response",
    "execute_calculate_tenant_nps",
]
