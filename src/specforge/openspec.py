"""OpenSpec mode: work inside an existing OpenSpec project instead of creating a second spec system.

OpenSpec (openspec.dev) keeps living specs in `openspec/specs/<capability>/spec.md` and proposed changes in
`openspec/changes/<change>/` (proposal.md, design.md, tasks.md, delta specs with ADDED / MODIFIED / REMOVED /
RENAMED Requirements), archiving them under `openspec/changes/archive/`. Its own workflows create, apply and
archive changes. SpecForge adds what that workflow doesn't have, reading and writing OpenSpec's own files:

  status      capabilities, requirements, scenarios, active changes with task progress
  check       structure problems (no CLI needed) plus code references that no longer resolve
  coverage    which scenarios have a test (explicit title match, likely match, or none)
  gaps        code modules no capability spec mentions (candidates for a baseline spec)

Everything is read from disk and the local index; nothing here calls the OpenSpec CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

from .index import STOPWORDS, TEST_RX, CodeIndex, split_terms, stem

REQ_RX = re.compile(r"^### Requirement:\s*(.+?)\s*$")
SCN_RX = re.compile(r"^#### Scenario:\s*(.+?)\s*$")
OP_RX = re.compile(r"^## (ADDED|MODIFIED|REMOVED|RENAMED) Requirements\s*$")
H2_RX = re.compile(r"^## (.+?)\s*$")
REF_RX = re.compile(r"`([\w./\-]+\.\w+)::([\w.$<>]+)`")
TASK_RX = re.compile(r"^\s*- \[([ xX])\]\s+(.*)$")
# test-case titles across common frameworks: it("..."), test('...'), t.Run("..."), def test_x, func TestX, @DisplayName
TEST_TITLE_RXS = [
    re.compile(r"""\b(?:it|test|describe|scenario|specify|context)\s*(?:\.\w+)*\s*\(\s*(['"`])(.+?)\1"""),
    re.compile(r"""\b(?:it|test|describe|scenario|specify|context)\s+(['"])(.+?)\1\s+do\b"""),
    re.compile(r"""\bt\.Run\(\s*(")(.+?)\1"""),
    re.compile(r"""@DisplayName\(\s*(")(.+?)\1"""),
    re.compile(r"""^\s*(?:async\s+)?def\s+()(test_\w+)"""),
    re.compile(r"""^\s*func\s+()(Test\w+)\s*\("""),
    re.compile(r"""^\s*(?:public\s+)?(?:async\s+)?(?:void|Task|fun)\s+()(\w*(?:[Tt]est|[Ss]hould)\w*)\s*\("""),
]


def find(root: Path) -> Path | None:
    d = root / "openspec"
    return d if d.is_dir() and ((d / "specs").is_dir() or (d / "changes").is_dir()) else None


# ---------------------------------------------------------------------------- parsing

def parse_spec(text: str) -> dict:
    """A capability spec or one delta section: requirements with their text and scenarios."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = {"purpose": "", "requirements": [], "title_before_ops": False}
    req = scn = None
    section = ""
    fence = False
    for i, ln in enumerate(lines, 1):
        if ln.strip().startswith("```"):
            fence = not fence
        if fence:
            continue
        m = REQ_RX.match(ln)
        if m:
            req = {"name": m.group(1), "line": i, "text": "", "scenarios": [], "op": section}
            out["requirements"].append(req)
            scn = None
            continue
        m = SCN_RX.match(ln)
        if m and req is not None:
            scn = {"name": m.group(1), "line": i, "steps": []}
            req["scenarios"].append(scn)
            continue
        m = H2_RX.match(ln)
        if m:
            op = OP_RX.match(ln)
            section = op.group(1) if op else m.group(1)
            req = scn = None
            continue
        if ln.startswith("# ") and not out["requirements"]:
            continue
        if scn is not None:
            if ln.strip():
                scn["steps"].append(ln.strip())
        elif req is not None:
            if ln.strip():
                req["text"] += ln.strip() + " "
        elif section == "Purpose" and ln.strip():
            out["purpose"] += ln.strip() + " "
    return out


def parse_delta(text: str) -> dict:
    spec = parse_spec(text)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delta = {"ADDED": [], "MODIFIED": [], "REMOVED": [], "RENAMED": [],
             "title_before_ops": first.startswith("# ")}
    for r in spec["requirements"]:
        if r["op"] in delta:
            delta[r["op"]].append(r)
    for m in re.finditer(r"FROM:\s*`?### Requirement:\s*(.+?)`?\s*\n\s*-?\s*TO:\s*`?### Requirement:\s*(.+?)`?\s*$", text, re.M):
        delta["RENAMED"].append({"name": m.group(2), "from": m.group(1), "line": text.count("\n", 0, m.start()) + 1,
                                 "text": "renamed", "scenarios": [], "op": "RENAMED"})
    return delta


def capabilities(root: Path) -> dict[str, dict]:
    os_dir = find(root)
    out = {}
    if os_dir and (os_dir / "specs").is_dir():
        for p in sorted((os_dir / "specs").rglob("spec.md")):
            name = p.parent.relative_to(os_dir / "specs").as_posix()
            out[name] = {**parse_spec(p.read_text(encoding="utf-8", errors="replace")), "path": p.relative_to(root).as_posix()}
    return out


def changes(root: Path, archived: bool = False) -> list[dict]:
    os_dir = find(root)
    base = (os_dir / "changes") if os_dir else None
    if not base or not base.is_dir():
        return []
    dirs = sorted((base / "archive").iterdir()) if archived and (base / "archive").is_dir() else \
        sorted(d for d in base.iterdir() if d.is_dir() and d.name != "archive")
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        tasks = (d / "tasks.md").read_text(encoding="utf-8", errors="replace") if (d / "tasks.md").exists() else ""
        marks = [m.group(1) for m in map(TASK_RX.match, tasks.splitlines()) if m]
        deltas = {}
        for p in sorted((d / "specs").rglob("spec.md")) if (d / "specs").is_dir() else []:
            deltas[p.parent.relative_to(d / "specs").as_posix()] = {
                **parse_delta(p.read_text(encoding="utf-8", errors="replace")), "path": p.relative_to(root).as_posix()}
        out.append({"id": d.name, "path": d.relative_to(root).as_posix(), "has_proposal": (d / "proposal.md").exists(),
                    "has_design": (d / "design.md").exists(), "tasks_done": sum(1 for x in marks if x.lower() == "x"),
                    "tasks_total": len(marks), "deltas": deltas})
    return out


def status(root: Path) -> dict:
    if not find(root):
        raise ValueError("No openspec/ folder here. Run `openspec init` first, or use SpecForge's own specs.")
    caps = capabilities(root)
    reqs = sum(len(c["requirements"]) for c in caps.values())
    scns = sum(len(r["scenarios"]) for c in caps.values() for r in c["requirements"])
    active = changes(root)
    arch = find(root) / "changes" / "archive"
    return {"capabilities": len(caps), "requirements": reqs, "scenarios": scns,
            "archived_changes": sum(1 for d in arch.iterdir() if d.is_dir()) if arch.is_dir() else 0,
            "active_changes": [{"id": c["id"], "tasks": f"{c['tasks_done']}/{c['tasks_total']}",
                                "capabilities": sorted(c["deltas"]),
                                "delta": {op: sum(len(d[op]) for d in c["deltas"].values())
                                          for op in ("ADDED", "MODIFIED", "REMOVED", "RENAMED")},
                                "missing": [n for n, ok in (("proposal.md", c["has_proposal"]), ("tasks.md", c["tasks_total"] > 0))
                                            if not ok]} for c in active]}


# ---------------------------------------------------------------------------- check

def _refs(ix: CodeIndex, rel: str, text: str) -> list[dict]:
    out = []
    for path, qual in set(REF_RX.findall(text)):
        if not ix.db.execute("SELECT 1 FROM symbols WHERE path=? AND (qualname=? OR name=?) LIMIT 1", (path, qual, qual)).fetchone():
            out.append({"level": "error", "file": rel, "issue": f"code reference `{path}::{qual}` does not resolve",
                        "fix": "the symbol was renamed, moved or deleted; update the reference or the requirement"})
    return out


def check(root: Path, ix: CodeIndex, target: str | None = None) -> dict:
    """Structure and reference problems in capability specs and active changes (optionally one of them)."""
    if not find(root):
        raise ValueError("No openspec/ folder here.")
    caps = capabilities(root)
    issues: list[dict] = []

    def req_issues(rel: str, reqs: list[dict], delta: bool = False) -> None:
        seen: set[str] = set()
        for r in reqs:
            where = f"{rel}:{r['line']}"
            if r["name"] in seen:
                issues.append({"level": "error", "file": where, "issue": f"duplicate requirement '{r['name']}'",
                               "fix": "merge them or rename one"})
            seen.add(r["name"])
            if r.get("op") in ("REMOVED", "RENAMED"):
                continue
            if not r["text"].strip():
                issues.append({"level": "error", "file": where, "issue": f"requirement '{r['name']}' has no text before its scenarios",
                               "fix": "add a sentence starting 'The system SHALL ...'"})
            elif not re.search(r"\b(SHALL|MUST)\b", r["text"] + r["name"]):
                issues.append({"level": "warning", "file": where, "issue": f"requirement '{r['name']}' has no SHALL / MUST",
                               "fix": "state the normative behaviour with SHALL or MUST"})
            if not r["scenarios"]:
                issues.append({"level": "error", "file": where, "issue": f"requirement '{r['name']}' has no scenario",
                               "fix": "add at least one '#### Scenario:' with WHEN / THEN"})
            for s in r["scenarios"]:
                steps = " ".join(s["steps"]).upper()
                if "WHEN" not in steps or "THEN" not in steps:
                    issues.append({"level": "warning", "file": f"{rel}:{s['line']}",
                                   "issue": f"scenario '{s['name']}' lacks WHEN and/or THEN", "fix": "use WHEN ... THEN ... bullets"})

    for name, c in caps.items():
        if target and target != name:
            continue
        text = (root / c["path"]).read_text(encoding="utf-8", errors="replace")
        if not c["purpose"].strip():
            issues.append({"level": "warning", "file": c["path"], "issue": "no ## Purpose text", "fix": "say what the capability is for"})
        req_issues(c["path"], c["requirements"])
        issues += _refs(ix, c["path"], text)
    for ch in changes(root):
        if target and target != ch["id"]:
            continue
        if not ch["has_proposal"]:
            issues.append({"level": "error", "file": ch["path"], "issue": "no proposal.md", "fix": "add the why and what"})
        else:
            prop = (root / ch["path"] / "proposal.md").read_text(encoding="utf-8", errors="replace")
            for sec in ("Why", "What Changes"):
                if not re.search(rf"^##\s+{sec}\b", prop, re.M | re.I):
                    issues.append({"level": "warning", "file": f"{ch['path']}/proposal.md", "issue": f"no '## {sec}' section",
                                   "fix": f"add '## {sec}'"})
            issues += _refs(ix, f"{ch['path']}/proposal.md", prop)
        if not ch["deltas"]:
            issues.append({"level": "error", "file": ch["path"], "issue": "no delta specs",
                           "fix": "add specs/<capability>/spec.md with '## ADDED|MODIFIED|REMOVED Requirements'"})
        for cap, d in ch["deltas"].items():
            base = {r["name"] for r in caps.get(cap, {}).get("requirements", [])}
            if d["title_before_ops"]:
                issues.append({"level": "error", "file": d["path"], "issue": "delta starts with a title before the operation headers",
                               "fix": "start the file with '## ADDED Requirements' (or MODIFIED / REMOVED)"})
            if not any(d[op] for op in ("ADDED", "MODIFIED", "REMOVED", "RENAMED")):
                issues.append({"level": "error", "file": d["path"], "issue": "no requirements under an operation header",
                               "fix": "put '### Requirement:' blocks under '## ADDED Requirements' etc."})
            req_issues(d["path"], d["ADDED"] + d["MODIFIED"], delta=True)
            for r in d["ADDED"]:
                if r["name"] in base:
                    issues.append({"level": "error", "file": f"{d['path']}:{r['line']}",
                                   "issue": f"ADDED requirement '{r['name']}' already exists in {cap}", "fix": "move it under MODIFIED"})
            for op in ("MODIFIED", "REMOVED"):
                for r in d[op]:
                    if cap in caps and r["name"] not in base:
                        issues.append({"level": "error", "file": f"{d['path']}:{r['line']}",
                                       "issue": f"{op} requirement '{r['name']}' is not in {cap}",
                                       "fix": "match the existing requirement name exactly, or move it under ADDED"})
            for r in d["RENAMED"]:
                if cap in caps and r.get("from") not in base:
                    issues.append({"level": "error", "file": f"{d['path']}:{r['line']}",
                                   "issue": f"RENAMED source '{r.get('from')}' is not in {cap}", "fix": "use the current requirement name"})
            issues += _refs(ix, d["path"], (root / d["path"]).read_text(encoding="utf-8", errors="replace"))
    issues.sort(key=lambda x: (x["level"] != "error", x["file"]))
    return {"errors": sum(1 for i in issues if i["level"] == "error"),
            "warnings": sum(1 for i in issues if i["level"] == "warning"), "issues": issues[:200],
            "checked": {"capabilities": len([c for c in caps if not target or c == target]),
                        "changes": len([c for c in changes(root) if not target or c["id"] == target])}}


# ---------------------------------------------------------------------------- scenario -> test coverage

def _terms(text: str) -> set[str]:
    return {stem(t) for t in split_terms(text).split() if t not in STOPWORDS and len(t) > 2}


def test_titles(ix: CodeIndex) -> list[dict]:
    """Test-case titles read from test files (it/test/describe strings, t.Run names, test function names)."""
    out = []
    for (path,) in ix.db.execute("SELECT path FROM files"):
        if not TEST_RX.search(path):
            continue
        try:
            lines = (ix.root / path).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            if len(ln) > 400:
                continue
            for rx in TEST_TITLE_RXS:
                m = rx.search(ln)
                if m:
                    title = m.group(2)
                    out.append({"file": path, "line": i, "title": title, "terms": _terms(title)})
                    break
    return out


def coverage(root: Path, ix: CodeIndex, target: str | None = None, limit: int = 400) -> dict:
    """For each scenario: explicit (a test title contains the scenario name), likely (most of its words appear in
    one test title), or none. `target` is a capability name or an active change id; default: every capability."""
    if not find(root):
        raise ValueError("No openspec/ folder here.")
    groups: list[tuple[str, str, list[dict]]] = []
    caps = capabilities(root)
    if target and target in caps:
        groups.append((target, caps[target]["path"], caps[target]["requirements"]))
    elif target:
        ch = next((c for c in changes(root) if c["id"] == target), None)
        if not ch:
            raise ValueError(f"'{target}' is not a capability or an active change. Capabilities: {', '.join(list(caps)[:12])} ...")
        for cap, d in ch["deltas"].items():
            groups.append((f"{target}/{cap}", d["path"], d["ADDED"] + d["MODIFIED"]))
    else:
        groups = [(n, c["path"], c["requirements"]) for n, c in caps.items()]
    tests = test_titles(ix)
    norm = [(re.sub(r"[^a-z0-9]+", " ", t["title"].lower()).strip(), t) for t in tests]
    rows, counts = [], {"explicit": 0, "likely": 0, "none": 0}
    by_group: dict[str, dict] = {}
    for gname, gpath, reqs in groups:
        g = by_group.setdefault(gname, {"explicit": 0, "likely": 0, "none": 0})
        for r in reqs:
            for s in r["scenarios"]:
                key = re.sub(r"[^a-z0-9]+", " ", s["name"].lower()).strip()
                st = _terms(s["name"])
                hit, kind = None, "none"
                if len(key) >= 8:
                    hit = next((t for n, t in norm if key in n), None)
                    kind = "explicit" if hit else "none"
                if not hit and len(st) >= 2:
                    best = max(tests, key=lambda t: len(st & t["terms"]) / len(st), default=None)
                    if best and len(st & best["terms"]) >= 2 and len(st & best["terms"]) / len(st) >= 0.7:
                        hit, kind = best, "likely"
                counts[kind] += 1
                g[kind] += 1
                if len(rows) < limit and (kind != "explicit" or target):
                    rows.append({"spec": gname, "requirement": r["name"], "scenario": s["name"], "at": f"{gpath}:{s['line']}",
                                 "coverage": kind, "test": f"{hit['file']}:{hit['line']} “{hit['title'][:80]}”" if hit else None})
    total = sum(counts.values())
    worst = sorted(((n, v) for n, v in by_group.items() if sum(v.values())),
                   key=lambda x: -(x[1]["none"] / sum(x[1].values())))[:10]
    return {"scenarios": total, **counts, "covered_pct": round(100 * (counts["explicit"] + counts["likely"]) / total, 1) if total else 0,
            "test_titles_scanned": len(tests),
            "least_covered": [{"spec": n, "none": v["none"], "of": sum(v.values())} for n, v in worst],
            "rows": sorted(rows, key=lambda x: {"none": 0, "likely": 1, "explicit": 2}[x["coverage"]]),
            "note": "Matching is by test titles: 'explicit' means a test title contains the scenario name; 'likely' means "
                    "at least 70% of the scenario's words appear in one title. Name tests after scenarios to make it exact."}


# ---------------------------------------------------------------------------- gaps

def gaps(root: Path, ix: CodeIndex, limit: int = 30) -> dict:
    """Code modules that no capability spec mentions by name, path or main symbols: candidates for a baseline spec."""
    if not find(root):
        raise ValueError("No openspec/ folder here.")
    caps = capabilities(root)
    corpus = " ".join((root / c["path"]).read_text(encoding="utf-8", errors="replace") for c in caps.values()).lower()
    corpus_terms = _terms(corpus)
    out = []
    for m in ix.modules():
        mod = m["m"]
        files = [r[0] for r in ix.db.execute("SELECT path FROM files WHERE module=? AND parser!='text'", (mod,))]
        files = [f for f in files if not TEST_RX.search(f)]
        if not files:
            continue
        mt = _terms(mod)
        named = mod.lower() in corpus or any(f.lower() in corpus for f in files) or (mt and mt <= corpus_terms)
        syms = [r[0] for r in ix.db.execute(
            "SELECT name FROM symbols WHERE path IN (%s) AND kind IN ('class','function','interface','struct') "
            "AND exported=1 LIMIT 40" % ",".join("?" * len(files[:200])), files[:200])]
        mentioned = [s for s in syms if len(s) > 4 and s.lower() in corpus]
        if not named and len(mentioned) <= max(1, len(syms) // 10):
            out.append({"module": mod, "files": len(files), "lines": m.get("lines"), "exported_symbols": len(syms),
                        "examples": syms[:6]})
    out.sort(key=lambda x: -(x["lines"] or 0))
    return {"capabilities": len(caps), "unspecified_modules": out[:limit], "count": len(out),
            "next": "Draft a baseline spec for the biggest ones in openspec/specs/<capability>/spec.md "
                    "(## Purpose, ## Requirements, ### Requirement:, #### Scenario:) from the code's current behaviour."}
