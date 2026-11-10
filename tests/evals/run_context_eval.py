"""Retrieval eval for get_context: does the budgeted context contain the symbols an engineer would need?

Metrics per case: recall@full (expected symbols whose full body was included), recall@any (included as
body or signature), and MRR of the first expected symbol. Run:
    python tests/evals/run_context_eval.py <repo-path> tests/evals/context_fullstack_fastapi.json
"""
import json
import re
import sys
from pathlib import Path

from specforge.index import CodeIndex


def main(repo: str, dataset: str) -> int:
    data = json.loads(Path(dataset).read_text())
    ix = CodeIndex(repo)
    ix.build()
    rows, tot_full, tot_any, mrr = [], 0.0, 0.0, 0.0
    for case in data["cases"]:
        text, _ = ix.context_for(case["task"], data["budget_tokens"])
        full = re.findall(r"^### ([\w.]+)", text, re.M)
        sigs = re.findall(r"^- \[\d+\] \w+ (.+?)  \(", text, re.M)
        order = [f.split(".")[-1] for f in full] + [re.split(r"[\s(<:]", s.split(" ", 1)[-1].replace("def ", "")
                                                    .replace("function ", "").replace("class ", ""))[0] for s in sigs]
        exp = case["expected"]
        in_full = [e for e in exp if any(f.split(".")[-1] == e or f == e for f in full)]
        in_any = [e for e in exp if e in order or e in in_full]
        rank = next((i + 1 for i, n in enumerate(order) if n in exp), None)
        tot_full += len(in_full) / len(exp)
        tot_any += len(in_any) / len(exp)
        mrr += 1 / rank if rank else 0
        rows.append((case["task"][:58], f"{len(in_full)}/{len(exp)}", f"{len(in_any)}/{len(exp)}", rank or "-"))
    n = len(data["cases"])
    print(f"{'task':<60} {'full':>5} {'any':>5} {'rank':>5}")
    for r in rows:
        print(f"{r[0]:<60} {r[1]:>5} {r[2]:>5} {r[3]:>5}")
    print(f"\nrecall@full {tot_full / n:.2f} · recall@any {tot_any / n:.2f} · MRR {mrr / n:.2f} "
          f"· budget {data['budget_tokens']} tokens · {n} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
