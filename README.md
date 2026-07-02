# Customer Success Agent

A B2B Customer Success Automation Platform powered by a multi-step AI Agent.

> **Read the full documentation in [`docs/`](./docs/)** — `docs/README.md` (English) or `docs/README.zh-CN.md` (中文).

This README is a quick orientation for developers who just want to know what's in the repo.

---

## Repository Layout

```text
CustomerAgent/
├── apps/                          # Running processes (independently deployed)
│   ├── api-gateway/               # FastAPI HTTP gateway
│   ├── agent-service/             # Python ReAct agent core (P-E-R) + LangGraph
│   ├── skill-gateway/             # Tool registry + sandbox execution
│   └── temporal-worker/           # Temporal workflow worker
│
├── packages/                      # Shared libraries (imported by apps)
│   ├── shared/                    # Shared type definitions and utilities
│   ├── config/                    # Environment variable schema and config loading
│   ├── db/                        # SQLAlchemy models + Alembic migrations
│   ├── redis/                     # Redis client wrapper (tenant-namespaced keys)
│   ├── llm-gateway/               # Multi-provider LLM routing, caching, circuit breaking
│   ├── skill-system/              # Skill registry + sandbox definitions
│   ├── session/                   # State machine types + Temporal workflow helpers
│   ├── observability/             # OpenTelemetry + Langfuse SDK wrappers
│   └── auth/                      # JWT validation + tenant-scoped RBAC
│
├── infra/                         # Infrastructure as code
│   ├── docker/                    # Local dev docker-compose
│   ├── k8s/                      # Kubernetes deployment manifests
│   └── terraform/                 # Cloud resource provisioning
│
├── tests/                         # Test suites (unit, integration, e2e)
├── docs/                          # Full project documentation
│
├── infra/docker/docker-compose.yml  # Local dev stack (postgres, redis, temporal, langfuse)
├── infra/docker/.env              # Docker stack secrets (NEXTAUTH_SECRET, SALT, ...)
├── start.sh                       # One-command local startup
├── config.sh                      # Local shell env loaded by start.sh
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

**Rule of thumb:** `packages/` are libraries, `apps/` are processes. `apps/` are deployed independently and share logic only through `packages/` — they never import each other.

---

## Quick Start (Local Development)

```bash
# 1. Configure secrets for the local Docker stack
#    Edit infra/docker/.env (NEXTAUTH_SECRET, SALT) and config.sh as needed.

# 2. Start the local infrastructure stack (postgres, redis, temporal, langfuse)
./start.sh

# 3. Run database migrations
cd packages/db
alembic upgrade head

# 4. Start each app (in separate terminals)
cd apps/api-gateway
uvicorn src.app:app --reload --port 8000

cd apps/agent-service
python -m src.rq_worker

cd apps/temporal-worker
python -m src.temporal
```

Open:

- API Gateway → http://localhost:8000
- Temporal UI → http://localhost:8088
- Langfuse → http://localhost:3000
- Jaeger → http://localhost:16686

---

## Where to Look

| If you want to... | Look here |
|---|---|
| Understand what the agent does | `docs/README.md` (or `docs/README.zh-CN.md`) |
| Add a new LLM provider | `packages/llm-gateway/src/router.py` |
| Add a new skill (e.g. `send-sms`) | `packages/skill-system/src/registry.py` |
| Change the agent's decision logic | `apps/agent-service/src/rq_worker.py`, and see `docs/AGENT_PLAN.md` |
| Add a new HTTP route | `apps/api-gateway/src/routes/` |
| Modify the customer journey state machine | `packages/session/src/state.py` |
| Change database schema | `packages/db/src/` |
| Add tracing to a function | `packages/observability/src/tracer.py` |

---

## Status

This repository is in the layout-and-scaffolding phase. Each file is currently a placeholder; the next iteration will fill in the implementation following the plan in `docs/AGENT_PLAN.md`.
