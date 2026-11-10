# Changelog

## 0.2.0
- **Indexing**: strictly local (grammars only fetched by explicit `specforge grammars` / `init`, `--offline` mode, socket-blocking test); (size, mtime_ns) fingerprints, BLAKE2b, batched writes, WAL, parallel parsing, ripgrep fast path; fixed an O(n²) removal check. Django refresh 1.62 s → 0.09 s.
- **Unit-test pipeline**: `test_surface` (branches, side effects, test refs, lcov/Cobertura/JaCoCo coverage), `unit-test-planner` + `unit-test-executor` agents, `/sf:test-plan`, `/sf:test-run`, schema-validated `milestones.json`, test-verified "done".
- **Ticket backfill**: `/sf:backfill`, `ticket-archaeologist` agent, `ticket_keys` / `ticket_trace` / `backfill_progress` tools; opt-in, read-only, resumable.
- **Profile**: `~/.specforge/profile.json` with validated model per agent role and backfill settings; `specforge profile`, `/sf:profile`; rendered into agent `model:` frontmatter.
- **Guardrails**: PreToolUse secret guard hook; MCP errors returned as `isError` with next-step messages.
- **Evals & docs**: retrieval eval set (recall 1.00, MRR 0.78), `docs/ARCHITECTURE.md` with ADRs, `docs/BENCHMARKS.md`.

## 0.1.0
- Initial release: bootstrap, code-index MCP, lifecycle commands, agents, skills, connectors, drift, report.
