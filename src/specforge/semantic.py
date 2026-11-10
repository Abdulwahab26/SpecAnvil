"""Optional semantic search with local embeddings.

Providers (config `semantic.provider`):
- "fastembed" (default): ONNX models run in-process on CPU. The model is downloaded once by the explicit
  `specforge embed` step (like grammars); after that everything is offline.
- "endpoint": an embeddings HTTP endpoint - Ollama (`/api/embed`) or OpenAI-compatible (`/v1/embeddings`).
  Only localhost is allowed unless `semantic.allow_remote` is true, so code never leaves the machine by accident.

Vectors are keyed by (path, qualname, body_hash), so re-embedding after edits only touches changed symbols.
"""

from __future__ import annotations

import json
import time
import urllib.request
from array import array
from urllib.parse import urlparse

from .config import load_config
from .index import CodeIndex, body_terms, split_terms

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
CODE_MODEL = "jinaai/jina-embeddings-v2-base-code"
KINDS = ("function", "method", "class", "struct", "interface", "trait")
SCHEMA = """CREATE TABLE IF NOT EXISTS sem_vectors(path TEXT, qualname TEXT, body_hash TEXT, model TEXT, vec BLOB,
            PRIMARY KEY(path, qualname)) WITHOUT ROWID;"""

_cache: dict = {}


def settings(ix: CodeIndex) -> dict:
    s = {"provider": "fastembed", "model": DEFAULT_MODEL, "endpoint": "", "allow_remote": False}
    s.update(load_config(ix.root).get("semantic") or {})
    return s


class _FastEmbed:
    def __init__(self, model: str, allow_download: bool = False):
        """Loads strictly from the local cache. Only the explicit `specforge embed` step may download."""
        import os
        from .profile import home
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        if not allow_download:
            os.environ["HF_HUB_OFFLINE"] = "1"  # no update checks at query time
        from fastembed import TextEmbedding  # optional dependency: pip install "specforge[semantic]"
        cache = str(home() / "models")
        try:
            self.model = TextEmbedding(model, cache_dir=cache, local_files_only=True)
        except Exception:  # noqa: BLE001 - not downloaded yet
            if not allow_download:
                raise ValueError(f"Embedding model {model} is not downloaded. Run `specforge embed` once.")
            os.environ.pop("HF_HUB_OFFLINE", None)
            self.model = TextEmbedding(model, cache_dir=cache)
            os.environ["HF_HUB_OFFLINE"] = "1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.model.embed(texts, batch_size=64)]


class _Endpoint:
    def __init__(self, url: str, model: str, allow_remote: bool):
        host = urlparse(url).hostname or ""
        if not allow_remote and host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError(f"Refusing to send code to remote embeddings endpoint {host}. "
                             "Set semantic.allow_remote=true in .specforge/config.json if that is intended.")
        self.url, self.model = url.rstrip("/"), model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.url.endswith("/api/embed") or "/api/" in self.url:  # Ollama
            body = {"model": self.model, "input": texts}
            data = self._post(self.url, body)
            return data.get("embeddings") or [data.get("embedding")]
        body = {"model": self.model, "input": texts}  # OpenAI-compatible
        data = self._post(self.url if self.url.endswith("/embeddings") else self.url + "/v1/embeddings", body)
        return [d["embedding"] for d in data["data"]]

    @staticmethod
    def _post(url: str, body: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - URL validated above
            return json.loads(r.read())


def provider(ix: CodeIndex, allow_download: bool = False):
    s = settings(ix)
    key = (s["provider"], s["model"], s["endpoint"])
    if key not in _cache:
        if s["provider"] == "endpoint":
            _cache[key] = _Endpoint(s["endpoint"], s["model"], bool(s["allow_remote"]))
        else:
            _cache[key] = _FastEmbed(s["model"], allow_download)
    return _cache[key]


def _doc_text(r, body: str) -> str:
    """What gets embedded: natural-language view of a symbol (split identifiers + doc + body vocabulary)."""
    return (f"{r['kind']} {split_terms(r['qualname'])}\n{r['signature']}\n{r['doc'] or ''}\n"
            f"{body_terms(body, 60)}")[:1500]


def build(ix: CodeIndex, batch: int = 128, progress=None) -> dict:
    """Embed new/changed symbols. The first call may download the model (explicit, one-time)."""
    ix.db.executescript(SCHEMA)
    s = settings(ix)
    prov = provider(ix, allow_download=True)  # the one explicit step allowed to fetch the model
    q = ",".join("?" * len(KINDS))
    rows = ix.db.execute(f"SELECT id, path, qualname, kind, signature, doc, start_line, end_line, body_hash "
                         f"FROM symbols WHERE kind IN ({q})", KINDS).fetchall()
    have = {(r[0], r[1]): (r[2], r[3]) for r in ix.db.execute("SELECT path, qualname, body_hash, model FROM sem_vectors")}
    if ix.db.execute("SELECT value FROM meta WHERE key='sem_text_version'").fetchone() != ("3",):
        have = {}  # embedding text format changed: re-embed everything once
        ix.db.execute("INSERT OR REPLACE INTO meta VALUES('sem_text_version', '3')")
    todo = [r for r in rows if have.get((r["path"], r["qualname"])) != (r["body_hash"], s["model"])]
    current = {(r["path"], r["qualname"]) for r in rows}
    stale = [k for k in have if k not in current]
    t0, files = time.time(), {}
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        texts = []
        for r in chunk:
            lines = files.get(r["path"])
            if lines is None:
                try:
                    lines = files[r["path"]] = (ix.root / r["path"]).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = files[r["path"]] = []
            texts.append(_doc_text(r, "\n".join(lines[r["start_line"] - 1:r["end_line"]])))
        vecs = prov.embed(texts)
        ix.db.executemany("INSERT OR REPLACE INTO sem_vectors VALUES(?,?,?,?,?)",
                          [(r["path"], r["qualname"], r["body_hash"], s["model"], array("f", _norm(v)).tobytes())
                           for r, v in zip(chunk, vecs)])
        ix.db.commit()
        if progress:
            progress(min(i + batch, len(todo)), len(todo))
    if stale:
        ix.db.executemany("DELETE FROM sem_vectors WHERE path=? AND qualname=?", stale)
        ix.db.commit()
    _cache.pop("matrix", None)
    return {"embedded": len(todo), "removed": len(stale), "total": len(rows), "model": s["model"],
            "provider": s["provider"], "seconds": round(time.time() - t0, 1)}


def _norm(v):
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


def has_vectors(ix: CodeIndex) -> bool:
    ix.db.executescript(SCHEMA)
    return bool(ix.db.execute("SELECT 1 FROM sem_vectors LIMIT 1").fetchone())


def _matrix(ix: CodeIndex):
    stamp = ix.db.execute("SELECT COUNT(*), MAX(rowid) FROM symbols").fetchone()[0], \
        ix.db.execute("SELECT COUNT(*) FROM sem_vectors").fetchone()[0]
    m = _cache.get("matrix")
    if m and m["stamp"] == stamp:
        return m
    rows = ix.db.execute(
        "SELECT s.id, s.path, s.name, s.qualname, s.kind, s.signature, s.start_line, s.end_line, s.doc, s.tokens, "
        "s.body_hash, v.vec FROM sem_vectors v JOIN symbols s ON s.path=v.path AND s.qualname=v.qualname "
        "AND s.body_hash=v.body_hash").fetchall()
    try:
        import numpy as np
        mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else None
    except ImportError:
        np, mat = None, [array("f", r["vec"]) for r in rows]
    meta = [{k: r[k] for k in r.keys() if k != "vec"} for r in rows]
    _cache["matrix"] = m = {"stamp": stamp, "mat": mat, "meta": meta, "np": np}
    return m


def search(ix: CodeIndex, query: str, limit: int = 20, kind: str | None = None) -> list[dict]:
    if not has_vectors(ix):
        raise ValueError("No embeddings yet. Run `specforge embed` once (downloads a small local model).")
    m = _matrix(ix)
    if not m["meta"]:
        return []
    qv = _norm(provider(ix).embed([query])[0])
    if m["np"] is not None:
        np = m["np"]
        scores = m["mat"] @ np.asarray(qv, dtype=np.float32)
        order = np.argsort(-scores)[: limit * 3]
        pairs = [(int(i), float(scores[i])) for i in order]
    else:
        scored = [(i, sum(a * b for a, b in zip(vec, qv))) for i, vec in enumerate(m["mat"])]
        pairs = sorted(scored, key=lambda x: -x[1])[: limit * 3]
    out = []
    for i, sc in pairs:
        r = dict(m["meta"][i])
        if kind and r["kind"] != kind:
            continue
        r["score"] = -sc
        r["similarity"] = round(sc, 3)
        out.append(r)
        if len(out) >= limit:
            break
    return out
