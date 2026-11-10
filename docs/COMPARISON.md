# SpecForge vs jCodeMunch

jCodeMunch (jgravelle/jcodemunch-mcp, v1.108.431) is the most mature token-saving code-index MCP server, and it publishes a reproducible benchmark. This comparison uses **jCodeMunch's own benchmark**: its pinned repos, its five queries, its workflow and its tokenizer. jCodeMunch's numbers come from **its own harness** (`benchmarks/harness/run_benchmark.py`, default `served` method). SpecForge runs the same workflow through its MCP server over stdio.

## Token efficiency (jCodeMunch's benchmark)

Workflow per query: `search_symbols` (top 5), then fetch the top 3 symbols. 5 queries × 3 repos = 15 lookups. Tokens: tiktoken `cl100k_base`, counted on the text the server returns.

| Repo (pinned commit) | jCodeMunch | SpecForge default | SpecForge, every body in full |
|---|---|---|---|
| expressjs/express `1faf228` | — | 3,193 | 3,231 |
| fastapi/fastapi `a64dfbb` | — | 4,290 | 7,094 |
| gin-gonic/gin `75ccf94` | — | 1,823 | 1,823 |
| **Total (15 lookups)** | **17,581** | **9,306 (−47%)** | **12,148 (−31%)** |

"Default" includes SpecForge's large-symbol skeleton: a symbol over 1,500 tokens comes back as its signature and top-level statements, with nested blocks folded (`full=true` or `focus=` gets the rest). The "every body in full" column turns that off, to match jCodeMunch's behaviour of always returning full bodies. Session dedupe was **off** in both columns, so every lookup is measured fresh.

Raw outputs: `benchmarks/results_vs_jcodemunch_specforge.json`, `benchmarks/results_vs_jcodemunch_jcm.json`. Reproduce: `benchmarks/vs_jcodemunch.py`.

### Are the results comparable in quality?

Token counts alone can reward returning the wrong, small thing. This benchmark doesn't score relevance, so I compared the top-3 picks by hand:

| Query | jCodeMunch top picks | SpecForge top picks |
|---|---|---|
| express · request response | `handle_request`, `dispatch`, `router` | `Layer.handle_request`, `req.accepts`, `req.acceptsEncodings` |
| express · param bind | `mergeParams`, `paramCallback`, `decode_param` | `app.param`, `req.param`, `param` |
| fastapi · router route handler | `get_route_handler`, `_IncludedRouter`, `from_api_route` | `APIRoute.get_route_handler`, `APIRoute.handle`, `_IncludedRouter.handle` |
| fastapi · error exception | `request_validation_exception_handler`, `websocket_…handler`, `RequestValidationError` | `ValidationException.errors`, `RequestValidationError`, `ResponseValidationError` |
| gin · middleware | `TestMiddlewareAbort`, `TestMiddlewareWrite`, `TestBindMiddleware` | `Use`, `Use`, `TestMiddlewareAbort` |

Both tools land on framework internals for most queries. Neither wins clearly on every query. Separately, SpecForge's own retrieval eval (12 natural-language tasks, `tests/evals`) scores recall 1.00 and MRR 0.88 (MRR 0.83 on the 9 tasks not used for tuning).

## Indexing speed (cold, CLI, includes process start)

| Repo | jCodeMunch `index --no-ai-summaries` | SpecForge `index --full` |
|---|---|---|
| express | 1.72 s | **0.23 s** |
| fastapi | 7.07 s | **1.39 s** |
| gin | 1.56 s | **0.27 s** |

Incremental refresh after an edit on Django (5.6k files): SpecForge 0.14 s (see `BENCHMARKS.md`).

## Per-turn tool-schema cost

| Surface | jCodeMunch | SpecForge |
|---|---|---|
| Default surface | 6 router tools, ~1,050 tokens (other tools are reached via `menu`/`order`/`route`) | `core` tier: 9 tools, ~1,083 tokens |
| Everything exposed | 94 tools, ~28,142 tokens | 29 tools, ~3,071 tokens |

jCodeMunch's figure is its own `surface` command (bytes/4); SpecForge's is cl100k. Both are paid once per cache lifetime and then read from cache, so treat this as a secondary cost.

## Cost features

| Feature | jCodeMunch | SpecForge |
|---|---|---|
| Symbol-level retrieval, outlines, budgeted context | ✔ | ✔ |
| Never resend code already in context this session | Agent is asked to track it (prompt guidance) | **Enforced by the server**: unchanged bodies return a one-line reference; ledger resets on the PreCompact hook |
| Large symbols | Full body | **Skeleton by default**, `focus=` excerpts, `full=true` |
| Batch fetch | — | `get_symbols([...])` with the same dedupe |
| Nested-result dedupe | — | Children of a better-ranked parent are not returned twice |
| Measured savings ledger | ✔ | ✔ per tool, plus session resend savings |
| Model routing to cut spend | Adaptive tool tiering | **Planner/executor agents on different models**, chosen per role in the user profile |

## Where jCodeMunch is ahead

Stated plainly, so nobody is surprised:
- **Breadth of analysis:** 94 tools, including call and class hierarchies, PageRank importance, dead code, dependency cycles, coupling metrics, layer violations, PR risk scoring, an AST pattern DSL and refactoring plans. SpecForge has hotspots, transitive impact, references, changed symbols and spec drift.
- **Symbol coverage:** it also indexes variables, constants and types (express: 1,085 symbols vs SpecForge's 269). SpecForge indexes functions, methods, classes, interfaces, structs and enums, plus CommonJS prototype assignments.
- **Optional semantic/hybrid search** (embeddings) and AI summaries (local providers by default).
- Starter packs, watch mode, GitHub-repo indexing, and a long track record.

## Where SpecForge is ahead

- **Fewer tokens on jCodeMunch's own benchmark** (−47% default, −31% full bodies) with comparable picks.
- **5–7× faster cold indexing**, 0.1 s incremental refresh, and **provably no network** during indexing.
- **Programmatic enforcement:** session dedupe in the server, a secret guard hook, schema-validated test plans, test-verified milestones, documentation completeness checks.
- **MIT licence:** free for commercial use. jCodeMunch is free for non-commercial use only, with paid commercial licences ($79–$1,999 per its PyPI page).
- **A complete spec-driven package, not just an index:** bootstrap, specs with drift checks, 12 agents, 19 commands, grooming → design → FE/BE → DB → QA → Playwright E2E, unit-test planner/executor, ticket backfill, repo documentation for every repo type, per-role model profile.

*Measured on 2026-11-10 in a 2-vCPU container. jCodeMunch features and prices are taken from its PyPI page (v1.75.0 description) and its installed v1.108.431. Check the current versions before quoting.*
