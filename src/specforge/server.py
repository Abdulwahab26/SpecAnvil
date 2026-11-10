"""SpecForge MCP server (stdio).

Gives the model surgical access to the codebase so it reads signatures and single symbols
instead of whole files. Every call is logged to a local ledger to measure token savings.
Runs entirely locally: no network calls, no telemetry.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer as _Server  # mcp >= 2
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

from .config import load_config
from .index import CodeIndex
from .specs import drift as _drift
from .specs import headings, list_specs, read_section, specs_root
from . import profile as _profile
from .docs import DocEngine
from . import testing as _testing
from . import tickets as _tickets
from . import analysis as _analysis
from .session import Session
from .tokens import estimate_tokens

INSTRUCTIONS = """SpecForge code index for this repository. Prefer these tools over reading whole files:
repo_map -> orientation; get_context(task) -> budgeted relevant code for a task; search_symbols ->
find by name/meaning; file_outline -> structure of one file; get_symbol -> exact source of one symbol;
find_references -> usages. Specs live in the specs folder: read_spec with a section to load only what
you need. Read a full file only when you are about to edit most of it."""

ROOT = Path(os.environ.get("SPECFORGE_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
mcp = _Server("specforge", instructions=INSTRUCTIONS)
_ix: CodeIndex | None = None
_last_refresh = 0.0
_session = Session(ROOT)


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


def _done(tool: str, text: str, baseline: int) -> str:
    ix().record(tool, text, baseline)
    return text


@mcp.tool()
def repo_map(max_modules: int = 25) -> str:
    """Compact map of the repo: languages, modules, and key types/functions with file:line. Start here."""
    i = ix()
    text, files = i.overview_with_files(max_modules=max_modules)
    # baseline: only the files you'd have to open to learn the same symbols
    return _done("repo_map", text, i.file_tokens(files))


@mcp.tool()
def file_outline(path: str) -> str:
    """Symbols of one file (signatures, line ranges, ids, imports) without bodies."""
    i = ix()
    o = i.outline(path)
    if not o:
        raise ToolError(f"Not indexed: {path}. Check the path with search_symbols, or call reindex if the file is new.")
    text = (f"{o['path']} ({o['language']}, {o['lines']} lines, ~{o['file_tokens']} tokens in full)\n"
            + ("imports: " + "; ".join(o["imports"][:15]) + "\n" if o["imports"] else "")
            + "\n".join(o["symbols"] or ["(no symbols; data/text file)"]))
    return _done("file_outline", text, o["file_tokens"])


@mcp.tool()
def search_symbols(query: str, kind: str | None = None, path_prefix: str | None = None, limit: int = 15) -> str:
    """Ranked symbol search (names, camel/snake parts, signatures, docs, paths). kind: class|function|method|interface..."""
    i = ix()
    rows = i.search(query, kind=kind, path_prefix=path_prefix, limit=limit)
    if not rows:
        return "No matches. Try other words, or search_code for raw text."
    text = "\n".join(f"[{r['id']}] {r['kind']} {r['qualname']} — {r['signature']}  ({r['path']}:{r['start_line']})"
                     + (f"  # {r['doc']}" if r["doc"] else "") for r in rows)
    return _done("search_symbols", text, i.file_tokens(r["path"] for r in rows[:5]))


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
    return f"{head}{note}\n```\n{body}\n```", i.file_tokens([r["path"]])


@mcp.tool()
def get_symbol(ref: str, path: str | None = None, context_lines: int = 0, focus: str | None = None,
               force: bool = False, full: bool = False) -> str:
    """Exact source of one symbol. ref = id, name, Class.method or 'path::Qualname'. Very large symbols come back
    as a skeleton (full=true for everything); focus='words' returns the signature and matching lines. Bodies
    already sent this session return a short reference unless force=true."""
    i = ix()
    r, err = _resolve_one(i, ref, path)
    if err:
        return err
    text, base = _render_symbol(i, r, context_lines, focus, force, full)
    return _done("get_symbol", text, base)


@mcp.tool()
def get_symbols(refs: list[str], focus: str | None = None, force: bool = False, full: bool = False) -> str:
    """Several symbols in one call (ids or names). Same dedupe and focus rules as get_symbol."""
    i = ix()
    parts, files, base = [], set(), 0
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
def get_context(task: str, budget_tokens: int | None = None) -> str:
    """Best code for a task within a token budget: full bodies of the most relevant symbols, signatures for the rest.
    Code already sent this session is referenced, not resent, so the budget goes to new code."""
    i = ix()
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
        return "No specs yet. Run `specforge init` or /sf:groom to create some."
    return "\n".join(f"{s['path']} — {s['title']} [{s['status'] or 'n/a'}] ~{s['tokens']} tok, {s['refs']} refs"
                     for s in specs)


@mcp.tool()
def read_spec(path: str, section: str | None = None) -> str:
    """Read a spec. Without section: headings only. With section (heading text): just that section."""
    p = (ROOT / path).resolve()
    sr = specs_root(ROOT).resolve()
    if not str(p).startswith(str(sr)) or not p.exists():
        raise ToolError(f"Spec not found under {sr.name}/: {path}. Call list_specs to see available specs.")
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
    """Detected stack, databases, ticket system, commands and conventions (from .specforge/config.json)."""
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
    return json.dumps(ix().build(full=full))


@mcp.tool()
def token_savings() -> str:
    """How many tokens SpecForge saved versus reading whole files, per tool, plus this session's dedupe savings."""
    data = ix().savings()
    data["this_session"] = {"resends_avoided": _session.resent_avoided, "tokens_not_resent": _session.tokens_avoided,
                            "symbols_in_context": len(_session.delivered), "tool_tier": TIER}
    return json.dumps(data, indent=1)


# ----------------------------------------------------------------------------- unit-test planning

@mcp.tool()
def test_surface(target: str, limit: int = 40) -> str:
    """Unit-test analysis of a module/folder/file: per function branches, side effects (db, network, time...),
    existing test references, real coverage if a report exists, ranked by testing priority."""
    i = ix()
    try:
        data = _testing.test_surface(i, target, limit)
    except ValueError as e:
        raise ToolError(str(e)) from e
    text = json.dumps(data, separators=(",", ":"))
    files = {it["ref"].split("::")[0] for it in data["functions_by_priority"]}
    return _done("test_surface", text, i.file_tokens(files))


@mcp.tool()
def validate_test_plan(module: str) -> str:
    """Check specs/tests/<module>/milestones.json against the schema and the index (targets must exist)."""
    try:
        plan, path = _testing.load_plan(ROOT, module, load_config(ROOT)["specs_dir"])
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e
    errs = _testing.validate_plan(plan, ix())
    return json.dumps({"valid": not errs, "problems": errs, "milestones": len(plan.get("milestones", []))})


@mcp.tool()
def next_test_milestone(module: str) -> str:
    """The next milestone to execute (in_progress first, then todo by priority) with the plan's test command.
    Returns only that milestone so the executor's context stays small."""
    try:
        plan, _ = _testing.load_plan(ROOT, module, load_config(ROOT)["specs_dir"])
    except FileNotFoundError as e:
        raise ToolError(str(e)) from e
    m = _testing.next_milestone(plan)
    if m is None:
        return json.dumps({"done": True, "message": "All milestones are done or blocked."})
    return json.dumps({"test_framework": plan["test_framework"], "test_command": plan["test_command"],
                       "conventions": plan.get("conventions", ""), "milestone": m}, separators=(",", ":"))


@mcp.tool()
def update_test_milestone(module: str, milestone_id: str, status: str, notes: str = "", tests_added: int = 0) -> str:
    """Set a milestone's status (in_progress | done | blocked). 'done' is only accepted if the milestone's
    tests pass when this tool runs them; 'blocked' requires notes for the planner."""
    try:
        res = _testing.update_milestone(ROOT, module, milestone_id, status, notes, tests_added,
                                        load_config(ROOT)["specs_dir"])
    except (ValueError, FileNotFoundError) as e:
        raise ToolError(str(e)) from e
    return json.dumps(res)


# ----------------------------------------------------------------------------- ticket backfill

@mcp.tool()
def ticket_keys(limit: int = 50) -> str:
    """Ticket keys found in git history (PROJ-123, AB#123, #123) with commit counts and date range."""
    keys = _tickets.keys_in_history(ROOT)[:limit]
    have = _tickets.existing_feature_keys(ROOT, load_config(ROOT)["specs_dir"])
    return json.dumps([{**k, "has_spec": k["key"] in have} for k in keys], separators=(",", ":"))


@mcp.tool()
def ticket_trace(key: str) -> str:
    """Commits mentioning a ticket key, files they changed, and current symbols they most likely touched
    (as path::Qualname refs for the spec's Code map)."""
    return json.dumps(_tickets.trace(ROOT, ix(), key), separators=(",", ":"))


@mcp.tool()
def backfill_progress(done: list[str] | None = None, failed: dict[str, str] | None = None,
                      skipped: list[str] | None = None, query: str | None = None, reset: bool = False) -> str:
    """Read or update resumable backfill state. Call with no arguments to read; pass keys as you finish them."""
    if not any([done, failed, skipped, query, reset]):
        st = _tickets.load_state(ROOT)
    else:
        st = _tickets.update_state(ROOT, query, done, failed, skipped, reset)
    have = sorted(_tickets.existing_feature_keys(ROOT, load_config(ROOT)["specs_dir"]))
    return json.dumps({**st, "specs_existing": have}, separators=(",", ":"))


# ----------------------------------------------------------------------------- documentation

def _docs() -> DocEngine:
    return DocEngine(ix())


@mcp.tool()
def doc_plan() -> str:
    """Detected repo archetypes (backend, frontend, qa-automation, azure-functions, aws-lambda, infrastructure,
    library, cli, data-pipeline, mobile, worker, microservices) and every required document with its status."""
    de = _docs()
    return json.dumps({"archetypes": de.facts.archetypes(), "documents": de.status()}, separators=(",", ":"))


@mcp.tool()
def doc_facts(doc_id: str, max_items: int = 120) -> str:
    """Everything needed to write one document: purpose, required sections, audience, locally extracted facts
    with file:line, and must_mention (items that must appear in the document)."""
    de = _docs()
    try:
        data = de.facts_for(doc_id, max_items)
    except KeyError as e:
        raise ToolError(str(e)) from e
    text = json.dumps(data, separators=(",", ":"))
    files = set()
    for v in data["facts"].values():
        for item in (v if isinstance(v, list) else []):
            if isinstance(item, dict):
                at = str(item.get("at") or item.get("file") or "").split(":")[0]
                if at:
                    files.add(at)
    return _done("doc_facts", text, ix().file_tokens(files))


@mcp.tool()
def doc_check(doc_id: str) -> str:
    """Completeness check for one document: required sections, every must_mention item present, no placeholders
    outside 'Open questions', relative links resolve. Fix all problems, then call doc_register."""
    try:
        return json.dumps(_docs().check(doc_id))
    except KeyError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def doc_register(doc_id: str, model: str = "") -> str:
    """Record that a document passed doc_check, with a hash of the facts it describes (it turns 'stale'
    automatically when those facts change). Refuses if the check fails."""
    try:
        res = _docs().register(doc_id, model)
    except KeyError as e:
        raise ToolError(str(e)) from e
    if not res.get("registered"):
        raise ToolError("Not registered; doc_check failed: " + "; ".join(res.get("problems", [])))
    return json.dumps(res)


@mcp.tool()
def get_profile() -> str:
    """The user's SpecForge profile: model per agent role and ticket backfill preferences."""
    return json.dumps(_profile.effective(ROOT), separators=(",", ":"))


# ----------------------------------------------------------------------------- tool tiers
# Every tool schema costs tokens on every turn. Tiers expose only what a session needs.
TIERS = {
    "core": {"repo_map", "get_context", "search_symbols", "file_outline", "get_symbol", "get_symbols",
             "find_references", "read_spec", "token_savings"},
}
TIERS["standard"] = TIERS["core"] | {"search_code", "changed_symbols", "impact_of", "hotspots", "list_specs",
                                     "spec_drift", "project_profile", "get_profile", "reindex"}
TIER = (os.environ.get("SPECFORGE_TOOLS") or load_config(ROOT).get("tool_tier") or "full").lower()
if TIER in TIERS:
    for _t in list(mcp._tool_manager._tools if hasattr(mcp, "_tool_manager") else []):
        if _t not in TIERS[TIER]:
            mcp.remove_tool(_t)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
