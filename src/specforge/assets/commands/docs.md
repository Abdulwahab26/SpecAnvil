---
description: Create or refresh every document this repo needs (BE, FE, QA automation, Azure Functions, Lambda, infra, library, CLI...), grounded in code and checked for completeness (runs on your docs model)
argument-hint: [all | missing | stale | <doc-id ...>]
---

# Repository documentation: $ARGUMENTS

1. `doc_plan()` — detected archetypes (with evidence), every required document, and its status: **missing**, **unverified** (exists, never checked), **stale** (the code it describes changed since it was written), **fresh**.
2. Pick the set: `missing` (default when no argument), `stale`, `all`, or the listed doc ids. Show the user the list with paths before writing anything; for `all` on a repo with existing docs, confirm that existing files will be updated in place (human content preserved).
3. Write in parallel: run up to 4 **doc-writer** subagents at a time (they run on the docs model from your profile, see `get_profile`). Give each subagent exactly one doc id. Order: `architecture`, `configuration`, `api-reference` / `functions-catalog` / `qa-framework` first (other docs link to them), `docs-index` and `readme` last.
4. After each batch: `doc_check` every written doc yourself. Re-dispatch any that fail with the reported problems (max 2 retries per doc, then list it as needing human input).
5. Finish with `doc_plan()` again and report a table: document, path, status, open questions count. Suggest adding `specforge docs check --strict` to CI so stale or incomplete docs fail the build.

Never write secret values. Never delete existing documents.
