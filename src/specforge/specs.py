"""Spec files: section-level reads, code references, drift detection against the index."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import load_config, state_dir
from .index import CodeIndex
from .tokens import estimate_tokens

REF_RX = re.compile(r"`([^`\s]+?)::([\w.$<>\-]+)`")
LOCK = "spec-lock.json"


def specs_root(root: Path) -> Path:
    return root / load_config(root).get("specs_dir", "specs")


def list_specs(root: Path) -> list[dict]:
    sr = specs_root(root)
    if not sr.exists():
        return []
    out = []
    for p in sorted(sr.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
        status = re.search(r"(?mi)^status:\s*(\S+)", text)
        out.append({"path": p.relative_to(root).as_posix(), "title": title,
                    "status": status.group(1) if status else "", "tokens": estimate_tokens(text),
                    "refs": len(REF_RX.findall(text))})
    return out


def headings(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if re.match(r"^#{1,4} ", ln)]


def read_section(text: str, section: str) -> str | None:
    lines = text.splitlines()
    target = section.lower().lstrip("# ").strip()
    start, level = None, 0
    for i, ln in enumerate(lines):
        m = re.match(r"^(#{1,6}) (.*)", ln)
        if not m:
            continue
        if start is None and target in m.group(2).lower():
            start, level = i, len(m.group(1))
        elif start is not None and len(m.group(1)) <= level:
            return "\n".join(lines[start:i]).strip()
    return "\n".join(lines[start:]).strip() if start is not None else None


def _lock_path(root: Path) -> Path:
    return state_dir(root) / LOCK


def _load_lock(root: Path) -> dict:
    p = _lock_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve(ix: CodeIndex, path: str, qual: str):
    rows = ix.db.execute("SELECT * FROM symbols WHERE path=? AND (qualname=? OR name=?) LIMIT 1",
                         (path, qual, qual)).fetchall()
    return rows[0] if rows else None


def _drift_specs(root: Path) -> list[dict]:
    """Specs that are checked: everything except templates and the specs README."""
    return [s for s in list_specs(root)
            if "/_template/" not in s["path"] and not s["path"].endswith("/README.md")]


def drift(root: Path, ix: CodeIndex) -> dict:
    lock = _load_lock(root)
    issues, ok = [], 0
    referenced_files: set[str] = set()
    for spec in _drift_specs(root):
        text = (root / spec["path"]).read_text(encoding="utf-8", errors="ignore")
        locked = lock.get(spec["path"], {})
        for path, qual in REF_RX.findall(text):
            ref = f"{path}::{qual}"
            referenced_files.add(path)
            row = _resolve(ix, path, qual)
            if row is None:
                issues.append({"spec": spec["path"], "ref": ref, "issue": "missing",
                               "detail": "symbol no longer exists (renamed, moved or deleted)"})
            elif ref in locked and locked[ref] != row["body_hash"]:
                issues.append({"spec": spec["path"], "ref": ref, "issue": "changed",
                               "detail": f"code changed since the spec was last verified ({path}:{row['start_line']})"})
            elif ref not in locked:
                issues.append({"spec": spec["path"], "ref": ref, "issue": "unverified",
                               "detail": "never verified; run `specforge drift --lock` after review"})
            else:
                ok += 1
    uncovered = []
    for m in ix.modules():
        files = ix.module_files(m["m"])
        if files and not any(f in referenced_files for f in files):
            uncovered.append(m["m"])
    return {"verified_refs": ok, "issues": issues, "modules_without_spec_refs": uncovered,
            "summary": f"{ok} verified, {sum(i['issue'] == 'changed' for i in issues)} changed, "
                       f"{sum(i['issue'] == 'missing' for i in issues)} missing, "
                       f"{sum(i['issue'] == 'unverified' for i in issues)} unverified, "
                       f"{len(uncovered)} modules with no spec references"}


def lock_specs(root: Path, ix: CodeIndex, spec_path: str | None = None) -> int:
    lock = _load_lock(root)
    n = 0
    for spec in _drift_specs(root):
        if spec_path and spec["path"] != spec_path:
            continue
        text = (root / spec["path"]).read_text(encoding="utf-8", errors="ignore")
        entry = {}
        for path, qual in REF_RX.findall(text):
            row = _resolve(ix, path, qual)
            if row is not None:
                entry[f"{path}::{qual}"] = row["body_hash"]
                n += 1
        lock[spec["path"]] = entry
    p = _lock_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return n
