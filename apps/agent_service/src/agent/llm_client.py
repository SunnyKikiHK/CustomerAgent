"""Thin interim OpenRouter wrapper with optional Langfuse tracing.

This module is a direct-provider stand-in until the dedicated LLM Gateway owns
model routing, caching, billing, and circuit breaking.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from packages.agent.src.models import worker_model
from packages.agent.src.types import LLMUsage


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
        self._langfuse = langfuse_client or self._build_langfuse_client()

    async def complete(
        self,
        messages: Sequence[LLMMessage | Mapping[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        trace_id: str | None = None,
        name: str = "agent.llm.complete",
        metadata: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        """Call OpenAI Chat Completions and return normalized text/usage."""
        selected_model = model or self.default_model
        payload_messages = [self._serialize_message(message) for message in messages]
        generation = self._start_generation(
            name=name,
            model=selected_model,
            trace_id=trace_id,
            messages=payload_messages,
            metadata=dict(metadata or {}),
        )

        request: dict[str, Any] = {
            "model": selected_model,
            "messages": payload_messages,
            "temperature": temperature,
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

    @staticmethod
    def _serialize_message(message: LLMMessage | Mapping[str, str]) -> dict[str, str]:
        if isinstance(message, LLMMessage):
            return message.model_dump()
        return {"role": message["role"], "content": message["content"]}

    @staticmethod
    def _build_langfuse_client() -> Any | None:
        try:
            from langfuse import Langfuse
        except ImportError:
            return None

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST")
        if not public_key or not secret_key:
            return None
        return Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    def _start_generation(
        self,
        *,
        name: str,
        model: str,
        trace_id: str | None,
        messages: list[dict[str, str]],
        metadata: dict[str, Any],
    ) -> Any | None:
        if self._langfuse is None:
            return None
        try:
            trace = self._langfuse.trace(id=trace_id, name=name, metadata=metadata)
            return trace.generation(name=name, model=model, input=messages, metadata=metadata)
        except Exception:
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
        try:
            if error is not None:
                generation.end(level="ERROR", status_message=str(error))
                return
            if response is not None:
                generation.end(
                    output=response.text,
                    usage={
                        "promptTokens": response.usage.prompt_tokens,
                        "completionTokens": response.usage.completion_tokens,
                        "totalTokens": response.usage.total,
                    },
                )
        except Exception:
            return


__all__ = ["LLMClient", "LLMMessage", "LLMResponse"]
