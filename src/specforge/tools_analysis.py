"""Analysis tools: code graph, summaries and sources, insights, history.

Registered on the shared server object; see server.py for the core tools and tiers."""

from __future__ import annotations

import json

from . import server as _s
from .index import CodeIndex
from .server import (
    ToolError,
    _done,
    _j,
    _resolve_one,
    _run,
    _src,
    _vector_hits,
    ix,
    mcp,
)

# ----------------------------------------------------------------------------- structural analysis

def _sid(i: CodeIndex, ref: str) -> int:
    r, err = _resolve_one(i, ref, None)
    if err:
        raise ToolError(err)
    return r["id"]


@mcp.tool()
def call_hierarchy(ref: str, direction: str = "callers", depth: int = 2) -> str:
    """Who calls a function (direction='callers') or what it calls ('callees'), transitively, with call lines.
    Calls are resolved by scope: same file, imported files, receiver (crud.create_user), unique name."""
    from . import graph as _g
    i = ix()
    return _done("call_hierarchy", _g.call_hierarchy(i, _sid(i, ref), direction, depth), 0)


@mcp.tool()
def class_hierarchy(name: str) -> str:
    """Ancestors (extends/implements, through the repo) and all descendants of a class/interface/trait."""
    from . import graph as _g
    return json.dumps(_g.class_hierarchy(ix(), name), separators=(",", ":"))


@mcp.tool()
def symbol_importance(limit: int = 25) -> str:
    """Most central symbols by PageRank over the resolved call graph, with caller counts. Where changes hurt most."""
    from . import graph as _g
    return json.dumps(_g.importance(ix(), limit), separators=(",", ":"))


@mcp.tool()
def dead_code(limit: int = 50, include_exported: bool = False) -> str:
    """Unreferenced functions, classes, variables and constants: no resolved callers AND no other occurrence of
    the name anywhere in code (verified by a word search). Decorated/registered and framework hooks excluded."""
    from . import graph as _g
    return json.dumps(_g.dead_code(ix(), limit, include_exported), separators=(",", ":"))


@mcp.tool()
def dependency_cycles(level: str = "file") -> str:
    """Import cycles (strongly connected components), level='file' or 'module'."""
    from . import graph as _g
    return json.dumps(_g.cycles(ix(), level), separators=(",", ":"))


@mcp.tool()
def coupling(limit: int = 30) -> str:
    """Per-module afferent/efferent coupling and instability, with what each module depends on and is used by."""
    from . import graph as _g
    return json.dumps(_g.coupling(ix(), limit), separators=(",", ":"))


@mcp.tool()
def layer_violations() -> str:
    """Dependencies that point the wrong way between layers (presentation > application > infrastructure >
    domain by default; configure 'layers' in .specforge/config.json)."""
    from . import graph as _g
    return json.dumps(_g.layer_violations(ix()), separators=(",", ":"))


@mcp.tool()
def pr_risk(base: str = "HEAD") -> str:
    """0-1 risk for the current change vs a git ref: complexity, callers (blast radius), test exposure, churn,
    size per changed symbol, with concrete review tips."""
    from . import graph as _g
    i = ix()
    i.build()
    return json.dumps(_g.pr_risk(i, base), separators=(",", ":"))


@mcp.tool()
def search_ast(preset: str | None = None, query: str | None = None, language: str | None = None,
               path_glob: str | None = None, limit: int = 50) -> str:
    """Structural search with tree-sitter queries. Presets: empty-catch, broad-except, debug-print, eval-exec,
    sql-string-build, await-in-loop, mutable-default, hardcoded-secret, nested-loops, todo-comment. Or pass a raw
    tree-sitter S-expression query (capture @hit) with language=."""
    from . import astsearch as _a
    try:
        return json.dumps(_a.search(ix(), query, preset, language, path_glob, limit), separators=(",", ":"))
    except ValueError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def plan_refactor(kind: str, ref: str, new_name: str | None = None, new_signature: str | None = None,
                  target_path: str | None = None) -> str:
    """Exact, reviewable edit plans (nothing is written). kind='rename' (new_name): every in-scope code occurrence
    as old/new line pairs plus conflicts; 'signature' (new_signature): every call site and override; 'move'
    (target_path): imports to update and dependencies to carry."""
    from . import refactor as _r
    i = ix()
    sid = _sid(i, ref)
    try:
        if kind == "rename":
            if not new_name:
                raise ValueError("rename needs new_name")
            res = _r.plan_rename(i, sid, new_name)
        elif kind == "signature":
            res = _r.plan_signature_change(i, sid, new_signature or "")
        elif kind == "move":
            if not target_path:
                raise ValueError("move needs target_path")
            res = _r.plan_move(i, sid, target_path)
        else:
            raise ValueError("kind must be rename, signature or move")
    except ValueError as e:
        raise ToolError(str(e)) from e
    return json.dumps(res, separators=(",", ":"))


# ----------------------------------------------------------------------------- summaries & sources

@mcp.tool()
def summaries_needed(target: str | None = None, limit: int = 25) -> str:
    """Undocumented functions/classes (most-called first) with their code, for writing one-line summaries."""
    return json.dumps(ix().symbols_needing_summary(target, limit), separators=(",", ":"))


@mcp.tool()
def save_summaries(items: dict[str, str], model: str = "") -> str:
    """Store one-line summaries {symbol_id: text}. Kept by code hash until the code changes; shown with a ≈ marker
    and used by search."""
    n = ix().save_summaries(items, model)
    return json.dumps({"saved": n})


@mcp.tool()
def list_sources() -> str:
    """Indexed dependency packs and external repos usable via source= on repo_map/search_symbols/get_symbol/
    file_outline/get_context."""
    from . import sources as _sources
    return json.dumps(_sources.registry(), separators=(",", ":"))


@mcp.tool()
def add_source(spec: str) -> str:
    """Index an installed dependency ('pkg:react', 'pkg:fastapi', 'pkg:github.com/gin-gonic/gin') or a local folder
    ('path:../other-service'). Cached per version and shared across repos. GitHub repos: use the CLI (network)."""
    from . import sources as _sources
    try:
        return json.dumps(_sources.add(spec, _s.ROOT, allow_network=False), separators=(",", ":"))
    except ValueError as e:
        raise ToolError(str(e)) from e


# ----------------------------------------------------------------------------- insights (complexity, tests, structure)



@mcp.tool()
def symbol_complexity(target: str, limit: int = 30) -> str:
    """Cyclomatic complexity, control-block nesting, parameter count and lines for one symbol, or for every function
    in a file (target = path)."""
    from . import insights as I
    return _run(I.symbol_complexity, ix(), target, limit)


@mcp.tool()
def file_risk(path: str) -> str:
    """Per-symbol risk for one file: complexity, callers, test evidence, churn and size, highest first."""
    from . import insights as I
    return _run(I.file_risk, ix(), path)


@mcp.tool()
def untested_symbols(target: str | None = None, limit: int = 50) -> str:
    """Code with no test evidence (not reachable from tests via the call graph, never named in a test), most
    complex first. Optional target = folder/module."""
    from . import insights as I
    return _run(I.untested_symbols, ix(), target, limit)


@mcp.tool()
def similar_symbols(ref: str | None = None, threshold: float = 0.8, limit: int = 30) -> str:
    """Near-duplicate functions (MinHash + exact Jaccard over token shingles), repo-wide or for one symbol."""
    from . import insights as I
    return _run(I.similar_symbols, ix(), ref, threshold, 6, limit)


@mcp.tool()
def related_symbols(ref: str, limit: int = 15) -> str:
    """Symbols related to one symbol: shared callers/callees, direct calls, same file, similar names."""
    from . import insights as I
    return _run(I.related_symbols, ix(), ref, limit)


@mcp.tool()
def extraction_candidates(limit: int = 20) -> str:
    """Refactoring opportunities: cross-file duplicates to extract, utilities used by 3+ modules living in one,
    and functions too complex to keep whole."""
    from . import insights as I
    return _run(I.extraction_candidates, ix(), limit)


@mcp.tool()
def decorator_census(limit: int = 40) -> str:
    """Repo-wide count of decorators / annotations / attributes with examples (routes, DI, jobs, validators...)."""
    from . import insights as I
    return _run(I.decorator_census, ix(), limit)


@mcp.tool()
def entry_points(limit: int = 100) -> str:
    """Where execution starts: framework-registered routes/handlers/jobs/commands and program mains."""
    from . import insights as I
    return _run(I.entry_points, ix(), limit)


@mcp.tool()
def signal_chains(entry: str | None = None, max_depth: int = 6, limit: int = 20) -> str:
    """End-to-end paths entry point -> ... -> leaf through the resolved call graph (all entry points, or one)."""
    from . import insights as I
    return _run(I.signal_chains, ix(), entry, max_depth, limit)


@mcp.tool()
def tectonic_map() -> str:
    """Logical modules discovered from imports + calls (label propagation), their cohesion, and files whose logical
    home differs from their folder."""
    from . import insights as I
    return _run(I.tectonic_map, ix())


@mcp.tool()
def architecture_metrics() -> str:
    """Module count, dependency depth, fan-in concentration, average fan-out, most depended-on files, module cycles."""
    from . import insights as I
    return _run(I.architecture_metrics, ix())


@mcp.tool()
def repo_health() -> str:
    """Six-axis health radar (complexity, modularity, testing, dead code, documentation, change risk; 0-100) with
    evidence, trend vs the last run, and what to focus on next."""
    from . import insights as I
    return _run(I.repo_health, ix())


@mcp.tool()
def winnow_symbols(kind: str | None = None, path_glob: str | None = None, name_regex: str | None = None,
                   min_complexity: int = 0, min_callers: int = 0, max_callers: int | None = None,
                   untested: bool | None = None, exported: bool | None = None, has_doc: bool | None = None,
                   changed_since: str | None = None, sort_by: str = "complexity", limit: int = 30) -> str:
    """One query over many signals, e.g. untested functions with complexity >= 8 and 3+ callers changed since main.
    sort_by: complexity | callers | lines | name."""
    from . import insights as I
    return _run(I.winnow, ix(), kind, path_glob, name_regex, min_complexity, min_callers, max_callers, untested,
                exported, has_doc, changed_since, sort_by, limit)


@mcp.tool()
def suggest_queries() -> str:
    """Useful next questions for this repo with the exact tool calls (important symbols, hotspots, entry points)."""
    from . import insights as I
    return _run(I.suggest_queries, ix())


@mcp.tool()
def plan_turn(query: str, budget_tokens: int = 4000) -> str:
    """Before reading anything: the symbols most likely relevant to a query (with ids and size), terms that match
    nothing (negative evidence), and the cheapest next calls within a token budget."""
    i = ix()
    hits = i.hybrid_search(query, 12, vector_hits=_vector_hits(i, query))
    from .index import split_terms as _st
    misses = [t for t in dict.fromkeys(_st(query).split()) if len(t) > 3 and not i.search(t, limit=1)]
    plan, used = [], 0
    for h in hits:
        cost = h.get("tokens") or 50
        if used + cost > budget_tokens:
            continue
        used += cost
        plan.append({"id": h["id"], "ref": f"{h['path']}::{h['qualname']}", "tokens": cost})
    return _j({"read_first": plan, "estimated_tokens": used, "no_match_terms": misses,
               "next": f"get_symbols({[p['id'] for p in plan[:6]]})" if plan else "repo_map()"})


@mcp.tool()
def endpoint_impact(endpoint: str, depth: int = 3) -> str:
    """What breaks if an HTTP endpoint changes ('POST /users/signup'): handler, transitive callees, frontend
    callers of the path, tests and specs that reference it."""
    from . import insights as I
    return _run(I.endpoint_impact, ix(), endpoint, depth)


@mcp.tool()
def flow_edges() -> str:
    """Links a call graph can't see: route -> handler, event emit -> listeners (unhandled / never emitted),
    constructor dependency injection."""
    from . import insights as I
    return _run(I.flow_edges, ix())


@mcp.tool()
def parity_map(left: str, right: str, right_source: str | None = None) -> str:
    """Migration parity between two trees (e.g. legacy/ vs src/, or this repo vs source='gh:org/old'): matched,
    missing and new symbols, parity %."""
    from . import insights as I
    return _run(I.parity_map, ix(), left, right, _src(right_source) if right_source else None)


@mcp.tool()
def search_columns(query: str = "", limit: int = 60) -> str:
    """Database columns from ORM models (SQLAlchemy, SQLModel, Django, JPA, EF, Prisma, TypeORM) and SQL migrations."""
    from . import insights as I
    return _run(I.search_columns, ix(), query, limit)


@mcp.tool()
def dependency_graph(path: str, depth: int = 1, direction: str = "both") -> str:
    """File-level imports (out) and importers (in) of a file, up to `depth` hops."""
    from . import insights as I
    return _run(I.dependency_graph, ix(), path, depth, direction)


@mcp.tool()
def find_implementations(name: str) -> str:
    """Concrete implementations of an interface / abstract class / trait (Go implicit interfaces included).
    Use 'Iface.method' to list the implementing methods."""
    from . import insights as I
    return _run(I.find_implementations, ix(), name)


@mcp.tool()
def check_safety(ref: str, action: str = "edit", new_name: str | None = None) -> str:
    """Preflight before changing code. action: edit | delete | rename (with new_name). Verdict safe/caution/unsafe
    with reasons: callers, importing files, tests, overrides, framework registration, name collisions."""
    from . import insights as I
    return _run(I.check_safety, ix(), ref, action, new_name)


# ----------------------------------------------------------------------------- history

@mcp.tool()
def churn_rate(target: str, days: int = 90) -> str:
    """Commits, authors and lines changed for a file or a single symbol over a time window."""
    from . import history as H
    return _run(H.churn_rate, ix(), target, days)


@mcp.tool()
def symbol_provenance(ref: str) -> str:
    """Who wrote a symbol, when and WHY: its line-range history, ticket keys, decision-bearing commit messages,
    and docs/ADRs that mention it."""
    from . import history as H
    return _run(H.provenance, ix(), ref)


@mcp.tool()
def symbol_diff(base: str = "HEAD", head: str | None = None) -> str:
    """Symbols added / removed / modified / renamed between two revisions (head omitted = working tree)."""
    from . import history as H
    return _run(H.symbol_diff, ix(), base, head)


@mcp.tool()
def digest(since: str = "1 day ago") -> str:
    """Stand-up briefing: commits, authors, tickets, new and changed symbols, hotspots touched, tests changed."""
    from . import history as H
    return _run(H.digest, ix(), since)


@mcp.tool()
def delivery_metrics(days: int = 30) -> str:
    """Delivery signals from git: commits/week, change size, fix and revert share, merges, busiest files."""
    from . import history as H
    return _run(H.delivery_metrics, ix(), days)
