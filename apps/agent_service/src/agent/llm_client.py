"""Thin interim OpenRouter wrapper with optional Langfuse tracing.

This module is a direct-provider stand-in until the dedicated LLM Gateway owns
model routing, caching, billing, and circuit breaking.

Reasoning (thinking) mode is controlled per call via ``reasoning=True/False``,
or left unset to follow ``LLM_REASONING_DEFAULT`` (default off for latency).

When enabled, the OpenRouter payload is always:

    {"reasoning": {"enabled": true, "budget_tokens": <BUDGET_TOKENS>}}

where ``BUDGET_TOKENS`` is read from the environment (default 512). Latency-sensitive
steps (intent, role routing, rewrite/rerank, memory) always pass ``reasoning=False``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from packages.agent.src.models import worker_model
from packages.agent.src.types import LLMUsage
from packages.observability.src.langfuse import get_langfuse_client
from packages.observability.src.redaction import redact_attributes

logger = logging.getLogger(__name__)

#: Default reasoning token budget when ``BUDGET_TOKENS`` is unset.
_DEFAULT_BUDGET_TOKENS = 512


def budget_tokens() -> int:
    """Return the reasoning token budget from ``BUDGET_TOKENS`` (default 512)."""
    raw = os.getenv("BUDGET_TOKENS", str(_DEFAULT_BUDGET_TOKENS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_BUDGET_TOKENS
    return max(0, value)


def reasoning_default() -> bool:
    """Whether calls that omit ``reasoning=`` should enable thinking.

    Controlled by ``LLM_REASONING_DEFAULT`` (default false). Keep off for chat
    latency; set to 1/true only when you want thinking on subagents/critic.
    """
    raw = (os.getenv("LLM_REASONING_DEFAULT") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def reasoning_payload(enabled: bool) -> dict[str, Any]:
    """Build the OpenRouter ``reasoning`` object for a completion request.

    Enabled shape (required by reasoning models):

        {"enabled": true, "budget_tokens": <BUDGET_TOKENS>}

    Disabled shape:

        {"enabled": false}
    """
    if enabled:
        return {"enabled": True, "budget_tokens": budget_tokens()}
    return {"enabled": False}


class LLMMessage(BaseModel):
    """OpenAI-compatible chat message."""

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class LLMResponse(BaseModel):
    """Normalized response from a chat completion call."""

    model_config = ConfigDict(extra="forbid")

    text: str
    model: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw_response_id: str | None = None


class LLMClient:
    """Small async client for OpenRouter chat completions (OpenAI-compatible)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str | None = None,
        langfuse_client: Any | None = None,
    ) -> None:
        # A key is required to instantiate the SDK. Fall back to a placeholder so
        # construction never crashes offline; real calls then fail fast and the
        # orchestrator degrades gracefully (delegation catches the error).
        resolved_key = api_key or os.getenv("OPENROUTER_API_KEY") or "missing"
        self._client = AsyncOpenAI(
            api_key=resolved_key,
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "1")),
        )
        self.default_model = default_model or worker_model()
        self._langfuse = langfuse_client if langfuse_client is not None else get_langfuse_client()

    async def complete(
        self,
        messages: Sequence[LLMMessage | Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool | None = None,
        trace_id: str | None = None,
        name: str = "agent.llm.complete",
        metadata: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        """Call OpenAI Chat Completions and return normalized text/usage.

        ``reasoning``:
          - ``None`` (default) -> ``LLM_REASONING_DEFAULT`` (off unless set)
          - ``True`` / ``False`` -> explicit override for this call
        Latency-sensitive steps should pass ``reasoning=False`` explicitly.
        """
        selected_model = model or self.default_model
        use_reasoning = reasoning_default() if reasoning is None else bool(reasoning)
        payload_messages = [self._serialize_message(message) for message in messages]
        generation = self._start_generation(
            name=name,
            model=selected_model,
            trace_id=trace_id,
            messages=payload_messages,
            metadata=dict(metadata or {}),
            model_parameters={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_enabled": use_reasoning,
            },
        )

        request: dict[str, Any] = {
            "model": selected_model,
            "messages": payload_messages,
            "temperature": temperature,
            # `reasoning` is an OpenRouter extension, not an OpenAI SDK kwarg.
            # The SDK rejects unknown top-level kwargs, so it must be passed via
            # extra_body to reach the provider verbatim.
            "extra_body": {"reasoning": reasoning_payload(use_reasoning)},
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        try:
            completion = await self._client.chat.completions.create(**cast(Any, request))
            choice = completion.choices[0] if completion.choices else None
            text = choice.message.content if choice and choice.message.content else ""
            usage = LLMUsage(
                prompt_tokens=getattr(completion.usage, "prompt_tokens", 0) if completion.usage else 0,
                completion_tokens=(
                    getattr(completion.usage, "completion_tokens", 0) if completion.usage else 0
                ),
            )
            response = LLMResponse(
                text=cast(str, text),
                model=selected_model,
                usage=usage,
                raw_response_id=cast(str | None, getattr(completion, "id", None)),
            )
            self._end_generation(generation, response=response)
            return response
        except Exception as exc:
            self._end_generation(generation, error=exc)
            raise

    async def stream(
        self,
        messages: Sequence[LLMMessage | Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        reasoning: bool | None = None,
        trace_id: str | None = None,
        name: str = "agent.llm.stream",
        metadata: Mapping[str, Any] | None = None,
        on_complete: Callable[[LLMResponse], None] | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion while recording one Langfuse v4 generation."""
        selected_model = model or self.default_model
        use_reasoning = reasoning_default() if reasoning is None else bool(reasoning)
        payload_messages = [self._serialize_message(message) for message in messages]
        generation = self._start_generation(
            name=name,
            model=selected_model,
            trace_id=trace_id,
            messages=payload_messages,
            metadata=dict(metadata or {}),
            model_parameters={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_enabled": use_reasoning,
                "stream": True,
            },
        )
        request: dict[str, Any] = {
            "model": selected_model,
            "messages": payload_messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": {"reasoning": reasoning_payload(use_reasoning)},
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        text_parts: list[str] = []
        usage = LLMUsage()
        response_id: str | None = None
        try:
            stream = await self._client.chat.completions.create(**cast(Any, request))
            async for chunk in stream:
                response_id = response_id or cast(str | None, getattr(chunk, "id", None))
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage.prompt_tokens = getattr(chunk_usage, "prompt_tokens", 0)
                    usage.completion_tokens = getattr(
                        chunk_usage,
                        "completion_tokens",
                        0,
                    )
                choices = getattr(chunk, "choices", [])
                delta = choices[0].delta if choices else None
                content = getattr(delta, "content", None) if delta else None
                if content:
                    text_parts.append(content)
                    yield content
            response = LLMResponse(
                text="".join(text_parts),
                model=selected_model,
                usage=usage,
                raw_response_id=response_id,
            )
            self._end_generation(generation, response=response)
            if on_complete is not None:
                on_complete(response)
        except Exception as exc:
            self._end_generation(generation, error=exc)
            raise

    @staticmethod
    def _serialize_message(message: LLMMessage | Mapping[str, str]) -> dict[str, str]:
        if isinstance(message, LLMMessage):
            return message.model_dump()
        return {"role": message["role"], "content": message["content"]}

    def _start_generation(
        self,
        *,
        name: str,
        model: str,
        trace_id: str | None,
        messages: list[dict[str, str]],
        metadata: dict[str, Any],
        model_parameters: dict[str, Any] | None = None,
    ) -> Any | None:
        """Start an SDK v4 generation and return its context and observation."""
        if self._langfuse is None:
            return None
        try:
            trace_context = None
            if trace_id:
                trace_context = {
                    "trace_id": self._langfuse.create_trace_id(seed=str(trace_id)),
                }
            context_manager = self._langfuse.start_as_current_observation(
                trace_context=trace_context,
                name=name,
                as_type="generation",
                input=messages,
                model=model,
                model_parameters=model_parameters,
                metadata=redact_attributes(metadata),
            )
            observation = context_manager.__enter__()
            return context_manager, observation
        except Exception:
            logger.exception("Failed to start Langfuse generation name=%s", name)
            return None

    @staticmethod
    def _end_generation(
        generation: Any | None,
        *,
        response: LLMResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        if generation is None:
            return
        context_manager, observation = generation
        try:
            if error is not None:
                observation.update(
                    level="ERROR",
                    status_message=f"{type(error).__name__}: {error}",
                )
            elif response is not None:
                observation.update(
                    output=response.text,
                    usage_details={
                        "input": response.usage.prompt_tokens,
                        "output": response.usage.completion_tokens,
                        "total": response.usage.total,
                    },
                    metadata={"provider_response_id": response.raw_response_id},
                )
        except Exception:
            logger.exception("Failed to update Langfuse generation")
        finally:
            try:
                context_manager.__exit__(
                    type(error) if error is not None else None,
                    error,
                    error.__traceback__ if error is not None else None,
                )
            except Exception:
                logger.exception("Failed to close Langfuse generation")


__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "budget_tokens",
    "reasoning_default",
    "reasoning_payload",
]
