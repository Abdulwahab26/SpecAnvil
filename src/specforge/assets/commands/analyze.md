---
description: Architecture health review - cycles, coupling, layer violations, hotspots, dead code, most important symbols, risky patterns - with a prioritised fix list
argument-hint: [module | folder] [--write]
---

# Architecture health: $ARGUMENTS

Run these SpecForge tools (scope them to `$ARGUMENTS` where a tool accepts a path) and synthesise one report. Do not read whole files; use `get_symbol` only to confirm a finding.

1. `symbol_importance` — the 15 most central symbols (PageRank over the call graph). These are where bugs hurt most and where docs/tests matter most.
2. `hotspots` — complexity × churn. Cross-reference with importance: important AND hot is the top risk.
3. `dependency_cycles` (file and module level) — every cycle with the import that closes it.
4. `coupling` — modules with high instability that many others depend on (unstable foundations).
5. `layer_violations` — dependencies pointing the wrong way. If the default layer guess is wrong for this repo, propose a `layers` config.
6. `dead_code` — high-confidence candidates only; group by module.
7. `search_ast` presets: `broad-except`, `empty-catch`, `sql-string-build`, `hardcoded-secret`, `debug-print`, `await-in-loop`.

Report (write to `{{specs_dir}}/architecture-health.md` when `--write` is given):
- **Summary scorecard**: cycles, violations, dead symbols, risky patterns, top hotspot.
- **Top 10 actions**, ordered by impact ÷ effort. Each with evidence (`path:line`), the fix, and the tool to verify it.
- **Refactoring candidates**: for each, produce the plan with `plan_refactor` (rename / move / signature) so it can be applied safely.
- **What not to touch**: important, stable, well-tested symbols.
