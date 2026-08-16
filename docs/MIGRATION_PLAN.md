# Migration Plan — Orchestrator-Workers Conversation + Finalize Stubs

> Engineer: Hermes (reviews, verifies, gates each milestone)
> Coder: Claude Code (`-p` print mode, DeepSeek-backed Anthropic-compat shim)
> Branch: `fix/orchestrator-workers-migration` (off `plan_execute_conversation`)

## Problem statement (agreed)

1. The conversation system is too slow (P50 ~44s, P95 ~137s, network-inflated).
2. Docs recommend switching the **conversation** path to the Orchestrator-Workers
   (GeneralAgent ReAct loop) architecture — currently scaffolded but not wired.
3. Several functions are unfinished (signal stubs, mock email, no scheduling).

## Hard constraints

- **Signal system keeps P-E-R** (Planner → Executor → Reflector / ComplianceCritic).
  The signal path architecture is **off-limits**. Only finalize its stubs/email/scheduling
  within the existing P-E-R + critic framework. Do not restructure it.
- **Only the conversation path changes architecture** (to Orchestrator-Workers).
- **Latency targets:** P50 ≤ 30s worst case; ~10s average. If these are not reachable,
  report the lowest achieved latency instead of hiding it.
- **Never open/read/parse/edit `config.sh`** (live keys). Use `.env.example` for var names.
- **All Python execution goes through WSL.** Claude Code's Bash runs in git-bash, so wrap
  every Python/test command:
  `wsl.exe -e bash -c "cd /mnt/d/OVERALL_NOTEBOOK/agent/no_name/CustomerAgent && source .venv/bin/activate && export PYTHONPATH=\$PWD && <cmd>"`
- Absolute imports only, anchored at repo root. PEP 8; no `===` in comments/docstrings.
- Model names resolved via `packages/agent/src/models.py`, never hardcoded.

## Milestones

### M1 — Clean baseline & freeze source of truth
- Commit pre-existing uncommitted changes as `chore: baseline pre-migration WIP`.
- Run offline suite: `pytest tests/ -q -k 'not postgres and not live'` → expect 163 passed.
- **Decision (default):** Orchestrator-Workers becomes canonical for conversation;
  old P-E-R planner kept as runtime fallback during M2, removed in M3.
- **Gate:** green offline suite + committed baseline.

### M2 — Wire the conversation loop into the live path (conversation only)
- Rewire `chat_handler.py` → `ConversationOrchestrator.run_stream()/run()` →
  `ConversationLoop` (GeneralAgent ReAct), replacing the old
  `run_conversation_agent` → `ConversationOrchestrator.run()` P-E-R path.
- Ensure `delegate_billing/technical/escalation`, real token streaming
  (`LLMClient.stream`), and Forced Synthesis are connected end-to-end.
- **Signal path untouched.**
- **Gate:** `tests/test_conversation_loop.py` green; a manual `POST /chat/turn`
  streams a critic-compliant reply.

### M3 — Remove dead code & unify docs (conversation only)
- Retire `conversation_planner.py`, `llm_planner.py`, `capability_catalog.py` once
  confirmed unreferenced.
- Update `CLAUDE.md`: remove "scaffolded, not wired"; document canonical path + fallback.
- **Gate:** zero references to removed modules; full offline suite green.

### M4 — Fix eval quality + the safety leak
- Improve weak categories: tool_error (44.4%), edge (50%), ambiguous (63.6%).
- Fix `edge-10` hard-rule trip (`SELECT * FROM` leak) — harden critic/injection rules.
- Re-run 52-case live eval.
- **Gate:** pass rate > 80%, 0 hard-rule trips, no regressions.

### M5 — Latency validation
- Re-run eval on stable network; confirm `llm` phase → ~0%, fewer calls/turn.
- **Gate:** P50 ≤ 30s, avg ~10s. If missed, record and report the lowest achieved.

### M6 — Finalize signal-system stubs (NO architecture change)
- Route NPS survey invite + QBR delivery through the gated `send_email` path
  (currently mock / `mark_qbr_delivered` only).
- Resolve prototype stubs (`check_human_availability`, `process_refund`,
  `escalate_to_human`): implement or explicitly scope with a TODO gate.
- Add detector scheduling (cron for scan / NPS / QBR) or defer with rationale.
- **Gate:** `scripts/verify_e2e.py` + `smoke_live_api.py` pass; email via `EMAIL_PROVIDER=console`.

### M7 — Frontend (conditional)
- Frontend is already SSE-ready. Verify the SSE event contract (`status`/`token`/
  `done`/`error`) still matches after M2 wiring; update only if event names/shape changed.
- **Gate:** chat streams token-by-token in the browser against the running API.

### M8 — Docs reconciliation + full verification gate
- Fill empty `note/signal_system.md`; reconcile EN/zh/`AGENT_PLAN.md`/`CLAUDE.md`.
- Run full §17 gate: fresh 1536-dim DB (`down -v` + `start.sh`), `pytest tests/ -v`,
  `seed_playbooks.py`, `verify_e2e.py`, signals scan → `done`.
- **Gate:** all steps green; docs agree with code.

### M9 — Finalize
- Group commits per milestone, push branch, open PR with eval before/after.

## Operating rules for Claude Code delegation

- Print mode: `claude -p "<task>" --allowedTools "Read,Edit,Write,Bash" --max-turns N`,
  `workdir` = repo root. One milestone (or sub-step) per call.
- Every prompt restates: WSL wrapper, no `config.sh`, signal path untouched,
  absolute imports, target files.
- Hermes verifies after each delegation (`git diff` + test output) before advancing.
- Claude's "done" is a self-report; always confirm against real output.
