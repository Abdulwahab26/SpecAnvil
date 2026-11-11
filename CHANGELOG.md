# Changelog

## 0.9.0
- **Renamed SpecForge → SpecAnvil** (package, commands, MCP server, docs). The PyPI name is `specanvil`.
- **Upgrades in place**: on the next `init` (or `uninstall`), a repo set up as SpecForge has `.specforge/` moved to `.specanvil/`, `~/.specforge` moved to `~/.specanvil`, and the old server entries, hook commands, permission and rules markers rewritten in every shared file, with no duplicates and user content kept. `SPECFORGE_*` environment variables are still honoured; `specforge` and `specforge-mcp` remain as command aliases.
- `uninstall` always removes SpecAnvil's own MCP server, including in repos with no install record.
- Entries below were written under the old name.

## 0.8.0
- **Safety**: unit-test commands from plans are allowlisted (known test runners, the repo's detected command, or `allowed_test_commands`), refuse shell operators and never run through a shell.
- **OpenSpec mode**: detects `openspec/` and works inside it; `specanvil openspec status | check | coverage | gaps`, four MCP tools, `/sf:baseline`, lifecycle workflows mapped to OpenSpec's files and format; OpenSpec's own files are left untouched.
- **Secret guard in more tools**: hooks for Cursor (`.cursor/hooks.json`) and GitHub Copilot (`.github/hooks/specanvil.json`) in their own protocols.
- **`specanvil uninstall [tools] [--dry-run] [--purge]`**: removes exactly what `init` wrote (manifest), keeps your own rules, servers, hooks and specs.
- **Retrieval**: doc comments indexed for every language (Go, Java and JavaScript had none); stemmer connects verb/noun families; config keys and doc sections rank below code for behaviour questions; larger candidate pool. Multi-repo eval: 0.89 → 0.96 found, MRR 0.61 → 0.72.
- **Call graph**: class instantiation linked to constructors; calls inside interpolated strings; callbacks passed as values; functions reached through re-exporting modules; `self` / `this` calls prefer the caller's class. Recall against 751 call pairs traced at runtime: 0.91.
- **Scale and footprint**: batched build (peak memory 906 → 393 MB on Kubernetes), no FTS prefix indexes and compaction after rebuilds (index about half the size), logs pruned after 90 days with totals preserved, opt-out flags for doc sections and config keys.
- **Evidence in CI**: 74-question multi-repo gate, call-graph accuracy gate, concurrency stress test, lint, coverage floor (78% measured, 72% floor), type-error ratchet, weekly scheduled run; dependency upper bounds.
- **Maintainability**: `server.py`, `insights.py` and the test file split into focused modules; `SPECANVIL_DEBUG=1` logs errors that are deliberately not raised; five unused functions found by SpecAnvil's own dead-code tool removed.
- **Packaging**: distribution renamed `specforge-ai` (the PyPI name `specforge` belongs to another project); PyPI publishing step ready behind a repository variable. (Superseded by the 0.9.0 rename to `specanvil`.)
- Benchmark vs jCodeMunch is now 6,799 tokens (−61%; was −66%) because results include doc comments. 96 tools; 63 unit tests.

## 0.7.0
- **Any AI coding tool**: `specanvil init` detects Claude Code, GitHub Copilot, Cursor, Augment Code, Windsurf / Devin Local, OpenAI Codex, Kiro, Gemini CLI and Replit from their config folders (or `--agents ...`) and writes rules, workflows, role agents, skills and MCP config in each tool's native format; `specanvil agents list | add | all`.
- **Specs per tool**: Kiro feature specs in `.kiro/specs/<feature>/` plus seeded steering (product, tech, structure); `features_dir` / `templates_dir` config; drift, `list_specs` and `read_spec` cover both folders.
- **No-MCP tools**: `specanvil tool <name> '<json>'` CLI bridge to every tool (used by Replit's `replit.md`).
- **MCP root discovery** for any client (env, client project variable, nearest `.specanvil`/`.git`; unexpanded variables ignored).
- **Per-tool models**: `providers.<tool>.models.<role>` for Copilot, Augment and Codex.
- `docs/PROVIDERS.md` (support matrix, limits, sources); 53 unit tests.

## 0.6.0
- **Runtime traces**: `import_runtime_trace` (OTLP JSON / JSONL / gzip, flat spans; mapped by code attributes, HTTP route with app prefixes, or name), `runtime_hot_paths` (hits, p50 / p95 / max ms), `runtime_unused`; latency feeds `file_risk`. CLI: `specanvil insight trace-import | hot | unused`.
- **Ranking regret**: local query → fetch ledger; `suggest_corrections` (zero results, reformulations, reads outside top 5, vocabulary gaps → synonym rules, `apply=True`); config synonyms used by search and `get_context`; `tune_weights` grid-searches fusion weights on an eval file or the ledger.
- **Tool surface**: default `lite` tier (6 tools, ~790 tokens); `set_tool_tier` switches at runtime with `notifications/tools/list_changed` and picks a tier from a model id; `run_tool` dispatcher; compact schemas (full surface 10.2k → 7.9k tokens).
- **Cross-repo contracts**: `group_contracts` with re-export resolution. **Handoffs**: `finalize_handoff` validated against the index.
- 92 tools; every jCodeMunch tool has an implemented equivalent (`docs/COMPARISON.md`). `tests/mcp_v06_tools.py` (in CI); 46 unit tests.

## 0.5.0
- **Every jCodeMunch tool mapped**: 40 new MCP tools (83 total): `symbol_complexity`, `file_risk`, `untested_symbols`, `similar_symbols`, `related_symbols`, `extraction_candidates`, `winnow_symbols`, `entry_points`, `signal_chains`, `flow_edges`, `endpoint_impact`, `tectonic_map`, `architecture_metrics`, `repo_health`, `decorator_census`, `dependency_graph`, `find_implementations` (incl. Go implicit interfaces), `check_safety`, `parity_map`, `search_columns`, `symbol_provenance`, `symbol_diff`, `churn_rate`, `digest`, `delivery_metrics`, `render_diagram`, `audit_agent_config`, `runtime_coverage`, `plan_turn`, `suggest_queries`, `session_snapshot`, `tool_stats`, `redaction_log`, `index_status`, `file_tree`, `get_file_content`, `list_workspaces`, `cross_repo_map`, `list_packs`, `add_pack`. Mapping table in `docs/COMPARISON.md`.
- **Symbol coverage**: config keys (YAML / JSON / TOML / INI / `.env` names only) and documentation sections (Markdown / RST / AsciiDoc). express 712 → 994, fastapi 8,137 → 26,842, gin 1,680 → 1,960.
- **Starter packs without installing**: 51-pack catalog, `npm:` / `pypi:` / `go:` / `gh:` at any version, `.sfpack` export / import, defensive extraction. CLI: `specanvil pack`.
- **CLI**: `specanvil insight …` and `specanvil diagram … -o x.html`.
- **Fixes**: grammars downloaded after the first index (e.g. by `specanvil init`) now really re-parse those files (a content-hash shortcut skipped them, leaving regex-quality symbols); CRLF files parse like LF (Windows checkouts); JS `@/` aliases in monorepos resolved inside the importing project's own `src/` (was matching files in other languages); folder targets for `symbol_complexity` / `dependency_graph`; symbol lookups prefer code over keys / sections; embeddings never mixed across models (`index_status` reports drift); first `reindex(full=True)` no longer indexes twice; route handlers requested by tests over HTTP count as tested; full rebuilds clear tables in one pass; Windows quoting for test commands.
- **Track record**: CI (3 OSes × 3 Pythons, retrieval-eval quality gate, clean wheel smoke test), tag-driven releases, `SECURITY.md`, `CONTRIBUTING.md`; `tests/mcp_v05_tools.py` (also run in CI); 40 unit tests.
- **Benchmarks**: −66% tokens vs jCodeMunch on its benchmark (5,924 vs 17,581); cold index 0.27 / 3.44 / 0.39 s vs 1.72 / 7.07 / 1.56 s.

## 0.4.0
- **Symbol coverage**: variables, constants, type aliases, fields (Python, JS/TS, JVM/C#, Go, PHP), Go struct/interface types and receiver-qualified methods, Rust/C/Ruby declarations (express 269 → 712, fastapi 5,576 → 8,137, gin 1,037 → 1,680).
- **Code graph**: call sites with receivers, inheritance, package/namespace, resolved import graph (JS/TS incl. CommonJS, Python, Java/Kotlin/C#/PHP, Go, Rust, Ruby).
- **Analysis tools**: `call_hierarchy`, `class_hierarchy`, `symbol_importance` (PageRank), `dead_code` (verified), `dependency_cycles`, `coupling`, `layer_violations`, `pr_risk`, `search_ast` (tree-sitter queries + 10 presets), `plan_refactor` (rename / signature / move); `/sf:analyze`.
- **Search**: hybrid (name + body vocabulary + optional embeddings, RRF tuned on evals), `mode=` on `search_symbols`, optional local embeddings (`specanvil embed`, fastembed / localhost endpoint, offline at query time), embeddings feed `get_context` (paraphrase tasks 3/12 → 8/12).
- **AI summaries** written by the user's Claude via MCP (`summarizer` agent, `/sf:summarize`), cached by code hash.
- **Sources**: dependency packs from installed versions, GitHub repos, local folders; `source=` on core tools; `specanvil source`.
- **Watch mode**: `specanvil watch [--embed]`.
- **Performance**: extractor regexes fixed for catastrophic backtracking (7.5 s → 0.8 s on Django); identifier table replaced by verification-time search.
- **Benchmarks**: −65% tokens vs jCodeMunch on its benchmark (was −47%).

## 0.3.0
- **Repository documentation**: archetype detection (backend, frontend, QA automation, Azure Functions, AWS Lambda, infrastructure, library, CLI, data pipeline, mobile, worker, microservices); 44-document catalog with required sections; local fact extraction (endpoints across 9 frameworks with prefixes and handlers, env vars, Azure Functions v1/v2/isolated/Node v4 triggers, host.json, Lambda, routes, components, test inventory with tags, page objects, models, migrations, IaC resources, CI, CLI commands, queues, pipelines, auth, integrations); `doc-writer` agent on the user's docs model; `/sf:docs`; `doc_plan`/`doc_facts`/`doc_check`/`doc_register`; `specanvil docs` CLI; completeness gate and automatic staleness.
- **Cost**: server-enforced session dedupe (reset on PreCompact), `get_symbols` batch, large-symbol skeletons, `focus=` excerpts, nested-result dedupe, tool tiers, `hotspots`, transitive `impact_of`.
- **Retrieval**: CommonJS/prototype method extraction; search ranking by term coverage with test/example/generated down-ranking. Eval MRR 0.78 → 0.88.
- **Security**: `local.settings.json`, `secrets.json`, publish settings never indexed.
- **Benchmarks**: head-to-head on jCodeMunch's own benchmark (−47% tokens default, −31% full bodies; 5–7× faster cold index), `docs/COMPARISON.md`.

## 0.2.0
- **Indexing**: strictly local (grammars only fetched by explicit `specanvil grammars` / `init`, `--offline` mode, socket-blocking test); (size, mtime_ns) fingerprints, BLAKE2b, batched writes, WAL, parallel parsing, ripgrep fast path; fixed an O(n²) removal check. Django refresh 1.62 s → 0.09 s.
- **Unit-test pipeline**: `test_surface` (branches, side effects, test refs, lcov/Cobertura/JaCoCo coverage), `unit-test-planner` + `unit-test-executor` agents, `/sf:test-plan`, `/sf:test-run`, schema-validated `milestones.json`, test-verified "done".
- **Ticket backfill**: `/sf:backfill`, `ticket-archaeologist` agent, `ticket_keys` / `ticket_trace` / `backfill_progress` tools; opt-in, read-only, resumable.
- **Profile**: `~/.specanvil/profile.json` with validated model per agent role and backfill settings; `specanvil profile`, `/sf:profile`; rendered into agent `model:` frontmatter.
- **Guardrails**: PreToolUse secret guard hook; MCP errors returned as `isError` with next-step messages.
- **Evals & docs**: retrieval eval set (recall 1.00, MRR 0.78), `docs/ARCHITECTURE.md` with ADRs, `docs/BENCHMARKS.md`.

## 0.1.0
- Initial release: bootstrap, code-index MCP, lifecycle commands, agents, skills, connectors, drift, report.
