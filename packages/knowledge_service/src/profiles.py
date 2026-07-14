"""Relational customer-profile persistence (Postgres `customer_profiles`).

The conversation system distills a durable profile from chat and upserts it
here so the signal system's health analysis can read chat-learned preferences,
sentiment, and risk signals. This is the structured, queryable companion to the
pgvector ``user_profile`` collection.
"""

from __future__ import annotations

import json
from typing import Any

from packages.db.src import PostgresConfigError, execute, fetch_one

_LIST_FIELDS = (
    "preferences",
    "sentiment_signals",
    "risk_signals",
    "communication_preferences",
)


async def upsert_customer_profile(
    *,
    tenant_id: str,
    customer_id: str,
    profile: dict[str, Any],
    last_intent: str | None = None,
    last_sentiment: str | None = None,
) -> bool:
    """Insert or update the structured profile row for a customer.

    ``customer_id`` must be a UUID that exists in ``customers`` (the FK is
    enforced). Returns True on success, False when the DB is unavailable or the
    customer row does not exist yet (both are non-fatal for a chat turn).
    """
    entities = profile.get("entities", {})
    try:
        status = await execute(
            """
            insert into customer_profiles (
                tenant_id, customer_id, preferences, sentiment_signals,
                risk_signals, communication_preferences, entities,
                last_intent, last_sentiment, updated_at
            )
            values ($1::uuid, $2::uuid, $3::jsonb, $4::jsonb, $5::jsonb,
                    $6::jsonb, $7::jsonb, $8, $9, now())
            on conflict (tenant_id, customer_id) do update set
                preferences = excluded.preferences,
                sentiment_signals = excluded.sentiment_signals,
                risk_signals = excluded.risk_signals,
                communication_preferences = excluded.communication_preferences,
                entities = excluded.entities,
                last_intent = coalesce(excluded.last_intent, customer_profiles.last_intent),
                last_sentiment = coalesce(excluded.last_sentiment, customer_profiles.last_sentiment),
                updated_at = now()
            """,
            tenant_id,
            customer_id,
            profile.get("preferences", []),
            profile.get("sentiment_signals", []),
            profile.get("risk_signals", []),
            profile.get("communication_preferences", []),
            entities if isinstance(entities, dict) else {},
            last_intent,
            last_sentiment,
            tenant_id=tenant_id,
        )
        return status.startswith("INSERT") or status.startswith("UPDATE")
    except PostgresConfigError:
        return False
    except Exception:
        # A missing customer row (invalid UUID / not seeded) must not break chat.
        return False


async def get_customer_profile(
    *, tenant_id: str, customer_id: str
) -> dict[str, Any] | None:
    """Return the structured profile row for a customer, or None."""
    try:
        row = await fetch_one(
            """
            select preferences, sentiment_signals, risk_signals,
                   communication_preferences, entities, last_intent,
                   last_sentiment, updated_at
            from customer_profiles
            where customer_id = $1::uuid
            """,
            customer_id,
            tenant_id=tenant_id,
        )
    except PostgresConfigError:
        return None
    except Exception:
        return None
    if row is None:
        return None
    return {
        "preferences": _as_list(row["preferences"]),
        "sentiment_signals": _as_list(row["sentiment_signals"]),
        "risk_signals": _as_list(row["risk_signals"]),
        "communication_preferences": _as_list(row["communication_preferences"]),
        "entities": row["entities"] if isinstance(row["entities"], dict) else {},
        "last_intent": row["last_intent"],
        "last_sentiment": row["last_sentiment"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


__all__ = ["upsert_customer_profile", "get_customer_profile"]
