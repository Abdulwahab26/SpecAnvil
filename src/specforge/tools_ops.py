"""Operational tools: diagrams, audits, sessions, packs, runtime traces, regret, contracts, handoff, OpenSpec.

Registered on the shared server object; see server.py for the core tools and tiers."""

from __future__ import annotations

from . import server as _s
from .server import (
    _FUNCS,
    ToolError,
    _done,
    _j,
    _run,
    ix,
    mcp,
)

# ----------------------------------------------------------------------------- diagrams, audit, ops

@mcp.tool()
def render_diagram(kind: str, target: str = "", depth: int = 2) -> str:
    """Mermaid diagram. kind: call (symbol), class (name), deps (file), modules, sequence (entry point), tectonic."""
    from . import diagrams as D
    try:
        return D.render(ix(), kind, target, depth)
    except ValueError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def audit_agent_config() -> str:
    """Wasted tokens in agent setup: always-loaded instruction size, skill/agent sizes, duplicated rules, stale
    paths, MCP servers with missing env vars, estimated tool-schema load, concrete fixes."""
    from . import agentaudit
    return _j(agentaudit.audit(_s.ROOT))


@mcp.tool()
def runtime_coverage(limit: int = 30) -> str:
    """From a coverage report (lcov/Cobertura/JaCoCo): hot paths by hit count, functions never executed, histogram."""
    from . import testing as T
    return _j(T.runtime_coverage(ix(), limit))


@mcp.tool()
def session_snapshot() -> str:
    """What this session has already seen (symbols in context, recent queries) plus the snapshot from before the last
    compaction, so work can continue without re-reading."""
    return _j({"current": _s._session.snapshot(), "before_last_compaction": _s._session.load_previous()})


@mcp.tool()
def tool_stats() -> str:
    """Per-tool calls, average/p95 latency and average tokens returned (local telemetry, never sent anywhere)."""
    return _j(ix().tool_stats())


@mcp.tool()
def redaction_log() -> str:
    """How many secrets were redacted from tool output this session, by kind (values are never stored)."""
    from .tokens import REDACTIONS
    return _j(dict(REDACTIONS))


@mcp.tool()
def index_status() -> str:
    """Index freshness: last indexed, files/symbols, and how many files changed since (0 = fresh)."""
    return _j(ix().index_status())


@mcp.tool()
def file_tree(prefix: str = "", depth: int = 3) -> str:
    """Directory tree with file, line and symbol counts per folder (optionally under a prefix)."""
    from . import insights as I
    return I.file_tree(ix(), prefix, depth)


@mcp.tool()
def get_file_content(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Raw lines of an indexed file (secrets redacted; secret files refused). Prefer get_symbol / file_outline."""
    i = ix()
    f = i.db.execute("SELECT lines, tokens FROM files WHERE path=?", (path,)).fetchone()
    if not f:
        raise ToolError(f"Not indexed (or a secret/ignored file): {path}")
    end = end_line or f["lines"]
    text = i.read_lines(path, start_line, end)
    return _done("get_file_content", f"{path}:{start_line}-{end}\n```\n{text}\n```", f["tokens"])


@mcp.tool()
def list_workspaces() -> str:
    """Monorepo workspaces and sub-projects (npm/pnpm/yarn workspaces, go.work, Cargo, .sln, per-folder manifests)."""
    from . import insights as I
    return _run(I.list_workspaces, ix())


@mcp.tool()
def cross_repo_map() -> str:
    """Package-level map across this repo and every registered source: what each publishes and who imports whom."""
    from . import insights as I
    return _run(I.cross_repo_map, ix())


@mcp.tool()
def list_packs() -> str:
    """Curated starter packs (popular frameworks/SDKs by ecosystem) and whether each is already indexed."""
    from . import packs as P
    return _j(P.list_catalog())


@mcp.tool()
def add_pack(spec: str) -> str:
    """Fetch and index a library's published source without installing it: a catalog name ('react', 'fastapi',
    'gin') or npm:<name>[@ver], pypi:<name>[@ver], go:<module>[@ver], gh:<owner>/<repo>[@tag]. Downloads public
    packages only (no repo code is sent); cached per version and usable via source=."""
    from . import packs as P
    try:
        return _j(P.add(spec))
    except (ValueError, OSError) as e:
        raise ToolError(f"Could not add pack: {e}") from e


# ----------------------------------------------------------------------------- runtime traces, regret, contracts, handoff

@mcp.tool()
def import_runtime_trace(path: str, source: str = "otel") -> str:
    """Ingest an OpenTelemetry trace export (OTLP JSON / JSON lines, .gz ok) or flat span lines; spans are mapped
    to symbols by code.* attributes, HTTP route, or name. Stored locally; enables per-symbol latency."""
    from . import runtime as R
    return _run(R.import_trace, ix(), path, source)


@mcp.tool()
def runtime_hot_paths(limit: int = 20, name: str = "", sort_by: str = "count") -> str:
    """Symbols ranked by runtime hits from imported traces (sort_by count | p95 | total) with p50/p95/max ms."""
    from . import runtime as R
    if not R.has_traces(ix()):
        raise ToolError("No runtime traces yet. Call import_runtime_trace(path) first.")
    return _j(R.hot_paths(ix(), limit, name or None, sort_by))


@mcp.tool()
def runtime_unused(days: int = 30, limit: int = 50) -> str:
    """Functions in traced files with no runtime hits (or none in `days`): reachable on paper, never executed."""
    from . import runtime as R
    return _run(R.unused_paths, ix(), days, limit)


@mcp.tool()
def suggest_corrections(days: int = 30, apply: bool = False) -> str:
    """Why searches miss, mined from this repo's local query/fetch ledger (zero results, re-asked queries,
    reads outside the top 5, vocabulary gaps), with synonym rules; apply=True saves them to config."""
    from . import regret
    return _run(regret.suggest_corrections, ix(), days, apply)


@mcp.tool()
def tune_weights(eval_file: str = "", days: int = 90, apply: bool = False) -> str:
    """Grid-search hybrid search fusion weights for MRR on an eval file (cases: query/task + expected) or on the
    ledger's query -> fetched pairs. Saved only with apply=True and a real gain."""
    from . import regret
    return _run(regret.tune_weights, ix(), eval_file or None, days, apply)


@mcp.tool()
def group_contracts(members: list[str] | None = None, limit: int = 60) -> str:
    """APIs shared across a group of repos (this one + registered sources/packs): provider symbol, signature,
    consuming repos and files. The breaking-change surface between them."""
    from . import insights as I
    return _run(I.group_contracts, ix(), members, limit)


@mcp.tool()
def finalize_handoff(title: str, sections: dict[str, str], kind: str = "audit", force: bool = False) -> str:
    """Write one validated Markdown handoff (kind: audit | investigation | review | design) to specs/handoffs:
    required sections, no placeholders, every `path::Symbol` / `path:line` checked against the index."""
    from . import handoff
    return _run(handoff.finalize, ix(), title, sections, kind, force)


@mcp.tool()
def openspec_status() -> str:
    """OpenSpec project at a glance: capabilities, requirements, scenarios, active changes with task progress."""
    from . import openspec as O
    return _run(O.status, _s.ROOT)


@mcp.tool()
def openspec_check(target: str | None = None) -> str:
    """Structure problems in OpenSpec specs and active changes (or one capability / change), plus code references
    that no longer resolve. Needs no OpenSpec CLI."""
    from . import openspec as O
    return _run(O.check, _s.ROOT, ix(), target)


@mcp.tool()
def openspec_coverage(target: str | None = None, limit: int = 100) -> str:
    """Which OpenSpec scenarios have a test: explicit (title match), likely, or none. target = capability or change."""
    from . import openspec as O
    return _run(O.coverage, _s.ROOT, ix(), target, limit)


@mcp.tool()
def openspec_gaps(limit: int = 30) -> str:
    """Code modules no OpenSpec capability spec mentions: candidates for a baseline spec."""
    from . import openspec as O
    return _run(O.gaps, _s.ROOT, ix(), limit)


@mcp.tool()
def run_tool(name: str, arguments: dict | None = None) -> str:
    """Call any SpecForge tool by name, including ones not loaded in the current tier. set_tool_tier() lists them."""
    fn = _FUNCS.get(name)
    if fn is None or name == "run_tool":
        import difflib
        raise ToolError(f"Unknown tool '{name}'. Close: {difflib.get_close_matches(name, list(_FUNCS), 5)}")
    try:
        return fn(**(arguments or {}))
    except TypeError as e:
        raise ToolError(f"Bad arguments for {name}: {e}") from e
