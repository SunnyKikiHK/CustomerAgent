"""notify_csm: trusted helper that emails the final draft to the CSM inbox.

Used for ``nps_detractor`` (and similar internal-alert signals). The recipient is
always ``EMAIL_FROM`` (the platform's own CSM mailbox — "send email to self" in
local/demo). This is not an LLM-callable tool; trusted code builds a normal
``send_email`` payload so the existing approval → MCP gateway path still applies.
"""

from __future__ import annotations

import os
import re
from typing import Any

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def csm_notify_email() -> str | None:
    """Return the configured CSM notify address (``EMAIL_FROM``), or None if unset."""
    raw = (os.getenv("EMAIL_FROM") or "").strip()
    if not raw or not _EMAIL_RE.match(raw):
        return None
    return raw


def build_notify_csm_write(
    *,
    tenant_id: str,
    customer_id: str,
    subject: str,
    body: str,
    sender_name: str = "Customer Success Agent",
) -> dict[str, Any] | None:
    """Build a ``send_email`` proposed-write targeting the CSM inbox.

    Returns None when ``EMAIL_FROM`` is missing/invalid so callers can skip cleanly.
    """
    recipient = csm_notify_email()
    if recipient is None:
        return None
    subject = (subject or "CSM alert").strip()[:200] or "CSM alert"
    body = (body or "").strip()
    if not body:
        return None
    return {
        "tool": "send_email",
        "arguments": {
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "recipient_email": recipient,
            "subject": subject,
            "body": body,
            "sender_name": sender_name,
        },
    }


def rewrite_writes_to_csm(
    writes: list[dict[str, Any]],
    *,
    tenant_id: str,
    customer_id: str,
) -> list[dict[str, Any]]:
    """Force any approved ``send_email`` payloads to go to ``EMAIL_FROM``.

    Leaves non-email actions unchanged. If there are no email writes, returns the
    original list unchanged (caller may append a synthesized notify write).
    """
    recipient = csm_notify_email()
    if recipient is None or not writes:
        return list(writes)

    out: list[dict[str, Any]] = []
    for write in writes:
        action = str(write.get("tool") or write.get("name") or write.get("action") or "")
        raw = write.get("arguments", write.get("params", write.get("payload", write)))
        if action != "send_email" or not isinstance(raw, dict):
            out.append(write)
            continue
        args = dict(raw)
        args["tenant_id"] = tenant_id
        args["customer_id"] = customer_id
        args["recipient_email"] = recipient
        args.setdefault("sender_name", "Customer Success Agent")
        out.append({"tool": "send_email", "arguments": args})
    return out


def ensure_nps_detractor_csm_notify(
    *,
    tenant_id: str,
    customer_id: str,
    signal_payload: dict[str, Any] | None,
    draft_markdown: str,
    approved_writes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the email write list for an ``nps_detractor`` signal.

    1. Rewrite any approved customer emails to the CSM inbox (``EMAIL_FROM``).
    2. If none exist, synthesize one from the outreach draft markdown.
    """
    rewritten = rewrite_writes_to_csm(
        approved_writes, tenant_id=tenant_id, customer_id=customer_id
    )
    if any(
        str(w.get("tool") or w.get("name") or w.get("action") or "") == "send_email"
        for w in rewritten
    ):
        return rewritten

    score = (signal_payload or {}).get("score")
    comment = (signal_payload or {}).get("comment")
    subject = "NPS detractor — CSM follow-up needed"
    if score is not None:
        subject = f"NPS detractor (score {score}) — CSM follow-up needed"

    body_parts = [
        "# NPS detractor alert",
        "",
        f"- Customer id: `{customer_id}`",
        f"- Tenant id: `{tenant_id}`",
    ]
    if score is not None:
        body_parts.append(f"- Score: **{score}**")
    if comment:
        body_parts.append(f"- Comment: {comment}")
    body_parts.extend(["", "## Draft / analysis", "", draft_markdown.strip() or "(no draft body)"])
    synthesized = build_notify_csm_write(
        tenant_id=tenant_id,
        customer_id=customer_id,
        subject=subject,
        body="\n".join(body_parts),
    )
    if synthesized is None:
        return rewritten
    return [*rewritten, synthesized]


__all__ = [
    "csm_notify_email",
    "build_notify_csm_write",
    "rewrite_writes_to_csm",
    "ensure_nps_detractor_csm_notify",
]
