"""Run `specanvil proof` on the demo shop with one of the planted changes for ticket SHOP-77.

    python tests/evals/run_proof_demo.py wrong       # also: safe, boundary
    python tests/evals/run_proof_demo.py --all       # all three, then the caught/missed table

Needs Node.js. The first run installs @playwright/test into a scratch copy of the fixture (set PW_CHANNEL=chrome to
use an installed Chrome instead of downloading a browser with `npx playwright install chromium`).
The fixture repo is created under the system temp folder; nothing in this repository is modified.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent / "proof"
EXPECTED = {"safe": "auto", "wrong": "review", "boundary": "review"}
GIT = ["git", "-c", "user.email=demo@example.com", "-c", "user.name=demo"]


def prepare(change: str, work: Path) -> Path:
    """A git repo holding the committed fixture, with one planted change, its intent contract and probes staged."""
    repo = work / change
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(HERE / "fixture", repo, ignore=shutil.ignore_patterns("node_modules", ".specanvil"))
    (repo / ".specanvil").mkdir()
    (repo / ".specanvil" / "config.json").write_text(json.dumps({
        "app_url": "http://localhost:4173",
        "proof": {"e2e_cmd": "npx playwright test", "test_paths": ["e2e"]}}, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--silent"], cwd=repo, check=True,
                   shell=sys.platform == "win32")  # node_modules is ignored by git, so it stays put during the stash
    subprocess.run(GIT + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(GIT + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(GIT + ["commit", "-qm", "shop: base"], cwd=repo, check=True)
    shutil.copy(HERE / "changes" / change / "pricing.js", repo / "pricing.js")
    (repo / "e2e" / "probes").mkdir()
    shutil.copy(HERE / "probes" / "SHOP-77.probe.spec.js", repo / "e2e" / "probes")
    (repo / ".specanvil" / "proof" / "intents").mkdir(parents=True)
    shutil.copy(HERE / "intent" / "SHOP-77.json", repo / ".specanvil" / "proof" / "intents")
    subprocess.run(GIT + ["add", "-A"], cwd=repo, check=True)
    return repo


def prove(change: str, work: Path, quiet: bool = False) -> dict:
    repo = prepare(change, work)
    p = subprocess.run([sys.executable, "-m", "specanvil.cli", "proof", "run", "SHOP-77", "--author-model", "opus",
                        "--author-tool", "demo", "--json", "--path", str(repo)], capture_output=True, text=True)
    if p.returncode not in (0, 3):
        sys.exit(f"proof failed for '{change}':\n{p.stderr}")
    rec = json.loads(p.stdout)
    if not quiet:
        subprocess.run([sys.executable, "-m", "specanvil.cli", "proof", "show", "--path", str(repo)], check=True)
    status = subprocess.run(GIT + ["status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert "pricing.js" in status and not subprocess.run(GIT + ["stash", "list"], cwd=repo, capture_output=True,
                                                         text=True).stdout, "the working tree was not restored"
    return rec


def main() -> int:
    args = sys.argv[1:] or ["wrong"]
    work = Path(tempfile.gettempdir()) / "specanvil-proof-demo"
    work.mkdir(exist_ok=True)
    if args[0] != "--all":
        if args[0] not in EXPECTED:
            sys.exit(f"unknown change '{args[0]}'. Use one of: {', '.join(EXPECTED)} or --all")
        rec = prove(args[0], work)
        return 0 if rec["decision"]["route"] == EXPECTED[args[0]] else 1
    ok = True
    print(f"{'change':<10} {'expected':<8} {'got':<8} {'risk':>4}  findings")
    for change, want in EXPECTED.items():
        rec = prove(change, work, quiet=True)
        by = rec["behaviour"]["by_class"]
        found = ", ".join(f"{n} {k}" for k, n in sorted(by.items())) + f", {len(rec['probes'])} failed probes"
        got = rec["decision"]["route"]
        ok &= got == want
        print(f"{change:<10} {want:<8} {got:<8} {rec['risk']['score']:>4}  {found}")
    print("all planted changes routed as expected" if ok else "MISMATCH: see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
