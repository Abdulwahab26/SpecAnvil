"""Change safety, file tree, workspaces, cross-repo maps and contracts, file risk.

Part of the insights toolkit; import these through `specanvil.insights`."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .graph import CodeGraph
from .index import TEST_RX, CodeIndex
from .insights import (
    ENTRY_DECOR_RX,
    Files,
    _prod,
    _sym,
    _test_text,
    complexity_of,
)

# ----------------------------------------------------------------------------- change safety

def check_safety(ix: CodeIndex, ref: str, action: str = "edit", new_name: str | None = None) -> dict:
    """Preflight for edit / delete / rename: callers, importers, tests, overrides, exposure, collisions."""
    g = CodeGraph(ix)
    s = _sym(ix, ref)
    sid = s["id"]
    f = Files(ix)
    callers = g.callers(sid)
    importers = sorted(g.rdeps.get(s["path"], set()))
    using_files = sorted({p for p in importers if re.search(r"(?<![\w$])" + re.escape(s["name"]) + r"(?![\w$])",
                                                            "\n".join(f.lines(p)))})
    tests = [p for p in using_files if TEST_RX.search(p)] + \
        sorted({g.sym[c]["path"] for c, _ in callers if TEST_RX.search(g.sym[c]["path"])})
    prev = f.lines(s["path"])[max(0, s["start_line"] - 4):s["start_line"] - 1]
    registered = any(ENTRY_DECOR_RX.match(x) or re.match(r"^\s*(@[\w.]+|\[[A-Z])", x) for x in prev)
    overrides = [f"{r['path']}::{r['qualname']}" for r in ix.db.execute(
        "SELECT path, qualname FROM symbols WHERE name=? AND id!=? AND kind=? AND parent!=''",
        (s["name"], sid, s["kind"]))] if s["parent"] else []
    reasons, level = [], 0
    if action == "delete":
        if callers:
            reasons.append(f"{len(callers)} callers would break")
            level = max(level, 2)
        if using_files:
            reasons.append(f"referenced in {len(using_files)} importing files")
            level = max(level, 2)
        if registered:
            reasons.append("registered with a framework (route/handler/job/DI); may be invoked without a direct call")
            level = max(level, 2)
        if s["exported"]:
            reasons.append("exported: external code may depend on it")
            level = max(level, 1)
    elif action == "rename":
        if not new_name:
            raise ValueError("rename needs new_name")
        clash = [f"{r['path']}:{r['start_line']} {r['qualname']}" for r in ix.db.execute(
            "SELECT path, start_line, qualname FROM symbols WHERE name=? AND (path=? OR parent=?)",
            (new_name, s["path"], s["parent"] or "\0"))]
        if clash:
            reasons.append(f"name collision with {clash[:3]}")
            level = max(level, 2)
        same_name = ix.db.execute("SELECT COUNT(*) FROM symbols WHERE name=?", (s["name"],)).fetchone()[0]
        if same_name > 1:
            reasons.append(f"{same_name - 1} other symbols share the old name: review plan_refactor's needs_review list")
            level = max(level, 1)
        if registered or s["exported"]:
            reasons.append("public/registered: external references or config may use the name")
            level = max(level, 1)
    else:  # edit
        if len(callers) >= 10:
            reasons.append(f"high fan-in: {len(callers)} callers")
            level = max(level, 1)
        if not tests:
            reasons.append("no tests exercise it: add one before changing behaviour")
            level = max(level, 1)
        if overrides:
            reasons.append(f"{len(overrides)} overrides/implementations share the contract")
            level = max(level, 1)
    verdict = ["safe", "caution", "unsafe"][level]
    return {"symbol": f"{s['path']}::{s['qualname']}", "action": action, "verdict": verdict, "reasons": reasons,
            "callers": [f"{g.sym[c]['qualname']} ({g.sym[c]['path']}:{ln})" for c, ln in callers[:20]],
            "importing_files": using_files[:20], "tests": sorted(set(tests))[:10], "overrides": overrides[:10],
            "registered": registered}


# ----------------------------------------------------------------------------- tree, workspaces, cross-repo

def file_tree(ix: CodeIndex, prefix: str = "", depth: int = 3) -> str:
    rows = ix.db.execute("SELECT f.path, f.lines, (SELECT COUNT(*) FROM symbols s WHERE s.path=f.path) n FROM files f "
                         "WHERE f.path LIKE ? ORDER BY f.path", (prefix.rstrip("/") + "%",)).fetchall()
    dirs: dict[str, list] = defaultdict(lambda: [0, 0, 0])
    lines_out = []
    for r in rows:
        rel = r["path"][len(prefix.rstrip("/")) + 1:] if prefix else r["path"]
        parts = rel.split("/")
        for d in range(1, min(len(parts), depth + 1)):
            key = "/".join(parts[:d])
            dirs[key][0] += 1
            dirs[key][1] += r["lines"] or 0
            dirs[key][2] += r["n"]
        if len(parts) <= depth:
            lines_out.append(("/".join(parts), f"{r['lines']} lines, {r['n']} symbols"))
    for k, (nf, nl, ns) in dirs.items():
        lines_out.append((k + "/", f"{nf} files, {nl:,} lines, {ns} symbols"))
    lines_out.sort()
    return "\n".join(f"{'  ' * k.rstrip('/').count('/')}{PurePosixPath(k.rstrip('/')).name}{'/' if k.endswith('/') else ''}  ({v})"
                     for k, v in lines_out[:600])


def list_workspaces(ix: CodeIndex) -> dict:
    from .detect import detect_stack
    root = ix.root
    st = detect_stack(root, ix)
    declared: list = []
    try:
        pj = json.loads((root / "package.json").read_text(encoding="utf-8"))
        ws = pj.get("workspaces")
        declared += (ws.get("packages", []) if isinstance(ws, dict) else ws or [])
    except (OSError, json.JSONDecodeError):
        pass
    for fname, rx in (("pnpm-workspace.yaml", r"^\s*-\s*['\"]?([^'\"\n]+)"), ("go.work", r"^\s*use\s+(\S+)|^\s+(\.\S*)"),
                      ("Cargo.toml", r"members\s*=\s*\[([^\]]*)\]")):
        p = root / fname
        if p.exists():
            for m in re.finditer(rx, p.read_text(encoding="utf-8", errors="ignore"), re.M):
                declared += [x.strip(" '\"") for x in (m.group(1) or m.group(m.lastindex or 0) or "").split(",") if x.strip()]
    sln = [str(p.relative_to(root)) for p in root.glob("*.sln")]
    return {"declared_workspaces": declared, "solutions": sln, "projects": st.get("projects", [])}


def _package_name(root) -> list[str]:
    names = []
    for fname, rx in (("package.json", r'"name"\s*:\s*"([^"]+)"'), ("pyproject.toml", r'(?m)^name\s*=\s*"([^"]+)"'),
                      ("go.mod", r"(?m)^module\s+(\S+)"), ("Cargo.toml", r'(?m)^name\s*=\s*"([^"]+)"'),
                      ("setup.cfg", r"(?m)^name\s*=\s*(\S+)")):
        p = root / fname
        if p.exists():
            m = re.search(rx, p.read_text(encoding="utf-8", errors="ignore"))
            if m:
                names.append(m.group(1))
    return names


def cross_repo_map(ix: CodeIndex) -> dict:
    """Package-level map across this repo and every registered source: who publishes what, who imports whom."""
    from . import sources as _src
    repos = {"(this repo)": ix}
    for name in _src.registry():
        try:
            repos[name] = _src.open_source(name)
        except ValueError:
            continue
    published = {k: _package_name(v.root) for k, v in repos.items()}
    edges = []
    for a, ia in repos.items():
        text = "\n".join(r[0] for r in ia.db.execute("SELECT source FROM imports"))
        for b, names in published.items():
            if a == b:
                continue
            for n in names:
                short = n.split("/")[-1]
                if re.search(r"""['"\s]""" + re.escape(n) + r"""[/'"\s]|^(?:from|import)\s+""" + re.escape(short.replace("-", "_")) + r"\b",
                             text, re.M):
                    edges.append({"from": a, "to": b, "via": n})
    return {"repos": {k: {"root": str(v.root), "publishes": published[k]} for k, v in repos.items()}, "edges": edges}


JS_NAMED_RX = re.compile(r"""import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?(?:\*\s+as\s+(\w+))?\s*from\s*['"]([^'"]+)['"]""")
JS_REQ_RX = re.compile(r"""(?:const|let|var)\s+(?:\{([^}]*)\}|(\w+))\s*=\s*require\(\s*['"]([^'"]+)['"]\s*\)""")
PY_FROM_NAMES_RX = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+\(?([^)#]+)\)?", re.M)


def _imported_names(stmt: str) -> list[tuple[str, list[str]]]:
    """(module, [names]) pairs from one import statement (JS/TS ESM + CommonJS, Python)."""
    out = []
    for m in JS_NAMED_RX.finditer(stmt):
        default, named, star, mod = m.groups()
        names = [x.split(" as ")[0].strip() for x in (named or "").split(",") if x.strip()]
        if default:
            names.append("default")
        out.append((mod, names or ["*"]))
    for m in JS_REQ_RX.finditer(stmt):
        named, whole, mod = m.groups()
        out.append((mod, [x.split(":")[0].strip() for x in named.split(",") if x.strip()] if named else ["*"]))
    for m in PY_FROM_NAMES_RX.finditer(stmt):
        out.append((m.group(1), [x.split(" as ")[0].strip() for x in m.group(2).split(",") if x.strip()]))
    return out


def group_contracts(ix: CodeIndex, members: list[str] | None = None, limit: int = 60) -> dict:
    """De-facto API contracts across a group of repos: which symbols one repo publishes that others import, who
    consumes each, and the provider-side signature. That set is the breaking-change surface between them."""
    from . import sources as _src
    repos = {"(this repo)": ix}
    for name in _src.registry():
        if members and name not in members:
            continue
        try:
            repos[name] = _src.open_source(name)
        except ValueError:
            continue
    published = {k: _package_name(v.root) for k, v in repos.items()}
    contracts: dict[tuple, dict] = {}
    unresolved: Counter = Counter()
    for a, ia in repos.items():
        for path, stmt in ia.db.execute("SELECT path, source FROM imports"):
            for mod, names in _imported_names(stmt):
                for b, pkgs in published.items():
                    if b == a or not any(mod == n or mod.startswith(n + "/") or mod.startswith(n.replace("-", "_") + ".")
                                         or mod == n.replace("-", "_") for n in pkgs):
                        continue
                    ib = repos[b]
                    for nm in names:
                        if nm in ("*", "default"):
                            key = (b, f"{mod} ({nm})", "", "module")
                        else:
                            mods = sorted((x[0] for x in ib.db.execute(
                                "SELECT path FROM files WHERE path GLOB ? OR path GLOB ? OR path GLOB ?",
                                (f"*{nm}.py", f"*{nm}/__init__.py", f"*{nm}.ts")) if _prod(x[0])
                                and x[0].rsplit("/", 1)[-1].split(".")[0] in (nm, "__init__")), key=lambda x: x.count("/"))
                            mod_file = (mods[0],) if mods else None
                            src_mod = mod.replace(".", "/")
                            reexport = ib.db.execute("SELECT path FROM files WHERE path IN (?, ?, ?, ?)",
                                                     (src_mod + ".py", src_mod + "/__init__.py", src_mod + ".ts",
                                                      src_mod + "/index.ts")).fetchone()
                            rows = [x for x in ib.db.execute(
                                "SELECT path, qualname, kind, signature, exported FROM symbols WHERE name=? AND "
                                "kind NOT IN ('field', 'key', 'section') AND qualname=name", (nm,)) if _prod(x["path"])]
                            rows.sort(key=lambda x: (-x["exported"], x["path"].count("/")))
                            if rows:
                                r = rows[0]
                                key = (b, f"{r['path']}::{r['qualname']}", r["signature"], r["kind"])
                            elif mod_file:
                                key = (b, mod_file[0], "", "module")
                            elif reexport:
                                key = (b, f"{reexport[0]}::{nm}", "", "re-export")
                            else:
                                unresolved[f"{b}:{nm}"] += 1
                                continue
                        c = contracts.setdefault(key, {"provider": b, "symbol": key[1], "kind": key[3],
                                                       "signature": key[2][:200], "consumers": {}})
                        c["consumers"].setdefault(a, set()).add(path)
    rows = [{**c, "consumer_repos": len(c["consumers"]), "consumer_files": sum(len(v) for v in c["consumers"].values()),
             "consumers": {k: sorted(v)[:5] for k, v in c["consumers"].items()}} for c in contracts.values()]
    rows.sort(key=lambda r: (-r["consumer_repos"], -r["consumer_files"]))
    by_provider = Counter(r["provider"] for r in rows)
    return {"repos": list(repos), "contracts": rows[:limit], "contract_count": len(rows),
            "by_provider": dict(by_provider), "unresolved_names": dict(unresolved.most_common(10)),
            "note": "Register other repos with add_source path:/gh: (or add_pack) so they join the group."}


def file_risk(ix: CodeIndex, path: str) -> dict:
    """Per-symbol composite risk for one file (complexity .3, callers .25, untested .25, churn .1, size .1;
    +0.1 for heavy runtime traffic when traces are imported)."""
    from .analysis import churn
    from .runtime import latency_of
    if not ix.db.execute("SELECT 1 FROM files WHERE path=?", (path,)).fetchone():
        raise ValueError(f"Not indexed: {path}")
    g = CodeGraph(ix)
    f = Files(ix)
    tests = set(re.findall(r"[A-Za-z_]\w{2,}", _test_text(ix, f)))
    ch = churn(ix, 90).get(path, 0)
    out = []
    for r in ix.db.execute("SELECT * FROM symbols WHERE path=? AND kind IN ('function','method','class')", (path,)):
        c = complexity_of(f.body(r), r["signature"])
        callers = len(g.callers(r["id"]))
        untested = r["name"] not in tests
        risk = round(0.3 * min(1, c["cyclomatic"] / 12) + 0.25 * min(1, callers / 10) + 0.25 * untested
                     + 0.1 * min(1, (ch ** 0.5) / 20) + 0.1 * min(1, c["lines"] / 120), 2)
        row = {"ref": r["qualname"], "line": r["start_line"], "risk": risk, "cyclomatic": c["cyclomatic"],
               "callers": callers, "tested": not untested, "lines": c["lines"]}
        lat = latency_of(ix, path, r["qualname"])
        if lat:  # runtime traces imported: hot and slow code is riskier to change
            row["runtime"] = lat
            row["risk"] = round(min(1.0, risk + 0.1 * min(1, lat["hits"] / 1000)), 2)
        out.append(row)
    out.sort(key=lambda x: -x["risk"])
    return {"file": path, "churn_90d": ch, "max_risk": out[0]["risk"] if out else 0, "symbols": out[:40]}
