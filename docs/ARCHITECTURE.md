# SpecForge architecture

SpecForge is a deterministic local toolchain (Python) plus a set of Claude Code assets (agents, commands, skills, hooks, MCP config) generated per repository. The core design rule: **anything that can be decided by code is enforced by code; the model is used only for judgement.**

```mermaid
flowchart TB
  subgraph Local["Local machine (no network)"]
    SC[scanner<br/>git ls-files · 1 stat/file · secret filter] --> PA[parser<br/>tree-sitter (cached grammars) · regex fallback]
    PA --> IX[(SQLite WAL<br/>symbols · FTS5 prefix · imports · ledger)]
    DT[detect<br/>stack · DBs · tickets · conventions] --> BS[bootstrap]
    IX --> BS
    PR[(~/.specforge/profile.json<br/>models per role · backfill prefs)] --> BS
    IX --> MCP[specforge-mcp<br/>23 tools]
    TS[testing<br/>surface · coverage · plan schema] --> MCP
    TK[tickets<br/>git archaeology · resumable state] --> MCP
  end
  BS --> CC[".claude/ agents · commands · skills · hooks<br/>specs/ · CLAUDE.md · .mcp.json"]
  subgraph Claude["Claude Code session"]
    OR[orchestrator command] --> AG[specialist subagents<br/>model per role from profile]
    AG <--> MCP
    AG <--> EXT[ticket / DB / Playwright / Figma MCPs]
    HK[PreToolUse guard · PostToolUse re-index] -. enforces .-> AG
  end
  CC --> Claude
```

## How it maps to the Claude Certified Architect – Foundations domains

| Domain (weight) | Where it shows up |
|---|---|
| **Agentic architecture & orchestration** (27%) | `/sf:flow` orchestrator + 11 single-responsibility subagents with human gates and bounded retry loops (verify ≤ 3 rounds). Planner/executor split for unit tests (ADR-4). Parallel fan-out with explicit hand-offs (spec paths and task ids, never whole conversations). Resumable long-running backfill with checkpointed state (ADR-6). |
| **Claude Code configuration & workflows** (20%) | Generated `CLAUDE.md` block, slash commands with `argument-hint`, subagents with `model` and `tools` frontmatter, project skills generated from the repo's own code, `.mcp.json` merge that never clobbers user servers, `settings.json` hooks and permissions. Upgrades re-render assets without touching user-edited specs. |
| **Prompt engineering & structured output** (20%) | Commands state role, inputs, procedure, output contract and next step. Machine-readable artifacts where a program consumes them (`milestones.json`, schema-checked by `validate_plan`); Gherkin ACs and `path::Qualname` refs in specs so drift can be checked by code. |
| **Tool design & MCP integration** (18%) | Narrow, composable tools with ids that chain (`search_symbols` → `get_symbol(id)`), budget parameters, section-level reads, errors raised as MCP `isError` with an actionable next step, and tools that return the minimum context a small model needs (`next_test_milestone`). Connector catalog picks third-party MCP servers from detected signals with `${ENV}` secrets and read-only defaults. |
| **Context management & reliability** (15%) | Token-budgeted retrieval with a measured ledger; incremental index kept fresh by a hook; specs sized ≤ ~1.5k tokens and read by section; subagents receive file paths, not transcripts; retrieval quality measured by an eval set (`tests/evals`). |

**Programmatic vs prompt-based enforcement** is applied deliberately throughout:

| Rule | Enforced by |
|---|---|
| Never read or write secret files | `PreToolUse` hook (`specforge hook guard`, exit 2 blocks the call) + scanner never indexes them + output redaction |
| A unit-test milestone is only "done" when its tests pass | `update_test_milestone` runs the tests itself and refuses otherwise |
| "blocked" must carry a reason for the planner | tool validation error |
| Test plans are well-formed and reference real code | `validate_plan` (schema + index lookup), also in the CLI for CI |
| Executor may not touch production code or edit the plan | `tools:` allow-list on the executor agent; status changes only via the tool |
| Specs don't silently diverge from code | `spec_drift` + `specforge drift --strict` in CI |
| Indexing never uses the network | grammar policy in `parser.py` + socket-blocking test |
| Model choices are valid Claude Code values | `profile.validate_model` |

Prompts still carry intent and judgement (how to design a test, how to word an AC); code carries every rule that has a right answer.

---

## Architecture decision records

### ADR-1: Local SQLite index instead of a vector database
**Context.** Agents need to find code by name and by meaning, quickly, without sending code anywhere.
**Decision.** Symbol-level index in SQLite with FTS5 (bm25, prefix index), camel/snake term splitting, light stemming and a task-word synonym table; ranking adds term coverage and down-weights generated code and tests.
**Why.** Zero infrastructure, single file, no embedding model or API calls (privacy), deterministic results, millisecond queries. Code search is dominated by identifiers, where lexical retrieval is strong.
**Trade-off.** Weaker on purely conceptual queries with no shared vocabulary. Mitigated by synonyms and measured with an eval (recall@any 1.00, MRR 0.78 on 12 tasks). An embedding layer can be added behind the same `get_context` interface if evals show the need.

### ADR-2: tree-sitter with a regex fallback, and grammars never fetched during indexing
**Context.** "Works on any repo" and "indexing is local" pull in opposite directions: 300+ grammars cannot all be bundled.
**Decision.** Parse with tree-sitter for grammars already in the local cache; fetch missing grammars only in an explicit step (`specforge grammars`, run by `init` unless `--offline`), then re-parse. Uncached languages use a pure-Python regex extractor. Only programming languages are parsed; docs and data are indexed as text.
**Why.** Indexing is provably offline (tested with sockets blocked); the one network action is visible and opt-out.
**Trade-off.** Regex extraction is less precise (end lines are inferred). Measured fallback quality is acceptable for navigation; precision returns once grammars are fetched.

### ADR-3: Incremental indexing by (size, mtime_ns) fingerprint, hash on change, parallel parse
**Decision.** One `stat` per file; read and BLAKE2b-hash only files whose fingerprint changed; re-parse only if the hash changed; parse in a process pool above 150 files; write all rows in one transaction with `executemany`; SQLite in WAL mode with `synchronous=NORMAL`. A PostToolUse hook re-indexes each file right after Claude edits it.
**Result (Django, 5.6k indexed files).** No-op refresh 1.62 s → 0.09 s; one-file change 1.68 s → 0.14 s; cold 10.2 s → 6.0 s on 2 cores. See `docs/BENCHMARKS.md`.
**Trade-off.** mtime can be preserved by some tools (e.g. archive extraction); `--full` and the parser/schema version stamps force a rebuild when needed.

### ADR-4: Planner/executor split for unit tests, with model choice per role
**Context.** Deep test design needs a strong model; writing dozens of well-specified tests does not.
**Decision.** `unit-test-planner` (default `opus`) produces `milestones.json` precise enough that execution needs no judgement; `unit-test-executor` (default `haiku`) implements one milestone at a time with a restricted tool set. The model per role comes from the user's profile and is rendered into the agents' `model:` frontmatter.
**Why.** Spend expensive tokens once on analysis, cheap tokens repeatedly on execution. Quality is protected by code, not by trusting the small model: schema validation, target existence checks, test-verified "done", explicit `escalate_if` conditions that route ambiguity back to the planner as `blocked`.
**Trade-off.** Planning is front-loaded; if code changes mid-execution, targets can go stale (caught by `validate_test_plan`).

### ADR-5: User profile outside the repo
**Decision.** Personal choices live in `~/.specforge/profile.json` (precedence: defaults < project `profile_defaults` < user). `specforge profile set` validates values and re-renders agent frontmatter in the current repo.
**Why.** Model choice depends on the person's plan and budget, not the codebase; it should follow them across repos and never be committed. Teams can still ship defaults in `.specforge/config.json`.
**Trade-off.** Agent files in `.claude/agents/` reflect whoever last synced. If the team commits `.claude/`, each developer runs `specforge profile sync` after pulling (idempotent, only rewrites changed files).

### ADR-6: Ticket backfill is opt-in, read-only and resumable
**Decision.** `/sf:backfill` refuses to start until `backfill.enabled` is true in the profile, never writes to the ticket system, records progress after each batch (`backfill_progress`), skips tickets that already have specs, and links each ticket to code through local git archaeology (`ticket_trace`: commits mentioning the key → changed files → current symbols via hunk headers and changed identifiers).
**Why.** Hundreds of tickets will exceed one context window and may be interrupted; checkpointing makes the job restartable and parallelisable (batches to up to 3 subagents). Linking through git gives specs real `path::Qualname` refs, so drift checks work on historical features too.
**Trade-off.** Inferred refs can include neighbours of the real change; specs mark them for human review.

### ADR-7: MCP tool contract
**Decision.** Tools are narrow and composable, return compact text or minified JSON, accept budgets, chain through ids, and raise MCP errors (`isError: true`) whose message names the next action ("Try search_symbols(...)", "Run /sf:test-plan ... first"). Every call is logged with a whole-file baseline for the savings report.
**Why.** Small, predictable outputs keep agent context lean; actionable errors let the model self-correct without a human.

### ADR-8: Secrets defence in depth
Layers: never indexed (scanner), redacted in any returned text (tokens.redact), blocked at tool level (PreToolUse guard, overridable per session with `SPECFORGE_ALLOW_SECRETS=1`), never written to configs (`${ENV}` references), DB connectors read-only by default. The guard fails open if SpecForge isn't installed, so it never bricks a session; the other layers still apply.
