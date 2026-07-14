"""Signal + customer endpoints for detectors, manual triggers, and the dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from apps.agent_service.src.signals.records import list_signals
from apps.temporal_worker.src.client import start_signal_workflow, start_tenant_scan

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
    """Start a tenant scan workflow (Temporal), falling back to inline scan.

    Detection + per-signal processing now runs as durable Temporal workflows.
    When Temporal is unreachable the client degrades to running the scan and
    each signal inline so the endpoint still works in local/offline dev.
    """
    tenant_id = _require_tenant(x_tenant_id, body.tenant_id)
    return await start_tenant_scan(tenant_id)


@router.post("/signals")
async def create_signal(
    body: ManualSignalRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """Start a ProcessSignalWorkflow for a manually-created signal."""
    tenant_id = _require_tenant(x_tenant_id, body.tenant_id)
    payload = {
        "tenant_id": tenant_id,
        "customer_id": body.customer_id,
        "type": body.type,
        "severity": body.severity,
        "source": "manual",
        "payload": body.payload,
    }
    return await start_signal_workflow(payload)


@router.get("/signals")
async def get_signals(
    tenant_id: str = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
):
    """List recent signals for the dashboard."""
    resolved = _require_tenant(x_tenant_id, tenant_id)
    return {"signals": await list_signals(tenant_id=resolved, limit=limit)}


# NOTE: customer read/write endpoints live in routes/customers.py (authenticated,
# membership-derived tenant access). This module owns only signal endpoints.


__all__ = ["router"]
