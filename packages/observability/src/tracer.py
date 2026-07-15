"""Observability facade: structured logs + optional Langfuse + optional OTel.

One entry point (``observe``) for the whole platform. It records a span for a
business operation (signal.process, planner.build, subagent.<role>,
compliance.review, tool.send_email, qbr.generate, ...) and fans out to whichever
backends are configured:

- Always: a structured log line (cheap, dependency-free, works offline).
- Langfuse (AI behavior) when credentials are present.
- OpenTelemetry (API/DB/Temporal/network) when an OTel SDK is configured.

Every backend is optional and failures are swallowed: instrumentation must never
break a request. All attributes pass through ``redact_attributes`` so secrets and
raw PII never reach a backend.

``trace_event`` is kept as a thin wrapper over ``observe`` for existing callers.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from packages.observability.src.redaction import redact_attributes

logger = logging.getLogger("observability")


def _enabled(flag: str, default: bool = False) -> bool:
    raw = os.getenv(flag)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def langfuse_enabled() -> bool:
    """Langfuse is used only when credentials are present and not disabled."""
    if not _enabled("OBSERVABILITY_LANGFUSE", default=True):
        return False
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def otel_enabled() -> bool:
    """OTel is used when an OTLP endpoint is configured and not disabled."""
    if not _enabled("OBSERVABILITY_OTEL", default=True):
        return False
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))


class Span:
    """A handle to an in-flight operation span across all active backends."""

    __slots__ = ("name", "attributes", "_otel_span", "_started")

    def __init__(self, name: str, attributes: dict[str, Any]) -> None:
        self.name = name
        self.attributes = attributes
        self._otel_span: Any | None = None
        self._started = time.monotonic()

    def set(self, key: str, value: Any) -> None:
        """Attach one more (redacted) attribute to the span."""
        safe = redact_attributes({key: value})
        self.attributes.update(safe)
        if self._otel_span is not None and safe:
            try:
                for k, v in safe.items():
                    self._otel_span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
            except Exception:
                pass


@contextmanager
def observe(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    kind: str = "span",
) -> Iterator[Span]:
    """Record a business operation as a span across all active backends.

    Usage::

        with observe("planner.build", attributes={"tenant_id": t}) as span:
            plan = build_plan(...)
            span.set("task_count", len(plan.tasks))

    The span always emits a structured start/end log; it additionally opens a
    Langfuse span and/or an OTel span when those backends are enabled. Any
    backend error is swallowed. Attributes are redacted before leaving.
    """
    safe = redact_attributes(attributes)
    span = Span(name, dict(safe))
    otel_cm = _maybe_otel_span(name, safe)
    if otel_cm is not None:
        try:
            span._otel_span = otel_cm.__enter__()
        except Exception:
            otel_cm = None

    langfuse_span = _maybe_langfuse_span(name, safe)
    logger.info("observe.start name=%s attrs=%s", name, span.attributes)
    error: BaseException | None = None
    try:
        yield span
    except BaseException as exc:  # record then re-raise
        error = exc
        raise
    finally:
        elapsed_ms = round((time.monotonic() - span._started) * 1000, 2)
        status = "error" if error is not None else "ok"
        logger.info(
            "observe.end name=%s status=%s ms=%s attrs=%s",
            name,
            status,
            elapsed_ms,
            span.attributes,
        )
        try:
            from packages.observability.src.collector import record_span

            record_span(name, elapsed_ms, status)
        except Exception:
            pass
        _end_langfuse_span(langfuse_span, span, status, error)
        if otel_cm is not None:
            try:
                otel_cm.__exit__(type(error) if error else None, error, None)
            except Exception:
                pass


def trace_event(name: str, metadata: dict[str, Any] | None = None) -> None:
    """Record a point-in-time event (backward-compatible with older callers)."""
    safe = redact_attributes(metadata)
    logger.info("trace_event name=%s metadata=%s", name, safe)
    langfuse_span = _maybe_langfuse_span(name, safe)
    _end_langfuse_span(langfuse_span, None, "ok", None)


def _maybe_otel_span(name: str, attributes: dict[str, Any]) -> Any | None:
    """Return an OTel span context manager when OTel is enabled, else None."""
    if not otel_enabled():
        return None
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("customer-agent")
        cm = tracer.start_as_current_span(name)
        return cm
    except Exception:
        return None


def _maybe_langfuse_span(name: str, attributes: dict[str, Any]) -> Any | None:
    """Open a Langfuse span/trace when enabled, else None."""
    if not langfuse_enabled():
        return None
    try:
        from packages.observability.src.langfuse import get_langfuse_client

        client = get_langfuse_client()
        if client is None:
            return None
        trace_id = attributes.get("trace_id")
        return client.trace(name=name, id=trace_id, metadata=attributes)
    except Exception:
        return None


def _end_langfuse_span(
    langfuse_span: Any | None,
    span: Span | None,
    status: str,
    error: BaseException | None,
) -> None:
    if langfuse_span is None:
        return
    try:
        output = {"status": status}
        if error is not None:
            output["error"] = type(error).__name__
        if span is not None:
            output.update(span.attributes)
        langfuse_span.update(output=output)
    except Exception:
        pass


__all__ = [
    "observe",
    "trace_event",
    "langfuse_enabled",
    "otel_enabled",
    "Span",
]
