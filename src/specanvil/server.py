"""SpecAnvil MCP server (stdio).

Gives the model surgical access to the codebase so it reads signatures and single symbols
instead of whole files. Every call is logged to a local ledger to measure token savings.
Runs entirely locally: no network calls, no telemetry.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

try:
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as _Server  # mcp >= 2
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import Context
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

from . import analysis as _analysis
from .config import debug_exc, discover_root, load_config
from .index import CodeIndex
from .session import Session
from .specs import drift as _drift
from .specs import headings, list_specs, read_section, specs_root
from .tokens import estimate_tokens

INSTRUCTIONS = """SpecAnvil code index for this repository. Prefer these tools over reading whole files:
repo_map -> orientation; get_context(task) -> budgeted relevant code for a task; search_symbols ->
find by name/meaning; file_outline -> structure of one file; get_symbol -> exact source of one symbol;
find_references -> usages. Specs live in the specs folder: read_spec with a section to load only what
you need. Read a full file only when you are about to edit most of it."""

ROOT = discover_root()  # works for any MCP client: env var, client project dir, or nearest .specanvil/.git
mcp = _Server("specanvil", instructions=INSTRUCTIONS)
_ix: CodeIndex | None = None
_last_refresh = 0.0
_session = Session(ROOT)

# Every tool is wrapped once: latency, the session query log, and a ledger row even for tools that don't
# report a token baseline. (functools.wraps keeps the signature, so MCP schemas are unchanged.)
import contextvars  # noqa: E402
import functools  # noqa: E402
import inspect  # noqa: E402

_CALL = contextvars.ContextVar("specanvil_call", default=None)
_orig_tool = mcp.tool


_FUNCS: dict = {}  # every tool function by name, including tools hidden by the current tier (see run_tool)
_SESSION_ID = f"{os.getpid()}-{int(time.time())}"


def _timed_tool(*dargs, **dkwargs):
    register = _orig_tool(*dargs, **dkwargs)

    def wrap(fn):
        name = dkwargs.get("name") or fn.__name__
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            state = {"t0": time.perf_counter(), "recorded": False}
            token = _CALL.set(state)
            try:
                first = next(iter(kwargs.values()), args[0] if args else "")
                _session.note(name, first)
                out = fn(*args, **kwargs)
                if not state["recorded"] and isinstance(out, str):
                    try:
                        ix().record(name, out, 0, (time.perf_counter() - state["t0"]) * 1000)
                    except Exception:  # noqa: BLE001 - stats must never break a tool
                        debug_exc("server:80")
                        pass
                if isinstance(out, str):
                    try:
                        from . import regret
                        bound = sig.bind_partial(*args, **kwargs).arguments
                        regret.log_call(ix(), _SESSION_ID, name, bound, out)
                    except Exception:  # noqa: BLE001 - the regret ledger must never break a tool
                        debug_exc("server:87")
                        pass
                return out
            finally:
                _CALL.reset(token)
        _FUNCS[name] = inner
        return register(inner)
    return wrap


mcp.tool = _timed_tool  # type: ignore[method-assign]


def ix() -> CodeIndex:
    """Open the index, building it on first use and refreshing incrementally every 20s."""
    global _ix, _last_refresh
    if _ix is None:
        _ix = CodeIndex(ROOT)
    if not _ix.is_built():
        _ix.build()
        _last_refresh = time.time()
    elif time.time() - _last_refresh > 20:
        _ix.build()
        _last_refresh = time.time()
    return _ix


def _src(source: str | None) -> CodeIndex:
    """The main repo index, or an external source (pkg:<name>, gh:<owner>/<repo>, path:<dir>)."""
    if not source:
        return ix()
    from . import sources as _sources
    try:
        return _sources.open_source(source)
    except ValueError as e:
        raise ToolError(str(e)) from e


def _done(tool: str, text: str, baseline: int) -> str:
    st = _CALL.get()
    ms = (time.perf_counter() - st["t0"]) * 1000 if st else None
    ix().record(tool, text, baseline, ms)
    if st:
        st["recorded"] = True
    return text


@mcp.tool()
def repo_map(max_modules: int = 25, source: str | None = None) -> str:
    """Compact map of the repo: languages, modules, and key types/functions with file:line. Start here.
    source='pkg:react' / 'gh:owner/repo' maps an indexed dependency or external repo instead."""
    i = _src(source)
    text, files = i.overview_with_files(max_modules=max_modules)
    # baseline: only the files you'd have to open to learn the same symbols
    return _done("repo_map", text, i.file_tokens(files))


@mcp.tool()
def file_outline(path: str, source: str | None = None) -> str:
    """Symbols of one file (signatures, line ranges, ids, imports) without bodies."""
    i = _src(source)
    o = i.outline(path)
    if not o:
        raise ToolError(f"Not indexed: {path}. Check the path with search_symbols, or call reindex if the file is new.")
    text = (f"{o['path']} ({o['language']}, {o['lines']} lines, ~{o['file_tokens']} tokens in full)\n"
            + ("imports: " + "; ".join(o["imports"][:15]) + "\n" if o["imports"] else "")
            + "\n".join(o["symbols"] or ["(no symbols; data/text file)"]))
    return _done("file_outline", text, o["file_tokens"])


@mcp.tool()
def search_symbols(query: str, kind: str | None = None, path_prefix: str | None = None, limit: int = 15,
                   mode: str = "auto", source: str | None = None) -> str:
    """Ranked symbol search. mode: lexical (names/signatures), hybrid (+ body vocabulary + embeddings if built),
    semantic (embeddings only, for paraphrased intent), auto (lexical, falling back to hybrid when matches are weak).
    kind: class|function|method|interface|variable|constant|field|type. source: search a dependency/external repo."""
    i = _src(source)
    if mode == "semantic":
        from . import semantic as _sem
        try:
            rows = _sem.search(i, query, limit=limit, kind=kind)
        except (ValueError, ImportError) as e:
            raise ToolError(f"{e} Install with: pip install 'specanvil[semantic]'") from e
    elif mode == "hybrid":
        rows = i.hybrid_search(query, limit, kind, path_prefix, _vector_hits(i, query))
    else:
        rows = i.search(query, kind=kind, path_prefix=path_prefix, limit=limit)
        if mode == "auto" and (not rows or rows[0]["score"] > -12):  # weak lexical evidence
            rows = i.hybrid_search(query, limit, kind, path_prefix, _vector_hits(i, query))
    if not rows:
        return "No matches. Try other words, or search_code for raw text."
    text = "\n".join(f"[{r['id']}] {r['kind']} {r['qualname']} — {r['signature']}  ({r['path']}:{r['start_line']})"
                     + (f"  # {r['doc']}" if r["doc"] else "") for r in rows)
    return _done("search_symbols", text, i.file_tokens(r["path"] for r in rows[:5]))


def _vector_hits(i: CodeIndex, query: str):
    try:
        from . import semantic as _sem
        return _sem.search(i, query, 60) if _sem.has_vectors(i) else None
    except Exception:  # noqa: BLE001 - embeddings are optional
        debug_exc("server:187")
        return None


def _resolve_one(i: CodeIndex, ref: str, path: str | None):
    rows = i.find_symbol(ref, path)
    if not rows:
        raise ToolError(f"Symbol not found: {ref}. Try search_symbols('{ref}') to find the right name or id.")
    if len(rows) > 1 and not path and not str(ref).isdigit():
        exact = [r for r in rows if r["qualname"] == ref]
        if len(exact) != 1:
            return None, "Ambiguous; pick an id:\n" + "\n".join(
                f"[{r['id']}] {r['qualname']} ({r['path']}:{r['start_line']})" for r in rows)
        rows = exact
    return rows[0], None


def _render_symbol(i: CodeIndex, r, context_lines: int, focus: str | None, force: bool,
                   full: bool = False) -> tuple[str, int]:
    """Returns (text, baseline). Unchanged bodies already sent this session become a one-line reference."""
    head = f"{r['path']}:{r['start_line']}-{r['end_line']}  {r['kind']} {r['qualname']}  [{r['id']}]"
    if not force and not focus and _session.seen(r["id"], r["body_hash"]):
        _session.avoided(r["tokens"] or 0)
        return head + "  (already in your context this session, unchanged; force=true to resend)", \
            i.file_tokens([r["path"]])
    body = i.symbol_source(r, context_lines)
    note = ""
    if focus:
        body, cut = _analysis.excerpt(body, focus)
        if cut:
            note = f"  (focused on '{focus}'; get_symbol without focus for the full body)"
    elif not full and (cap := int(load_config(ROOT).get("symbol_max_tokens", 1500))) > 0:
        body, cut = _analysis.skeleton(body, cap)
        if cut:
            note = f"  (large: {r['tokens']} tokens; skeleton shown, nested blocks folded. focus='terms' or full=true for more)"
        else:
            _session.mark(r["id"], r["body_hash"])
    else:
        _session.mark(r["id"], r["body_hash"])
    _session.labels[r["id"]] = f"{r['path']}::{r['qualname']}"
    return f"{head}{note}\n```\n{body}\n```", i.file_tokens([r["path"]])


@mcp.tool()
def get_symbol(ref: str, path: str | None = None, context_lines: int = 0, focus: str | None = None,
               force: bool = False, full: bool = False, source: str | None = None) -> str:
    """Exact source of one symbol. ref = id, name, Class.method or 'path::Qualname'. Very large symbols come back
    as a skeleton (full=true for everything); focus='words' returns the signature and matching lines. Bodies
    already sent this session return a short reference unless force=true."""
    i = _src(source)
    r, err = _resolve_one(i, ref, path)
    if err:
        return err
    text, base = _render_symbol(i, r, context_lines, focus, force, full)
    return _done("get_symbol", text, base)


@mcp.tool()
def get_symbols(refs: list[str], focus: str | None = None, force: bool = False, full: bool = False) -> str:
    """Several symbols in one call (ids or names). Same dedupe and focus rules as get_symbol."""
    i = ix()
    parts, files = [], set()
    for ref in refs[:25]:
        try:
            r, err = _resolve_one(i, str(ref), None)
        except ToolError as e:
            parts.append(str(e))
            continue
        if err:
            parts.append(err)
            continue
        text, _ = _render_symbol(i, r, 0, focus, force, full)
        parts.append(text)
        files.add(r["path"])
    out = "\n\n".join(parts)
    return _done("get_symbols", out, i.file_tokens(files))


@mcp.tool()
def get_context(task: str, budget_tokens: int | None = None, source: str | None = None) -> str:
    """Best code for a task within a token budget: full bodies of the most relevant symbols, signatures for the rest.
    Uses embeddings too when built. Code already sent this session is referenced, not resent."""
    i = _src(source)
    text, files = i.context_for(task, budget_tokens, delivered=_session.ledger())
    return _done("get_context", text, i.file_tokens(files))


@mcp.tool()
def hotspots(days: int = 180, limit: int = 20) -> str:
    """Riskiest functions: complexity (branches) × recent git churn. Use to prioritise reviews, tests, refactors."""
    i = ix()
    return json.dumps(_analysis.hotspots(i, days, limit), separators=(",", ":"))


@mcp.tool()
def search_code(pattern: str, path_glob: str | None = None, regex: bool = False, limit: int = 40) -> str:
    """Text search across indexed files (secrets excluded). Returns path:line: snippet."""
    i = ix()
    try:
        lines, _ = i.grep(pattern, path_glob, limit, regex=regex)
    except Exception as e:  # noqa: BLE001 - bad regex
        raise ToolError(f"Invalid pattern ({e}). Pass regex=False for a literal search.") from e
    text = "\n".join(lines) or "No matches."
    return _done("search_code", text, estimate_tokens(text))


@mcp.tool()
def find_references(name: str, include_docs: bool = False, limit: int = 40) -> str:
    """Usages of an identifier in code (include_docs=True to add specs/markdown), labelled with the enclosing symbol."""
    i = ix()
    lines, _ = i.grep(name, None, limit, word=True, code_only=not include_docs)
    out = []
    for ln in lines:
        path, lineno, snippet = ln.split(":", 2)
        enc = i.enclosing_symbol(path, int(lineno))
        out.append(f"{path}:{lineno} in {enc['qualname'] if enc else '(module)'}: {snippet.strip()}")
    text = "\n".join(out) or "No references."
    return _done("find_references", text, estimate_tokens(text))


@mcp.tool()
def changed_symbols(base: str = "HEAD") -> str:
    """Symbols touched by uncommitted changes (or since a git ref like main). Use for reviews and spec verification."""
    i = ix()
    i.build()
    rows = i.changed_symbols(base)
    if not rows:
        return f"No changes against {base}."
    text = "\n".join(f"{r['path']} :: {r['symbol']}" + (f" [{r['id']}]" if r["id"] else "") for r in rows)
    try:
        diff = subprocess.run(["git", "diff", base], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        diff = ""
    return _done("changed_symbols", text, estimate_tokens(diff))


@mcp.tool()
def impact_of(path: str, depth: int = 2) -> str:
    """Files that depend on this file through imports, transitively up to `depth` hops, tests flagged."""
    i = ix()
    deps = _analysis.impact(i, path, depth)
    text = "\n".join(f"{'  ' * (d['hops'] - 1)}{d['path']}" + ("  [test]" if d["test"] else "") for d in deps) \
        or "No dependents found by imports."
    return _done("impact_of", text, i.file_tokens(d["path"] for d in deps))


@mcp.tool(name="list_specs")
def list_specs_tool() -> str:
    """All spec files with title, status, size and number of code references."""
    specs = list_specs(ROOT)
    if not specs:
        return "No specs yet. Run `specanvil init` or /sa:groom to create some."
    return "\n".join(f"{s['path']} — {s['title']} [{s['status'] or 'n/a'}] ~{s['tokens']} tok, {s['refs']} refs"
                     for s in specs)


@mcp.tool()
def read_spec(path: str, section: str | None = None) -> str:
    """Read a spec. Without section: headings only. With section (heading text): just that section."""
    from .specs import spec_roots
    p = (ROOT / path).resolve()
    roots = [r.resolve() for r in spec_roots(ROOT)] or [specs_root(ROOT).resolve()]
    if not any(str(p).startswith(str(r)) for r in roots) or not p.exists():
        raise ToolError(f"Spec not found under {', '.join(r.name + '/' for r in roots)}: {path}. "
                        "Call list_specs to see available specs.")
    text = p.read_text(encoding="utf-8", errors="ignore")
    full = estimate_tokens(text)
    if section is None:
        out = f"{path} (~{full} tokens). Sections:\n" + "\n".join(headings(text))
    else:
        out = read_section(text, section) or f"Section '{section}' not found. Sections:\n" + "\n".join(headings(text))
    return _done("read_spec", out, full)


@mcp.tool()
def spec_drift() -> str:
    """Compare specs with code: missing or changed referenced symbols, and modules no spec covers."""
    i = ix()
    return json.dumps(_drift(ROOT, i), indent=1)


@mcp.tool()
def project_profile() -> str:
    """Detected stack, databases, ticket system, commands and conventions (from .specanvil/config.json)."""
    cfg = load_config(ROOT)
    prof = cfg.get("profile") or {}
    stack = dict(prof.get("stack") or {})
    stack["languages"] = [f"{l['lang']}:{l['files']}" for l in stack.get("languages", [])]
    data = {"stack": {k: v for k, v in stack.items() if v not in (None, [], {}, False)},
            "projects": prof.get("projects"), "conventions": prof.get("conventions"),
            "tickets": cfg.get("tickets"), "databases": cfg.get("databases"), "app_url": cfg.get("app_url"),
            "specs_dir": cfg.get("specs_dir"), "connectors": cfg.get("connectors")}
    return json.dumps({k: v for k, v in data.items() if v not in (None, [], {})}, separators=(",", ":"))


@mcp.tool()
def reindex(full: bool = False) -> str:
    """Refresh the index (incremental unless full=True)."""
    global _ix, _last_refresh
    if _ix is None:
        _ix = CodeIndex(ROOT)
    stats = _ix.build(full=full and _ix.is_built())  # a first build is already full: never index twice
    _last_refresh = time.time()
    return json.dumps(stats)


@mcp.tool()
def token_savings() -> str:
    """How many tokens SpecAnvil saved versus reading whole files, per tool, plus this session's dedupe savings."""
    data = ix().savings()
    data["this_session"] = {"resends_avoided": _session.resent_avoided, "tokens_not_resent": _session.tokens_avoided,
                            "symbols_in_context": len(_session.delivered), "tool_tier": TIER}
    return json.dumps(data, indent=1)


def _j(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


def _run(fn, *a, **k) -> str:
    try:
        return _j(fn(*a, **k))
    except (ValueError, KeyError) as e:
        raise ToolError(str(e)) from e


# ----------------------------------------------------------------------------- more tools (registered on import)
from . import tools_analysis, tools_ops, tools_workflow  # noqa: E402, F401

# ----------------------------------------------------------------------------- tool tiers
# Every tool schema costs tokens on every turn. Tiers expose only what a session needs; set_tool_tier switches
# at runtime (tools/list_changed), and run_tool reaches any tool even on clients that ignore that notification.
TIERS = {
    "lite": {"repo_map", "get_context", "search_symbols", "get_symbol", "set_tool_tier", "run_tool"},
}
TIERS["core"] = TIERS["lite"] | {"file_outline", "get_symbols", "find_references", "read_spec", "token_savings"}
TIERS["standard"] = TIERS["core"] | {"search_code", "changed_symbols", "impact_of", "hotspots", "list_specs",
                                     "spec_drift", "project_profile", "get_profile", "reindex", "call_hierarchy",
                                     "class_hierarchy", "search_ast", "plan_refactor", "list_sources", "check_safety",
                                     "plan_turn", "find_implementations", "dependency_graph", "get_file_content",
                                     "index_status", "finalize_handoff", "openspec_status", "openspec_check",
                                     "openspec_coverage", "openspec_gaps"}
TIERS["analysis"] = TIERS["standard"] | {"symbol_importance", "dead_code", "dependency_cycles", "coupling",
                                         "layer_violations", "pr_risk", "symbol_complexity", "file_risk",
                                         "untested_symbols", "similar_symbols", "related_symbols",
                                         "extraction_candidates", "signal_chains", "entry_points", "tectonic_map",
                                         "architecture_metrics", "repo_health", "winnow_symbols", "endpoint_impact",
                                         "flow_edges", "symbol_provenance", "symbol_diff", "churn_rate",
                                         "render_diagram", "search_columns", "list_packs", "add_pack",
                                         "runtime_hot_paths", "runtime_unused", "group_contracts"}
MODEL_TIERS = [("haiku", "core"), ("sonnet", "standard"), ("opus", "analysis"), ("fable", "analysis"),
               ("mythos", "analysis")]
_ALL_TOOLS: dict = {}
TIER = "full"


def _registry() -> dict | None:
    """The MCP SDK's name -> tool table. This is the only place SpecAnvil touches SDK internals (there is no
    public API to hide and re-show a tool). If a future SDK changes it, tiers switch off and every tool stays
    listed, instead of the server failing to start; run_tool keeps working either way."""
    reg = getattr(getattr(mcp, "_tool_manager", None), "_tools", None)
    return reg if isinstance(reg, dict) else None


def _apply_tier(names: set[str] | None) -> list[str]:
    """Show exactly `names` (None = everything). Returns the tool names now visible."""
    tm = _registry()
    if tm is None:
        return sorted(_FUNCS)
    if not _ALL_TOOLS:
        _ALL_TOOLS.update(tm)
    want = set(_ALL_TOOLS) if names is None else (names & set(_ALL_TOOLS))
    for n in list(tm):
        if n not in want:
            del tm[n]
    for n in want:
        tm.setdefault(n, _ALL_TOOLS[n])
    return sorted(want)


@_orig_tool()
async def set_tool_tier(tier: str = "", enable: list[str] | None = None, model: str = "",
                        ctx: Context | None = None) -> str:
    """Change the loaded tool set: tier lite | core | standard | analysis | full, and/or enable extra tools by
    name, or pass your model id to pick a tier. No arguments: list every tool by tier."""
    global TIER
    if not tier and not enable and not model:
        seen: set
        groups, seen = {}, set()
        for t in ("lite", "core", "standard", "analysis"):
            groups[t] = sorted(TIERS[t] - seen)
            seen |= TIERS[t]
        groups["full"] = sorted(set(_ALL_TOOLS or _registry() or _FUNCS) - seen)
        return _j({"current": TIER, "tiers_add": groups})
    if model and not tier:
        tier = next((t for k, t in MODEL_TIERS if k in model.lower()), TIER)
    tier = (tier or TIER).lower()
    if tier not in TIERS and tier != "full":
        raise ToolError(f"Unknown tier '{tier}'. Use one of {sorted(TIERS) + ['full']}.")
    names = None if tier == "full" else set(TIERS[tier])
    if enable:
        unknown = [e for e in enable if e not in _ALL_TOOLS]
        if unknown:
            raise ToolError(f"Unknown tools {unknown}")
        names = None if names is None else names | set(enable)
    before = set(_registry() or _FUNCS)
    visible = _apply_tier(names)
    TIER = tier
    if ctx is not None:
        try:
            # classic notifications/tools/list_changed (what Claude Code and most clients listen for) ...
            await ctx.session.send_tool_list_changed()
            # ... and the newer subscriptions/listen event where the SDK has it
            if hasattr(ctx, "notify_tools_changed"):
                await ctx.notify_tools_changed()
        except Exception:  # noqa: BLE001 - some clients/transports don't take notifications
            debug_exc("server:1217")
            pass
    added = sorted(set(visible) - before)
    return _j({"tier": tier, "tools": len(visible), "added": added, "removed": sorted(before - set(visible)),
               "note": "If your client does not refresh its tool list, call run_tool(name, arguments)."})


def _compact_schema(node):
    """Drop pydantic's auto titles and collapse Optional[X] (anyOf X|null) to X: same arguments, fewer tokens on
    every turn. Arguments are still validated against the Python signature."""
    if isinstance(node, dict):
        node.pop("title", None)
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            rest = [x for x in any_of if x != {"type": "null"}]
            if len(rest) == 1 and len(rest) < len(any_of):
                node.pop("anyOf")
                node.update(rest[0])
        if node.get("additionalProperties") is True and node.get("type") == "object":
            node.pop("additionalProperties")
        for v in list(node.values()):
            _compact_schema(v)
    elif isinstance(node, list):
        for v in node:
            _compact_schema(v)
    return node


for _tool in (_registry() or {}).values():
    if isinstance(getattr(_tool, "parameters", None), dict):
        _compact_schema(_tool.parameters)
_ALL_TOOLS.update(_registry() or {})
TIER = (os.environ.get("SPECANVIL_TOOLS") or load_config(ROOT).get("tool_tier") or "lite").lower()
_apply_tier(None if TIER == "full" or TIER not in TIERS else set(TIERS[TIER]))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
