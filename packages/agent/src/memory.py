"""Three-tier conversation memory and shared user profile storage."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
from packages.knowledge_service.src.retrieve import retrieve_documents, store_document

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MemoryContext:
    """Fused memory slice for planners and CustomerChatAgent."""

    recent_messages: list[ChatMessage] = field(default_factory=list)
    relevant_history: list[str] = field(default_factory=list)
    user_profile: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_prompt_text(self, *, max_chars: int = 6000) -> str:
        """Format the fused memory for LLM consumption."""
        parts: list[str] = []
        if self.summary:
            parts.append(f"[Session summary]\n{self.summary}")
        if self.relevant_history:
            parts.append(
                "[Relevant history]\n"
                + "\n".join(f"- {item}" for item in self.relevant_history[:3])
            )
        if self.user_profile:
            parts.append(f"[User profile]\n{json.dumps(self.user_profile, default=str)}")
        if self.recent_messages:
            parts.append("[Recent messages]")
            for message in self.recent_messages[-8:]:
                parts.append(f"{message.role.value}: {message.content}")
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "\n..."
        return text

    def profile_excerpt(self, *, max_chars: int = 1200) -> str | None:
        """Return a bounded read-only profile slice for signal subagents."""
        if not self.user_profile:
            return None
        text = json.dumps(self.user_profile, default=str)
        if len(text) > max_chars:
            return text[:max_chars].rstrip() + "..."
        return text


class ConversationMemory:
    """Redis working memory plus pgvector episodic/profile tiers."""

    WORKING_MAX = 20
    COMPRESS_AT = 15
    HISTORY_TOP_K = 5

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._redis: Any | None = None
        self._memory_store: dict[str, list[str]] = {}

    def _client(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        if redis is None or not self._redis_url:
            return None
        try:
            self._redis = redis.from_url(self._redis_url, decode_responses=True)
            self._redis.ping()
            return self._redis
        except Exception:
            self._redis = None
            return None

    async def add_message(self, message: ChatMessage) -> None:
        """Append a message to working memory and compress when needed."""
        key = self._wm_key(message.tenant_id, message.customer_id, message.session_id)
        payload = json.dumps(
            {
                "role": message.role.value,
                "content": message.content,
                "ts": message.created_at.isoformat(),
                "metadata": message.metadata,
            },
            default=str,
        )
        client = self._client()
        if client is not None:
            client.lpush(key, payload)
            client.expire(key, 86400)
        else:
            self._memory_store.setdefault(key, []).insert(0, payload)

        if await self._working_count(key) >= self.COMPRESS_AT:
            await self._compress(message.tenant_id, message.customer_id, message.session_id)

    async def get_context(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        session_id: str,
        query: str = "",
    ) -> MemoryContext:
        """Build the fused three-tier memory context."""
        recent = await self._get_working_memory(tenant_id, customer_id, session_id)
        history = await self._search_episodic(tenant_id, customer_id, query or _last_content(recent))
        profile = await self._get_profile(tenant_id, customer_id)
        summary = self._get_summary(tenant_id, customer_id, session_id)
        return MemoryContext(
            recent_messages=recent,
            relevant_history=history,
            user_profile=profile,
            summary=summary,
        )

    async def update_profile(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        session_id: str,
        profile_data: dict[str, Any],
    ) -> None:
        """Persist a distilled user profile for cross-system sharing."""
        doc_id = f"{tenant_id}:{customer_id}:profile"
        await store_document(
            tenant_id=tenant_id,
            collection="user_profile",
            doc_id=doc_id,
            text=json.dumps(profile_data, default=str),
            metadata={"customer_id": customer_id, "session_id": session_id},
        )
        key = self._profile_key(tenant_id, customer_id)
        client = self._client()
        if client is not None:
            client.setex(key, 86400 * 30, json.dumps(profile_data, default=str))
        else:
            self._memory_store[key] = [json.dumps(profile_data, default=str)]

    async def _compress(self, tenant_id: str, customer_id: str, session_id: str) -> None:
        messages = await self._get_working_memory(tenant_id, customer_id, session_id)
        if len(messages) < self.COMPRESS_AT:
            return

        to_compress = messages[:-5]
        keep = messages[-5:]
        summary = " | ".join(f"{msg.role.value}: {msg.content[:120]}" for msg in to_compress)
        summary_key = self._summary_key(tenant_id, customer_id, session_id)
        old_summary = self._get_summary(tenant_id, customer_id, session_id)
        new_summary = f"{old_summary}\n{summary}".strip()
        client = self._client()
        if client is not None:
            client.setex(summary_key, 86400, new_summary)
        else:
            self._memory_store[summary_key] = [new_summary]

        await store_document(
            tenant_id=tenant_id,
            collection="episodic",
            doc_id=f"{tenant_id}:{customer_id}:{session_id}:{_utcnow().timestamp()}",
            text=summary,
            metadata={"customer_id": customer_id, "session_id": session_id},
        )

        key = self._wm_key(tenant_id, customer_id, session_id)
        if client is not None:
            client.delete(key)
            for message in reversed(keep):
                client.lpush(
                    key,
                    json.dumps(
                        {
                            "role": message.role.value,
                            "content": message.content,
                            "ts": message.created_at.isoformat(),
                            "metadata": message.metadata,
                        },
                        default=str,
                    ),
                )
            client.expire(key, 86400)
        else:
            self._memory_store[key] = [
                json.dumps(
                    {
                        "role": message.role.value,
                        "content": message.content,
                        "ts": message.created_at.isoformat(),
                        "metadata": message.metadata,
                    },
                    default=str,
                )
                for message in reversed(keep)
            ]

    async def _get_working_memory(
        self,
        tenant_id: str,
        customer_id: str,
        session_id: str,
    ) -> list[ChatMessage]:
        key = self._wm_key(tenant_id, customer_id, session_id)
        client = self._client()
        if client is not None:
            raws = client.lrange(key, 0, self.WORKING_MAX - 1)
        else:
            raws = self._memory_store.get(key, [])[: self.WORKING_MAX]

        messages: list[ChatMessage] = []
        for raw in reversed(raws):
            data = json.loads(raw)
            messages.append(
                ChatMessage(
                    tenant_id=tenant_id,
                    customer_id=customer_id,
                    session_id=session_id,
                    role=ChatMessageRole(data["role"]),
                    content=data["content"],
                    metadata=data.get("metadata", {}),
                    created_at=datetime.fromisoformat(data["ts"]),
                )
            )
        return messages

    async def _search_episodic(self, tenant_id: str, customer_id: str, query: str) -> list[str]:
        if not query.strip():
            return []
        docs = await retrieve_documents(
            tenant_id=tenant_id,
            query=query,
            collection="episodic",
            limit=self.HISTORY_TOP_K,
            metadata_filter={"customer_id": customer_id},
        )
        return [str(doc.get("text", doc)) for doc in docs if doc]

    async def _get_profile(self, tenant_id: str, customer_id: str) -> dict[str, Any]:
        key = self._profile_key(tenant_id, customer_id)
        client = self._client()
        raw: str | None = None
        if client is not None:
            raw = client.get(key)
        elif key in self._memory_store:
            raw = self._memory_store[key][0]
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
        docs = await retrieve_documents(
            tenant_id=tenant_id,
            query=customer_id,
            collection="user_profile",
            limit=1,
            metadata_filter={"customer_id": customer_id},
        )
        if docs:
            try:
                return json.loads(str(docs[0].get("text", "{}")))
            except json.JSONDecodeError:
                return {}
        return {}

    def _get_summary(self, tenant_id: str, customer_id: str, session_id: str) -> str:
        key = self._summary_key(tenant_id, customer_id, session_id)
        client = self._client()
        if client is not None:
            return client.get(key) or ""
        values = self._memory_store.get(key, [])
        return values[0] if values else ""

    async def _working_count(self, key: str) -> int:
        client = self._client()
        if client is not None:
            return int(client.llen(key))
        return len(self._memory_store.get(key, []))

    @staticmethod
    def _wm_key(tenant_id: str, customer_id: str, session_id: str) -> str:
        return f"wm:{tenant_id}:{customer_id}:{session_id}"

    @staticmethod
    def _summary_key(tenant_id: str, customer_id: str, session_id: str) -> str:
        return f"summary:{tenant_id}:{customer_id}:{session_id}"

    @staticmethod
    def _profile_key(tenant_id: str, customer_id: str) -> str:
        return f"profile:{tenant_id}:{customer_id}"


_MEMORY: ConversationMemory | None = None


def get_conversation_memory() -> ConversationMemory:
    """Return the process-wide conversation memory manager."""
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = ConversationMemory()
    return _MEMORY


def _last_content(messages: list[ChatMessage]) -> str:
    if not messages:
        return ""
    return messages[-1].content


__all__ = ["ConversationMemory", "MemoryContext", "get_conversation_memory"]
