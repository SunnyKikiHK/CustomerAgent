"""Signal + customer endpoints for detectors, manual triggers, and the dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from packages.db.src import PostgresConfigError, fetch_all

from apps.agent_service.src.signals.detectors import run_all_detectors
from apps.agent_service.src.signals.queue import enqueue_signal
from apps.agent_service.src.signals.records import list_signals

router = APIRouter(tags=["signals"])


def _require_tenant(x_tenant_id: str | None, body_tenant_id: str | None) -> str:
    tenant_id = x_tenant_id or body_tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    if x_tenant_id and body_tenant_id and x_tenant_id != body_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")
    return tenant_id


class ManualSignalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    customer_id: str
    type: str
    severity: str = "normal"
    payload: dict[str, Any] = Field(default_factory=dict)


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str


@router.post("/signals/scan")
async def scan_signals(
    body: ScanRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Run all detectors for a tenant and enqueue the resulting signals."""
    tenant_id = _require_tenant(x_tenant_id, body.tenant_id)
    detected = await run_all_detectors(tenant_id=tenant_id)
    enqueued: list[dict[str, Any]] = []
    for payload in detected:
        signal_id = await enqueue_signal(payload)
        enqueued.append(
            {
                "type": payload["type"],
                "customer_id": payload["customer_id"],
                "signal_id": signal_id,
                "duplicate": signal_id == "",
            }
        )
    return {
        "detected": len(detected),
        "enqueued": sum(1 for item in enqueued if not item["duplicate"]),
        "signals": enqueued,
    }


@router.post("/signals")
async def create_signal(
    body: ManualSignalRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Manually enqueue a signal."""
    tenant_id = _require_tenant(x_tenant_id, body.tenant_id)
    payload = {
        "tenant_id": tenant_id,
        "customer_id": body.customer_id,
        "type": body.type,
        "severity": body.severity,
        "source": "manual",
        "payload": body.payload,
    }
    signal_id = await enqueue_signal(payload)
    return {"signal_id": signal_id, "duplicate": signal_id == ""}


@router.get("/signals")
async def get_signals(
    tenant_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """List recent signals for the dashboard."""
    resolved = _require_tenant(x_tenant_id, tenant_id)
    return {"signals": await list_signals(tenant_id=resolved, limit=limit)}


@router.get("/customers")
async def get_customers(
    tenant_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """List customers for the dashboard (health, renewal, MRR)."""
    resolved = _require_tenant(x_tenant_id, tenant_id)
    try:
        rows = await fetch_all(
            """
            select id::text, name, email, health_score, mrr, renewal_date, nps
            from customers
            order by health_score asc nulls last
            limit $1
            """,
            limit,
            tenant_id=resolved,
        )
    except PostgresConfigError:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {
        "customers": [
            {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
                "mrr": float(row["mrr"]) if row["mrr"] is not None else None,
                "renewal_date": row["renewal_date"].isoformat() if row["renewal_date"] else None,
                "nps": row["nps"],
            }
            for row in rows
        ]
    }


__all__ = ["router"]
