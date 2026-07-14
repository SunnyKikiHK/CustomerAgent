"""
Three-tier conversation memory and shared user profile storage.

1. newest KEEP_RECENT_MESSAGES = 5 messages as raw chat history.
2. older messages are compressed into an LLM summary.
3.1. The generated summary is appended to the prior session summary and 
    kept for 24 hours.
3.2 The generated summary is stored to the episodic collection, 
    so later semantic retrieval can find relevant prior conversation segments.
4. Only the latest five turns remain raw after compression.

Note: If the LLM call errors or returns no content, the system does not fail the conversation. It falls back to a bounded plain-text representation
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage
from packages.agent.src.chat_types import ChatMessage, ChatMessageRole
from packages.knowledge_service.src.profiles import upsert_customer_profile
from packages.knowledge_service.src.retrieve import retrieve_documents, store_document
from packages.redis.src import RedisConfigError, get_client


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
        if _has_profile_content(self.user_profile):
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
        if not _has_profile_content(self.user_profile):
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
    KEEP_RECENT_MESSAGES = 5
    PROFILE_SOURCE_MESSAGES = 10
    PROFILE_LIST_FIELDS = (
        "preferences",
        "risk_signals",
        "sentiment_signals",
        "adoption_barriers",
        "communication_preferences",
    )
    PROFILE_ENTITY_FIELDS = ("issue_types", "stakeholders")
    EMPTY_PROFILE = {
        "preferences": [],
        "risk_signals": [],
        "sentiment_signals": [],
        "adoption_barriers": [],
        "communication_preferences": [],
        "entities": {"issue_types": [], "stakeholders": []},
    }

    def __init__(self, redis_url: str | None = None, llm_client: LLMClient | None = None) -> None:
        _ = redis_url
        self._redis: Any | None = None
        self._llm: LLMClient | None = llm_client
        self._memory_store: dict[str, list[str]] = {}

    def _client(self) -> Any | None:
        if self._redis is not None:
            return self._redis
        try:
            self._redis = get_client()
            return self._redis
        except RedisConfigError:
            return None
        except Exception:
            self._redis = None
            return None

    async def add_message(self, message: ChatMessage) -> None:
        """Append a message to working memory and compress when needed."""
        key = self._wm_key(message.tenant_id, message.customer_id, message.session_id)
        payload = self._serialize_message(message)
        client = self._client()
        if client is not None:
            client.lpush(key, payload)
            client.expire(key, 86400)
        else:
            self._memory_store.setdefault(key, []).insert(0, payload)

        if await self._working_count(key) >= self.COMPRESS_AT:
            await self._compress(message.tenant_id, message.customer_id, message.session_id)

    async def get_recent_messages(
        self,
        *,
        tenant_id: str,
        customer_id: str,
        limit: int = 5,
    ) -> list[ChatMessage]:
        """Return the newest messages across this customer's active sessions."""
        bounded_limit = max(1, min(limit, 5))
        prefix = self._wm_key(tenant_id, customer_id, "")
        client = self._client()
        if client is not None:
            keys = list(client.scan_iter(f"{prefix}*"))
            stored = [
                (key, raw)
                for key in keys
                for raw in client.lrange(key, 0, self.WORKING_MAX - 1)
            ]
        else:
            stored = [
                (key, raw)
                for key, raws in self._memory_store.items()
                if key.startswith(prefix)
                for raw in raws[: self.WORKING_MAX]
            ]

        messages: list[ChatMessage] = []
        for key, raw in stored:
            data = json.loads(raw)
            session_id = str(key).removeprefix(prefix)
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
        messages.sort(key=lambda message: message.created_at)
        return messages[-bounded_limit:]

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
        history = await self._search_episodic(
            tenant_id,
            customer_id,
            query or _last_content(recent),
        )
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
        profile_data: dict[str, Any] | None = None,
    ) -> None:
        """Persist an LLM-distilled user profile for cross-system sharing."""
        messages = await self._get_working_memory(tenant_id, customer_id, session_id)
        if not messages and not profile_data:
            return

        existing_profile = await self._get_profile(tenant_id, customer_id)
        generated = await self._extract_profile(messages, existing_profile)
        merged = self._merge_profile(existing_profile, generated)
        merged = self._merge_profile(merged, profile_data or {})
        raw_profile = json.dumps(merged, default=str)
        await store_document(
            tenant_id=tenant_id,
            collection="user_profile",
            doc_id=f"{tenant_id}:{customer_id}:profile",
            text=raw_profile,
            metadata={"customer_id": customer_id, "session_id": session_id},
        )
        key = self._profile_key(tenant_id, customer_id)
        client = self._client()
        if client is not None:
            client.setex(key, 86400 * 30, raw_profile)
        else:
            self._memory_store[key] = [raw_profile]

        # Also persist the structured, queryable profile row so the signal
        # system's health analysis can read chat-learned signals. Best-effort:
        # a missing/unseeded customer row must not break the chat turn.
        await upsert_customer_profile(
            tenant_id=tenant_id,
            customer_id=customer_id,
            profile=merged,
            last_intent=(profile_data or {}).get("last_intent"),
            last_sentiment=(profile_data or {}).get("last_sentiment"),
        )

    async def _compress(self, tenant_id: str, customer_id: str, session_id: str) -> None:
        messages = await self._get_working_memory(tenant_id, customer_id, session_id)
        if len(messages) < self.COMPRESS_AT:
            return

        to_compress = messages[:-self.KEEP_RECENT_MESSAGES]
        keep = messages[-self.KEEP_RECENT_MESSAGES :]
        source_text = self._messages_to_text(to_compress)
        summary = await self._generate_summary(to_compress)
        summary_key = self._summary_key(tenant_id, customer_id, session_id)
        merged_summary = self._append_summary(
            self._get_summary(tenant_id, customer_id, session_id),
            summary,
        )
        client = self._client()
        if client is not None:
            client.setex(summary_key, 86400, merged_summary)
        else:
            self._memory_store[summary_key] = [merged_summary]

        await store_document(
            tenant_id=tenant_id,
            collection="episodic",
            doc_id=f"{tenant_id}:{customer_id}:{session_id}:{_utcnow().timestamp()}",
            text=summary,
            metadata={
                "customer_id": customer_id,
                "session_id": session_id,
                "full_text_excerpt": source_text[:1000],
            },
        )

        key = self._wm_key(tenant_id, customer_id, session_id)
        if client is not None:
            client.delete(key)
            for message in reversed(keep):
                client.lpush(key, self._serialize_message(message))
            client.expire(key, 86400)
        else:
            self._memory_store[key] = [
                self._serialize_message(message) for message in reversed(keep)
            ]

    async def _generate_summary(self, messages: list[ChatMessage]) -> str:
        if not messages:
            return ""
        prompt = (
            "Summarize the following customer support conversation in English. "
            "Write 2-3 concise sentences that preserve the user's goal, key issues, "
            "constraints, and any commitments already made.\n\n"
            f"Conversation:\n{self._messages_to_text(messages)}"
        )
        try:
            llm = self._llm or LLMClient()
            response = await llm.complete(
                [LLMMessage(role="user", content=prompt)],
                temperature=0.0,
                max_tokens=220,
                name="memory.compress.summary",
                metadata={"component": "conversation_memory", "kind": "summary"},
            )
            if response.text.strip():
                return response.text.strip()
        except Exception:
            pass
        return self._fallback_summary(messages)

    async def _extract_profile(
        self,
        messages: list[ChatMessage],
        existing_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_messages = messages[-self.PROFILE_SOURCE_MESSAGES :]
        if not source_messages:
            return dict(self.EMPTY_PROFILE)

        normalized_existing_profile = self._normalize_profile(
            existing_profile or self.EMPTY_PROFILE
        )
        prompt = (
            "You are extracting durable customer-success profile signals from a conversation. "
            "Use the existing profile as already-known memory. Do not repeat items that already "
            "exist there unless the conversation clearly updates or corrects them. "
            "Return only net-new or updated profile information supported by the latest "
            "conversation. Return valid JSON only with this exact shape: "
            "{\"preferences\": [\"...\"], \"risk_signals\": [\"...\"], "
            "\"sentiment_signals\": [\"...\"], \"adoption_barriers\": [\"...\"], "
            "\"communication_preferences\": [\"...\"], \"entities\": {\"issue_types\": [], "
            "\"stakeholders\": []}}. "
            "Use short English phrases. Focus on the person and their customer-success risk "
            "state, not product catalog data. Use empty lists when there is nothing new to add.\n\n"
            f"Existing profile:\n{json.dumps(normalized_existing_profile, default=str)}\n\n"
            f"Latest conversation:\n{self._messages_to_text(source_messages)}"
        )
        try:
            llm = self._llm or LLMClient()
            response = await llm.complete(
                [LLMMessage(role="user", content=prompt)],
                temperature=0.0,
                max_tokens=260,
                name="memory.profile.extract",
                metadata={"component": "conversation_memory", "kind": "profile"},
            )
            return self._normalize_profile(self._parse_json_object(response.text))
        except Exception:
            return dict(self.EMPTY_PROFILE)

    async def _get_working_memory(
        self,
        tenant_id: str,
        customer_id: str,
        session_id: str,
    ) -> list[ChatMessage]:
        key = self._wm_key(tenant_id, customer_id, session_id)
        client = self._client()
        raws = (
            client.lrange(key, 0, self.WORKING_MAX - 1)
            if client is not None
            else self._memory_store.get(key, [])[: self.WORKING_MAX]
        )

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
        try:
            docs = await retrieve_documents(
                tenant_id=tenant_id,
                query=query,
                collection="episodic",
                limit=self.HISTORY_TOP_K,
                metadata_filter={"customer_id": customer_id},
            )
        except Exception:
            return []
        return [str(doc.get("text", doc)) for doc in docs if doc]

    async def _get_profile(self, tenant_id: str, customer_id: str) -> dict[str, Any]:
        key = self._profile_key(tenant_id, customer_id)
        client = self._client()
        raw: str | None = client.get(key) if client is not None else None
        if raw is None and key in self._memory_store:
            raw = self._memory_store[key][0]
        if raw:
            try:
                return self._normalize_profile(json.loads(raw))
            except json.JSONDecodeError:
                return dict(self.EMPTY_PROFILE)

        try:
            docs = await retrieve_documents(
                tenant_id=tenant_id,
                query=customer_id,
                collection="user_profile",
                limit=1,
                metadata_filter={"customer_id": customer_id},
            )
        except Exception:
            return dict(self.EMPTY_PROFILE)
        if docs:
            try:
                profile_text = str(docs[0].get("text", "{}"))
                return self._normalize_profile(json.loads(profile_text))
            except json.JSONDecodeError:
                return dict(self.EMPTY_PROFILE)
        return dict(self.EMPTY_PROFILE)

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
    def _serialize_message(message: ChatMessage) -> str:
        return json.dumps(
            {
                "role": message.role.value,
                "content": message.content,
                "ts": message.created_at.isoformat(),
                "metadata": message.metadata,
            },
            default=str,
        )

    @staticmethod
    def _messages_to_text(messages: list[ChatMessage]) -> str:
        return "\n".join(f"{message.role.value}: {message.content}" for message in messages)

    @staticmethod
    def _append_summary(old_summary: str, new_summary: str) -> str:
        if not old_summary.strip():
            return new_summary.strip()
        if not new_summary.strip():
            return old_summary.strip()
        return f"{old_summary.strip()}\n{new_summary.strip()}"

    @staticmethod
    def _fallback_summary(messages: list[ChatMessage]) -> str:
        parts = [f"{message.role.value}: {message.content[:160]}" for message in messages[:6]]
        if len(messages) > 6:
            parts.append(f"... {len(messages) - 6} more messages omitted")
        return " ".join(parts).strip()

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        stripped = text.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        loaded = json.loads(stripped[start : end + 1])
        return loaded if isinstance(loaded, dict) else {}

    @classmethod
    def _normalize_profile(cls, profile: dict[str, Any]) -> dict[str, Any]:
        normalized_profile: dict[str, Any] = {
            field: cls._normalize_string_list(profile.get(field, []))
            for field in cls.PROFILE_LIST_FIELDS
        }
        entities = profile.get("entities", {})
        normalized_entities: dict[str, list[str]] = {
            field: [] for field in cls.PROFILE_ENTITY_FIELDS
        }
        if isinstance(entities, dict):
            for field in cls.PROFILE_ENTITY_FIELDS:
                normalized_entities[field] = cls._normalize_string_list(entities.get(field, []))
        normalized_profile["entities"] = normalized_entities
        return normalized_profile

    @classmethod
    def _merge_profile(cls, generated: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        merged = cls._normalize_profile(generated)
        extra_profile = cls._normalize_profile(
            {
                **{field: extra.get(field, []) for field in cls.PROFILE_LIST_FIELDS},
                "entities": extra.get("entities", {}),
            }
        )
        for field in cls.PROFILE_LIST_FIELDS:
            merged[field] = cls._dedupe([*merged[field], *extra_profile[field]])
        entities = dict(merged["entities"])
        for key, values in extra_profile["entities"].items():
            entities[key] = cls._dedupe([*entities.get(key, []), *values])
        if extra.get("last_intent"):
            entities["issue_types"] = cls._dedupe(
                [*entities.get("issue_types", []), str(extra["last_intent"])]
            )
        merged["entities"] = entities
        return merged

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if not isinstance(value, list):
            return []
        return ConversationMemory._dedupe(
            [str(item).strip() for item in value if str(item).strip()]
        )

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

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


def _has_profile_content(profile: dict[str, Any]) -> bool:
    """Return True only when the profile holds at least one non-empty value.

    A normalized profile is a dict of empty lists / nested empty lists until it
    is populated; rendering that as a ``[User profile]`` block is noise, so we
    skip it until there is real content.
    """
    if not profile:
        return False

    def _non_empty(value: Any) -> bool:
        if isinstance(value, dict):
            return any(_non_empty(item) for item in value.values())
        if isinstance(value, (list, tuple, set, str)):
            return len(value) > 0
        return value is not None

    return any(_non_empty(value) for value in profile.values())


__all__ = ["ConversationMemory", "MemoryContext", "get_conversation_memory"]
