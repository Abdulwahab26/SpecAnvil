"""Concept-retrieval eval: lexical vs hybrid (vs embeddings when built). Metric: hit@k and MRR of first expected symbol.

    python tests/evals/run_semantic_eval.py <repo> tests/evals/semantic_fullstack_fastapi.json
"""
import json
import sys
from pathlib import Path

from specforge.index import CodeIndex


def run(repo: str, dataset: str) -> dict:
    data = json.loads(Path(dataset).read_text())
    ix = CodeIndex(repo)
    ix.build()
    modes = {"lexical": lambda q: ix.search(q, limit=data["k"]),
             "hybrid": lambda q: ix.hybrid_search(q, limit=data["k"])}
    try:
        from specforge import semantic
        if semantic.has_vectors(ix):
            modes["semantic"] = lambda q: semantic.search(ix, q, limit=data["k"])
            modes["hybrid+vectors"] = lambda q: ix.hybrid_search(q, limit=data["k"], vector_hits=semantic.search(ix, q, 60))
    except ImportError:
        pass
    res = {}
    for name, fn in modes.items():
        hits, mrr, rows = 0, 0.0, []
        for c in data["cases"]:
            names = [r["name"] for r in fn(c["query"])]
            rank = next((i + 1 for i, n in enumerate(names) if n in c["expected"]), None)
            hits += 1 if rank else 0
            mrr += 1 / rank if rank else 0
            rows.append((c["query"][:50], rank or "-", names[:3]))
        res[name] = {"hit@k": round(hits / len(data["cases"]), 2), "mrr": round(mrr / len(data["cases"]), 2), "rows": rows}
    return res


if __name__ == "__main__":
    out = run(sys.argv[1], sys.argv[2])
    for mode, r in out.items():
        print(f"{mode:<16} hit@5 {r['hit@k']:.2f}  MRR {r['mrr']:.2f}")
    if "-v" in sys.argv:
        for mode, r in out.items():
            print("\n" + mode)
            for row in r["rows"]:
                print("  ", row)
