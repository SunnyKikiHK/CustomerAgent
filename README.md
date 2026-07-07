# Customer Success Agent

> A B2B Customer Success Automation Platform powered by a layered multi-agent AI runtime. The platform separates proactive signal automation from customer conversation: `SignalOrchestrator` handles backend-detected customer-success events, while `ConversationOrchestrator` handles chat turns. Both run a shared Planner → Executor → Reflector lifecycle, spin up scoped specialist subagents for bounded work, monitor customer health, generate personalized outreach, run playbooks, and escalate to humans when needed — automatically, 24/7.

---

## 1. What Does This Agent Do?

In a B2B SaaS company, the **Customer Success** team is responsible for making sure customers keep using the product, renew their subscriptions, and ideally upgrade to a higher tier. This work is mostly:

- **Monitoring** how customers use the product
- **Reaching out** when something looks wrong (low usage, support issues, upcoming renewal)
- **Sending the right message** at the right time (email, Slack DM, meeting invite)
- **Following up** if there is no reply
- **Escalating** to a human Customer Success Manager (CSM) when AI alone is not enough

Doing this well for hundreds of customers is impossible by hand. This agent does it automatically.

### The Problem It Solves

| Before (Manual CSM Work) | After (AI Agent) |
|---|---|
| CSM checks 10 different tools to gauge customer health | Agent analyzes usage, support, and billing data in real time |
| Generic "just checking in" emails | AI writes a personalized email based on the customer's actual product usage |
| 4 hours to prepare a Quarterly Business Review (QBR) | Agent drafts the QBR presentation in ~5 minutes |
| Renewal risk is noticed only after the customer says no | Agent detects risk 90 days before renewal and starts a recovery playbook |
| Upsell opportunities are easy to miss | Agent spots power users ready for the enterprise tier |

### A Real Scenario

It is 8:00 AM. The agent runs its morning scan of every customer account.

- **Acme Corp's API usage dropped 40%** this week. Red flag.
- Acme's support tickets **tripled** since last Monday. Another red flag.
- Acme's contract renews in 60 days. The risk is real.

**8:05 AM** — The agent triggers the "at-risk" playbook, generates a personalized email to Acme's CTO referencing the webhook feature they had discussed, attaches the integration guide, and sends it via Gmail. It also logs the interaction in Salesforce.

**8:10 AM** — If Acme does not reply within 48 hours, the agent escalates by sending a Slack DM to the assigned CSM with full context. If Acme replies, the agent drafts a meeting agenda and sends a calendar invite automatically.

The CSM only has to step in for the conversations that actually need a human. The agent handles the routine.

---

## 2. High-Level Architecture

The system is split into two kinds of components:

- **`packages/`** — Shared libraries imported by apps. These are not running processes; they hold business logic, schemas, and SDK wrappers.
- **`apps/`** — Executable processes. These are deployed independently and share logic only through `packages/` — they never import each other directly.

### Repository Layout

```text
CustomerAgent/
├── apps/                          # Running processes (independently deployed)
│   ├── api-gateway/               # FastAPI HTTP gateway
│   │   └── src/
│   │       ├── app.py             # FastAPI app and plugin registration
│   │       ├── routes/            # Route definitions
│   │       └── plugins/           # Custom plugins (auth, rate limiting, etc.)
│   │
│   ├── agent-service/             # Orchestrator-Subagent runtime + LangGraph
│   │   └── src/
│   │       └── rq_worker.py       # RQ worker entry point
│   │
│   ├── tool-gateway/             # Tool registry + sandbox execution
│   │   └── src/
│   │       └── index.py           # Sandboxed tool runner
│   │
│   └── temporal-worker/            # Temporal workflow worker
│       └── src/
│           └── temporal.py          # Temporal worker entry point
│
├── packages/                      # Shared libraries (imported by apps)
│   ├── shared/                    # Shared type definitions and utilities
│   │   └── src/
│   ├── config/                    # Environment variable schema (pydantic-settings)
│   │   └── src/
│   ├── db/                        # SQLAlchemy models + Alembic migrations
│   │   └── src/
│   │       └── migrations/        # Alembic migration files
│   ├── redis/                     # Redis client wrapper (tenant-namespaced keys)
│   │   └── src/
│   ├── llm-gateway/               # Multi-provider LLM routing, caching, circuit breaking
│   │   └── src/
│   │       ├── router.py          # Picks the right model per request
│   │       ├── cache.py           # Semantic cache (Redis + pgvector)
│   │       └── circuit.py         # Circuit breaker (state stored in Redis)
│   ├── tool-system/              # Tool registry + sandbox definitions
│   │   └── src/
│   │       ├── registry.py        # Tool registration and discovery
│   │       └── sandbox.py        # Sandboxed execution (RestrictedPython / subprocess)
│   ├── session/                   # State machine types + Temporal workflow helpers
│   │   └── src/
│   │       ├── workflow.py        # Temporal workflow definitions
│   │       ├── activities.py      # Temporal activities
│   │       └── state.py           # Session state types and transitions
│   ├── observability/             # OpenTelemetry + Langfuse SDK wrappers
│   │   └── src/
│   │       ├── tracer.py          # OTel tracer initialization
│   │       └── langfuse.py       # Langfuse SDK wrapper
│   └── auth/                      # JWT validation + tenant-scoped RBAC
│       └── src/
│
├── infra/                         # Infrastructure as code
│   ├── docker/                    # Local dev docker-compose
│   ├── k8s/                      # Kubernetes deployment manifests
│   └── terraform/                 # Cloud resource provisioning
│
├── tests/                         # Test suites (unit, integration, e2e)
├── docs/                          # Full project documentation
│
├── infra/docker/docker-compose.yml # Local dev stack (postgres, redis, temporal, langfuse)
├── infra/docker/.env              # Docker stack secrets (NEXTAUTH_SECRET, SALT, ...)
├── start.sh                       # One-command local startup
├── config.sh                      # Local shell env loaded by start.sh
└── requirements.txt               # Python dependencies
```

### How The Pieces Fit Together

```text
                       ┌──────────────┐
                       │  CSM / Web   │
                       │   Frontend   │
                       └──────┬───────┘
                              │ HTTPS
                              ▼
                       ┌──────────────┐
                       │ api-gateway  │  FastAPI HTTP
                       └──┬─────┬─────┘
                          │     │
              enqueues    │     │  reads/writes
                          ▼     ▼
        ┌──────────────────┐  ┌──────────────────┐
        │ RQ (Redis Queue)  │  │  Postgres        │
        │   - outreach     │  │   - tenants      │
        │   - workflows    │  │   - customers    │
        └────────┬─────────┘  │   - interactions │
                 │            │   - audit_logs   │
                 ▼            └──────────────────┘
        ┌─────────────────────────────────────────┐
        │         agent-service (app)              │  SignalOrchestrator + ConversationOrchestrator
        │  - plans role-based subagent tasks        │
        │  - spins up ephemeral specialist agents   │
        │  - reflects before output or mutation     │
        └────────┬───────────────────────────────┘
                 │          │              │
        calls    │          │              │ calls
                 ▼          ▼              ▼
        ┌──────────────┐ ┌──────┐ ┌──────────────────┐
        │ llm-gateway  │ │ RAG  │ │  tool-gateway   │
        │ (packages/)  │ │      │ │   (apps/)        │
        └──────┬───────┘ └──────┘ └────────┬─────────┘
               │          │                  │
               ▼          ▼                  ▼
        ┌──────────┐ ┌──────────┐ ┌─────────────────┐
        │  Redis   │ │ pgvector │ │  External APIs   │
        │          │ │          │ │ Email/Slack/CRM  │
        └──────────┘ └──────────┘ └─────────────────┘
```

---

## 3. Core Functions

### 3.1 The Agent Runtime (Two Top-Level Orchestrators → Shared P-E-R Runtime)

Agent work enters `agent-service` through one of two top-level systems:

| Top-level system | Input | Purpose |
|---|---|---|
| `SignalOrchestrator` | `SignalAgentInput` / `CustomerSignal` | Proactive customer-success automation from backend-detected events |
| `ConversationOrchestrator` | `ConversationAgentInput` / `ChatMessage` | Customer-facing chat/message turns with required conversation memory |

Both systems run a shared top-level **Planner → Executor → Reflector (P-E-R)** loop:

| Phase | Signal path | Conversation path | Shared behavior |
|---|---|---|---|
| Planner | `signal_planner.py` builds a signal-specific `OrchestratorPlan` | `conversation_planner.py` builds a chat-specific `OrchestratorPlan` | Plans role-based `SubagentTask` objects, not raw tool calls |
| Executor | Delegates to health/playbook/outreach specialists | Starts with `CustomerChatAgent`, then adds specialists only when needed | `DelegationManager` spins up ephemeral subagents with isolated ReAct loops |
| Reflector | Reviews proposed outreach/actions before writes | Reviews customer-visible answer before final emission | `ComplianceCriticAgent` checks PII, tenant isolation, factual grounding, tone, and policy |

Subagents are **spin-up, tear-down workers**. They do not own long-term memory, final customer-visible responses, or durable side effects. They receive a task packet, produce a structured `SubagentResult`, and are discarded.

Common subagent roles:

- `HealthAnalysisAgent` — analyzes health score, usage trends, support tickets, churn risk.
- `PlaybookRetrievalAgent` — retrieves relevant playbooks or knowledge-base context.
- `OutreachDraftAgent` — drafts proactive customer-facing emails, Slack messages, and meeting agendas using prior subagent markdown.
- `CustomerChatAgent` — handles synchronous customer chat turns with bounded memory slices.
- `ComplianceCriticAgent` — performs the Reflector review before output emission or external writes.

The lower-level ReAct loop still exists, but it is used **inside** tool-using subagents rather than acting as the top-level architecture.

### 3.1.1 Agent Domain Separation

The project intentionally separates two input domains:

| Domain object | Top-level system | Meaning | Typical path |
|---|---|---|---|
| `CustomerSignal` | `SignalOrchestrator` | Business event / automation trigger, such as usage drop or renewal risk | Signal planner → health/playbook/outreach subagents → ComplianceCriticAgent |
| `ChatMessage` | `ConversationOrchestrator` | Customer conversation turn in a chat session | Conversation planner → CustomerChatAgent → optional specialists → ComplianceCriticAgent |

They should not be merged into one generic business payload. An optional dispatch wrapper can route inbound data, but after routing, signal automation and customer conversation use separate top-level orchestrators.

### 3.2 LLM Gateway (Model Routing, Caching, Billing)

A single entry point for all LLM calls, with three responsibilities:

- **Model routing** — picks the right model per request. A simple "checking in" email goes to a cheap, fast model. An executive QBR summary goes to a more powerful model. Streaming chat goes to a low-latency model.
- **Prompt caching** — caches customer profiles and playbooks so the same context is not re-sent on every LLM call. A typical workload sees roughly **80% cost reduction** after caching.
- **Billing** — records every token used per tenant per model, so we can bill each customer company for their actual LLM spend.

### 3.3 Tool Sandbox (Safe Tool Execution)

Tools are the "hands" of the agent — they actually send emails, query Salesforce, post to Slack. Running untrusted code is dangerous, so each tool runs in a sandbox with explicit permissions. The `tool-gateway` app runs the sandbox as a separate OS process:

| Tool | Network | Files | Rate Limit | Extra Validation |
|---|---|---|---|---|
| `send-email` | SendGrid / Mailgun only | Read templates | 100/hr per tenant | Email format + rate check |
| `send-slack` | Slack API only | None | 1000/hr per tenant | Channel ID validation |
| `salesforce-query` | Salesforce domains | None | 500/hr per tenant | Block any write query (UPDATE / DELETE / INSERT) |
| `generate-pdf` | None (local) | Write `/tmp` only | 50/hr per tenant | PDF size limit |

### 3.4 RAG Knowledge Base (pgvector)

A retrieval-augmented generation (RAG) pipeline lets the agent pull relevant playbooks, case studies, and objection-handling notes from a vector database. The `packages/knowledge-service/` package handles:

- **Ingest** — document parsing and chunking
- **Embed** — vector embedding via embedding API
- **Retrieve** — pgvector similarity search per tenant

Each tenant has its own knowledge collection — Acme's playbooks are kept separate from Beta's playbooks.

### 3.5 Session State (Redis + Postgres + Temporal)

Every customer has a state in their journey:

```text
  monitoring ──► outreach_sent ──► waiting_reply ──► reply_received
       ▲              │                  │               │
       │              ▼                  ▼               ▼
       │          escalated          meeting_scheduled  resolved
       │              │
       └──────────────┘  (after human resolution, back to monitoring)
```

- **Redis** (via `packages/redis/`) stores the current state in real time (sub-millisecond read/write), with tenant-namespaced keys.
- **Postgres** (via `packages/db/`) stores the full history of state transitions (audit trail).
- **Temporal** (via `apps/temporal-worker/`) runs long-lived workflows — for example, "wait 48 hours for a reply, then escalate if none arrives." If the worker crashes mid-wait, Temporal resumes from where it left off.

### 3.6 Multi-Tenant Isolation

The platform serves many B2B SaaS companies. Customer data for Acme must never leak to Beta. Three layers of isolation enforce this:

- **Postgres Row-Level Security (RLS)** (in `packages/db/`) — every query automatically filters by `tenant_id`. A query that forgets the `WHERE` clause still returns only the correct tenant's data.
- **Redis key prefixing** (in `packages/redis/`) — every key is namespaced as `tenant:{tenantId}:...`. There is no global "state" key.
- **Per-tenant queues** — each tenant has their own RQ queue, so a flood of jobs from one tenant cannot starve another.

### 3.7 Security (Audit, PII, RBAC)

- **Audit log** — every action (email sent, CRM updated, report generated) is recorded with the actor, the resource, the before/after state, and the IP address (`packages/db/` schema).
- **PII masking** — emails, phone numbers, and credit card numbers are replaced with opaque tokens before being sent to the LLM. The LLM never sees the raw PII (`packages/auth/`).
- **RBAC** — three roles: `admin` (configure playbooks, view all data), `csm` (view assigned customers, send emails), `viewer` (read-only access) (`packages/auth/`).

### 3.8 High-Concurrency Handling (Queue, Circuit Breaker, Rate Limit)

- **Priority queue** — at-risk customers are processed before renewal reminders, which are processed before routine check-ins (enforced in `apps/agent-service/`).
- **Circuit breaker** — if Salesforce's API starts returning 500s, the breaker opens and we serve cached data for 60 seconds instead of hammering a broken service (`packages/llm-gateway/src/circuit.py`).
- **Rate limiting** — per-tenant token buckets prevent one customer from monopolizing our email quota (in `packages/llm-gateway/`).

### 3.9 Observability (OpenTelemetry + Langfuse)

- **OpenTelemetry** (`packages/observability/`) traces every LLM call, every tool execution, and every database query. The full chain — "the planner decided X, then the email tool sent Y" — is visible in Jaeger / Grafana.
- **Langfuse** (`packages/observability/`) specifically tracks AI quality: prompt, response, model, token usage, and a quality score ("was this email effective? did the customer respond positively?").

---

## 4. End-to-End Workflow

Here is what happens when the agent decides to reach out to a customer.

```text
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 1 — Detection                                                │
  │ A scheduled job (or a webhook from the product analytics system)  │
  │ signals that Acme Corp's usage dropped 40% week-over-week.        │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 2 — Enqueue                                                  │
  │ The api-gateway enqueues an RQ job with priority=CRITICAL         │
  │ (because health score < 50).                                      │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 3 — Worker picks up the job                                  │
  │ The agent-service starts SignalOrchestrator, loads tenant config, │
  │ global constraints, account memory slices, and audit context.      │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 4 — Planner phase                                             │
  │ The Signal Planner LLM returns an OrchestratorPlan:                │
  │   - HealthAnalysisAgent: analyze risk indicators                   │
  │   - PlaybookRetrievalAgent: retrieve recovery playbook             │
  │   - OutreachDraftAgent: draft outreach using prior markdown        │
  │ Each item is a SubagentTask, not a raw low-level tool step.        │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 5 — Executor phase                                            │
  │ The delegation manager spins up ephemeral subagents. Each receives │
  │ scoped context, allowed tools, and dependency markdown only.       │
  │ Health and playbook subagents run first; draft uses their results. │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 6 — Reflector phase                                           │
  │ ComplianceCriticAgent reviews aggregated SubagentResults for PII, │
  │ tenant isolation, factual grounding, tone, and business policy.    │
  │ No email, CRM write, or memory mutation happens before approval.   │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 7 — Approved emission / action request                        │
  │ If approved, SignalOrchestrator emits the final response or        │
  │ approved write payloads through tool-gateway. It then writes      │
  │ memory and audit logs. If blocked, it replans or escalates.        │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 8 — State update + Temporal workflow                         │
  │ Session state: monitoring → outreach_sent → waiting_reply.        │
  │ A Temporal workflow is started: "wait 48 hours, check for reply". │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 9a — Reply received (happy path)                            │
  │ Temporal detects the reply. State → reply_received.               │
  │ ConversationOrchestrator runs on the reply. If approved, it       │
  │ submits a meeting or follow-up action through tool-gateway.      │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 9b — No reply (escalation path)                             │
  │ Temporal times out after 48 hours. State → escalated.            │
  │ SignalOrchestrator prepares a CSM escalation packet, Reflector    │
  │ approves it, and tool-gateway sends the Slack DM.               │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ STEP 10 — Observability                                           │
  │ Every step above is traced in OTel and scored in Langfuse.        │
  │ Token usage and cost are recorded for tenant billing.             │
  └────────────────────────────────────────────────────────────────────┘
```

---

## 5. Why This Architecture?

### Why split `packages/` and `apps/`?

- **`packages` are libraries** that the apps import. They hold business logic — the LLM router, the RAG retriever, the tool registry, the DB schema, the auth layer — that can be unit-tested in isolation.
- **`apps` are processes**. `api-gateway` serves HTTP. `agent-service` runs the agent loop. `temporal-worker` runs workflows. `tool-gateway` runs untrusted tool code.
- In production, each app is deployed as an **independent service** that scales separately. A traffic spike to the HTTP API does not starve the worker, and a slow workflow does not slow down the API. They share logic through `packages/` only, never by importing each other.

### Why split signal automation and conversation, but share the P-E-R runtime?

- **Different outer loops**: signal automation is async and proactive; conversation is low-latency, customer-facing, and often streaming.
- **Shared execution model**: both still benefit from Planner → Executor → Reflector, role-based subagents, structured results, and critic approval.
- **Separation of expertise**: health analysis, playbook retrieval, drafting, chat, and compliance each have different prompts and tool needs.
- **Context control**: each top-level orchestrator gives each subagent only the task packet, dependency markdown, and tool permissions it needs.
- **Isolation**: subagents are spin-up, tear-down workers. Their scratchpads do not pollute long-term memory or the top-level orchestrator context window.
- **Safety**: subagents return structured `SubagentResult` objects; the `ComplianceCriticAgent` reviews aggregated results before customer-visible output or external writes.
- **Testability**: signal planner, conversation planner, shared delegation, subagents, and critic behavior can be tested independently.
- **Explainability**: audits can show which orchestrator ran, which subagent produced which result, what the Reflector approved or blocked, and why the final decision was emitted.

### Why a separate LLM Gateway?

Without a gateway, every part of the code would call OpenAI / Anthropic / Google directly. The gateway gives us:

- **A single place to switch models** when pricing or quality changes.
- **A single place to cache** — without it, two unrelated code paths would re-send the same context to the LLM and pay for it twice.
- **A single place to count tokens** for billing.

### Why a separate tool-gateway process?

A tool is, in effect, code that the LLM wrote and we are about to run. The LLM can hallucinate — it might generate a `send-email` call with a wrong address, or even a `salesforce-query` with a `DELETE` statement. By running tools in a separate process with explicit permissions (allowed domains, rate limits, query validation), we contain the blast radius of any bug or prompt-injection attack. If the sandbox crashes, the main agent is unaffected.

### Why Temporal for long waits?

Temporal is a workflow engine. A typical CS workflow is full of "wait 48 hours, then do X" steps. If we implemented that with `time.sleep` in a worker process, the worker would have to be alive for the full 48 hours — wasteful and fragile. Temporal persists workflow state to the database, so the worker can crash and resume without losing the wait. The same workflow can be inspected, replayed, and debugged after the fact.

---

## 6. Glossary

- **Tenant** — A B2B SaaS company using this platform (e.g. "Acme Corp", "StartupXYZ"). Each tenant has their own customers, playbooks, and CRM integrations.
- **SignalOrchestrator** — The top-level agent system for proactive `CustomerSignal` workflows such as usage drops, renewal risk, NPS drops, and support-ticket escalations.
- **ConversationOrchestrator** — The top-level agent system for customer chat/message turns. It uses conversation memory, starts with `CustomerChatAgent`, and can delegate to specialists when needed.
- **P-E-R** — Planner → Executor → Reflector. The top-level lifecycle run by both orchestrators for active agent work.
- **SubagentTask** — A bounded task packet created by the Orchestrator Planner for a specialist subagent role.
- **SubagentResult** — The structured result returned by an ephemeral subagent, including markdown, data, tool evidence, and error state.
- **ComplianceCriticAgent** — The Reflector-phase evaluator that checks PII, security, tenant isolation, factual grounding, and business policy before output or mutation.
- **Customer** — An end-user account managed by the tenant (e.g. "Sarah at Acme Corp"). Has a health score, MRR, and renewal date.
- **Health score** — A 0-100 number estimating how likely a customer is to renew. Drops in usage, spikes in support tickets, and low NPS all lower it.
- **MRR** — Monthly Recurring Revenue. How much this customer pays per month.
- **CSM** — Customer Success Manager. A human who owns the relationship with a set of customers.
- **Playbook** — A standard workflow for handling a situation (e.g. "at-risk recovery", "renewal", "expansion").
- **Tool** — A tool the agent can invoke (e.g. `send-email`, `salesforce-query`).
- **QBR** — Quarterly Business Review. A presentation given to the customer summarizing their usage, value, and upcoming opportunities.
- **NPS** — Net Promoter Score. A measure of customer satisfaction, from 0 to 10.
- **PII** — Personally Identifiable Information (emails, phone numbers, credit cards).
- **RAG** — Retrieval-Augmented Generation. A pattern where the LLM is given relevant documents from a vector database before generating a response.
- **RLS** — Row-Level Security. A Postgres feature that filters rows in every query based on a policy.

---

## 7. Quick Reference: File Map

| Component | Purpose | Key Files |
|---|---|---|
| `apps/api-gateway` | HTTP service (FastAPI) | `src/app.py`, `src/routes/`, `src/plugins/` |
| `apps/agent-service` | Agent core (`SignalOrchestrator`, `ConversationOrchestrator`, shared P-E-R runtime, ephemeral subagents, Reflector) | `src/rq_worker.py`, `src/agent/signal/`, `src/agent/conversation/`, `src/agent/orchestrator/`, `src/agent/runtime/`, `src/agent/subagents/` |
| `apps/tool-gateway` | Sandboxed tool execution | `src/index.py` |
| `apps/temporal-worker` | Temporal workflow worker | `src/temporal.py` |
| `packages/shared` | Shared types and utilities | `src/` |
| `packages/config` | Env schema + config loading | `src/` |
| `packages/db` | SQLAlchemy models + Alembic migrations | `src/`, `migrations/` |
| `packages/redis` | Redis client (tenant-namespaced) | `src/` |
| `packages/llm-gateway` | LLM routing, caching, billing | `router.py`, `cache.py`, `circuit.py` |
| `packages/tool-system` | Tool registry + sandbox definitions | `registry.py`, `sandbox.py` |
| `packages/knowledge-service` | RAG pipeline | `ingest.py`, `embed.py`, `retrieve.py` |
| `packages/session` | State machine + Temporal helpers | `workflow.py`, `activities.py`, `state.py` |
| `packages/observability` | Tracing + AI quality | `tracer.py`, `langfuse.py` |
| `packages/auth` | JWT validation + RBAC | `src/` |
| `infra/docker` | Local dev docker-compose | `docker-compose.yml` |
| `infra/k8s` | K8s deployment manifests | |
| `infra/terraform` | Cloud resource provisioning | |
