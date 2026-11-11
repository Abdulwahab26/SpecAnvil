"""Multi-repo retrieval eval for get_context: is a symbol an engineer needs in the budgeted context, and how high?

    python tests/evals/run_suite.py <bench-dir> [--min-found 0.9] [--min-mrr 0.6] [--verbose]

<bench-dir> holds the checkouts named like the dataset files in tests/evals/suite/ (gin, express, fastapi,
spring-petclinic). A case counts as found when any expected symbol is in the context (full body, inside a full body, or signature);
MRR uses the position of the first expected symbol. Expected names are verified to exist in the index first, so a
stale dataset fails loudly instead of scoring zero quietly.
"""
import json
import re
import sys
from pathlib import Path

from specforge.index import CodeIndex

ID_RX = re.compile(r"^(?:### .*?\[(\d+)\]|- \[(\d+)\])", re.M)


def run_repo(repo: Path, data: dict, verbose: bool) -> dict:
    ix = CodeIndex(repo)
    ix.build()
    found = full = 0
    mrr = 0.0
    rows = []
    for case in data["cases"]:
        exp = set(case["expected"])
        missing = [e for e in exp if not ix.db.execute("SELECT 1 FROM symbols WHERE name=? LIMIT 1", (e,)).fetchone()]
        if missing:
            raise SystemExit(f"{repo.name}: expected symbols not in the index (stale dataset?): {missing}")
        text, _ = ix.context_for(case["task"], data["budget_tokens"])
        order, in_full = [], set()
        for m in ID_RX.finditer(text):
            sid = int(m.group(1) or m.group(2))
            row = ix.db.execute("SELECT name, path, start_line, end_line FROM symbols WHERE id=?", (sid,)).fetchone()
            if not row:
                continue
            names = {row["name"]}
            if m.group(1):  # a full body also delivers every symbol nested inside it (a class with its methods)
                names |= {r[0] for r in ix.db.execute(
                    "SELECT name FROM symbols WHERE path=? AND start_line>=? AND end_line<=?",
                    (row["path"], row["start_line"], row["end_line"]))}
                in_full |= names
            order.append(names)
        rank = next((i + 1 for i, names in enumerate(order) if names & exp), None)
        found += rank is not None
        full += bool(exp & in_full)
        mrr += 1 / rank if rank else 0
        rows.append((case["task"][:62], rank or "-", ", ".join(sorted(n)[0] for n in order[:3])[:60]))
    n = len(data["cases"])
    if verbose:
        for r in rows:
            print(f"  {r[0]:<63} {str(r[1]):>4}  {r[2]}")
    return {"repo": repo.name, "cases": n, "found": found / n, "full": full / n, "mrr": mrr / n}


def main(argv: list[str]) -> int:
    bench = Path(argv[1])
    verbose = "--verbose" in argv
    def opt(name: str, default: float) -> float:
        return float(argv[argv.index(name) + 1]) if name in argv else default
    min_found, min_mrr = opt("--min-found", 0.0), opt("--min-mrr", 0.0)
    results = []
    for ds in sorted((Path(__file__).parent / "suite").glob("*.json")):
        repo = bench / ds.stem
        if not repo.is_dir():
            print(f"skip {ds.stem}: no checkout at {repo}")
            continue
        if verbose:
            print(f"\n{ds.stem}")
        results.append(run_repo(repo, json.loads(ds.read_text(encoding="utf-8")), verbose))
    print(f"\n{'repo':<20} {'cases':>5} {'found':>6} {'full':>6} {'MRR':>6}")
    for r in results:
        print(f"{r['repo']:<20} {r['cases']:>5} {r['found']:>6.2f} {r['full']:>6.2f} {r['mrr']:>6.2f}")
    n = sum(r["cases"] for r in results)
    if not n:
        return 1
    tot = {k: sum(r[k] * r["cases"] for r in results) / n for k in ("found", "full", "mrr")}
    print(f"{'ALL':<20} {n:>5} {tot['found']:>6.2f} {tot['full']:>6.2f} {tot['mrr']:>6.2f}")
    ok = tot["found"] >= min_found and tot["mrr"] >= min_mrr
    if not ok:
        print(f"FAILED the gate: found >= {min_found}, MRR >= {min_mrr}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
