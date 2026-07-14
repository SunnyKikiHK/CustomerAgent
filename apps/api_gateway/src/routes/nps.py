"""NPS API: survey score submission and tenant aggregate.

The response endpoint is the authoritative path for a score entering the system
(never an LLM). It is intentionally lightweight: a customer submits a 0-10 score
for a survey id. The score is validated (0-10) and recorded, and a low score
creates a detractor signal for CSM analysis rather than triggering an automated
customer reply.

Score submission is scoped by the survey's tenant (resolved server-side from the
survey id); it does not require a CSM JWT because the responder is the surveyed
customer. Aggregate/read endpoints require tenant-authorized access.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from packages.auth.src.models import AuthContext
from packages.knowledge_service.src import nps

from apps.api_gateway.src.security import tenant_context

router = APIRouter(tags=["nps"])


class NpsResponseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(description="Tenant that owns the survey")
    score: int = Field(ge=0, le=10, description="Single NPS answer, 0-10")
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/nps/surveys/{survey_id}/response")
async def submit_nps_response(survey_id: str, body: NpsResponseInput) -> dict[str, Any]:
    """Record a customer's 0-10 survey answer and, if a detractor, raise a signal.

    Deterministic classification decides follow-up: a detractor (0-6) enqueues an
    ``nps_detractor`` signal for CSM analysis; it never auto-sends a customer
    reply.
    """
    try:
        result = await nps.record_response(
            tenant_id=body.tenant_id,
            survey_id=survey_id,
            score=body.score,
            comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="survey not found")

    detractor_signal = None
    if result["classification"] == "detractor":
        detractor_signal = await _raise_detractor_signal(body.tenant_id, result)
    return {**result, "detractor_signal": detractor_signal}


async def _raise_detractor_signal(tenant_id: str, response: dict[str, Any]) -> dict[str, Any] | None:
    """Enqueue an nps_detractor signal for analysis (best-effort)."""
    from apps.agent_service.src.signals.queue import enqueue_signal

    try:
        signal_id = await enqueue_signal(
            {
                "tenant_id": tenant_id,
                "customer_id": response["customer_id"],
                "type": "nps_detractor",
                "severity": "high",
                "source": "nps",
                "payload": {
                    "score": response["score"],
                    "comment": response.get("comment"),
                    "survey_id": response["survey_id"],
                },
            }
        )
        return {"enqueued": bool(signal_id), "signal_id": signal_id or None}
    except Exception:
        return None


@router.get("/nps/tenant")
async def get_tenant_nps(auth: AuthContext = Depends(tenant_context)) -> dict[str, Any]:
    """Return the deterministic aggregate NPS for the authorized tenant."""
    return await nps.tenant_nps(tenant_id=auth.tenant_id)


__all__ = ["router"]
