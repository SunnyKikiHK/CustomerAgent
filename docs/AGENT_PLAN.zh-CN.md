# 智能体实现计划

> 本文档将智能体引擎设计映射到现有的、基于 Python 的 CustomerAgent 代码库上。
> 它是 `apps/agent-service/` 和 `packages/agent/` 包的实现蓝图。
> 请与 `docs/README.md` 一起阅读，以获取完整的系统背景。

---

## 1. 范围

智能体引擎处理平台中**所有由 AI 驱动的决策与动作执行**。
它的职责：

- 分类进入的客户信号（使用率下降、支持工单、NPS 变化、续约日期）
- 决定采取什么动作（发送邮件、发 Slack、安排会议、更新 CRM、生成 QBR）
- 安全地执行 skill（通过 skill-gateway 进行沙箱化的外部 API 调用）
- 评估结果，并跟进或升级
- 为长时间运行的工作流持久化执行状态（48 小时等待、人在环审批）

**这里不在范围内的部分**（由其他 packages/文档处理）：

- 接收 webhook 并入队任务的 HTTP 网关（`apps/api-gateway`）
- 路由/限流/缓存 LLM 调用的 LLM 网关（`packages/llm-gateway`）
- 实际运行外部 API 调用的 skill 沙箱（`apps/skill-gateway`、`packages/skill-system`）
- 会话状态机（`packages/session`）
- 可观测性层（`packages/observability`）

## 2. 架构决策：Orchestrator-Subagent Runtime vs LangGraph Workflows

### Orchestrator-Subagent 模式

主智能体运行时采用 **Claude Code 风格的 Orchestrator-Subagent 模式**：

- **OrchestratorAgent** 拥有请求、租户上下文、记忆、策略以及最终响应。
- **Subagent** 只接收边界清晰的任务、有限上下文和受限工具集。
- Subagent 返回结构化的 `SubagentResult`，不直接拥有长期记忆，也不直接决定最终对客户可见的输出。
- Orchestrator 合并 subagent 结果，执行策略/critic 检查，决定被批准的动作，并写入记忆/审计日志。

共享的 ReAct 循环仍然存在，但它成为**子智能体运行时原语**，而不是顶层架构。

### 领域边界决策

`CustomerSignal` 和 `ChatMessage` 保持为**独立的领域对象**：

- `CustomerSignal` = 业务事件 / 自动化触发 / 异步工作流输入。
- `ChatMessage` = 客户对话轮次 / 同步聊天输入 / 带记忆的线程。

它们只在 Orchestrator 边界通过 `AgentInput` wrapper 相遇。不要把它们合并成一个通用 payload 模型；
它们的生命周期、延迟要求、安全策略和记忆行为都不同。

### 决策矩阵

| 场景 | 运行时 | 原因 |
|---|---|---|
| 纯问答、意图分类 | Orchestrator → CustomerChatAgent | 低延迟、带记忆、支持流式响应 |
| 多工具链、≤ 5 步、< 30s | Orchestrator + 可使用 ReAct 的 subagent | 边界清晰的委托 + 受限工具 |
| 主动外联（来自 `CustomerSignal`） | Orchestrator → specialist subagents | health/playbook/draft/critic 可隔离 |
| 面向客户的聊天 | Orchestrator → CustomerChatAgent → optional specialists | 使用 `ChatMessage`、记忆、流式响应 |
| 复杂多步（QBR 生成，> 30s） | LangGraph Python + subagents | 需要 checkpoint，长时间执行 |
| 人在环审批（退款 > 阈值） | LangGraph Python | 需要 interrupt + checkpoint |
| 需要完整重放的合规/审计 | LangGraph Python + audit log | 可重放/可调试 |
| 长等待步骤（"等 48 小时回复"） | Temporal（已在技术栈中） | 进程崩溃恢复能力 |

### Orchestrator-Subagent 技术栈

对于常规智能体执行，我们采用不依赖 Mastra 的 Python 直接实现：

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

我们刻意**不**使用 Mastra Python。Orchestrator-Subagent runtime、delegation dispatcher
和 ReAct loop 直接用 Python + `openai` SDK 实现，使系统保持显式、可审计。

### LangGraph Python 技术栈

对于需要 **checkpoint + interrupt** 的工作流，我们使用 `langgraph`（官方 LangGraph Python SDK）：

```
langgraph StateGraph
  └─ node: "gather_data" → calls Orchestrator/subagents for bounded work
  └─ node: "wait_for_approval" → interrupt
  └─ node: "execute_action" → approved tool/action execution
```

### 决策树（从输入到运行时）

```
Input arrives
├── CustomerSignal?
│   └── Orchestrator → HealthAnalysisAgent → PlaybookRetrievalAgent → OutreachDraftAgent → ComplianceCriticAgent
├── ChatMessage?
│   └── Orchestrator → memory load → CustomerChatAgent → optional specialists → ComplianceCriticAgent
└── Long-running / approval / replay needed?
    └── LangGraph or Temporal owns durability; Orchestrator/subagents do bounded work inside steps
```

## 3. 文件布局

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
│   └── memory.py             # Tenant-scoped conversation memory (Phase 2)

packages/session/
├── src/
│   ├── state.py              # SessionState types (existing, may need extension)
│   └── workflow.py           # Temporal workflow definitions (existing)
│       ├── outreach.py       # Outreach + wait-for-reply + escalate workflow
│       └── refund.py          # Refund approval workflow (existing; may delegate to LangGraph)
```

> **导入路径注意事项（在 Phase 1 编码前必须解决）：** 下面的代码示例从
> `packages.llm_gateway`、`packages.skill_system`、`packages.agent`、`apps.agent_service`
> （下划线）导入，但真实目录是带连字符的（`packages/llm-gateway`、`packages/skill-system`、
> `apps/agent-service`），并且 `packages/agent/` 尚不存在。连字符在 Python 模块名中**不合法**，
> 因此这些导入按现状无法工作。解决方案的两个选项见开放问题 #6。

### Orchestrator-Subagent 目标布局

当前扁平的 `agent/` 布局应在目标阶段演进为以下结构：

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

**领域规则：** `CustomerSignal` 位于 `types.py`；`ChatMessage` 位于 `chat_types.py`。
它们是独立领域对象，只通过 `orchestration_types.py` 中的 `AgentInput` 在 Orchestrator 边界相遇。

### 对现有文件的关键改动

| 文件 | 改动 |
|---|---|
| `packages/db/src/schema.sql` | 新增 `task_history`、`planner_calls`、`reflector_calls` 表用于审计 |
| `apps/agent-service/src/rq_worker.py` | 导入并调用 `orchestrate()`，替代内联逻辑 |
| `packages/llm-gateway/src/router.py` | 新增 `model_for_agent_task()` 方法用于 P-E-R 模型路由 |
| `apps/skill-gateway/src/index.py` | skill-gateway 是 Python Agent 工具与 LangGraph 活动共同的执行后端 |
| `requirements.txt` | 新增 `openai`、`langgraph`、`pydantic`、`httpx` |

## 4. 数据模型

所有类型都使用 **Pydantic v2** 进行校验、序列化和 JSON Schema 生成
（JSON Schema 会传给 LLM 用于工具调用的参数校验）。

> **导入路径注意事项（在 Phase 1 编码前解决）：** 下面的代码示例从
> `packages.llm_gateway`、`packages.skill_system`、`packages.agent`、`apps.agent_service`
> （下划线）导入，但真实目录是带连字符的（`packages/llm-gateway`、`packages/skill-system`、
> `apps/agent-service`），并且 `packages/agent/` 尚不存在。连字符在 Python 模块名中**不合法**，
> 因此这些导入按现状无法工作。两个解决选项见开放问题 #6。

### 智能体领域分离

本计划保持两个输入领域分离：

- `CustomerSignal` 是触发自动化的业务事件。
- `ChatMessage` 是客户聊天会话中的一轮对话。
- `AgentInput` 是 Orchestrator 边界 wrapper，可以承载二者之一。

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

### Orchestrator/Subagent 类型

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

### 核心类型

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

### 每租户的 AgentConfig

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
    memory_enabled: bool = False            # conversation memory (Phase 2)
    pii_masking_enabled: bool = True       # mask PII before LLM calls
```

配置在启动时从 `tenants` 数据库表加载，并缓存到 Redis，TTL 为 5 分钟。

## 5. 工具定义

工具位于 `packages/skill-system/src/tools/`，并在 skill-gateway 注册表中注册。
Python Agent 按名称引用它们，并通过 HTTP 分发到 skill-gateway。

### 5.1 核心 CS 工具（Phase 1 —— MVP）

| 工具 ID | 描述 | 外部调用 |
|---|---|---|
| `query_health` | 查询客户的健康分、使用趋势、支持工单、NPS、MRR、续约日期 | 内部 DB 调用 |
| `query_playbooks` | 为给定信号类型检索最相关的 playbook | 内部 DB + 向量搜索 |
| `send_email` | 通过 SendGrid 发送个性化邮件 | skill-gateway → SendGrid |
| `send_slack` | 给 CSM 发送 Slack 私信 | skill-gateway → Slack API |
| `update_crm` | 向 Salesforce 写入备注/更新 | skill-gateway → Salesforce |
| `schedule_meeting` | 发送 Google Calendar 邀请 | skill-gateway → Google Calendar |

### 5.2 高级工具（Phase 2）

| 工具 ID | 描述 |
|---|---|
| `query_order` | 查询租户的订单/使用系统以获取账户级数据 |
| `generate_qbr` | 起草 QBR 演示（调用 LLM + PDF 生成器） |
| `query_knowledge_base` | 通过 pgvector 对租户的 playbook/文档做语义搜索 |
| `escalate_to_csm` | 将客户标记为已升级，分配给人工 CSM |
| `initiate_refund` | 提交退款请求（若 > 阈值则 → LangGraph 审批工作流） |

### 工具 Schema 定义

每个工具都定义为一个带 JSON Schema 的 Pydantic 模型。JSON Schema 会被提取
并传给 LLM 用于工具调用。

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

### 工具注册表

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

> **工具描述编写规则**（源自课程，略作调整）：
> - 描述必须说明前置条件（"先调用 query_health"）
> - 描述必须说明不该做什么（"不要包含原始 PII"）
> - 每个字段的 `description=` 必须解释该参数
> - 模糊的描述会导致 LLM 选错工具

## 6. Orchestrator-Subagent 实现

目标阶段运行时是一个 Orchestrator-Subagent 系统。旧的 P-E-R 概念映射如下：

| 旧 P-E-R 概念 | Orchestrator-Subagent 替代 |
|---|---|
| Planner | Orchestrator 的 delegation planner 创建 `SubagentTask` |
| Executor | Delegation dispatcher 运行 subagents 及其受限 ReAct loop |
| Reflector | `ComplianceCriticAgent` + Orchestrator reducer/policy checks |
| ReAct loop | 供需要工具的 subagent 使用的共享运行时原语 |

下面的实现片段为了连续性仍保留部分旧命名，但实际实现应按 §3 放入
`orchestrator/`、`subagents/` 和 `runtime/`。

### 6.1 Delegation Planner

Planner 是一次 LLM 调用，把一个 `CustomerSignal` 分解为一组 `TaskPlan`。

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

### 6.2 核心 ReAct 循环

Python Agent 的核心是 **ReAct 循环**（Reasoning + Acting，推理 + 行动）。它等价于
Mastra 的 `generate()`/`stream()` 工具调用机制，这里直接用 openai SDK 实现。

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

### 6.3 工具分发器

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

### 6.4 Executor（任务级，位于 ReAct 之上）

Executor 层位于 ReAct 循环之上。它负责：

1. **依赖注入** —— 若依赖任务尚未完成，先执行它
2. **结果传递** —— 把依赖结果注入当前任务的上下文
3. **部分执行** —— 若某依赖失败，跳过依赖它的任务

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

Reflector 评估执行结果是否满足原始意图。

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

### 6.6 Orchestrate（顶层）

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

## 7. 会话记忆（目标阶段）

会话记忆属于**目标阶段**（Phase 1）。它把每个客户的对话历史持久化到 Postgres，
使 Orchestrator 在多次运行之间、以及在多轮聊天之间（见 §8 客户聊天）都拥有上下文。
Orchestrator 拥有长期记忆写入权；subagent 只接收边界清晰的记忆切片并返回结果，避免记忆污染。
基于 pgvector 的语义召回是可选增强，可按租户开关。

### 记忆设计

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
        # Embed content via pgvector (Phase 2)
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

**成本提示**：语义召回会为每条消息触发一次额外的 embedding 调用。
对一个每天处理 1000 次对话的 CS 平台，这意味着每天约 1000 次额外的 embedding 调用。
在 Langfuse 中跟踪它。若成本/质量权衡不佳，就关闭语义召回。

---

## 8. 客户聊天（目标阶段）

除了主动式、信号触发的外联之外，智能体还支持**同步、面向客户的聊天**通道。
聊天由 `ChatMessage` 触发，而不是由 `CustomerSignal` 触发，并以多轮对话和流式响应运行。

### 8.1 聊天与信号驱动外联的区别

| 维度 | 信号驱动外联（§1–§7） | 客户聊天（本节） |
|---|---|---|
| 触发 | `CustomerSignal`（使用率下降、续约……） | 入站 `ChatMessage` |
| 入口 | RQ 任务 → `orchestrate()` | HTTP 请求 → chat handler |
| 运行时 | Orchestrator → specialist subagents | Orchestrator → CustomerChatAgent → optional specialists |
| 记忆 | Orchestrator 选择性加载 | 必需：每轮读写记忆（§7） |
| 响应 | 邮件 / Slack / CRM 动作 | 流式文本返回给客户 |

聊天复用 Orchestrator（§6）、ReAct loop（§6.2）、工具注册表（§5）和会话记忆（§7）。
典型聊天轮次由 Orchestrator 委托给 `CustomerChatAgent`，必要时调用 specialist subagents，
再经过 `ComplianceCriticAgent` 检查后返回或流式输出。

### 8.2 聊天数据类型

`ChatMessage`、`ChatRequest`、`ChatResponse` 放在 `packages/agent/src/chat_types.py`。
`CustomerSignal` 仍放在 `packages/agent/src/types.py`。二者只通过 `AgentInput` 在 Orchestrator 边界相遇。

### 8.3 聊天处理器

`apps/agent-service/src/agent/chat.py` 负责：

1. 从 Postgres 记忆中加载对话历史
2. 写入新的用户消息
3. 调用 Orchestrator → `CustomerChatAgent`
4. 运行 critic / policy 检查
5. 写入 assistant 回复并流式返回

---

## 9. LangGraph Python 工作流

LangGraph Python 用于需要 **checkpoint + interrupt** 的工作流：

- `refund.py` —— 带人在环的退款审批
- `qbr.py` —— QBR 生成（3-5 分钟，20+ 次 LLM 调用）

### 9.1 退款审批工作流

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

### 9.2 QBR 生成工作流

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

## 10. 集成点

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

### 10.2 Skill Gateway 协议

skill-gateway 暴露单一的 HTTP API。Python Agent 的工具分发和 LangGraph
活动都调用这个端点。

**请求：**

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

**成功响应：**

```json
{
  "success": true,
  "data": {
    "message_id": "msg-123",
    "sent_at": "2026-06-29T10:00:00Z"
  }
}
```

**错误响应：**

```json
{
  "success": false,
  "error": "Recipient email address rejected by SendGrid"
}
```

### 10.3 LLM Gateway 集成

所有 LLM 调用都经过 `packages/llm-gateway/`。智能体引擎**从不**直接调用 OpenAI/Anthropic。

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

### 10.4 可观测性（Langfuse Python SDK）

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

## 11. 实现阶段

### Phase 1 —— 核心智能体（4-6 周）

> **过渡方案（网关延后）：** Phase 1 通过一个薄的 `openai` SDK 封装（`llm_client.py`）
> 直接调用 LLM 提供商，并在**进程内**执行所有工具。专门的 **LLM Gateway**（多提供商路由、
> 缓存、熔断、计费）和 **Skill Gateway**（沙箱化的外部 skill 执行）移至下方的
> "未来计划 —— 网关"。Phase 1 代码导入本地的 `llm_client` shim；当 LLM Gateway 落地时，
> 只需把导入换成 `packages.llm_gateway`，P-E-R 调用处无需改动。

- [ ] `packages/agent/src/types.py` —— Pydantic 模型：`TaskPlan`、`TaskResult`、`CustomerSignal`、`SessionContext`、`AgentResponse`、`LLMUsage`
- [ ] `packages/agent/src/config.py` —— 每租户的 `AgentConfig`
- [ ] `packages/skill-system/src/registry.py` —— `TOOL_REGISTRY`、`get_tool_definition()`、`get_tools_for_tenant()`
- [ ] 核心工具：`query_health`、`send_email`、`send_slack`，配备正确的 Pydantic schema（Phase 1 中**在进程内**执行）
- [ ] `apps/agent-service/src/agent/llm_client.py` —— 薄的 `openai` SDK 封装（直接调用提供商 + Langfuse 追踪），作为 LLM Gateway 的过渡替身
- [ ] `packages/agent/src/orchestration_types.py` —— `AgentInput`、`OrchestratorPlan`、`FinalDecision`
- [ ] `packages/agent/src/subagent_types.py` —— `AgentRole`、`SubagentTask`、`SubagentResult`
- [ ] `apps/agent-service/src/agent/orchestrator/orchestrator.py` —— `OrchestratorAgent` 拥有上下文、记忆、策略和最终响应
- [ ] `apps/agent-service/src/agent/orchestrator/planner.py` —— delegation planner 创建 `SubagentTask`
- [ ] `apps/agent-service/src/agent/orchestrator/reducer.py` —— 将 `SubagentResult` 合并为 `FinalDecision`
- [ ] `apps/agent-service/src/agent/subagents/health_analysis.py` —— 健康/风险 specialist
- [ ] `apps/agent-service/src/agent/subagents/playbook_retrieval.py` —— playbook/RAG specialist
- [ ] `apps/agent-service/src/agent/subagents/outreach_draft.py` —— 面向客户文案 draft specialist
- [ ] `apps/agent-service/src/agent/subagents/customer_chat.py` —— 客户聊天 specialist
- [ ] `apps/agent-service/src/agent/subagents/compliance_critic.py` —— 安全/策略 critic
- [ ] `apps/agent-service/src/agent/runtime/react_loop.py` —— 供使用工具的 subagent 共享的 ReAct loop
- [ ] `apps/agent-service/src/agent/runtime/tool_caller.py` —— 工具分发器（Phase 1 中**仅进程内**；skill-gateway 路由延后）
- [ ] `apps/agent-service/src/agent/planner.py` —— 带 skip-reflector 逻辑的 P-E-R Planner
- [ ] `apps/agent-service/src/agent/executor.py` —— 带并行化的拓扑排序 executor
- [ ] `apps/agent-service/src/agent/reflector.py` —— 带 skip 逻辑的 P-E-R Reflector
- [ ] `apps/agent-service/src/workflow/orchestrate.py` —— 接收 `AgentInput`（`CustomerSignal` 或 `ChatMessage`）的顶层入口
- [ ] `apps/agent-service/src/rq_worker.py` —— 调用 `orchestrate()` 替代内联逻辑
- [ ] 为 Orchestrator 和所有 subagent 调用接入 Langfuse 追踪
- [ ] 单元测试：Orchestrator delegation plan、subagent result merge、critic approval logic

### 未来计划 —— 网关（LLM Gateway + Skill Gateway）

这两个网关**不在 Phase 1 构建**。Phase 1 依赖 `llm_client` shim 和进程内工具执行。
本阶段引入专门的网关，并把智能体迁移到它们上面。

- [ ] `packages/llm-gateway/src/__init__.py` —— 带计费 + 追踪的 `AgentChatCompletions` 类；替换 Phase 1 的 `llm_client` shim（调用签名一致，P-E-R 代码无需改动）
- [ ] `packages/llm-gateway/src/router.py` —— `model_for_agent_task()` 按请求的模型路由
- [ ] `packages/llm-gateway/src/cache.py` —— prompt/语义缓存（Redis + pgvector）
- [ ] `packages/llm-gateway/src/circuit.py` —— 熔断器（状态存 Redis）
- [ ] `apps/skill-gateway/src/index.py` —— 沙箱化 skill 执行器，暴露 `POST /run` HTTP API
- [ ] 扩展 `apps/agent-service/src/agent/tool_caller.py`，把 `requires_sandbox` 工具通过 HTTP 路由到 skill-gateway
- [ ] 把写工具（`send_email`、`send_slack`、`update_crm`、...）从进程内迁移到 skill-gateway 沙箱执行
- [ ] 集成测试：LLM Gateway 路由/缓存；skill-gateway 沙箱隔离

### Phase 2 —— 高级能力（3-4 周）

- [ ] `packages/agent/src/memory.py` —— 用 Postgres + pgvector 的会话记忆
- [ ] 高级工具：`query_knowledge_base`、`generate_qbr`、`schedule_meeting`、`escalate_to_csm`
- [ ] LangGraph Python `refund.py` —— 带 interrupt + PostgresSaver checkpointer 的退款审批
- [ ] LangGraph Python `qbr.py` —— 带 checkpoint 的 QBR 生成
- [ ] 从 DB 加载多租户智能体配置（替换硬编码的 `load_tenant_config`）
- [ ] 集成测试：带 mock interrupt 恢复的完整退款审批周期

### Phase 3 —— 生产加固（2-3 周）

- [ ] 每租户每模型的 token 计量（与 llm-gateway 计费集成）
- [ ] 每个 skill 的熔断器（skill-gateway 已有；接线接入）
- [ ] 每租户每 skill 的限流
- [ ] `tool_caller.py` 中的 PII 脱敏中间件（在调用 skill 前应用 `packages/auth/` 的脱敏）
- [ ] 审计日志：每次任务执行写入 `task_history` 表
- [ ] 带尝试次数限制和告警的 replan 循环
- [ ] 压力测试：100 个并发智能体运行，测量 p95 延迟

---

## 12. 测试策略

### 单元测试（pytest + pytest-asyncio）

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

### 集成测试

- 用 mock skill-gateway 做端到端：signal → orchestrate() → 邮件发出
- LangGraph 工作流：带 mock interrupt 恢复的完整退款审批周期
- 多租户隔离：验证租户 A 的数据绝不会到达租户 B 的智能体

### 压力测试

- 模拟 100 个并发 `orchestrate()` 调用
- 测量：Planner p95 延迟、Executor p95 延迟、端到端总时间
- 目标：简单单工具任务 < 5s 端到端，多工具 < 30s

## 13. 开放问题

1. **Orchestrator/subagent 使用的 LLM 模型**：Orchestrator、critic 和不同 subagent 是否使用同一模型，还是使用更便宜/更快的模型
   （例如 Haiku 做分类/critic、Sonnet 做复杂执行）？当前计划保留 per-role model 配置能力。
2. **Skill-gateway 协议**：HTTP POST 简单，但每次工具调用增加约 50-200ms 延迟。
   若延迟成为问题，可考虑用共享 Redis stream 做更低延迟的 skill 分发。
3. **记忆保留策略**：每个客户保留多少天的对话历史？
   这会影响 Postgres 存储和 embedding 成本。
4. **重新委托/重试阈值**：当 `ComplianceCriticAgent` 或 reducer 判定结果不满足时，升级给人工前重试几次？
   当前计划：可配置的 `max_replan_attempts`（默认 2）。
5. **长等待用 LangGraph 还是 Temporal**：LangGraph Python interrupt 对 < 1 小时的等待很干净。
   Temporal 对 > 1 小时更久经考验。对于 48 小时的回复等待，应该用
   LangGraph interrupt 还是带 signal channel 的 Temporal 工作流？建议：> 1 小时的等待用
   Temporal，人工审批的中断用 LangGraph。
6. **Python 导入路径 vs 带连字符的目录**：当前目录（`packages/llm-gateway`、
   `packages/skill-system`、`apps/agent-service`）使用连字符，而连字符在 Python 模块名中不合法。
   两个选项：(a) 把包源目录重命名为下划线（`packages/llm_gateway`、...）并直接导入；
   或 (b) 保留带连字符的仓库目录，但在 `pyproject.toml` 中定义可安装的发行包，把导入名
   映射到下划线包（例如 `[tool.setuptools] packages = ["llm_gateway"]` 配合 `package-dir`）。
   建议：在 monorepo 中为简单起见选 (a)，或采用每包一个 `src/` 的布局并以 editable 方式安装。
   这必须在编写任何 Phase 1 代码之前决定，因为本计划中的每个代码片段都假设使用下划线导入路径。

---

## 14. 反思：相较原计划的改动

原计划全程引用 TypeScript/Mastra，并采用较线性的 P-E-R 结构。本 Python 重写进一步收敛为 Orchestrator-Subagent 架构，做了以下实质性改动：

### 框架改动

- **移除**：`apps/agent-service/src/agent/mastra/` 目录及所有 Mastra 引用
- **移除**：TypeScript 代码示例；所有片段现在都是 Python（Pydantic、openai SDK、httpx、asyncio）
- **新增**：`react_loop.py` —— 直接的 ReAct 实现，替代 Mastra 的 Agent 类所做的事
- **新增**：`tool_caller.py` —— 工具分发层（进程内 vs skill-gateway HTTP）
- **LangGraph**：保留，但用 Python 语法重写（typed state、StateGraph builder、PostgresSaver）

### 类型系统改动

- **移除**：Zod schema 和 TypeScript 类型
- **新增**：全程使用 Pydantic v2 模型 —— `BaseModel`、`Field`、`model_json_schema()`
- `PlannerOutput` → 改为直接返回 `tuple[list[TaskPlan], LLMUsage]`
- `TaskPlan` 字段 `depends_on` 从 `dependsOn` 重命名（Python 约定）
- 从反思中新增 `AgentConfig.skip_reflector_for_simple`

### 文件布局改动

- `packages/agent-core/` → `packages/agent/`（单数名词，匹配现有约定）
- 移除 `mastra/` 子目录；由 `react_loop.py` + `tool_caller.py` 替代
- `langgraph/` 移至 `apps/agent-service/src/workflow/langgraph/`

### 集成改动

- **Skill gateway**：完整定义了 HTTP API 契约（请求/响应 schema）—— 原计划
  只提到"HTTP POST"但没有定义契约
- **LLM gateway**：定义了 `AgentChatCompletions` 类 —— 用智能体特定需求（工具 schema、
  追踪、计费）包装现有的 `chat_completions`
- **可观测性**：使用 Langfuse Python SDK，而非 TypeScript 追踪 API

### 内容新增

- **第 6.2 节**：完整的 `ReActLoop` 实现 —— 最关键的 Python 原生组件
- **第 6.3 节**：带进程内 vs skill-gateway 路由的 `tool_caller.py` 分发器
- **第 9.2 节**：Skill gateway HTTP API 契约（完整定义的请求/响应 JSON）
- **第 13 节（本节）**：每个 TypeScript 引用到 Python 等价物的显式映射

### 内容移除

- `examples/src/planner-executor.ts` 引用（TypeScript demo）
- Mastra 的 `generate()` / `stream()` 方法示例
- `@mastra/core`、`@ai-sdk/anthropic` 导入
- Zod schema 导入和 `.describe()` 链
- 全程的 TypeScript interface 语法
