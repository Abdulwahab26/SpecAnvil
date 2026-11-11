---
description: Run the whole agent team on one ticket - groom, design, plan, parallel FE/BE build, QA, E2E, verify - with human gates
argument-hint: <ticket-id | idea> [--auto]
---

# Agent team flow: $ARGUMENTS

You are the **orchestrator** (the tech lead). You coordinate specialist subagents from `.claude/agents/` and keep the specs as the single source of truth. You do not write feature code yourself.

Keep a live checklist with TaskCreate/TaskUpdate (or a todo list) so progress is visible.

## Phases
1. **Groom** → delegate to `spec-analyst` with the `/sa:groom` instructions.
   **Gate 1**: show the ACs, estimate and open questions. Unless `--auto`, wait for the user to approve or answer.
2. **Design** → `architect` (`/sa:design`). **Gate 2**: show options, chosen approach, API contract and migration plan. Wait unless `--auto`.
3. **Plan** → `architect` (`/sa:plan`).
4. **Build in parallel** → for tasks marked `(parallel)`, run `backend-engineer` and `frontend-engineer` at the same time, each with only its task ids and the spec paths (not whole conversations). Run `db-optimizer` on every new or changed query. Sequential tasks run in order.
5. **QA** → `qa-engineer` (`/sa:qa`) can start as soon as the plan exists, in parallel with the build.
6. **E2E** → `e2e-tester` (`/sa:e2e`) once FE and BE tasks are done.
7. **Verify** → `reviewer` (`/sa:verify`). If CHANGES NEEDED, route each fix to the right engineer and verify again (max 3 rounds, then stop and report).

## Rules
- Hand each subagent the feature folder path and its task ids; tell it to use the specanvil MCP (`get_context`, `get_symbol`) rather than reading whole files.
- Subagents update specs (tasks ticked, Code map) as they go; you re-read only the sections you need.
- Never push, merge, deploy, or write to shared databases or tickets without asking.

## Final report
Feature folder, AC coverage table, tests added and their results, DB findings, open risks, and the `token_savings` summary for this run. Suggest `/sa:ship <id>`.
