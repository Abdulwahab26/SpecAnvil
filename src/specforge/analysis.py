"""Structural analyses that answer questions grep can't: hotspots and transitive impact."""

from __future__ import annotations

import re
import subprocess
from collections import Counter, deque

from .index import GENERATED_RX, TEST_RX, CodeIndex
from .testing import BRANCH_RX


def churn(ix: CodeIndex, days: int = 180) -> Counter:
    try:
        out = subprocess.run(["git", "log", f"--since={days} days ago", "--numstat", "--format="], cwd=ix.root,
                             capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return Counter()
    c: Counter = Counter()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            c[parts[2]] += int(parts[0]) + int(parts[1])
    return c


def hotspots(ix: CodeIndex, days: int = 180, limit: int = 20) -> list[dict]:
    """Risky code = complexity × recent change. Where bugs and review effort concentrate."""
    ch = churn(ix, days)
    files = [p for p, _ in ch.most_common(200) if ix.db.execute(
        "SELECT 1 FROM files WHERE path=? AND parser!='text'", (p,)).fetchone()
        and not TEST_RX.search(p) and not GENERATED_RX.search(p)]
    out = []
    for p in files:
        try:
            lines = (ix.root / p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for r in ix.db.execute("SELECT id, qualname, start_line, end_line FROM symbols WHERE path=? AND kind IN "
                               "('function','method')", (p,)):
            body = "\n".join(lines[r["start_line"] - 1:r["end_line"]])
            br = len(BRANCH_RX.findall(body))
            if br < 2:
                continue
            out.append({"id": r["id"], "ref": f"{p}::{r['qualname']}", "branches": br,
                        "lines": r["end_line"] - r["start_line"] + 1, "file_churn": ch[p],
                        "score": round((br + 1) * (ch[p] ** 0.5), 1)})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def impact(ix: CodeIndex, path: str, depth: int = 2, limit: int = 60) -> list[dict]:
    """Files that import `path`, then files importing those, up to `depth` hops (breadth-first)."""
    seen = {path: 0}
    q = deque([path])
    while q:
        cur = q.popleft()
        if seen[cur] >= depth:
            continue
        for dep in ix.dependents(cur):
            if dep not in seen:
                seen[dep] = seen[cur] + 1
                q.append(dep)
                if len(seen) > limit:
                    q.clear()
                    break
    return [{"path": p, "hops": h, "test": bool(TEST_RX.search(p))} for p, h in
            sorted(seen.items(), key=lambda kv: (kv[1], kv[0])) if p != path]


def excerpt(body: str, focus: str, context: int = 2) -> tuple[str, bool]:
    """Signature + lines matching any focus term (± context) + closing line; elisions marked. Falls back to
    the full body when nothing matches."""
    terms = [t for t in re.split(r"[\s,|]+", focus.lower()) if len(t) > 1]
    lines = body.splitlines()
    if len(lines) <= 12 or not terms:
        return body, False
    keep = {0, 1, len(lines) - 1}
    hits = [i for i, ln in enumerate(lines) if any(t in ln.lower() for t in terms)]
    if not hits:
        return body, False
    for i in hits:
        keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    out, prev = [], -1
    for i in sorted(keep):
        if i - prev > 1:
            out.append(f"    … ({i - prev - 1} lines)")
        out.append(lines[i])
        prev = i
    return "\n".join(out), True


def skeleton(body: str, max_tokens: int) -> tuple[str, bool]:
    """Structural view of a large symbol: signature + its top-level statements, nested blocks folded.
    Agents see the shape and every branch head, then ask for detail with focus= or full=true."""
    from .tokens import estimate_tokens
    if estimate_tokens(body) <= max_tokens:
        return body, False
    lines = body.splitlines()
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip())
    body_lines = [l for l in lines[1:] if l.strip()]
    if not body_lines:
        return body, False
    base = min(indent(l) for l in body_lines)
    out, folded = [], 0
    for i, ln in enumerate(lines):
        if i == 0 or not ln.strip() or indent(ln) <= base or i == len(lines) - 1:
            if folded:
                out.append(" " * (base + 4) + f"… ({folded} lines)")
                folded = 0
            if ln.strip():
                out.append(ln)
        else:
            folded += 1
    text = "\n".join(out)
    if estimate_tokens(text) > max_tokens:  # still too big: keep the head
        keep = max(10, int(len(out) * max_tokens / max(estimate_tokens(text), 1)))
        text = "\n".join(out[:keep]) + f"\n    … ({len(out) - keep} more top-level lines)"
    return text, True
