"""
Environment validation for the local CustomerAgent stack.

Checks PostgreSQL, Redis, optional OpenRouter access, and optional LLM audit logging.
"""

import json
import os
import time
from typing import Any
from uuid import UUID

import psycopg
import redis
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "deepseek/deepseek-v4-flash",
)

TENANT_NAME = os.getenv("TENANT_NAME", "Demo Tenant")
TENANT_CACHE_SLUG = os.getenv("TENANT_CACHE_SLUG", "demo-tenant")
SESSION_ID = f"session-{int(time.time())}"

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5433"))
PG_DB = os.getenv("PG_DB", "agent")
PG_USER = os.getenv("PG_USER", "sunny")
PG_PASS = os.getenv("PG_PASS", "sunny")


def get_pg_connection() -> psycopg.Connection[Any]:
    """Create a PostgreSQL connection using local stack defaults."""
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
    )


def step1_query_tenants() -> UUID | None:
    """Query the tenants table and create a demo tenant when none exists."""
    print("--- Step 1: Query PostgreSQL tenants table ---")
    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tenants (name)
                    SELECT %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM tenants WHERE name = %s
                    )
                    RETURNING id;
                    """,
                    (TENANT_NAME, TENANT_NAME),
                )
                inserted = cursor.fetchone()

                if inserted:
                    tenant_id = inserted[0]
                    print(f"Created demo tenant: {tenant_id} - {TENANT_NAME}")
                else:
                    cursor.execute(
                        "SELECT id FROM tenants WHERE name = %s LIMIT 1;",
                        (TENANT_NAME,),
                    )
                    tenant_id = cursor.fetchone()[0]
                    print(f"Demo tenant already exists: {tenant_id} - {TENANT_NAME}")

                cursor.execute("SELECT id, name, plan FROM tenants ORDER BY created_at;")
                rows = cursor.fetchall()

                print(f"Queried {len(rows)} tenant(s):")
                for row in rows:
                    print(f"  - {row[0]}: {row[1]} ({row[2]})")

                return tenant_id

    except Exception as exc:
        print(f"PostgreSQL query failed: {exc}")
        return None


def step2_redis_operations() -> bool:
    """Connect to Redis and write/read sample session data."""
    print("\n--- Step 2: Redis operations ---")
    try:
        redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
        )
        redis_client.ping()
        print("Redis connection succeeded")

        session_key = f"{TENANT_CACHE_SLUG}:session:{SESSION_ID}"
        session_data = {
            "user_id": "user-001",
            "created_at": int(time.time()),
            "messages": [],
        }
        redis_client.setex(session_key, 3600, json.dumps(session_data))
        print(f"Wrote session data: {session_key}")

        circuit_key = f"{TENANT_CACHE_SLUG}:circuit-breaker:openrouter-api"
        redis_client.setex(circuit_key, 60, "0")
        print(f"Wrote circuit breaker state: {circuit_key}")

        saved_session_raw = redis_client.get(session_key)
        if saved_session_raw is None:
            raise RuntimeError("Session key was not found after write")

        saved_session = json.loads(saved_session_raw)
        print(f"Verified session read: user_id={saved_session['user_id']}")

        return True

    except Exception as exc:
        print(f"Redis operation failed: {exc}")
        return False


def step3_call_openrouter(
    user_message: str = "Hello! Please introduce yourself.",
) -> dict[str, Any] | None:
    """Call the OpenRouter API when OPENROUTER_API_KEY is configured."""
    print("\n--- Step 3: Call OpenRouter API ---")

    if not OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY is not set; skipping OpenRouter call")
        return None

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )

        response = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": user_message}],
        )

        choice = response.choices[0]
        assistant_message = choice.message.content or ""
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        print("OpenRouter API call succeeded")
        print(f"  Model: {OPENROUTER_MODEL}")
        print(f"  User message: {user_message}")
        print(f"  AI reply: {assistant_message[:100]}...")

        return {
            "model": OPENROUTER_MODEL,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    except Exception as exc:
        print(f"OpenRouter API call failed: {exc}")
        return None


def step4_write_audit_log(tenant_id: UUID, conversation: dict[str, Any] | None) -> bool:
    """Write an LLM audit record using the schema from init.sql."""
    print("\n--- Step 4: Write audit log to PostgreSQL ---")

    if conversation is None:
        print("No conversation data to write; skipping audit log")
        return True

    try:
        with get_pg_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO llm_audit_logs (
                        tenant_id,
                        model,
                        prompt_tokens,
                        completion_tokens,
                        status,
                        request_body,
                        response_body
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        tenant_id,
                        conversation["model"],
                        conversation["input_tokens"],
                        conversation["output_tokens"],
                        "success",
                        json.dumps({"message": conversation["user_message"]}),
                        json.dumps({"message": conversation["assistant_message"]}),
                    ),
                )

                cursor.execute(
                    "SELECT COUNT(*) FROM llm_audit_logs WHERE tenant_id = %s;",
                    (tenant_id,),
                )
                count = cursor.fetchone()[0]

        print(f"Audit log written successfully for tenant: {tenant_id}")
        print(f"  Current tenant audit log total: {count}")
        return True

    except Exception as exc:
        print(f"Audit log write failed: {exc}")
        return False


def main() -> None:
    """Run the full local environment validation flow."""
    print("- " * 30)
    print("Hello Agent - Python Version")
    print("Environment: PostgreSQL + Redis + OpenRouter API + Audit Log")
    print("- " * 30)

    results: list[tuple[str, bool]] = []

    tenant_id = step1_query_tenants()
    results.append(("PostgreSQL query", tenant_id is not None))
    results.append(("Redis operations", step2_redis_operations()))

    conversation = step3_call_openrouter()
    if OPENROUTER_API_KEY:
        results.append(("OpenRouter API call", conversation is not None))
    else:
        results.append(("OpenRouter API call", True))

    if tenant_id is not None:
        results.append(("Audit log write", step4_write_audit_log(tenant_id, conversation)))
    else:
        results.append(("Audit log write", False))

    print("\n" + "- " * 30)
    print("Validation Results Summary")
    print("- " * 30)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            all_passed = False

    print("\n" + "- " * 30)
    if all_passed:
        print("All validations passed. Environment is correctly configured.")
    else:
        print("Some validations failed. Please review the error messages above.")
    print("- " * 30)


if __name__ == "__main__":
    main()
