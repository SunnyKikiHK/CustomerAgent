# Tenant Skills

Skills are hot-loadable, per-tenant capability files. At runtime the
`SkillManager` (`apps/agent_service/src/agent/runtime/skills.py`) scans
`skills/<tenant_id>/**/SKILL.md`, and when a request matches, injects the skill
body into the matching subagent's system prompt (see
`apps/agent_service/src/agent/runtime/prompts.py`).

A **Skill** is a modular, single-responsibility unit of *procedural knowledge* —
the SOP that tells a specialist *how* to do its job. It is distinct from a
*tool* (an executable capability with a typed schema, in
`packages/tool_system/`). A prompt is not necessarily a skill; a skill is a
prompt organized as a discoverable, role-scoped capability. (See
`../../skill_explan.md` for the full Skills-vs-MCP discussion.)

## Layout

One folder per skill, with a `SKILL.md` main file:

```
skills/<tenant_id>/<skill_name>/SKILL.md
```

`<tenant_id>` is the tenant UUID (the demo tenant is `demo-tenant`).

## SKILL.md format

A simple YAML-ish front matter block, then a Markdown body:

```markdown
---
name: billing-support
description: >
  Short, precise description of what the skill does and when to use it.
keywords: refund, invoice, charge        # optional; empty = always match for the agent
agents: billing                          # which subagent role(s) this applies to
enabled: true
---

# Billing Support Skill

## Role
...
## Workflow
...
## Escalation conditions
...
## Prohibitions
...
```

Fields consumed by the loader:

- `name` — display name injected into the prompt.
- `description` — short summary, shown in `/skills`.
- `keywords` — comma-separated trigger words. If present, the skill is injected
  only when the user message contains one. **Empty ⇒ always injected** for its
  agent (role-matched).
- `agents` — subagent role(s) this skill applies to. Empty ⇒ all agents.
  Valid roles: `general`, `technical`, `billing`, `escalation`,
  `health_analysis`, `outreach_draft`, `playbook_retrieval`.
- `enabled` — `true`/`false`.

## Role-matched personas (current design)

Each subagent role owns exactly one persona skill, injected on every turn that
role runs (`agents: <role>`, `keywords` empty):

| Role                | Skill folder                      |
|---------------------|-----------------------------------|
| `general`           | `general_support/`                |
| `technical`         | `technical_support/`              |
| `billing`           | `billing_support/`                |
| `escalation`        | `escalation_handling/`            |
| `health_analysis`   | `health_analysis/`                |
| `outreach_draft`    | `outreach_drafting/`              |
| `playbook_retrieval`| `playbook_retrieval/`             |
| `compliance_critic` | `compliance_critic/`              |

The `compliance_critic` persona is the Reflector-phase reviewer. It is not a
ReAct subagent role: it is loaded via `SkillManager.persona_for("compliance_critic")`
and injected as the critic's system prompt (with a code-owned fallback if the file
is missing).

The subagent `.py` classes keep only a one-line `ROLE_BRIEF` fallback (used if
the skills dir is missing); the full SOP lives here.

## Writing guidance

- Put the most important rules first — long bodies are truncated to the prompt
  budget.
- One responsibility per skill. Don't mix billing and technical rules.
- Include stable sections: Role, Workflow, Escalation conditions, Prohibitions.
- For sensitive data (passwords, one-time codes, API keys, full card numbers),
  state clearly that they must not be collected or exposed.
- Use conservative wording for anything you cannot guarantee.

## Playbooks vs skills

`skills/<tenant>/playbooks/*.md` are **RAG corpus** documents embedded into
pgvector and retrieved by the `query_playbooks` tool — they are *not* SKILL.md
personas and are not injected by the SkillManager.

## Hot reload

Edit a `SKILL.md`, then reload without restarting:

```bash
curl -X POST "http://localhost:8000/skills/reload?tenant_id=<tenant_id>"
curl "http://localhost:8000/skills?tenant_id=<tenant_id>"   # inspect load result
```
