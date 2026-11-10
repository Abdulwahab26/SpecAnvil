"""SQLite-backed code index: incremental, FTS5 ranked search, token ledger.

Pipeline: scan (git ls-files + one stat per file) -> diff against stored (size, mtime_ns)
fingerprints -> read + hash only changed files -> parse in a process pool when there is
enough work -> write everything in one transaction with executemany.
Everything runs on the local machine; nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .config import INDEX_FILE, load_config, state_dir
from .parser import FileParse, Symbol, parse_source
from .scanner import Candidate, is_secret, iter_candidates, looks_binary
from .tokens import estimate_tokens, redact

SCHEMA_VERSION = "6"
SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  path TEXT PRIMARY KEY, lang TEXT, hash TEXT, size INT, mtime_ns INT, lines INT, tokens INT,
  parser TEXT, module TEXT, indexed_at REAL, package TEXT);
CREATE INDEX IF NOT EXISTS ix_files_module ON files(module);
CREATE TABLE IF NOT EXISTS calls(caller_id INT, path TEXT, callee TEXT, line INT, recv TEXT);
CREATE INDEX IF NOT EXISTS ix_calls_callee ON calls(callee);
CREATE INDEX IF NOT EXISTS ix_calls_caller ON calls(caller_id);
CREATE INDEX IF NOT EXISTS ix_calls_path ON calls(path);
CREATE TABLE IF NOT EXISTS bases(path TEXT, cls TEXT, base TEXT);
CREATE INDEX IF NOT EXISTS ix_bases_base ON bases(base);
CREATE INDEX IF NOT EXISTS ix_bases_path ON bases(path);
CREATE TABLE IF NOT EXISTS file_deps(src TEXT, dst TEXT, PRIMARY KEY(src, dst)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_deps_dst ON file_deps(dst);
CREATE TABLE IF NOT EXISTS symbols(
  id INTEGER PRIMARY KEY, path TEXT, name TEXT, qualname TEXT, kind TEXT, signature TEXT,
  start_line INT, end_line INT, doc TEXT, parent TEXT, exported INT, body_hash TEXT, tokens INT);
CREATE INDEX IF NOT EXISTS ix_sym_path ON symbols(path, start_line);
CREATE INDEX IF NOT EXISTS ix_sym_name ON symbols(name);
CREATE INDEX IF NOT EXISTS ix_sym_qual ON symbols(qualname);
CREATE TABLE IF NOT EXISTS imports(path TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_imp_path ON imports(path);
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(name, terms, signature, doc, path, body,
  tokenize='unicode61', prefix='2 3 4');
CREATE TABLE IF NOT EXISTS summaries(body_hash TEXT PRIMARY KEY, summary TEXT, model TEXT, at REAL);
CREATE TABLE IF NOT EXISTS vectors(symbol_id INTEGER PRIMARY KEY, body_hash TEXT, dim INT, vec BLOB);
CREATE TABLE IF NOT EXISTS ledger(ts REAL, tool TEXT, returned INT, baseline INT, ms REAL);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
DATA_TABLES = ("files", "symbols", "imports", "symbols_fts", "calls", "bases", "file_deps", "idents", "vectors")
PARALLEL_THRESHOLD = 150  # below this, process start-up costs more than it saves


def _parse_job(item: tuple[str, str]) -> tuple[str, dict]:
    rel, text = item
    return rel, asdict(parse_source(rel, text))


STOPWORDS = set("""a an the and or of to in on for with from by is are be as at this that it into
add new make create update fix implement build feature should when then so we our use using need
able allow page screen show get set have has can will would there their them user users""".split())
STOPWORDS -= {"user", "users", "get", "set"}  # domain words worth keeping
STOPWORDS |= {"please", "want", "like", "also", "via"}

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
GENERIC_ROOTS = {"src", "app", "lib", "source", "main", "java", "kotlin", "scala", "pkg",
                 "internal", "cmd", "code", "srcs"}
APP_ROOTS = {"frontend", "backend", "web", "webapp", "client", "server", "api", "ui", "mobile", "admin"}
MONO_ROOTS = {"packages", "apps", "services", "libs", "modules", "projects", "components"}


def split_terms(*texts: str) -> str:
    out = []
    for t in texts:
        for w in re.split(r"[^A-Za-z0-9]+", t or ""):
            out.extend(p.lower() for p in _CAMEL.findall(w))
    return " ".join(dict.fromkeys(out))


# Task words -> how code usually names them. Helps natural-language tasks find the right symbols.
SYNONYMS = {
    "endpoint": ["route", "router", "api", "controller", "handler", "view"],
    "api": ["route", "controller", "handler", "endpoint"],
    "list": ["read", "get", "all", "index", "find", "fetch"],
    "pagin": ["skip", "limit", "offset", "cursor", "page"],
    "page": ["skip", "limit", "offset", "cursor"],
    "creat": ["add", "new", "insert", "post", "save"],
    "delet": ["remove", "destroy", "drop"],
    "remov": ["delete", "destroy"],
    "updat": ["edit", "patch", "put", "save"],
    "edit": ["update", "patch"],
    "login": ["auth", "signin", "authenticate", "session", "token"],
    "auth": ["login", "authenticate", "permission", "token", "session"],
    "signup": ["register", "create"],
    "regist": ["signup", "create"],
    "email": ["mail", "send"],
    "search": ["find", "query", "filter"],
    "filter": ["query", "where", "search"],
    "upload": ["file", "storage", "multipart"],
    "permiss": ["role", "authorize", "access", "policy"],
    "validat": ["check", "verify", "schema"],
}


_BODY_IDENT_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_BODY_STOP = {"self", "this", "return", "none", "null", "true", "false", "const", "let", "var", "def", "function",
              "async", "await", "import", "from", "else", "elif", "for", "while", "try", "except", "catch", "finally",
              "raise", "throw", "new", "public", "private", "static", "void", "string", "int", "str", "bool"}


def body_terms(body: str, cap: int = 120) -> str:
    """Distinct identifier sub-words used inside a body: the vocabulary of what the code does."""
    out: dict[str, None] = {}
    for w in _BODY_IDENT_RX.findall(body):
        for p in _CAMEL.findall(w):
            p = p.lower()
            if len(p) > 2 and p not in _BODY_STOP:
                out[p] = None
                if len(out) >= cap:
                    return " ".join(out)
    return " ".join(out)


def stem(w: str) -> str:
    """Tiny suffix stripper so tokens~token, expired~expiry~expire share a prefix."""
    for suf in ("ations", "ation", "ings", "ing", "ies", "ers", "ed", "es", "er", "s", "y", "e"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


GENERATED_RX = re.compile(r"(\.gen\.|\.generated\.|/generated/|__generated__|\.pb\.|_pb2\.|/migrations?/versions/|"
                          r"\.d\.ts$|/client/(types|sdk|schemas)\.|openapi)", re.I)
DECL_KINDS = ("variable", "field", "constant", "type", "enum_member", "key", "section")
NONPROD_RX = re.compile(r"(^|/)(examples?|samples?|demos?|docs?|docs_src|benchmarks?|fixtures?|testdata|"
                        r"stories|storybook|playground|scripts|tools)(/|$)", re.I)
TEST_RX = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|[._](test|spec)\.[a-z]+$|(^|/)test_[^/]+$|Tests?\.\w+$")


def module_of(path: str) -> str:
    parts = path.split("/")[:-1]
    if not parts:
        return "(root)"
    prefix: list[str] = []
    if parts[0] in MONO_ROOTS and len(parts) > 1:
        prefix, parts = parts[:2], parts[2:]
    elif parts[0].lower() in APP_ROOTS and len(parts) > 1:
        prefix, parts = parts[:1], parts[1:]
    while parts and parts[0].lower() in GENERIC_ROOTS:
        parts = parts[1:]
    if parts and parts[0] in ("com", "org", "net", "io", "dev", "co", "de", "uk"):
        parts = parts[3:] if len(parts) > 3 else parts[-1:]
    mod = prefix + parts[:1]
    if prefix and parts[:1] and parts[0] == prefix[-1]:
        mod = prefix
    return "/".join(mod) if mod else ("/".join(prefix) or path.split("/")[0])


def _from_dict(d: dict) -> FileParse:
    return FileParse(language=d["language"], parser=d["parser"], symbols=[Symbol(**x) for x in d["symbols"]],
                     imports=d["imports"], calls=[tuple(c) for c in d.get("calls", [])],
                     bases=[tuple(b) for b in d.get("bases", [])], package=d.get("package", ""),
                     idents=d.get("idents", {}))


class CodeIndex:
    def __init__(self, root: Path | str, state: Path | str | None = None, relaxed: bool = False):
        """`state` stores the index outside the repo (dependency packs, remote repos); `relaxed` keeps
        dist/build/lib folders, which is where published packages ship their code."""
        self.root = Path(root).resolve()
        self.relaxed = relaxed
        d = Path(state) if state else state_dir(self.root)
        d.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(d / INDEX_FILE, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "temp_store=MEMORY",
                       "mmap_size=268435456", "cache_size=-65536"):
            self.db.execute(f"PRAGMA {pragma}")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        row = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not row or row[0] != SCHEMA_VERSION:  # schema changed: rebuild data tables, keep ledger
            for t in DATA_TABLES:
                self.db.execute(f"DROP TABLE IF EXISTS {t}")
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (SCHEMA_VERSION,))
            self.db.execute("DELETE FROM meta WHERE key='parser_version'")
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.cfg = load_config(self.root)

    # ------------------------------------------------------------------ build
    def is_built(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0] > 0

    def build(self, full: bool = False, only: list[str] | None = None, workers: int | None = None) -> dict:
        t0 = time.time()
        from .parser import PARSER_VERSION
        row = self.db.execute("SELECT value FROM meta WHERE key='parser_version'").fetchone()
        if not row or row[0] != PARSER_VERSION:
            full, only = True, None
        stored = {r["path"]: (r["size"], r["mtime_ns"], r["hash"])
                  for r in self.db.execute("SELECT path, size, mtime_ns, hash FROM files")}

        # 1. scan
        if only is not None:
            cands = []
            for rel in only:
                try:
                    st = os.stat(self.root / rel)
                except OSError:
                    continue
                if not is_secret(rel):
                    cands.append(Candidate(rel, st.st_size, st.st_mtime_ns))
            present = {c.path for c in cands}
            removed = [p for p in only if p in stored and p not in present]
        else:
            cands = iter_candidates(self.root, relaxed=self.relaxed)
            present = {c.path for c in cands}
            removed = [p for p in stored if p not in present]
        t_scan = time.time()

        # 2. diff by fingerprint, read + hash only what changed
        to_parse: list[tuple[str, str]] = []
        meta: dict[str, tuple] = {}
        touched: list[tuple] = []
        for c in cands:
            old = stored.get(c.path)
            if not full and old and old[0] == c.size and old[1] == c.mtime_ns:
                continue
            try:
                data = (self.root / c.path).read_bytes()
            except OSError:
                continue
            if looks_binary(data):
                if old:
                    removed.append(c.path)
                continue
            h = hashlib.blake2b(data, digest_size=16).hexdigest()
            if not full and old and old[2] == h:  # touched but identical: just refresh fingerprint
                touched.append((c.size, c.mtime_ns, c.path))
                continue
            text = data.decode("utf-8", errors="replace")
            to_parse.append((c.path, text))
            meta[c.path] = (h, c.size, c.mtime_ns)

        # 3. parse (parallel when worthwhile)
        n_workers = workers if workers is not None else min(8, os.cpu_count() or 1)
        if len(to_parse) >= PARALLEL_THRESHOLD and n_workers > 1:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                parsed = list(pool.map(_parse_job, to_parse, chunksize=32))
            parsed = [(rel, _from_dict(d)) for rel, d in parsed]
        else:
            parsed = [(rel, parse_source(rel, text)) for rel, text in to_parse]
        t_parse = time.time()

        # 4. write in one transaction
        texts = dict(to_parse)

        with self.db:
            wipe = full and only is None  # full rebuild: clear tables once instead of ~8 deletes per file
            if wipe:
                for t in ("symbols_fts", "symbols", "imports", "files", "calls", "bases"):
                    self.db.execute(f"DELETE FROM {t}")  # noqa: S608 - fixed table names
            else:
                for rel in removed:
                    self._delete_file(rel)
                self.db.executemany("UPDATE files SET size=?, mtime_ns=? WHERE path=?", touched)
            for rel, fp in parsed:
                self._write_file(rel, texts[rel], fp, *meta[rel], fresh=wipe)
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_index', ?)", (str(time.time()),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('parser_version', ?)", (PARSER_VERSION,))
            if parsed or removed:
                from .graph import rebuild_file_deps
                rebuild_file_deps(self)
        ts_files = self.db.execute("SELECT COUNT(*) FROM files WHERE parser='tree-sitter'").fetchone()[0]
        return {"files_changed": len(parsed), "files_removed": len(removed),
                "files_total": self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "symbols_total": self.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                "tree_sitter_files": ts_files, "seconds": round(time.time() - t0, 2),
                "timings": {"scan": round(t_scan - t0, 3), "parse": round(t_parse - t_scan, 3),
                            "write": round(time.time() - t_parse, 3)},
                "parallel_workers": n_workers if len(to_parse) >= PARALLEL_THRESHOLD and n_workers > 1 else 1}

    def _summary(self, body_hash: str) -> str:
        if not hasattr(self, "_summary_cache"):
            self._summary_cache = {r[0]: r[1] for r in self.db.execute("SELECT body_hash, summary FROM summaries")}
        return self._summary_cache.get(body_hash, "")

    def symbols_needing_summary(self, target: str | None = None, limit: int = 40) -> list[dict]:
        """Functions/classes with no docstring and no summary yet, biggest and most-called first."""
        sql = ("SELECT s.id, s.path, s.qualname, s.kind, s.signature, s.start_line, s.end_line, s.tokens, s.body_hash "
               "FROM symbols s WHERE s.kind IN ('function','method','class','struct','interface') AND s.doc='' "
               "AND s.body_hash NOT IN (SELECT body_hash FROM summaries) AND (s.end_line - s.start_line) >= 2")
        args: list = []
        if target:
            sql += " AND (s.path LIKE ? OR s.path IN (SELECT path FROM files WHERE module=?))"
            args += [target.rstrip("/") + "%", target]
        rows = self.db.execute(sql + " ORDER BY s.tokens DESC LIMIT ?", (*args, limit * 3)).fetchall()
        called = {r[0]: r[1] for r in self.db.execute("SELECT callee, COUNT(*) FROM calls GROUP BY callee")}
        out = []
        for r in rows:
            if TEST_RX.search(r["path"]) or GENERATED_RX.search(r["path"]):
                continue
            body = self.read_lines(r["path"], r["start_line"], min(r["end_line"], r["start_line"] + 40))
            out.append({"id": r["id"], "ref": f"{r['path']}::{r['qualname']}", "kind": r["kind"],
                        "callers": called.get(r["qualname"].rsplit(".", 1)[-1], 0), "code": body})
        out.sort(key=lambda x: -x["callers"])
        return out[:limit]

    def save_summaries(self, items: dict, model: str = "") -> int:
        """items: {symbol_id: one-line summary}. Stored by body hash, so they survive re-indexing until code changes."""
        n = 0
        for sid, text in items.items():
            text = " ".join(str(text).split())[:240]
            row = self.db.execute("SELECT body_hash, doc FROM symbols WHERE id=?", (int(sid),)).fetchone()
            if not row or not text:
                continue
            self.db.execute("INSERT OR REPLACE INTO summaries VALUES(?,?,?,?)", (row["body_hash"], text, model, time.time()))
            if not row["doc"] or row["doc"].startswith("≈ "):
                self.db.execute("UPDATE symbols SET doc=? WHERE id=?", ("≈ " + text, int(sid)))
                self.db.execute("UPDATE symbols_fts SET doc=? WHERE rowid=?", (text, int(sid)))
            n += 1
        self.db.commit()
        if hasattr(self, "_summary_cache"):
            del self._summary_cache
        return n

    def reparse_languages(self, langs: list[str]) -> dict:
        """After grammars are fetched, re-parse files of those languages that used the regex fallback."""
        if not langs:
            return {"files_changed": 0}
        q = ",".join("?" * len(langs))
        # clear the hash too: unchanged bytes would otherwise hit the "touched but identical" shortcut and skip the parse
        self.db.execute(f"UPDATE files SET mtime_ns=0, hash='' WHERE parser!='tree-sitter' AND lang IN ({q})", langs)
        self.db.commit()
        return self.build()

    def code_languages(self) -> list[str]:
        from .parser import CODE_LANGUAGES
        rows = self.db.execute("SELECT lang, COUNT(*) n FROM files GROUP BY lang ORDER BY n DESC").fetchall()
        return [r["lang"] for r in rows if r["lang"] in CODE_LANGUAGES]

    def _delete_file(self, rel: str) -> None:
        self.db.execute("DELETE FROM symbols_fts WHERE rowid IN (SELECT id FROM symbols WHERE path=?)", (rel,))
        self.db.execute("DELETE FROM symbols WHERE path=?", (rel,))
        self.db.execute("DELETE FROM imports WHERE path=?", (rel,))
        self.db.execute("DELETE FROM files WHERE path=?", (rel,))
        self.db.execute("DELETE FROM calls WHERE path=?", (rel,))
        self.db.execute("DELETE FROM bases WHERE path=?", (rel,))

    def _write_file(self, rel: str, text: str, fp: FileParse, h: str, size: int, mtime_ns: int, fresh: bool = False) -> None:
        if not fresh:
            self._delete_file(rel)
        lines = text.splitlines()
        self.db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (rel, fp.language, h, size, mtime_ns, len(lines), estimate_tokens(text),
                         fp.parser, module_of(rel), time.time(), fp.package or ""))
        qual_ids: dict[str, int] = {}
        if fp.symbols:
            base = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM symbols").fetchone()[0]
            sym_rows, fts_rows = [], []
            rel_terms = split_terms(rel)
            for i, s in enumerate(fp.symbols, start=1):
                body = "\n".join(lines[s.start_line - 1:s.end_line])
                sid = base + i
                qual_ids.setdefault(s.qualname, sid)
                sym_rows.append((sid, rel, s.name, s.qualname, s.kind, s.signature, s.start_line, s.end_line,
                                 s.doc, s.parent, int(s.exported),
                                 hashlib.blake2b(body.encode(), digest_size=12).hexdigest(), estimate_tokens(body)))
                bh = sym_rows[-1][11]
                doc = s.doc or self._summary(bh)
                if doc and not s.doc:  # AI summary (marked with ≈) shown where there is no docstring
                    sym_rows[-1] = sym_rows[-1][:8] + ("≈ " + doc,) + sym_rows[-1][9:]
                fts_rows.append((sid, s.name, split_terms(s.qualname, rel), s.signature, doc, rel_terms,
                                 "" if s.end_line <= s.start_line else
                                 body_terms(body[:800], 40) if s.kind == "section" else body_terms(body)))
            self.db.executemany("INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", sym_rows)
            self.db.executemany("INSERT INTO symbols_fts(rowid,name,terms,signature,doc,path,body) VALUES(?,?,?,?,?,?,?)",
                                fts_rows)
        if fp.imports:
            self.db.executemany("INSERT INTO imports VALUES(?,?)", [(rel, i) for i in fp.imports])
        if fp.calls:
            self.db.executemany("INSERT INTO calls VALUES(?,?,?,?,?)",
                                [(qual_ids.get(c[0]), rel, c[1], c[2], c[3] if len(c) > 3 else "")
                                 for c in fp.calls if c[0] in qual_ids])
        if fp.bases:
            self.db.executemany("INSERT INTO bases VALUES(?,?,?)", [(rel, c, b) for c, b in fp.bases])

    # ----------------------------------------------------------------- ledger
    def record(self, tool: str, returned_text: str, baseline_tokens: int, ms: float | None = None) -> None:
        returned = estimate_tokens(returned_text)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(ledger)")]
        if "ms" not in cols:
            self.db.execute("ALTER TABLE ledger ADD COLUMN ms REAL")
        self.db.execute("INSERT INTO ledger(ts, tool, returned, baseline, ms) VALUES(?,?,?,?,?)",
                        (time.time(), tool, returned, max(baseline_tokens, returned), ms))
        self.db.commit()

    def tool_stats(self) -> list[dict]:
        rows = self.db.execute("SELECT tool, ms, returned FROM ledger WHERE ms IS NOT NULL ORDER BY tool").fetchall()
        by: dict[str, list] = defaultdict(list)
        for r in rows:
            by[r["tool"]].append((r["ms"], r["returned"]))
        out = []
        for tool, vals in by.items():
            ms = sorted(v[0] for v in vals)
            out.append({"tool": tool, "calls": len(ms), "avg_ms": round(sum(ms) / len(ms), 1),
                        "p95_ms": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1),
                        "avg_tokens": round(sum(v[1] for v in vals) / len(vals))})
        return sorted(out, key=lambda x: -x["calls"])

    def index_status(self) -> dict:
        last = self.db.execute("SELECT value FROM meta WHERE key='last_index'").fetchone()
        stale = 0
        stored = {r["path"]: (r["size"], r["mtime_ns"]) for r in self.db.execute("SELECT path, size, mtime_ns FROM files")}
        for c in iter_candidates(self.root, relaxed=self.relaxed):
            if stored.get(c.path) != (c.size, c.mtime_ns):
                stale += 1
        return {"root": str(self.root), "last_index": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(last[0])))
                if last else None, "files": len(stored),
                "symbols": self.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                "files_changed_since": stale, "fresh": stale == 0, "embeddings": self._embedding_status()}

    def _embedding_status(self) -> dict | None:
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='sem_vectors'").fetchone():
            return None
        from .semantic import model_drift
        return model_drift(self)

    def savings(self) -> dict:
        rows = self.db.execute("SELECT tool, COUNT(*) n, SUM(returned) r, SUM(baseline) b "
                               "FROM ledger GROUP BY tool ORDER BY b DESC").fetchall()
        per = [{"tool": r["tool"], "calls": r["n"], "returned_tokens": r["r"], "baseline_tokens": r["b"],
                "saved_pct": round(100 * (1 - r["r"] / r["b"]), 1) if r["b"] else 0} for r in rows]
        tr = sum(p["returned_tokens"] for p in per)
        tb = sum(p["baseline_tokens"] for p in per)
        return {"calls": sum(p["calls"] for p in per), "returned_tokens": tr, "baseline_tokens": tb,
                "saved_tokens": tb - tr, "saved_pct": round(100 * (1 - tr / tb), 1) if tb else 0,
                "by_tool": per,
                "note": "baseline = tokens of the whole files the model would otherwise have read (≈4 chars/token)"}

    # ---------------------------------------------------------------- queries
    def file_tokens(self, paths) -> int:
        paths = list(set(paths))
        if not paths:
            return 0
        q = ",".join("?" * len(paths))
        return self.db.execute(f"SELECT COALESCE(SUM(tokens),0) FROM files WHERE path IN ({q})",
                               paths).fetchone()[0]

    def stats(self) -> dict:
        langs = self.db.execute("SELECT lang, COUNT(*) n, SUM(lines) l FROM files GROUP BY lang "
                                "ORDER BY l DESC").fetchall()
        kinds = self.db.execute("SELECT kind, COUNT(*) n FROM symbols GROUP BY kind ORDER BY n DESC").fetchall()
        tot = self.db.execute("SELECT COUNT(*), COALESCE(SUM(lines),0), COALESCE(SUM(tokens),0) FROM files").fetchone()
        return {"files": tot[0], "lines": tot[1], "repo_tokens": tot[2],
                "symbols": self.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                "languages": [{"lang": r["lang"], "files": r["n"], "lines": r["l"]} for r in langs],
                "symbol_kinds": {r["kind"]: r["n"] for r in kinds}}

    def modules(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT f.module m, COUNT(DISTINCT f.path) files, SUM(f.lines) lines, "
            "(SELECT COUNT(*) FROM symbols s JOIN files f2 ON s.path=f2.path WHERE f2.module=f.module) syms "
            "FROM files f WHERE f.parser != 'text' GROUP BY f.module ORDER BY lines DESC").fetchall()
        return [dict(r) for r in rows]

    def module_files(self, module: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT path FROM files WHERE module=? AND parser!='text' ORDER BY path", (module,))]

    def outline(self, path: str, include_private: bool = True) -> dict | None:
        f = self.db.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        if not f:
            return None
        syms = self.db.execute("SELECT id,name,qualname,kind,signature,start_line,end_line,doc,parent,exported "
                               "FROM symbols WHERE path=? ORDER BY start_line", (path,)).fetchall()
        items = []
        for s in syms:
            if not include_private and s["name"].startswith("_"):
                continue
            depth = s["qualname"].count(".")
            items.append(f"{'  ' * depth}[{s['id']}] {s['kind']} {s['signature']}  "
                         f"(L{s['start_line']}-{s['end_line']})" + (f"  # {s['doc']}" if s["doc"] else ""))
        imps = [r[0] for r in self.db.execute("SELECT source FROM imports WHERE path=? LIMIT 40", (path,))]
        return {"path": path, "language": f["lang"], "lines": f["lines"], "file_tokens": f["tokens"],
                "imports": imps, "symbols": items}

    def search(self, query: str, kind: str | None = None, path_prefix: str | None = None,
               limit: int = 20) -> list[dict]:
        terms = list(dict.fromkeys(stem(t) for t in split_terms(query).split() if t))
        if not terms:
            return []
        learned = self.user_synonyms()
        if learned:  # rules learned by suggest_corrections widen recall
            extra = [stem(x) for t in terms for k, v in learned.items() if t.startswith(k) for x in v]
            terms += [x for x in dict.fromkeys(extra) if x not in terms]
        fts = "{name terms signature doc path} : (" + " OR ".join(f'"{t}"*' for t in terms) + ")"
        sql = ("SELECT s.id,s.path,s.name,s.qualname,s.kind,s.signature,s.start_line,s.end_line,s.doc,s.tokens,s.body_hash,"
               "bm25(symbols_fts, 10.0, 4.0, 2.0, 1.5, 1.0, 0.0) AS score "
               "FROM symbols_fts JOIN symbols s ON s.id = symbols_fts.rowid WHERE symbols_fts MATCH ?")
        args: list = [fts]
        if kind:
            sql += " AND s.kind = ?"
            args.append(kind)
        if path_prefix:
            sql += " AND s.path LIKE ?"
            args.append(path_prefix.rstrip("/") + "%")
        sql += " ORDER BY score LIMIT ?"
        args.append(limit * 3)
        rows = [dict(r) for r in self.db.execute(sql, args)]
        ql = query.strip().lower()
        wants_tests = any(t.startswith("test") or t.startswith("spec") for t in terms)
        wants_docs = bool(re.search(r"\b(docs?|readme|guide|tutorial|section|how to|documentation|faq)\b", ql))
        wants_nonprod = any(t.startswith(("exampl", "sampl", "demo", "doc", "bench", "fixtur")) for t in terms)
        for r in rows:
            name_terms = split_terms(r["qualname"]).split()
            path_terms = split_terms(r["path"]).split()
            name_cov = sum(any(x.startswith(t) for x in name_terms) for t in terms)
            path_cov = sum(any(x.startswith(t) for x in path_terms) for t in terms)
            adj = -4.0 * name_cov - 1.0 * path_cov
            if r["name"].lower() == ql or r["qualname"].lower() == ql:
                adj -= 100  # exact name
            if TEST_RX.search(r["path"]) and not wants_tests:
                adj += 9
            if NONPROD_RX.search(r["path"]) and not wants_nonprod:
                adj += 6
            if GENERATED_RX.search(r["path"]):
                adj += 10
            if r["kind"] in DECL_KINDS and r["name"].lower() != ql:
                adj += 4  # variables/fields/constants: useful, but rarely the answer to a behaviour query
            if r["kind"] == "section" and not wants_docs:
                adj += 8  # doc sections answer "how/why" questions; code queries should land on code first
            elif r["kind"] == "section":
                adj -= 6  # the query asked for docs/readme/guide
            r["score"] += adj
        rows.sort(key=lambda r: r["score"])
        # drop results nested inside (or containing) a better-ranked result: never pay for the same lines twice
        picked: list[dict] = []
        for r in rows:
            if any(p["path"] == r["path"] and p["start_line"] <= r["start_line"] and r["end_line"] <= p["end_line"]
                   for p in picked):
                continue  # already covered by a better-ranked enclosing symbol
            picked.append(r)
            if len(picked) >= limit:
                break
        return picked

    def search_body(self, query: str, limit: int = 50) -> list[dict]:
        terms = list(dict.fromkeys(stem(t) for t in split_terms(query).split() if t and t not in STOPWORDS))
        if not terms:
            return []
        fts = "{body doc} : (" + " OR ".join(f'"{t}"*' for t in terms) + ")"
        sql = ("SELECT s.id,s.path,s.name,s.qualname,s.kind,s.signature,s.start_line,s.end_line,s.doc,s.tokens,s.body_hash,"
               "bm25(symbols_fts, 0, 0, 0, 2.0, 0, 1.0) AS score FROM symbols_fts JOIN symbols s ON s.id=symbols_fts.rowid "
               "WHERE symbols_fts MATCH ? AND s.kind IN ('function','method','class','struct') ORDER BY score LIMIT ?")
        return [dict(r) for r in self.db.execute(sql, (fts, limit))]

    def fusion_weights(self, with_vectors: bool) -> tuple:
        """RRF weights (name, body[, vectors]); defaults tuned on tests/evals, overridable by `tune_weights`."""
        fw = self.cfg.get("fusion_weights") or {}
        w = fw.get("with_vectors") if with_vectors else fw.get("lexical")
        return tuple(w) if w else ((1.0, 0.5, 2.0) if with_vectors else (1.0, 0.8))

    def user_synonyms(self) -> dict[str, list[str]]:
        """Learned / configured rules from .specforge/config.json `synonyms` (see suggest_corrections)."""
        return {stem(k.lower()): [str(x).lower() for x in v] for k, v in (self.cfg.get("synonyms") or {}).items()}

    def hybrid_search(self, query: str, limit: int = 20, kind: str | None = None, path_prefix: str | None = None,
                      vector_hits: list[dict] | None = None, weights: tuple | None = None) -> list[dict]:
        """Reciprocal-rank fusion of name/signature BM25, body-vocabulary BM25 and (optional) embedding hits."""
        lists = [self.search(query, kind=kind, path_prefix=path_prefix, limit=60), self.search_body(query, 60)]
        if vector_hits:
            lists.append(vector_hits)
        fused: dict[int, dict] = {}
        weights = weights or self.fusion_weights(bool(vector_hits))
        for weight, lst in zip(weights, lists):
            for rank, r in enumerate(lst):
                e = fused.setdefault(r["id"], {**r, "rrf": 0.0})
                e["rrf"] += weight / (60 + rank)
        out = sorted(fused.values(), key=lambda r: -r["rrf"])
        if kind:
            out = [r for r in out if r["kind"] == kind]
        if path_prefix:
            out = [r for r in out if r["path"].startswith(path_prefix.rstrip("/"))]
        ql = query.lower()
        wants_tests = "test" in ql
        out.sort(key=lambda r: -(r["rrf"] - (0.012 if TEST_RX.search(r["path"]) and not wants_tests else 0)
                                 - (0.008 if NONPROD_RX.search(r["path"]) else 0)
                                 - (0.006 if r["kind"] in DECL_KINDS and r["name"].lower() != ql else 0)))
        for r in out:
            r["score"] = -r["rrf"]
        return out[:limit]

    def find_symbol(self, ref: str | int, path: str | None = None) -> list[sqlite3.Row]:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            return self.db.execute("SELECT * FROM symbols WHERE id=?", (int(ref),)).fetchall()
        if "::" in ref and not path:
            path, ref = ref.split("::", 1)
        sql, args = "SELECT * FROM symbols WHERE (qualname=? OR name=?)", [ref, ref]
        if path:
            sql += " AND path=?"
            args.append(path)
        return self.db.execute(sql + " ORDER BY length(qualname) LIMIT 10", args).fetchall()

    def read_lines(self, path: str, start: int, end: int) -> str:
        try:
            lines = (self.root / path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        text = "\n".join(lines[max(0, start - 1):end])
        return redact(text) if self.cfg.get("redact_secrets", True) else text

    def symbol_source(self, row: sqlite3.Row, context_lines: int = 0) -> str:
        start = max(1, row["start_line"] - context_lines)
        # include a leading doc comment / decorators if present
        body = self.read_lines(row["path"], start, row["end_line"] + context_lines)
        return body

    def overview(self, max_modules: int = 25, per_module: int = 8) -> str:
        return self.overview_with_files(max_modules, per_module)[0]

    def overview_with_files(self, max_modules: int = 25, per_module: int = 8) -> tuple[str, list[str]]:
        listed: list[str] = []
        st = self.stats()
        out = [f"# Repo map ({st['files']} files, {st['lines']:,} lines, {st['symbols']:,} symbols, "
               f"~{st['repo_tokens']:,} tokens if read in full)",
               "Languages: " + ", ".join(f"{l['lang']} ({l['files']})" for l in st["languages"][:10]), ""]
        for m in self.modules()[:max_modules]:
            out.append(f"## {m['m']}  — {m['files']} files, {m['lines']:,} lines")
            top = self.db.execute(
                "SELECT s.kind, s.qualname, s.path, s.start_line FROM symbols s JOIN files f ON s.path=f.path "
                "WHERE f.module=? AND s.parent='' AND s.kind IN ('class','interface','struct','enum','trait','function',"
                "'impl','namespace') ORDER BY s.exported DESC, s.tokens DESC LIMIT ?", (m["m"], per_module)).fetchall()
            for s in top:
                out.append(f"- {s['kind']} {s['qualname']}  ({s['path']}:{s['start_line']})")
                listed.append(s["path"])
        return "\n".join(out), listed

    def context_for(self, task: str, budget_tokens: int | None = None,
                    delivered: dict | None = None) -> tuple[str, list[str]]:
        """`delivered` (id -> body_hash) is the session's record of bodies already sent; unchanged ones are
        referenced instead of resent, and newly sent bodies are added to it."""
        budget = budget_tokens or int(self.cfg.get("context_budget_tokens", 6000))
        words = [w for w in split_terms(task).split() if w not in STOPWORDS and len(w) > 2]
        if not words:
            return "No meaningful keywords in task.", []
        stems = list(dict.fromkeys(stem(w) for w in words))
        syn = {**SYNONYMS, **self.user_synonyms()}
        extra = list(dict.fromkeys(x for st in stems for k, v in syn.items() if st.startswith(k) for x in v))
        hits = self.search(" ".join(words + extra), limit=100)
        sem_rank: dict[int, int] = {}
        if self.cfg.get("semantic_context", True):
            try:
                from . import semantic
                if semantic.has_vectors(self):
                    for i, r in enumerate(semantic.search(self, task, 40)):
                        sem_rank[r["id"]] = i
                        if all(h["id"] != r["id"] for h in hits):
                            r.setdefault("score", 0.0)
                            hits.append(r)
            except Exception:  # noqa: BLE001 - embeddings optional; never break context building
                sem_rank = {}
        extra_stems = [stem(x) for x in extra if stem(x) not in stems]
        wants_tests = any(w.startswith("test") for w in words)
        file_score = Counter(h["path"] for h in hits)

        def rank(h: dict) -> float:
            hay = split_terms(h["qualname"], h["signature"], h["doc"] or "", h["path"])
            hay_words = hay.split()
            coverage = sum(any(x.startswith(st) for x in hay_words) for st in stems)
            coverage += 0.5 * min(2, sum(any(x.startswith(st) for x in hay_words) for st in extra_stems))
            score = -coverage * 10 + h["score"] - 0.5 * min(file_score[h["path"]], 6)
            if GENERATED_RX.search(h["path"]):
                score += 25  # generated code: rarely what you edit
            if TEST_RX.search(h["path"]) and not wants_tests:
                score += 8
            if h["kind"] in ("function", "method"):
                score -= 1
            if h["kind"] in DECL_KINDS:
                score += 6
            if h["id"] in sem_rank:
                score -= 8 * (1 - sem_rank[h["id"]] / 40)  # semantic agreement, strongest for top matches
            return score

        hits.sort(key=rank)
        # a matching parent (component/class/function) of reasonable size covers its matching children: keep
        # the parent at the best child's position and drop the children, so no lines are paid for twice
        by_path: dict[str, list[dict]] = {}
        for h in hits:
            by_path.setdefault(h["path"], []).append(h)
        drop: set[int] = set()
        promote: dict[int, int] = {}
        for h in hits:
            for p in by_path[h["path"]]:
                if p["id"] != h["id"] and p["start_line"] <= h["start_line"] and h["end_line"] <= p["end_line"] \
                        and p["kind"] not in DECL_KINDS and (p["tokens"] or 0) <= 1500 \
                        and (p["end_line"] - p["start_line"]) > (h["end_line"] - h["start_line"]):
                    drop.add(h["id"])
                    promote.setdefault(p["id"], hits.index(h))
                    break
        if drop:
            pos = {h["id"]: i for i, h in enumerate(hits)}
            hits = sorted((h for h in hits if h["id"] not in drop),
                          key=lambda h: min(pos[h["id"]], promote.get(h["id"], pos[h["id"]])))
        out = [f"# Context for: {task}", f"keywords: {', '.join(words)}", ""]
        used = estimate_tokens("\n".join(out))
        full_budget = int(budget * 0.7)
        files: list[str] = []
        sig_only: list[dict] = []
        seen_parents: set[tuple[str, str]] = set()
        per_file: Counter = Counter()
        already: list[str] = []
        for h in hits:
            key = (h["path"], h["qualname"].rsplit(".", 1)[0])
            if delivered is not None and delivered.get(h["id"]) == h["body_hash"]:
                if len(already) < 15:
                    already.append(f"[{h['id']}] {h['qualname']}")
                files.append(h["path"])
                continue
            if h["kind"] in DECL_KINDS:
                sig_only.append(h)
                continue
            if h["tokens"] and used + h["tokens"] < full_budget and key not in seen_parents \
                    and per_file[h["path"]] < 3:
                per_file[h["path"]] += 1
                body = self.read_lines(h["path"], h["start_line"], h["end_line"])
                block = f"### {h['qualname']}  [{h['id']}]  {h['path']}:{h['start_line']}-{h['end_line']}\n```\n{body}\n```"
                used += estimate_tokens(block)
                out.append(block)
                files.append(h["path"])
                if delivered is not None:
                    delivered[h["id"]] = h["body_hash"]
                if h["kind"] in ("class", "interface", "struct"):
                    seen_parents.add((h["path"], h["qualname"]))
            else:
                sig_only.append(h)
        if already:
            out.insert(3, "Already in your context this session (unchanged, not resent): " + ", ".join(already))
        if sig_only:
            out.append("\n### Related (signatures only — fetch with get_symbol if needed)")
            for h in sig_only:
                line = f"- [{h['id']}] {h['kind']} {h['signature']}  ({h['path']}:{h['start_line']})"
                if used + estimate_tokens(line) > budget:
                    break
                out.append(line)
                used += estimate_tokens(line)
                files.append(h["path"])
        return "\n".join(out), files

    def grep(self, pattern: str, path_glob: str | None = None, limit: int = 50,
             regex: bool = False, word: bool = False, code_only: bool = False) -> tuple[list[str], list[str]]:
        import fnmatch
        rx = re.compile(pattern if regex else (r"\b" + re.escape(pattern) + r"\b" if word else re.escape(pattern)))
        out, files = [], []
        sql = "SELECT path FROM files" + (" WHERE parser != 'text'" if code_only else "") + " ORDER BY path"
        allowed = [r[0] for r in self.db.execute(sql)]
        fast = self._rg(rx.pattern, set(allowed), path_glob, limit)
        if fast is not None:
            return fast
        for path in allowed:
            if path_glob and not fnmatch.fnmatch(path, path_glob):
                continue
            try:
                lines = (self.root / path).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            hit = False
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    out.append(f"{path}:{i}: {redact(line.strip())[:200]}")
                    hit = True
                    if len(out) >= limit:
                        files.append(path)
                        return out, files
            if hit:
                files.append(path)
        return out, files

    def _rg(self, pattern: str, allowed: set[str], path_glob: str | None, limit: int):
        """ripgrep fast path (if installed). Results are filtered to indexed files, so ignored and
        secret files can never leak through, exactly like the pure-Python path."""
        import shutil
        rg = shutil.which("rg")
        if not rg or os.environ.get("SPECFORGE_NO_RG"):
            return None
        cmd = [rg, "--line-number", "--no-heading", "--color", "never", "--max-columns", "300", "-e", pattern]
        if path_glob:
            cmd += ["--glob", path_glob]
        cmd += ["--", "."]
        out, files = [], []
        try:
            proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace")
        except OSError:
            return None
        assert proc.stdout is not None
        for line in proc.stdout:
            path, _, rest = line.partition(":")
            path = path[2:] if path.startswith("./") else path
            lineno, _, text = rest.partition(":")
            if path not in allowed or not lineno.isdigit():
                continue
            out.append(f"{path}:{lineno}: {redact(text.strip())[:200]}")
            if not files or files[-1] != path:
                files.append(path)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            proc.kill()
        code = proc.wait()
        if code == 2 and not out:  # rg rejected the pattern (Python/Rust regex dialects differ): fall back
            return None
        return out, files

    def enclosing_symbol(self, path: str, line: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM symbols WHERE path=? AND start_line<=? AND end_line>=? "
                               "ORDER BY (end_line-start_line) LIMIT 1", (path, line, line)).fetchone()

    def changed_symbols(self, base: str = "HEAD") -> list[dict]:
        try:
            diff = subprocess.run(["git", "diff", "--unified=0", base, "--"], cwd=self.root,
                                  capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        result: dict[tuple, dict] = {}
        current = None
        for line in diff.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else None
            elif line.startswith("@@") and current:
                m = re.search(r"\+(\d+)(?:,(\d+))?", line)
                if not m:
                    continue
                start, count = int(m.group(1)), int(m.group(2) or 1)
                for ln in range(start, start + max(count, 1)):
                    s = self.enclosing_symbol(current, ln)
                    key = (current, s["qualname"] if s else "(module level)")
                    result.setdefault(key, {"path": current, "symbol": key[1],
                                            "id": s["id"] if s else None,
                                            "signature": s["signature"] if s else ""})
        return list(result.values())

    def dependents(self, path: str) -> list[str]:
        """Files whose imports mention this file's module/stem (cheap reverse-dependency guess)."""
        stem = Path(path).stem
        if stem in ("index", "__init__", "main", "mod"):
            stem = Path(path).parent.name
        rows = self.db.execute("SELECT DISTINCT path FROM imports WHERE source LIKE ? AND path != ?",
                               (f"%{stem}%", path)).fetchall()
        return [r[0] for r in rows]

    def to_json(self) -> str:
        return json.dumps(self.stats(), indent=2)


def group_by(rows, key):
    d = defaultdict(list)
    for r in rows:
        d[r[key]].append(r)
    return d
