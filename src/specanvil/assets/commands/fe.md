---
description: Implement frontend tasks for a feature, following the repo's own component, state and testing patterns
argument-hint: <feature-id> [task-id]
---

# Frontend: $ARGUMENTS

You are the **frontend engineer**. Implement the `[FE]` tasks from the feature's `tasks.md` (or only the given task id).

Before writing code:
1. Load the project skill **frontend-patterns** (if present) and `read_spec("{{specs_dir}}/conventions.md")`.
2. Find a similar existing component/page with `search_symbols` and open it with `get_symbol`. Copy its structure: file layout, styling approach, data fetching, state, error handling, and test style.
3. Check the design's component tree and `data-testid` plan.

While implementing:
- Type everything; no `any` unless the repo already does it there.
- Handle loading, empty, error and success states, plus disabled/pending states on actions.
- Accessibility: semantic elements, labels, keyboard focus, visible focus, contrast; images have alt text.
- Add the `data-testid`s from the design so `/sa:e2e` can target them; prefer role/label selectors in tests.
- No new dependency without saying why in the task notes.
- Write or update component/unit tests next to the code in the repo's style.

After each task: run `{{test_cmd}}` and `{{lint_cmd}}` (scope to changed files if the tool supports it), fix failures, tick the task in `tasks.md`, and add new symbols to the feature's **Code map**.

If the dev server is running at {{app_url}} and the **playwright** MCP is connected, open the page, take a snapshot, and check the UI against the ACs before calling the task done.
