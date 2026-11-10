# SpecForge

**Turn any existing repo into spec-driven development with one command.** SpecForge indexes your code locally, learns your stack and conventions, and sets up everything Claude Code needs to work like a disciplined team: a token-saving code-index MCP, project-specific skills, a 13-agent team, lifecycle commands from ticket grooming to Playwright E2E, a planner/executor unit-test pipeline on the models you choose, ticket-history backfill, complete repository documentation for any repo type (backend, frontend, QA automation, Azure Functions, Lambda, infra, library, CLI, data, mobile), and connectors for your ticket system and databases.

Design notes and decision records: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · measured numbers: [docs/BENCHMARKS.md](docs/BENCHMARKS.md) · head-to-head: [docs/COMPARISON.md](docs/COMPARISON.md)

```bash
cd your-repo
specforge init        # ~1 second on a 250-file repo; no AI calls, nothing leaves your machine
claude                # then: /sf:learn, /sf:flow PROJ-123
```

| Measured | Result |
|---|---|
| jCodeMunch's own benchmark (same repos, queries, workflow, tokenizer) | **65% fewer tokens** than jCodeMunch |
| Cold index incl. call/import graphs vs jCodeMunch (express / fastapi / gin) | **3–4× faster** |
| Paraphrased tasks in `get_context` with local embeddings | **8/12** found (3/12 keyword-only) |
| Token savings, 13 typical lookups on [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template) | **84% fewer tokens** than reading the files |
| Retrieval eval (12 tasks, 4k budget) | recall **1.00**, MRR **0.86** |
| Refresh after an edit on Django (5.6k files) | **0.14 s** (no-op: 0.09 s) |
| Network calls during indexing | **none** (tested with sockets blocked) |

---

## What `specforge init` creates

```
your-repo/
├── CLAUDE.md                    # SpecForge block added (your content is kept)
├── .mcp.json                    # specforge + matching connectors (no secrets; ${ENV} refs)
├── .claude/
│   ├── commands/sf/             # 21 commands (below)
│   ├── agents/                  # spec-analyst, architect, frontend/backend engineer, db-optimizer,
│   │                            # qa-engineer, e2e-tester, reviewer, unit-test-planner,
│   │                            # unit-test-executor, ticket-archaeologist, doc-writer, summarizer
│   ├── skills/                  # code-navigation, spec-writing, test-case-design,
│   │                            # playwright-e2e, db-optimization (only your engines),
│   │                            # <repo>-conventions, frontend-patterns, backend-patterns
│   └── settings.json            # hooks: secret guard (PreToolUse), re-index (PostToolUse), dedupe reset (PreCompact)
├── specs/
│   ├── project.md               # stack, commands, sub-projects, modules
│   ├── conventions.md           # naming, layout, tests, lint + reference implementations
│   ├── modules/<module>.md      # reverse-engineered public API with code refs
│   └── features/_template/      # requirements, design, tasks, test-cases
└── .specforge/                  # local index (SQLite), config, spec lock, report.html
```

Everything is **detected, not configured**: languages (300+ via tree-sitter, regex fallback for anything else), frameworks, ORMs, databases (from dependencies, docker-compose, Prisma, connection strings, `.env.example`, Spring/ASP.NET config), test tools, CI, ticket system (from git remotes, CI files and `PROJ-123` / `AB#123` keys in commit history), and per-sub-project commands in monorepos. Override anything in `.specforge/config.json` or with flags.

## The workflow

| Command | Agent | What it does |
|---|---|---|
| `/sf:groom <ticket>` | spec-analyst | Pulls the ticket (Jira / Azure DevOps / GitHub / Linear / GitLab MCP), INVEST check, Gherkin ACs, estimate, split, risks, open questions with defaults |
| `/sf:design <id>` | architect | Options + decision (mini ADR), Mermaid diagram, API contract, data model + migration, component tree, `data-testid` plan, security, test strategy. Uses Figma MCP if linked |
| `/sf:plan <id>` | architect | ≤ half-day tasks, each with a pass/fail check, mapped to ACs, parallel tasks marked |
| `/sf:be <id>` / `/sf:fe <id>` | backend / frontend engineer | Implements by mirroring the closest existing code in the repo, tests included |
| `/sf:db <id\|query\|audit>` | db-optimizer | N+1 hunt, **real** read-only plans via the DB MCP (Postgres, MySQL, SQL Server, MongoDB, SQLite), index migrations with rollback |
| `/sf:qa <id>` | qa-engineer | Manual test cases using EP, BVA, decision tables, state transitions; traceability matrix; CSV for TestRail / Xray / Azure Test Plans |
| `/sf:e2e <id>` | e2e-tester | Explores the running app with **Playwright MCP**, then writes role-based Playwright tests per AC and checks them for flakiness |
| `/sf:verify <id>` | reviewer | AC → code → test table, spec drift, diff review, quality gates; PASS or numbered fixes |
| `/sf:ship <id>` | — | PR description, changelog, release note, ticket update (asks first) |
| `/sf:flow <ticket>` | orchestrator | **Runs the whole team**: groom → design → plan → parallel FE/BE + QA → E2E → verify, with human gates (or `--auto`) |
| `/sf:test-plan <module>` | unit-test-planner (**your planner model**) | Deep test analysis from `test_surface` (branches, side effects, existing tests, real coverage), then executable milestones with technical challenges and approaches, schema-validated |
| `/sf:test-run <module>` | unit-test-executor (**your executor model**) | Implements milestones one by one; "done" is accepted only when the tool re-runs the tests and they pass; ambiguity is escalated as `blocked`, never improvised |
| `/sf:backfill` | ticket-archaeologist | Opt-in: pulls Done / In Progress / In Review tickets through your ticket MCP and writes linked specs, resumable, in parallel batches |
| `/sf:docs [missing\|stale\|all\|ids]` | doc-writer (**your docs model**) | Detects the repo type(s) and writes every required document (up to 25 for an Azure Functions repo) from locally extracted facts: endpoints, functions + triggers, settings, routes, test inventory, models, infra. `specforge docs check` enforces sections, full coverage (every endpoint/function/setting mentioned), no placeholders, valid links; docs go **stale** automatically when the code they describe changes |
| `/sf:analyze` | — | Architecture health: PageRank importance, hotspots, cycles, coupling, layer violations, dead code, risky AST patterns, with refactor plans |
| `/sf:summarize` | summarizer (**your summarizer model**) | One-line summaries for undocumented code, written into the index (cached by code hash) to sharpen search and outlines |
| `/sf:profile` | — | Show or change your models per role and backfill settings |
| `/sf:spec`, `/sf:status`, `/sf:learn` | — | Quick spec for small changes; board of all features; AI enrichment of the generated specs and skills |

## The code-index MCP (token reduction)

Agents burn most of their tokens reading whole files to find one function. `specforge-mcp` gives them surgical tools instead:

| Tool | Returns |
|---|---|
| `repo_map` | modules and key symbols with `file:line` |
| `search_symbols(mode=)` | lexical, hybrid (body vocabulary + embeddings, RRF) or semantic; `auto` falls back to hybrid on weak matches |
| `get_symbol(s)` | exact source; **already-sent bodies return a one-line reference** (session dedupe, reset on compaction); large symbols return a skeleton; `focus=` excerpts |
| `get_context(task, budget)` | ranked, budgeted code for a task: full bodies for the best matches, signatures for the rest (stemming, synonyms like *endpoint → route/controller*, generated code and tests down-ranked) |
| `search_symbols`, `file_outline`, `get_symbol` | symbol search, signatures only, one exact symbol |
| `find_references`, `impact_of`, `changed_symbols`, `hotspots` | usages with enclosing symbol, transitive dependents, symbols touched by a diff, complexity × churn |
| `call_hierarchy`, `class_hierarchy`, `symbol_importance`, `dead_code` | resolved call graph both ways, inheritance up/down, PageRank, verified unused code |
| `dependency_cycles`, `coupling`, `layer_violations`, `pr_risk` | import cycles, Ca/Ce/instability, wrong-way dependencies, 0–1 change risk with review tips |
| `search_ast`, `plan_refactor` | tree-sitter structural queries + 10 anti-pattern presets; reviewable rename/signature/move edit plans |
| `summaries_needed`, `save_summaries`, `list_sources`, `add_source` | AI summaries via your Claude; dependency packs and external repos (`source=` on the core tools) |
| `read_spec(path, section)`, `list_specs`, `spec_drift` | section-level spec reads and drift checks |
| `test_surface`, `validate_test_plan`, `next_test_milestone`, `update_test_milestone` | unit-test analysis and the enforced milestone workflow |
| `doc_plan`, `doc_facts`, `doc_check`, `doc_register` | documentation plan by archetype, grounded facts, completeness gate, freshness lock |
| `ticket_keys`, `ticket_trace`, `backfill_progress` | ticket keys in git history, ticket → commits → symbols, resumable backfill state |
| `project_profile`, `get_profile`, `reindex`, `token_savings` | detected stack, your model/backfill profile, refresh, the savings ledger |

Every call is logged against a *whole-file baseline* so the savings are measured, not claimed. `specforge stats` and `.specforge/report.html` show them.

Tool tiers keep the per-turn schema small: `core` (9 tools, ~1.4k tokens), `standard` (23, ~2.8k), `analysis` (29, ~3.4k), `full` (43, ~5.0k). Set with `"tool_tier"` in `.specforge/config.json` or `SPECFORGE_TOOLS`.

## Your profile: models per role

Model choices are personal, so they live in `~/.specforge/profile.json` (never committed) and are applied to the agents' `model:` frontmatter:

```bash
specforge profile                                   # show
specforge profile set models.test_planner opus      # deep analysis
specforge profile set models.test_executor haiku    # cheap execution
specforge profile set models.reviewer sonnet        # any role: spec_analyst, architect, frontend, backend, db, qa, e2e, reviewer, backfill
specforge profile set backfill.enabled true
specforge profile set backfill.statuses "Done,In Review,In Progress"
```
Values are validated (`opus`, `sonnet`, `haiku`, `fable`, `inherit`, or a full model ID). Precedence: built-in defaults < team defaults in `.specforge/config.json` (`profile_defaults`) < your profile.

## Specs that can't silently rot

Specs reference code as `` `path::Qualname` ``. `specforge drift` checks each reference against the index: **missing** (renamed or deleted), **changed** (code changed since the spec was verified), or **unverified**. `specforge drift --lock` records the reviewed state; `specforge init --ci` adds a GitHub Action that fails PRs which break specs.

## Privacy and security

- **Local only.** Indexing, detection, drift, test analysis and reports run on your machine with no telemetry. Indexing never touches the network (a test blocks sockets to prove it). The one network step is fetching tree-sitter grammars, done explicitly by `specforge grammars` or by `init` (skip with `--offline`; uncached languages then use the built-in regex extractor).
- **Enforced, not just instructed.** A `PreToolUse` hook blocks Claude from reading or writing secret files; unit-test "done" is verified by running the tests; plans are schema-checked. See the enforcement table in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Your code only goes where it already goes**: to the model your Claude Code account uses, and only the snippets the agent asks for.
- **Secrets are never indexed** (`.env*`, keys, keystores, credential files, tfstate…) and secret-looking values (private keys, cloud and API tokens, passwords, credentials in URLs) are **redacted** from tool output.
- **Connector configs hold no secrets**: `.mcp.json` uses `${ENV_VAR}` references. Database connectors default to read-only or restricted modes.
- **Generated specs are labelled as reverse-engineered**: they describe what the code does, which may include bugs. Review before you rely on them.

## Install

Requires Python 3.10+. Node 18+ (`npx`) and `uv` (`uvx`) are needed only for the connectors that use them.

```bash
uv tool install git+https://github.com/YOUR_GITHUB_USER/specforge   # or: pipx install ...
specforge doctor
```

`specforge-mcp` must be on your `PATH` so Claude Code can start it from `.mcp.json`.

## CLI

```
specforge init [path] [--dry-run] [--offline] [--tickets jira] [--db postgres,mongodb] [--app-url URL] [--ci] [--force]
specforge profile [set|unset|sync] ...   # your models per role and backfill settings
specforge tests surface|validate|status <module>
specforge grammars [--list]              # explicit, one-time grammar download
specforge docs plan|status|check [--strict]|facts <id>
specforge analyze importance|dead|cycles|coupling|layers|risk|hotspots|ast [--preset X]
specforge embed [--code-model]          # optional local embeddings (pip install 'specforge[semantic]')
specforge source add|list|remove pkg:react | gh:owner/repo[@ref] | path:../svc
specforge watch [--embed]
specforge connect                 # list the connector catalog
specforge connect jira figma      # add connectors to .mcp.json  (--remove to drop)
specforge index [--full] | detect | stats | drift [--lock] [--strict] | report | doctor | serve
```

Connectors: `playwright`, `github`, `jira`, `atlassian-remote`, `azure-devops`, `linear`, `gitlab`, `postgres`, `mysql`, `sqlserver`, `sqlite`, `mongodb`, `figma`, `sentry`, `context7`. The catalog lives in `src/specforge/assets/connectors/catalog.json`; check package names and URLs against each vendor's current docs before rolling out to a team.

## Architecture

```mermaid
flowchart LR
  R[Your repo] --> S[scanner<br/>gitignore + secret filter]
  S --> P[parser<br/>tree-sitter 300+ langs / regex fallback]
  P --> I[(SQLite index<br/>symbols, FTS5, imports, ledger)]
  R --> D[detect<br/>stack, DBs, tickets, commands, conventions]
  I --> B[bootstrap]
  D --> B
  B --> O[specs/ · .claude/ · CLAUDE.md · .mcp.json]
  I --> M[specforge-mcp]
  M <--> C[Claude Code + agent team]
  C <--> X[Ticket / DB / Playwright / Figma MCPs]
  H[PostToolUse hook] --> I
```

## Development

```bash
pip install -e . && pytest -q
python tests/mcp_smoke.py /path/to/repo     # exercises the core MCP tools over stdio
python tests/mcp_new_tools.py /path/to/repo # test, backfill and profile tools
python tests/evals/run_context_eval.py <repo> tests/evals/context_fullstack_fastapi.json
```

License: MIT.
