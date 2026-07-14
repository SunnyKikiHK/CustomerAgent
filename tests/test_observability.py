"""Offline tests for the Phase 4 observability facade and redaction."""

from __future__ import annotations

import pytest


# ── Redaction ─────────────────────────────────────────────────────────────────

def test_redaction_drops_sensitive_keys():
    from packages.observability.src.redaction import redact_attributes

    out = redact_attributes(
        {
            "tenant_id": "t1",
            "password": "x",
            "password_hash": "y",
            "access_token": "z",
            "refresh_token": "z",
            "api_key": "k",
            "client_secret": "s",
            "authorization": "Bearer x",
            "email_body": "dear customer",
            "prompt": "raw pii prompt",
            "messages": [{"role": "user"}],
        }
    )
    assert out == {"tenant_id": "t1"}


def test_redaction_truncates_long_strings():
    from packages.observability.src.redaction import redact_attributes

    out = redact_attributes({"note": "a" * 1000})
    assert len(out["note"]) <= 257
    assert out["note"].endswith("…")


def test_redaction_summarizes_lists_and_recurses_dicts():
    from packages.observability.src.redaction import redact_attributes

    out = redact_attributes(
        {"items": [1, 2, 3, 4], "nested": {"ok": 1, "secret": "no"}}
    )
    assert out["items"] == {"_count": 4}
    assert out["nested"] == {"ok": 1}


def test_redaction_substring_match_on_key():
    from packages.observability.src.redaction import redact_attributes

    # A key containing "token"/"secret" is dropped even if not an exact match.
    out = redact_attributes({"user_token": "x", "my_secret_val": "y", "keep": 1})
    assert out == {"keep": 1}


def test_redaction_handles_none():
    from packages.observability.src.redaction import redact_attributes

    assert redact_attributes(None) == {}


# ── observe / trace_event ───────────────────────────────────────────────────────

def test_observe_yields_span_and_swallows_sensitive():
    from packages.observability.src.tracer import observe

    with observe("test.op", attributes={"tenant_id": "t1", "api_key": "leak"}) as span:
        span.set("count", 3)
        span.set("token", "leak")  # dropped by redaction
    assert span.attributes["tenant_id"] == "t1"
    assert span.attributes["count"] == 3
    assert "api_key" not in span.attributes
    assert "token" not in span.attributes


def test_observe_records_error_and_reraises():
    from packages.observability.src.tracer import observe

    with pytest.raises(ValueError):
        with observe("test.fail", attributes={"tenant_id": "t1"}):
            raise ValueError("boom")


def test_trace_event_backward_compatible():
    from packages.observability.src.tracer import trace_event

    # Must not raise, and must accept the legacy (name, metadata) signature.
    trace_event("legacy.event", {"tenant_id": "t1", "password": "drop"})
    trace_event("legacy.event.no_meta")


def test_backends_disabled_without_config(monkeypatch):
    from packages.observability.src import tracer

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracer.langfuse_enabled() is False
    assert tracer.otel_enabled() is False


def test_langfuse_client_none_without_credentials(monkeypatch):
    from packages.observability.src import langfuse as lf

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    lf.reset_langfuse_client()
    assert lf.get_langfuse_client() is None
