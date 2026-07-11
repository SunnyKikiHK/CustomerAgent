"""Embedding helpers for pgvector retrieval.

When no remote embedding provider is configured, a stable local n-gram hash
vector is used so retrieval and intent fusion can run in tests without external
services.
"""

from __future__ import annotations

import hashlib


def local_embedding(text: str, dims: int = 256) -> list[float]:
    """Build a stable character n-gram hash vector."""
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


async def embed_text(text: str) -> list[float]:
    """Return an embedding vector for the given text."""
    return local_embedding(text)


__all__ = ["embed_text", "local_embedding"]
