# Changelog

## 0.5.0
- **Every jCodeMunch tool mapped**: 40 new MCP tools (83 total): `symbol_complexity`, `file_risk`, `untested_symbols`, `similar_symbols`, `related_symbols`, `extraction_candidates`, `winnow_symbols`, `entry_points`, `signal_chains`, `flow_edges`, `endpoint_impact`, `tectonic_map`, `architecture_metrics`, `repo_health`, `decorator_census`, `dependency_graph`, `find_implementations` (incl. Go implicit interfaces), `check_safety`, `parity_map`, `search_columns`, `symbol_provenance`, `symbol_diff`, `churn_rate`, `digest`, `delivery_metrics`, `render_diagram`, `audit_agent_config`, `runtime_coverage`, `plan_turn`, `suggest_queries`, `session_snapshot`, `tool_stats`, `redaction_log`, `index_status`, `file_tree`, `get_file_content`, `list_workspaces`, `cross_repo_map`, `list_packs`, `add_pack`. Mapping table in `docs/COMPARISON.md`.
- **Symbol coverage**: config keys (YAML / JSON / TOML / INI / `.env` names only) and documentation sections (Markdown / RST / AsciiDoc). express 712 → 994, fastapi 8,137 → 26,842, gin 1,680 → 1,960.
- **Starter packs without installing**: 51-pack catalog, `npm:` / `pypi:` / `go:` / `gh:` at any version, `.sfpack` export / import, defensive extraction. CLI: `specforge pack`.
- **CLI**: `specforge insight …` and `specforge diagram … -o x.html`.
- **Fixes**: JS `@/` aliases in monorepos resolved inside the importing project's own `src/` (was matching files in other languages); folder targets for `symbol_complexity` / `dependency_graph`; symbol lookups prefer code over keys / sections; embeddings never mixed across models (`index_status` reports drift); first `reindex(full=True)` no longer indexes twice; route handlers requested by tests over HTTP count as tested; full rebuilds clear tables in one pass; Windows quoting for test commands.
- **Track record**: CI (3 OSes × 3 Pythons, retrieval-eval quality gate, clean wheel smoke test), tag-driven releases, `SECURITY.md`, `CONTRIBUTING.md`; `tests/mcp_v05_tools.py` (also run in CI); 38 unit tests.
- **Benchmarks**: −66% tokens vs jCodeMunch on its benchmark (5,924 vs 17,581); cold index 0.27 / 3.44 / 0.39 s vs 1.72 / 7.07 / 1.56 s.

## 0.4.0
- **Symbol coverage**: variables, constants, type aliases, fields (Python, JS/TS, JVM/C#, Go, PHP), Go struct/interface types and receiver-qualified methods, Rust/C/Ruby declarations (express 269 → 712, fastapi 5,576 → 8,137, gin 1,037 → 1,680).
- **Code graph**: call sites with receivers, inheritance, package/namespace, resolved import graph (JS/TS incl. CommonJS, Python, Java/Kotlin/C#/PHP, Go, Rust, Ruby).
- **Analysis tools**: `call_hierarchy`, `class_hierarchy`, `symbol_importance` (PageRank), `dead_code` (verified), `dependency_cycles`, `coupling`, `layer_violations`, `pr_risk`, `search_ast` (tree-sitter queries + 10 presets), `plan_refactor` (rename / signature / move); `/sf:analyze`.
- **Search**: hybrid (name + body vocabulary + optional embeddings, RRF tuned on evals), `mode=` on `search_symbols`, optional local embeddings (`specforge embed`, fastembed / localhost endpoint, offline at query time), embeddings feed `get_context` (paraphrase tasks 3/12 → 8/12).
- **AI summaries** written by the user's Claude via MCP (`summarizer` agent, `/sf:summarize`), cached by code hash.
- **Sources**: dependency packs from installed versions, GitHub repos, local folders; `source=` on core tools; `specforge source`.
- **Watch mode**: `specforge watch [--embed]`.
- **Performance**: extractor regexes fixed for catastrophic backtracking (7.5 s → 0.8 s on Django); identifier table replaced by verification-time search.
- **Benchmarks**: −65% tokens vs jCodeMunch on its benchmark (was −47%).

## 0.3.0
- **Repository documentation**: archetype detection (backend, frontend, QA automation, Azure Functions, AWS Lambda, infrastructure, library, CLI, data pipeline, mobile, worker, microservices); 44-document catalog with required sections; local fact extraction (endpoints across 9 frameworks with prefixes and handlers, env vars, Azure Functions v1/v2/isolated/Node v4 triggers, host.json, Lambda, routes, components, test inventory with tags, page objects, models, migrations, IaC resources, CI, CLI commands, queues, pipelines, auth, integrations); `doc-writer` agent on the user's docs model; `/sf:docs`; `doc_plan`/`doc_facts`/`doc_check`/`doc_register`; `specforge docs` CLI; completeness gate and automatic staleness.
- **Cost**: server-enforced session dedupe (reset on PreCompact), `get_symbols` batch, large-symbol skeletons, `focus=` excerpts, nested-result dedupe, tool tiers, `hotspots`, transitive `impact_of`.
- **Retrieval**: CommonJS/prototype method extraction; search ranking by term coverage with test/example/generated down-ranking. Eval MRR 0.78 → 0.88.
- **Security**: `local.settings.json`, `secrets.json`, publish settings never indexed.
- **Benchmarks**: head-to-head on jCodeMunch's own benchmark (−47% tokens default, −31% full bodies; 5–7× faster cold index), `docs/COMPARISON.md`.

## 0.2.0
- **Indexing**: strictly local (grammars only fetched by explicit `specforge grammars` / `init`, `--offline` mode, socket-blocking test); (size, mtime_ns) fingerprints, BLAKE2b, batched writes, WAL, parallel parsing, ripgrep fast path; fixed an O(n²) removal check. Django refresh 1.62 s → 0.09 s.
- **Unit-test pipeline**: `test_surface` (branches, side effects, test refs, lcov/Cobertura/JaCoCo coverage), `unit-test-planner` + `unit-test-executor` agents, `/sf:test-plan`, `/sf:test-run`, schema-validated `milestones.json`, test-verified "done".
- **Ticket backfill**: `/sf:backfill`, `ticket-archaeologist` agent, `ticket_keys` / `ticket_trace` / `backfill_progress` tools; opt-in, read-only, resumable.
- **Profile**: `~/.specforge/profile.json` with validated model per agent role and backfill settings; `specforge profile`, `/sf:profile`; rendered into agent `model:` frontmatter.
- **Guardrails**: PreToolUse secret guard hook; MCP errors returned as `isError` with next-step messages.
- **Evals & docs**: retrieval eval set (recall 1.00, MRR 0.78), `docs/ARCHITECTURE.md` with ADRs, `docs/BENCHMARKS.md`.

## 0.1.0
- Initial release: bootstrap, code-index MCP, lifecycle commands, agents, skills, connectors, drift, report.
