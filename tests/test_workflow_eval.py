"""Self-check of the workflow eval harness (no model needed): it must tell an untouched repo from a finished one."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "evals"))
import run_workflow_eval as wf  # noqa: E402


def test_untouched_fixture_fails_and_reference_solution_passes_code_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    wf.prepare(repo)
    before = wf.score(repo)["checks"]
    assert before["regression"]["pass"] and before["scope"]["pass"]
    assert not before["acceptance"]["pass"] and not before["own_tests"]["pass"] and not before["spec"]["pass"]

    shutil.copytree(wf.HERE / "reference", repo, dirs_exist_ok=True)
    assert not wf.score(repo)["checks"]["drift"]["pass"]   # module specs describe checkout(), which just changed
    wf._run([sys.executable, "-m", "specanvil.cli", "drift", "--lock"], repo)   # what /sa:verify does after review
    after = wf.score(repo)["checks"]
    assert all(after[k]["pass"] for k in ("regression", "acceptance", "own_tests", "drift", "scope")), after
    assert not after["spec"]["pass"]      # the reference adds code only; writing the spec is the workflow's job

    (repo / "shop" / "discounts.py").write_text(
        (repo / "shop" / "discounts.py").read_text() + "\n\ndef is_expired(code):\n    return False\n")
    assert not wf.score(repo)["checks"]["scope"]["pass"]
