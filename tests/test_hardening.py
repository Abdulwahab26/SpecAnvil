"""Safety and maintenance: test runner, log pruning, SDK registry, guard hooks."""
import json
import time
from pathlib import Path

import pytest
from helpers import (
    _tool_repo,
)

from specanvil.index import CodeIndex


def test_test_command_is_allowlisted_and_never_a_shell(tmp_path):
    from specanvil.testing import safe_test_argv
    root = _tool_repo(tmp_path)
    argv, env = safe_test_argv("CI=1 python -m pytest {test_file} -q", root, "tests/test_a.py")
    assert Path(argv[0]).name.startswith("python") and argv[1:] == ["-m", "pytest", "tests/test_a.py", "-q"] and env == {"CI": "1"}
    for bad in ["pytest {test_file}; rm -rf /", "pytest {test_file} && curl x | sh", "pytest `whoami` {test_file}",
                "pytest $(id) {test_file}", "pytest {test_file} > /etc/passwd", "rm -rf {test_file}",
                "curl http://x/{test_file}", "bash -c {test_file}", "sh {test_file}"]:
        with pytest.raises(ValueError):
            safe_test_argv(bad, root, "t.py")
    (root / ".specanvil").mkdir(exist_ok=True)
    (root / ".specanvil" / "config.json").write_text(json.dumps({"allowed_test_commands": ["bazel test"]}), encoding="utf-8")
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
    from specanvil import server
    assert isinstance(server._registry(), dict) and "search_symbols" in server._ALL_TOOLS
    monkeypatch.setattr(server, "_registry", lambda: None)
    assert "search_symbols" in server._apply_tier({"search_symbols"})  # SDK changed: everything stays listed


def test_doc_sections_can_be_switched_off(tmp_path):
    (tmp_path / "README.md").write_text("# A\n\n## B\ntext\n", encoding="utf-8")
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".specanvil").mkdir()
    (tmp_path / ".specanvil" / "config.json").write_text('{"index_doc_sections": false}', encoding="utf-8")
    ix = CodeIndex(tmp_path)
    ix.build()
    assert {r[0] for r in ix.db.execute("SELECT kind FROM symbols")} == {"function"}


def test_repo_set_up_under_the_old_name_upgrades_in_place(tmp_path, monkeypatch):
    """A repo initialised by SpecForge (the old name) keeps its index, settings and user content, and ends up with
    exactly one SpecAnvil server, hook and rules block per file."""
    from specanvil.bootstrap import run
    from specanvil.uninstall import uninstall
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    (root / ".specforge").mkdir()
    (root / ".specforge" / "config.json").write_text('{"agents": ["claude", "cursor", "copilot"], "guard_secrets": true}',
                                                    encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Mine\n\nKeep it.\n\n<!-- specforge:start -->\nold block\n<!-- specforge:end -->\n",
                                    encoding="utf-8")
    (root / ".mcp.json").write_text('{"mcpServers": {"specforge": {"command": "specforge-mcp", "args": []}, '
                                    '"mine": {"command": "x"}}}', encoding="utf-8")
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [
        {"type": "command", "command": "specforge hook guard"}]}]}, "permissions": {"allow": ["mcp__specforge"]}}),
        encoding="utf-8")
    (root / ".cursor").mkdir()
    (root / ".cursor" / "hooks.json").write_text('{"version": 1, "hooks": {"beforeReadFile": [{"command": "specforge hook guard"}]}}',
                                                 encoding="utf-8")
    (root / ".github" / "hooks").mkdir(parents=True)
    (root / ".github" / "hooks" / "specforge.json").write_text('{"version": 1, "hooks": {"preToolUse": [{"type": "command", '
                                                               '"command": "specforge hook guard"}]}}', encoding="utf-8")
    res = run(root, offline=True)
    assert res["agents"] == ["claude", "cursor", "copilot"]                     # old config carried over
    assert (root / ".specanvil" / "config.json").exists() and not (root / ".specforge").exists()
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.startswith("# Mine") and "Keep it." in claude_md and "old block" not in claude_md
    assert claude_md.count("specanvil:start") == 1 and "specforge:start" not in claude_md
    servers = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "specforge" not in servers and servers["specanvil"]["command"] == "specanvil-mcp" and servers["mine"] == {"command": "x"}
    settings = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
    assert "specforge" not in settings and settings.count("specanvil hook guard") == 1
    cursor = json.loads((root / ".cursor" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["beforeReadFile"]
    assert cursor == [{"command": "specanvil hook guard"}]
    assert not (root / ".github/hooks/specforge.json").exists() and (root / ".github/hooks/specanvil.json").exists()
    uninstall(root)
    left = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "specanvil" not in left and left["mine"] == {"command": "x"}
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n\nKeep it.\n"


def test_old_environment_variable_names_still_work():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", "import specanvil, os; print(os.environ.get('SPECANVIL_TOOLS'))"],
                         env={**__import__("os").environ, "SPECFORGE_TOOLS": "core"}, capture_output=True, text=True)
    assert out.stdout.strip() == "core"


def test_old_sf_workflow_files_are_replaced_by_sa_on_upgrade(tmp_path, monkeypatch):
    """Workflows moved from /sf: to /sa: in 0.10: init must remove the old files, not leave every workflow twice."""
    from helpers import make_repo

    from specanvil import bootstrap
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    (tmp_path / "repo").mkdir()
    root = make_repo(tmp_path / "repo")
    old = [".claude/commands/sf/groom.md", ".cursor/commands/sf-groom.md", ".github/prompts/sf-flow.prompt.md",
           ".github/agents/sf-architect.agent.md", ".agents/skills/sf-plan/SKILL.md"]
    mine = [".claude/commands/sf/my-own.md", ".claude/commands/deploy.md"]
    for rel in old + mine:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("old\n", encoding="utf-8")
    (root / ".specanvil").mkdir()
    (root / ".specanvil" / "manifest.json").write_text(json.dumps(
        {"tools": {"claude": [".claude/commands/sf/groom.md"], "cursor": [".cursor/commands/sf-groom.md"]}, "servers": {}}),
        encoding="utf-8")
    bootstrap.run(root, offline=True, agents="claude,cursor,copilot,codex")
    assert not any((root / rel).exists() for rel in old)
    assert all((root / rel).exists() for rel in mine)  # files SpecAnvil never wrote are left alone
    assert (root / ".claude/commands/sa/groom.md").exists() and (root / ".cursor/commands/sa-groom.md").exists()
    assert (root / ".agents/skills/sa-plan/SKILL.md").exists() and not (root / ".agents/skills/sf-plan").exists()
    manifest = (root / ".specanvil" / "manifest.json").read_text(encoding="utf-8")
    assert "/sa/groom.md" in manifest and "/sf/" not in manifest and "sf-" not in manifest
    assert "/sf:" not in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_pr_comment_reports_risk_and_drift(tmp_path, monkeypatch):
    """`specanvil pr-comment`: the changed symbol is scored against the base ref and a broken spec reference is listed."""
    import subprocess

    from helpers import make_repo

    from specanvil.bootstrap import run
    from specanvil.report import PR_COMMENT_MARK, pr_comment
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    (tmp_path / "repo").mkdir()
    root = make_repo(tmp_path / "repo")
    run(root, offline=True, agents="claude")
    (root / "specs" / "features" / "orders").mkdir(parents=True)
    (root / "specs" / "features" / "orders" / "requirements.md").write_text(
        "# Orders\n\nTotals come from `src/orders/service.py::total` and `src/orders/service.py::gone`.\n", encoding="utf-8")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(root)]
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    svc = root / "src" / "orders" / "service.py"
    svc.write_text(svc.read_text().replace('return sum(i["price"] for i in items)',
                                           'if not items:\n        return 0\n    return sum(i["price"] for i in items)'))
    ix = CodeIndex(root)
    ix.build()
    text = pr_comment(root, ix, "HEAD")
    assert text.startswith(PR_COMMENT_MARK)
    assert "`src/orders/service.py::total`" in text and "Change risk:" in text
    assert "src/orders/service.py::gone` is missing" in text
    import contextlib
    import io

    from specanvil.cli import main as cli_main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli_main(["drift", str(root), "--json", "--strict"]) == 1     # machine-readable, for editors
    assert {"spec", "ref", "issue"} <= set(json.loads(buf.getvalue())["issues"][0])
    subprocess.run(git + ["commit", "-qam", "change"], check=True)
    ix.build()
    assert "Change risk:** none" in pr_comment(root, ix, "HEAD")
