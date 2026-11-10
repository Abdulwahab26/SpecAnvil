"""Refactoring plans: exact edit lists an agent (or a human) can review and apply.

Plans never write files. Each edit is {path, line, old_text, new_text}, computed on code with
strings and comments excluded, and scoped to files that can actually see the symbol (its own
file plus files importing it), so a rename of `get` does not touch every `get` in the repo.
"""

from __future__ import annotations

import re

from .extract import _strip_noise
from .graph import CodeGraph

IDENT_OK = re.compile(r"^[A-Za-z_$][\w$]*$")


def _scope_files(g: CodeGraph, sid: int) -> list[str]:
    s = g.sym[sid]
    files = {s["path"]} | g.rdeps.get(s["path"], set())
    if len(g.by_name.get(s["name"], [])) == 1:  # unique name: any file mentioning it is in scope
        files |= {r[0] for r in g.ix.db.execute("SELECT DISTINCT path FROM calls WHERE callee=?", (s["name"],))}
    return sorted(files)


def plan_rename(ix, sid: int, new_name: str) -> dict:
    if not IDENT_OK.match(new_name):
        raise ValueError(f"'{new_name}' is not a valid identifier")
    g = CodeGraph(ix)
    if sid not in g.sym:
        raise ValueError(f"No symbol id {sid}")
    s = g.sym[sid]
    old = s["name"]
    conflicts = [f"{r['path']}:{r['start_line']} {r['qualname']}" for r in ix.db.execute(
        "SELECT path, start_line, qualname FROM symbols WHERE name=? AND (path=? OR parent=?)",
        (new_name, s["path"], s["parent"] or "\0"))]
    rx = re.compile(r"(?<![\w$])" + re.escape(old) + r"(?![\w$])")
    edits, ambiguous = [], []
    other_defs = {r["path"] for r in ix.db.execute("SELECT path FROM symbols WHERE name=? AND id!=?", (old, sid))}
    for path in _scope_files(g, sid):
        try:
            raw = (ix.root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        code_lines = _strip_noise(raw).split("\n")
        raw_lines = raw.split("\n")
        for i, (cl, rl) in enumerate(zip(code_lines, raw_lines), 1):
            if not rx.search(cl):
                continue
            # replace only occurrences that are in code (positions where the stripped line still has the name)
            new_line, last = [], 0
            for m in rx.finditer(cl):
                new_line.append(rl[last:m.start()] + new_name)
                last = m.end()
            new_line.append(rl[last:])
            edit = {"path": path, "line": i, "old_text": rl, "new_text": "".join(new_line)}
            if path in other_defs and path != s["path"]:
                ambiguous.append(edit)  # another symbol with the same name lives here: needs a human look
            else:
                edits.append(edit)
    return {"symbol": f"{s['path']}::{s['qualname']}", "rename": f"{old} -> {new_name}", "edits": edits,
            "needs_review": ambiguous, "conflicts": conflicts,
            "files": sorted({e["path"] for e in edits}),
            "note": "Review needs_review edits: another symbol with the same name is defined in those files. "
                    "Dynamic references (reflection, string lookups, templates) are not detected."}


def plan_signature_change(ix, sid: int, new_signature: str = "") -> dict:
    """Every call site that must change when this function's parameters change."""
    g = CodeGraph(ix)
    if sid not in g.sym:
        raise ValueError(f"No symbol id {sid}")
    s = g.sym[sid]
    sites = []
    for caller, line in g.callers(sid):
        c = g.sym[caller]
        try:
            text = (ix.root / c["path"]).read_text(encoding="utf-8", errors="replace").split("\n")[line - 1]
        except (OSError, IndexError):
            text = ""
        sites.append({"path": c["path"], "line": line, "in": c["qualname"], "call": text.strip()[:200]})
    overrides = [f"{r['path']}:{r['start_line']} {r['qualname']}" for r in ix.db.execute(
        "SELECT path, start_line, qualname FROM symbols WHERE name=? AND id!=? AND kind IN ('method','function') "
        "AND parent!=''", (s["name"], sid))] if s["parent"] else []
    return {"symbol": f"{s['path']}::{s['qualname']}", "current": s["signature"], "proposed": new_signature,
            "call_sites": sites, "possible_overrides": overrides[:30],
            "note": "Update each call site and any override/implementation to match the new signature."}


def plan_move(ix, sid: int, target_path: str) -> dict:
    """What moving a symbol to another file touches: its body, files importing it, and its own dependencies."""
    g = CodeGraph(ix)
    if sid not in g.sym:
        raise ValueError(f"No symbol id {sid}")
    s = g.sym[sid]
    importers = []
    for p in sorted(g.rdeps.get(s["path"], set())):
        for (line,) in ix.db.execute("SELECT source FROM imports WHERE path=?", (p,)):
            if re.search(r"(?<![\w$])" + re.escape(s["name"]) + r"(?![\w$])", line):
                importers.append({"path": p, "import": line})
    needs = sorted({g.sym[t]["path"] for t, _ in g.callees(sid) if g.sym[t]["path"] != s["path"]})
    same_file_deps = sorted({g.sym[t]["qualname"] for t, _ in g.callees(sid) if g.sym[t]["path"] == s["path"]})
    return {"symbol": f"{s['path']}::{s['qualname']}", "from": f"{s['path']}:{s['start_line']}-{s['end_line']}",
            "to": target_path, "update_imports_in": importers, "target_must_import": needs,
            "also_move_or_import_from_source_file": same_file_deps,
            "check_cycles": "run dependency_cycles after the move"}
