# SpecForge vs jCodeMunch

jCodeMunch (jgravelle/jcodemunch-mcp, measured at v1.108.431) is the most mature token-saving code-index MCP server, and it publishes a reproducible benchmark. This comparison uses **jCodeMunch's own benchmark**: its pinned repos, five queries, workflow and tokenizer. jCodeMunch's numbers come from **its own harness** (`benchmarks/harness/run_benchmark.py`, default `served` method); SpecForge runs the same workflow through its MCP server over stdio.

## 1. Token efficiency (jCodeMunch's benchmark)

Workflow per query: `search_symbols` (top 5) → fetch the top 3 symbols. 5 queries × 3 repos = 15 lookups. Tokens: tiktoken `cl100k_base`, counted on the text the server returns. Session dedupe **off**: every lookup measured fresh.

| Repo (pinned commit) | jCodeMunch | SpecForge 0.4 |
|---|---|---|
| expressjs/express `1faf228` | — | 1,979 |
| fastapi/fastapi `a64dfbb` | — | 2,581 |
| gin-gonic/gin `75ccf94` | — | 1,526 |
| **Total (15 lookups)** | **17,581** | **6,086 (−65%)** |

The default and "every body in full" numbers are identical in 0.4: the picks are now small, precise symbols, so the large-symbol skeleton never triggers. (In 0.3: 9,306 default and 12,148 full.)

### Top-3 picks by hand (the benchmark doesn't score relevance)

| Query | jCodeMunch | SpecForge 0.4 |
|---|---|---|
| express · router route handler | `Route`, `debug`, `Router` (variables) | `Route._handles_method`, `Layer.handle_request`, `Layer.handle_error` |
| express · param bind | `mergeParams`, `paramCallback`, `decode_param` | `req.param`, `paramCallback`, `param` |
| fastapi · middleware | `middleware`, a test helper, `AsyncExitStackMiddleware` | `FastAPI.middleware`, `middleware`, `run_middleware` |
| fastapi · request response | `request_response`, `app`, `http_exception_handler` | `request_response`, 2 test helpers |
| gin · router route handler | `staticFileHandler`, `createStaticHandler`, `DELETE` | `RouterGroup.handle`, `RouterGroup.Handle`, `RouterGroup.combineHandlers` |
| gin · middleware | 3 test functions | `Use`, `RouterGroup.Use`, `Engine.Use` |
| gin · param bind | `param` (constant), `BindUnmarshaler`, `countParams` | `Context.Param`, `Param` (struct), `Context.AddParam` |

Both tools mostly land on framework internals. SpecForge's picks are more often the dispatch and registration code itself, and Go methods are receiver-qualified. One query (fastapi · request response) is weaker for SpecForge.

## 2. Retrieval quality (SpecForge's evals, `tests/evals`)

| Eval | Metric | Result |
|---|---|---|
| 12 natural-language tasks → `get_context` (4k budget) | recall of needed symbols / MRR | **1.00 / 0.86** |
| 12 paraphrased tasks (no shared words) → `get_context` | expected symbol in full context | 3/12 keyword-only → **8/12 with local embeddings** |
| Same paraphrases → `search_symbols` top 5 | hit@5 | lexical 0.08 · hybrid 0.25 · **semantic 0.50** · hybrid+vectors 0.42 |

## 3. Indexing

| Repo | jCodeMunch cold `index --no-ai-summaries` | SpecForge 0.4 cold `index --full` |
|---|---|---|
| express | 1.72 s | **0.37 s** |
| fastapi | 7.07 s | **2.54 s** |
| gin | 1.56 s | **0.45 s** |

SpecForge 0.4 also builds the call graph, inheritance table and resolved import graph during that time. Incremental refresh on Django (5.6k files): 0.11 s for no changes, 0.29 s for one file.

## 4. Symbol coverage

| Repo | jCodeMunch (its methodology table) | SpecForge 0.3 | SpecForge 0.4 |
|---|---|---|---|
| express | 1,085 | 269 | **712** |
| fastapi | 13,699 | 5,576 | **8,137** |
| gin | 1,822 | 1,037 | **1,680** |

0.4 adds variables, constants, type aliases, fields (Python, JS/TS, Java/Kotlin/C#, Go structs, PHP), Go struct/interface types, Rust consts/statics/types, C `#define`/`typedef`, Ruby constants, and receiver-qualified Go methods. jCodeMunch still reports more. Part of the gap is counting policy: SpecForge deliberately skips function-local variables and one-letter names.

## 5. Analysis tools

| Capability | jCodeMunch | SpecForge 0.4 |
|---|---|---|
| Call hierarchy (callers / callees, transitive) | ✔ | ✔ `call_hierarchy`, resolved by scope, imports and **receiver** (`crud.create_user`) |
| Class hierarchy | ✔ | ✔ `class_hierarchy` (extends / implements / `:` bases / Rust `impl … for`) |
| Importance (PageRank) | ✔ | ✔ `symbol_importance` |
| Dead code | ✔ | ✔ `dead_code`: graph prefilter + batched word-search verification; decorated / framework hooks excluded |
| Dependency cycles | ✔ | ✔ `dependency_cycles` (file / module, Tarjan) |
| Coupling metrics | ✔ | ✔ `coupling` (Ca, Ce, instability) |
| Layer violations | ✔ | ✔ `layer_violations` (configurable layers) |
| PR risk score | ✔ | ✔ `pr_risk` (complexity, callers, tests, churn, size, with review tips) |
| AST pattern search | custom mini-DSL + 10 presets | **standard tree-sitter query language** + 10 presets across up to 11 languages |
| Refactoring plans | rename, move, extract, signature | rename (strings / comments excluded, scoped, conflicts), signature (call sites + overrides), move |
| Hotspots (complexity × churn) | ✔ | ✔ |
| Blast radius / importers | ✔ | ✔ `impact_of` (transitive) |
| Total tools | 94 | 43 |

Remaining jCodeMunch-only features include an "extract" refactor, signal chains, a tectonic map, symbol provenance, turn planning and many more niche tools. SpecForge covers the commonly used analyses with fewer, broader tools, which keeps the per-turn schema small (next section).

## 6. Tool-schema cost per turn

| Surface | jCodeMunch | SpecForge 0.4 |
|---|---|---|
| Default | 6 router tools, ~1,050 tokens (others reached via `menu` / `order` / `route`) | `core`: 9 tools, ~1,380 tokens |
| Mid | — | `standard` 23 tools ~2,850 · `analysis` 29 tools ~3,360 |
| Everything | 94 tools, ~28,142 tokens | **43 tools, ~4,980 tokens** |

## 7. Semantic search, summaries, sources, watch

| Feature | jCodeMunch | SpecForge 0.4 |
|---|---|---|
| Semantic / hybrid search | Optional embeddings | Body-vocabulary hybrid (no download) + optional local embeddings (fastembed ONNX, or Ollama / OpenAI-compatible **localhost** endpoint). Fusion weights tuned on two evals. Model downloaded only by the explicit `specforge embed`; query-time loads are offline (verified: 0 network connections) |
| AI summaries | Provider APIs (Anthropic, Gemini, OpenAI-compatible…) or local | Written by **your own Claude session** via MCP on a cheap model from your profile. No extra API keys. Cached by code hash, marked `≈` |
| Prebuilt packs | Starter packs for popular frameworks (some licensed) | **Dependency packs from your installed versions** (`node_modules`, virtualenv / site-packages, Go module cache), cached per version and shared across repos; query with `source="pkg:react"` |
| GitHub repo indexing | ✔ | ✔ `specforge source add gh:owner/repo[@ref]` |
| Other local folders | — | ✔ `path:../other-service` |
| Watch mode | ✔ | ✔ `specforge watch [--embed]` + PostToolUse re-index + MCP auto-refresh |

## What remains in jCodeMunch's favour

- More tools overall, including niche analyses SpecForge doesn't have.
- More symbols counted per repo.
- Curated starter packs that don't require the dependency to be installed.
- A long public track record and user base. SpecForge's evidence is this repo's tests, evals and benchmarks.

## What SpecForge adds beyond a code index

- **−65% tokens** on jCodeMunch's own benchmark, and **3–4× faster** cold indexing including graph building.
- **Programmatic enforcement:** server-side session dedupe, secret guard hook, schema-validated test plans, test-verified milestones, documentation completeness gate, local-only guarantees with tests.
- **MIT licence** (jCodeMunch: free for non-commercial use; commercial licences $79–$1,999 per its PyPI page).
- The complete spec-driven package: specs with drift checks, 13 agents, 21 commands from grooming to Playwright E2E, a unit-test planner/executor, ticket backfill, documentation for every repo type, per-role models.

*Measured 2026-11-10 in a 2-vCPU container. jCodeMunch features and prices are from its PyPI page (v1.75.0 description) and installed v1.108.431; check current versions before quoting.*
