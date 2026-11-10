# SpecForge vs jCodeMunch

jCodeMunch (jgravelle/jcodemunch-mcp, measured at v1.108.431) is the most mature token-saving code-index MCP server, and it publishes a reproducible benchmark. This comparison uses **jCodeMunch's own benchmark**: its pinned repos, five queries, workflow and tokenizer. jCodeMunch's numbers come from **its own harness** (`benchmarks/harness/run_benchmark.py`, default `served` method); SpecForge runs the same workflow through its MCP server over stdio (`benchmarks/vs_jcodemunch.py`).

## 1. Token efficiency (jCodeMunch's benchmark)

Workflow per query: `search_symbols` (top 5) → fetch the top 3 symbols. 5 queries × 3 repos = 15 lookups. Tokens: tiktoken `cl100k_base`, counted on the text the server returns. Session dedupe **off**: every lookup measured fresh.

| Repo (pinned commit) | jCodeMunch | SpecForge 0.5 |
|---|---|---|
| expressjs/express `1faf228` | — | 1,800 |
| fastapi/fastapi `a64dfbb` | — | 2,604 |
| gin-gonic/gin `75ccf94` | — | 1,520 |
| **Total (15 lookups)** | **17,581** | **5,924 (−66%)** |

0.4: 6,086 (−65%). 0.3: 9,306. Adding config keys and documentation sections to the index in 0.5 did not move any of the 15 picks onto non-code results.

### Top-3 picks by hand (the benchmark doesn't score relevance)

| Query | jCodeMunch | SpecForge 0.5 |
|---|---|---|
| express · router route handler | `Route`, `debug`, `Router` (variables) | `Route._handles_method`, `Layer.handle_request`, `Layer.handle_error` |
| express · param bind | `mergeParams`, `paramCallback`, `decode_param` | `app.param`, `req.param`, `paramCallback` |
| fastapi · middleware | `middleware`, a test helper, `AsyncExitStackMiddleware` | `FastAPI.middleware`, `AsyncExitStackMiddleware`, its `__init__` |
| fastapi · request response | `request_response`, `app`, `http_exception_handler` | `request_response`, 2 test helpers |
| gin · router route handler | `staticFileHandler`, `createStaticHandler`, `DELETE` | `RouterGroup.handle`, `RouterGroup.Handle`, `RouterGroup.combineHandlers` |
| gin · middleware | 3 test functions | `Use`, `RouterGroup.Use`, `Engine.Use` |
| gin · param bind | `param` (constant), `BindUnmarshaler`, `countParams` | `Context.Param`, `Param` (struct), `Context.AddParam` |

Both tools mostly land on framework internals. SpecForge's picks are more often the dispatch and registration code itself, and Go methods are receiver-qualified. One query (fastapi · request response) is weaker for SpecForge.

## 2. Retrieval quality (SpecForge's evals, `tests/evals`, gated in CI)

| Eval | Metric | Result |
|---|---|---|
| 12 natural-language tasks → `get_context` (4k budget) | recall of needed symbols / MRR | **1.00 / 0.88** (CI fails below 0.95 / 0.75) |
| 12 paraphrased tasks (no shared words) → `get_context` | expected symbol in full context | 3/12 keyword-only → **8/12 with local embeddings** |
| Same paraphrases → `search_symbols` top 5 | hit@5 | lexical 0.08 · hybrid 0.25 · **semantic 0.50** · hybrid+vectors 0.42 |

## 3. Indexing

| Repo | jCodeMunch cold `index --no-ai-summaries` | SpecForge 0.5 cold, through the MCP server |
|---|---|---|
| express | 1.72 s | **0.27 s** |
| fastapi | 7.07 s | **3.44 s** |
| gin | 1.56 s | **0.39 s** |

SpecForge also builds the call graph, inheritance table and resolved import graph in that time, and in 0.5 indexes twice as many symbols on fastapi (section 4). 0.5 fixed two things that inflated earlier timings: a first `reindex(full=True)` no longer indexes twice, and a full rebuild clears tables once instead of deleting row by row. Incremental refresh on Django (5.6k files): 0.11 s for no changes, 0.29 s for one file.

## 4. Symbol coverage

| Repo | jCodeMunch (its methodology table) | SpecForge 0.4 | SpecForge 0.5 total | of which code / config keys / doc sections |
|---|---|---|---|---|
| express | 1,085 | 712 | 994 | 712 / 179 / 103 |
| fastapi | 13,699 | 8,137 | **26,842** | 8,137 / 3,386 / 15,319 |
| gin | 1,822 | 1,680 | **1,960** | 1,680 / 108 / 172 |

jCodeMunch's totals include configuration keys (for example 189 from express's `.github` workflows). 0.5 indexes those too (YAML, JSON, TOML, INI/properties, `.env` — key names only, values never stored; lockfiles skipped), plus Markdown / RST / AsciiDoc headings as sections whose line range runs to the next heading, so `get_symbol` returns one section of a long document. fastapi's count is dominated by its translated docs. Both kinds rank below code unless the query mentions docs, readme or a guide. Code-symbol policy is unchanged: function-local variables and one-letter names are skipped on purpose.

## 5. Tools: every jCodeMunch tool mapped

jCodeMunch v1.108.431 registers 98 tool names (counted from its `server.py`); 94 are listed in its full tier. SpecForge 0.5 has 83 tools. Where jCodeMunch has three tools for one question, SpecForge often has one with a parameter, and some jCodeMunch tools are workflow guides that SpecForge ships as slash commands instead of schema.

**Search and retrieval**

| jCodeMunch | SpecForge |
|---|---|
| `search_symbols` | `search_symbols` (`mode=` lexical / hybrid / semantic / auto) |
| `search_text` | `search_code` |
| `get_symbol_source`, `get_context_bundle` | `get_symbol`, `get_symbols` (batch, session dedupe, skeletons, `focus=`) |
| `get_ranked_context`, `assemble_task_context` | `get_context(task, budget)` |
| `get_file_outline`, `get_file_content`, `get_file_tree` | `file_outline`, `get_file_content`, `file_tree` |
| `get_repo_outline`, `get_repo_map` | `repo_map` |
| `find_references`, `check_references` | `find_references` (`include_docs=`) |
| `find_importers` | `dependency_graph(direction="in")`, `impact_of` |
| `search_ast` | `search_ast` (standard tree-sitter query language + 10 presets) |
| `search_columns`, `winnow_symbols`, `suggest_queries`, `plan_turn` | same names |

**Graph and architecture**

| jCodeMunch | SpecForge |
|---|---|
| `get_call_hierarchy`, `get_class_hierarchy`, `get_symbol_importance` | `call_hierarchy`, `class_hierarchy`, `symbol_importance` |
| `find_dead_code`, `get_dependency_cycles`, `get_coupling_metrics`, `get_layer_violations` | `dead_code`, `dependency_cycles`, `coupling`, `layer_violations` |
| `get_hotspots`, `get_pr_risk_profile` | `hotspots`, `pr_risk` |
| `get_blast_radius`, `get_impact_preview` | `impact_of` (transitive), `check_safety` |
| `check_delete_safe`, `check_edit_safe`, `check_rename_safe` | `check_safety(action=delete / edit / rename)` |
| `get_dependency_graph` | `dependency_graph` (file or folder) |
| `get_architecture_metrics`, `get_tectonic_map`, `get_signal_chains` | `architecture_metrics`, `tectonic_map`, `signal_chains` |
| `get_symbol_complexity`, `get_file_risk`, `get_untested_symbols` | `symbol_complexity` (symbol, file or folder), `file_risk`, `untested_symbols` |
| `find_similar_symbols`, `get_related_symbols`, `get_extraction_candidates` | `similar_symbols`, `related_symbols`, `extraction_candidates` |
| `get_decorator_census`, `get_endpoint_impact`, `get_parity_map`, `find_implementations` | `decorator_census`, `endpoint_impact` (incl. frontend callers), `parity_map`, `find_implementations` (incl. Go implicit interfaces) |
| `get_repo_health`, `diff_health_radar` | `repo_health` (trend vs the previous run is built in) |
| `plan_refactoring` | `plan_refactor` (rename / signature / move; extract via `extraction_candidates`) |
| `render_diagram` | `render_diagram`, `specforge diagram -o x.html` |

**History and runtime**

| jCodeMunch | SpecForge |
|---|---|
| `get_changed_symbols`, `get_symbol_diff` | `changed_symbols`, `symbol_diff` |
| `get_symbol_provenance`, `get_churn_rate`, `digest`, `get_delivery_metrics` | `symbol_provenance` (with ticket keys and decision lines), `churn_rate`, `digest`, `delivery_metrics` |
| `get_runtime_coverage`, `find_hot_paths`, `find_unused_paths` | `runtime_coverage` (hot paths and never-executed code from lcov / Cobertura / JaCoCo) |
| `import_runtime_signal` | partial: coverage files are read directly; **no OTel trace ingestion or per-symbol latency** |

**Session, telemetry, config**

| jCodeMunch | SpecForge |
|---|---|
| `get_session_context`, `get_session_snapshot` | `session_snapshot` (survives compaction) |
| `get_session_stats`, `analyze_perf` | `token_savings`, `tool_stats` (calls, avg / p95 ms, tokens) |
| `get_redaction_log`, `audit_agent_config` | `redaction_log`, `audit_agent_config` |
| `check_embedding_drift` | built in: vectors are stored per model and never mixed; `index_status` reports coverage per model |
| `menu`, `order`, `route`, `set_tool_tier`, `announce_model` | tool tiers chosen at startup (`SPECFORGE_TOOLS` / config); not switched mid-session |
| `jcodemunch_guide`, `workflow`, `explore`, `triage`, `assess`, `trace` | slash commands and CLAUDE.md block (`/sf:analyze`, `/sf:verify`…) over `repo_map`, `repo_health`, `pr_risk`, `call_hierarchy` + `signal_chains` |
| `finalize_handoff` | different shape: `/sf:ship` writes `pr.md`; the docs engine writes and gates repository docs |

**Indexing and sources**

| jCodeMunch | SpecForge |
|---|---|
| `index_repo` (GitHub), `index_folder`, `index_dependency` | `add_source gh:` / `path:` / `pkg:` (installed version) and `add_pack` (registry, no install) |
| `index_file`, `register_edit`, `invalidate_cache` | PostToolUse re-index hook, 20 s auto-refresh, `reindex(full=True)` |
| `get_watch_status` | `index_status`, `specforge watch` |
| `list_repos`, `list_workspaces`, `resolve_repo`, `get_cross_repo_map` | `list_workspaces`, `list_sources`, `cross_repo_map` |
| `get_group_contracts` | partial: `cross_repo_map` shows cross-repo imports, not a contract summary |
| `get_project_intel` | config-key symbols + `doc_facts` (Docker, CI, compose, env templates, IaC) |
| `embed_repo`, `summarize_repo` | `specforge embed` (CLI on purpose: the only step allowed to download), `summaries_needed` + `save_summaries` |

**Not implemented:** `suggest_corrections` (mining ranking regret from telemetry), `tune_weights` (fusion weights are tuned offline on the evals and fixed, so results are reproducible), `test_summarizer` (no provider API keys; summaries come from your own Claude session).

**SpecForge-only:** `entry_points`, `flow_edges` (route → handler, event emit → listeners, DI), spec tools (`read_spec`, `list_specs`, `spec_drift`), unit-test pipeline (`test_surface`, `validate_test_plan`, `next_test_milestone`, `update_test_milestone`), documentation engine (`doc_plan`, `doc_facts`, `doc_check`, `doc_register`), ticket tools (`ticket_keys`, `ticket_trace`, `backfill_progress`), `project_profile`, `get_profile`.

## 6. Tool-schema cost per turn

| Surface | jCodeMunch | SpecForge 0.5 |
|---|---|---|
| Default | 6 router tools, ~1,050 tokens (others reached via `menu` / `order` / `route`) | `core`: 9 tools, ~1,380 tokens |
| Mid | — | `standard` 29 tools ~3,520 · `analysis` 56 tools ~6,380 |
| Everything | 94 tools, ~28,142 tokens | **83 tools, ~9,000 tokens** |

Measured as tiktoken `cl100k_base` over each tool's name, description and input schema.

## 7. Semantic search, summaries, packs, watch

| Feature | jCodeMunch | SpecForge 0.5 |
|---|---|---|
| Semantic / hybrid search | Optional embeddings | Body-vocabulary hybrid (no download) + optional local embeddings (fastembed ONNX, or Ollama / OpenAI-compatible **localhost** endpoint). Model downloaded only by the explicit `specforge embed`; query-time loads are offline (verified: 0 network connections) |
| AI summaries | Provider APIs (Anthropic, Gemini, OpenAI-compatible…) or local | Written by **your own Claude session** via MCP on a cheap model from your profile. No extra API keys. Cached by code hash, marked `≈` |
| Starter packs | Curated packs for popular frameworks | **51 curated packs** fetched from npm / PyPI / Go proxy / GitHub at any version, **no install needed**; plus packs from your installed versions; `.sfpack` export / import for offline sharing; safe extraction (no absolute paths, no `..`, size caps — tested) |
| GitHub repo indexing | ✔ | ✔ `specforge source add gh:owner/repo[@ref]` |
| Watch mode | ✔ | ✔ `specforge watch [--embed]` + PostToolUse re-index + MCP auto-refresh |

## 8. Track record

jCodeMunch has a long public history and a user base; SpecForge is new. What SpecForge offers instead is evidence you can re-run:

- **CI on every push** (`.github/workflows/ci.yml`): unit tests on Ubuntu, macOS and Windows × Python 3.10 / 3.12 / 3.13; the retrieval eval as a **quality gate** (recall@any ≥ 0.95, MRR ≥ 0.75); a wheel built and smoke-tested in a clean environment.
- **Tagged releases** (`release.yml`) with the matching CHANGELOG section as release notes.
- **Reproducible benchmarks**: `benchmarks/vs_jcodemunch.py` and the eval runners in `tests/evals`, with result JSONs committed.
- `SECURITY.md` (private reporting, local-only guarantees and the tests behind them) and `CONTRIBUTING.md` (every ranking change must keep the eval gate green).

## What remains in jCodeMunch's favour

- A long public track record and user base.
- OTel runtime-trace ingestion with per-symbol latency, ranking-regret mining, and runtime tier switching.
- Its default surface is slightly smaller (6 router tools vs 9).

## What SpecForge adds beyond a code index

- **−66% tokens** on jCodeMunch's own benchmark, **2–6× faster** cold indexing including graph building, and more symbols on two of the three benchmark repos.
- **Programmatic enforcement:** server-side session dedupe, secret guard hook, schema-validated test plans, test-verified milestones, documentation completeness gate, local-only guarantees with tests.
- **MIT licence** (jCodeMunch: free for non-commercial use; commercial licences $79–$1,999 per its PyPI page).
- The complete spec-driven package: specs with drift checks, 13 agents, 21 commands from grooming to Playwright E2E, a unit-test planner/executor, ticket backfill, documentation for every repo type, per-role models.

*Measured 2026-11-10 in a 2-vCPU container. jCodeMunch's token and timing numbers are from its harness at v1.108.431 on the same commits; its tool list is from that version's `server.py`. Features and prices change: check current versions before quoting.*
