"""Retrieval regret: learn from how agents actually search, then fix ranking with evidence.

Every search-type call logs its query and the ids it returned; every fetch logs what was finally read. From
that ledger (local, in the index DB) we mine:

  * zero / thin results            - queries that returned nothing useful
  * reformulation churn            - the same need re-asked with different words before anything was read
  * fetched outside the top 5      - the agent had to dig past the ranking (or found it some other way)
  * vocabulary gaps                - query words that never appear in the names of what was fetched

`suggest_corrections` turns the gaps into synonym rules (`synonyms` in .specforge/config.json, applied to
search and get_context), and `tune_weights` grid-searches the hybrid fusion weights against either an eval
file or the ledger's own query -> fetched pairs, saving them only when they beat the current weights.
"""

from __future__ import annotations

import itertools
import json
import re
import time
from collections import Counter, defaultdict

from .config import load_config, save_config
from .index import STOPWORDS, CodeIndex, split_terms, stem

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (id INTEGER PRIMARY KEY, ts REAL, session TEXT, tool TEXT, query TEXT,
                                      results TEXT, n INTEGER);
CREATE TABLE IF NOT EXISTS fetch_log (ts REAL, session TEXT, tool TEXT, sym_id INTEGER, ref TEXT);
"""
SEARCH_TOOLS = {"search_symbols", "get_context", "search_code", "plan_turn"}
FETCH_TOOLS = {"get_symbol", "get_symbols", "file_outline", "get_file_content"}
ID_RX = re.compile(r"^\s*\[(\d+)\]", re.M)
REF_RX = re.compile(r"([\w./\-]+\.\w+)::([\w.$<>]+)")
WINDOW_S = 180


def _ensure(ix: CodeIndex) -> None:
    ix.db.executescript(SCHEMA)


def _ids_in(ix: CodeIndex, text: str, cap: int = 20) -> list[int]:
    ids = [int(x) for x in ID_RX.findall(text)]
    for path, qual in REF_RX.findall(text):
        if len(ids) >= cap:
            break
        r = ix.db.execute("SELECT id FROM symbols WHERE path=? AND qualname=? LIMIT 1", (path, qual)).fetchone()
        if r and r[0] not in ids:
            ids.append(r[0])
    return list(dict.fromkeys(ids))[:cap]


def log_call(ix: CodeIndex, session: str, tool: str, args: dict, out: str) -> None:
    """Called by the server wrapper after each tool call. Cheap, never raises."""
    try:
        if tool in SEARCH_TOOLS:
            q = str(args.get("query") or args.get("task") or args.get("pattern") or "")[:300]
            if not q:
                return
            _ensure(ix)
            ids = _ids_in(ix, out)
            ix.db.execute("INSERT INTO query_log(ts, session, tool, query, results, n) VALUES(?,?,?,?,?,?)",
                          (time.time(), session, tool, q, json.dumps(ids), len(ids)))
            ix.db.commit()
        elif tool in FETCH_TOOLS:
            refs = args.get("refs") or [args.get("ref") or args.get("path") or ""]
            if isinstance(refs, str):
                refs = [refs]
            _ensure(ix)
            rows = []
            for ref in refs[:25]:
                try:
                    hit = ix.find_symbol(str(ref))
                except Exception:  # noqa: BLE001
                    hit = []
                rows.append((time.time(), session, tool, hit[0]["id"] if hit else None, str(ref)[:200]))
            ix.db.executemany("INSERT INTO fetch_log VALUES(?,?,?,?,?)", rows)
            ix.db.commit()
    except Exception:  # noqa: BLE001 - telemetry must never break a tool call
        pass


def _terms(text: str) -> list[str]:
    return [stem(t) for t in split_terms(text).split() if t not in STOPWORDS and len(t) > 2]


def episodes(ix: CodeIndex, days: int = 30) -> list[dict]:
    """Group queries with the fetches that followed them (same session, within WINDOW_S)."""
    _ensure(ix)
    since = time.time() - days * 86400
    qs = [dict(r) for r in ix.db.execute("SELECT * FROM query_log WHERE ts>=? ORDER BY ts", (since,))]
    fs = [dict(r) for r in ix.db.execute("SELECT * FROM fetch_log WHERE ts>=? ORDER BY ts", (since,))]
    by_session = defaultdict(list)
    for f in fs:
        by_session[f["session"]].append(f)
    out = []
    for i, q in enumerate(qs):
        nxt = next((x["ts"] for x in qs[i + 1:] if x["session"] == q["session"]), float("inf"))
        fetched = [f for f in by_session[q["session"]] if q["ts"] <= f["ts"] <= min(q["ts"] + WINDOW_S, nxt)]
        out.append({**q, "results": json.loads(q["results"]), "fetched": fetched,
                    "next_query_s": None if nxt == float("inf") else round(nxt - q["ts"], 1)})
    return out


def suggest_corrections(ix: CodeIndex, days: int = 30, apply: bool = False, min_evidence: int = 2) -> dict:
    """Why searches miss, from the local ledger, plus synonym rules that would have fixed them."""
    eps = episodes(ix, days)
    if not eps:
        raise ValueError("No searches logged yet. The ledger fills as agents use search_symbols / get_context.")
    zero = [e for e in eps if e["n"] == 0]
    churn = [e for e in eps if not e["fetched"] and e["next_query_s"] is not None and e["next_query_s"] < 60]
    ranks, outside, gaps = [], [], Counter()
    gap_examples: dict[tuple, list] = defaultdict(list)
    for e in eps:
        for f in e["fetched"]:
            if f["sym_id"] is None:
                continue
            rank = e["results"].index(f["sym_id"]) + 1 if f["sym_id"] in e["results"] else None
            ranks.append(rank)
            if rank is None or rank > 5:
                outside.append({"query": e["query"], "fetched": f["ref"], "rank": rank})
            row = ix.db.execute("SELECT name, qualname, path FROM symbols WHERE id=?", (f["sym_id"],)).fetchone()
            if not row:
                continue
            name_terms = set(_terms(row["qualname"])) | set(_terms(row["path"].rsplit("/", 1)[-1]))
            if rank is None or rank > 3:
                for qt in set(_terms(e["query"])) - name_terms:
                    for nt in name_terms:
                        gaps[(qt, nt)] += 1
                        if len(gap_examples[(qt, nt)]) < 3:
                            gap_examples[(qt, nt)].append(f"{e['query']!r} -> {row['qualname']}")
    # reformulation chains: a failed query followed by one that led to a fetch -> words that worked
    for e in churn:
        later = next((x for x in eps if x["session"] == e["session"] and x["ts"] > e["ts"] and x["fetched"]), None)
        if later and later["ts"] - e["ts"] < 300:
            for qt in set(_terms(e["query"])) - set(_terms(later["query"])):
                for nt in set(_terms(later["query"])) - set(_terms(e["query"])):
                    gaps[(qt, nt)] += 1
                    if len(gap_examples[(qt, nt)]) < 3:
                        gap_examples[(qt, nt)].append(f"{e['query']!r} -> re-asked as {later['query']!r}")
    rules: dict[str, list[str]] = defaultdict(list)
    suggestions = []
    for (qt, nt), n in gaps.most_common(40):
        if n < min_evidence or qt == nt or nt in rules[qt]:
            continue
        rules[qt].append(nt)
        suggestions.append({"when_query_has": qt, "also_search": nt, "evidence": n, "examples": gap_examples[(qt, nt)]})
    found = [r for r in ranks if r]
    report = {
        "queries": len(eps), "zero_results": len(zero), "reformulated_without_reading": len(churn),
        "fetches_after_search": len(ranks), "fetched_outside_top5": len(outside),
        "mean_rank_of_fetched": round(sum(found) / len(found), 2) if found else None,
        "hit_at_1": round(sum(1 for r in ranks if r == 1) / len(ranks), 2) if ranks else None,
        "zero_result_queries": [e["query"] for e in zero[:10]], "outside_top5": outside[:10],
        "synonym_suggestions": suggestions[:15],
    }
    if apply and suggestions:
        cfg = load_config(ix.root)
        syn = cfg.get("synonyms") or {}
        for s in suggestions[:15]:
            syn.setdefault(s["when_query_has"], [])
            if s["also_search"] not in syn[s["when_query_has"]]:
                syn[s["when_query_has"]].append(s["also_search"])
        cfg["synonyms"] = syn
        save_config(ix.root, {k: v for k, v in cfg.items()})
        ix.cfg = load_config(ix.root)
        report["applied"] = {k: syn[k] for k in rules if k in syn}
    return report


def _cases_from_eval(path: str) -> list[tuple[str, set[str]]]:
    d = json.loads(open(path, encoding="utf-8").read())
    cases = d["cases"] if isinstance(d, dict) else d
    return [(c.get("query") or c.get("task"), set(c["expected"])) for c in cases]


def _cases_from_ledger(ix: CodeIndex, days: int) -> list[tuple[str, set[str]]]:
    out = []
    for e in episodes(ix, days):
        names = set()
        for f in e["fetched"]:
            if f["sym_id"] is not None:
                r = ix.db.execute("SELECT name FROM symbols WHERE id=?", (f["sym_id"],)).fetchone()
                if r:
                    names.add(r["name"])
        if names:
            out.append((e["query"], names))
    return out


def _mrr(ix: CodeIndex, cases, weights, vec: bool) -> float:
    from . import semantic
    total = 0.0
    for q, expected in cases:
        vh = semantic.search(ix, q, 60) if vec else None
        res = ix.hybrid_search(q, limit=10, vector_hits=vh, weights=weights)
        rank = next((i + 1 for i, r in enumerate(res) if r["name"] in expected or r["qualname"] in expected), None)
        total += 1 / rank if rank else 0
    return total / len(cases)


def tune_weights(ix: CodeIndex, eval_file: str | None = None, days: int = 90, apply: bool = False) -> dict:
    """Grid-search hybrid fusion weights (name, body[, vectors]) for MRR@10 on an eval file or the ledger."""
    from . import semantic
    cases = _cases_from_eval(eval_file) if eval_file else _cases_from_ledger(ix, days)
    if len(cases) < 5:
        raise ValueError(f"Need at least 5 query -> expected pairs to tune (have {len(cases)}). "
                         "Pass eval_file, or let the ledger collect more searches followed by fetches.")
    vec = semantic.has_vectors(ix)
    current = ix.fusion_weights(vec)
    grid = [0.25, 0.5, 0.8, 1.0, 1.5, 2.0]
    combos = [(1.0, b, v) for b, v in itertools.product(grid, grid)] if vec else [(1.0, b) for b in grid]
    base = _mrr(ix, cases, current, vec)
    scored = sorted(((_mrr(ix, cases, w, vec), w) for w in combos), key=lambda x: -x[0])
    best_mrr, best = scored[0]
    improved = best_mrr > base + 0.01
    res = {"cases": len(cases), "source": eval_file or f"ledger ({days} days)", "with_vectors": vec,
           "current": {"weights": list(current), "mrr": round(base, 3)},
           "best": {"weights": list(best), "mrr": round(best_mrr, 3)},
           "top5": [{"weights": list(w), "mrr": round(m, 3)} for m, w in scored[:5]], "improved": improved,
           "note": "Saved only with apply=True and a gain of more than 0.01 MRR; small case sets overfit, "
                   "so keep a held-out eval and re-check it after applying."}
    if apply and improved:
        cfg = load_config(ix.root)
        fw = cfg.get("fusion_weights") or {}
        fw["with_vectors" if vec else "lexical"] = list(best)
        cfg["fusion_weights"] = fw
        save_config(ix.root, cfg)
        ix.cfg = load_config(ix.root)
        res["applied"] = fw
    return res
