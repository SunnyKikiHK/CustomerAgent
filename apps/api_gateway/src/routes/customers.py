"""Customer simulator API: create/update/delete customers and usage events.

This is the write path behind the frontend "Customer Simulator" tab. It exists
so a demo operator can shape a customer's health, MRR, renewal date, NPS, and
rolling usage_trend, then run detectors and watch signals fire.

Authorization: every route derives tenant access from the caller's memberships
(``tenant_context``), never from the ``X-Tenant-Id`` header alone, and mutations
additionally require a write role. Each mutation writes an audit row.
"""

from __future__ import annotations

from datetime import date
import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from packages.auth.src.models import AuthContext, TenantRole
from packages.auth.src.store import (
    create_user,
    get_user_by_email,
    upsert_membership,
)
from packages.db.src import PostgresConfigError, get_pool
from packages.knowledge_service.src.audit import record_audit
from packages.knowledge_service.src.customers import (
    create_customer,
    delete_customer,
    get_customer,
    list_customers,
    merge_usage_trend,
    update_customer,
)

from apps.api_gateway.src.security import require_write, tenant_context

router = APIRouter(tags=["customers"])

_DEMO_CSM_EMAIL = "csm@demo.io"
_DEMO_CSM_PASSWORD = "demo-csm-password"


def _require_local_demo() -> None:
    """Restrict unauthenticated bootstrap helpers to local/demo deployments."""
    if os.getenv("APP_ENV", "local").strip().lower() not in {
        "local",
        "development",
        "test",
    }:
        raise HTTPException(status_code=404, detail="not found")


class DemoEntityStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exists: bool


class DemoCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created: bool


@router.get("/demo/tenants/{tenant_id}/status", response_model=DemoEntityStatus)
async def demo_tenant_status(tenant_id: UUID) -> DemoEntityStatus:
    """Report whether a tenant exists before the local UI attempts creation."""
    _require_local_demo()
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "select exists(select 1 from tenants where id = $1::uuid)",
            str(tenant_id),
        )
    return DemoEntityStatus(exists=bool(exists))


@router.post("/demo/tenants/{tenant_id}", response_model=DemoCreateResult)
async def create_demo_tenant(tenant_id: UUID) -> DemoCreateResult:
    """Idempotently create a local tenant and grant the demo CSM access."""
    _require_local_demo()
    tenant_id_text = str(tenant_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            insert into tenants (id, name, plan)
            values ($1::uuid, $2, 'growth')
            on conflict (id) do nothing
            """,
            tenant_id_text,
            f"Demo Tenant {tenant_id_text[:8]}",
        )
    user = await get_user_by_email(_DEMO_CSM_EMAIL)
    if user is None:
        user = await create_user(
            email=_DEMO_CSM_EMAIL,
            password=_DEMO_CSM_PASSWORD,
            full_name="Demo CSM",
        )
    await upsert_membership(
        user_id=user.id,
        tenant_id=tenant_id_text,
        role=TenantRole.CSM,
    )
    return DemoCreateResult(id=tenant_id_text, created=status.endswith("1"))


@router.get(
    "/demo/tenants/{tenant_id}/customers/{customer_id}/status",
    response_model=DemoEntityStatus,
)
async def demo_customer_status(
    tenant_id: UUID,
    customer_id: UUID,
) -> DemoEntityStatus:
    """Report whether a tenant-scoped customer exists for the local UI."""
    _require_local_demo()
    customer = await get_customer(
        tenant_id=str(tenant_id),
        customer_id=str(customer_id),
    )
    return DemoEntityStatus(exists=customer is not None)


@router.post(
    "/demo/tenants/{tenant_id}/customers/{customer_id}",
    response_model=DemoCreateResult,
)
async def create_demo_customer(
    tenant_id: UUID,
    customer_id: UUID,
) -> DemoCreateResult:
    """Idempotently create a customer with the exact ID entered in the UI."""
    _require_local_demo()
    tenant_id_text = str(tenant_id)
    customer_id_text = str(customer_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        tenant_exists = await conn.fetchval(
            "select exists(select 1 from tenants where id = $1::uuid)",
            tenant_id_text,
        )
        if not tenant_exists:
            raise HTTPException(status_code=409, detail="create the tenant first")
        status = await conn.execute(
            """
            insert into customers
                (id, tenant_id, name, email, health_score, mrr, nps, usage_trend)
            values ($1::uuid, $2::uuid, $3, $4, 70, 0, 0, '{}'::jsonb)
            on conflict (id) do nothing
            """,
            customer_id_text,
            tenant_id_text,
            f"Demo Customer {customer_id_text[:8]}",
            f"{customer_id_text[:8]}@demo.local",
        )
    if status.endswith("0"):
        existing = await get_customer(
            tenant_id=tenant_id_text,
            customer_id=customer_id_text,
        )
        if existing is None:
            raise HTTPException(
                status_code=409,
                detail="customer ID belongs to another tenant",
            )
    return DemoCreateResult(id=customer_id_text, created=status.endswith("1"))


class UsageTrendInput(BaseModel):
    """Structured usage fields the frontend serializes into usage_trend."""

    model_config = ConfigDict(extra="forbid")

    previous_active_users: int | None = Field(default=None, ge=0)
    current_active_users: int | None = Field(default=None, ge=0)
    previous_event_count: int | None = Field(default=None, ge=0)
    current_event_count: int | None = Field(default=None, ge=0)
    previous_login_count: int | None = Field(default=None, ge=0)
    current_login_count: int | None = Field(default=None, ge=0)
    period_start: str | None = None
    period_end: str | None = None

    def to_trend(self) -> dict[str, Any]:
        return merge_usage_trend(**self.model_dump())


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    health_score: float | None = Field(default=None, ge=0, le=100)
    mrr: float | None = Field(default=None, ge=0)
    renewal_date: date | None = None
    # A single NPS survey answer is 0-10; the aggregate NPS (-100..100) is
    # computed elsewhere and never set directly here.
    nps: int | None = Field(default=None, ge=0, le=10)
    usage_trend: UsageTrendInput | None = None


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    health_score: float | None = Field(default=None, ge=0, le=100)
    mrr: float | None = Field(default=None, ge=0)
    renewal_date: date | None = None
    nps: int | None = Field(default=None, ge=0, le=10)
    usage_trend: UsageTrendInput | None = None


@router.post("/customers")
async def create_customer_route(
    body: CustomerCreate,
    auth: AuthContext = Depends(require_write),
):
    """Create a customer for the authorized tenant."""
    try:
        customer = await create_customer(
            tenant_id=auth.tenant_id,
            name=body.name,
            email=body.email,
            health_score=body.health_score,
            mrr=body.mrr,
            renewal_date=body.renewal_date,
            nps=body.nps,
            usage_trend=body.usage_trend.to_trend() if body.usage_trend else None,
        )
    except PostgresConfigError:
        raise HTTPException(status_code=503, detail="database unavailable")
    await record_audit(
        tenant_id=auth.tenant_id,
        actor=auth.user.email,
        action="customer_created",
        resource="customer",
        resource_id=customer["id"],
        after_state=customer,
    )
    return customer


@router.get("/customers/{customer_id}")
async def get_customer_route(
    customer_id: str,
    auth: AuthContext = Depends(tenant_context),
):
    """Return one customer, including usage_trend."""
    customer = await get_customer(tenant_id=auth.tenant_id, customer_id=customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return customer


@router.patch("/customers/{customer_id}")
async def update_customer_route(
    customer_id: str,
    body: CustomerUpdate,
    auth: AuthContext = Depends(require_write),
):
    """Patch mutable fields on a customer and audit the before/after state."""
    before = await get_customer(tenant_id=auth.tenant_id, customer_id=customer_id)
    if before is None:
        raise HTTPException(status_code=404, detail="customer not found")

    changes = body.model_dump(exclude_unset=True)
    if "usage_trend" in changes and body.usage_trend is not None:
        changes["usage_trend"] = body.usage_trend.to_trend()

    updated = await update_customer(
        tenant_id=auth.tenant_id, customer_id=customer_id, changes=changes
    )
    await record_audit(
        tenant_id=auth.tenant_id,
        actor=auth.user.email,
        action="customer_updated",
        resource="customer",
        resource_id=customer_id,
        before_state=before,
        after_state=updated,
    )
    return updated


@router.delete("/customers/{customer_id}")
async def delete_customer_route(
    customer_id: str,
    auth: AuthContext = Depends(require_write),
):
    """Delete a customer (demo cleanup)."""
    before = await get_customer(tenant_id=auth.tenant_id, customer_id=customer_id)
    if before is None:
        raise HTTPException(status_code=404, detail="customer not found")
    removed = await delete_customer(tenant_id=auth.tenant_id, customer_id=customer_id)
    await record_audit(
        tenant_id=auth.tenant_id,
        actor=auth.user.email,
        action="customer_deleted",
        resource="customer",
        resource_id=customer_id,
        before_state=before,
    )
    return {"deleted": removed}


@router.get("/customers")
async def list_customers_route(
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(tenant_context),
):
    """List customers for the authorized tenant."""
    return {"customers": await list_customers(tenant_id=auth.tenant_id, limit=limit)}


class UsageEventInput(BaseModel):
    """A single simulated usage snapshot merged into the customer usage_trend."""

    model_config = ConfigDict(extra="forbid")

    usage_trend: UsageTrendInput


@router.post("/customers/{customer_id}/usage-events")
async def record_usage_event(
    customer_id: str,
    body: UsageEventInput,
    auth: AuthContext = Depends(require_write),
):
    """Replace a customer's usage_trend with a new simulated snapshot."""
    before = await get_customer(tenant_id=auth.tenant_id, customer_id=customer_id)
    if before is None:
        raise HTTPException(status_code=404, detail="customer not found")
    updated = await update_customer(
        tenant_id=auth.tenant_id,
        customer_id=customer_id,
        changes={"usage_trend": body.usage_trend.to_trend()},
    )
    await record_audit(
        tenant_id=auth.tenant_id,
        actor=auth.user.email,
        action="usage_event_recorded",
        resource="customer",
        resource_id=customer_id,
        before_state={"usage_trend": before.get("usage_trend")},
        after_state={"usage_trend": updated.get("usage_trend") if updated else None},
    )
    return updated


__all__ = ["router"]
