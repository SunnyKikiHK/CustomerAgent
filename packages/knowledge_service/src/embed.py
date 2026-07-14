"""Embedding helpers for pgvector retrieval.

Primary path: an OpenAI-compatible ``/embeddings`` endpoint (OpenRouter by
default). When no provider is configured, or a call fails, a stable local
n-gram hash vector of the same dimension is used so retrieval, intent fusion,
and tests keep working without external services.
"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)

#: Target embedding dimension. Must match knowledge_chunks.embedding VECTOR(n).
#: 1536 matches the configured embedding model (qwen/qwen3-embedding-8b emits
#: 1536 via MRL dimension selection; the local fallback vector uses the same n).
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

#: Default embedding model id (OpenAI-compatible, served via OpenRouter) used
#: when EMBEDDING_MODEL is unset.
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"

_CLIENT = None
_CLIENT_DISABLED = False
_FALLBACK_WARNED = False


def local_embedding(text: str, dims: int = EMBEDDING_DIM) -> list[float]:
    """Build a stable character n-gram hash vector.

    Deterministic and dependency-free: the same text always maps to the same
    vector, so semantic-ish nearest-neighbour retrieval degrades gracefully
    when the real embedding provider is unavailable.
    """
    normalized = text.lower().strip()
    vec = [0.0] * dims
    tokens: set[str] = set()
    for n in (1, 2, 3):
        if len(normalized) >= n:
            tokens.update(normalized[i : i + n] for i in range(len(normalized) - n + 1))
    if not tokens:
        tokens.add(normalized)

    for token in tokens:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    return vec


def _get_client():
    """Lazily build the async OpenAI-compatible embeddings client."""
    global _CLIENT, _CLIENT_DISABLED
    if _CLIENT_DISABLED:
        return None
    if _CLIENT is not None:
        return _CLIENT

    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        _CLIENT_DISABLED = True
        return None
    try:
        from openai import AsyncOpenAI

        _CLIENT = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            timeout=float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("EMBEDDING_MAX_RETRIES", "1")),
        )
    except Exception:  # pragma: no cover - import/config guard
        _CLIENT_DISABLED = True
        return None
    return _CLIENT


def _fit_dims(vector: list[float], dims: int = EMBEDDING_DIM) -> list[float]:
    """Pad or truncate a vector to the expected column dimension."""
    if len(vector) == dims:
        return vector
    if len(vector) > dims:
        return vector[:dims]
    return vector + [0.0] * (dims - len(vector))


async def embed_text(text: str) -> list[float]:
    """Return an embedding vector for the given text.

    Tries the configured embedding API; on any failure or missing config, falls
    back to :func:`local_embedding` (logged once).
    """
    global _FALLBACK_WARNED
    client = _get_client()
    if client is not None:
        model = os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        try:
            response = await client.embeddings.create(model=model, input=text or " ")
            return _fit_dims(list(response.data[0].embedding))
        except Exception as exc:  # network/model/quota errors -> fallback
            if not _FALLBACK_WARNED:
                logger.warning(
                    "embedding provider unavailable, using local fallback: %s", exc
                )
                _FALLBACK_WARNED = True

    return local_embedding(text)


def reset_embedding_client() -> None:
    """Reset cached client state (used by tests toggling env vars)."""
    global _CLIENT, _CLIENT_DISABLED, _FALLBACK_WARNED
    _CLIENT = None
    _CLIENT_DISABLED = False
    _FALLBACK_WARNED = False


__all__ = [
    "embed_text",
    "local_embedding",
    "reset_embedding_client",
    "EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_MODEL",
]
