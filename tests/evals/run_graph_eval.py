"""How accurate is the static call graph? Ground truth from execution.

Runs this repo's own unit tests under a profiler, recording every real (caller -> callee) pair between functions
in src/specforge, then compares with the static graph built by the index:

  call-graph recall   = executed call pairs that the static graph also has
  dead-code precision = symbols reported dead that were, in fact, never executed (lower bound: unexecuted code
                        may still be alive, executed code is certainly not dead)

    python tests/evals/run_graph_eval.py [--min-recall 0.7]

Python only (the tracer is Python's); it measures the resolver's logic, which is shared by all languages.
"""
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = str(ROOT / "src" / "specforge")
PAIRS: set[tuple] = set()
EXECUTED: set[tuple] = set()


def _key(code):
    return (code.co_filename, code.co_firstlineno)


def _profile(frame, event, arg):  # noqa: ARG001
    if event != "call":
        return
    code = frame.f_code
    if not code.co_filename.startswith(SRC):
        return
    EXECUTED.add(_key(code))
    back = frame.f_back
    # attribute the call to the nearest caller frame inside src/specforge (skip functools wrappers etc.)
    hops = 0
    while back is not None and hops < 3 and not back.f_code.co_filename.startswith(SRC):
        back = back.f_back
        hops += 1
    if back is not None and back.f_code.co_filename.startswith(SRC):
        PAIRS.add((_key(back.f_code), _key(code)))


def main(argv: list[str]) -> int:
    import pytest
    min_recall = float(argv[argv.index("--min-recall") + 1]) if "--min-recall" in argv else 0.0
    sys.setprofile(_profile)
    threading.setprofile(_profile)
    rc = pytest.main(["-q", "-x", "-p", "no:cacheprovider", str(ROOT / "tests")])
    sys.setprofile(None)
    threading.setprofile(None)
    if rc != 0:
        print("unit tests failed; no measurement")
        return 1

    from specforge.graph import CodeGraph, dead_code
    from specforge.index import CodeIndex
    ix = CodeIndex(ROOT)
    ix.build()
    funcs = [dict(r) for r in ix.db.execute(
        "SELECT id, path, qualname, start_line, end_line FROM symbols WHERE kind IN ('function','method') "
        "AND path LIKE 'src/specforge/%'")]
    by_file: dict[str, list[dict]] = {}
    for f in funcs:
        by_file.setdefault(str(ROOT / f["path"]), []).append(f)

    def sym(key) -> int | None:
        cands = [f for f in by_file.get(key[0], []) if f["start_line"] <= key[1] <= f["end_line"]]
        return min(cands, key=lambda f: f["end_line"] - f["start_line"])["id"] if cands else None

    dyn = set()
    for a, b in PAIRS:
        sa, sb = sym(a), sym(b)
        if sa and sb and sa != sb:
            dyn.add((sa, sb))
    g = CodeGraph(ix)
    static = set(g.edges())
    hit = dyn & static
    recall = len(hit) / len(dyn) if dyn else 0.0
    executed_ids = {s for s in (sym(k) for k in EXECUTED) if s}
    static_src = {(a, b) for a, b in static if a in g.sym and b in g.sym
                  and g.sym[a]["path"].startswith("src/specforge/") and g.sym[b]["path"].startswith("src/specforge/")}
    both_ran = {(a, b) for a, b in static_src if a in executed_ids and b in executed_ids}
    confirmed = len(both_ran & dyn) / len(both_ran) if both_ran else 0.0
    dead = [d for d in dead_code(ix, limit=10_000, include_exported=True)
            if d.get("ref", "").startswith("src/specforge/") and d.get("kind") in ("function", "method")]
    dead_ids = {d["id"] for d in dead}
    wrong = dead_ids & executed_ids
    name = {f["id"]: f"{f['path']}::{f['qualname']}" for f in funcs}
    print(f"\nexecuted functions: {len(executed_ids)} of {len(funcs)}")
    print(f"executed call pairs: {len(dyn)}   static graph has {len(hit)}   recall {recall:.2f}")
    print(f"static edges between executed functions: {len(both_ran)}   seen at runtime {confirmed:.2f} "
          "(the rest are branches the tests did not take, or false edges)")
    print(f"dead-code reports in src: {len(dead_ids)}   proven wrong by execution: {len(wrong)}   "
          f"precision >= {1 - len(wrong) / len(dead_ids):.2f}" if dead_ids else "dead-code reports in src: 0")
    for a, b in sorted(dyn - static, key=lambda e: name.get(e[0], ""))[:12]:
        print(f"  missed: {name.get(a)} -> {name.get(b)}")
    for w in sorted(wrong)[:8]:
        print(f"  not dead: {name.get(w)}")
    for d in dead[:8]:
        print(f"  reported dead: {d['ref']}")
    if recall < min_recall:
        print(f"FAILED the gate: call-graph recall >= {min_recall}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
