"""Workflow eval: does the lifecycle workflow turn a fixed ticket into a working, specified, tested change?

    python tests/evals/run_workflow_eval.py prepare <dir> [--agents claude]
    #   ... run the workflow in <dir> with your AI coding tool:  /sa:flow SHOP-42 --auto
    python tests/evals/run_workflow_eval.py score <dir> [--json]

`prepare` copies the fixture repo (tests/evals/workflow/fixture: a small order service and the ticket
tickets/SHOP-42.md), commits it and runs `specanvil init`. `score` checks what the agent left behind:

  acceptance    the hidden acceptance tests for the ticket pass (they are copied in only for scoring)
  regression    the tests that existed before still pass
  own_tests     the agent added tests of its own, and they pass
  spec          a feature spec exists with requirements, design, tasks and test cases
  drift         every code reference in the specs resolves (specanvil drift --strict)
  scope         nothing outside the ticket's scope was added (no expiry, one-time or stacked codes)

The score is the number of checks passed out of six; the eval passes only at 6/6. Running the workflow needs a
model, so CI runs only the harness self-check (tests/test_workflow_eval.py): the untouched fixture must fail
`acceptance`, and the reference solution must pass every code check.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent / "workflow"
SPEC_PARTS = ("requirements", "design", "tasks", "test-cases")


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)


def _pytest(root: Path, *targets: str) -> tuple[bool, str]:
    r = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets], root)
    tail = (r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1]
    return r.returncode == 0, tail


def prepare(dest: Path, agents: str = "claude") -> None:
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty")
    shutil.copytree(HERE / "fixture", dest, dirs_exist_ok=True)
    git = ["git", "-c", "user.email=eval@example.com", "-c", "user.name=eval", "-C", str(dest)]
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "fixture"], check=True)
    r = _run([sys.executable, "-m", "specanvil.cli", "init", "--offline", "--agents", agents], dest)
    if r.returncode != 0:
        raise SystemExit(r.stderr or r.stdout)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "specanvil init"], check=True)
    print(f"Prepared {dest}\nNow run the workflow there, for example in Claude Code:\n\n"
          "    /sa:flow SHOP-42 --auto\n\n(the ticket is tickets/SHOP-42.md), then:\n\n"
          f"    python {Path(__file__).as_posix()} score {dest}")


def score(root: Path) -> dict:
    checks: dict[str, dict] = {}
    base_tests = sorted(p.relative_to(HERE / "fixture").as_posix() for p in (HERE / "fixture" / "tests").glob("test_*.py"))

    ok, tail = _pytest(root, *base_tests)
    checks["regression"] = {"pass": ok, "detail": tail}

    hidden = root / "tests" / "_acceptance_shop42.py"
    shutil.copy(HERE / "acceptance" / "test_acceptance.py", hidden)
    try:
        ok, tail = _pytest(root, hidden.relative_to(root).as_posix())
    finally:
        hidden.unlink(missing_ok=True)
    checks["acceptance"] = {"pass": ok, "detail": tail}

    own = sorted(p.relative_to(root).as_posix() for p in root.rglob("test_*.py")
                 if p.relative_to(root).as_posix() not in base_tests and ".specanvil" not in p.parts)
    grown = [t for t in base_tests if (root / t).is_file() and
             len(re.findall(r"^def test_", (root / t).read_text(encoding="utf-8"), re.M)) >
             len(re.findall(r"^def test_", (HERE / "fixture" / t).read_text(encoding="utf-8"), re.M))]
    if own or grown:
        ok, tail = _pytest(root, *(own + grown))
        checks["own_tests"] = {"pass": ok, "detail": f"{len(own)} new test files, {len(grown)} extended: {tail}"}
    else:
        checks["own_tests"] = {"pass": False, "detail": "no tests were added"}

    features = [d for d in (root / "specs" / "features").glob("*") if d.is_dir() and not d.name.startswith("_")] \
        if (root / "specs" / "features").is_dir() else []
    best, missing = None, list(SPEC_PARTS)
    for d in features:
        miss = [p for p in SPEC_PARTS if not (d / f"{p}.md").is_file() or len((d / f"{p}.md").read_text(encoding="utf-8")) < 200]
        if len(miss) < len(missing) or best is None:
            best, missing = d, miss
    checks["spec"] = {"pass": best is not None and not missing,
                      "detail": f"{best.name}: missing {missing}" if best is not None and missing else
                      (best.name if best is not None else "no feature spec under specs/features/")}

    r = _run([sys.executable, "-m", "specanvil.cli", "drift", "--strict"], root)
    checks["drift"] = {"pass": r.returncode == 0, "detail": (r.stdout.strip().splitlines() or [""])[0][:160]}

    code = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (root / "shop").glob("*.py"))
    creep = sorted(set(re.findall(r"expir|one_time|single_use|stack|max_uses|valid_until", code.lower())))
    checks["scope"] = {"pass": not creep, "detail": f"out-of-scope names in shop/: {creep}" if creep else "in scope"}

    passed = sum(c["pass"] for c in checks.values())
    return {"ticket": "SHOP-42", "passed": passed, "of": len(checks), "ok": passed == len(checks), "checks": checks}


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("prepare", "score"):
        print(__doc__)
        return 2
    target = Path(argv[2]).resolve()
    if argv[1] == "prepare":
        prepare(target, argv[argv.index("--agents") + 1] if "--agents" in argv else "claude")
        return 0
    res = score(target)
    if "--json" in argv:
        print(json.dumps(res, indent=1))
    else:
        for name, c in res["checks"].items():
            print(f"  {'PASS' if c['pass'] else 'FAIL'}  {name:<11} {c['detail']}")
        print(f"{res['passed']}/{res['of']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
