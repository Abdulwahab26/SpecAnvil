"""Code insights built on the index and code graph.

Complexity, risk, test reachability, similarity/duplication, structure (entry points, signal chains,
tectonic map, architecture metrics), health radar, multi-signal filtering, endpoint impact, framework
flow edges, migration parity, DB columns, implementations and change-safety checks.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import PurePosixPath

from .graph import CodeGraph, cycles, layer_violations
from .index import GENERATED_RX, NONPROD_RX, TEST_RX, CodeIndex, split_terms
from .testing import BRANCH_RX

FUNC_KINDS = ("function", "method")
CODE_KINDS = ("function", "method", "class", "struct", "interface", "trait")


# ----------------------------------------------------------------------------- helpers

class Files:
    def __init__(self, ix: CodeIndex):
        self.ix = ix
        self._c: dict[str, list[str]] = {}

    def lines(self, path: str) -> list[str]:
        if path not in self._c:
            try:
                self._c[path] = (self.ix.root / path).read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                self._c[path] = []
        return self._c[path]

    def body(self, s) -> str:
        return "\n".join(self.lines(s["path"])[s["start_line"] - 1:s["end_line"]])


def _sym(ix: CodeIndex, ref) -> dict:
    rows = ix.find_symbol(ref)
    if not rows:
        raise ValueError(f"Symbol not found: {ref}")
    if len(rows) > 1 and not str(ref).isdigit():
        exact = [r for r in rows if r["qualname"] == ref or f"{r['path']}::{r['qualname']}" == ref]
        code = [r for r in rows if r["kind"] not in ("key", "section")]
        rows = exact or code or rows
    return dict(rows[0])


def _folder(ix: CodeIndex, target: str) -> str | None:
    """A target that names a directory in the index (e.g. 'backend', 'src/app/'), normalised without trailing slash."""
    t = target.strip().rstrip("/")
    if not t or "::" in t:
        return None
    hit = ix.db.execute("SELECT 1 FROM files WHERE path LIKE ? LIMIT 1", (t + "/%",)).fetchone()
    return t if hit else None


def _test_text(ix: CodeIndex, f: Files) -> str:
    return "\n".join("\n".join(f.lines(r[0])) for r in ix.db.execute("SELECT path FROM files WHERE parser!='text'")
                     if TEST_RX.search(r[0]))


def _prod(path: str) -> bool:
    return not (TEST_RX.search(path) or GENERATED_RX.search(path) or NONPROD_RX.search(path))


# ----------------------------------------------------------------------------- complexity

CONTROL_RX = re.compile(r"^\s*(?:\}\s*)?(if|elif|else|for|foreach|while|do|switch|case|try|catch|except|finally|"
                        r"with|match|when|select|loop|unless|until|guard|defer)\b")


def complexity_of(body: str, signature: str) -> dict:
    """Cyclomatic complexity (decision points + 1), nesting depth of control blocks, parameter count, lines."""
    lines = [l for l in body.split("\n") if l.strip()]
    branches = len(BRANCH_RX.findall(body))
    stack: list[int] = []
    nesting = 0
    for l in lines[1:]:
        if not CONTROL_RX.match(l):
            continue
        ind = len(l) - len(l.lstrip())
        while stack and stack[-1] >= ind:
            stack.pop()
        stack.append(ind)
        nesting = max(nesting, len(stack))
    m = re.search(r"\((.*)\)", signature)
    params = [p for p in re.split(r",(?![^<\[(]*[>\])])", m.group(1)) if p.strip() and p.strip() not in ("self", "cls", "this")] if m else []
    return {"cyclomatic": branches + 1, "nesting": nesting, "params": len(params), "lines": len(lines)}


def symbol_complexity(ix: CodeIndex, target: str, limit: int = 30) -> list[dict]:
    f = Files(ix)
    folder = _folder(ix, target)
    if "::" not in target and ix.db.execute("SELECT 1 FROM files WHERE path=?", (target,)).fetchone():
        rows = [dict(r) for r in ix.db.execute("SELECT * FROM symbols WHERE path=? AND kind IN ('function','method')", (target,))]
    elif folder:
        rows = [dict(r) for r in ix.db.execute("SELECT * FROM symbols WHERE path LIKE ? AND kind IN ('function','method')",
                                               (folder + "/%",)) if _prod(r["path"])]
    else:
        rows = [_sym(ix, target)]
    out = []
    for s in rows:
        c = complexity_of(f.body(s), s["signature"])
        rating = "high" if c["cyclomatic"] > 15 or c["nesting"] > 4 else "medium" if c["cyclomatic"] > 8 else "low"
        out.append({"ref": f"{s['path']}::{s['qualname']}", **c, "rating": rating})
    out.sort(key=lambda x: -x["cyclomatic"])
    return out[:limit]


# ----------------------------------------------------------------------------- tests

def _route_rx(path: str) -> re.Pattern | None:
    """Regex for how tests spell a route: its last two segments, params as wildcards (prefixes like
    f"{settings.API_V1_STR}" vary, so they are not required)."""
    segs = [x for x in path.strip("/").split("/") if x][-2:]
    if not segs or all(re.fullmatch(r"\{[^}]+\}|<[^>]+>|:\w+", x) for x in segs):
        return None
    pat = "/".join(r"[^/'\"`\s]+" if re.fullmatch(r"\{[^}]+\}|<[^>]+>|:\w+", x) else re.escape(x) for x in segs)
    return re.compile(r"/" + pat + r"/?['\"`?]")


def _http_tested_handlers(ix: CodeIndex, test_text: str) -> set[int]:
    if not test_text:
        return set()
    try:
        from .facts import Facts
        eps = Facts(ix).get("endpoints")
    except Exception:  # noqa: BLE001 - facts are best-effort here
        return set()
    out = set()
    for e in eps:
        rx = _route_rx(e.get("path", ""))
        if not rx or not rx.search(test_text):
            continue
        row = ix.db.execute("SELECT id FROM symbols WHERE path=? AND (qualname=? OR name=?) LIMIT 1",
                            (e["at"].split(":")[0], e["handler"], e["handler"].rsplit(".", 1)[-1])).fetchone()
        if row:
            out.add(row[0])
    return out


def untested_symbols(ix: CodeIndex, target: str | None = None, limit: int = 50) -> dict:
    """No test evidence: not reachable from any test function via the call graph (3 hops) and the name never
    appears in a test file."""
    g = CodeGraph(ix)
    f = Files(ix)
    tests = [sid for sid, s in g.sym.items() if TEST_RX.search(s["path"]) and s["kind"] in FUNC_KINDS]
    reached: set[int] = set()
    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in g.edges():
        adj[a].append(b)
    q = deque((t, 0) for t in tests)
    text = _test_text(ix, f)
    for hid in _http_tested_handlers(ix, text):  # route handlers exercised over HTTP (client.post("/users/"))
        reached.add(hid)
        q.append((hid, 1))
    while q:
        cur, d = q.popleft()
        if d >= 3:
            continue
        for nxt in adj.get(cur, []):
            if nxt not in reached:
                reached.add(nxt)
                q.append((nxt, d + 1))
    names = set(re.findall(r"[A-Za-z_]\w{2,}", text))
    out, total = [], 0
    for sid, s in g.sym.items():
        if s["kind"] not in CODE_KINDS or not _prod(s["path"]) or s["name"].startswith("_"):
            continue
        if target and not (s["path"].startswith(target.rstrip("/")) or target in s["path"]):
            continue
        total += 1
        if sid in reached or s["name"] in names:
            continue
        c = complexity_of(f.body(s), s["signature"])["cyclomatic"] if s["kind"] in FUNC_KINDS else 1
        out.append({"id": sid, "ref": f"{s['path']}::{s['qualname']}", "kind": s["kind"], "cyclomatic": c,
                    "callers": len(g.callers(sid)) if c > 3 else None})
    out.sort(key=lambda x: (-x["cyclomatic"], x["ref"]))
    return {"untested": len(out), "of": total, "tested_pct": round(100 * (1 - len(out) / total), 1) if total else None,
            "symbols": out[:limit],
            "note": "Evidence = reachable from a test through the call graph (3 hops), named in a test file, or behind a route a test requests. Code exercised "
                    "only over HTTP/UI shows as untested; runtime_coverage (from a coverage report) is exact."}


# ----------------------------------------------------------------------------- similarity

TOKEN_RX = re.compile(r"[A-Za-z_]\w*|\d+|[^\s\w]")


def _shingles(body: str, k: int = 5) -> set[int]:
    toks = [t if not t.isdigit() else "0" for t in TOKEN_RX.findall(body)]
    return {int.from_bytes(hashlib.blake2b(" ".join(toks[i:i + k]).encode(), digest_size=8).digest(), "big")
            for i in range(max(0, len(toks) - k + 1))}


def _minhash(sh: set[int], perms: int = 64) -> tuple:
    mask = (1 << 61) - 1
    return tuple(min(((x * (2 * i + 1) + i * 0x9E3779B97F4A7C15) & mask) for x in sh) if sh else 0 for i in range(perms))


def similar_symbols(ix: CodeIndex, ref: str | None = None, threshold: float = 0.8, min_lines: int = 6,
                    limit: int = 30) -> list[dict]:
    """Near-duplicate functions (MinHash over 5-token shingles, verified with exact Jaccard)."""
    f = Files(ix)
    rows = [dict(r) for r in ix.db.execute("SELECT id, path, qualname, name, signature, start_line, end_line FROM symbols "
                                           "WHERE kind IN ('function','method') AND end_line - start_line >= ?",
                                           (min_lines - 1,))]
    rows = [r for r in rows if _prod(r["path"])]
    sh = {r["id"]: _shingles(f.body(r)) for r in rows}
    sig = {sid: _minhash(s) for sid, s in sh.items() if len(s) >= 8}
    bands: dict[tuple, list[int]] = defaultdict(list)
    for sid, mh in sig.items():
        for b in range(16):
            bands[(b,) + mh[b * 4:(b + 1) * 4]].append(sid)
    by_id = {r["id"]: r for r in rows}
    target = _sym(ix, ref)["id"] if ref else None
    pairs: dict[tuple, float] = {}
    for members in bands.values():
        if len(members) < 2 or len(members) > 50:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = sorted((members[i], members[j]))
                if (a, b) in pairs or (target and target not in (a, b)):
                    continue
                inter = len(sh[a] & sh[b])
                jac = inter / max(1, len(sh[a] | sh[b]))
                if jac >= threshold:
                    pairs[(a, b)] = round(jac, 2)
    # cluster pairs
    parent: dict[int, int] = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    for a, b in pairs:
        parent[find(a)] = find(b)
    clusters: dict[int, set] = defaultdict(set)
    for a, b in pairs:
        clusters[find(a)].update((a, b))
    out = []
    for members in clusters.values():
        ms = sorted(members)
        sims = [v for (a, b), v in pairs.items() if a in members and b in members]
        out.append({"similarity": max(sims), "size": len(ms), "cross_file": len({by_id[m]["path"] for m in ms}) > 1,
                    "lines": by_id[ms[0]]["end_line"] - by_id[ms[0]]["start_line"] + 1,
                    "symbols": [f"{by_id[m]['path']}::{by_id[m]['qualname']} (L{by_id[m]['start_line']})" for m in ms]})
    out.sort(key=lambda c: (-c["size"] * c["lines"], -c["similarity"]))
    return out[:limit]


def related_symbols(ix: CodeIndex, ref: str, limit: int = 15) -> list[dict]:
    """Symbols related by shared callers/callees, direct calls, same file and name overlap."""
    g = CodeGraph(ix)
    s = _sym(ix, ref)
    sid = s["id"]
    callers = {c for c, _ in g.callers(sid)}
    callees = {c for c, _ in g.callees(sid)}
    score: Counter = Counter()
    why: dict[int, set] = defaultdict(set)
    for c in callers:
        score[c] += 3
        why[c].add("calls it")
        for sib, _ in g.callees(c):
            if sib != sid:
                score[sib] += 1.5
                why[sib].add("same caller")
    for c in callees:
        score[c] += 3
        why[c].add("called by it")
        for co, _ in g.callers(c):
            if co != sid:
                score[co] += 1
                why[co].add("same callee")
    terms = set(split_terms(s["qualname"]).split()) - {"get", "set", "is", "to", "the"}
    for other, o in g.sym.items():
        if other == sid or o["kind"] not in CODE_KINDS:
            continue
        if o["path"] == s["path"]:
            score[other] += 0.5
            why[other].add("same file")
        ot = set(split_terms(o["qualname"]).split())
        overlap = len(terms & ot)
        if overlap >= 2 or (overlap == 1 and len(terms) == 1):
            score[other] += overlap
            why[other].add("similar name")
    return [{"ref": f"{g.sym[k]['path']}::{g.sym[k]['qualname']}", "score": round(v, 1), "why": sorted(why[k])}
            for k, v in score.most_common(limit * 2) if k in g.sym and g.sym[k]["kind"] in CODE_KINDS][:limit]


def extraction_candidates(ix: CodeIndex, limit: int = 20) -> list[dict]:
    """Functions worth extracting or moving: duplicated across files, utilities used by many modules but
    living in one, and functions too complex to keep whole."""
    out = []
    for c in similar_symbols(ix, threshold=0.75, limit=50):
        if c["cross_file"]:
            out.append({"kind": "duplicate", "action": "extract into a shared module", "evidence": c["symbols"],
                        "similarity": c["similarity"], "lines": c["lines"], "weight": c["size"] * c["lines"]})
    g = CodeGraph(ix)
    mod = {r[0]: r[1] for r in ix.db.execute("SELECT path, module FROM files")}
    fan: dict[int, set] = defaultdict(set)
    for a, b in g.edges():
        ma, mb = mod.get(g.sym[a]["path"]), mod.get(g.sym[b]["path"])
        if ma and mb and ma != mb:
            fan[b].add(ma)
    for sid, mods in fan.items():
        s = g.sym[sid]
        if len(mods) >= 3 and _prod(s["path"]) and s["kind"] in FUNC_KINDS and \
                not re.search(r"(^|/)(utils?|helpers?|common|shared|lib|core)(/|\.|$)", s["path"]):
            out.append({"kind": "misplaced utility", "action": "move to a shared/utilities module",
                        "evidence": [f"{s['path']}::{s['qualname']}", f"used by {len(mods)} modules: {sorted(mods)[:6]}"],
                        "weight": len(mods) * 10})
    for c in symbol_complexity_all(ix, 15):
        out.append({"kind": "too complex", "action": "split into smaller functions",
                    "evidence": [c["ref"], f"cyclomatic {c['cyclomatic']}, nesting {c['nesting']}, {c['lines']} lines"],
                    "weight": c["cyclomatic"] * 3})
    out.sort(key=lambda x: -x["weight"])
    return out[:limit]


def symbol_complexity_all(ix: CodeIndex, min_cyclomatic: int = 10, limit: int = 30) -> list[dict]:
    f = Files(ix)
    out = []
    for r in ix.db.execute("SELECT * FROM symbols WHERE kind IN ('function','method') AND end_line - start_line >= 8"):
        if not _prod(r["path"]):
            continue
        c = complexity_of(f.body(r), r["signature"])
        if c["cyclomatic"] >= min_cyclomatic:
            out.append({"ref": f"{r['path']}::{r['qualname']}", **c})
    out.sort(key=lambda x: -x["cyclomatic"])
    return out[:limit]


# ----------------------------------------------------------------------------- decorators, entry points, chains

DECOR_RX = re.compile(r"^[ \t]*(?:@([A-Za-z_][\w.]*)|\[([A-Z]\w*)(?:\(|\])|#\[(\w+))", re.M)


def decorator_census(ix: CodeIndex, limit: int = 40) -> list[dict]:
    f = Files(ix)
    counts: Counter = Counter()
    where: dict[str, list] = defaultdict(list)
    for (path,) in ix.db.execute("SELECT path FROM files WHERE parser!='text'"):
        text = "\n".join(f.lines(path))
        for m in DECOR_RX.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3)
            if name in ("param", "returns", "return", "type", "see", "example", "throws", "deprecated") and \
                    text[max(0, m.start() - 3):m.start()].strip().endswith("*"):
                continue  # JSDoc tags
            counts[name] += 1
            if len(where[name]) < 3:
                where[name].append(f"{path}:{text.count(chr(10), 0, m.start()) + 1}")
    return [{"decorator": k, "count": v, "examples": where[k]} for k, v in counts.most_common(limit)]


ENTRY_DECOR_RX = re.compile(r"^\s*(@(app|router|api|bp|blueprint)\.|@(Get|Post|Put|Patch|Delete|Request)Mapping|"
                            r"@(Get|Post|Put|Patch|Delete|Controller|Scheduled|KafkaListener|EventListener|Cron)\b|"
                            r"\[(Http(Get|Post|Put|Patch|Delete)|Function|FunctionName|Route)\b|@(click|command|task|"
                            r"shared_task|celery)|@\w+\.(route|timer_trigger|queue_trigger|blob_trigger|"
                            r"service_bus_queue_trigger|event_grid_trigger))", re.I)


def entry_points(ix: CodeIndex, limit: int = 100) -> list[dict]:
    f = Files(ix)
    out = []
    for r in ix.db.execute("SELECT id, path, qualname, name, kind, start_line FROM symbols WHERE kind IN "
                           "('function','method')"):
        if not _prod(r["path"]):
            continue
        lines = f.lines(r["path"])
        prev = lines[max(0, r["start_line"] - 4):r["start_line"] - 1]
        reason = None
        if any(ENTRY_DECOR_RX.match(x) for x in prev):
            reason = "framework-registered (route/handler/job)"
        elif r["name"] in ("main", "Main", "handler", "lambda_handler", "run_server"):
            reason = "program entry"
        if reason:
            out.append({"id": r["id"], "ref": f"{r['path']}::{r['qualname']}", "why": reason})
    return out[:limit]


def signal_chains(ix: CodeIndex, entry: str | None = None, max_depth: int = 6, limit: int = 20) -> list[dict]:
    """Entry point -> ... -> leaf paths through the resolved call graph (longest first)."""
    g = CodeGraph(ix)
    adj: dict[int, list[int]] = defaultdict(list)
    for a, b in g.edges():
        if b not in adj[a]:
            adj[a].append(b)
    starts = [_sym(ix, entry)["id"]] if entry else [e["id"] for e in entry_points(ix, 200)]
    chains = []
    for s in starts:
        stack = [(s, [s])]
        while stack and len(chains) < 2000:
            cur, path = stack.pop()
            nxt = [n for n in adj.get(cur, []) if n not in path and _prod(g.sym[n]["path"])]
            if not nxt or len(path) >= max_depth:
                if len(path) > 1:
                    chains.append(path)
                continue
            for n in nxt[:8]:
                stack.append((n, path + [n]))
    chains.sort(key=len, reverse=True)
    seen, out = set(), []
    for c in chains:
        key = (c[0], c[-1])
        if key in seen:
            continue
        seen.add(key)
        out.append({"length": len(c), "chain": [f"{g.sym[i]['qualname']} ({g.sym[i]['path']}:{g.sym[i]['start_line']})" for i in c]})
        if len(out) >= limit:
            break
    return out


# ----------------------------------------------------------------------------- structure

def tectonic_map(ix: CodeIndex, iterations: int = 15) -> dict:
    """Logical modules from the dependency graph (label propagation over imports + cross-file calls), compared
    with the folder structure: files whose logical home differs from their folder are flagged."""
    g = CodeGraph(ix)
    w: dict[str, Counter] = defaultdict(Counter)
    for s, d in ix.db.execute("SELECT src, dst FROM file_deps"):
        w[s][d] += 2
        w[d][s] += 2
    for a, b in g.edges():
        pa, pb = g.sym[a]["path"], g.sym[b]["path"]
        if pa != pb:
            w[pa][pb] += 1
            w[pb][pa] += 1
    files = sorted(p for p in w if _prod(p))
    label = {p: p for p in files}
    for _ in range(iterations):
        changed = 0
        for p in files:
            votes: Counter = Counter()
            for q, wt in w[p].items():
                if q in label:
                    votes[label[q]] += wt
            if votes:
                best = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
                if best != label[p]:
                    label[p] = best
                    changed += 1
        if not changed:
            break
    mod = {r[0]: r[1] for r in ix.db.execute("SELECT path, module FROM files")}
    groups: dict[str, list[str]] = defaultdict(list)
    for p, l in label.items():
        groups[l].append(p)
    plates = []
    misplaced = []
    for l, ps in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(ps) < 2:
            continue
        folder = Counter(mod.get(p, "?") for p in ps)
        home, n = folder.most_common(1)[0]
        internal = sum(w[p][q] for p in ps for q in ps if q in w[p]) / 2
        external = sum(wt for p in ps for q, wt in w[p].items() if q not in ps)
        plates.append({"plate": home, "files": len(ps), "folders": dict(folder.most_common(4)),
                       "cohesion": round(internal / max(1, internal + external), 2), "sample": ps[:6]})
        for p in ps:
            if mod.get(p) != home and folder[mod.get(p)] <= 1:
                misplaced.append({"file": p, "folder_module": mod.get(p), "logically_belongs_with": home})
    return {"plates": plates[:25], "misplaced": misplaced[:40]}


def architecture_metrics(ix: CodeIndex) -> dict:
    mod = {r[0]: r[1] for r in ix.db.execute("SELECT path, module FROM files WHERE parser!='text'")}
    edges: dict[str, set] = defaultdict(set)
    fan_in: Counter = Counter()
    fan_out: Counter = Counter()
    for s, d in ix.db.execute("SELECT src, dst FROM file_deps"):
        fan_in[d] += 1
        fan_out[s] += 1
        if mod.get(s) and mod.get(d) and mod[s] != mod[d]:
            edges[mod[s]].add(mod[d])
    # dependency depth over the module DAG (cycles collapsed)
    sccs = cycles(ix, "module", 1000)
    comp = {m: i for i, c in enumerate(sccs) for m in c}
    memo: dict[str, int] = {}

    def depth(m: str, stack: frozenset = frozenset()) -> int:
        if m in memo:
            return memo[m]
        if m in stack:
            return 0
        best = 0
        for d in edges.get(m, ()):
            if comp.get(d) is not None and comp.get(d) == comp.get(m):
                continue
            best = max(best, 1 + depth(d, stack | {m}))
        memo[m] = best
        return best
    modules = set(mod.values())
    depths = {m: depth(m) for m in modules}
    total_in = sum(fan_in.values()) or 1
    top = sorted(fan_in.values(), reverse=True)[:max(1, len(mod) // 10)]
    sizes = Counter(mod.values())
    return {"files": len(mod), "modules": len(modules), "max_dependency_depth": max(depths.values(), default=0),
            "deepest_modules": sorted(depths.items(), key=lambda kv: -kv[1])[:5],
            "fan_in_concentration_top10pct": round(sum(top) / total_in, 2),
            "avg_fan_out": round(sum(fan_out.values()) / max(1, len(mod)), 2),
            "most_depended_on": [p for p, _ in fan_in.most_common(8)],
            "largest_modules": sizes.most_common(6), "module_cycles": len(sccs)}


def repo_health(ix: CodeIndex, save: bool = True) -> dict:
    """Six-axis health radar (0-100, higher is better) with the evidence behind each score and a trend vs the
    previous snapshot."""
    from .graph import dead_code
    from .specs import drift
    funcs = symbol_complexity_all(ix, 0, 100000)
    n_f = max(1, len(funcs))
    complex_pct = sum(1 for c in funcs if c["cyclomatic"] > 10) / n_f
    complexity = round(100 * (1 - min(1.0, complex_pct * 4)))
    cyc = cycles(ix, "file", 1000)
    viol = layer_violations(ix)["violations"]
    files = max(1, ix.db.execute("SELECT COUNT(*) FROM files WHERE parser!='text'").fetchone()[0])
    modularity = round(100 * max(0.0, 1 - (len(cyc) * 5 + len(viol)) / max(10, files / 5)))
    ut = untested_symbols(ix, limit=1)
    testing = round(ut["tested_pct"] or 0)
    dead = dead_code(ix, 100000)
    syms = max(1, ix.db.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('function','method','class')").fetchone()[0])
    deadness = round(100 * max(0.0, 1 - len([d for d in dead if d["confidence"] == "high"]) * 10 / syms))
    documented = ix.db.execute("SELECT COUNT(*) FROM symbols WHERE kind IN ('function','method','class') AND doc!=''").fetchone()[0]
    dr = drift(ix.root, ix)
    docs = round(100 * documented / syms * 0.7 + (30 if not [i for i in dr["issues"] if i["issue"] in ("changed", "missing")] else 10))
    from .analysis import churn, hotspots
    hs = hotspots(ix, limit=200)
    ch = sorted(churn(ix, 180).values())
    p75 = ch[int(len(ch) * 0.75)] if ch else 0
    risky = [h for h in hs if h["branches"] >= 8 and h["file_churn"] >= max(1, p75)]
    churn_risk = round(100 * max(0.0, 1 - len(risky) / max(5, n_f * 0.02)))
    axes = {"complexity": complexity, "modularity": modularity, "testing": testing, "dead_code": deadness,
            "documentation": min(100, docs), "change_risk": churn_risk}
    overall = round(sum(axes.values()) / len(axes))
    prev = ix.db.execute("SELECT value FROM meta WHERE key='health_last'").fetchone()
    trend = {}
    if prev:
        old = json.loads(prev[0])
        trend = {k: v - old["axes"].get(k, v) for k, v in axes.items()}
    snap = {"overall": overall, "axes": axes,
            "evidence": {"functions_over_cyclomatic_10": f"{round(complex_pct * 100, 1)}%", "import_cycles": len(cyc),
                         "layer_violations": len(viol), "tested_symbols": f"{ut['tested_pct']}%",
                         "dead_high_confidence": len([d for d in dead if d["confidence"] == "high"]),
                         "documented_symbols": f"{round(100 * documented / syms, 1)}%", "spec_drift": dr["summary"],
                         "complex_and_frequently_changed": [h["ref"] for h in risky[:3]]},
            "trend_vs_last": trend}
    if save:
        ix.db.execute("INSERT OR REPLACE INTO meta VALUES('health_last', ?)", (json.dumps(snap),))
        ix.db.commit()
    weakest = sorted(axes.items(), key=lambda kv: kv[1])[:2]
    snap["focus_next"] = [f"{k} ({v}/100)" for k, v in weakest]
    return snap


# ----------------------------------------------------------------------------- winnow & suggestions

def winnow(ix: CodeIndex, kind: str | None = None, path_glob: str | None = None, name_regex: str | None = None,
           min_complexity: int = 0, min_callers: int = 0, max_callers: int | None = None, untested: bool | None = None,
           exported: bool | None = None, has_doc: bool | None = None, changed_since: str | None = None,
           sort_by: str = "complexity", limit: int = 30) -> list[dict]:
    """Compose several signals into one ranked symbol query."""
    sql = "SELECT * FROM symbols WHERE 1=1"
    args: list = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    rows = [dict(r) for r in ix.db.execute(sql, args)]
    if path_glob:
        rows = [r for r in rows if fnmatch.fnmatch(r["path"], path_glob)]
    if name_regex:
        rx = re.compile(name_regex)
        rows = [r for r in rows if rx.search(r["qualname"])]
    if exported is not None:
        rows = [r for r in rows if bool(r["exported"]) == exported]
    if has_doc is not None:
        rows = [r for r in rows if bool(r["doc"]) == has_doc]
    if changed_since:
        changed = {(c["path"], c["symbol"]) for c in ix.changed_symbols(changed_since)}
        rows = [r for r in rows if (r["path"], r["qualname"]) in changed]
    rows = [r for r in rows if _prod(r["path"])] if not (path_glob and TEST_RX.search(path_glob)) else rows
    f = Files(ix)
    g = CodeGraph(ix) if (min_callers or max_callers is not None or sort_by == "callers") else None
    test_names = set(re.findall(r"[A-Za-z_]\w{2,}", _test_text(ix, f))) if untested is not None else set()
    out = []
    for r in rows[:20000]:
        c = complexity_of(f.body(r), r["signature"])["cyclomatic"] if r["kind"] in FUNC_KINDS else 1
        if c < min_complexity:
            continue
        callers = len(g.callers(r["id"])) if g else None
        if g and (callers < min_callers or (max_callers is not None and callers > max_callers)):
            continue
        if untested is not None and ((r["name"] not in test_names) != untested):
            continue
        out.append({"id": r["id"], "ref": f"{r['path']}::{r['qualname']}", "kind": r["kind"], "complexity": c,
                    "callers": callers, "lines": r["end_line"] - r["start_line"] + 1})
    key = {"complexity": lambda x: -x["complexity"], "callers": lambda x: -(x["callers"] or 0),
           "lines": lambda x: -x["lines"], "name": lambda x: x["ref"]}.get(sort_by, lambda x: -x["complexity"])
    out.sort(key=key)
    return out[:limit]


def suggest_queries(ix: CodeIndex) -> list[dict]:
    from .analysis import hotspots
    from .graph import importance
    out = [{"ask": "orientation", "call": "repo_map()"}]
    for imp in importance(ix, 3):
        out.append({"ask": f"who depends on {imp['ref'].split('::')[1]}?", "call": f"call_hierarchy('{imp['ref']}', 'callers')"})
    for h in hotspots(ix, limit=2):
        out.append({"ask": f"is {h['ref'].split('::')[1]} risky to change?", "call": f"check_safety('{h['ref']}', 'edit')"})
    eps = entry_points(ix, 3)
    for e in eps[:2]:
        out.append({"ask": f"what does {e['ref'].split('::')[1]} touch end to end?", "call": f"signal_chains(entry='{e['ref']}')"})
    out += [{"ask": "overall health", "call": "repo_health()"}, {"ask": "duplicated logic", "call": "similar_symbols()"},
            {"ask": "risky patterns", "call": "search_ast(preset='broad-except')"}]
    return out


# ----------------------------------------------------------------------------- endpoints & flow edges

def endpoint_impact(ix: CodeIndex, endpoint: str, depth: int = 3) -> dict:
    """What breaks if this HTTP endpoint changes: handler, everything it calls, frontend callers, tests, specs."""
    from .facts import Facts
    eps = Facts(ix).get("endpoints")
    method, _, path = endpoint.strip().partition(" ") if " " in endpoint.strip() else ("", "", endpoint.strip())
    match = [e for e in eps if e["path"].rstrip("/") == path.rstrip("/") and (not method or e["method"] == method.upper())]
    if not match:
        cands = sorted({e["path"] for e in eps if path.strip("/").split("/")[0] in e["path"]})[:10]
        raise ValueError(f"No endpoint '{endpoint}'. Similar: {cands or 'none'}")
    g = CodeGraph(ix)
    res = {"endpoint": [f"{e['method']} {e['path']} -> {e['handler']} ({e['at']})" for e in match], "calls": [],
           "frontend_callers": [], "tests": [], "specs": []}
    for e in match:
        rows = ix.db.execute("SELECT id FROM symbols WHERE path=? AND (qualname=? OR name=?) LIMIT 1",
                             (e["at"].split(":")[0], e["handler"], e["handler"].rsplit(".", 1)[-1])).fetchall()
        if not rows:
            continue
        seen = {rows[0]["id"]}
        q = deque([(rows[0]["id"], 0)])
        while q:
            cur, d = q.popleft()
            if d >= depth:
                continue
            for n, _ in g.callees(cur):
                if n not in seen:
                    seen.add(n)
                    res["calls"].append(f"{'  ' * d}{g.sym[n]['qualname']} ({g.sym[n]['path']})")
                    q.append((n, d + 1))
    tail = re.sub(r"\{[^}]+\}|<[^>]+>|:\w+", "@@", path.rstrip("/"))
    parts = [re.escape(p) for p in tail.split("@@")]
    rx = re.compile("[^/'\"`]+".join(parts) if len(parts) > 1 else re.escape(tail))
    for (p,) in ix.db.execute("SELECT path FROM files"):
        try:
            text = (ix.root / p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = [i + 1 for i, ln in enumerate(text.split("\n")) if rx.search(ln)]
        if not hits or p == match[0]["at"].split(":")[0]:
            continue
        loc = f"{p}:{hits[0]}" + (f" (+{len(hits) - 1})" if len(hits) > 1 else "")
        if TEST_RX.search(p):
            res["tests"].append(loc)
        elif p.endswith(".md"):
            res["specs"].append(loc)
        elif re.search(r"\.(tsx?|jsx?|vue|svelte|dart|kt|swift)$", p):
            res["frontend_callers"].append(loc)
    return res


EMIT_RX = re.compile(r"""\.(?:emit|publish|send|dispatch|trigger|fire|produce|enqueue|delay|apply_async)\(\s*['"]([\w.:\-/]+)['"]""")
ON_RX = re.compile(r"""(?:\.(?:on|once|subscribe|addListener|addEventListener|listen|consume|handle)\(\s*['"]([\w.:\-/]+)['"]|"""
                   r"""@(?:OnEvent|EventPattern|MessagePattern|KafkaListener|RabbitListener|SqsListener|Subscribe)\(\s*(?:topics\s*=\s*)?['"]([\w.:\-/]+)['"])""")
CTOR_RX = re.compile(r"constructor\s*\(([^)]*)\)|public\s+\w+\s*\(([^)]*)\)\s*\{|def __init__\(self,([^)]*)\)")


def flow_edges(ix: CodeIndex, limit: int = 200) -> dict:
    """Edges a call graph misses: route -> handler, event emit -> listeners, constructor dependency injection."""
    from .facts import Facts
    f = Files(ix)
    routes = [{"from": f"{e['method']} {e['path']}", "to": e["handler"], "at": e["at"]} for e in Facts(ix).get("endpoints")]
    emits: dict[str, list] = defaultdict(list)
    ons: dict[str, list] = defaultdict(list)
    di = []
    classes = {r[0] for r in ix.db.execute("SELECT name FROM symbols WHERE kind IN ('class','interface','struct')")}
    for (path,) in ix.db.execute("SELECT path FROM files WHERE parser!='text'"):
        if not _prod(path):
            continue
        text = "\n".join(f.lines(path))
        for m in EMIT_RX.finditer(text):
            emits[m.group(1)].append(f"{path}:{text.count(chr(10), 0, m.start()) + 1}")
        for m in ON_RX.finditer(text):
            ons[m.group(1) or m.group(2)].append(f"{path}:{text.count(chr(10), 0, m.start()) + 1}")
        for m in CTOR_RX.finditer(text):
            params = m.group(1) or m.group(2) or m.group(3) or ""
            types = set(re.findall(r"\b([A-Z]\w+)\b", params)) & classes
            if types:
                line = text.count("\n", 0, m.start()) + 1
                owner = ix.enclosing_symbol(path, line)
                di.append({"class": owner["qualname"].split(".")[0] if owner else path, "injects": sorted(types),
                           "at": f"{path}:{line}"})
    events = [{"event": k, "emitted_at": emits[k][:5], "handled_at": ons.get(k, [])[:5]}
              for k in sorted(set(emits) | set(ons))]
    return {"routes": routes[:limit], "events": events[:limit], "dependency_injection": di[:limit],
            "unhandled_events": [e["event"] for e in events if e["emitted_at"] and not e["handled_at"]][:30],
            "never_emitted": [e["event"] for e in events if e["handled_at"] and not e["emitted_at"]][:30]}


# ----------------------------------------------------------------------------- parity, columns, graph, impls

def _norm_name(n: str) -> str:
    return re.sub(r"[_\-]", "", n.lower())


def parity_map(ix: CodeIndex, left: str, right: str, right_ix: CodeIndex | None = None) -> dict:
    """Migration parity: which symbols under `left` have a counterpart under `right` (same normalised name)."""
    r_ix = right_ix or ix

    def collect(i: CodeIndex, prefix: str) -> dict:
        rows = i.db.execute("SELECT path, qualname, name, kind, signature FROM symbols WHERE kind IN "
                            "('function','method','class','interface','struct') AND path LIKE ?",
                            (prefix.rstrip("/") + "%",)).fetchall()
        return {_norm_name(r["name"]): dict(r) for r in rows if not TEST_RX.search(r["path"])}
    L, R = collect(ix, left), collect(r_ix, right)
    matched = [{"left": f"{L[k]['path']}::{L[k]['qualname']}", "right": f"{R[k]['path']}::{R[k]['qualname']}"}
               for k in sorted(set(L) & set(R))]
    missing = [f"{L[k]['path']}::{L[k]['qualname']}" for k in sorted(set(L) - set(R))]
    extra = [f"{R[k]['path']}::{R[k]['qualname']}" for k in sorted(set(R) - set(L))]
    return {"parity_pct": round(100 * len(matched) / max(1, len(L)), 1), "matched": len(matched),
            "missing_in_right": missing[:100], "new_in_right": extra[:100], "pairs": matched[:100]}


SQL_TABLE_RX = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\((.*?)\)\s*;", re.I | re.S)
ORM_COL_RX = re.compile(r"^\s*(\w+)\s*(?::\s*[^=\n]+)?=\s*(?:\w+\.)?(Column|Field|mapped_column|relationship|"
                        r"models\.\w+Field|models\.ForeignKey|models\.\w+)\(([^\n]*)", re.M)


def search_columns(ix: CodeIndex, query: str = "", limit: int = 60) -> list[dict]:
    """Database columns from ORM models (SQLAlchemy/SQLModel/Django/JPA/EF/Prisma/TypeORM) and SQL migrations."""
    from .facts import Facts
    f = Files(ix)
    out = []
    for m in Facts(ix).get("models"):
        path, line = m["at"].rsplit(":", 1)
        sym = ix.db.execute("SELECT start_line, end_line FROM symbols WHERE path=? AND name=? LIMIT 1",
                            (path, m["name"])).fetchone()
        lines = f.lines(path)
        if path.endswith(".prisma"):
            start = int(line)
            for i in range(start, min(len(lines), start + 200)):
                if lines[i].strip().startswith("}"):
                    break
                mm = re.match(r"\s*(\w+)\s+([\w\[\]?]+)", lines[i])
                if mm:
                    out.append({"table": m["name"], "column": mm.group(1), "type": mm.group(2), "at": f"{path}:{i + 1}"})
            continue
        if not sym:
            continue
        body = "\n".join(lines[sym["start_line"] - 1:sym["end_line"]])
        for cm in ORM_COL_RX.finditer(body):
            out.append({"table": m["name"], "column": cm.group(1), "type": f"{cm.group(2)}({cm.group(3)[:60]}",
                        "at": f"{path}:{sym['start_line'] + body.count(chr(10), 0, cm.start())}"})
        for cm in re.finditer(r"^\s*(\w+)\s*:\s*([\w\[\]| .]+?)\s*(?:=\s*Field\(([^\n]*))?$", body, re.M):
            if cm.group(1) not in ("model_config", "class") and not cm.group(1).startswith("_"):
                out.append({"table": m["name"], "column": cm.group(1), "type": cm.group(2).strip(),
                            "at": f"{path}:{sym['start_line'] + body.count(chr(10), 0, cm.start())}"})
        for cm in re.finditer(r"@Column[^\n]*\n\s*(?:private|public|protected)?\s*([\w<>]+)\s+(\w+)", body):
            out.append({"table": m["name"], "column": cm.group(2), "type": cm.group(1), "at": path})
    for (p,) in ix.db.execute("SELECT path FROM files WHERE path LIKE '%.sql'"):
        text = "\n".join(f.lines(p))
        for tm in SQL_TABLE_RX.finditer(text):
            for col in re.split(r",\s*\n|,(?![^(]*\))", tm.group(2)):
                cm = re.match(r"\s*[`\"\[]?(\w+)[`\"\]]?\s+([\w()]+)", col)
                if cm and cm.group(1).upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "KEY", "INDEX", "CHECK"):
                    out.append({"table": tm.group(1), "column": cm.group(1), "type": cm.group(2), "at": p})
    seen, uniq = set(), []
    for c in out:
        k = (c["table"], c["column"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    if query:
        terms = split_terms(query).split()
        uniq = [c for c in uniq if all(t in split_terms(c["table"], c["column"]) for t in terms)] or \
            [c for c in uniq if any(t in split_terms(c["table"], c["column"]) for t in terms)]
    return uniq[:limit]


def dependency_graph(ix: CodeIndex, path: str, depth: int = 1, direction: str = "both") -> dict:
    folder = _folder(ix, path)
    if folder and not ix.db.execute("SELECT 1 FROM files WHERE path=?", (path,)).fetchone():
        inside = lambda p: p.startswith(folder + "/")  # noqa: E731
        outs, ins = {}, {}
        for src, dst in ix.db.execute("SELECT src, dst FROM file_deps WHERE src LIKE ? OR dst LIKE ?",
                                      (folder + "/%", folder + "/%")):
            if inside(src) and not inside(dst):
                outs[dst] = outs.get(dst, 0) + 1
            elif inside(dst) and not inside(src):
                ins[src] = ins.get(src, 0) + 1
        top = lambda d: [{"file": k, "edges": v} for k, v in sorted(d.items(), key=lambda x: -x[1])]  # noqa: E731
        return {"folder": folder, "imports": top(outs) if direction != "in" else [],
                "imported_by": top(ins) if direction != "out" else []}
    out = {"file": path, "imports": [], "imported_by": []}
    if direction in ("both", "out"):
        seen, frontier = {path}, [path]
        for d in range(depth):
            nxt = []
            for p in frontier:
                for (dst,) in ix.db.execute("SELECT dst FROM file_deps WHERE src=?", (p,)):
                    if dst not in seen:
                        seen.add(dst)
                        out["imports"].append({"file": dst, "hops": d + 1})
                        nxt.append(dst)
            frontier = nxt
    if direction in ("both", "in"):
        seen, frontier = {path}, [path]
        for d in range(depth):
            nxt = []
            for p in frontier:
                for (src,) in ix.db.execute("SELECT src FROM file_deps WHERE dst=?", (p,)):
                    if src not in seen:
                        seen.add(src)
                        out["imported_by"].append({"file": src, "hops": d + 1})
                        nxt.append(src)
            frontier = nxt
    return out


GO_IFACE_METHOD_RX = re.compile(r"^\s*([A-Z]\w*)\s*\(", re.M)


def find_implementations(ix: CodeIndex, name: str) -> dict:
    """Concrete implementations of an interface / abstract class / trait (and of one of its methods with
    `Iface.method`), including Go's implicit interface satisfaction."""
    iface, _, method = name.partition(".")
    impls = [{"class": r["cls"], "at": r["path"]} for r in ix.db.execute(
        "SELECT DISTINCT cls, path FROM bases WHERE base=?", (iface,))]
    row = ix.db.execute("SELECT path, start_line, end_line, kind FROM symbols WHERE name=? AND kind IN "
                        "('interface','trait','class','struct') LIMIT 1", (iface,)).fetchone()
    if row and row["path"].endswith(".go") and row["kind"] == "interface":
        f = Files(ix)
        body = "\n".join(f.lines(row["path"])[row["start_line"]:row["end_line"] - 1])
        needed = set(GO_IFACE_METHOD_RX.findall(body))
        if needed:
            by_recv: dict[str, set] = defaultdict(set)
            where = {}
            for r in ix.db.execute("SELECT parent, name, path FROM symbols WHERE kind='method' AND parent!='' AND path LIKE '%.go'"):
                by_recv[r["parent"]].add(r["name"])
                where[r["parent"]] = r["path"]
            impls += [{"class": t, "at": where[t], "implicit": True} for t, ms in by_recv.items()
                      if needed <= ms and t != iface]
    methods = []
    if method:
        for im in impls:
            for r in ix.db.execute("SELECT path, qualname, start_line FROM symbols WHERE name=? AND (parent=? OR parent LIKE ?)",
                                   (method, im["class"], "%." + im["class"])):
                methods.append(f"{r['path']}:{r['start_line']} {r['qualname']}")
    return {"interface": iface, "implementations": impls, "method_implementations": methods}


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
    declared = []
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
                declared += [x.strip(" '\"") for x in (m.group(1) or m.group(m.lastindex) or "").split(",") if x.strip()]
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
    unresolved = Counter()
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
