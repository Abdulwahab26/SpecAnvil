"""Do calls made through an injected dependency reach the right method?

    python tests/evals/run_di_eval.py <bench-dir> [--min-recall 0.9] [--max-wrong 0.1] [--verbose]

<bench-dir> holds the checkouts named in tests/evals/di_edges.json. Each case is a call site (file:line) and the
method it really calls, written by reading the code. A case is `right` when the graph links the calling function to
that method, `wrong` when it links the call to a method of the same name somewhere else and not to the right one,
and `missing` otherwise. The same cases are also run with `typed_receivers` off to show what the change adds.
"""
import json
import sys
from pathlib import Path

from specanvil.graph import CodeGraph
from specanvil.index import CodeIndex


def run_repo(repo: Path, edges: list[dict], typed: bool, verbose: bool) -> tuple[int, int]:
    ix = CodeIndex(repo)
    ix.build()
    ix.cfg["typed_receivers"] = typed
    g = CodeGraph(ix)
    right = wrong = 0
    for e in edges:
        row = ix.db.execute(
            "SELECT id FROM symbols WHERE path=? AND kind IN ('function','method') AND start_line<=? AND end_line>=? "
            "ORDER BY end_line-start_line LIMIT 1", (e["file"], e["line"], e["line"])).fetchone()
        if not row:
            raise SystemExit(f"{repo.name}: no function at {e['file']}:{e['line']} (stale dataset?)")
        targets = [g.sym[t] for t, line in g.callees(row["id"]) if line == e["line"] and g.sym[t]["name"] == e["callee"]]
        ok = any(t["path"].endswith(e["target"]) for t in targets)
        right += ok
        wrong += bool(targets) and not ok
        if verbose and typed:
            got = ", ".join(sorted({t["path"].rsplit("/", 1)[-1] for t in targets})) or "-"
            print(f"  {'ok ' if ok else 'BAD'} {e['file'].rsplit('/', 1)[-1]}:{e['line']} {e['callee']} -> {got}")
    return right, wrong


def main(argv: list[str]) -> int:
    bench = Path(argv[1])
    verbose = "--verbose" in argv

    def opt(name: str, default: float) -> float:
        return float(argv[argv.index(name) + 1]) if name in argv else default
    min_recall, max_wrong = opt("--min-recall", 0.0), opt("--max-wrong", 1.0)
    data = json.loads((Path(__file__).parent / "di_edges.json").read_text(encoding="utf-8"))["repos"]
    print(f"{'repo':<18} {'framework':<22} {'cases':>5} {'right':>6} {'wrong':>6}   without typed receivers: right / wrong")
    tot = [0, 0, 0, 0, 0]
    for name, d in data.items():
        repo = bench / name
        if not repo.is_dir():
            print(f"skip {name}: no checkout at {repo}")
            continue
        if verbose:
            print(name)
        r, w = run_repo(repo, d["edges"], True, verbose)
        r0, w0 = run_repo(repo, d["edges"], False, False)
        n = len(d["edges"])
        print(f"{name:<18} {d['framework']:<22} {n:>5} {r:>6} {w:>6}   {r0:>3} / {w0}")
        tot = [a + b for a, b in zip(tot, (n, r, w, r0, w0))]
    n = tot[0]
    if not n:
        return 1
    print(f"{'ALL':<41} {n:>5} {tot[1]:>6} {tot[2]:>6}   {tot[3]:>3} / {tot[4]}")
    print(f"recall {tot[1] / n:.2f}  wrong {tot[2] / n:.2f}   (without typed receivers: recall {tot[3] / n:.2f}  wrong {tot[4] / n:.2f})")
    return 0 if tot[1] / n >= min_recall and tot[2] / n <= max_wrong else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
