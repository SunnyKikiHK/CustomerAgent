"""Relational customer persistence and simulation helpers (Postgres ``customers``).

The customer simulator (frontend + API) writes here to create/update the fields
the detectors and ``query_health`` read: health score, MRR, renewal date, NPS,
and the rolling ``usage_trend`` JSON. Every write is tenant-scoped through the
RLS-aware ``execute``/``fetch_one`` helpers; the API layer additionally derives
tenant authorization from the caller's memberships (never the header alone).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from packages.db.src import PostgresConfigError, execute, fetch_all, fetch_one

#: Fields a simulator update may set on a customer row.
_UPDATABLE_FIELDS = (
    "name",
    "email",
    "health_score",
    "mrr",
    "renewal_date",
    "nps",
    "usage_trend",
)


def _row_to_customer(row: Any) -> dict[str, Any]:
    """Normalize an asyncpg customer row into a JSON-safe dict."""
    usage_trend = row["usage_trend"]
    if isinstance(usage_trend, str):
        usage_trend = json.loads(usage_trend) if usage_trend else {}
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "email": row["email"],
        "health_score": float(row["health_score"]) if row["health_score"] is not None else None,
        "mrr": float(row["mrr"]) if row["mrr"] is not None else None,
        "renewal_date": row["renewal_date"].isoformat() if row["renewal_date"] else None,
        "nps": row["nps"],
        "usage_trend": usage_trend or {},
        "support_ticket_count": row.get("support_ticket_count")
        if hasattr(row, "get")
        else None,
    }


_SELECT_COLUMNS = (
    "id, tenant_id, name, email, health_score, mrr, renewal_date, nps, usage_trend"
)


async def get_customer(*, tenant_id: str, customer_id: str) -> dict[str, Any] | None:
    """Return one tenant-scoped customer, or None if absent/unavailable."""
    try:
        row = await fetch_one(
            f"select {_SELECT_COLUMNS} from customers"
            " where id = $1::uuid and tenant_id = $2::uuid",
            customer_id,
            tenant_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    return _row_to_customer(row) if row else None


async def list_customers(*, tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Return tenant-scoped customers ordered by ascending health score."""
    try:
        rows = await fetch_all(
            f"select {_SELECT_COLUMNS} from customers"
            " order by health_score asc nulls last limit $1",
            limit,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return []
    return [_row_to_customer(row) for row in rows]


async def create_customer(
    *,
    tenant_id: str,
    name: str | None = None,
    email: str | None = None,
    health_score: float | None = None,
    mrr: float | None = None,
    renewal_date: date | None = None,
    nps: int | None = None,
    usage_trend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a customer and return the created row."""
    row = await fetch_one(
        f"""
        insert into customers (tenant_id, name, email, health_score, mrr,
                               renewal_date, nps, usage_trend)
        values ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb)
        returning {_SELECT_COLUMNS}
        """,
        tenant_id,
        name,
        email,
        health_score,
        mrr,
        renewal_date,
        nps,
        json.dumps(usage_trend or {}),
        tenant_id=tenant_id,
    )
    return _row_to_customer(row)


async def update_customer(
    *,
    tenant_id: str,
    customer_id: str,
    changes: dict[str, Any],
) -> dict[str, Any] | None:
    """Patch the allowed fields on a customer, returning the updated row.

    Only keys in ``_UPDATABLE_FIELDS`` are applied; unknown keys are ignored so
    the API model is the single source of truth for what a caller can mutate.
    Returns None when the customer does not exist for the tenant.
    """
    applied = {key: changes[key] for key in _UPDATABLE_FIELDS if key in changes}
    if not applied:
        return await get_customer(tenant_id=tenant_id, customer_id=customer_id)

    set_clauses: list[str] = []
    args: list[Any] = []
    index = 1
    for key, value in applied.items():
        if key == "usage_trend":
            set_clauses.append(f"{key} = ${index}::jsonb")
            args.append(json.dumps(value or {}))
        else:
            set_clauses.append(f"{key} = ${index}")
            args.append(value)
        index += 1

    args.extend([customer_id, tenant_id])
    row = await fetch_one(
        f"""
        update customers set {", ".join(set_clauses)}
        where id = ${index}::uuid and tenant_id = ${index + 1}::uuid
        returning {_SELECT_COLUMNS}
        """,
        *args,
        tenant_id=tenant_id,
    )
    return _row_to_customer(row) if row else None


async def delete_customer(*, tenant_id: str, customer_id: str) -> bool:
    """Delete a customer for demo cleanup. Returns True when a row was removed."""
    try:
        status = await execute(
            "delete from customers where id = $1::uuid and tenant_id = $2::uuid",
            customer_id,
            tenant_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return False
    # asyncpg returns e.g. "DELETE 1"
    return status.rsplit(" ", 1)[-1] != "0"


def merge_usage_trend(
    *,
    previous_active_users: int | None = None,
    current_active_users: int | None = None,
    previous_event_count: int | None = None,
    current_event_count: int | None = None,
    previous_login_count: int | None = None,
    current_login_count: int | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> dict[str, Any]:
    """Serialize simulator usage fields into the ``usage_trend`` JSON shape.

    Keeps the shape flat and explicit so the frontend never has to hand-author
    JSON and the usage-decline detector can compute deltas deterministically.
    Only provided fields are included.
    """
    trend: dict[str, Any] = {}
    pairs = {
        "previous_active_users": previous_active_users,
        "current_active_users": current_active_users,
        "previous_event_count": previous_event_count,
        "current_event_count": current_event_count,
        "previous_login_count": previous_login_count,
        "current_login_count": current_login_count,
        "period_start": period_start,
        "period_end": period_end,
    }
    for key, value in pairs.items():
        if value is not None:
            trend[key] = value
    return trend


__all__ = [
    "get_customer",
    "list_customers",
    "create_customer",
    "update_customer",
    "delete_customer",
    "merge_usage_trend",
]
