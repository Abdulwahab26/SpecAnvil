"""Endpoints, flow edges, parity, columns, dependency graph, implementations.

Part of the insights toolkit; import these through `specanvil.insights`."""

from __future__ import annotations

import re
from collections import defaultdict, deque

from .graph import CodeGraph
from .index import TEST_RX, CodeIndex, split_terms
from .insights import (
    Files,
    _folder,
    _prod,
)

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
        outs: dict; ins: dict
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
