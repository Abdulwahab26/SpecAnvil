"""Repository documentation: plan, facts, completeness checks and drift.

- `plan`: which documents this repo needs, from its detected archetypes (BE, FE, QA automation,
  Azure Functions, Lambda, infra, library, CLI, data, mobile, worker, microservices).
- `facts_for`: the exact, locally extracted facts a document must be written from.
- `check`: programmatic completeness — required sections, every coverage item mentioned
  (e.g. every endpoint in the API reference), no placeholders, working relative links.
- `register` / `status`: a lock of the facts each document was written from, so a document
  becomes *stale* the moment the code it describes changes.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from importlib import resources
from pathlib import Path

from .config import state_dir
from .facts import Facts, coverage_keys
from .index import CodeIndex

LOCK = "docs-lock.json"
PLACEHOLDER_RX = re.compile(r"\b(TODO|TBD|FIXME|lorem ipsum|XXX)\b|<fill[^>]*>|\[insert[^\]]*\]|\?\?\?", re.I)
LINK_RX = re.compile(r"\]\((?!https?://|mailto:|#)([^)#\s]+)(?:#[^)]*)?\)")


def catalog(root: Path) -> list[dict]:
    base = json.loads(resources.files("specforge").joinpath("assets/docs/catalog.json").read_text(encoding="utf-8"))["docs"]
    custom = root / ".specforge" / "docs-catalog.json"
    if custom.exists():
        try:
            extra = json.loads(custom.read_text(encoding="utf-8")).get("docs", [])
            by_id = {d["id"]: d for d in base}
            for d in extra:
                by_id[d["id"]] = {**by_id.get(d["id"], {}), **d}
            base = list(by_id.values())
        except (json.JSONDecodeError, KeyError):
            pass
    return base


def _lock_path(root: Path) -> Path:
    return state_dir(root) / LOCK


def load_lock(root: Path) -> dict:
    p = _lock_path(root)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _hash(obj) -> str:
    return hashlib.blake2b(json.dumps(obj, sort_keys=True, default=str).encode(), digest_size=10).hexdigest()


class DocEngine:
    def __init__(self, ix: CodeIndex):
        self.ix = ix
        self.root = ix.root
        self.facts = Facts(ix)

    # ------------------------------------------------------------------ plan
    def required(self) -> list[dict]:
        arch = {a["archetype"] for a in self.facts.archetypes()}
        has_tests = bool(self.facts.get("tests")["files"])
        out = []
        for d in catalog(self.root):
            a = d["archetypes"]
            if a == "all" or arch & set(a):
                if d["id"] == "testing-strategy" and not has_tests and "qa-automation" not in arch:
                    pass  # still required: documents that tests are missing is useful
                out.append(d)
        return out

    def facts_for(self, doc_id: str, max_items: int = 120) -> dict:
        d = self._doc(doc_id)
        data = {}
        for f in d["facts"]:
            v = self.facts.get(f) if f != "doc_list" else [
                {"id": x["id"], "title": x["title"], "path": x["path"], "audience": x["audience"]} for x in self.required()]
            data[f] = _trim(v, max_items)
        cov = d.get("coverage")
        must = coverage_keys(cov, self.facts.get(cov)) if cov else []
        return {"doc": {k: d[k] for k in ("id", "title", "path", "purpose", "sections", "audience")},
                "archetypes": [a["archetype"] for a in self.facts.archetypes()],
                "must_mention": must, "facts": data, "facts_hash": self.facts_hash(doc_id)}

    def facts_hash(self, doc_id: str) -> str:
        d = self._doc(doc_id)
        payload = {}
        for f in d["facts"]:
            if f == "doc_list":
                payload[f] = sorted(x["id"] for x in self.required())
                continue
            v = self.facts.get(f)
            payload[f] = _stable(v)
        return _hash(payload)

    def _doc(self, doc_id: str) -> dict:
        for d in catalog(self.root):
            if d["id"] == doc_id:
                return d
        raise KeyError(f"Unknown document '{doc_id}'. Known: {', '.join(x['id'] for x in catalog(self.root))}")

    # ------------------------------------------------------------------ checks
    def check(self, doc_id: str) -> dict:
        d = self._doc(doc_id)
        p = self.root / d["path"]
        if not p.exists():
            return {"id": doc_id, "path": d["path"], "ok": False, "problems": ["missing"]}
        text = p.read_text(encoding="utf-8", errors="replace")
        problems = []
        heads = [h.lower() for h in re.findall(r"^#{1,4}\s+(.+?)\s*#*$", text, re.M)]
        for s in d["sections"]:
            if not any(s.lower() in h for h in heads):
                problems.append(f"missing section: {s}")
        body = re.split(r"^#{1,4}\s+open questions.*$", text, flags=re.M | re.I)[0]
        for m in PLACEHOLDER_RX.finditer(body):
            problems.append(f"placeholder '{m.group(0)}' at line {text.count(chr(10), 0, m.start()) + 1} "
                            "(move unknowns to an 'Open questions' section)")
            if len(problems) > 25:
                break
        cov = d.get("coverage")
        missing_items = []
        if cov:
            for key in coverage_keys(cov, self.facts.get(cov)):
                if key and key not in text:
                    missing_items.append(key)
            if missing_items:
                problems.append(f"{len(missing_items)} {cov} not mentioned: " + ", ".join(missing_items[:12])
                                + (" ..." if len(missing_items) > 12 else ""))
        for m in LINK_RX.finditer(text):
            target = (p.parent / m.group(1)).resolve()
            if not target.exists():
                problems.append(f"broken link: {m.group(1)}")
        words = len(re.findall(r"\w+", text))
        if words < 80:
            problems.append(f"too thin ({words} words)")
        return {"id": doc_id, "path": d["path"], "ok": not problems, "problems": problems,
                "coverage_missing": missing_items}

    # ------------------------------------------------------------------ lock / status
    def register(self, doc_id: str, model: str = "") -> dict:
        res = self.check(doc_id)
        if not res["ok"]:
            return {"registered": False, **res}
        lock = load_lock(self.root)
        lock[doc_id] = {"path": res["path"], "facts_hash": self.facts_hash(doc_id), "model": model,
                        "written_at": time.strftime("%Y-%m-%d %H:%M")}
        p = _lock_path(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        return {"registered": True, "id": doc_id, "path": res["path"]}

    def status(self) -> list[dict]:
        lock = load_lock(self.root)
        out = []
        for d in self.required():
            exists = (self.root / d["path"]).exists()
            entry = lock.get(d["id"])
            if not exists:
                st = "missing"
            elif not entry:
                st = "unverified"   # exists but never checked + registered by SpecForge
            elif entry["facts_hash"] != self.facts_hash(d["id"]):
                st = "stale"
            else:
                st = "fresh"
            out.append({"id": d["id"], "title": d["title"], "path": d["path"], "status": st,
                        "coverage": d.get("coverage"), "archetypes": d["archetypes"]})
        return out


def _trim(v, n):
    if isinstance(v, list):
        return v[:n] + ([{"_truncated": len(v) - n}] if len(v) > n else [])
    if isinstance(v, dict):
        return {k: _trim(x, n) for k, x in v.items()}
    return v


def _stable(v):
    """Hash input without volatile parts (line numbers move on unrelated edits)."""
    if isinstance(v, list):
        return [_stable(x) for x in v]
    if isinstance(v, dict):
        return {k: _stable(x) for k, x in v.items() if k not in ("at", "used_at", "declared_in", "evidence", "used_in")}
    return v
