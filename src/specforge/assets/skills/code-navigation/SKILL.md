---
name: code-navigation
description: How to explore this codebase with minimal tokens using the specforge MCP. Use whenever you need to find, understand or change code.
---

# Token-efficient code navigation

Reading whole files is the biggest token cost in agentic coding. This repo has a local code index (specforge MCP). Use it in this order:

| Need | Tool | Typical cost |
|---|---|---|
| Orientation in an unfamiliar area | `repo_map` | ~1-2k tokens for the whole repo |
| "Where is the code for X?" | `get_context("X")` or `search_symbols("X")` | budgeted (default {{budget}} tokens) |
| Structure of a file | `file_outline(path)` | signatures only, ~10-20% of the file |
| One function/class | `get_symbol(id or name)` | just that symbol |
| Who calls this? | `find_references(name)` | one line per usage |
| What did I change? | `changed_symbols()` | symbol list, not the diff |
| What breaks if I change this file? | `impact_of(path)` | file list |
| Spec content | `read_spec(path, section)` | one section |

Rules:
1. Never open a file "to understand the project" — use `repo_map` and `get_context`.
2. Before editing, `get_symbol` the exact symbol; read the full file only if you will change most of it.
3. Use symbol ids returned by one tool in the next call (`get_symbol("42")`) — it's unambiguous and cheap.
4. If the index looks stale after big changes, call `reindex`.
5. `token_savings` shows how much this saved; mention it in final reports.
