# CustomerAgent

> B2B customer-success automation platform: **proactively** detects customer-health signals
> and **reactively** serves customer conversations, with a strict compliance gate in front of
> every external action.

CustomerAgent runs two independently-deployed top-level agent systems — a *signal* system for
proactive outreach and a *conversation* system for customer chat — that share one runtime,
one tool layer, and one multi-tenant data plane.

---

## Architecture at a glance

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                 Shared runtime (BaseOrchestrator)        │
                    │  tool layer · skills · memory · compliance · observability│
                    └───────────────▲─────────────────────────────▲────────────┘
                                    │                             │
        ┌───────────────────────────┴───────────┐   ┌─────────────┴──────────────────────────┐
        │  Conversation system                   │   │  Signal system                        │
        │  Orchestrator-Workers                  │   │  P-E-R over Temporal                  │
        │  (GeneralAgent ReAct loop)             │   │  (deterministic planner + critic)     │
        │  · customer chat turns, SSE streaming  │   │  · proactive outreach, NPS, QBR       │
        │  · delegates billing/technical/esc     │   │  · external writes (email/Slack)      │
        └───────────────────────────────────────┘   └────────────────────────────────────────┘
```

- **Conversation path** — a single `GeneralAgent` runs a bounded ReAct loop and decides
  delegation itself through `delegate_billing` / `delegate_technical` / `delegate_escalation`
  tools; compliance/PII/tone rules are embedded in its `SKILL.md`. Answers stream token-by-token.
- **Signal path** — `SignalOrchestrator` runs the deterministic
  `Planner → Executor → Reflector` lifecycle; `ComplianceCriticAgent` gates every outgoing email
  or notification before the tool-gateway releases it.

### Tool boundaries (security-critical)

Tools are split into two enforced boundaries in `packages/tool_system/src/registry.py`:

| Boundary | Runs | Tools |
|---|---|---|
| `INTERNAL` | in-process (read-only) | `query_health`, `query_playbooks`, `check_human_availability`*, `process_refund`* |
| `MCP_ACTION` | `apps/tool_gateway` MCP process only | `send_email`, `escalate_to_human` |

\* prototype stubs (deterministic, not yet integrated). Side-effecting tools are gated by
**approval** + **idempotency** in a tenant-authoritative gateway — never routed through the
internal path, and never proposed-and-executed by a subagent.

### Multi-tenant isolation

Every layer filters by `tenant_id`: Postgres RLS, Redis key prefixing (`tenant:{id}:...`),
per-tenant queues, and a gateway that re-derives tenant identity from trusted context rather
than LLM-generated payloads. Leaking one tenant's data to another is treated as a bug.

---

## Repository layout

```
apps/
  agent_service/      # both orchestrators + subagents + runtime (LLM client, ReAct, skills)
  api_gateway/        # FastAPI: /chat/turn, /signals/*, /nps/*, /qbr/*, dashboard, SSE
  tool_gateway/       # MCP action sandbox (approval + idempotency + email providers)
  temporal_worker/    # Temporal worker: signal/NPS/QBR workflows, activities, schedules
packages/             # shared libraries (apps never import each other, only packages/)
  agent/  auth/  config/  db/  evaluation/  knowledge_service/  llm_gateway/
  observability/  redis/  session/  shared/  tool_system/  tools_cred/
skills/               # tenant-scoped hot-loadable SKILL.md personas + playbooks
scripts/              # seed_playbooks, verify_e2e, smoke_live_api, run_evaluation, ...
infra/docker/         # compose stack (Postgres+pgvector, Redis, Temporal, Langfuse, gateways)
frontend/             # Vite + React: chat view + signal dashboard
docs/  note/          # design docs + architecture walkthroughs (EN + zh)
tests/                # pytest suite (unit + live-backend tiers)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, asyncio |
| Orchestration | Temporal (signal/NPS/QBR workflows + schedules) |
| Data | PostgreSQL + pgvector (RAG, RLS), Redis (memory, queue, dedupe) |
| LLM | OpenRouter (OpenAI-compatible) — `deepseek-v4-flash` (worker) / `deepseek-v4-pro` (planner) |
| Embeddings | `qwen/qwen3-embedding-8b` (1536-dim, deterministic offline fallback) |
| Tool sandbox | MCP (streamable-HTTP) |
| Observability | OpenTelemetry + Langfuse |
| Auth | JWT + RBAC (`packages/auth`) |
| Frontend | Vite + React |

---

## Quick start

Run/build rules live in [`CLAUDE.md`](./CLAUDE.md) (the operational runbook). In short:

```bash
# 1. bring up infra (Postgres+pgvector, Redis, Temporal, Langfuse)
./start.sh

# 2. seed demo-tenant playbooks, then verify end-to-end
python scripts/seed_playbooks.py
python scripts/verify_e2e.py

# 3. run the test suite (see CLAUDE.md for offline/live variants)
python -m pytest tests/ -q -k 'not postgres'
```

> **Python runs through WSL only** — the `.venv` is a Linux venv; the Windows interpreter
> will not work. See `CLAUDE.md` → *Environment & execution* for the exact command prefix.

---

## Documentation

| Doc | What it covers |
|---|---|
| [`CLAUDE.md`](./CLAUDE.md) | Operational runbook — env, commands, run/build rules, conventions (authoritative) |
| [`docs/AGENT_PLAN.md`](./docs/AGENT_PLAN.md) | Implementation blueprint — target system, contracts, data flow, verification gate |
| [`docs/MIGRATION_PLAN.md`](./docs/MIGRATION_PLAN.md) | Migration milestones (M1–M9) to Orchestrator-Workers + signal finalization |
| [`note/conversation_system.md`](./note/conversation_system.md) | Conversation system deep-dive (Orchestrator-Workers redesign) |
| [`note/signal_system.md`](./note/signal_system.md) | Signal system deep-dive (detectors, NPS, QBR, gated email, schedules) |
| `note/*_zh.md` | Same walkthroughs in Chinese |

---

## Status

- **Conversation**: Orchestrator-Workers (GeneralAgent ReAct) is the live path; the old
  P-E-R conversation planner was removed.
- **Signal**: P-E-R over Temporal is wired end-to-end (detectors, NPS campaign, QBR,
  gated email delivery, schedules).
- **Deferred / stubs**: Temporal *durable long-waits* (`packages/session`), Langfuse tracing
  depth, and the `check_human_availability` / `process_refund` / `escalate_to_human` prototype
  tools (deterministic `TODO(gate)` stubs).
- **Infra placeholders**: `infra/k8s` and `infra/terraform` exist as empty directories — no
  Kubernetes/Terraform manifests are present yet.
