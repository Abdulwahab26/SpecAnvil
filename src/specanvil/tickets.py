"""Local ticket archaeology for backfilling specs from ticket history.

The ticket *content* comes from the user's ticket MCP (Jira, Azure DevOps, GitHub, Linear, GitLab)
inside their Claude session. This module adds what only the repo knows: which commits mention a
ticket, which files and symbols they touched, and resumable progress for long backfills.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from .config import state_dir
from .index import CodeIndex

KEY_RX = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b|\bAB#(\d+)\b|(?<![\w/])#(\d{1,6})\b")
HUNK_RX = re.compile(r"^@@ [^@]+ @@\s*(.*)$")
IDENT_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STATE_FILE = "backfill-state.json"


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout,
                              errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def keys_in_history(root: Path, limit: int = 2000) -> list[dict]:
    """Ticket keys mentioned in commit subjects/bodies and branch names, with counts and dates."""
    log = _git(root, "log", "--all", f"-{limit}", "--format=%x1e%ad%x1f%s%n%b", "--date=short")
    stats: dict[str, dict] = {}
    for entry in log.split("\x1e"):
        if "\x1f" not in entry:
            continue
        date, msg = entry.split("\x1f", 1)
        for m in KEY_RX.finditer(msg):
            key = m.group(1) or (f"AB#{m.group(2)}" if m.group(2) else f"#{m.group(3)}")
            s = stats.setdefault(key, {"key": key, "commits": 0, "first": date, "last": date})
            s["commits"] += 1
            s["first"] = min(s["first"], date)
            s["last"] = max(s["last"], date)
    return sorted(stats.values(), key=lambda s: (-s["commits"], s["key"]))


def trace(root: Path, ix: CodeIndex, key: str, max_commits: int = 30) -> dict:
    """Commits that mention `key`, the files they changed, and the current symbols they most likely touched."""
    pattern = re.escape(key) + (r"\b" if key[-1].isdigit() else "")
    log = _git(root, "log", "--all", "-E", "-i", f"--grep={pattern}", f"-{max_commits}",
               "--format=%H%x1f%ad%x1f%an%x1f%s", "--date=short")
    files: dict
    commits, files, symbols = [], defaultdict(int), defaultdict(set)
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, date, author, subject = parts
        commits.append({"sha": sha[:10], "date": date, "author": author, "subject": subject[:160]})
        show = _git(root, "show", "--format=", "-U0", "--no-color", sha, timeout=30)
        current = None
        for ln in show.splitlines():
            if ln.startswith("+++ "):
                current = ln[6:] if ln.startswith("+++ b/") else None
                if current:
                    files[current] += 1
            elif current and ln.startswith("@@"):
                m = HUNK_RX.match(ln)
                if m and m.group(1):  # git's hunk header names the enclosing function/class
                    for ident in IDENT_RX.findall(m.group(1)):
                        symbols[current].add(ident)
            elif current and ln[:1] in "+-" and not ln.startswith(("+++", "---")):
                for ident in IDENT_RX.findall(ln)[:6]:
                    symbols[current].add(ident)
    code_refs = []
    for path, idents in symbols.items():
        if not idents:
            continue
        q = ",".join("?" * len(idents))
        for r in ix.db.execute(f"SELECT path, qualname FROM symbols WHERE path=? AND name IN ({q}) "
                               "ORDER BY start_line LIMIT 12", (path, *idents)):
            code_refs.append(f"{r['path']}::{r['qualname']}")
    existing = {r[0] for r in ix.db.execute("SELECT path FROM files")}
    return {"key": key, "commits": commits,
            "files": [{"path": p, "commits": n, "exists_now": p in existing}
                      for p, n in sorted(files.items(), key=lambda kv: -kv[1])][:40],
            "code_refs": list(dict.fromkeys(code_refs))[:40],
            "note": "code_refs are inferred from commit diffs against the current index; verify before relying on them"}


# ----------------------------------------------------------------------------- resumable state

def _state_path(root: Path) -> Path:
    return state_dir(root) / STATE_FILE


def load_state(root: Path) -> dict:
    p = _state_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"started": None, "query": None, "done": [], "failed": {}, "skipped": []}


def update_state(root: Path, query: str | None = None, done: list[str] | None = None,
                 failed: dict[str, str] | None = None, skipped: list[str] | None = None, reset: bool = False) -> dict:
    st = {"started": None, "query": None, "done": [], "failed": {}, "skipped": []} if reset else load_state(root)
    if st["started"] is None:
        st["started"] = time.strftime("%Y-%m-%d %H:%M")
    if query:
        st["query"] = query
    for k in done or []:
        if k not in st["done"]:
            st["done"].append(k)
        st["failed"].pop(k, None)
    st["failed"].update(failed or {})
    for k in skipped or []:
        if k not in st["skipped"]:
            st["skipped"].append(k)
    st["updated"] = time.strftime("%Y-%m-%d %H:%M")
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
    return st


def existing_feature_keys(root: Path, specs_dir: str = "specs") -> set[str]:
    from .config import features_dir, load_config
    cfg = load_config(root)
    d = root / (features_dir(cfg) if cfg.get("specs_dir", "specs") == specs_dir else f"{specs_dir}/features")
    if not d.exists():
        return set()
    out = set()
    for p in d.iterdir():
        m = KEY_RX.match(p.name)
        if p.is_dir() and m:
            out.add(m.group(0))
    return out
