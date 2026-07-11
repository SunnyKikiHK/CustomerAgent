"""Normalize inbound webhook/detector payloads into CustomerSignal."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from packages.agent.src.types import CustomerSignal


def normalize_signal_payload(payload: dict[str, Any]) -> CustomerSignal:
    """Normalize a queued or webhook payload into a CustomerSignal."""
    signal_type = str(payload.get("type") or payload.get("signal_type") or "unknown")
    tenant_id = str(payload["tenant_id"])
    customer_id = str(payload["customer_id"])
    signal_payload = payload.get("payload", {})
    if not isinstance(signal_payload, dict):
        signal_payload = {"value": signal_payload}
    return CustomerSignal(
        id=str(payload.get("id") or _dedupe_key(tenant_id, customer_id, signal_type, signal_payload)),
        tenant_id=tenant_id,
        customer_id=customer_id,
        type=signal_type,
        payload=signal_payload,
    )


def _dedupe_key(
    tenant_id: str,
    customer_id: str,
    signal_type: str,
    payload: dict[str, Any],
) -> str:
    digest = hashlib.md5(
        json.dumps(
            {
                "tenant_id": tenant_id,
                "customer_id": customer_id,
                "type": signal_type,
                "payload": payload,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    return digest


__all__ = ["normalize_signal_payload"]
