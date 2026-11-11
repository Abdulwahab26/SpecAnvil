"""One canonical, validated Markdown handoff for a finished audit / investigation / review.

The agent supplies the sections; the server assembles them in a fixed order, checks that every required section
is present and non-trivial, that no placeholders remain, and that every code reference (`path::Qualname`,
`path:line`) resolves against the index. It refuses to write a handoff with errors unless force=True, and stamps
the commit and index time so a reader knows what state of the code it describes.
"""

from __future__ import annotations

import re
import subprocess
import time

from .index import CodeIndex

KINDS = {
    "audit": ["Summary", "Scope", "Findings", "Evidence", "Recommendations", "Risks", "Next steps"],
    "investigation": ["Summary", "Symptom", "Root cause", "Evidence", "Fix", "Verification", "Next steps"],
    "review": ["Summary", "Changes", "Findings", "Tests", "Verdict"],
    "design": ["Summary", "Context", "Options", "Decision", "Consequences", "Next steps"],
}
PLACEHOLDER_RX = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b|<[A-Za-z _-]{3,30}>|\.\.\.\s*$", re.I | re.M)
SYM_REF_RX = re.compile(r"`?([\w./\-]+\.\w+)::([\w.$<>]+)`?")
LINE_REF_RX = re.compile(r"`([\w./\-]+\.\w+):(\d+)(?:-(\d+))?`")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "handoff"


def _git(ix: CodeIndex, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ix.root, capture_output=True, text=True, timeout=10,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def validate(ix: CodeIndex, kind: str, sections: dict[str, str]) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    norm = {k.strip().lower(): v for k, v in sections.items()}
    errors, warnings = [], []
    for req in KINDS[kind]:
        body = (norm.get(req.lower()) or "").strip()
        if not body:
            errors.append(f"missing section: {req}")
        elif len(body) < 20:
            warnings.append(f"section '{req}' is very short")
    text = "\n".join(sections.values())
    for m in PLACEHOLDER_RX.finditer(text):
        errors.append(f"placeholder left in text: {m.group(0).strip()!r}")
    refs, bad = 0, []
    for path, qual in set(SYM_REF_RX.findall(text)):
        refs += 1
        if not ix.db.execute("SELECT 1 FROM symbols WHERE path=? AND (qualname=? OR name=?)", (path, qual, qual)).fetchone():
            bad.append(f"{path}::{qual}")
    for path, a, b in set(LINE_REF_RX.findall(text)):
        refs += 1
        r = ix.db.execute("SELECT lines FROM files WHERE path=?", (path,)).fetchone()
        if not r or int(b or a) > r[0]:
            bad.append(f"{path}:{a}" + (f"-{b}" if b else ""))
    errors += [f"reference does not resolve in the index: {x}" for x in sorted(bad)]
    if kind in ("audit", "investigation") and refs == 0:
        warnings.append("no code references: cite `path::Symbol` or `path:line` so readers can verify claims")
    extra = [k for k in sections if k.strip().lower() not in {r.lower() for r in KINDS[kind]}]
    return {"errors": errors, "warnings": warnings, "references": refs, "extra_sections": extra}


def finalize(ix: CodeIndex, title: str, sections: dict[str, str], kind: str = "audit", force: bool = False,
             out_dir: str | None = None) -> dict:
    v = validate(ix, kind, sections)
    if v["errors"] and not force:
        return {"written": False, **v, "hint": "Fix the errors (or pass force=True to write anyway, marked as unverified)."}
    norm = {k.strip().lower(): (k, v2) for k, v2 in sections.items()}
    order = KINDS[kind] + [k for k in sections if k.strip().lower() not in {r.lower() for r in KINDS[kind]}]
    commit = _git(ix, "rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(_git(ix, "status", "--porcelain"))
    last = ix.db.execute("SELECT value FROM meta WHERE key='last_index'").fetchone()
    stamp = time.strftime("%Y-%m-%d")
    lines = ["---", f"title: {title}", f"kind: {kind}", f"date: {stamp}", f"commit: {commit}{' (dirty)' if dirty else ''}",
             f"indexed: {time.strftime('%Y-%m-%d %H:%M', time.localtime(float(last[0]))) if last else 'unknown'}",
             f"status: {'unverified' if v['errors'] else 'verified'}", f"references: {v['references']}", "format: specanvil.handoff/v1",
             "---", "", f"# {title}", ""]
    for name in order:
        k = name.strip().lower()
        if k in norm:
            lines += [f"## {name}", "", norm[k][1].strip(), ""]
    if v["errors"]:
        lines += ["## Validation errors (written with force=True)", ""] + [f"- {e}" for e in v["errors"]] + [""]
    from .config import load_config
    d = ix.root / (out_dir or f"{load_config(ix.root).get('specs_dir', 'specs')}/handoffs")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{stamp}-{_slug(title)}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"written": True, "path": str(path.relative_to(ix.root)), **v}
