# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository root

The actual project lives in `CustomerAgent/` (this directory). The outer `no_name/`
folder only holds `plan.md`, a `reference_project/`, and agent config. Run all
commands from the `CustomerAgent/` root — `PYTHONPATH` is anchored here (this is the
directory containing `config.sh`).

## System documentation

Two Chinese walkthroughs describe the two top-level systems:

- `note/conversation_system_zh.md` — 对话系统：当前架构（Planner→Executor→Reflector）
  的功能与架构，以及一套用于降低延迟的新架构（GeneralAgent / Orchestrator-Workers）。
- `note/signal_system_zh.md` — 信号系统：来源、检测器、NPS、QBR、邮件发送等全部功能，
  以及当前缺失 / 待完善之处。

Older English notes (`note/conversation_system.md`, `note/signal_system.mmd`) may drift
from the code; treat the code and this CLAUDE.md as authoritative.

## Environment & execution

- **All Python execution must go through WSL/Linux**, not the Windows interpreter.
  The `.venv` here is a Linux venv (Python 3.13.13); the Windows Python will not work.
- WSL path to root: `/mnt/d/OVERALL_NOTEBOOK/agent/no_name/CustomerAgent`
- Standard command prefix (activate venv, set PYTHONPATH, then run):

  ```bash
  cd /mnt/d/OVERALL_NOTEBOOK/agent/no_name/CustomerAgent \
    && source .venv/bin/activate \
    && export PYTHONPATH=$PWD \
    && <your command>
  ```

- `PYTHONPATH=$PWD` is **required** — imports are absolute from the repo root
  (`from packages...`, `from apps...`). There is no installed package; nothing works
  without it.

## Commands

Run tests (`tests/test_new_capabilities.py` + `tests/test_project_capabilities.py`):

```bash
python -m pytest tests/ -v
python -m pytest tests/ -q -k 'not postgres'   # offline only (skip live-backend tests)
python -m pytest tests/test_project_capabilities.py::test_factory_partitions_specialists_by_domain -v  # single test
```

Bring up the local infra stack (Postgres+pgvector, Redis, Temporal, Langfuse):

```bash
./start.sh          # sources config.sh, then docker compose up --wait + health probes
```

- `config.sh` exports all local env vars (DB, Redis, Temporal, OpenRouter keys, model
  names). `start.sh` sources it automatically. **Do not open, read, parse, or edit
  `config.sh`** — it is treated as restricted (it holds live keys). Reference
  `.env.example` instead when you need to know a variable name.
- Compose file: `infra/docker/docker-compose.yml`. Note Postgres is published on host
  port **5433** (5432 inside the container) to avoid clashing with a local Postgres.

Run the tool-gateway MCP server (external-action sandbox) standalone:

```bash
python -m apps.tool_gateway.src.index    # streamable-HTTP MCP on MCP_GATEWAY_PORT (8002)
```

Run the API gateway (FastAPI: chat + signals + dashboard endpoints) and the signal worker:

```bash
uvicorn apps.api_gateway.src.app:app --host 0.0.0.0 --port 8000   # API on :8000
python -m apps.agent_service.src.rq_worker                        # signal queue drain
```

Seed tenant playbooks into pgvector and run the live end-to-end verification:

```bash
python scripts/seed_playbooks.py    # ensures demo tenant + seeds skills/demo-tenant/playbooks/*.md
python scripts/verify_e2e.py        # RAG, query_health, chat, signal bridge, detector+worker
python scripts/smoke_live_api.py    # live OpenRouter smoke: intent + refund/escalation chat turns
```

`smoke_live_api.py` exercises real LLM calls (intent recognition, the LLM conversation
planner, subagents, and the compliance critic) against OpenRouter. It reports each check as
PASS/FAIL and distinguishes an auth/connectivity failure from a normal critic block, so it
doubles as an "is the API key + network working" probe.

Frontend (Vite + React chat + signal dashboard; built separately, not a Python module):

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies /api -> :8000
```

Full Docker stack (adds `api-gateway` on :8000 and `agent-worker` to the infra services):

```bash
docker compose -f infra/docker/docker-compose.yml --env-file infra/docker/.env up -d --build
```

The embedding change to 1536-dim requires a fresh DB volume the first time:
`docker compose ... down -v` then bring the stack up so `init.sql` reapplies.

## Test-suite structure

Tests split into two tiers:
- **Unit tests** run offline against in-memory fakes (`_FakeLLM`, `InMemoryIdempotencyStore`).
- **Live-backend tests** (`test_postgres_*`) call `_require_live_backends()` and
  **skip automatically** if Postgres/Redis are not up. Bring up `./start.sh` first if you
  want them to actually run.

## Architecture

A B2B customer-success automation platform. Two independently-deployed process types
(`apps/`) share business logic through libraries (`packages/`). **Apps never import each
other — only through `packages/`.** See `README.md` for the full narrative; the key
runtime facts:

### Two orchestrators, one shared runtime

All agent work enters `apps/agent_service` through one of two top-level orchestrators,
both subclassing `BaseOrchestrator` (`apps/agent_service/src/agent/orchestrator/base.py`),
which implements a shared **Planner → Executor → Reflector** lifecycle in `BaseOrchestrator.run()`:

| Orchestrator | Input | `supports_external_writes` | Path |
|---|---|---|---|
| `SignalOrchestrator` (`agent/signal/`) | `SignalAgentInput` / `CustomerSignal` | `True` — can send email/Slack | proactive backend automation |
| `ConversationOrchestrator` (`agent/conversation/`) | `ConversationAgentInput` / `ChatMessage` | `False` — chat only, no external writes | customer chat turns |

Subclasses supply only the domain-specific hooks (`load_config`, `build_plan`,
`load_memory_excerpt`, `on_approved`). The shared lifecycle — delegation, compliance
review, decision finalization, response assembly — is **not** duplicated; it lives in
`base.py`. When editing lifecycle behavior, change `base.py`, not the subclasses.

### Executor: ephemeral subagents

The Planner emits an `OrchestratorPlan` of role-based `SubagentTask` objects (not raw
tool calls). `runtime/delegation.py::execute_tasks` runs them in **dependency-aware
batches** (`asyncio.gather` per batch; a task is "ready" when all `depends_on` are
`completed`). Each subagent is spin-up/tear-down: it gets a scoped context packet +
allowed tools, returns a structured `SubagentResult`, and is discarded. It owns no
long-term memory and no durable side effects.

Roles are **partitioned by domain** so neither orchestrator can instantiate the other's
specialists (`agent/subagents/__init__.py::role_map_for_domain`):
- **Conversation-only** (`agent/conversation/subagents/`): `general`, `technical`,
  `billing`, `escalation`.
- **Signal-only** (`agent/subagents/`): `health_analysis`, `outreach_draft`.
- **Shared**: `playbook_retrieval`.

The compliance critic is **not** a delegated subagent — it is the Reflector phase invoked
directly by the orchestrator (see below).

### Planner: LLM role selection with deterministic fallback

`ConversationOrchestrator` builds its plan in `conversation/conversation_planner.py`.
After intent/urgency/entity extraction, an **LLM planner** (`conversation/llm_planner.py`)
semantically selects which specialist roles answer the turn, given a compact **capability
catalog** (`conversation/capability_catalog.py`) built from each role's skill *description*
(not the full SKILL.md body). Only trusted Python converts the validated role list into
tasks — the LLM never chooses tools, dependencies, or fan-out.

Safety rails, all enforced in code regardless of model output: a fixed role allowlist
(signal-only roles are unselectable), forced escalation on CRITICAL urgency / explicit
human-handoff, per-role tool grants (`_ROLE_TOOLS`), and a hard cap on answer roles. The
planner falls back to the deterministic `_route_roles()` keyword/intent router whenever
the LLM call fails, times out, returns malformed/low-confidence/invalid JSON, or picks an
unsupported role. The LLM planner is **on by default**; set `CONVERSATION_LLM_PLANNER=0`
to force the deterministic path (tests use this to stay hermetic). The planner call
timeout is generous (planner models can take ~15–20s); a timeout logs and falls back.

### Reflector: the compliance gate

`ComplianceCriticAgent` (`run_compliance_critic`) reviews aggregated results before any
customer-visible output or external write. Emission is gated on the **finalized decision**
(`reducer.py::finalize_decision`), not the raw review — the critic may approve while the
reducer still blocks (e.g. redactions require a replan). The sentinel
`EMITTED_ACTION = "emit_or_execute_approved_payload"` is the only decision action that
releases output/writes. `on_approved` is called only when that sentinel is set.

Its system persona is loaded from `skills/<tenant>/compliance_critic/SKILL.md` via
`SkillManager.persona_for("compliance_critic")`, with a short in-code string as fallback
when the skills dir is unavailable.

### on_approved side effects (non-blocking profile update)

`ConversationOrchestrator.on_approved` records the turn to memory and then **fire-and-forgets**
the customer-profile update (`asyncio.create_task`, tracked in a task set with a
done-callback that logs — never raises — failures). The profile update runs an LLM distill
plus DB writes, so it must **not** block the customer's reply. A failure there (e.g. DB
down) is swallowed and does not fail the turn.

### Tool boundaries (security-critical)

Tools are split into two enforced boundaries in `packages/tool_system/src/registry.py`:
- `ToolBoundary.INTERNAL` — read-only analysis, run in-process (`query_health`,
  `query_playbooks`, plus the read-only prototypes `check_human_availability` and
  `process_refund`).
- `ToolBoundary.MCP_ACTION` — side-effecting external writes (`send_email`, `send_slack`,
  `escalate_to_human`), which run **only** through the separate `apps/tool_gateway` MCP
  process.

Prototype tools (current milestone — deterministic stubs, no real integration yet):
- `check_human_availability` (INTERNAL) — called inline by the escalation subagent during
  the turn; always returns `available=false` + "No human support representative is currently
  available." so the fallback message can appear in the same reply.
- `process_refund` (INTERNAL) — called by the billing subagent for straightforward,
  order-referenced refunds; always returns success. Disputes/large/complex cases still route
  to human review per the billing SKILL.md.
- `escalate_to_human` (MCP_ACTION) — gateway-only wiring for the *future* real (networked)
  human-handoff. It is **not** invoked from a chat turn (conversation `supports_external_writes`
  stays `False`); it exists so the propose → compliance → `on_approved` path is ready later.

`require_tool_boundary()` raises `ToolBoundaryError` if a tool is invoked through the wrong
boundary. Internal dispatch refuses MCP-action tools; the gateway exposes *only* action
tools. The gateway (`ActionService`) is **tenant-authoritative** (it does not trust the
tenant id in the draft payload), enforces **approval** (`approval_id`) and **idempotency**
(`idempotency_key`) — a repeat call returns `status="duplicate"` with the same
`provider_message_id`. Never route a side-effecting tool through the internal path, and
never let a draft/outreach subagent execute actions (`outreach_draft` has
`DEFAULT_ALLOWED_TOOLS == []` by design).

### Skills layer (tenant-scoped prompt injection)

Role personas live as `SKILL.md` files under `skills/<tenant>/<role>/` (e.g.
`skills/demo-tenant/billing_support/SKILL.md`), each with YAML front matter (`name`,
`description`, `agents`, `keywords`, `enabled`) plus a markdown SOP body. `SkillManager`
(`agent/runtime/skills.py`) discovers and injects them:
- `prompt_for(message, agent_role)` — role-matched advisory skill block injected into a
  subagent's system prompt at build time (`runtime/prompts.py::build_system_prompt`).
- `persona_for(agent_role)` — returns the SOP body as a system persona (used by the
  compliance critic).

The full SKILL.md body is execution-focused and only injected into the **selected**
subagent at run time. The planner's capability catalog uses only each skill's short
*description* — never the whole body. Subagent classes keep a one-line `ROLE_BRIEF` as an
in-code fallback used when the skills dir is unavailable (e.g. offline). Skills are advisory:
the system role and safety boundaries always win over skill content.

### Supporting packages

- `packages/llm_gateway/` — model routing (`router.py`), semantic cache (`cache.py`),
  circuit breaker (`circuit.py`). Single entry point for all LLM calls.
- `packages/knowledge_service/` — RAG over pgvector, per-tenant collections
  (`ingest`/`embed`/`retrieve`).
- `packages/session/` — Temporal workflow/activity/state helpers for long waits
  (e.g. "wait 48h then escalate").
- `packages/observability/` — OpenTelemetry tracer + Langfuse wrapper.
- `packages/redis/`, `packages/db/`, `packages/auth/` — tenant-namespaced Redis keys,
  Postgres/RLS access, JWT+RBAC.

### Multi-tenant isolation

Every layer filters by `tenant_id`: Postgres RLS, Redis key prefixing
(`tenant:{id}:...`), per-tenant queues. The gateway re-derives tenant identity from
trusted context rather than the LLM-generated payload. Treat any code that could leak one
tenant's data to another as a bug.

### Conversation latency redesign (scaffolded, not wired)

`apps/agent_service/src/agent/conversation/conversation_loop.py` (`ConversationLoop`) and
`delegates.py` implement a **proposed** lower-latency conversation architecture: a single
GeneralAgent running a bounded ReAct loop, calling specialists as tools (`delegate_billing`
/ `delegate_technical` / `delegate_escalation`), with compliance rules embedded in its
SKILL.md (no separate critic call) and real token streaming. It is **not yet wired into the
running path** — `chat_handler.py` still calls `run_conversation_agent` →
`ConversationOrchestrator.run()` (the P-E-R pipeline described above). See
`note/conversation_system_zh.md` and `tests/test_conversation_loop.py` for the full design.

## Conventions

- **Absolute imports only**, anchored at the repo root: `from packages.x.src.y import z`,
  `from apps.x.src.y import z`. No relative imports (`from ..utils import ...`).
- Module directories use **underscores** (`api_gateway`, `llm_gateway`). The README and
  some diagrams show hyphenated names (`api-gateway`) — those are display names only; the
  real Python modules are underscored.
- PEP 8. Never write the literal `===` (three-or-more equals) inside a comment or
  docstring anywhere in the codebase.
- LLM provider defaults to **OpenRouter** (OpenAI-compatible), models
  `deepseek/deepseek-v4-flash` (worker) and `deepseek/deepseek-v4-pro` (large/planner).
  Embeddings use `qwen/qwen3-embedding-8b` (1536-dim). Never hardcode model names — resolve
  them through `packages/agent/src/models.py` (`worker_model()` / `planner_model()`) and the
  `EMBEDDING_MODEL` / `OPENROUTER_MODEL` / `OPENROUTER_LARGE_MODEL` env vars.

## Deferred / stubs

- `apps/temporal_worker/src/temporal.py` is an intentional empty stub — Temporal
  (durable long-waits) and Langfuse (tracing depth) are **deferred** until later; the app
  runs without them. See `docs/AGENT_PLAN.md`.
- `infra/k8s` and `infra/terraform` described in the README do not exist.
- LangGraph/LangChain are not installed (removed from `requirements.txt`); no code imports
  them yet.
- `apps/api_gateway/src/app.py` IS now implemented (chat + signals + skills). The API
  mounts routers via a direct `app.router.routes.append` shim (`_mount`) because the pinned
  Starlette drops routes on `include_router` with a prefix — don't "fix" it back to
  `include_router` without re-verifying `/chat/turn` and `/signals/*` still register.
