# Agent Implementation Plan

> This document maps the agent engine design onto the existing Python-based CustomerAgent codebase.
> It is the implementation blueprint for the `apps/agent-service/` and `packages/agent/` packages.
> Read this alongside `docs/README.md` for full system context.

---

## 1. Scope

The agent engine handles **all AI-driven decision-making and action execution** in the platform.
Its responsibilities:

- Classify incoming customer signals (usage drops, support tickets, NPS changes, renewal dates)
- Decide what action to take (send email, post Slack, schedule meeting, update CRM, generate QBR)
- Execute skills safely (sandboxed external API calls via skill-gateway)
- Evaluate outcomes and follow up or escalate
- Persist execution state for long-running workflows (48-hour waits, human-in-the-loop approvals)

**What is NOT in scope here** (handled by other packages/docs):

- The HTTP gateway that receives webhooks and enqueues jobs (`apps/api-gateway`)
- The LLM gateway that routes/limits/caches LLM calls (`packages/llm-gateway`)
- The skill sandbox that actually runs external API calls (`apps/skill-gateway`, `packages/skill-system`)
- The session state machine (`packages/session`)
- The observability layer (`packages/observability`)

---

## 2. Architecture Decision: Orchestrator-Subagent Runtime vs LangGraph Workflows

### Orchestrator-Subagent Pattern

The primary agent runtime uses a **Claude Code-style Orchestrator-Subagent pattern**:

- The **OrchestratorAgent** owns the request, tenant context, memory, policy, and final response.
- **Subagents** receive bounded tasks with limited context and scoped tools.
- Subagents return structured `SubagentResult` objects; they do not directly own long-term memory or final customer-visible output.
- The Orchestrator merges subagent results, runs policy/critic checks, decides approved actions, and writes memory/audit logs.

The shared ReAct loop remains useful, but it becomes a **subagent runtime primitive**, not the top-level architecture.

### Domain Boundary Decision

`CustomerSignal` and `ChatMessage` stay as **separate domain objects**:

- `CustomerSignal` = business event / automation trigger / async workflow input.
- `ChatMessage` = customer conversation turn / synchronous chat input / memory-backed thread.

They only meet at the orchestrator boundary through an `AgentInput` wrapper. Do not merge them into
one generic payload model; their lifecycle, latency requirements, safety policy, and memory behavior differ.

### Runtime Decision Matrix

| Scenario | Runtime | Reason |
|---|---|---|
| Plain Q&A, intent classification | Orchestrator → CustomerChatAgent | Low latency, memory-aware, streams response |
| Multi-tool chain, ≤ 5 steps, < 30s | Orchestrator + ReAct-capable subagent | Bounded delegation with scoped tools |
| Proactive outreach from `CustomerSignal` | Orchestrator → specialist subagents | Health/playbook/draft/critic can be isolated |
| Customer-facing chat | Orchestrator → CustomerChatAgent → optional specialists | Uses `ChatMessage`, memory, streaming |
| Complex multi-step (QBR generation, > 30s) | LangGraph Python + subagents | Needs checkpoint, long-running execution |
| Human-in-the-loop approval (refund > threshold) | LangGraph Python | Interrupt + checkpoint required |
| Compliance/audit requiring full replay | LangGraph Python + audit log | Replay/debuggability |
| Long wait steps ("wait 48h for reply") | Temporal (already in stack) | Process crash resilience |

### Orchestrator-Subagent Stack

For normal agent execution, we use direct Python components without Mastra:

```
OrchestratorAgent
  ├─ loads tenant config + memory
  ├─ creates SubagentTask objects
  ├─ delegates to scoped subagents
  │    ├─ HealthAnalysisAgent
  │    ├─ PlaybookRetrievalAgent
  │    ├─ OutreachDraftAgent
  │    ├─ CustomerChatAgent
  │    └─ ComplianceCriticAgent
  ├─ reduces SubagentResult objects into a FinalDecision
  └─ writes memory + audit log

Subagents
  └─ may use shared ReAct loop: LLM → tool call → result → LLM → ...
```

We deliberately **do not** use Mastra Python. The Orchestrator-Subagent runtime, delegation dispatcher,
and ReAct loop are implemented directly with Python + `openai` SDK, keeping the system explicit and auditable.


### LangGraph Python Stack

For workflows requiring **checkpoint + interrupt**, we use `langgraph` (the official
LangGraph Python SDK):

```
langgraph StateGraph
  └─ node: "gather_data" → calls Orchestrator/subagents for bounded work
  └─ node: "wait_for_approval" → interrupt
  └─ node: "execute_action" → approved tool/action execution
```

### Decision Tree (from input to runtime)

```
Input arrives
├── CustomerSignal?
│   └── Orchestrator → HealthAnalysisAgent → PlaybookRetrievalAgent → OutreachDraftAgent → ComplianceCriticAgent
├── ChatMessage?
│   └── Orchestrator → memory load → CustomerChatAgent → optional specialists → ComplianceCriticAgent
└── Long-running / approval / replay needed?
    └── LangGraph or Temporal owns durability; Orchestrator/subagents do bounded work inside steps
```

---

## 3. File Layout

```
apps/agent-service/
├── src/
│   ├── __init__.py
│   ├── rq_worker.py           # RQ worker entry point (existing)
│   │
│   ├── agent/                 # NEW — agent engine (Python-first)
│   │   ├── __init__.py
│   │   │
│   │   ├── types.py          # Shared Pydantic types (TaskPlan, TaskResult, etc.)
│   │   │
│   │   ├── planner.py        # P-E-R: Planner — decomposes signals into task plans
│   │   ├── executor.py       # P-E-R: Executor — ReAct tool-calling loop
│   │   ├── reflector.py      # P-E-R: Reflector — validates outcome against intent
│   │   │
│   │   ├── react_loop.py     # Core ReAct loop: LLM → tool call → result → LLM
│   │   ├── tool_caller.py    # Tool call dispatcher (routes to skill-gateway)
│   │   ├── chat.py           # Customer chat handler (multi-turn ReAct + memory)
│   │   │
│   │   └── registry.py       # Per-tenant agent factory (loads config, builds system prompt)
│   │
│   ├── workflow/
│   │   ├── __init__.py
│   │   ├── orchestrate.py    # Top-level: decides Python Agent vs LangGraph per task
│   │   │
│   │   └── langgraph/        # LangGraph Python workflows
│   │       ├── __init__.py
│   │       ├── refund.py     # Refund approval workflow (interrupt)
│   │       ├── qbr.py        # QBR generation workflow (checkpoint)
│   │       └── types.py      # Shared LangGraph state types

packages/agent/                # NEW — shared agent primitives (imported by agent-service)
├── src/
│   ├── __init__.py
│   ├── types.py              # TaskPlan, TaskResult, SessionContext, AgentResponse, CustomerSignal
│   ├── config.py             # AgentConfig per tenant (system prompt, tool list, model)
│   ├── chat_types.py         # ChatMessage, ChatRequest, ChatResponse (Target Phase)
│   └── memory.py             # Tenant-scoped conversation memory (Target Phase)

packages/session/
├── src/
│   ├── state.py              # SessionState types (existing, may need extension)
│   └── workflow.py           # Temporal workflow definitions (existing)
│       ├── outreach.py       # Outreach + wait-for-reply + escalate workflow
│       └── refund.py          # Refund approval workflow (existing; may delegate to LangGraph)
```

### Orchestrator-Subagent Layout Target

The current flat `agent/` layout should evolve into this structure during the Target Phase:

```text
apps/agent-service/src/agent/
├── orchestrator/
│   ├── orchestrator.py      # OrchestratorAgent: owns context, policy, final decision
│   ├── planner.py           # Builds SubagentTask delegation plan
│   ├── reducer.py           # Merges SubagentResult objects
│   └── policy.py            # Approval/guardrail rules
├── subagents/
│   ├── base.py              # BaseSubagent protocol
│   ├── health_analysis.py   # HealthAnalysisAgent
│   ├── playbook_retrieval.py
│   ├── outreach_draft.py
│   ├── customer_chat.py
│   └── compliance_critic.py
├── runtime/
│   ├── react_loop.py        # Shared ReAct primitive used by subagents
│   ├── delegation.py        # Orchestrator → subagent dispatch
│   ├── context.py           # Context packing and subagent memory slices
│   └── tool_caller.py       # Tool call dispatcher
├── chat.py                  # Customer chat integration wrapper
└── registry.py              # Orchestrator/subagent registry

packages/agent/src/
├── types.py                 # CustomerSignal and shared primitives
├── chat_types.py            # ChatMessage, ChatRequest, ChatResponse
├── orchestration_types.py   # AgentInput, OrchestratorPlan, FinalDecision
├── subagent_types.py        # AgentRole, SubagentTask, SubagentResult
├── config.py
└── memory.py
```

**Domain rule:** `CustomerSignal` lives in `types.py`; `ChatMessage` lives in `chat_types.py`.
They are separate domain objects and meet only through `AgentInput` in `orchestration_types.py`.

### Key Changes to Existing Files

| File | Change |
|---|---|
| `packages/db/src/schema.sql` | Add `task_history`, `subagent_calls`, `orchestrator_runs`, and memory tables for audit |
| `apps/agent-service/src/rq_worker.py` | Import and call `orchestrate()` instead of inline logic |
| `apps/agent-service/src/agent/orchestrator/` | New OrchestratorAgent, delegation planner, reducer, and policy layer |
| `apps/agent-service/src/agent/subagents/` | New specialist subagents: health, playbook, outreach draft, chat, compliance critic |
| `packages/agent/src/orchestration_types.py` | Add `AgentInput`, `OrchestratorPlan`, `FinalDecision` |
| `packages/agent/src/subagent_types.py` | Add `AgentRole`, `SubagentTask`, `SubagentResult` |
| `packages/llm-gateway/src/router.py` | Future plan: add `model_for_agent_task()` method for orchestrator/subagent routing |
| `apps/skill-gateway/src/index.py` | Future plan: sandboxed execution backend for approved tool calls |
| `requirements.txt` | Add `openai`, `langgraph`, `pydantic`, `httpx` |

---

## 4. Data Models

All types use **Pydantic v2** for validation, serialization, and JSON Schema generation
(which is passed to the LLM for tool-calling parameter validation).

> **Import-path caveat (resolve before Phase 1 coding):** the code samples below import from
> `packages.llm_gateway`, `packages.skill_system`, `packages.agent`, and `apps.agent_service`
> (underscores), but the real directories are hyphenated (`packages/llm-gateway`,
> `packages/skill-system`, `apps/agent-service`) and `packages/agent/` does not exist yet.
> Hyphens are **not valid** in Python module names, so these imports will not work as written.
> See Open Question #6 for the two resolution options.

### Agent Domain Separation

The plan keeps the two input domains separate:

- `CustomerSignal` is a business event that triggers automation.
- `ChatMessage` is a conversation turn in a customer chat session.
- `AgentInput` is the orchestrator boundary wrapper that accepts either one.

```python
# packages/agent/src/orchestration_types.py
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator
from packages.agent.types import CustomerSignal
from packages.agent.chat_types import ChatMessage


class AgentInputType(str, Enum):
    SIGNAL = "signal"
    CHAT = "chat"


class AgentInput(BaseModel):
    """Unified orchestrator boundary; domain objects remain separate."""
    type: AgentInputType
    tenant_id: str
    customer_id: str
    session_id: Optional[str] = None
    signal: Optional[CustomerSignal] = None
    message: Optional[ChatMessage] = None

    @model_validator(mode="after")
    def exactly_one_domain_input(self):
        if self.type == AgentInputType.SIGNAL and not self.signal:
            raise ValueError("signal input requires CustomerSignal")
        if self.type == AgentInputType.CHAT and not self.message:
            raise ValueError("chat input requires ChatMessage")
        return self
```

### Orchestrator/Subagent Types

```python
# packages/agent/src/subagent_types.py
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    ORCHESTRATOR = "orchestrator"
    HEALTH_ANALYSIS = "health_analysis"
    PLAYBOOK_RETRIEVAL = "playbook_retrieval"
    OUTREACH_DRAFT = "outreach_draft"
    CUSTOMER_CHAT = "customer_chat"
    COMPLIANCE_CRITIC = "compliance_critic"
    ACTION_EXECUTION = "action_execution"  # Future: when write actions move behind skill-gateway


class SubagentTask(BaseModel):
    id: str
    role: AgentRole
    objective: str
    input: dict = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    max_tokens: int = 2000


class SubagentResult(BaseModel):
    task_id: str
    role: AgentRole
    success: bool
    summary: str
    data: dict = Field(default_factory=dict)
    tool_calls: list[str] = Field(default_factory=list)
    error: Optional[str] = None
```

```python
# packages/agent/src/orchestration_types.py
class OrchestratorPlan(BaseModel):
    goal: str
    tasks: list[SubagentTask]
    requires_critic: bool = True


class FinalDecision(BaseModel):
    action: str
    response_text: str
    approved_tool_calls: list[str] = Field(default_factory=list)
    reasoning_summary: str
```

### Core Types

```python
# packages/agent/src/types.py
from __future__ import annotations
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TaskType(str, Enum):
    QUERY = "query"       # read-only
    MUTATION = "mutation" # write operation


class CustomerSignal(BaseModel):
    """A signal/event that triggers an agent run. The primary trigger input."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    customer_id: str
    type: str                          # e.g. "usage_drop", "nps_change", "renewal_due"
    payload: dict = Field(default_factory=dict)  # type-specific data
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Derived fields from payload
    @property
    def health_score(self) -> Optional[float]:
        return self.payload.get("health_score")

    @property
    def signal_text(self) -> str:
        """Human-readable description of the signal, used as Planner input."""
        type_labels = {
            "usage_drop": f"Usage dropped {self.payload.get('pct', '?')}% week-over-week",
            "nps_change": f"NPS score changed from {self.payload.get('from_nps')} to {self.payload.get('to_nps')}",
            "renewal_due": f"Renewal in {self.payload.get('days', '?')} days, health score {self.payload.get('health_score')}",
            "support_ticket": f"New support ticket: {self.payload.get('subject', '')}",
        }
        return type_labels.get(self.type, str(self.payload))


class TaskPlan(BaseModel):
    """A single task in a Planner-generated plan."""
    id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    description: str                           # natural-language for Executor / LLM
    skill: Optional[str] = None              # skill name in skill-gateway registry
    params: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)  # list of task IDs
    type: TaskType = TaskType.QUERY
    estimated_duration_seconds: int = 5

    @property
    def is_ready(self, completed: set[str]) -> bool:
        """Check if all dependencies are satisfied."""
        return all(dep_id in completed for dep_id in self.depends_on)


class TaskResult(BaseModel):
    """Result of executing a single TaskPlan."""
    task_id: str
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    tokens_used: int = 0


class LLMUsage(BaseModel):
    """Token usage from a single LLM call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class AgentResponse(BaseModel):
    """Final response from the P-E-R engine."""
    text: str
    tasks: list[TaskResult] = Field(default_factory=list)
    planner_tokens: int = 0
    reflector_tokens: int = 0
    skipped_reflector: bool = False
    satisfied: Optional[bool] = None        # only set when reflector ran
    feedback: Optional[str] = None          # only set when reflector ran


class SessionContext(BaseModel):
    """Runtime context passed through every layer of the agent engine."""
    tenant_id: str
    user_id: str
    session_id: str
    signal_id: str                          # CustomerSignal.id
    trace_id: Optional[str] = None           # for Langfuse/OpenTelemetry correlation
```

### AgentConfig per Tenant

```python
# packages/agent/src/config.py
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Per-tenant agent configuration. Loaded from tenants DB table, cached in Redis."""
    tenant_id: str
    name: str
    instructions: str                       # system prompt
    model: str                              # e.g. "claude-sonnet-4-7-2025-06-09"
    planner_model: str                       # model for Planner/Reflector (can differ from Executor)
    tools: list[str]                        # list of available skill names
    skip_reflector_for_simple: bool = True  # auto-skip for single-query tasks
    max_replan_attempts: int = 2           # max Reflector → Planner retry cycles
    memory_enabled: bool = True             # conversation memory (Target Phase; required for chat)
    pii_masking_enabled: bool = True       # mask PII before LLM calls
```

Config is loaded from `tenants` DB table at startup and cached in Redis with a 5-minute TTL.

---

## 5. Tool Definitions

Tools live in `packages/skill-system/src/tools/` and are registered in the skill-gateway registry.
The Python Agent references them by name and dispatches via HTTP to skill-gateway.

### 5.1 Core CS Tools (Phase 1 — MVP)

| Tool ID | Description | External Call |
|---|---|---|
| `query_health` | Query a customer's health score, usage trend, support tickets, NPS, MRR, renewal | Internal DB call |
| `query_playbooks` | Retrieve the most relevant playbook(s) for a given signal type | Internal DB + vector search |
| `send_email` | Send a personalized email via SendGrid | skill-gateway → SendGrid |
| `send_slack` | Send a Slack DM to a CSM | skill-gateway → Slack API |
| `update_crm` | Write a note/update to Salesforce | skill-gateway → Salesforce |
| `schedule_meeting` | Send a Google Calendar invite | skill-gateway → Google Calendar |

### 5.2 Advanced Tools (Phase 2)

| Tool ID | Description |
|---|---|
| `query_order` | Query tenant's order/usage system for account-level data |
| `generate_qbr` | Draft a QBR presentation (calls LLM + PDF generator) |
| `query_knowledge_base` | Semantic search over tenant's playbooks/documents via pgvector |
| `escalate_to_csm` | Mark a customer as escalated, assign to a human CSM |
| `initiate_refund` | Submit a refund request (→ LangGraph approval workflow if > threshold) |

### Tool Schema Definition

Each tool is defined as a Pydantic model with a JSON Schema. The JSON Schema is extracted
and passed to the LLM for tool-calling.

```python
# packages/skill-system/src/tools/query_health.py
from pydantic import BaseModel, Field
from typing import Optional
import httpx


class QueryHealthInput(BaseModel):
    """Input schema for query_health tool."""
    customer_id: str = Field(description="Customer UUID from the platform")
    tenant_id: str = Field(description="Tenant UUID for data isolation (auto-injected by executor)")


class QueryHealthOutput(BaseModel):
    """Output schema for query_health tool."""
    found: bool
    customer_id: Optional[str] = None
    health_score: Optional[float] = None
    usage_trend: Optional[dict] = None          # {"current": 40, "previous": 60, "pct_change": -33}
    support_ticket_count: Optional[int] = None
    nps: Optional[int] = None
    mrr: Optional[float] = None
    renewal_date: Optional[str] = None
    error: Optional[str] = None


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "query_health",
        "description": (
            "Query a customer's health profile. Returns health score, usage trend, "
            "support ticket count, NPS, MRR, and renewal date. "
            "Call this first to understand the customer's situation before taking action."
        ),
        "parameters": QueryHealthInput.model_json_schema(),
    },
}


async def execute_query_health(
    params: QueryHealthInput,
    ctx: "SessionContext",   # forward reference, injected by caller
) -> QueryHealthOutput:
    """
    Implementation: calls internal DB for customer health data.
    Returns QueryHealthOutput matching the schema above.
    """
    # Internal DB call (use packages/db/ SQLAlchemy session)
    ...


# packages/skill-system/src/tools/send_email.py
class SendEmailInput(BaseModel):
    recipient_email: str = Field(description="Recipient email address")
    subject: str = Field(description="Email subject line")
    body: str = Field(description="Email body in markdown format")
    sender_name: str = Field(description="Display name of the sender (e.g. 'Acme CS Team')")
    customer_id: str = Field(description="Customer UUID for tracking")


TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "send_email",
        "description": (
            "Send a personalized email to a customer. "
            "Requires: recipient_email, subject, body (markdown), sender_name. "
            "Do not call with raw PII — use masked values from the customer profile."
        ),
        "parameters": SendEmailInput.model_json_schema(),
    },
}
```

### Tool Registry

```python
# packages/skill-system/src/registry.py
from dataclasses import dataclass
from typing import Callable, Any
from .tools.query_health import TOOL_DEFINITION as QUERY_HEALTH, execute_query_health
from .tools.send_email import TOOL_DEFINITION as SEND_EMAIL, execute_send_email
# ... other tools


@dataclass
class ToolEntry:
    definition: dict          # JSON Schema for LLM function calling
    execute: Callable[..., Any]  # async function(params, ctx) -> output
    requires_sandbox: bool = True  # if False, runs in-process (DB calls only)


TOOL_REGISTRY: dict[str, ToolEntry] = {
    "query_health": ToolEntry(QUERY_HEALTH, execute_query_health, requires_sandbox=False),
    "send_email": ToolEntry(SEND_EMAIL, execute_send_email, requires_sandbox=True),
    # ... register all tools
}


def get_tool_definition(name: str) -> dict:
    """Return the JSON Schema definition for a tool (used by the LLM)."""
    return TOOL_REGISTRY[name].definition


def get_tools_for_tenant(tenant_config: AgentConfig) -> list[dict]:
    """Return all tool definitions available to a tenant."""
    return [get_tool_definition(name) for name in tenant_config.tools]
```

> **Tool description writing rules** (from lecture, adapted):
> - description must state preconditions ("call query_health first")
> - description must state what NOT to do ("do not include raw PII")
> - each field's `description=` must explain the parameter
> - vague descriptions cause wrong tool selection by the LLM

---

## 6. Orchestrator-Subagent Implementation

The Target Phase runtime is an Orchestrator-Subagent system. The older P-E-R terms map into this
architecture as follows:

| Previous P-E-R concept | Orchestrator-Subagent replacement |
|---|---|
| Planner | Orchestrator delegation planner creates `SubagentTask` objects |
| Executor | Delegation dispatcher runs subagents and their scoped ReAct loops |
| Reflector | `ComplianceCriticAgent` + Orchestrator reducer/policy checks |
| ReAct loop | Shared runtime primitive used inside subagents that need tools |

The following implementation snippets keep some legacy names for continuity, but the implementation
should place them under `orchestrator/`, `subagents/`, and `runtime/` as shown in §3.

### 6.1 Delegation Planner

The Planner is a single LLM call that decomposes a `CustomerSignal` into a list of `TaskPlan`.

```python
# apps/agent-service/src/agent/planner.py
import json
from packages.agent.types import CustomerSignal, TaskPlan, TaskType, LLMUsage, SessionContext
from packages.llm_gateway import chat_completions


async def run_planner(
    signal: CustomerSignal,
    ctx: SessionContext,
    available_skills: list[str],
    config: AgentConfig,
) -> tuple[list[TaskPlan], LLMUsage]:
    """
    Decompose a CustomerSignal into an ordered task plan via a single LLM call.

    Returns:
        plans: ordered list of TaskPlan objects
        usage: token usage for the planner LLM call
    """
    skill_list = ", ".join(available_skills) if available_skills else "(no skills available)"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Customer Success Planner. Given a customer signal, decompose it into "
                "an ordered task plan using the available skills.\n\n"
                "Rules:\n"
                "- Each task corresponds to exactly one skill\n"
                "- If task B depends on task A's result, add task A's id to B's depends_on\n"
                "- Read-only actions (query_*) are type: query; write actions (send_*, update_*) are type: mutation\n"
                "- Maximum 5 tasks per plan\n"
                "- For simple signals (single check), return a 1-task plan — Executor will skip Reflector\n"
                "- Output a valid JSON object with a 'plans' key (list) and a 'reasoning' key (str)"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Signal: {signal.signal_text}\n"
                f"Customer ID: {signal.customer_id}\n"
                f"Tenant ID: {signal.tenant_id}\n"
                f"Health score: {signal.health_score or 'unknown'}\n"
                f"Available skills: {skill_list}\n"
            ),
        },
    ]

    response = await chat_completions.create(
        model=config.planner_model,
        messages=messages,
        response_format={"type": "json_object"},
        tenant_id=ctx.tenant_id,
        trace_name="planner",
        trace_metadata={"signal_id": ctx.signal_id},
    )

    content = response.choices[0].message.content
    parsed = json.loads(content)

    plans = [TaskPlan(**p) for p in parsed["plans"]]
    usage = LLMUsage(
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )

    return plans, usage
```

### 6.2 Core ReAct Loop

The heart of the Python Agent is the **ReAct loop** (Reasoning + Acting). This is the equivalent
of Mastra's `generate()`/`stream()` tool-calling mechanism, implemented directly with the openai SDK.

```python
# apps/agent-service/src/agent/react_loop.py
import json
import asyncio
from typing import AsyncGenerator, Optional
from openai import AsyncStream
from packages.agent.types import (
    CustomerSignal, SessionContext, TaskResult, TaskPlan,
    LLMUsage, AgentConfig,
)
from packages.llm_gateway import chat_completions
from packages.agent.tool_caller import dispatch_tool_call
from packages.agent import registry as tool_registry


MAX_REACT_STEPS = 10   # prevent infinite loops


class ReActLoop:
    """
    ReAct (Reasoning + Acting) loop: the core of the Python Agent.

    Each iteration:
    1. LLM generates a response (may include a tool call)
    2. If tool call: execute it, append result to messages, repeat
    3. If text response: return it

    This replaces what Mastra's Agent.generate() / agent.stream() do internally.
    """

    def __init__(
        self,
        signal: CustomerSignal,
        ctx: SessionContext,
        config: AgentConfig,
        initial_plans: list[TaskPlan],
    ):
        self.signal = signal
        self.ctx = ctx
        self.config = config
        self.plans = initial_plans
        self.messages: list[dict] = []
        self.tool_results: list[TaskResult] = []
        self.total_usage = LLMUsage()

    def _build_system_prompt(self) -> str:
        """Build the system prompt from config.instructions + available tools."""
        skill_docs = []
        for name in self.config.tools:
            tool_def = tool_registry.get_tool_definition(name)
            func = tool_def["function"]
            skill_docs.append(
                f"- {func['name']}: {func['description']}\n"
                f"  Parameters: {json.dumps(func['parameters'], indent=2)}"
            )

        return (
            f"{self.config.instructions}\n\n"
            f"Available tools:\n" + "\n".join(skill_docs) + "\n\n"
            "You have access to these tools. Use them to fulfill the user's request.\n"
            "Always respond with a tool call or a text answer. Do not hallucinate tool parameters."
        )

    async def run(self) -> tuple[str, list[TaskResult], LLMUsage]:
        """
        Run the ReAct loop until the LLM produces a text answer.

        Returns:
            text: the final text response
            tool_results: all tool execution results
            total_usage: cumulative token usage
        """
        self.messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Tenant: {self.ctx.tenant_id}\n"
                    f"Customer: {self.signal.customer_id}\n"
                    f"Signal: {self.signal.signal_text}\n"
                    f"Health score: {self.signal.health_score or 'unknown'}\n"
                ),
            },
        ]

        for step in range(MAX_REACT_STEPS):
            response = await chat_completions.create(
                model=self.config.model,
                messages=self.messages,
                tools=[tool_registry.get_tool_definition(name) for name in self.config.tools],
                tenant_id=self.ctx.tenant_id,
                trace_name=f"react_step_{step}",
                trace_metadata={"signal_id": self.ctx.signal_id, "step": step},
            )

            self.total_usage.prompt_tokens += response.usage.prompt_tokens
            self.total_usage.completion_tokens += response.usage.completion_tokens

            message = response.choices[0].message

            # Case 1: text response — loop terminates
            if message.content and (not message.tool_calls):
                self.messages.append({"role": "assistant", "content": message.content})
                return message.content, self.tool_results, self.total_usage

            # Case 2: tool calls — execute each, append results
            if message.tool_calls:
                self.messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in message.tool_calls
                    ],
                })

                # Execute all tool calls in this turn (can be parallel)
                tool_tasks = [
                    self._execute_tool_call(tc)
                    for tc in message.tool_calls
                ]
                results = await asyncio.gather(*tool_tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        # Log error, continue loop
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": "unknown",
                            "content": json.dumps({"error": str(result)}),
                        })
                    # tool result appended in _execute_tool_call

            # Case 3: no content, no tool calls — unexpected, terminate
            if not message.content and not message.tool_calls:
                return "[No response from model]", self.tool_results, self.total_usage

        # Max steps reached
        return (
            "[Agent reached maximum ReAct steps. Please simplify the request.]",
            self.tool_results,
            self.total_usage,
        )

    async def _execute_tool_call(self, tool_call) -> TaskResult:
        """Execute a single tool call and append the result to messages."""
        tool_name = tool_call.function.name
        try:
            params = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            err = f"Invalid JSON arguments for {tool_name}: {tool_call.function.arguments}"
            self.messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps({"error": err})})
            return TaskResult(task_id=tool_name, success=False, error=err)

        # Inject tenant_id into params if not present
        if "tenant_id" not in params:
            params["tenant_id"] = self.ctx.tenant_id

        try:
            result_data = await dispatch_tool_call(tool_name, params, self.ctx)
            content = json.dumps(result_data)
            success = True
        except Exception as exc:
            content = json.dumps({"error": str(exc)})
            success = False

        self.messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})

        return TaskResult(
            task_id=tool_name,
            success=success,
            data=json.loads(content) if success else None,
            error=None if success else content,
        )
```

### 6.3 Tool Dispatcher

```python
# apps/agent-service/src/agent/tool_caller.py
import httpx
from packages.agent.types import SessionContext


async def dispatch_tool_call(
    tool_name: str,
    params: dict,
    ctx: SessionContext,
) -> dict:
    """
    Dispatch a tool call. Routes to:
    - In-process execution for DB-only tools (query_health, etc.)
    - skill-gateway HTTP API for external API tools (send_email, etc.)

    This is the sandbox boundary. External API calls never happen outside skill-gateway.
    """
    from packages.skill_system.src.registry import TOOL_REGISTRY

    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        raise ValueError(f"Unknown tool: {tool_name}")

    if entry.requires_sandbox:
        return await _dispatch_to_skill_gateway(tool_name, params, ctx)
    else:
        return await entry.execute(params, ctx)


async def _dispatch_to_skill_gateway(
    tool_name: str,
    params: dict,
    ctx: SessionContext,
) -> dict:
    """
    Call the skill-gateway HTTP API to execute a sandboxed skill.

    Request:
        POST {skill_gateway_url}/run
        {
            "skill": "send_email",
            "params": {...},
            "tenant_id": "...",
            "trace_id": "..."
        }

    Response:
        {"success": true, "data": {...}}
        or
        {"success": false, "error": "..."}
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{skill_gateway_url}/run",
            json={
                "skill": tool_name,
                "params": params,
                "tenant_id": ctx.tenant_id,
                "trace_id": ctx.trace_id,
            },
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("success"):
            raise RuntimeError(f"Skill {tool_name} failed: {result.get('error')}")

        return result["data"]
```

### 6.4 Executor (Task-level, above ReAct)

The Executor layer sits above the ReAct loop. It handles:

1. **Dependency injection** — pre-execute a dependency task if not yet done
2. **Result passing** — inject dependency results into the current task's context
3. **Partial execution** — skip dependent tasks if a dependency failed

```python
# apps/agent-service/src/agent/executor.py
import asyncio
from packages.agent.types import (
    TaskPlan, TaskResult, SessionContext, TaskType, AgentConfig,
)
from packages.agent.react_loop import ReActLoop


async def execute_tasks(
    plans: list[TaskPlan],
    ctx: SessionContext,
    config: AgentConfig,
    signal: "CustomerSignal",   # forward ref
) -> list[TaskResult]:
    """
    Execute tasks in dependency order using a topological-sort strategy.

    - Tasks with no dependencies are parallelized via asyncio.gather
    - Tasks with satisfied dependencies are executed after dependencies complete
    - If a dependency fails, dependent tasks are skipped (not executed)

    Time complexity is O(n²) with n≤5 — not a bottleneck.
    The real win: parallel execution reduces total time from n*T_llm to depth*T_llm.
    """
    result_map: dict[str, TaskResult] = {}
    completed: set[str] = set()
    pending = list(plans)
    all_results: list[TaskResult] = []

    while pending:
        # Find all tasks whose dependencies are satisfied
        ready = [p for p in pending if p.is_ready(completed)]

        if not ready:
            # Remaining tasks have unsatisfied dependencies — stop
            break

        # Execute ready tasks in parallel
        tasks = [
            _execute_single(plan, result_map, ctx, config, signal)
            for plan in ready
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for plan, result in zip(ready, batch_results):
            if isinstance(result, Exception):
                task_result = TaskResult(task_id=plan.id, success=False, error=str(result))
            else:
                task_result = result

            result_map[plan.id] = task_result
            all_results.append(task_result)

            if task_result.success:
                completed.add(plan.id)
            else:
                # On failure: do NOT execute tasks that depend on this one
                # (they remain in pending and will be skipped)
                pass

        # Remove executed from pending
        pending = [p for p in pending if p.id not in completed]

    return all_results


async def _execute_single(
    plan: TaskPlan,
    result_map: dict[str, TaskResult],
    ctx: SessionContext,
    config: AgentConfig,
    signal: "CustomerSignal",
) -> TaskResult:
    """
    Execute a single TaskPlan using the ReAct loop.

    Before running the loop, inject dependency results into the task description
    so the LLM has full context.
    """
    # Build enriched context: inject dependency results
    dependency_context = ""
    if plan.depends_on:
        dep_lines = []
        for dep_id in plan.depends_on:
            dep_result = result_map.get(dep_id)
            if dep_result:
                dep_lines.append(f"[{dep_id}] {dep_result.data or dep_result.error}")
        if dep_lines:
            dependency_context = "\n\nRelevant context from previous steps:\n" + "\n".join(dep_lines)

    # Run ReAct loop with enriched description
    enriched_signal = signal
    # (In practice, pass dependency_context to the ReAct loop's initial user message)

    loop = ReActLoop(
        signal=enriched_signal,
        ctx=ctx,
        config=config,
        initial_plans=[plan],
    )

    try:
        text, tool_results, usage = await loop.run()
        return TaskResult(
            task_id=plan.id,
            success=True,
            data={"text": text, "tool_results": [r.model_dump() for r in tool_results]},
            tokens_used=usage.total,
        )
    except Exception as exc:
        return TaskResult(
            task_id=plan.id,
            success=False,
            error=str(exc),
        )
```

### 6.5 Reflector

The Reflector evaluates whether the execution results satisfy the original intent.

```python
# apps/agent-service/src/agent/reflector.py
import json
from packages.agent.types import (
    CustomerSignal, TaskPlan, TaskResult, LLMUsage, SessionContext, AgentConfig,
)
from packages.llm_gateway import chat_completions


async def run_reflector(
    signal: CustomerSignal,
    plans: list[TaskPlan],
    results: list[TaskResult],
    ctx: SessionContext,
    config: AgentConfig,
) -> tuple[bool, str, LLMUsage]:
    """
    Evaluate whether execution results fully satisfy the original intent.

    Returns:
        satisfied: True if results meet the intent
        feedback: explanation (used for replanning if not satisfied)
        usage: token usage
    """
    result_lines = [
        f"- {r.task_id}: {'SUCCESS' if r.success else 'FAILED'} | {json.dumps(r.data or r.error)}"
        for r in results
    ]

    messages = [
        {
            "role": "system",
            "content": (
                "You are a Result Validator. Determine whether the task execution results\n"
                "fully satisfy the customer's original intent.\n\n"
                "Respond with a valid JSON object: {\"satisfied\": true/false, \"feedback\": \"...\"}\n"
                "feedback should be 1-2 sentences explaining why."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Original signal: {signal.signal_text}\n"
                f"Planned tasks: {', '.join(p.description for p in plans)}\n"
                f"Execution results:\n" + "\n".join(result_lines) + "\n\n"
                "Did the execution fully satisfy the intent? If not, what is missing or wrong?"
            ),
        },
    ]

    response = await chat_completions.create(
        model=config.planner_model,   # Reflector uses the same model as Planner (fast/cheap)
        messages=messages,
        response_format={"type": "json_object"},
        tenant_id=ctx.tenant_id,
        trace_name="reflector",
        trace_metadata={"signal_id": ctx.signal_id},
    )

    content = response.choices[0].message.content
    parsed = json.loads(content)

    usage = LLMUsage(
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
    )

    return parsed["satisfied"], parsed["feedback"], usage


def should_skip_reflector(
    plans: list[TaskPlan],
    skip_enabled: bool,
) -> bool:
    """
    Skip Reflector when:
    - Only 1 task planned AND
    - The task is a QUERY (read-only) AND
    - skip_reflector_for_simple is True in config

    This saves 1 LLM call per simple interaction — significant cost savings.
    """
    return (
        len(plans) == 1
        and plans[0].type == TaskType.QUERY
        and skip_enabled
    )
```

### 6.6 Orchestrate (Top-Level)

```python
# apps/agent-service/src/workflow/orchestrate.py
import json
import asyncio
from packages.agent.types import (
    CustomerSignal, SessionContext, AgentConfig, AgentResponse,
    TaskType, TaskResult,
)
from packages.agent.planner import run_planner
from packages.agent.executor import execute_tasks
from packages.agent.reflector import run_reflector, should_skip_reflector
from packages.agent.workflow.langgraph import run_langgraph_workflow
from packages.llm_gateway import chat_completions


async def orchestrate(
    signal: CustomerSignal,
    ctx: SessionContext,
) -> AgentResponse:
    """
    Top-level P-E-R orchestration.

    Flow:
        1. Load tenant config
        2. Planner → list[TaskPlan]
        3. Decide: Python Agent or LangGraph?
        4. Executor → list[TaskResult]
        5. Reflector (maybe skipped)
        6. Return AgentResponse
    """
    # 1. Load tenant config
    config = await load_tenant_config(ctx.tenant_id)

    # 2. Planner
    plans, planner_usage = await run_planner(signal, ctx, config.tools, config)
    total_planner_tokens = planner_usage.total

    # 3. Decide: LangGraph or Python Agent?
    use_langgraph = (
        any(p.type == TaskType.MUTATION for p in plans)   # write ops need checkpoint
        or len(plans) > 3                                  # complex multi-step
        or signal.payload.get("requires_human_approval")   # explicit interrupt needed
    )

    if use_langgraph:
        # LangGraph workflow handles its own execution + checkpoint
        result = await run_langgraph_workflow(plans, signal, ctx)
        return AgentResponse(
            text=result.summary,
            tasks=result.task_results,
            planner_tokens=total_planner_tokens,
            reflector_tokens=0,
            skipped_reflector=True,
        )

    # 4. Python Agent path: Executor
    results = await execute_tasks(plans, ctx, config, signal)
    total_reflector_tokens = 0

    # 5. Reflector (maybe skipped)
    if should_skip_reflector(plans, config.skip_reflector_for_simple):
        final_text = _summarize_results(results)
        return AgentResponse(
            text=final_text,
            tasks=results,
            planner_tokens=total_planner_tokens,
            reflector_tokens=0,
            skipped_reflector=True,
        )

    # Run Reflector
    satisfied, feedback, reflector_usage = await run_reflector(
        signal, plans, results, ctx, config
    )
    total_reflector_tokens = reflector_usage.total

    # 6. Replan if not satisfied (up to max_replan_attempts)
    replan_count = 0
    current_plans = plans
    current_results = results

    while not satisfied and replan_count < config.max_replan_attempts:
        replan_count += 1

        # Call Planner again with Reflector feedback
        current_plans, replan_usage = await run_planner(
            signal, ctx, config.tools, config,
            # Pass feedback as extra context
        )
        total_planner_tokens += replan_usage.total

        # Re-execute with new plan
        current_results = await execute_tasks(current_plans, ctx, config, signal)

        # Re-evaluate
        satisfied, feedback, _ = await run_reflector(
            signal, current_plans, current_results, ctx, config
        )

    if not satisfied:
        # Escalate to human
        feedback = f"[Max replan attempts reached] {feedback}"

    final_text = _summarize_results(current_results)

    return AgentResponse(
        text=final_text,
        tasks=current_results,
        planner_tokens=total_planner_tokens,
        reflector_tokens=total_reflector_tokens,
        skipped_reflector=False,
        satisfied=satisfied,
        feedback=feedback,
    )


def _summarize_results(results: list[TaskResult]) -> str:
    """Concatenate all successful task results into a summary."""
    summaries = []
    for r in results:
        if r.success and r.data:
            summaries.append(json.dumps(r.data))
        elif not r.success:
            summaries.append(f"[{r.task_id} failed: {r.error}]")
    return "\n".join(summaries) if summaries else "Task completed with no output."


# Helper: load tenant config (cached in Redis)
_tenant_config_cache: dict[str, tuple[AgentConfig, float]] = {}
_CACHE_TTL_SECONDS = 300


async def load_tenant_config(tenant_id: str) -> AgentConfig:
    """Load tenant config from DB, cached in memory with 5-min TTL."""
    import time
    now = time.time()

    if tenant_id in _tenant_config_cache:
        config, cached_at = _tenant_config_cache[tenant_id]
        if now - cached_at < _CACHE_TTL_SECONDS:
            return config

    # TODO: load from DB (packages/db/)
    # config = await db.tenants.find_one({"tenant_id": tenant_id})
    config = AgentConfig(
        tenant_id=tenant_id,
        name="default",
        instructions="You are a Customer Success AI agent.",
        model="claude-sonnet-4-7-2025-06-09",
        planner_model="claude-haiku-4-20250514",
        tools=["query_health", "send_email", "send_slack"],
    )

    _tenant_config_cache[tenant_id] = (config, now)
    return config
```

---

## 7. Conversation Memory (Target Phase)

Conversation memory is part of the **Target Phase** (Phase 1). It persists per-customer
conversation history in Postgres so the Orchestrator has context across runs and across chat turns
(see §8, Customer Chat). The Orchestrator owns long-term memory writes; subagents receive only
bounded memory slices and return results, preventing memory pollution. Semantic recall via pgvector is optional and can be
toggled per tenant.

### Memory Design

```python
# packages/agent/src/memory.py
from packages.db import get_session  # SQLAlchemy session
from packages.redis import get_client  # Redis client
import json
from datetime import datetime, timedelta


class TenantMemory:
    """
    Per-tenant, per-user conversation memory.
    Stored in Postgres (messages table) + pgvector (embeddings).

    threadId format: {tenant_id}:{user_id}:{session_id}
    """
    TABLE_NAME = "agent_messages"

    def __init__(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        last_messages: int = 20,
        semantic_top_k: int = 5,
    ):
        self.thread_id = f"{tenant_id}:{user_id}:{session_id}"
        self.last_messages = last_messages
        self.semantic_top_k = semantic_top_k

    async def add_message(self, role: str, content: str, metadata: dict | None = None):
        """Append a message to the conversation history."""
        # Insert into Postgres messages table
        # Embed content via pgvector (optional semantic recall)
        ...

    async def get_messages(self) -> list[dict]:
        """
        Retrieve conversation history for this thread.
        1. Last N messages (recency)
        2. Top-K semantically similar messages (if semantic recall enabled)
        """
        # Deduplicate and merge
        ...
```

**Cost note**: semantic recall triggers an extra embedding call per message.
For a CS platform doing 1000 conversations/day, this is ~1000 extra embedding calls/day.
Track in Langfuse. Disable semantic recall if cost/quality tradeoff is poor.

---

## 8. Customer Chat (Target Phase)

Beyond the proactive, signal-triggered outreach that drives the rest of this plan, the agent
also supports a **synchronous, customer-facing chat** channel. Where the P-E-R loop is
triggered by a `CustomerSignal` (a detected event), chat is triggered by a **customer message**
and runs as a multi-turn conversation with streamed responses.

### 8.1 How Chat Differs from Signal-Driven Outreach

| Dimension | Signal-driven outreach (§1–§7) | Customer chat (this section) |
|---|---|---|
| Trigger | `CustomerSignal` (usage drop, renewal, ...) | Inbound customer message |
| Entry point | RQ job → `orchestrate()` | HTTP request → chat handler |
| Turn model | One-shot (plan → act → reflect) | Multi-turn, conversational |
| Response | Email / Slack / CRM action | Streamed text back to the customer |
| Memory | Optional context | **Required** — every turn reads/writes memory (§7) |
| Latency target | Seconds to minutes | Sub-second first token (streaming) |

Chat reuses the same core primitives — the Orchestrator (§6), the ReAct loop (§6.2), the tool registry (§5), and
conversation memory (§7). It does **not** re-run the full signal-outreach plan on every turn; for a
typical chat turn the Orchestrator delegates to `CustomerChatAgent`, optionally calls specialists,
runs `ComplianceCriticAgent`, then streams or returns the approved response.

### 8.2 Chat Data Types

```python
# packages/agent/src/chat_types.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, Literal


class ChatMessage(BaseModel):
    """A single message in a customer chat conversation."""
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Inbound customer chat request."""
    tenant_id: str
    customer_id: str
    session_id: str                       # groups messages into one conversation thread
    message: str                          # the customer's message text
    stream: bool = True                   # stream tokens back by default


class ChatResponse(BaseModel):
    """Non-streaming chat response (streaming uses SSE chunks of the same text)."""
    session_id: str
    reply: str
    tool_calls: list[str] = Field(default_factory=list)  # names of tools invoked this turn
    tokens_used: int = 0
```

### 8.3 Chat Handler (ReAct + Memory)

```python
# apps/agent-service/src/agent/chat.py
from typing import AsyncGenerator
from packages.agent.chat_types import ChatRequest, ChatResponse, ChatMessage
from packages.agent.memory import TenantMemory
from packages.agent.types import SessionContext, AgentConfig
from apps.agent_service.src.agent.react_loop import ReActLoop


async def handle_chat_turn(req: ChatRequest, config: AgentConfig) -> ChatResponse:
    """
    Handle one customer chat turn.

    Flow:
        1. Load conversation history from memory (§7)
        2. Append the new user message
        3. Run the ReAct loop with history + tools
        4. Persist both user + assistant messages back to memory
        5. Return the reply
    """
    ctx = SessionContext(
        tenant_id=req.tenant_id,
        user_id=req.customer_id,
        session_id=req.session_id,
        signal_id=f"chat:{req.session_id}",
    )

    memory = TenantMemory(req.tenant_id, req.customer_id, req.session_id)

    # 1-2. Load history and append the new message
    history = await memory.get_messages()
    await memory.add_message(role="user", content=req.message)

    # 3. Run ReAct loop seeded with history + the new message
    loop = ReActLoop.from_chat(history=history, user_message=req.message, ctx=ctx, config=config)
    reply, tool_results, usage = await loop.run()

    # 4. Persist the assistant reply
    await memory.add_message(role="assistant", content=reply)

    # 5. Return
    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        tool_calls=[r.task_id for r in tool_results],
        tokens_used=usage.total,
    )
```

> **Note:** `ReActLoop.from_chat(...)` is a thin constructor that seeds `self.messages` with the
> stored history instead of a single signal-derived prompt. The loop body (§6.2) is unchanged.

### 8.4 HTTP Endpoint (api-gateway)

Chat is exposed as a streaming HTTP endpoint. Because this endpoint is customer-facing, it
**must** enforce authentication and tenant scoping (see `packages/auth/`).

```python
# apps/api-gateway/src/routes/chat.py
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from packages.agent.chat_types import ChatRequest

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest, principal = Depends(require_authenticated_tenant)):
    """
    Customer chat endpoint.

    - Authenticated + tenant-scoped (principal.tenant_id must match req.tenant_id)
    - Streams the assistant reply as Server-Sent Events when req.stream is True
    """
    assert principal.tenant_id == req.tenant_id, "tenant mismatch"

    if req.stream:
        return StreamingResponse(stream_chat_turn(req), media_type="text/event-stream")
    # Non-streaming fallback
    resp = await handle_chat_turn(req, config=await load_tenant_config(req.tenant_id))
    return resp
```

> **Security:** this is the only inbound customer-facing surface in the plan. It requires JWT
> validation and tenant-scoped RBAC (`packages/auth/`), plus per-tenant rate limiting to prevent
> one customer from exhausting the LLM quota. PII masking (§13, Open Question / Phase hardening)
> applies to chat input before it reaches the LLM.

---

## 9. LangGraph Python Workflows

LangGraph Python is used for workflows that need **checkpoint + interrupt**:

- `refund.py` — refund approval with human-in-the-loop
- `qbr.py` — QBR generation (3-5 minutes, 20+ LLM calls)

### 9.1 Refund Approval Workflow

```python
# apps/agent-service/src/workflow/langgraph/refund.py
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt, Command
import asyncio


class RefundState(TypedDict):
    order_id: str
    amount: float
    reason: str
    approval_status: Literal["pending", "approved", "rejected"]
    refund_id: str | None
    signal_id: str


def validate_refund(state: RefundState) -> RefundState:
    """Validate the refund request exists and amount is valid."""
    # TODO: call internal refund validation API
    print(f"[refund] Validating refund: order={state['order_id']}, amount={state['amount']}")
    return state


def wait_for_approval(state: RefundState) -> RefundState:
    """
    Interrupt the workflow and wait for human approval.

    The workflow pauses here. External system (Slack approval bot) calls:
        graph.invoke(Command(resume={"approved": True}), thread_id=...)

    Returns with approval_status updated.
    """
    decision = interrupt({
        "message": f"Approval required: refund {state['order_id']} for {state['amount']}",
        "order_id": state["order_id"],
        "amount": state["amount"],
        "reason": state["reason"],
    })

    approved = decision.get("approved", False)
    return {"approval_status": "approved" if approved else "rejected"}


def execute_refund(state: RefundState) -> RefundState:
    """Execute the refund if approved."""
    if state["approval_status"] != "approved":
        print(f"[refund] Refund rejected, not executing.")
        return state

    refund_id = f"RF{int(asyncio.get_event_loop().time() * 1000)}"
    # Call skill-gateway → send refund API
    print(f"[refund] Executing refund: {refund_id}")
    return {"refund_id": refund_id}


def build_refund_graph(checkpointer: PostgresSaver) -> StateGraph:
    """
    Build and compile the refund approval workflow graph.
    """
    builder = StateGraph(RefundState)
    builder.add_node("validate", validate_refund)
    builder.add_node("wait_for_approval", wait_for_approval)
    builder.add_node("execute_refund", execute_refund)

    builder.set_entry_point("validate")
    builder.add_edge("validate", "wait_for_approval")
    builder.add_edge("wait_for_approval", "execute_refund")
    builder.add_edge("execute_refund", END)

    return builder.compile(checkpointer=checkpointer)


# Usage:
# checkpointer = PostgresSaver.from_conn_string(DATABASE_URL)
# await checkpointer.setup()
# graph = build_refund_graph(checkpointer)
#
# # First call: runs to interrupt
# thread_config = {"configurable": {"thread_id": f"refund-{signal_id}"}}
# result = await graph.invoke(
#     {"order_id": "67890", "amount": 1299, "reason": "defective", "approval_status": "pending", "refund_id": None, "signal_id": signal_id},
#     thread_config,
# )
# # result["__interrupt__"] is non-null — workflow is paused
#
# # After human approval via webhook:
# resume_result = await graph.invoke(
#     Command(resume={"approved": True}),
#     thread_config,   # same thread_id
# )
```

### 9.2 QBR Generation Workflow

```python
# apps/agent-service/src/workflow/langgraph/qbr.py
from typing import TypedDict, Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver


class QBRState(TypedDict):
    customer_id: str
    tenant_id: str
    signal_id: str
    health_data: dict | None
    sections: dict
    slides: list
    status: Literal["drafting", "review", "done"]


def gather_data(state: QBRState) -> QBRState:
    """Gather all customer data needed for QBR."""
    # Call query_health skill
    # Call query_playbooks skill
    # Aggregate results
    return {
        **state,
        "health_data": {...},
        "sections": {},
        "status": "drafting",
    }


def draft_executive_summary(state: QBRState) -> QBRState:
    """LLM call: write the executive summary section."""
    # ...
    return {"sections": {**state["sections"], "executive_summary": "..."}}


def draft_usage_section(state: QBRState) -> QBRState:
    """LLM call: write the usage analysis section."""
    return {"sections": {**state["sections"], "usage": "..."}}


def draft_recommendations(state: QBRState) -> QBRState:
    """LLM call: write the recommendations section."""
    return {"sections": {**state["sections"], "recommendations": "..."}}


def render_slides(state: QBRState) -> QBRState:
    """Render all sections into slide format (markdown or PDF)."""
    return {
        **state,
        "slides": [...],   # final slide deck
        "status": "done",
    }


def build_qbr_graph(checkpointer: PostgresSaver) -> StateGraph:
    builder = StateGraph(QBRState)
    builder.add_node("gather_data", gather_data)
    builder.add_node("draft_executive_summary", draft_executive_summary)
    builder.add_node("draft_usage_section", draft_usage_section)
    builder.add_node("draft_recommendations", draft_recommendations)
    builder.add_node("render_slides", render_slides)

    builder.set_entry_point("gather_data")
    # All draft nodes can run sequentially or partially in parallel
    builder.add_edge("gather_data", "draft_executive_summary")
    builder.add_edge("draft_executive_summary", "draft_usage_section")
    builder.add_edge("draft_usage_section", "draft_recommendations")
    builder.add_edge("draft_recommendations", "render_slides")
    builder.add_edge("render_slides", END)

    return builder.compile(checkpointer=checkpointer)
```

---

## 10. Integration Points

### 10.1 RQ Worker (`rq_worker.py`)

```python
# apps/agent-service/src/rq_worker.py
# BEFORE: inline decision logic
# AFTER: call orchestrate()

async def process_job(job):
    from packages.agent.types import CustomerSignal, SessionContext
    from packages.agent.workflow.orchestrate import orchestrate

    signal = CustomerSignal(**job.payload)
    ctx = SessionContext(
        tenant_id=signal.tenant_id,
        user_id=job.metadata.get("user_id", ""),
        session_id=job.id,
        signal_id=signal.id,
        trace_id=job.metadata.get("trace_id"),
    )

    response = await orchestrate(signal, ctx)

    # Log results to DB
    await log_agent_run(
        signal_id=signal.id,
        tenant_id=signal.tenant_id,
        text=response.text,
        tasks=response.tasks,
        planner_tokens=response.planner_tokens,
        reflector_tokens=response.reflector_tokens,
        skipped_reflector=response.skipped_reflector,
    )
```

### 10.2 Skill Gateway Protocol

The skill-gateway exposes a single HTTP API. Both Python Agent tool dispatch and LangGraph
activities call this endpoint.

**Request:**

```http
POST {SKILL_GATEWAY_URL}/run
Content-Type: application/json

{
  "skill": "send_email",
  "params": {
    "recipient_email": "user@example.com",
    "subject": "Your Monthly Report",
    "body": "Hello, here is your report...",
    "sender_name": "Acme CS Team",
    "customer_id": "cust-abc123",
    "tenant_id": "tenant-xyz"
  },
  "tenant_id": "tenant-xyz",
  "trace_id": "trace-abc123"
}
```

**Success Response:**

```json
{
  "success": true,
  "data": {
    "message_id": "msg-123",
    "sent_at": "2026-06-29T10:00:00Z"
  }
}
```

**Error Response:**

```json
{
  "success": false,
  "error": "Recipient email address rejected by SendGrid"
}
```

### 10.3 LLM Gateway Integration

All LLM calls go through `packages/llm-gateway/`. The agent engine **never** calls OpenAI/Anthropic directly.

```python
# packages/llm-gateway/src/__init__.py (new)
# Extend existing llm_gateway with agent-specific methods

class AgentChatCompletions:
    """High-level interface for agent LLM calls."""

    async def create(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        tenant_id: str = "",
        trace_name: str = "",
        trace_metadata: dict | None = None,
        **kwargs,
    ) -> ChatCompletion:
        """
        Create a chat completion via llm-gateway.

        This method:
        1. Routes to the correct model provider
        2. Applies PII masking if enabled
        3. Records token usage for billing
        4. Emits Langfuse trace
        """
        ...

chat_completions = AgentChatCompletions()
```

### 10.4 Observability (Langfuse Python SDK)

```python
# packages/observability/src/langfuse.py (extend existing)
from langfuse import Langfuse
langfuse = Langfuse()

# In apps/agent-service/src/agent/planner.py:
langfuse.trace(
    name="per_run",
    tenant_id=ctx.tenant_id,
    user_id=ctx.user_id,
    metadata={"signal_id": ctx.signal_id},
).as_future()

# Span: Planner
with langfuse.span(name="planner", input=user_message, output=plans_dict):
    ...

# Span: each task result
for task_result in results:
    with langfuse.span(
        name=f"task.{task_result.task_id}",
        input=task_result.description,
        output=task_result.data,
    ):
        ...

# Span: Reflector
with langfuse.span(name="reflector", input=plans, output={"satisfied": satisfied, "feedback": feedback}):
    ...

# Metadata: token totals
trace.metadata["planner_tokens"] = response.planner_tokens
trace.metadata["reflector_tokens"] = response.reflector_tokens
```

---

## 11. Implementation Phases

### Target Phase (Core Agent, 4-6 weeks)

> **Interim approach (gateways deferred):** Phase 1 calls the LLM provider directly through a
> thin `openai` SDK wrapper (`llm_client.py`) and executes all tools **in-process**. The dedicated
> **LLM Gateway** (multi-provider routing, caching, circuit breaking, billing) and **Skill Gateway**
> (sandboxed external skill execution) are moved to "Future Plan — Gateways" below. Phase 1 code
> imports the local `llm_client` shim; when the LLM Gateway lands, the import is swapped for
> `packages.llm_gateway` with no change to the orchestrator/subagent call sites.
Phase 1 deliverables (Core Agent):

- [ ] `packages/agent/src/types.py` — Pydantic models: `TaskPlan`, `TaskResult`, `CustomerSignal`, `SessionContext`, `AgentResponse`, `LLMUsage`
- [ ] `packages/agent/src/config.py` — `AgentConfig` per tenant
- [ ] `packages/skill-system/src/registry.py` — `TOOL_REGISTRY`, `get_tool_definition()`, `get_tools_for_tenant()`
- [ ] Core tools: `query_health`, `send_email`, `send_slack` with proper Pydantic schemas (executed **in-process** in Phase 1)
- [ ] `apps/agent-service/src/agent/llm_client.py` — thin `openai` SDK wrapper (direct provider call + Langfuse tracing), interim stand-in for the LLM Gateway
- [ ] `packages/agent/src/orchestration_types.py` — `AgentInput`, `OrchestratorPlan`, `FinalDecision`
- [ ] `packages/agent/src/subagent_types.py` — `AgentRole`, `SubagentTask`, `SubagentResult`
- [ ] `apps/agent-service/src/agent/orchestrator/orchestrator.py` — `OrchestratorAgent` owns context, memory, policy, and final response
- [ ] `apps/agent-service/src/agent/orchestrator/planner.py` — delegation planner creates `SubagentTask` objects
- [ ] `apps/agent-service/src/agent/orchestrator/reducer.py` — merges `SubagentResult` objects into `FinalDecision`
- [ ] `apps/agent-service/src/agent/subagents/health_analysis.py` — health/risk specialist
- [ ] `apps/agent-service/src/agent/subagents/playbook_retrieval.py` — playbook/RAG specialist
- [ ] `apps/agent-service/src/agent/subagents/outreach_draft.py` — customer-facing draft specialist
- [ ] `apps/agent-service/src/agent/subagents/customer_chat.py` — customer chat specialist
- [ ] `apps/agent-service/src/agent/subagents/compliance_critic.py` — safety/policy critic
- [ ] `apps/agent-service/src/agent/runtime/react_loop.py` — shared ReAct loop for tool-using subagents
- [ ] `apps/agent-service/src/agent/runtime/tool_caller.py` — tool dispatcher (**in-process only** in Phase 1; skill-gateway routing deferred)
- [ ] `apps/agent-service/src/agent/orchestrator/planner.py` — delegation planner with skip-critic logic
- [ ] `apps/agent-service/src/agent/runtime/delegation.py` — subagent dispatcher with dependency-aware execution
- [ ] `apps/agent-service/src/agent/subagents/compliance_critic.py` — critic with skip logic
- [ ] `apps/agent-service/src/workflow/orchestrate.py` — top-level entry point accepting `AgentInput` (`CustomerSignal` or `ChatMessage`)
- [ ] `apps/agent-service/src/rq_worker.py` — call `orchestrate()` instead of inline logic
- [ ] Langfuse tracing for Orchestrator and all subagent calls
- [ ] Unit tests: Orchestrator delegation plan, subagent result merge, critic approval logic
- [ ] Unit tests: chat turn reads/writes memory; multi-turn context is preserved
- [ ] `packages/agent/src/memory.py` — conversation memory with Postgres (+ optional pgvector semantic recall)
- [ ] `packages/agent/src/chat_types.py` — `ChatMessage`, `ChatRequest`, `ChatResponse` Pydantic models
- [ ] `apps/agent-service/src/agent/chat.py` — `handle_chat_turn()` (ReAct + memory) and streaming variant
- [ ] `apps/api-gateway/src/routes/chat.py` — authenticated, tenant-scoped streaming chat endpoint

### Future Plan — Gateways (LLM Gateway + Skill Gateway)

These two gateways are **not built in Phase 1**. Phase 1 runs on the `llm_client` shim and
in-process tool execution. This phase introduces the dedicated gateways and migrates the agent
onto them.

- [ ] `packages/llm-gateway/src/__init__.py` — `AgentChatCompletions` class with billing + tracing; replaces the Phase 1 `llm_client` shim (same call signature, so orchestrator/subagent code is unchanged)
- [ ] `packages/llm-gateway/src/router.py` — `model_for_agent_task()` per-request model routing
- [ ] `packages/llm-gateway/src/cache.py` — prompt/semantic caching (Redis + pgvector)
- [ ] `packages/llm-gateway/src/circuit.py` — circuit breaker (state in Redis)
- [ ] `apps/skill-gateway/src/index.py` — sandboxed skill runner exposing the `POST /run` HTTP API
- [ ] Extend `apps/agent-service/src/agent/runtime/tool_caller.py` to route `requires_sandbox` tools to skill-gateway over HTTP
- [ ] Migrate write tools (`send_email`, `send_slack`, `update_crm`, ...) from in-process to skill-gateway sandbox execution
- [ ] Integration tests: LLM Gateway routing/caching; skill-gateway sandbox isolation

### Phase 2 — Advanced Capabilities (Future Plan)

- [ ] `packages/agent/src/memory.py` — conversation memory enhancements: pgvector semantic recall + retention policy (base memory ships in Target Phase §7)
- [ ] Advanced tools: `query_knowledge_base`, `generate_qbr`, `schedule_meeting`, `escalate_to_csm`
- [ ] LangGraph Python `refund.py` — refund approval with interrupt + PostgresSaver checkpointer
- [ ] LangGraph Python `qbr.py` — QBR generation with checkpoint
- [ ] Multi-tenant agent config loading from DB (replace hardcoded `load_tenant_config`)
- [ ] Integration tests: full refund approval cycle with mock interrupt resume

### Phase 3 — Production Hardening (Future Plan)

- [ ] Token accounting per tenant per model (integrate with llm-gateway billing, was Phase 3, keep here)
- [ ] Circuit breaker per skill (skill-gateway already has; wire it up)
- [ ] Rate limiting per tenant per skill
- [ ] PII masking middleware in `tool_caller.py` (apply `packages/auth/` masking before skill calls)
- [ ] Audit log: every task execution written to `task_history` table
- [ ] Replan loop with attempt limit and alerting
- [ ] Load testing: 100 concurrent agent runs, measure p95 latency

---

## 12. Testing Strategy

### Unit Tests (pytest + pytest-asyncio)

```python
# tests/test_planner.py
import pytest
from packages.agent.types import CustomerSignal, TaskType
from apps.agent_service.src.agent.planner import run_planner


@pytest.mark.asyncio
async def test_planner_decomposes_usage_drop():
    signal = CustomerSignal(
        tenant_id="t1",
        customer_id="c1",
        type="usage_drop",
        payload={"pct": 40, "health_score": 35},
    )
    ctx = SessionContext(tenant_id="t1", user_id="u1", session_id="s1", signal_id=signal.id)
    config = AgentConfig(tenant_id="t1", name="test", instructions="", model="claude-haiku", planner_model="claude-haiku", tools=["query_health", "send_email"])

    plans, usage = await run_planner(signal, ctx, config.tools, config)

    assert len(plans) == 2
    assert plans[0].skill == "query_health"
    assert plans[1].skill == "send_email"
    assert plans[1].depends_on == [plans[0].id]


@pytest.mark.asyncio
async def test_planner_single_query_plan():
    signal = CustomerSignal(
        tenant_id="t1", customer_id="c1", type="health_check", payload={}
    )
    ctx = SessionContext(tenant_id="t1", user_id="u1", session_id="s1", signal_id=signal.id)
    config = AgentConfig(tenant_id="t1", name="test", instructions="", model="claude-haiku", planner_model="claude-haiku", tools=["query_health"])

    plans, _ = await run_planner(signal, ctx, config.tools, config)

    assert len(plans) == 1
    assert plans[0].type == TaskType.QUERY


# tests/test_executor.py
@pytest.mark.asyncio
async def test_executor_parallelizes_independent_tasks():
    plans = [
        TaskPlan(id="a", description="...", type=TaskType.QUERY),
        TaskPlan(id="b", description="...", type=TaskType.QUERY),
    ]
    results = await execute_tasks(plans, ctx, config, signal)
    assert len(results) == 2
    assert all(r.success for r in results)


@pytest.mark.asyncio
async def test_executor_respects_dependencies():
    plans = [
        TaskPlan(id="a", description="...", type=TaskType.QUERY),
        TaskPlan(id="b", depends_on=["a"], description="...", type=TaskType.QUERY),
    ]
    results = await execute_tasks(plans, ctx, config, signal)
    # "a" executes first; "b" gets "a"'s result injected into its prompt
    assert len(results) == 2


# tests/test_reflector.py
def test_should_skip_reflector_simple_query():
    plans = [TaskPlan(id="a", type=TaskType.QUERY)]
    assert should_skip_reflector(plans, skip_enabled=True) is True


def test_should_not_skip_reflector_mutation():
    plans = [TaskPlan(id="a", type=TaskType.MUTATION)]
    assert should_skip_reflector(plans, skip_enabled=True) is False


def test_should_not_skip_reflector_multi_task():
    plans = [
        TaskPlan(id="a", type=TaskType.QUERY),
        TaskPlan(id="b", type=TaskType.QUERY),
    ]
    assert should_skip_reflector(plans, skip_enabled=True) is False
```

### Integration Tests

- End-to-end with mock skill-gateway: signal → orchestrate() → email sent
- LangGraph workflow: full refund approval cycle with mock interrupt resume
- Multi-tenant isolation: verify tenant A's data never reaches tenant B's agent

### Load Tests

- Simulate 100 concurrent `orchestrate()` calls
- Measure: Planner p95 latency, Executor p95 latency, total end-to-end time
- Target: < 5s end-to-end for simple 1-tool tasks, < 30s for multi-tool

---

## 13. Open Questions

1. **LLM model per orchestrator/subagent role**: Should Orchestrator, critic, and specialist subagents use the same model, or cheaper/faster models per role
   (for example Haiku for classification/critic and Sonnet for complex execution)? Keep per-role model config as the default.

2. **Skill-gateway protocol**: HTTP POST is simple but adds ~50-200ms latency per tool call.
   Consider a shared Redis stream for lower-latency skill dispatch if latency becomes a concern.
3. **Memory retention policy**: How many days of conversation history per customer?
   Affects Postgres storage and embedding costs.
4. **Redelegation/retry threshold**: When `ComplianceCriticAgent` or the reducer says "not satisfied", how many retries before
   escalating to a human? Current plan: configurable `max_replan_attempts` (default 2).
5. **LangGraph vs Temporal for long waits**: LangGraph Python interrupt is clean for < 1 hour waits.
   Temporal is more battle-tested for > 1 hour. For 48-hour reply waits, should we use
   LangGraph interrupt or Temporal workflow with a signal channel? Recommendation: Temporal
   for waits > 1 hour, LangGraph for human approval interruptions.
6. **Python import paths vs hyphenated directories**: the current dirs (`packages/llm-gateway`,
   `packages/skill-system`, `apps/agent-service`) use hyphens, which are invalid in Python module
   names. Two options: (a) rename the package source dirs to underscores
   (`packages/llm_gateway`, ...) and import directly, or (b) keep hyphenated repo dirs but define
   installable distributions in `pyproject.toml` that map import names to underscore packages
   (e.g. `[tool.setuptools] packages = ["llm_gateway"]` with `package-dir`). Recommendation:
   option (a) for simplicity in a monorepo, or a `src/`-per-package layout installed as editable
   packages. This must be decided before any Phase 1 code is written, since every snippet in this
   plan assumes underscore import paths.

---

## 14. Reflection: Changes from Original Plan

The original plan referenced TypeScript/Mastra throughout. This Python rewrite makes the following
substantive changes:

### Framework changes

- **Removed**: `apps/agent-service/src/agent/mastra/` directory and all Mastra references
- **Removed**: TypeScript code samples; all snippets are now Python (Pydantic, openai SDK, httpx, asyncio)
- **Added**: `react_loop.py` — a direct ReAct implementation replacing what Mastra's Agent class does
- **Added**: `tool_caller.py` — the tool dispatch layer (in-process vs skill-gateway HTTP)
- **LangGraph**: Kept but rewritten in Python syntax (typed state, StateGraph builder, PostgresSaver)

### Type system changes

- **Removed**: Zod schemas and TypeScript types
- **Added**: Pydantic v2 models throughout — `BaseModel`, `Field`, `model_json_schema()`
- `PlannerOutput` → replaced by returning `tuple[list[TaskPlan], LLMUsage]` directly
- `TaskPlan` field `depends_on` renamed from `dependsOn` (Python convention)
- `AgentConfig.skip_reflector_for_simple` added from reflection

### File layout changes

- `packages/agent-core/` → `packages/agent/` (singular noun, matches existing conventions)
- `mastra/` subdir removed; replaced by `react_loop.py` + `tool_caller.py`
- `langgraph/` moved to `apps/agent-service/src/workflow/langgraph/`

### Integration changes

- **Skill gateway**: Fully defined HTTP API contract (request/response schema) — the original
  plan mentioned "HTTP POST" but had no defined contract
- **LLM gateway**: `AgentChatCompletions` class defined — wraps existing `chat_completions`
  with agent-specific needs (tool schemas, tracing, billing)
- **Observability**: Langfuse Python SDK used instead of TypeScript tracing API

### Content additions

- **Section 6.2**: Full `ReActLoop` implementation — the most critical Python-native component
- **Section 6.3**: `tool_caller.py` dispatcher with in-process vs skill-gateway routing
- **Section 10.2**: Skill gateway HTTP API contract (fully defined request/response JSON)
- **Section 14 (this section)**: Explicit mapping of every TypeScript reference to Python equivalent

### Content removed

- `examples/src/planner-executor.ts` reference (TypeScript demo)
- Mastra's `generate()` / `stream()` method examples
- `@mastra/core`, `@ai-sdk/anthropic` imports
- Zod schema imports and `.describe()` chains
- TypeScript interface syntax throughout

