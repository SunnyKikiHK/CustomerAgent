"""Approval verification implementations for external MCP actions."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Protocol

from apps.tool_gateway.src.contracts import ActionContext
from packages.db.src import fetch_one


class ApprovalVerifier(Protocol):
    """Verify that an action was released by trusted orchestration."""

    async def verify(
        self,
        action_name: str,
        context: ActionContext,
        payload: dict[str, object] | None = None,
    ) -> bool: ...


class LocalApprovalVerifier:
    """Development verifier that requires trusted release metadata."""

    async def verify(
        self,
        action_name: str,
        context: ActionContext,
        payload: dict[str, object] | None = None,
    ) -> bool:
        _ = payload
        return bool(action_name and context.tenant_id and context.approval_id)


class PostgresApprovalVerifier:
    """Verify approvals against the tenant-scoped action_approvals table."""

    async def verify(
        self,
        action_name: str,
        context: ActionContext,
        payload: dict[str, object] | None = None,
    ) -> bool:
        row = await fetch_one(
            """
            select approval_id, payload_hash, expires_at, consumed_at
            from action_approvals
            where approval_id = $1 and action_name = $2 and tenant_id = $3::uuid
              and (expires_at is null or expires_at > now())
            """,
            context.approval_id,
            action_name,
            context.tenant_id,
            tenant_id=context.tenant_id,
        )
        if row is None or row["consumed_at"] is not None:
            return False
        expected_hash = row["payload_hash"]
        if expected_hash and payload is not None and expected_hash != stable_payload_hash(payload):
            return False
        return True


async def persist_action_approval(
    *,
    approval_id: str,
    tenant_id: str,
    action_name: str,
    trace_id: str | None,
    payload: dict[str, object],
    expires_in_seconds: int = 3600,
) -> None:
    """Persist one compliance-approved write for later gateway verification."""
    from packages.db.src import execute

    await execute(
        """
        insert into action_approvals (
            approval_id, tenant_id, action_name, trace_id, payload_hash, expires_at
        ) values ($1, $2::uuid, $3, $4, $5, now() + make_interval(secs => $6))
        on conflict (tenant_id, approval_id, action_name)
        do update set trace_id = excluded.trace_id,
                      payload_hash = excluded.payload_hash,
                      expires_at = excluded.expires_at,
                      consumed_at = null
        """,
        approval_id,
        tenant_id,
        action_name,
        trace_id,
        stable_payload_hash(payload),
        expires_in_seconds,
        tenant_id=tenant_id,
    )


async def mark_action_approval_consumed(*, tenant_id: str, approval_id: str, action_name: str) -> None:
    """Mark an approval as consumed after a successful external side effect."""
    from packages.db.src import execute

    await execute(
        """
        update action_approvals
        set consumed_at = now()
        where approval_id = $1 and action_name = $2 and consumed_at is null
        """,
        approval_id,
        action_name,
        tenant_id=tenant_id,
    )


def stable_payload_hash(payload: dict[str, object]) -> str:
    """Build a stable SHA-256 hash for approval payload matching."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_approval_verifier() -> ApprovalVerifier:
    """Select the configured approval backend."""
    backend = os.getenv("APPROVAL_BACKEND", "postgres").strip().lower()
    if backend == "local":
        return LocalApprovalVerifier()
    return PostgresApprovalVerifier()


__all__ = [
    "ApprovalVerifier",
    "LocalApprovalVerifier",
    "PostgresApprovalVerifier",
    "get_approval_verifier",
    "persist_action_approval",
    "stable_payload_hash",
]
