---
description: Show or change your SpecForge profile (models per agent role, ticket backfill settings)
argument-hint: [set <key> <value> | unset <key>]
---

# Profile: $ARGUMENTS

- No arguments: run `specforge profile` in the shell and show the result as a short table.
- `set <key> <value>` / `unset <key>`: run `specforge profile $ARGUMENTS` in the shell. It validates the value, saves it to your personal profile (`~/.specforge/profile.json`, never committed) and re-renders this repo's agents so the change applies to the next subagent run.

Keys: `models.<role>` (roles: test_planner, test_executor, spec_analyst, architect, frontend, backend, db, qa, e2e, reviewer, backfill; values: opus, sonnet, haiku, fable, inherit or a full model ID), `backfill.enabled`, `backfill.statuses` (comma list), `backfill.since` (YYYY-MM-DD), `backfill.limit`, `backfill.batch_size`.
