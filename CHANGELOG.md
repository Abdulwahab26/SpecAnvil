# Changelog

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
