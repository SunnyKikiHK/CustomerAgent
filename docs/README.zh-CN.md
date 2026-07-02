# 客户成功智能体 (Customer Success Agent)

> 一个由多步骤 AI 智能体驱动的 B2B 客户成功自动化平台。它能自动监控客户健康度、生成个性化的外联内容、执行标准运营流程，并在需要时升级给人工 —— 全天候 7×24 自动运行。

---

## 1. 这个智能体能做什么？

在一家 B2B SaaS 公司里，**客户成功（Customer Success）** 团队的职责是确保客户持续使用产品、续订订阅，并尽可能升级到更高套餐。这项工作主要包括：

- **监控** 客户如何使用产品
- **主动联系** 当出现异常信号时（使用率下降、支持工单激增、即将续约）
- **在合适的时机发送合适的信息**（邮件、Slack 私信、会议邀请）
- **跟进** 当客户没有回复时
- **升级** 当 AI 单独无法处理时，转交给人工客户成功经理（CSM）

要为数百个客户做好这些工作，靠手工几乎不可能。这个智能体能自动完成这一切。

### 它解决的问题

| 之前（人工 CSM 工作） | 之后（AI 智能体） |
|---|---|
| CSM 要在 10 个不同的工具中查看客户健康度 | 智能体实时分析使用、支持、计费数据 |
| 千篇一律的"最近怎么样"邮件 | AI 根据客户的实际产品使用情况写个性化邮件 |
| 准备季度业务回顾（QBR）要 4 小时 | 智能体 5 分钟生成 QBR 演示草稿 |
| 只有客户明确拒绝时才发现续约风险 | 智能体在续约前 90 天就识别风险并启动挽回流程 |
| 升级销售机会容易被错过 | 智能体识别出准备好升级企业版的重度用户 |

### 一个真实场景

早上 8:00。智能体启动它的每日例行扫描，检查所有客户账户。

- **Acme Corp 的 API 调用量本周下降了 40%**。危险信号。
- Acme 的支持工单 **自上周一起增长到 3 倍**。又一个危险信号。
- Acme 的合同 60 天后到期。风险真实存在。

**8:05 AM** —— 智能体触发"高风险"应对流程，生成一封个性化邮件给 Acme 的 CTO，邮件中引用了他们之前讨论过的 Webhook 功能，并附上集成指南，然后通过 Gmail 发送。同时把这次互动记录到 Salesforce。

**8:10 AM** —— 如果 Acme 在 48 小时内没有回复，智能体自动通过 Slack 私信通知负责的 CSM 并附上完整上下文。如果 Acme 回复了，智能体会起草会议议程并自动发送日历邀请。

CSM 只需要介入那些真正需要人参与的对话。常规工作由智能体完成。

---

## 2. 整体架构

系统分为两类组件：

- **`packages/`** —— 共享库。被 apps 导入使用。它们本身不是运行中的进程，承载业务逻辑、Schema 和 SDK 封装。
- **`apps/`** —— 可执行进程。这些是独立部署的服务，**互相不直接 import 对方的代码** —— 共享逻辑只能通过 `packages/` 引用。

### 代码目录结构

```text
CustomerAgent/
├── apps/                          # 运行中的进程（独立部署）
│   ├── api-gateway/               # FastAPI HTTP 网关
│   │   └── src/
│   │       ├── app.py             # FastAPI 应用与插件注册
│   │       ├── routes/            # 路由定义
│   │       └── plugins/           # 自定义插件（认证、限流等）
│   │
│   ├── agent-service/             # Python ReAct 智能体核心（P-E-R）+ LangGraph
│   │   └── src/
│   │       └── rq_worker.py       # RQ worker 入口
│   │
│   ├── skill-gateway/             # 工具注册 + 沙箱执行
│   │   └── src/
│   │       └── index.py            # 沙箱化 skill 执行器
│   │
│   └── temporal-worker/           # Temporal workflow worker
│       └── src/
│           └── temporal.py          # Temporal worker 入口
│
├── packages/                      # 共享库（被 apps 导入）
│   ├── shared/                    # 共享类型定义和工具函数
│   │   └── src/
│   ├── config/                    # 环境变量 Schema（pydantic-settings）
│   │   └── src/
│   ├── db/                        # SQLAlchemy 模型 + Alembic 迁移
│   │   └── src/
│   │       └── migrations/        # Alembic 迁移文件
│   ├── redis/                     # Redis 客户端封装（租户级键名前缀）
│   │   └── src/
│   ├── llm-gateway/               # 多 LLM 提供商路由、缓存、熔断
│   │   └── src/
│   │       ├── router.py          # 根据请求选择合适的模型
│   │       ├── cache.py           # 语义缓存（Redis + pgvector）
│   │       └── circuit.py         # 熔断器（状态存 Redis）
│   ├── skill-system/              # Skill 注册表 + 沙箱定义
│   │   └── src/
│   │       ├── registry.py        # Skill 注册与发现
│   │       └── sandbox.py        # 沙箱化执行（RestrictedPython / 子进程）
│   ├── session/                   # 状态机类型 + Temporal 工作流辅助
│   │   └── src/
│   │       ├── workflow.py        # Temporal 工作流定义
│   │       ├── activities.py      # Temporal 活动
│   │       └── state.py           # 会话状态类型与转换
│   ├── observability/             # OpenTelemetry + Langfuse SDK 封装
│   │   └── src/
│   │       ├── tracer.py          # OTel tracer 初始化
│   │       └── langfuse.py       # Langfuse SDK 封装
│   └── auth/                      # JWT 验证 + 租户级 RBAC
│       └── src/
│
├── infra/                         # 基础设施即代码
│   ├── docker/                    # 本地开发 docker-compose
│   ├── k8s/                      # Kubernetes 部署配置
│   └── terraform/                 # 云资源配置
│
├── tests/                         # 测试套件（单元、集成、端到端）
├── docs/                          # 完整项目文档
│
├── infra/docker/docker-compose.yml # 本地开发栈（postgres、redis、temporal、langfuse）
├── infra/docker/.env              # Docker 栈密钥（NEXTAUTH_SECRET、SALT 等）
├── start.sh                       # 一键本地启动脚本
├── config.sh                      # start.sh 加载的本地 shell 环境变量
└── requirements.txt               # Python 依赖
```

### 各模块如何协作

```text
                       ┌──────────────┐
                       │  CSM / Web  │
                       │   前端界面   │
                       └──────┬───────┘
                              │ HTTPS
                              ▼
                       ┌──────────────┐
                       │ api-gateway  │  FastAPI HTTP
                       └──┬─────┬─────┘
                          │     │
              入队         │     │  读写
                          ▼     ▼
        ┌──────────────────┐  ┌──────────────────┐
        │ RQ (Redis Queue) │  │  Postgres         │
        │   - outreach     │  │   - tenants       │
        │   - workflows   │  │   - customers     │
        └────────┬─────────┘  │   - interactions │
                 │            │   - audit_logs   │
                 ▼            └──────────────────┘
        ┌─────────────────────────────────────────┐
        │         agent-service (app)               │  Python ReAct（P-E-R）+ LangGraph
        │  - 运行 Planner / Executor / Reflector    │
        │  - 入队 Temporal 工作流                  │
        └────────┬────────────────────────────────┘
                 │          │              │
        调用      │          │              │ 调用
                 ▼          ▼              ▼
        ┌──────────────┐ ┌──────┐ ┌──────────────────┐
        │ llm-gateway │ │ RAG  │ │  skill-gateway    │
        │ (packages/)  │ │      │ │   (apps/)         │
        └──────┬───────┘ └──────┘ └────────┬─────────┘
               │          │                  │
               ▼          ▼                  ▼
        ┌──────────┐ ┌──────────┐ ┌─────────────────┐
        │  Redis   │ │ pgvector │ │  外部 API        │
        │          │ │          │ │ Email/Slack/CRM  │
        └──────────┘ └──────────┘ └─────────────────┘
```

---

## 3. 核心功能

### 3.1 智能体循环（Planner → Executor → Reflector）

每次客户互动都在 `agent-service` 中经历三步循环：

1. **Planner（规划器）** —— 查看客户的当前健康数据，决定该做什么。
   - 健康分 < 50？ → **关键优先级**，立即外联。
   - 使用率下降 > 30%？ → **高优先级**，主动问候。
   - NPS 9-10 分？ → **扩展机会**，推荐企业版功能。
   - 30 天内续约且健康分 < 70？ → **高优先级**，续约风险。

   Planner 返回一个结构化决策：动作类型、优先级、推理、使用的 playbook、目标客户。

2. **Executor（执行器）** —— 调用对应的 skill 执行动作：
   - `send-email` —— 发送个性化邮件
   - `send-slack` —— 给 CSM 发 Slack 私信
   - `schedule-meeting` —— 发送日历邀请
   - `update-crm` —— 写入 Salesforce 记录
   - `generate-qbr` —— 生成 QBR 演示草稿

3. **Reflector（反思器）** —— 评估结果。客户回复了吗？回复是正面的还是负面的？应该跟进、升级还是闭环？

### 3.2 LLM 网关（模型路由、缓存、计费）

所有 LLM 调用的统一入口，承担三大职责：

- **模型路由** —— 根据请求选择合适的模型。一封简单的"问候"邮件用便宜快速的模型；一份高管 QBR 摘要用更强的模型；流式聊天用低延迟模型。
- **Prompt 缓存** —— 缓存客户档案和 playbook，避免每次 LLM 调用重复发送相同上下文。典型工作负载在启用缓存后成本下降 **约 80%**。
- **计费** —— 记录每个租户在每个模型上的每次 token 用量，以便按实际 LLM 消费向客户公司收费。

### 3.3 Skill 沙箱（安全的工具执行）

Skill 是智能体的"手脚" —— 它们真正地发送邮件、查询 Salesforce、推送 Slack。运行不可信代码是危险的，所以每个 skill 都在显式权限的沙箱中运行。`skill-gateway` app 作为独立的操作系统进程运行沙箱：

| Skill | 网络 | 文件 | 限流 | 额外校验 |
|---|---|---|---|---|
| `send-email` | 仅 SendGrid / Mailgun | 读模板 | 每租户 100/小时 | 邮件格式 + 频次检查 |
| `send-slack` | 仅 Slack API | 无 | 每租户 1000/小时 | 频道 ID 校验 |
| `salesforce-query` | Salesforce 域名 | 无 | 每租户 500/小时 | 阻止任何写查询（UPDATE / DELETE / INSERT） |
| `generate-pdf` | 无（本地） | 仅写 `/tmp` | 每租户 50/小时 | PDF 大小限制 |

### 3.4 RAG 知识库（pgvector）

检索增强生成（RAG）流水线让智能体可以从向量数据库中拉取相关的 playbook、案例研究和异议处理笔记。`packages/knowledge-service/` 负责：

- **Ingest（摄入）** —— 文档解析与切分
- **Embed（嵌入）** —— 通过 embedding API 生成向量
- **Retrieve（检索）** —— 按租户执行 pgvector 相似度搜索

每个租户拥有独立的知识集合 —— Acme 的 playbook 与 Beta 的 playbook 互不可见。

### 3.5 会话状态（Redis + Postgres + Temporal）

每位客户都有自己的旅程状态：

```text
  monitoring ──► outreach_sent ──► waiting_reply ──► reply_received
       ▲              │                  │               │
       │              ▼                  ▼               ▼
       │          escalated          meeting_scheduled  resolved
       │              │
       └──────────────┘  （人工处理后，回到 monitoring）
```

- **Redis**（通过 `packages/redis/`）实时存储当前状态（亚毫秒级读写），使用租户级键名前缀。
- **Postgres**（通过 `packages/db/`）存储状态转换的完整历史（审计轨迹）。
- **Temporal**（通过 `apps/temporal-worker/`）运行长生命周期工作流 —— 例如"等待 48 小时回复，若无则升级"。即使 worker 在等待中途崩溃，Temporal 也能从断点恢复。

### 3.6 多租户隔离

平台服务多家 B2B SaaS 公司。Acme 的客户数据绝不能泄漏给 Beta。三层隔离机制：

- **Postgres 行级安全（RLS）**（在 `packages/db/` 中）—— 每次查询自动按 `tenant_id` 过滤。即使某个查询忘了写 `WHERE` 子句，也只会返回对应租户的数据。
- **Redis 键前缀**（在 `packages/redis/` 中）—— 每个键都以 `tenant:{tenantId}:...` 命名空间化。没有任何全局"state"键。
- **每租户独立队列** —— 每个租户拥有自己的 RQ 队列，因此一个租户的流量洪峰不会饿死其他租户。

### 3.7 安全（审计、PII、RBAC）

- **审计日志** —— 每个动作（发送邮件、更新 CRM、生成报告）都记录操作者、资源、前后状态、IP 地址（`packages/db/` Schema）。
- **PII 脱敏** —— 邮件地址、电话号码、信用卡号在送入 LLM 之前被替换为不透明 token。LLM 永远看不到原始 PII（`packages/auth/`）。
- **RBAC** —— 三种角色：`admin`（配置 playbook、查看全部数据）、`csm`（查看分配的客户、发送邮件）、`viewer`（只读访问）（`packages/auth/`）。

### 3.8 高并发处理（队列、熔断器、限流）

- **优先级队列** —— 高风险客户先于续约提醒处理，续约提醒先于常规问候处理（在 `apps/agent-service/` 中强制执行）。
- **熔断器** —— 如果 Salesforce API 持续返回 500，熔断器打开，60 秒内直接返回缓存数据，避免反复打挂掉的服务（`packages/llm-gateway/src/circuit.py`）。
- **限流** —— 每租户的 token bucket 防止一个客户独占我们的邮件配额（在 `packages/llm-gateway/` 中）。

### 3.9 可观测性（OpenTelemetry + Langfuse）

- **OpenTelemetry**（`packages/observability/`）追踪每一次 LLM 调用、每一次 skill 执行、每一次数据库查询。完整链路 —— "Planner 决定 X，然后 email skill 发送 Y" —— 在 Jaeger / Grafana 上一目了然。
- **Langfuse**（`packages/observability/`）专门追踪 AI 质量：prompt、响应、模型、token 用量、以及质量评分（"这封邮件有效吗？客户是否正面回复？"）。

---

## 4. 端到端工作流

下面是智能体决定主动联系客户时发生的完整流程。

```text
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 1 步 —— 检测                                                    │
  │ 定时任务（或产品分析系统的 webhook）发出信号：                      │
  │ Acme Corp 的使用量环比上周下降 40%。                                │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 2 步 —— 入队                                                    │
  │ api-gateway 把一个 RQ 任务入队，priority=CRITICAL                  │
  │ （因为健康分 < 50）。                                              │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 3 步 —— Worker 拾取任务                                         │
  │ agent-service 从 Postgres 加载客户的完整上下文。                    │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 4 步 —— Planner                                                 │
  │ Planner LLM 调用返回：                                             │
  │   { action: "email", priority: "CRITICAL",                        │
  │     playbook: "at_risk_recovery",                                 │
  │     reasoning: "使用量下降 40%，支持工单 3 倍，60 天续约" }       │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 5 步 —— RAG 检索                                                │
  │ retrieve 步骤从租户知识库中找出最相关的 playbook 和案例研究。       │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 6 步 —— 生成邮件                                                │
  │ 第二次 LLM 调用使用检索到的 playbook + 客户上下文写出个性化邮件。   │
  │ LLM Gateway 缓存客户档案，下次调用更便宜。                         │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 7 步 —— 执行 Skill                                              │
  │ send-email skill 在 skill-gateway 中运行，调用 SendGrid，          │
  │ 然后向 Postgres 写入一行 `interactions` 记录。                      │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 8 步 —— 状态更新 + Temporal 工作流                              │
  │ 会话状态：monitoring → outreach_sent → waiting_reply。             │
  │ 启动 Temporal 工作流："等待 48 小时，检查是否回复"。               │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 9a 步 —— 收到回复（理想路径）                                   │
  │ Temporal 检测到回复。状态 → reply_received。                       │
  │ Planner 用回复内容再跑一次。                                       │
  │ Executor 安排会议。状态 → meeting_scheduled。                      │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 9b 步 —— 没有回复（升级路径）                                   │
  │ Temporal 48 小时后超时。状态 → escalated。                         │
  │ Executor 调用 send-slack skill，给负责的 CSM 发私信。              │
  │ CSM 接管对话。                                                     │
  └──────────────────────────────┬─────────────────────────────────────┘
                                 ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ 第 10 步 —— 可观测性                                               │
  │ 以上每一步都被 OTel 追踪、被 Langfuse 评分。                       │
  │ token 用量和成本被记录用于租户计费。                               │
  └────────────────────────────────────────────────────────────────────┘
```

---

## 5. 为什么是这种架构？

### 为什么要把 `packages/` 和 `apps/` 拆开？

- **`packages` 是库**，被 app 导入。它们承载业务逻辑 —— LLM 路由器、RAG 检索器、Skill 注册表、DB Schema、认证层 —— 都可以独立做单元测试。
- **`apps` 是进程**。`api-gateway` 提供 HTTP 服务。`agent-service` 运行智能体循环。`temporal-worker` 运行工作流。`skill-gateway` 运行不可信的 skill 代码。
- 在生产环境中，每个 app 是**独立部署、独立伸缩**的服务。HTTP 流量激增不会饿死 worker，worker 里跑得慢的工作流也不会拖慢 API。它们只通过 `packages/` 共享逻辑，绝不互相 import。

### 为什么要用 Planner/Executor/Reflector 循环？

- **关注点分离**："决定做什么"和"实际去做"以及"评估结果"是三个不同的问题。混在一起会让代码很难改。
- **可测试性**：planner 可以用 mock 输入做单元测试；executor 可以用 mock skill 测试；reflector 可以用 mock 结果测试。
- **可解释性**：CSM 或审计员可以读 planner 的推理过程，理解为什么发送了某一封邮件。在 B2B 场景中，这对合规至关重要。

### 为什么要单独的 LLM 网关？

没有网关的话，代码的每个部分都会直接调用 OpenAI / Anthropic / Google。网关给我们带来：

- **一个地方切换模型** —— 当价格或质量发生变化时。
- **一个地方做缓存** —— 没有它，两条不相关的代码路径会重复发送相同上下文给 LLM 并付两次钱。
- **一个地方统计 token** —— 用于计费。

### 为什么要单独的 skill-gateway 进程？

Skill 实际上是 LLM 写的、我们即将运行的代码。LLM 可能会产生幻觉 —— 它可能生成一个地址错误的 `send-email` 调用，甚至生成一条带 `DELETE` 语句的 `salesforce-query`。通过在带显式权限（允许的域名、限流、查询校验）的独立进程中运行 skill，我们把任何 bug 或 prompt injection 攻击的影响范围控制住。即使沙箱崩溃，主智能体不受影响。

### 为什么要用 Temporal 处理长时间等待？

Temporal 是一个工作流引擎。典型的 CS 工作流充满"等 48 小时，然后做 X"这样的步骤。如果我们用 worker 进程里的 `time.sleep` 来实现，worker 必须存活整整 48 小时 —— 既浪费又脆弱。Temporal 把工作流状态持久化到数据库，所以 worker 可以崩溃后从断点恢复，而不会丢失等待状态。同一个工作流事后可以被检视、重放、调试。

---

## 6. 术语表

- **Tenant（租户）** —— 使用本平台的 B2B SaaS 公司（例如"Acme Corp"、"StartupXYZ"）。每个租户拥有自己的客户、playbook 和 CRM 集成。
- **Customer（客户）** —— 由租户管理的终端用户账户（例如"Acme Corp 的 Sarah"）。拥有健康分、MRR、续约日期。
- **Health score（健康分）** —— 一个 0-100 的数字，估算客户续约的可能性。使用率下降、支持工单激增、低 NPS 都会拉低它。
- **MRR** —— Monthly Recurring Revenue（月度经常性收入）。该客户每月支付多少。
- **CSM** —— Customer Success Manager（客户成功经理）。负责一组客户关系的人工。
- **Playbook** —— 处理某种情况的标准流程（例如"高风险挽回"、"续约"、"扩展销售"）。
- **Skill** —— 智能体可以调用的工具（例如 `send-email`、`salesforce-query`）。
- **QBR** —— Quarterly Business Review（季度业务回顾）。向客户展示的演示，总结他们的使用情况、价值、未来机会。
- **NPS** —— Net Promoter Score（净推荐值）。客户满意度的衡量，0 到 10 分。
- **PII** —— Personally Identifiable Information（个人可识别信息）：邮箱、电话、信用卡等。
- **RAG** —— Retrieval-Augmented Generation（检索增强生成）。在 LLM 生成回复前先从向量库中检索相关文档的模式。
- **RLS** —— Row-Level Security（行级安全）。Postgres 的特性，根据策略在每次查询中过滤行。

---

## 7. 速查：文件地图

| 组件 | 职责 | 关键文件 |
|---|---|---|
| `apps/api-gateway` | HTTP 服务（FastAPI） | `src/app.py`、`src/routes/`、`src/plugins/` |
| `apps/agent-service` | 智能体核心（Planner/Executor/Reflector） | `src/rq_worker.py` |
| `apps/skill-gateway` | 沙箱化 skill 执行 | `src/index.py` |
| `apps/temporal-worker` | Temporal workflow worker | `src/temporal.py` |
| `packages/shared` | 共享类型和工具函数 | `src/` |
| `packages/config` | 环境变量 Schema + 配置加载 | `src/` |
| `packages/db` | SQLAlchemy 模型 + Alembic 迁移 | `src/`、`migrations/` |
| `packages/redis` | Redis 客户端（租户命名空间） | `src/` |
| `packages/llm-gateway` | LLM 路由、缓存、计费 | `router.py`、`cache.py`、`circuit.py` |
| `packages/skill-system` | Skill 注册表 + 沙箱定义 | `registry.py`、`sandbox.py` |
| `packages/knowledge-service` | RAG 流水线 | `ingest.py`、`embed.py`、`retrieve.py` |
| `packages/session` | 状态机 + Temporal 辅助 | `workflow.py`、`activities.py`、`state.py` |
| `packages/observability` | 追踪 + AI 质量 | `tracer.py`、`langfuse.py` |
| `packages/auth` | JWT 验证 + RBAC | `src/` |
| `infra/docker` | 本地开发 docker-compose | `docker-compose.yml` |
| `infra/k8s` | K8s 部署配置 | |
| `infra/terraform` | 云资源配置 | |
