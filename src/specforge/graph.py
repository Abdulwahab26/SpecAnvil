"""Code graphs built from the index: resolved file imports, call graph, inheritance.

Import resolution is heuristic but language-aware: relative and aliased JS/TS paths, Python
relative/absolute modules and packages, Java/Kotlin/C#/PHP packages and namespaces, Go package
directories, Rust crate paths, Ruby require_relative. Unresolved imports (third-party packages)
are simply not edges.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import PurePosixPath

JS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".d.ts")
JS_SRC_RX = re.compile(r"""(?:from\s+|require\(\s*|import\(\s*|import\s+)['"]([^'"]+)['"]""")
PY_FROM_RX = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)")
PY_IMPORT_RX = re.compile(r"^\s*import\s+([\w.]+)")
JVM_RX = re.compile(r"^\s*(?:import|using)\s+(?:static\s+)?([\w.]+?)(\.\*)?\s*;?\s*$")
GO_RX = re.compile(r"""["']([\w./-]+)["']""")
RUST_RX = re.compile(r"^\s*(?:pub\s+)?use\s+(crate|super|self)?(?:::)?([\w:]+)")
RUBY_RX = re.compile(r"""require_relative\s*\(?\s*['"]([^'"]+)['"]""")
PHP_RX = re.compile(r"^\s*use\s+([\w\\]+)")
FANOUT_CAP = 25


class Resolver:
    def __init__(self, ix):
        rows = ix.db.execute("SELECT path, lang, package FROM files WHERE parser!='text'").fetchall()
        self.paths = {r["path"] for r in rows}
        self.lang = {r["path"]: r["lang"] for r in rows}
        self.by_noext: dict[str, str] = {}
        self.by_dotted: dict[str, list[str]] = defaultdict(list)
        self.by_package: dict[str, list[str]] = defaultdict(list)
        self.by_dir: dict[str, list[str]] = defaultdict(list)
        for r in rows:
            p = r["path"]
            noext = re.sub(r"(\.d)?\.\w+$", "", p)
            self.by_noext.setdefault(noext, p)
            dotted = noext.replace("/", ".")
            if dotted.endswith(".__init__"):
                dotted = dotted[: -len(".__init__")]
            parts = dotted.split(".")
            for i in range(len(parts)):
                self.by_dotted[".".join(parts[i:])].append(p)
            if r["package"]:
                self.by_package[r["package"]].append(p)
            self.by_dir[str(PurePosixPath(p).parent)].append(p)
        self.go_module = ""
        try:
            gm = (ix.root / "go.mod").read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"^module\s+(\S+)", gm, re.M)
            self.go_module = m.group(1) if m else ""
        except OSError:
            pass

    def _js(self, src: str, spec: str) -> list[str]:
        if spec.startswith("."):
            base = PurePosixPath(src).parent / spec
        elif spec.startswith(("@/", "~/")):
            # alias root = the importing file's own src/ folder (monorepos: frontend/src, apps/web/src), else ./src
            parts = PurePosixPath(src).parts
            root = PurePosixPath(*parts[:parts.index("src") + 1]) if "src" in parts[:-1] else PurePosixPath("src")
            base = root / spec[2:]
        elif spec.startswith("/"):
            base = PurePosixPath(spec.lstrip("/"))
        else:
            return []
        norm = _normpath(str(base))
        for cand in [norm] + [norm + e for e in JS_EXT] + [norm + "/index" + e for e in JS_EXT]:
            if cand in self.paths:
                return [cand]
        hit = self.by_noext.get(norm) or self.by_noext.get(norm + "/index")
        if hit:
            return [hit]
        # alias without src/: try any path ending with the spec
        if not spec.startswith(("@/", "~/")):
            return []
        tail = spec[2:].lstrip("./")
        cands = [v for k, v in self.by_noext.items() if (k.endswith("/" + tail) or k.endswith("/" + tail + "/index"))
                 and self.lang.get(v) in ("javascript", "typescript", "tsx", "vue", "svelte")]
        def shared(v: str) -> int:
            return len(os.path.commonprefix([v.split("/"), src.split("/")]))
        return sorted(cands, key=lambda v: (-shared(v), len(v)))[:1]

    def _py(self, src: str, line: str) -> list[str]:
        m = PY_FROM_RX.match(line)
        if m:
            dots, mod, names = m.groups()
            if dots:
                base = PurePosixPath(src).parent
                for _ in range(len(dots) - 1):
                    base = base.parent
                parts = [str(base)] if str(base) != "." else []
                full = "/".join(parts + ([mod.replace(".", "/")] if mod else []))
                out = self._py_path(full)
                if not out and not mod:  # from . import a, b  -> sibling modules
                    for n in re.findall(r"\w+", names):
                        out += self._py_path("/".join(parts + [n]))
                elif out and not mod.strip():
                    pass
                # from .pkg import module  -> the submodule if it exists
                for n in re.findall(r"\w+", names.split(" as ")[0])[:6]:
                    sub = self._py_path(full + "/" + n) if full else self._py_path(n)
                    out += [x for x in sub if x not in out]
                return out[:FANOUT_CAP]
            out = self._dotted(mod)[:1]
            for n in re.findall(r"\w+", names.split(" as ")[0])[:8]:  # from pkg import submodule
                out += [x for x in self._dotted(f"{mod}.{n}") if x not in out]
            return out[:FANOUT_CAP]
        m = PY_IMPORT_RX.match(line)
        return self._dotted(m.group(1)) if m else []

    def _py_path(self, full: str) -> list[str]:
        full = _normpath(full)
        for cand in (full + ".py", full + "/__init__.py", full + ".pyi"):
            if cand in self.paths:
                return [cand]
        return []

    def _dotted(self, mod: str) -> list[str]:
        hits = self.by_dotted.get(mod, [])
        return hits[:1] if len(hits) >= 1 else []

    def resolve(self, src: str, line: str) -> list[str]:
        lang = self.lang.get(src, "")
        if lang in ("javascript", "typescript", "tsx", "vue", "svelte"):
            m = JS_SRC_RX.search(line)
            return self._js(src, m.group(1)) if m else []
        if lang == "python":
            return self._py(src, line)
        if lang in ("java", "kotlin", "scala", "csharp", "groovy", "dart"):
            m = JVM_RX.match(line)
            if not m:
                return []
            name, wildcard = m.group(1), m.group(2)
            if wildcard or lang == "csharp":
                pkg = self.by_package.get(name, [])
                if pkg:
                    return pkg[:FANOUT_CAP]
            hit = self._dotted(name)
            if hit:
                return hit
            return self.by_package.get(name.rsplit(".", 1)[0], [])[:FANOUT_CAP] if lang != "csharp" else []
        if lang == "go":
            out = []
            for spec in GO_RX.findall(line):
                if self.go_module and spec.startswith(self.go_module):
                    d = spec[len(self.go_module):].strip("/") or "."
                    out += [p for p in self.by_dir.get(d, []) if not p.endswith("_test.go")][:FANOUT_CAP]
            return out
        if lang == "rust":
            m = RUST_RX.match(line)
            if not m:
                return []
            segs = m.group(2).split("::")
            for k in range(len(segs), 0, -1):
                stem = "/".join(segs[:k])
                for cand in (f"src/{stem}.rs", f"src/{stem}/mod.rs", f"{stem}.rs"):
                    if cand in self.paths:
                        return [cand]
            return []
        if lang == "ruby":
            m = RUBY_RX.search(line)
            if m:
                base = _normpath(str(PurePosixPath(src).parent / m.group(1)))
                return [base + ".rb"] if base + ".rb" in self.paths else []
            return []
        if lang == "php":
            m = PHP_RX.match(line)
            if m:
                name = m.group(1).replace("\\", ".")
                return self._dotted(name) or self.by_package.get(name.rsplit(".", 1)[0], [])[:FANOUT_CAP]
        return []


def _normpath(p: str) -> str:
    out: list[str] = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return "/".join(out)


def rebuild_file_deps(ix) -> int:
    res = Resolver(ix)
    edges = set()
    for row in ix.db.execute("SELECT path, source FROM imports"):
        for dst in res.resolve(row["path"], row["source"]):
            if dst != row["path"]:
                edges.add((row["path"], dst))
    ix.db.execute("DELETE FROM file_deps")
    ix.db.executemany("INSERT OR IGNORE INTO file_deps VALUES(?,?)", sorted(edges))
    return len(edges)


# =============================================================================== call graph & analyses

import fnmatch  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from collections import Counter, deque  # noqa: E402

CALLABLE_KINDS = ("function", "method", "class", "struct")


class CodeGraph:
    """Resolves call edges by name with scope preference: same file > imported files > unique name."""

    def __init__(self, ix):
        self.ix = ix
        db = ix.db
        self.sym = {r["id"]: dict(r) for r in db.execute(
            "SELECT id, path, name, qualname, kind, start_line, end_line, signature, exported, tokens, parent FROM symbols")}
        self.by_name: dict[str, list[int]] = defaultdict(list)
        for sid, s in self.sym.items():
            if s["kind"] in CALLABLE_KINDS:
                self.by_name[s["name"]].append(sid)
        self.deps: dict[str, set] = defaultdict(set)
        self.rdeps: dict[str, set] = defaultdict(set)
        for r in db.execute("SELECT src, dst FROM file_deps"):
            self.deps[r[0]].add(r[1])
            self.rdeps[r[1]].add(r[0])

    def resolve(self, caller_path: str, callee: str, recv: str = "") -> list[int]:
        cands = self.by_name.get(callee, [])
        if not cands:
            return []
        if recv and recv not in ("self", "this", "cls", "super", "base", "me"):
            # receiver names a module/file (crud.create_user) or a class (Order.create): prefer that target
            hit = [c for c in cands if PurePosixPath(self.sym[c]["path"]).stem == recv
                   or self.sym[c]["parent"] == recv or self.sym[c]["parent"].endswith("." + recv)]
            if hit:
                return hit[:1]
        same = [c for c in cands if self.sym[c]["path"] == caller_path]
        if same:
            return same[:1]
        imported = [c for c in cands if self.sym[c]["path"] in self.deps.get(caller_path, ())]
        if imported:
            return imported[:2]
        return cands[:1] if len(cands) == 1 else []

    def edges(self):
        """Resolved (caller_id, callee_id) pairs."""
        for r in self.ix.db.execute("SELECT caller_id, path, callee, recv FROM calls WHERE caller_id IS NOT NULL"):
            for t in self.resolve(r["path"], r["callee"], r["recv"] or ""):
                if t != r["caller_id"]:
                    yield r["caller_id"], t

    def callees(self, sid: int) -> list[tuple[int, int]]:
        s = self.sym[sid]
        out = []
        for r in self.ix.db.execute("SELECT callee, line, recv FROM calls WHERE caller_id=?", (sid,)):
            for t in self.resolve(s["path"], r["callee"], r["recv"] or ""):
                if t != sid:
                    out.append((t, r["line"]))
        return out

    def callers(self, sid: int) -> list[tuple[int, int]]:
        s = self.sym[sid]
        out = []
        for r in self.ix.db.execute("SELECT caller_id, path, line, recv FROM calls WHERE callee=? AND caller_id IS NOT NULL",
                                    (s["name"],)):
            if sid in self.resolve(r["path"], s["name"], r["recv"] or "") and r["caller_id"] != sid:
                out.append((r["caller_id"], r["line"]))
        return out

    def label(self, sid: int) -> str:
        s = self.sym[sid]
        return f"[{sid}] {s['qualname']} ({s['path']}:{s['start_line']})"


def call_hierarchy(ix, sid: int, direction: str = "callers", depth: int = 2, limit: int = 60) -> str:
    g = CodeGraph(ix)
    if sid not in g.sym:
        raise ValueError(f"No symbol id {sid}")
    step = g.callers if direction == "callers" else g.callees
    lines = [f"{direction} of {g.label(sid)}"]
    seen = {sid}
    q = deque([(sid, 0)])
    count = 0
    while q and count < limit:
        cur, d = q.popleft()
        if d >= depth:
            continue
        for nxt, line in step(cur):
            if nxt in seen:
                continue
            seen.add(nxt)
            count += 1
            lines.append(f"{'  ' * (d + 1)}{g.label(nxt)}  via line {line}")
            q.append((nxt, d + 1))
    if len(lines) == 1:
        lines.append("  (none found: may be called dynamically, via reflection/DI, or only from outside this repo)")
    return "\n".join(lines)


def class_hierarchy(ix, name: str) -> dict:
    db = ix.db
    up, frontier = [], [name]
    seen = {name}
    while frontier:
        nxt = []
        for c in frontier:
            for r in db.execute("SELECT DISTINCT base, path FROM bases WHERE cls=?", (c,)):
                if r["base"] not in seen:
                    seen.add(r["base"])
                    loc = db.execute("SELECT path, start_line FROM symbols WHERE name=? AND kind IN "
                                     "('class','interface','struct','trait') LIMIT 1", (r["base"],)).fetchone()
                    up.append({"class": c, "extends": r["base"],
                               "defined_at": f"{loc['path']}:{loc['start_line']}" if loc else "external"})
                    nxt.append(r["base"])
        frontier = nxt
    down, frontier, seen = [], [name], {name}
    while frontier and len(down) < 200:
        nxt = []
        for c in frontier:
            for r in db.execute("SELECT DISTINCT cls, path FROM bases WHERE base=?", (c,)):
                if r["cls"] not in seen:
                    seen.add(r["cls"])
                    down.append({"class": r["cls"], "extends": c, "at": r["path"]})
                    nxt.append(r["cls"])
        frontier = nxt
    return {"class": name, "ancestors": up, "descendants": down}


def importance(ix, limit: int = 25, damping: float = 0.85, iterations: int = 30) -> list[dict]:
    """PageRank over the resolved call graph plus file-import edges (spread to the file's top-level symbols)."""
    key = "pagerank:" + (ix.db.execute("SELECT value FROM meta WHERE key='last_index'").fetchone() or ["0"])[0]
    cached = ix.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if cached:
        return json.loads(cached[0])[:limit]
    g = CodeGraph(ix)
    nodes = [sid for sid, s in g.sym.items() if s["kind"] in CALLABLE_KINDS]
    idx = {sid: i for i, sid in enumerate(nodes)}
    out_edges: dict[int, Counter] = defaultdict(Counter)
    for a, b in g.edges():
        if a in idx and b in idx:
            out_edges[idx[a]][idx[b]] += 1
    n = len(nodes)
    if n == 0:
        return []
    rank = [1.0 / n] * n
    for _ in range(iterations):
        new = [(1 - damping) / n] * n
        dangling = 0.0
        for i in range(n):
            outs = out_edges.get(i)
            if not outs:
                dangling += rank[i]
                continue
            total = sum(outs.values())
            share = damping * rank[i] / total
            for j, w in outs.items():
                new[j] += share * w
        spread = damping * dangling / n
        rank = [x + spread for x in new]
    fan_in = Counter(j for outs in out_edges.values() for j in outs)
    top = sorted(range(n), key=lambda i: -rank[i])[:200]
    from .index import TEST_RX
    res = [{"id": nodes[i], "ref": f"{g.sym[nodes[i]]['path']}::{g.sym[nodes[i]]['qualname']}",
            "kind": g.sym[nodes[i]]["kind"], "score": round(rank[i] * n, 2), "callers": fan_in[i]}
           for i in top if not TEST_RX.search(g.sym[nodes[i]]["path"])]
    ix.db.execute("DELETE FROM meta WHERE key LIKE 'pagerank:%'")
    ix.db.execute("INSERT INTO meta VALUES(?,?)", (key, json.dumps(res)))
    ix.db.commit()
    return res[:limit]


ENTRY_HINT_RX = re.compile(r"^\s*(@|\[)(app|router|api|bp|blueprint|route|Get|Post|Put|Patch|Delete|Http|Function|"
                           r"FunctionName|Test|Fact|Theory|pytest|fixture|click|command|celery|task|receiver|"
                           r"shared_task|validator|field_validator|root_validator|property|cached_property|"
                           r"staticmethod|classmethod|override|Override|Bean|Component|Service|Controller|"
                           r"RestController|Injectable|Module|EventListener|Scheduled|KafkaListener|"
                           r"Subscribe|OnEvent|Cron|Mutation|Query|Resolver|Input|Output|HostListener|ViewChild)", re.I)
FRAMEWORK_NAMES = {"main", "setup", "setUp", "tearDown", "setUpClass", "tearDownClass", "setUpTestData", "handle",
                   "run", "execute", "init", "configure", "register", "ready", "save", "clean", "delete",
                   "get_queryset", "get_context_data", "dispatch", "render", "build", "toString", "equals",
                   "hashCode", "ServeHTTP", "String", "Error", "Meta", "Config", "default", "constructor",
                   "componentDidMount", "ngOnInit", "ngOnDestroy", "connectedCallback", "Dispose", "Main"}


def dead_code(ix, limit: int = 50, include_exported: bool = False) -> list[dict]:
    """Symbols nothing references. Index prefilter (no incoming call edges) + one batched word search to
    verify that the name appears nowhere except its own definition(s)."""
    from .index import GENERATED_RX, NONPROD_RX, TEST_RX
    db = ix.db
    called = {r[0] for r in db.execute("SELECT DISTINCT callee FROM calls")}
    rows = db.execute("SELECT id, path, name, qualname, kind, start_line, exported, parent FROM symbols WHERE kind IN "
                      "('function','method','class','struct','interface','variable','constant','type','enum')").fetchall()
    defs = Counter(r["name"] for r in rows)
    cands = []
    for r in rows:
        n, p = r["name"], r["path"]
        if n in called or n in FRAMEWORK_NAMES or len(n) < 3 or (n.startswith("__") and n.endswith("__")):
            continue
        if TEST_RX.search(p) or GENERATED_RX.search(p) or NONPROD_RX.search(p) or n.startswith(("test", "Test")):
            continue
        if (r["exported"] or (p.endswith(".go") and n[:1].isupper())) and not include_exported:
            continue
        try:
            prev = (ix.root / p).read_text(encoding="utf-8", errors="replace").splitlines()[max(0, r["start_line"] - 3):r["start_line"] - 1]
        except OSError:
            prev = []
        if any(ENTRY_HINT_RX.match(x) or re.match(r"^\s*(@[\w.]+|\[[A-Z]\w*)", x) for x in prev):
            continue  # decorated/annotated: likely registered with a framework
        cands.append(r)
    if not cands:
        return []
    counts = _word_counts(ix, sorted({r["name"] for r in cands}))
    out = []
    for r in cands:
        uses = counts.get(r["name"], 0) - defs[r["name"]]
        if uses <= 0:
            conf = "high" if (r["name"].startswith("_") or r["kind"] in ("function", "method", "variable", "constant")
                              and not r["exported"]) else "medium"
            if r["parent"] and r["kind"] == "method":
                conf = "medium"  # could be an override invoked by a framework
            out.append({"id": r["id"], "ref": f"{r['path']}::{r['qualname']}", "kind": r["kind"],
                        "line": r["start_line"], "confidence": conf})
    out.sort(key=lambda x: (x["confidence"] != "high", x["ref"]))
    return out[:limit]


def _word_counts(ix, names: list[str]) -> Counter:
    """Occurrences of each name as a whole word across indexed code files (one ripgrep pass when available)."""
    files = [r[0] for r in ix.db.execute("SELECT path FROM files WHERE parser!='text'")]
    allowed = set(files)
    c: Counter = Counter()
    rg = shutil.which("rg")
    for i in range(0, len(names), 400):
        chunk = names[i:i + 400]
        if rg:
            cmd = [rg, "-o", "-w", "--no-filename", "--no-line-number", "--color", "never"]
            for n in chunk:
                cmd += ["-e", re.escape(n)]
            cmd += ["--", "."]
            try:
                proc = subprocess.run(cmd, cwd=ix.root, capture_output=True, text=True, timeout=120,
                                      encoding="utf-8", errors="replace")
                if proc.returncode in (0, 1):
                    c.update(proc.stdout.split())
                    continue
            except (OSError, subprocess.SubprocessError):
                pass
        rx = re.compile(r"\b(" + "|".join(re.escape(n) for n in chunk) + r")\b")
        for p in allowed:
            try:
                c.update(rx.findall((ix.root / p).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return c


def cycles(ix, level: str = "file", limit: int = 20) -> list[list[str]]:
    """Strongly connected components in the import graph (Tarjan, iterative)."""
    edges: dict[str, set] = defaultdict(set)
    rows = ix.db.execute("SELECT src, dst FROM file_deps").fetchall()
    if level == "module":
        mod = {r[0]: r[1] for r in ix.db.execute("SELECT path, module FROM files")}
        for s, d in rows:
            if mod.get(s) != mod.get(d):
                edges[mod.get(s, s)].add(mod.get(d, d))
    else:
        for s, d in rows:
            edges[s].add(d)
    index, low, on, stack, sccs = {}, {}, set(), [], []
    counter = [0]
    for root in list(edges):
        if root in index:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edges.get(w, ())))))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    sccs.append(sorted(comp))
    sccs.sort(key=len, reverse=True)
    return sccs[:limit]


def coupling(ix, limit: int = 30) -> list[dict]:
    """Per module: afferent (Ca) and efferent (Ce) coupling and instability I = Ce / (Ca + Ce)."""
    mod = {r[0]: r[1] for r in ix.db.execute("SELECT path, module FROM files")}
    ca: dict[str, set] = defaultdict(set)
    ce: dict[str, set] = defaultdict(set)
    for s, d in ix.db.execute("SELECT src, dst FROM file_deps"):
        ms, md = mod.get(s), mod.get(d)
        if ms and md and ms != md:
            ce[ms].add(md)
            ca[md].add(ms)
    out = []
    for m in set(ca) | set(ce):
        a, e = len(ca[m]), len(ce[m])
        out.append({"module": m, "afferent": a, "efferent": e, "instability": round(e / (a + e), 2) if a + e else 0.0,
                    "depends_on": sorted(ce[m])[:8], "used_by": sorted(ca[m])[:8]})
    out.sort(key=lambda x: -(x["afferent"] + x["efferent"]))
    return out[:limit]


DEFAULT_LAYERS = [  # top (may depend on anything below) -> bottom (depends on nothing above)
    {"name": "presentation", "paths": ["*/controllers/*", "*/controller/*", "*/routes/*", "*/routers/*", "*/views/*",
                                       "*/pages/*", "*/components/*", "*/handlers/*", "*/api/*", "*/endpoints/*",
                                       "*Controller.*", "*/views.py", "*/routes.py", "*/urls.py"]},
    {"name": "application", "paths": ["*/services/*", "*/service/*", "*/usecases/*", "*/use_cases/*",
                                      "*/application/*", "*Service.*", "*/services.py", "*/service.py"]},
    {"name": "infrastructure", "paths": ["*/repositories/*", "*/repository/*", "*/infrastructure/*", "*/infra/*",
                                         "*/dal/*", "*/adapters/*", "*Repository.*", "*/crud.py",
                                         "*/repository.py", "*/repositories.py", "*/persistence/*"]},
    {"name": "domain", "paths": ["*/domain/*", "*/entities/*", "*/entity/*", "*/models/*", "*/model/*",
                                 "*/models.py", "*/model.py", "*/entities.py", "*/domain.py"]},
]
FORBIDDEN_EXTRA: set = set()  # extra (from_layer, to_layer) pairs a team wants to forbid, via config


def layer_of(path: str, layers: list[dict]) -> str | None:
    p = "/" + path
    for L in layers:
        if any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(path, pat) for pat in L["paths"]):
            return L["name"]
    return None


def layer_violations(ix, layers: list[dict] | None = None, limit: int = 50) -> dict:
    from .config import load_config
    layers = layers or load_config(ix.root).get("layers") or DEFAULT_LAYERS
    order = {L["name"]: i for i, L in enumerate(layers)}
    out, assigned = [], Counter()
    for s, d in ix.db.execute("SELECT src, dst FROM file_deps"):
        ls, ld = layer_of(s, layers), layer_of(d, layers)
        if ls:
            assigned[ls] += 0
        if not ls or not ld or ls == ld:
            continue
        forbidden = FORBIDDEN_EXTRA | {tuple(x) for x in (load_config(ix.root).get("layer_forbid") or [])}
        if order[ld] < order[ls] or (ls, ld) in forbidden:
            imp = ix.db.execute("SELECT source FROM imports WHERE path=? LIMIT 50", (s,)).fetchall()
            stem = PurePosixPath(d).stem
            line = next((r[0] for r in imp if stem in r[0]), "")
            out.append({"from": s, "from_layer": ls, "to": d, "to_layer": ld, "import": line[:160]})
    files = [r[0] for r in ix.db.execute("SELECT path FROM files WHERE parser!='text'")]
    coverage = Counter(layer_of(f, layers) or "unassigned" for f in files)
    return {"layers": [L["name"] for L in layers], "files_per_layer": dict(coverage), "violations": out[:limit],
            "rule": "a layer may depend only on layers below it (presentation > application > infrastructure > domain)",
            "configure": "set 'layers' in .specforge/config.json: [{name, paths:[glob,...]}, ...] top to bottom"}


def pr_risk(ix, base: str = "HEAD") -> dict:
    """Composite 0-1 risk for a change: complexity, blast radius (callers), churn, test exposure, size."""
    from .analysis import churn
    from .index import TEST_RX
    from .testing import BRANCH_RX
    changed = ix.changed_symbols(base)
    if not changed:
        return {"risk": 0.0, "level": "none", "symbols": [], "note": f"no changes against {base}"}
    g = CodeGraph(ix)
    ch = churn(ix, 90)
    test_text = ""
    for (p,) in ix.db.execute("SELECT path FROM files WHERE parser!='text'"):
        if TEST_RX.search(p):
            try:
                test_text += (ix.root / p).read_text(encoding="utf-8", errors="replace") + "\n"
            except OSError:
                pass
    items, max_risk = [], 0.0
    for c in changed:
        sid = c.get("id")
        if not sid or sid not in g.sym:
            continue
        s = g.sym[sid]
        try:
            body = "\n".join((ix.root / s["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
                             [s["start_line"] - 1:s["end_line"]])
        except OSError:
            body = ""
        branches = len(BRANCH_RX.findall(body))
        callers = len(g.callers(sid))
        tested = bool(re.search(r"\b" + re.escape(s["name"]) + r"\b", test_text)) if len(s["name"]) > 2 else False
        f_complexity = min(1.0, branches / 12)
        f_blast = min(1.0, callers / 15)
        f_churn = min(1.0, (ch.get(s["path"], 0) ** 0.5) / 30)
        f_untested = 0.0 if tested or TEST_RX.search(s["path"]) else 1.0
        f_size = min(1.0, (s["end_line"] - s["start_line"]) / 150)
        risk = round(0.25 * f_complexity + 0.25 * f_blast + 0.15 * f_churn + 0.25 * f_untested + 0.10 * f_size, 2)
        max_risk = max(max_risk, risk)
        tips = []
        if f_untested:
            tips.append("no test references this symbol: add a test")
        if callers >= 5:
            tips.append(f"{callers} callers: review call sites (call_hierarchy)")
        if branches >= 8:
            tips.append("complex: ask for focused review")
        items.append({"ref": f"{s['path']}::{s['qualname']}", "risk": risk, "branches": branches, "callers": callers,
                      "tested": tested, "churn_90d": ch.get(s["path"], 0), "tips": tips})
    items.sort(key=lambda x: -x["risk"])
    overall = round(min(1.0, 0.6 * max_risk + 0.4 * (sum(i["risk"] for i in items) / max(len(items), 1))
                        + 0.02 * max(0, len(items) - 10)), 2) if items else 0.0
    level = "high" if overall >= 0.6 else "medium" if overall >= 0.35 else "low"
    return {"risk": overall, "level": level, "symbols_changed": len(items), "symbols": items[:25],
            "weights": "complexity .25, callers .25, untested .25, churn .15, size .10"}
