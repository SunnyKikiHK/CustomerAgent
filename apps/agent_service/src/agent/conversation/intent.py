"""Three-way fused intent recognition for conversation turns."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from packages.knowledge_service.src.embed import local_embedding

from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage


class IntentCategory(str, Enum):
    QUERY = "query"
    COMPLAINT = "complaint"
    REQUEST = "request"
    GREETING = "greeting"
    ESCALATION = "escalation"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT = "account"
    FEEDBACK = "feedback"
    OTHER = "other"


class UrgencyLevel(int, Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class IntentResult:
    intent: IntentCategory
    confidence: float
    urgency: UrgencyLevel
    entities: dict[str, list[str]]
    reasoning: str
    latency_ms: float = 0.0


_TEMPLATES: dict[IntentCategory, list[str]] = {
    IntentCategory.QUERY: ["What is my order status?", "How do I reset my password?"],
    IntentCategory.COMPLAINT: ["I have waited for hours", "The service is terrible"],
    IntentCategory.REQUEST: ["Please cancel my order", "I need a refund"],
    IntentCategory.GREETING: ["Hello", "Hi there"],
    IntentCategory.ESCALATION: ["I want to speak to a manager", "Transfer me to a human"],
    IntentCategory.TECHNICAL: ["The app keeps crashing", "I cannot log in"],
    IntentCategory.BILLING: ["Why was I charged twice?", "I need an invoice"],
    IntentCategory.ACCOUNT: ["Update my email", "Reset my password"],
    IntentCategory.FEEDBACK: ["Great service", "Very helpful support"],
}

_URGENCY_KEYWORDS = {
    UrgencyLevel.CRITICAL: ["urgent", "emergency", "asap", "immediately"],
    UrgencyLevel.HIGH: ["today", "now", "right away"],
    UrgencyLevel.MEDIUM: ["this week", "soon"],
}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class IntentRecognizer:
    """Fuse LLM, embedding, and keyword strategies for one chat turn."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        model: str = "deepseek/deepseek-v4-flash",
        confidence_threshold: float = 0.5,
        embedding_enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client or LLMClient(default_model=model)
        self.model = model
        self.threshold = confidence_threshold
        self.embedding_enabled = embedding_enabled
        self._template_embeddings: dict[IntentCategory, list[list[float]]] = {}
        self._cache: dict[str, IntentResult] = {}

    async def recognize(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> IntentResult:
        key = message.strip().lower()[:200]
        if key in self._cache:
            return self._cache[key]

        llm_task = asyncio.create_task(self._llm_recognize(message, history))
        emb_task = (
            asyncio.create_task(self._embedding_recognize(message))
            if self.embedding_enabled
            else None
        )
        pattern = self._pattern_recognize(message)

        if emb_task:
            llm, emb = await asyncio.gather(llm_task, emb_task)
        else:
            llm = await llm_task
            emb = {"intent": IntentCategory.OTHER, "confidence": 0.0}

        intent = self._vote(llm, emb, pattern)
        entities = await self._extract_entities(message)
        urgency = self._urgency(message, intent)
        result = IntentResult(
            intent=intent,
            confidence=float(llm.get("confidence", 0.0)),
            urgency=urgency,
            entities=entities,
            reasoning=str(llm.get("reasoning", "")),
        )
        self._cache[key] = result
        return result

    async def _llm_recognize(
        self,
        message: str,
        history: list[dict[str, str]] | None,
    ) -> dict[str, Any]:
        examples = "\n".join(
            f'  message: "{template}" -> intent: {category.value}'
            for category, templates in _TEMPLATES.items()
            for template in templates[:1]
        )
        history_text = ""
        if history:
            history_text = "\n".join(
                f"  {item.get('role', 'user')}: {item.get('content', '')}"
                for item in history[-3:]
            )
        prompt = (
            "Classify the customer message intent. Return JSON only.\n"
            f"Examples:\n{examples}\n"
            f"History:\n{history_text}\n"
            f'Message: "{message}"\n'
            f'Format: {{"intent": "<intent>", "confidence": 0.0, "reasoning": "..."}}'
        )
        try:
            response = await self.llm_client.complete(
                [LLMMessage(role="user", content=prompt)],
                model=self.model,
                temperature=0.1,
                max_tokens=256,
                name="conversation.intent.llm",
            )
            raw = response.text
            start, end = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[start:end])
            data["intent"] = IntentCategory(data.get("intent", IntentCategory.OTHER.value))
            return data
        except Exception:
            return {
                "intent": IntentCategory.OTHER,
                "confidence": 0.0,
                "reasoning": "LLM intent classification failed",
                "failed": True,
            }

    async def _embedding_recognize(self, message: str) -> dict[str, Any]:
        await self._ensure_template_embeddings()
        msg_vec = local_embedding(message)
        best_cat = IntentCategory.OTHER
        best_score = 0.0
        for category, vectors in self._template_embeddings.items():
            score = max(_cosine(msg_vec, vector) for vector in vectors)
            if score > best_score:
                best_score = score
                best_cat = category
        return {"intent": best_cat, "confidence": best_score}

    def _pattern_recognize(self, message: str) -> dict[str, Any]:
        lowered = message.lower()
        patterns = {
            IntentCategory.ESCALATION: ["manager", "human", "supervisor"],
            IntentCategory.COMPLAINT: ["terrible", "awful", "frustrated"],
            IntentCategory.QUERY: ["?", "what", "how", "status"],
            IntentCategory.REQUEST: ["please", "help me", "need"],
            IntentCategory.GREETING: ["hello", "hi", "hey"],
            IntentCategory.BILLING: ["refund", "invoice", "charge"],
            IntentCategory.TECHNICAL: ["error", "crash", "bug"],
            IntentCategory.ACCOUNT: ["password", "email", "account"],
        }
        best_cat = IntentCategory.OTHER
        best_score = 0.0
        for category, keywords in patterns.items():
            hits = sum(1 for keyword in keywords if keyword in lowered)
            if hits:
                score = hits / len(keywords)
                if score > best_score:
                    best_score = score
                    best_cat = category
        return {"intent": best_cat, "confidence": best_score}

    def _vote(self, llm: dict[str, Any], emb: dict[str, Any], pattern: dict[str, Any]) -> IntentCategory:
        if llm.get("failed"):
            if emb.get("intent") != IntentCategory.OTHER:
                return emb["intent"]
            if pattern.get("intent") != IntentCategory.OTHER:
                return pattern["intent"]
            return IntentCategory.OTHER

        weights = (
            [(llm, 0.7), (emb, 0.2), (pattern, 0.1)]
            if self.embedding_enabled
            else [(llm, 0.85), (pattern, 0.15)]
        )
        scores: dict[IntentCategory, float] = {}
        for result, weight in weights:
            category = result.get("intent", IntentCategory.OTHER)
            confidence = float(result.get("confidence", 0.0))
            scores[category] = scores.get(category, 0.0) + weight * confidence
        best = max(scores, key=scores.get)
        return best if scores[best] >= self.threshold else IntentCategory.OTHER

    async def _extract_entities(self, message: str) -> dict[str, list[str]]:
        prompt = (
            'Extract entities from the customer message as JSON lists: '
            '{"order_id":[],"product":[],"date":[],"amount":[],"error_code":[]}\n'
            f'Message: "{message}"'
        )
        try:
            response = await self.llm_client.complete(
                [LLMMessage(role="user", content=prompt)],
                model=self.model,
                temperature=0.0,
                max_tokens=256,
                name="conversation.intent.entities",
            )
            raw = response.text
            start, end = raw.find("{"), raw.rfind("}") + 1
            return json.loads(raw[start:end])
        except Exception:
            return {
                "order_id": [],
                "product": [],
                "date": [],
                "amount": [],
                "error_code": [],
            }

    async def _ensure_template_embeddings(self) -> None:
        missing = [category for category in _TEMPLATES if category not in self._template_embeddings]
        if not missing:
            return
        for category in missing:
            self._template_embeddings[category] = [
                local_embedding(template) for template in _TEMPLATES[category]
            ]

    def _urgency(self, message: str, intent: IntentCategory) -> UrgencyLevel:
        lowered = message.lower()
        for level, keywords in _URGENCY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return level
        if intent == IntentCategory.ESCALATION:
            return UrgencyLevel.HIGH
        if intent == IntentCategory.COMPLAINT:
            return UrgencyLevel.MEDIUM
        return UrgencyLevel.LOW


_RECOGNIZER: IntentRecognizer | None = None


def get_intent_recognizer() -> IntentRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        _RECOGNIZER = IntentRecognizer(embedding_enabled=True)
    return _RECOGNIZER


__all__ = [
    "IntentCategory",
    "UrgencyLevel",
    "IntentResult",
    "IntentRecognizer",
    "get_intent_recognizer",
]
