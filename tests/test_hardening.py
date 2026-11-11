"""Safety and maintenance: test runner, log pruning, SDK registry, guard hooks."""
import json
import time
from pathlib import Path

import pytest
from helpers import (
    _tool_repo,
)

from specforge.index import CodeIndex


def test_test_command_is_allowlisted_and_never_a_shell(tmp_path):
    from specforge.testing import safe_test_argv
    root = _tool_repo(tmp_path)
    argv, env = safe_test_argv("CI=1 python -m pytest {test_file} -q", root, "tests/test_a.py")
    assert Path(argv[0]).name.startswith("python") and argv[1:] == ["-m", "pytest", "tests/test_a.py", "-q"] and env == {"CI": "1"}
    for bad in ["pytest {test_file}; rm -rf /", "pytest {test_file} && curl x | sh", "pytest `whoami` {test_file}",
                "pytest $(id) {test_file}", "pytest {test_file} > /etc/passwd", "rm -rf {test_file}",
                "curl http://x/{test_file}", "bash -c {test_file}", "sh {test_file}"]:
        with pytest.raises(ValueError):
            safe_test_argv(bad, root, "t.py")
    (root / ".specforge").mkdir(exist_ok=True)
    (root / ".specforge" / "config.json").write_text(json.dumps({"allowed_test_commands": ["bazel test"]}), encoding="utf-8")
    assert safe_test_argv("bazel test {test_file}", root, "//a:t")[0][-2:] == ["test", "//a:t"]


def test_logs_are_pruned_and_totals_survive(tmp_path):
    root = _tool_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    old = time.time() - 200 * 86400
    ix.db.execute("INSERT INTO ledger(ts, tool, returned, baseline, ms) VALUES(?,?,?,?,?)", (old, "get_symbol", 100, 1000, 1.0))
    ix.record("get_symbol", "x" * 400, 2000, 1.0)
    ix.db.commit()
    before = ix.savings()
    res = ix.prune_logs(force=True)
    assert res["removed"]["ledger"] == 1
    after = ix.savings()
    assert (after["calls"], after["baseline_tokens"], after["returned_tokens"]) == \
        (before["calls"], before["baseline_tokens"], before["returned_tokens"]) == (2, 3000, 200)
    assert ix.db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 1
    assert ix.prune_logs().get("skipped")  # at most once a day


def test_sdk_tool_registry_is_reachable_and_tiers_degrade_gracefully(monkeypatch):
    from specforge import server
    assert isinstance(server._registry(), dict) and "search_symbols" in server._ALL_TOOLS
    monkeypatch.setattr(server, "_registry", lambda: None)
    assert "search_symbols" in server._apply_tier({"search_symbols"})  # SDK changed: everything stays listed


def test_doc_sections_can_be_switched_off(tmp_path):
    (tmp_path / "README.md").write_text("# A\n\n## B\ntext\n", encoding="utf-8")
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".specforge").mkdir()
    (tmp_path / ".specforge" / "config.json").write_text('{"index_doc_sections": false}', encoding="utf-8")
    ix = CodeIndex(tmp_path)
    ix.build()
    assert {r[0] for r in ix.db.execute("SELECT kind FROM symbols")} == {"function"}
