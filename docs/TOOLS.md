# Tool reference

`specanvil-mcp` exposes 96 tools in five tiers. A session starts in `lite`; an agent raises the tier with `set_tool_tier` when a task needs more, or calls any single tool through `run_tool`. Pin a tier with `"tool_tier"` in `.specanvil/config.json` or the `SPECANVIL_TOOLS` environment variable. Every tool is also available from the shell: `specanvil tool <name> '<json arguments>'`.

This file is generated from the server's tool registry by `scripts/gen_tool_reference.py`; a test fails when it is out of date.

## `lite` (6 tools)

The default tier. Enough to navigate and read code within a token budget, and to reach every other tool.

| Tool | Description |
|---|---|
| `get_context` | Best code for a task within a token budget: full bodies of the most relevant symbols, signatures for the rest. Uses embeddings too when built. Code already sent this session is referenced, not resent. |
| `get_symbol` | Exact source of one symbol. ref = id, name, Class.method or 'path::Qualname'. Very large symbols come back as a skeleton (full=true for everything); focus='words' returns the signature and matching lines. Bodies already sent this session return a short reference unless force=true. |
| `repo_map` | Compact map of the repo: languages, modules, and key types/functions with file:line. Start here. source='pkg:react' / 'gh:owner/repo' maps an indexed dependency or external repo instead. |
| `run_tool` | Call any SpecAnvil tool by name, including ones not loaded in the current tier. set_tool_tier() lists them. |
| `search_symbols` | Ranked symbol search. mode: lexical (names/signatures), hybrid (+ body vocabulary + embeddings if built), semantic (embeddings only, for paraphrased intent), auto (lexical, falling back to hybrid when matches are weak). kind: class / function / method / interface / variable / constant / field / type. source: search a dependency/external repo. |
| `set_tool_tier` | Change the loaded tool set: tier lite / core / standard / analysis / full, and/or enable extra tools by name, or pass your model id to pick a tier. No arguments: list every tool by tier. |

## `core` (11 tools, 5 added)

Adds outlines, batch reads, references and spec reads. Suggested for small, fast models.

| Tool | Description |
|---|---|
| `file_outline` | Symbols of one file (signatures, line ranges, ids, imports) without bodies. |
| `find_references` | Usages of an identifier in code (include_docs=True to add specs/markdown), labelled with the enclosing symbol. |
| `get_symbols` | Several symbols in one call (ids or names). Same dedupe and focus rules as get_symbol. |
| `read_spec` | Read a spec. Without section: headings only. With section (heading text): just that section. |
| `token_savings` | How many tokens SpecAnvil saved versus reading whole files, per tool, plus this session's dedupe savings. |

## `standard` (36 tools, 25 added)

Adds change analysis, specs, the unit-test pipeline, documentation, tickets and sources.

| Tool | Description |
|---|---|
| `call_hierarchy` | Who calls a function (direction='callers') or what it calls ('callees'), transitively, with call lines. Calls are resolved by scope: same file, imported files, receiver (crud.create_user), unique name. |
| `changed_symbols` | Symbols touched by uncommitted changes (or since a git ref like main). Use for reviews and spec verification. |
| `check_safety` | Preflight before changing code. action: edit / delete / rename (with new_name). Verdict safe/caution/unsafe with reasons: callers, importing files, tests, overrides, framework registration, name collisions. |
| `class_hierarchy` | Ancestors (extends/implements, through the repo) and all descendants of a class/interface/trait. |
| `dependency_graph` | File-level imports (out) and importers (in) of a file, up to `depth` hops. |
| `finalize_handoff` | Write one validated Markdown handoff (kind: audit / investigation / review / design) to specs/handoffs: required sections, no placeholders, every `path::Symbol` / `path:line` checked against the index. |
| `find_implementations` | Concrete implementations of an interface / abstract class / trait (Go implicit interfaces included). Use 'Iface.method' to list the implementing methods. |
| `get_file_content` | Raw lines of an indexed file (secrets redacted; secret files refused). Prefer get_symbol / file_outline. |
| `get_profile` | The user's SpecAnvil profile: model per agent role and ticket backfill preferences. |
| `hotspots` | Riskiest functions: complexity (branches) × recent git churn. Use to prioritise reviews, tests, refactors. |
| `impact_of` | Files that depend on this file through imports, transitively up to `depth` hops, tests flagged. |
| `index_status` | Index freshness: last indexed, files/symbols, and how many files changed since (0 = fresh). |
| `list_sources` | Indexed dependency packs and external repos usable via source= on repo_map/search_symbols/get_symbol/ file_outline/get_context. |
| `list_specs` | All spec files with title, status, size and number of code references. |
| `openspec_check` | Structure problems in OpenSpec specs and active changes (or one capability / change), plus code references that no longer resolve. Needs no OpenSpec CLI. |
| `openspec_coverage` | Which OpenSpec scenarios have a test: explicit (title match), likely, or none. target = capability or change. |
| `openspec_gaps` | Code modules no OpenSpec capability spec mentions: candidates for a baseline spec. |
| `openspec_status` | OpenSpec project at a glance: capabilities, requirements, scenarios, active changes with task progress. |
| `plan_refactor` | Exact, reviewable edit plans (nothing is written). kind='rename' (new_name): every in-scope code occurrence as old/new line pairs plus conflicts; 'signature' (new_signature): every call site and override; 'move' (target_path): imports to update and dependencies to carry. |
| `plan_turn` | Before reading anything: the symbols most likely relevant to a query (with ids and size), terms that match nothing (negative evidence), and the cheapest next calls within a token budget. |
| `project_profile` | Detected stack, databases, ticket system, commands and conventions (from .specanvil/config.json). |
| `reindex` | Refresh the index (incremental unless full=True). |
| `search_ast` | Structural search with tree-sitter queries. Presets: empty-catch, broad-except, debug-print, eval-exec, sql-string-build, await-in-loop, mutable-default, hardcoded-secret, nested-loops, todo-comment. Or pass a raw tree-sitter S-expression query (capture @hit) with language=. |
| `search_code` | Text search across indexed files (secrets excluded). Returns path:line: snippet. |
| `spec_drift` | Compare specs with code: missing or changed referenced symbols, and modules no spec covers. |

## `analysis` (66 tools, 30 added)

Adds architecture, quality, history and runtime analysis.

| Tool | Description |
|---|---|
| `add_pack` | Fetch and index a library's published source without installing it: a catalog name ('react', 'fastapi', 'gin') or npm:<name>[@ver], pypi:<name>[@ver], go:<module>[@ver], gh:<owner>/<repo>[@tag]. Downloads public packages only (no repo code is sent); cached per version and usable via source=. |
| `architecture_metrics` | Module count, dependency depth, fan-in concentration, average fan-out, most depended-on files, module cycles. |
| `churn_rate` | Commits, authors and lines changed for a file or a single symbol over a time window. |
| `coupling` | Per-module afferent/efferent coupling and instability, with what each module depends on and is used by. |
| `dead_code` | Unreferenced functions, classes, variables and constants: no resolved callers AND no other occurrence of the name anywhere in code (verified by a word search). Decorated/registered and framework hooks excluded. |
| `dependency_cycles` | Import cycles (strongly connected components), level='file' or 'module'. |
| `endpoint_impact` | What breaks if an HTTP endpoint changes ('POST /users/signup'): handler, transitive callees, frontend callers of the path, tests and specs that reference it. |
| `entry_points` | Where execution starts: framework-registered routes/handlers/jobs/commands and program mains. |
| `extraction_candidates` | Refactoring opportunities: cross-file duplicates to extract, utilities used by 3+ modules living in one, and functions too complex to keep whole. |
| `file_risk` | Per-symbol risk for one file: complexity, callers, test evidence, churn and size, highest first. |
| `flow_edges` | Links a call graph can't see: route -> handler, event emit -> listeners (unhandled / never emitted), constructor dependency injection. |
| `group_contracts` | APIs shared across a group of repos (this one + registered sources/packs): provider symbol, signature, consuming repos and files. The breaking-change surface between them. |
| `layer_violations` | Dependencies that point the wrong way between layers (presentation > application > infrastructure > domain by default; configure 'layers' in .specanvil/config.json). |
| `list_packs` | Curated starter packs (popular frameworks/SDKs by ecosystem) and whether each is already indexed. |
| `pr_risk` | 0-1 risk for the current change vs a git ref: complexity, callers (blast radius), test exposure, churn, size per changed symbol, with concrete review tips. |
| `related_symbols` | Symbols related to one symbol: shared callers/callees, direct calls, same file, similar names. |
| `render_diagram` | Mermaid diagram. kind: call (symbol), class (name), deps (file), modules, sequence (entry point), tectonic. |
| `repo_health` | Six-axis health radar (complexity, modularity, testing, dead code, documentation, change risk; 0-100) with evidence, trend vs the last run, and what to focus on next. |
| `runtime_hot_paths` | Symbols ranked by runtime hits from imported traces (sort_by count / p95 / total) with p50/p95/max ms. |
| `runtime_unused` | Functions in traced files with no runtime hits (or none in `days`): reachable on paper, never executed. |
| `search_columns` | Database columns from ORM models (SQLAlchemy, SQLModel, Django, JPA, EF, Prisma, TypeORM) and SQL migrations. |
| `signal_chains` | End-to-end paths entry point -> ... -> leaf through the resolved call graph (all entry points, or one). |
| `similar_symbols` | Near-duplicate functions (MinHash + exact Jaccard over token shingles), repo-wide or for one symbol. |
| `symbol_complexity` | Cyclomatic complexity, control-block nesting, parameter count and lines for one symbol, or for every function in a file (target = path). |
| `symbol_diff` | Symbols added / removed / modified / renamed between two revisions (head omitted = working tree). |
| `symbol_importance` | Most central symbols by PageRank over the resolved call graph, with caller counts. Where changes hurt most. |
| `symbol_provenance` | Who wrote a symbol, when and WHY: its line-range history, ticket keys, decision-bearing commit messages, and docs/ADRs that mention it. |
| `tectonic_map` | Logical modules discovered from imports + calls (label propagation), their cohesion, and files whose logical home differs from their folder. |
| `untested_symbols` | Code with no test evidence (not reachable from tests via the call graph, never named in a test), most complex first. Optional target = folder/module. |
| `winnow_symbols` | One query over many signals, e.g. untested functions with complexity >= 8 and 3+ callers changed since main. sort_by: complexity / callers / lines / name. |

## `full` (96 tools, 30 added)

Everything else: session and index introspection, tuning, cross-repository tools and handoffs.

| Tool | Description |
|---|---|
| `add_source` | Index an installed dependency ('pkg:react', 'pkg:fastapi', 'pkg:github.com/gin-gonic/gin') or a local folder ('path:../other-service'). Cached per version and shared across repos. GitHub repos: use the CLI (network). |
| `audit_agent_config` | Wasted tokens in agent setup: always-loaded instruction size, skill/agent sizes, duplicated rules, stale paths, MCP servers with missing env vars, estimated tool-schema load, concrete fixes. |
| `backfill_progress` | Read or update resumable backfill state. Call with no arguments to read; pass keys as you finish them. |
| `cross_repo_map` | Package-level map across this repo and every registered source: what each publishes and who imports whom. |
| `decorator_census` | Repo-wide count of decorators / annotations / attributes with examples (routes, DI, jobs, validators...). |
| `delivery_metrics` | Delivery signals from git: commits/week, change size, fix and revert share, merges, busiest files. |
| `digest` | Stand-up briefing: commits, authors, tickets, new and changed symbols, hotspots touched, tests changed. |
| `doc_check` | Completeness check for one document: required sections, every must_mention item present, no placeholders outside 'Open questions', relative links resolve. Fix all problems, then call doc_register. |
| `doc_facts` | Everything needed to write one document: purpose, required sections, audience, locally extracted facts with file:line, and must_mention (items that must appear in the document). |
| `doc_plan` | Detected repo archetypes (backend, frontend, qa-automation, azure-functions, aws-lambda, infrastructure, library, cli, data-pipeline, mobile, worker, microservices) and every required document with its status. |
| `doc_register` | Record that a document passed doc_check, with a hash of the facts it describes (it turns 'stale' automatically when those facts change). Refuses if the check fails. |
| `file_tree` | Directory tree with file, line and symbol counts per folder (optionally under a prefix). |
| `import_runtime_trace` | Ingest an OpenTelemetry trace export (OTLP JSON / JSON lines, .gz ok) or flat span lines; spans are mapped to symbols by code.* attributes, HTTP route, or name. Stored locally; enables per-symbol latency. |
| `list_workspaces` | Monorepo workspaces and sub-projects (npm/pnpm/yarn workspaces, go.work, Cargo, .sln, per-folder manifests). |
| `next_test_milestone` | The next milestone to execute (in_progress first, then todo by priority) with the plan's test command. Returns only that milestone so the executor's context stays small. |
| `parity_map` | Migration parity between two trees (e.g. legacy/ vs src/, or this repo vs source='gh:org/old'): matched, missing and new symbols, parity %. |
| `redaction_log` | How many secrets were redacted from tool output this session, by kind (values are never stored). |
| `runtime_coverage` | From a coverage report (lcov/Cobertura/JaCoCo): hot paths by hit count, functions never executed, histogram. |
| `save_summaries` | Store one-line summaries {symbol_id: text}. Kept by code hash until the code changes; shown with a ≈ marker and used by search. |
| `session_snapshot` | What this session has already seen (symbols in context, recent queries) plus the snapshot from before the last compaction, so work can continue without re-reading. |
| `suggest_corrections` | Why searches miss, mined from this repo's local query/fetch ledger (zero results, re-asked queries, reads outside the top 5, vocabulary gaps), with synonym rules; apply=True saves them to config. |
| `suggest_queries` | Useful next questions for this repo with the exact tool calls (important symbols, hotspots, entry points). |
| `summaries_needed` | Undocumented functions/classes (most-called first) with their code, for writing one-line summaries. |
| `test_surface` | Unit-test analysis of a module/folder/file: per function branches, side effects (db, network, time...), existing test references, real coverage if a report exists, ranked by testing priority. |
| `ticket_keys` | Ticket keys found in git history (PROJ-123, AB#123, #123) with commit counts and date range. |
| `ticket_trace` | Commits mentioning a ticket key, files they changed, and current symbols they most likely touched (as path::Qualname refs for the spec's Code map). |
| `tool_stats` | Per-tool calls, average/p95 latency and average tokens returned (local telemetry, never sent anywhere). |
| `tune_weights` | Grid-search hybrid search fusion weights for MRR on an eval file (cases: query/task + expected) or on the ledger's query -> fetched pairs. Saved only with apply=True and a real gain. |
| `update_test_milestone` | Set a milestone's status (in_progress / done / blocked). 'done' is only accepted if the milestone's tests pass when this tool runs them; 'blocked' requires notes for the planner. |
| `validate_test_plan` | Check specs/tests/<module>/milestones.json against the schema and the index (targets must exist). |
