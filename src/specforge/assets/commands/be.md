---
description: Implement backend tasks contract-first, with validation, error handling, migrations and tests in the repo's style
argument-hint: <feature-id> [task-id]
---

# Backend: $ARGUMENTS

You are the **backend engineer**. Implement the `[BE]` and `[DB]` tasks from the feature's `tasks.md` (or only the given task id).

Before writing code:
1. Load the project skill **backend-patterns** (if present) and `read_spec("{{specs_dir}}/conventions.md")`.
2. Find the closest existing endpoint/service with `search_symbols`, open it with `get_symbol`, and follow its layering, naming, DI, validation, error model and test style exactly.
3. Re-read the API contract and data model sections of `design.md`.

Implementation order:
1. **Contract**: request/response types, validation rules, error codes exactly as in design.md.
2. **Migration** (if any): forward + rollback, safe for large tables (no long locks; add columns nullable, backfill, then constrain; create indexes concurrently where the engine supports it).
3. **Domain/service logic** with unit tests for each AC, including negative paths.
4. **Persistence**: avoid N+1 (eager-load or batch), select only needed columns, paginate lists.
5. **Endpoint/handler** with authz checks and integration tests.
6. **Observability**: structured logs without PII; metrics if the design asks.

After each task: run `{{test_cmd}}`, fix failures, tick the task, and update the **Code map** with `` `path::Qualname` `` for new symbols. For any query that runs per request, run `/sf:db` on it before calling the feature done.
