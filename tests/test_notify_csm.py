"""Tests for CSM notify helper (nps_detractor → EMAIL_FROM)."""

from __future__ import annotations


def test_csm_notify_email_requires_valid_email_from(monkeypatch):
    from packages.tool_system.src.tools.notify_csm import csm_notify_email

    monkeypatch.delenv("EMAIL_FROM", raising=False)
    assert csm_notify_email() is None
    monkeypatch.setenv("EMAIL_FROM", "not-an-email")
    assert csm_notify_email() is None
    monkeypatch.setenv("EMAIL_FROM", "csm@demo.io")
    assert csm_notify_email() == "csm@demo.io"


def test_rewrite_writes_to_csm_forces_recipient(monkeypatch):
    from packages.tool_system.src.tools.notify_csm import rewrite_writes_to_csm

    monkeypatch.setenv("EMAIL_FROM", "csm@demo.io")
    writes = [
        {
            "tool": "send_email",
            "arguments": {
                "tenant_id": "t1",
                "customer_id": "c1",
                "recipient_email": "customer@acme.example",
                "subject": "Sorry",
                "body": "We heard you.",
                "sender_name": "Agent",
            },
        }
    ]
    out = rewrite_writes_to_csm(writes, tenant_id="t1", customer_id="c1")
    assert len(out) == 1
    assert out[0]["arguments"]["recipient_email"] == "csm@demo.io"
    assert out[0]["arguments"]["body"] == "We heard you."


def test_ensure_nps_detractor_synthesizes_email_when_missing(monkeypatch):
    from packages.tool_system.src.tools.notify_csm import ensure_nps_detractor_csm_notify

    monkeypatch.setenv("EMAIL_FROM", "csm@demo.io")
    out = ensure_nps_detractor_csm_notify(
        tenant_id="t1",
        customer_id="c1",
        signal_payload={"score": 3, "comment": "slow"},
        draft_markdown="CSM should call this account today.",
        approved_writes=[],
    )
    assert len(out) == 1
    assert out[0]["tool"] == "send_email"
    args = out[0]["arguments"]
    assert args["recipient_email"] == "csm@demo.io"
    assert "3" in args["subject"]
    assert "CSM should call" in args["body"]


def test_ensure_nps_detractor_skips_without_email_from(monkeypatch):
    from packages.tool_system.src.tools.notify_csm import ensure_nps_detractor_csm_notify

    monkeypatch.delenv("EMAIL_FROM", raising=False)
    out = ensure_nps_detractor_csm_notify(
        tenant_id="t1",
        customer_id="c1",
        signal_payload={"score": 2},
        draft_markdown="draft",
        approved_writes=[],
    )
    assert out == []
