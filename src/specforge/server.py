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
from . import testing as _testing
from . import tickets as _tickets
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


@mcp.tool()
def get_symbol(ref: str, path: str | None = None, context_lines: int = 0) -> str:
    """Exact source of one symbol. ref = id from other tools, a name, Class.method, or 'path::Qualname'."""
    i = ix()
    rows = i.find_symbol(ref, path)
    if not rows:
        raise ToolError(f"Symbol not found: {ref}. Try search_symbols('{ref}') to find the right name or id.")
    if len(rows) > 1 and not path and not str(ref).isdigit():
        exact = [r for r in rows if r["qualname"] == ref]
        if len(exact) != 1:
            return "Ambiguous; pick an id:\n" + "\n".join(
                f"[{r['id']}] {r['qualname']} ({r['path']}:{r['start_line']})" for r in rows)
        rows = exact
    r = rows[0]
    body = i.symbol_source(r, context_lines)
    text = f"{r['path']}:{r['start_line']}-{r['end_line']}  {r['kind']} {r['qualname']}\n```\n{body}\n```"
    return _done("get_symbol", text, i.file_tokens([r["path"]]))


@mcp.tool()
def get_context(task: str, budget_tokens: int | None = None) -> str:
    """Best code for a task within a token budget: full bodies of the most relevant symbols, signatures for the rest."""
    i = ix()
    text, files = i.context_for(task, budget_tokens)
    return _done("get_context", text, i.file_tokens(files))


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
def impact_of(path: str) -> str:
    """Files that likely depend on this file (by imports). Use before changing shared code."""
    i = ix()
    deps = i.dependents(path)
    text = "\n".join(deps) or "No dependents found by imports."
    return _done("impact_of", text, i.file_tokens(deps))


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
    """How many tokens SpecForge saved versus reading whole files, per tool."""
    return json.dumps(ix().savings(), indent=1)


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


@mcp.tool()
def get_profile() -> str:
    """The user's SpecForge profile: model per agent role and ticket backfill preferences."""
    return json.dumps(_profile.effective(ROOT), separators=(",", ":"))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
