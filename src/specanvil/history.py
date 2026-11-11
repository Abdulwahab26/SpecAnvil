"""Git-based insights: churn, symbol provenance and decision context, symbol diffs between revisions,
stand-up digest and delivery metrics. Everything runs local `git`; nothing leaves the machine."""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from statistics import median

from .index import TEST_RX, CodeIndex
from .parser import parse_source

DECISION_RX = re.compile(r"\b(because|so that|in order to|decid|trade-?off|instead of|workaround|revert|why|"
                         r"rationale|deprecat|migrat|breaking|security|perf(ormance)?|hotfix)\b", re.I)
TICKET_RX = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b|AB#\d+|(?<![\w/])#\d{1,6}\b")


def _git(ix: CodeIndex, *args: str, timeout: int = 60) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ix.root, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _sym(ix: CodeIndex, ref: str):
    rows = ix.find_symbol(ref)
    if not rows:
        raise ValueError(f"Symbol not found: {ref}")
    return rows[0]


def churn_rate(ix: CodeIndex, target: str, days: int = 90) -> dict:
    """Commits, authors and lines changed for a file, or for one symbol's line range (git log -L)."""
    if "::" in target or not ix.db.execute("SELECT 1 FROM files WHERE path=?", (target,)).fetchone():
        s = _sym(ix, target)
        out = _git(ix, "log", f"--since={days} days ago", "--format=%x1e%H%x1f%an%x1f%ad", "--date=short",
                   "-L", f"{s['start_line']},{s['end_line']}:{s['path']}", timeout=120)
        commits = [c.split("\x1f") for c in out.split("\x1e") if "\x1f" in c]
        authors = Counter(c[1] for c in commits)
        return {"target": f"{s['path']}::{s['qualname']}", "days": days, "commits": len(commits),
                "authors": dict(authors.most_common(5)), "last_change": commits[0][2].split("\n")[0] if commits else None}
    out = _git(ix, "log", f"--since={days} days ago", "--numstat", "--format=%x1e%an%x1f%ad", "--date=short", "--", target)
    commits, added, deleted, authors = 0, 0, 0, Counter()
    for block in out.split("\x1e"):
        if "\x1f" not in block:
            continue
        head, *rest = block.strip().split("\n")
        a, _ = head.split("\x1f")
        commits += 1
        authors[a] += 1
        for ln in rest:
            p = ln.split("\t")
            if len(p) == 3 and p[0].isdigit():
                added += int(p[0])
                deleted += int(p[1])
    return {"target": target, "days": days, "commits": commits, "lines_added": added, "lines_deleted": deleted,
            "authors": dict(authors.most_common(5))}


def provenance(ix: CodeIndex, ref: str, limit: int = 15) -> dict:
    """Who wrote this symbol, when, and why: commit history of its line range, ticket keys, decision-bearing
    commit messages, and ADRs/docs that mention it."""
    s = _sym(ix, ref)
    out = _git(ix, "log", "--format=%x1e%h%x1f%an%x1f%ad%x1f%s%x1f%b", "--date=short", "--no-patch",
               "-L", f"{s['start_line']},{s['end_line']}:{s['path']}", timeout=120)
    commits = []
    for block in out.split("\x1e"):
        parts = block.split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, date, subject, body = parts[0].strip(), parts[1], parts[2], parts[3], parts[4].split("\ndiff --git")[0]
        text = f"{subject}\n{body}"
        commits.append({"sha": sha, "author": author, "date": date, "subject": subject[:160],
                        "tickets": sorted(set(TICKET_RX.findall(text))),
                        "decision": bool(DECISION_RX.search(text)),
                        "why": next((ln.strip() for ln in text.split("\n") if DECISION_RX.search(ln)), "")[:200]})
    authors = Counter(c["author"] for c in commits)
    adrs = []
    for (p,) in ix.db.execute("SELECT path FROM files WHERE path LIKE '%.md' AND (path LIKE '%adr%' OR path LIKE '%decision%' "
                              "OR path LIKE 'docs/%' OR path LIKE 'specs/%')"):
        try:
            text = (ix.root / p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"(?<![\w$])" + re.escape(s["name"]) + r"(?![\w$])", text) or s["path"] in text:
            line = next((i + 1 for i, ln in enumerate(text.split("\n")) if s["name"] in ln or s["path"] in ln), 1)
            adrs.append(f"{p}:{line}")
    return {"symbol": f"{s['path']}::{s['qualname']}", "introduced": commits[-1] if commits else None,
            "last_change": commits[0] if commits else None, "commits": len(commits), "authors": dict(authors.most_common(5)),
            "decisions": [c for c in commits if c["decision"]][:limit], "tickets": sorted({t for c in commits for t in c["tickets"]}),
            "history": commits[:limit], "mentioned_in_docs": adrs[:10]}


def _symbols_of(path: str, text: str) -> dict:
    import hashlib
    fp = parse_source(path, text)
    lines = text.split("\n")
    out = {}
    for s in fp.symbols:
        if s.kind in ("key",):
            continue
        body = "\n".join(lines[s.start_line - 1:s.end_line])
        out[s.qualname] = (hashlib.blake2b(body.encode(), digest_size=8).hexdigest(), s.kind, s.start_line, s.end_line - s.start_line + 1)
    return out


def symbol_diff(ix: CodeIndex, base: str = "HEAD", head: str | None = None, limit: int = 200) -> dict:
    """Symbols added, removed, modified and renamed between two revisions (head=None compares the working tree)."""
    names = _git(ix, "diff", "--name-status", base, *([head] if head else []), "--").splitlines()
    added, removed, modified, renamed = [], [], [], []
    for row in names:
        parts = row.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        old_path = parts[1] if status.startswith("R") else path
        old = _git(ix, "show", f"{base}:{old_path}") if not status.startswith("A") else ""
        if head:
            new = _git(ix, "show", f"{head}:{path}") if not status.startswith("D") else ""
        else:
            try:
                new = (ix.root / path).read_text(encoding="utf-8", errors="replace") if not status.startswith("D") else ""
            except OSError:
                new = ""
        a, b = _symbols_of(old_path, old) if old else {}, _symbols_of(path, new) if new else {}
        gone = {k: v for k, v in a.items() if k not in b}
        new_syms = {k: v for k, v in b.items() if k not in a}
        by_hash = {v[0]: k for k, v in gone.items()}
        for k, v in new_syms.items():
            if v[0] in by_hash:
                renamed.append({"from": f"{old_path}::{by_hash[v[0]]}", "to": f"{path}::{k}"})
                gone.pop(by_hash[v[0]], None)
            else:
                added.append({"symbol": f"{path}::{k}", "kind": v[1], "lines": v[3]})
        removed += [{"symbol": f"{old_path}::{k}", "kind": v[1]} for k, v in gone.items()]
        for k in set(a) & set(b):
            if a[k][0] != b[k][0]:
                modified.append({"symbol": f"{path}::{k}", "kind": b[k][1], "lines": f"{a[k][3]} -> {b[k][3]}"})
    return {"base": base, "head": head or "working tree", "added": added[:limit], "removed": removed[:limit],
            "modified": modified[:limit], "renamed": renamed[:limit],
            "summary": f"+{len(added)} -{len(removed)} ~{len(modified)} renamed {len(renamed)}"}


def digest(ix: CodeIndex, since: str = "1 day ago") -> dict:
    """Stand-up briefing: what changed since `since`, who changed it, which symbols, and what is risky."""
    log = _git(ix, "log", f"--since={since}", "--format=%h%x1f%an%x1f%s")
    commits = [c.split("\x1f") for c in log.splitlines() if "\x1f" in c]
    base = _git(ix, "rev-list", "-1", f"--before={since}", "HEAD").strip()
    diff = symbol_diff(ix, base, None) if base else {"summary": "no earlier commit", "added": [], "modified": [], "removed": []}
    from .analysis import hotspots
    hot = {h["ref"] for h in hotspots(ix, days=90, limit=30)}
    touched_hot = [m["symbol"] for m in diff.get("modified", []) if m["symbol"] in hot]
    tickets = sorted({t for c in commits for t in TICKET_RX.findall(c[2])})
    return {"since": since, "commits": len(commits), "authors": dict(Counter(c[1] for c in commits)),
            "tickets": tickets, "messages": [c[2][:120] for c in commits[:20]], "symbols": diff["summary"],
            "new_symbols": [a["symbol"] for a in diff.get("added", [])][:15],
            "changed_symbols": [m["symbol"] for m in diff.get("modified", [])][:20],
            "hotspots_touched": touched_hot, "tests_changed": [m["symbol"] for m in diff.get("modified", []) + diff.get("added", [])
                                                               if TEST_RX.search(m["symbol"].split("::")[0])][:10]}


def delivery_metrics(ix: CodeIndex, days: int = 30) -> dict:
    """Delivery signals from git: throughput, change size, rework (fix/revert share), merges, busiest files."""
    out = _git(ix, "log", f"--since={days} days ago", "--numstat", "--format=%x1e%h%x1f%an%x1f%at%x1f%s%x1f%P", timeout=120)
    files: Counter
    commits, sizes, files, times, merges = [], [], Counter(), [], 0
    for block in out.split("\x1e"):
        if "\x1f" not in block:
            continue
        head, *rest = block.strip().split("\n")
        sha, author, ts, subject, parents = head.split("\x1f")
        if len(parents.split()) > 1:
            merges += 1
        size = 0
        for ln in rest:
            p = ln.split("\t")
            if len(p) == 3 and p[0].isdigit():
                size += int(p[0]) + int(p[1])
                files[p[2]] += 1
        commits.append((author, subject))
        sizes.append(size)
        times.append(int(ts))
    n = len(commits)
    gaps = [(a - b) / 3600 for a, b in zip(times, times[1:])] if len(times) > 1 else []
    fixes = sum(1 for _, s in commits if re.search(r"\bfix|bug|hotfix|patch\b", s, re.I))
    reverts = sum(1 for _, s in commits if re.search(r"\brevert", s, re.I))
    return {"days": days, "commits": n, "per_week": round(n / max(1, days / 7), 1),
            "active_authors": len({a for a, _ in commits}), "merges": merges,
            "median_change_lines": median(sizes) if sizes else 0, "large_changes_over_500_lines": sum(1 for x in sizes if x > 500),
            "fix_share": round(fixes / n, 2) if n else 0, "revert_share": round(reverts / n, 2) if n else 0,
            "median_hours_between_commits": round(median(gaps), 1) if gaps else None,
            "busiest_files": files.most_common(8)}
