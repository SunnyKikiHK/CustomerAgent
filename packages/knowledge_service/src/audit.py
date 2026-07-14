"""Audit-log persistence (Postgres ``audit_logs``).

Records who changed what, with before/after state, for customer edits, detector
runs, signal state changes, and agent/compliance decisions. Best-effort: an
audit write must never break the operation it records, so failures are swallowed
(the caller's action already succeeded or will report its own error).
"""

from __future__ import annotations

import json
from typing import Any

from packages.db.src import PostgresConfigError, execute


async def record_audit(
    *,
    tenant_id: str,
    action: str,
    actor: str = "system",
    resource: str | None = None,
    resource_id: str | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> bool:
    """Insert one audit row. Returns True on success, False if unavailable."""
    try:
        await execute(
            """
            insert into audit_logs (
                tenant_id, actor, action, resource, resource_id,
                before_state, after_state, ip_address
            )
            values ($1::uuid, $2, $3, $4, $5::uuid, $6::jsonb, $7::jsonb, $8)
            """,
            tenant_id,
            actor,
            action,
            resource,
            resource_id,
            json.dumps(before_state) if before_state is not None else None,
            json.dumps(after_state) if after_state is not None else None,
            ip_address,
            tenant_id=tenant_id,
        )
        return True
    except PostgresConfigError:
        return False
    except Exception:
        # Audit is best-effort; never propagate.
        return False


__all__ = ["record_audit"]
