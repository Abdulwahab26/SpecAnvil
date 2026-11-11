"""Several agent sessions on one repo: readers search, fetch and log while a writer edits files and re-indexes.

    python tests/stress_concurrency.py <repo> [seconds]

Fails if any process hits "database is locked" (or any other error), or if a reader ever sees an empty index.
"""
import multiprocessing as mp
import sys
import time
from pathlib import Path


def reader(root: str, seconds: float, q, n: int) -> None:
    from specforge import regret
    from specforge.index import CodeIndex
    ix = CodeIndex(root)
    ops = errors = 0
    qs = ["create user", "password hash", "login token", "items list", "send email", "settings config"]
    end = time.time() + seconds
    try:
        while time.time() < end:
            query = qs[ops % len(qs)]
            hits = ix.search(query, limit=5)
            if not hits:
                errors += 1
            ix.context_for(query, 2000)
            ix.record("search_symbols", "x" * 200, 1000, 1.0)                      # ledger write
            regret.log_call(ix, f"s{n}", "search_symbols", {"query": query}, "\n".join(f"[{h['id']}]" for h in hits))
            ops += 1
        q.put(("reader", ops, errors, ""))
    except Exception as e:  # noqa: BLE001
        q.put(("reader", ops, errors + 1, repr(e)))


def writer(root: str, seconds: float, q) -> None:
    from specforge.index import CodeIndex
    ix = CodeIndex(root)
    target = Path(root) / "specforge_stress_tmp.py"
    ops = 0
    end = time.time() + seconds
    try:
        while time.time() < end:
            target.write_text(f"def stress_fn_{ops}():\n    return {ops}\n", encoding="utf-8")
            ix.build(only=[target.name])
            if ops % 5 == 4:
                ix.build()
            ops += 1
        target.unlink(missing_ok=True)
        ix.build()
        q.put(("writer", ops, 0, ""))
    except Exception as e:  # noqa: BLE001
        target.unlink(missing_ok=True)
        q.put(("writer", ops, 1, repr(e)))


def main(root: str, seconds: float = 15.0, readers: int = 4) -> int:
    from specforge.index import CodeIndex
    CodeIndex(root).build()
    q: mp.Queue = mp.Queue()
    procs = [mp.Process(target=reader, args=(root, seconds, q, i)) for i in range(readers)]
    procs += [mp.Process(target=writer, args=(root, seconds, q)), mp.Process(target=writer, args=(root, seconds, q))]
    for p in procs:
        p.start()
    results = [q.get(timeout=seconds + 120) for _ in procs]
    for p in procs:
        p.join()
    bad = 0
    for kind, ops, errors, msg in results:
        print(f"{kind:<7} ops={ops:<5} errors={errors} {msg}")
        bad += errors
    print("OK" if not bad else f"FAILED: {bad} errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 15.0))
