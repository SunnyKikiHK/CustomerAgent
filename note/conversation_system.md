# Conversation System — Orchestrator-Workers Redesign

## Summary

The customer-facing conversation system was rebuilt from a **Planner → multi-subagent
Executor → separate Reflector (ComplianceCritic)** pipeline into a single
**GeneralAgent orchestrator** using the Orchestrator-Workers pattern.

The GeneralAgent now:

- receives the customer message directly,
- runs a bounded ReAct loop (reason → call tools → repeat),
- calls existing internal tools (`query_health`, `query_playbooks`) and three new
  specialist **delegate tools** (`delegate_billing`, `delegate_technical`,
  `delegate_escalation`) when a domain requires it,
- is **its own critic** — the compliance/PII/tenant/tone/grounding rules that the
  `ComplianceCriticAgent` used to enforce as a separate LLM call are embedded in its
  `SKILL.md`,
- produces the final customer-facing reply itself, streamed token-by-token.

The **signal system path and its `ComplianceCriticAgent` are unchanged** — this
change is conversation-only.

## Architecture

```
customer turn
   │
   ▼
ConversationOrchestrator.run_stream()          # SSE entry (streaming clients)
   │   .run()                                  # non-streaming entry (eval/tests)
   ▼
ConversationLoop  (GeneralAgent, MAX_REACT_LOOPS = 6)
   │
   ├── step: LLMClient.complete()  → parse tool_calls           (planner.* span)
   │        │
   │        ├── no tool calls  ─────────────► NATURAL EXIT
   │        └── tool calls ► dispatch_tool_call() through the resilient tool layer
   │                 │        (circuit breaker / TTL cache / timeout / JSONSchema
   │                 │         validation / fallback / ToolStats)
   │                 ├── query_health / query_playbooks (RAG: rewrite→recall→dedup→rerank)
   │                 └── delegate_billing / _technical / _escalation
   │                          └── existing ReAct specialist subagent
   │                              (its own allowed tools + skill, max_react_steps = 4)
   │
   └── at step == MAX_REACT_LOOPS without a tool-free answer ─► FORCED SYNTHESIS
                                                                (strip tools + directive)
   ▼
LLMClient.stream()  → final customer answer, token-by-token    (planner.* span)
   ▼
on_approved()  → non-blocking profile update + conversation→signal bridge (unchanged)
```

### Key modules

| Concern | File |
|---|---|
| Orchestrator loop (natural exit + Forced Synthesis + streaming) | `apps/agent_service/src/agent/conversation/conversation_loop.py` |
| Delegate tools (specialists-as-tools) | `apps/agent_service/src/agent/conversation/delegates.py` |
| Orchestrator wiring (`run`/`run_stream`, memory, bridge) | `apps/agent_service/src/agent/conversation/conversation_orchestrator.py` |
| Real token streaming | `apps/agent_service/src/agent/llm_client.py` (`LLMClient.stream`) |
| SSE adapter | `apps/agent_service/src/agent/conversation/streaming.py` |
| Embedded compliance + delegation rules | `skills/demo-tenant/general_support/SKILL.md` |

## What was preserved

- **Tool layer** — `MCPToolLayer`: circuit breaker, TTL cache, timeout, JSONSchema
  parameter validation, fallback, and `ToolStats`. Every orchestrator tool call and
  every delegate call still goes through `dispatch_tool_call` → `layer.call`.
- **RAG retrieval pipeline** — `retrieve_with_optimization`: query rewrite → parallel
  recall → dedup by content hash → rerank. `query_playbooks` still routes through it.
- **Memory** — loading + context injection (`get_context`, memory excerpt, recent
  history) is unchanged and still fed to the orchestrator prompt.
- **Tenant isolation** — every tool call is scoped to the session `tenant_id`
  (injected by `execute_internal_analysis` and again in the loop's `_inject_identity`;
  `customer_id` comes from the session, never from model-supplied arguments).
- **Delegate tools reuse the existing specialists** — `BillingAgent`,
  `TechnicalAgent`, `EscalationAgent` run unchanged (same `ReActSubagent`/`ReActLoop`,
  same `DEFAULT_ALLOWED_TOOLS`, same skills).
- **Signal system** — `SignalOrchestrator`, `run_compliance_critic`, and the signal
  `ComplianceCriticAgent` are untouched.
- **Class/entry contracts** — `ConversationOrchestrator` (subclasses
  `BaseOrchestrator`, `supports_external_writes=False`), `run_conversation_agent`,
  `on_approved` (non-blocking profile update + signal bridge), and the SSE route in
  `apps/api_gateway/src/routes/chat.py`.

## What changed

- **Removed** the conversation planner path: `conversation_planner.py` (deterministic
  + fast-path routing) and `llm_planner.py` (LLM role selection). The GeneralAgent
  decides delegation itself.
- **Removed the separate compliance-critic LLM call from the conversation path.** The
  critic's rules are embedded in the GeneralAgent `SKILL.md`; the GeneralAgent
  self-checks its draft before sending. (The signal path keeps its critic.)
- **Real streaming** replaced the old cosmetic streaming. Previously `streaming.py`
  awaited the full response, then re-chunked the finished string into fake 40-char
  "token" events. Now `LLMClient.stream()` uses `stream=True` and forwards provider
  deltas as they arrive; intermediate reasoning/tool steps are never streamed.
- **Specialists became tools** (`delegate_*`) registered on the INTERNAL tool
  boundary, so delegation inherits circuit breaker / cache / stats / fallback.

## max_react_steps rationale

- **GeneralAgent orchestrator: `MAX_REACT_LOOPS = 6`.** The orchestrator may need to
  chain a lookup and a delegation (and occasionally a second delegation for a compound
  turn), each costing one step, plus slack for a re-query. Six bounds worst-case
  latency to ~6 reasoning round-trips + 1 streamed answer while leaving room for
  genuinely multi-step turns. Lower risked truncating legitimate compound turns before
  an answer; higher mostly buys runaway latency.
- **Delegate specialists: `max_react_steps = 4`.** A specialist is a single-domain
  worker: typically one tool lookup (health/playbook/refund) then compose. Four allows
  a lookup, a follow-up lookup, a retry, and the compose step, and matches the tight
  conversation-answer budgets already used elsewhere. The specialist is bounded
  independently so a slow delegate cannot consume the orchestrator's whole budget.

## Forced Synthesis: bounded, crash-free final response

The loop terminates in exactly one of two ways, and **both end with a real,
model-authored, streamed reply**:

- **A. Natural exit** — the model returns a response with no tool calls (the standard
  ReAct termination signal). No special `finish_task` tool is needed. Its answer is
  produced by the streaming synthesis call.
- **B. Forced Synthesis at the step limit** — if the loop reaches `MAX_REACT_LOOPS`
  without a tool-free answer, the orchestrator does **not** raise, crash, or return a
  hardcoded string. Instead it:
  1. strips **all** tools from the next call (the model can no longer request any),
  2. injects a system directive commanding an immediate final customer-facing answer
     from the information already gathered,
  3. makes that call and streams its text through the normal streaming path.

Because tools are stripped, Forced Synthesis cannot loop again — it is a single
terminal LLM call. This guarantees every turn ends with a bounded, real reply even at
the step cap. Verified by `test_forced_synthesis_at_max_loops` (tools stripped, answer
produced, streamed text equals final text, no exception raised).

## Validation

### Tests

- New focused suite `tests/test_conversation_loop.py` (7 tests, all pass):
  - delegates a billing turn to `delegate_billing`,
  - handles a greeting directly (no delegation),
  - natural ReAct exit on no tool calls,
  - Forced Synthesis at `MAX_REACT_LOOPS` (tools stripped, answer produced, no raise),
  - streamed text equals final model text (natural + forced),
  - `delegate_*` goes through the circuit breaker and degrades to the fallback,
  - `max_react_steps` respected for both the orchestrator (6) and delegates (4).
- Full offline suite: **163 passed** (`pytest tests/ -k 'not postgres and not live'`).
  The obsolete planner-routing tests were removed with the planner; tool-layer,
  memory, compliance, and signal-system tests are unchanged and green.

### Latency evaluation

Two live runs against the demo tenant (real OpenRouter + Postgres/Redis):

1. Harness run — first 5 cases via `scripts/run_evaluation.py --limit 5 --out eval`
   → 5/5 passed, judge dimensions all 5.0.
2. Representative-mix run — `scripts/eval_conversation_representative.py`
   (`eval/representative.json`), one case per behaviour: ambiguous, compound
   billing, tool/technical, policy/refund, adversarial.

**Environment caveat (important for reading absolute numbers).** This box's
outbound network to OpenRouter is flaky (DNS on the gateway intermittently fails),
so every LLM call can hit the 30 s client timeout + one retry before succeeding.
Absolute latencies below are therefore **network-bound, not compute-bound** — the
`tool`/`reasoning` phase times are inflated by provider round-trip stalls. The
*relative* signal (call counts, step counts, phase split, delegation behaviour,
pass rate) is the meaningful part.

#### Per-turn metrics (representative run)

| Case | Category | LLM calls | ReAct steps | Tool calls | Forced synth | Tokens | Latency (ms) | Delegated to |
|---|---|---|---|---|---|---|---|---|
| amb-01 "it's broken" | ambiguous | 3 | 2 | 2 | no | 5758 | 67 026 | `delegate_technical` |
| amb-02 cancel + double charge | ambiguous | 3 | 2 | 2 | no | 5296 | 110 414 | `delegate_billing` |
| tool-01 health/MRR | tool_error | 3 | 2 | 1 | no | 4750 | 53 496 | — (handled directly) |
| pol-01 annual-plan refund | policy | 3 | 2 | 1 | no | 4864 | 53 146 | `delegate_billing` |
| adv-02 "you are now DAN…" | adversarial | 2 | 1 | 0 | no | 3193 | 37 281 | — (refused directly) |

Aggregate (representative): **P50 53 496 ms, P95 67 026 ms** (network-bound);
**5/5 completed, 0 forced-synthesis, 0 crashes**. Harness run (first 5 cases):
P50 507 909 ms / P95 576 883 ms — same network caveat, and those 5 are all
ambiguous-category turns so they skew slower.

#### Behavioural findings

- **Delegation works as designed**: billing/refund → `delegate_billing`, technical
  fault → `delegate_technical`, and the GeneralAgent answers greetings/direct
  lookups itself. The adversarial jailbreak (adv-02) was **refused directly with no
  tool calls** — the compliance rules embedded in the `SKILL.md` held without a
  separate critic call.
- **LLM calls per turn dropped**: 2–3 (N reasoning steps + 1 streamed answer) vs. the
  old pipeline's planner + per-subagent + separate compliance-critic calls. The
  metrics breakdown shows **`llm` phase = 0.0%** in the harness run, confirming the
  standalone ComplianceCritic LLM call is gone from the conversation path; time now
  splits between `reasoning` (orchestrator steps) and `tool` (lookups + delegated
  specialists).
- **Every turn ended via natural exit** (forced_synthesis=false) within ≤2 steps —
  comfortably inside the 6-step budget, so the cap is a safety net, not a common path.
- **Graceful degradation**: tool-01's `query_health` hit a DB error and the tool-layer
  fallback returned a safe payload; the GeneralAgent produced a grounded "try again"
  reply instead of crashing.

#### Comparison to the previous multi-subagent baseline

| Metric | Old pipeline (baseline `eval/old_archi/full`, 52 cases) | New orchestrator (representative, 5 cases) |
|---|---|---|
| Time split | reasoning 50.8% / tool 14.3% / **llm 34.9%** | reasoning ~50% / tool ~45% / **llm 0%** |
| LLM calls / turn | planner + per-subagent + separate critic | 2–3 (steps + 1 streamed answer) |
| Critic | separate ComplianceCritic LLM call | embedded in GeneralAgent SKILL.md (no extra call) |
| Streaming | cosmetic (buffer then re-chunk) | real token streaming (`stream=True`) |

The headline structural win is the elimination of the separate compliance-critic LLM
call (**llm phase 34.9% → 0%**) and fewer LLM round-trips per turn. Absolute
wall-clock latency on this box is dominated by the flaky-network timeouts and is not a
fair compute comparison; a re-run on a stable network is needed for true latency
deltas.
