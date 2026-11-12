"""Runtime signal: ingest traces (OpenTelemetry) and map spans to indexed symbols with per-symbol latency.

Accepted inputs (plain or .gz):
  * OTLP JSON export: {"resourceSpans": [{"scopeSpans": [{"spans": [...]}]}]} (one document, or one per line)
  * flat JSON lines:  {"name": ..., "file": ..., "function": ..., "line": ..., "duration_ms": ...}

A span is mapped to a symbol by, in order:
  1. code attributes  (code.filepath / code.file.path, code.function / code.function.name, code.lineno /
     code.line.number, code.namespace)
  2. HTTP server route (http.route / url.template + http.method / http.request.method) -> the route handler
  3. span name equal to a qualified name ("UserService.create") or a unique function name

Everything stays in the local index (.specanvil); span attributes other than the ones above are not stored.
"""

from __future__ import annotations

import gzip
import json
import time
from collections import defaultdict
from pathlib import Path

from .config import debug_exc
from .index import CodeIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_spans (
  path TEXT, qualname TEXT, source TEXT, count INTEGER, total_ms REAL, samples TEXT,
  first_seen REAL, last_seen REAL, PRIMARY KEY (path, qualname, source)
);
CREATE TABLE IF NOT EXISTS runtime_imports (ts REAL, file TEXT, source TEXT, spans INTEGER, mapped INTEGER);
"""
MAX_SAMPLES = 500
FUNC_KINDS = ("function", "method")


def _attrs(span: dict) -> dict:
    a = span.get("attributes") or {}
    if isinstance(a, dict):
        return a
    out = {}
    for kv in a:  # OTLP: [{"key": k, "value": {"stringValue": v}}]
        v = kv.get("value", {})
        out[kv.get("key")] = next(iter(v.values()), None) if isinstance(v, dict) else v
    return out


def _iter_docs(path: Path):
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return
    try:
        yield json.loads(text)
        return
    except json.JSONDecodeError:
        pass
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            try:
                yield json.loads(ln)
            except json.JSONDecodeError:
                continue


def _spans(path: Path):
    """Yields normalised spans: {name, file, function, line, namespace, route, method, ms, end_ts}."""
    for doc in _iter_docs(path):
        docs = doc if isinstance(doc, list) else [doc]
        for d in docs:
            if "resourceSpans" in d:
                for rs in d.get("resourceSpans", []):
                    for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                        for sp in ss.get("spans", []):
                            a = _attrs(sp)
                            try:
                                ms = (int(sp["endTimeUnixNano"]) - int(sp["startTimeUnixNano"])) / 1e6
                                end = int(sp["endTimeUnixNano"]) / 1e9
                            except (KeyError, ValueError, TypeError):
                                ms, end = None, time.time()
                            yield {"name": sp.get("name", ""), "ms": ms, "end_ts": end,
                                   "file": a.get("code.filepath") or a.get("code.file.path"),
                                   "function": a.get("code.function") or a.get("code.function.name"),
                                   "line": a.get("code.lineno") or a.get("code.line.number"),
                                   "namespace": a.get("code.namespace"),
                                   "route": a.get("http.route") or a.get("url.template"),
                                   "method": a.get("http.method") or a.get("http.request.method")}
            elif "name" in d or "function" in d:
                yield {"name": d.get("name", ""), "ms": d.get("duration_ms"), "end_ts": d.get("ts", time.time()),
                       "file": d.get("file"), "function": d.get("function"), "line": d.get("line"),
                       "namespace": d.get("namespace"), "route": d.get("route"), "method": d.get("method")}


class _Mapper:
    def __init__(self, ix: CodeIndex):
        self.ix = ix
        self.paths = [r[0] for r in ix.db.execute("SELECT path FROM files")]
        self.by_name: dict[str, list] = defaultdict(list)
        self.by_qual: dict[str, list] = defaultdict(list)
        self.by_path: dict[str, list] = defaultdict(list)
        for r in ix.db.execute(f"SELECT path, name, qualname, start_line, end_line FROM symbols "
                               f"WHERE kind IN {FUNC_KINDS}"):
            row = tuple(r)
            self.by_name[r["name"]].append(row)
            self.by_qual[r["qualname"]].append(row)
            self.by_path[r["path"]].append(row)
        self._routes: dict | None = None
        self._file_cache: dict[str, str | None] = {}

    def _rel(self, f: str) -> str | None:
        if f in self._file_cache:
            return self._file_cache[f]
        f2 = f.replace("\\", "/")
        hit = None
        if f2 in self.paths:
            hit = f2
        else:
            cands = [p for p in self.paths if f2.endswith("/" + p) or p.endswith(f2.lstrip("./"))]
            hit = max(cands, key=len) if cands else None
        self._file_cache[f] = hit
        return hit

    def routes(self) -> dict:
        if self._routes is None:
            self._routes = {}
            try:
                from .facts import Facts
                for e in Facts(self.ix).get("endpoints"):
                    self._routes[(e["method"].upper(), e["path"].rstrip("/") or "/")] = e
            except Exception:  # noqa: BLE001 - endpoints are best-effort
                debug_exc("runtime:133")
                pass
        return self._routes

    def map(self, s: dict) -> tuple[str, str] | None:
        fn = (s.get("function") or "").split(".")[-1]
        if s.get("file"):
            rel = self._rel(str(s["file"]))
            if rel:
                cands = [c for c in self.by_path.get(rel, []) if not fn or c[1] == fn]
                if s.get("line"):
                    ln = int(s["line"])
                    inside = [c for c in cands if c[3] <= ln <= c[4]]
                    cands = sorted(inside, key=lambda c: c[4] - c[3]) or cands
                if cands:
                    return cands[0][0], cands[0][2]
        if s.get("route"):
            r = (s.get("method") or "").upper(), str(s["route"]).rstrip("/") or "/"
            e = self.routes().get(r) or next((v for (m, p), v in self.routes().items() if p == r[1]), None)
            if not e:  # span routes carry app-level prefixes (/api/v1) the router declaration doesn't
                cands = [((m == r[0]), len(p), v) for (m, p), v in self.routes().items()
                         if p != "/" and r[1].endswith(p) and (not r[0] or not m or m in (r[0], "ANY"))]
                e = max(cands, key=lambda c: (c[0], c[1]))[2] if cands else None
            if e:
                rel = e["at"].split(":")[0]
                h = e["handler"].rsplit(".", 1)[-1]
                c = next((c for c in self.by_path.get(rel, []) if c[1] == h), None)
                if c:
                    return c[0], c[2]
        for key in filter(None, [f"{s.get('namespace')}.{fn}" if s.get("namespace") and fn else None,
                                 s.get("function"), s.get("name")]):
            q = self.by_qual.get(key)
            if q and len(q) == 1:
                return q[0][0], q[0][2]
            n = self.by_name.get(key)
            if n and len(n) == 1:
                return n[0][0], n[0][2]
        return None


def import_trace(ix: CodeIndex, file: str | Path, source: str = "otel") -> dict:
    """Ingest a trace file; aggregates count, total and a bounded latency sample per symbol."""
    path = Path(file)
    if not path.is_absolute():
        path = ix.root / path
    if not path.is_file():
        raise ValueError(f"No trace file at {path}")
    ix.db.executescript(SCHEMA)
    m = _Mapper(ix)
    agg: dict[tuple, dict] = {}
    total = mapped = 0
    unmapped: dict[str, int] = defaultdict(int)
    for s in _spans(path):
        total += 1
        hit = m.map(s)
        if not hit:
            unmapped[s.get("name") or "?"] += 1
            continue
        mapped += 1
        a = agg.setdefault(hit, {"count": 0, "ms": [], "first": s["end_ts"], "last": s["end_ts"]})
        a["count"] += 1
        if s.get("ms") is not None:
            a["ms"].append(float(s["ms"]))
        a["first"], a["last"] = min(a["first"], s["end_ts"]), max(a["last"], s["end_ts"])
    with ix.db:
        for (p, q), a in agg.items():
            row = ix.db.execute("SELECT count, total_ms, samples, first_seen, last_seen FROM runtime_spans "
                                "WHERE path=? AND qualname=? AND source=?", (p, q, source)).fetchone()
            samples = (json.loads(row["samples"]) if row else []) + a["ms"]
            samples = samples[-MAX_SAMPLES:]
            ix.db.execute("INSERT OR REPLACE INTO runtime_spans VALUES(?,?,?,?,?,?,?,?)",
                          (p, q, source, (row["count"] if row else 0) + a["count"],
                           (row["total_ms"] if row else 0) + sum(a["ms"]), json.dumps([round(x, 3) for x in samples]),
                           min(row["first_seen"], a["first"]) if row else a["first"],
                           max(row["last_seen"], a["last"]) if row else a["last"]))
        ix.db.execute("INSERT INTO runtime_imports VALUES(?,?,?,?,?)", (time.time(), str(path), source, total, mapped))
    top_unmapped = sorted(unmapped.items(), key=lambda x: -x[1])[:10]
    return {"file": str(path), "source": source, "spans": total, "mapped": mapped, "symbols": len(agg),
            "mapped_pct": round(100 * mapped / total, 1) if total else 0.0,
            "unmapped_top": [{"span": k, "count": v} for k, v in top_unmapped],
            "hint": None if not total or mapped / total > 0.5 else
            "Few spans carry code.* attributes; enable code attributes in your OTel SDK or name spans after functions."}


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 2)


def has_traces(ix: CodeIndex) -> bool:
    ix.db.executescript(SCHEMA)
    return bool(ix.db.execute("SELECT 1 FROM runtime_spans LIMIT 1").fetchone())


def hot_paths(ix: CodeIndex, limit: int = 20, name: str | None = None, sort_by: str = "count") -> list[dict]:
    """Symbols ranked by runtime hits (or p95 / total time) with p50 / p95 / max latency and contributing sources."""
    ix.db.executescript(SCHEMA)
    by: dict[tuple, dict] = {}
    for r in ix.db.execute("SELECT * FROM runtime_spans"):
        if name and name.lower() not in r["qualname"].lower():
            continue
        e = by.setdefault((r["path"], r["qualname"]), {"count": 0, "total_ms": 0.0, "ms": [], "sources": set(), "last": 0})
        e["count"] += r["count"]
        e["total_ms"] += r["total_ms"]
        e["ms"] += json.loads(r["samples"])
        e["sources"].add(r["source"])
        e["last"] = max(e["last"], r["last_seen"])
    out = [{"ref": f"{p}::{q}", "hits": e["count"], "p50_ms": _pct(e["ms"], 0.5), "p95_ms": _pct(e["ms"], 0.95),
            "max_ms": round(max(e["ms"]), 2) if e["ms"] else None, "total_ms": round(e["total_ms"], 1),
            "sources": sorted(e["sources"]), "last_seen": time.strftime("%Y-%m-%d %H:%M", time.localtime(e["last"]))}
           for (p, q), e in by.items()]
    key = {"count": lambda x: -x["hits"], "p95": lambda x: -(x["p95_ms"] or 0),
           "total": lambda x: -x["total_ms"]}.get(sort_by, lambda x: -x["hits"])
    return sorted(out, key=key)[:limit]


def unused_paths(ix: CodeIndex, days: int = 30, limit: int = 50) -> dict:
    """Functions with no runtime hits (or none within `days`) in files the traces do cover: reachable on paper,
    never executed in the observed traffic. Distinct from dead_code, which is static."""
    from .insights import _prod
    ix.db.executescript(SCHEMA)
    rows = ix.db.execute("SELECT path, qualname, MAX(last_seen) last FROM runtime_spans GROUP BY path, qualname").fetchall()
    if not rows:
        raise ValueError("No runtime traces yet. Call import_runtime_trace with an OTel export first.")
    seen = {(r["path"], r["qualname"]): r["last"] for r in rows}
    covered_files = {p for p, _ in seen}
    cutoff = time.time() - days * 86400
    never, stale = [], []
    for r in ix.db.execute(f"SELECT path, qualname, start_line FROM symbols WHERE kind IN {FUNC_KINDS}"):
        if r["path"] not in covered_files or not _prod(r["path"]):
            continue
        last = seen.get((r["path"], r["qualname"]))
        if last is None:
            never.append(f"{r['path']}::{r['qualname']}")
        elif last < cutoff:
            stale.append({"ref": f"{r['path']}::{r['qualname']}",
                          "last_seen": time.strftime("%Y-%m-%d", time.localtime(last))})
    return {"files_with_traces": len(covered_files), "never_executed": never[:limit], "never_count": len(never),
            "stale": stale[:limit], "stale_days": days,
            "note": "Only files that appear in the traces are considered, so untraced modules are not reported."}


def latency_of(ix: CodeIndex, path: str, qualname: str) -> dict | None:
    """p50 / p95 for one symbol, used to enrich risk and complexity views."""
    if not ix.db.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_spans'").fetchone():
        return None
    ms, hits = [], 0
    for r in ix.db.execute("SELECT count, samples FROM runtime_spans WHERE path=? AND qualname=?", (path, qualname)):
        hits += r["count"]
        ms += json.loads(r["samples"])
    return {"hits": hits, "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95)} if hits else None

