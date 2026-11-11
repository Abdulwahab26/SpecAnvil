"""OpenSpec mode and uninstall."""
import json

from helpers import (
    _openspec_repo,
    _tool_repo,
)

from specanvil.index import CodeIndex


def test_openspec_status_check_coverage_gaps(tmp_path):
    from specanvil import openspec as O
    root = _openspec_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    st = O.status(root)
    assert (st["capabilities"], st["requirements"], st["scenarios"], st["archived_changes"]) == (1, 2, 2, 1)
    assert st["active_changes"][0]["tasks"] == "1/2" and st["active_changes"][0]["delta"]["ADDED"] == 1
    c = O.check(root, ix)
    issues = " | ".join(i["issue"] for i in c["issues"])
    assert "`app/crud.py::gone` does not resolve" in issues          # stale code reference
    assert "requirement 'Old thing' has no scenario" in issues
    assert "MODIFIED requirement 'Signup makes a user' is not in signup" in issues
    assert "create_user` does not resolve" not in issues
    cov = O.coverage(root, ix, "signup")
    kinds = {r["scenario"]: r["coverage"] for r in cov["rows"]}
    assert kinds == {"Valid payload creates a user": "explicit", "Empty payload is rejected": "likely"}
    assert O.coverage(root, ix, "add-email-check")["none"] == 2
    assert any(g["module"] for g in O.gaps(root, ix)["unspecified_modules"]) or O.gaps(root, ix)["count"] == 0


def test_openspec_mode_init_does_not_create_a_second_spec_system(tmp_path, monkeypatch):
    from specanvil.bootstrap import run
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _openspec_repo(tmp_path)
    before = {p: p.read_bytes() for p in (root / "openspec").rglob("*.md")}
    res = run(root, agents="claude,cursor", offline=True)
    cfg = res["config"]
    assert cfg["spec_system"] == "openspec" and cfg["features_dir"] == "openspec/changes" and cfg["specs_dir"] == "openspec/specanvil"
    assert not (root / "specs").exists()
    assert all(p.read_bytes() == b for p, b in before.items())      # OpenSpec's own files are untouched
    groom = (root / ".claude/commands/sa/groom.md").read_text(encoding="utf-8")
    assert "This repo uses OpenSpec" in groom and groom.startswith("---")
    assert (root / ".claude/commands/sa/baseline.md").exists() and (root / ".cursor/commands/sa-baseline.md").exists()
    assert "This repo uses OpenSpec" in (root / ".cursor/commands/sa-groom.md").read_text(encoding="utf-8")
    assert "### OpenSpec" in (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert not (root / "openspec/specanvil/templates/requirements.md").exists()
    from specanvil.specs import drift, list_specs
    paths = {s["path"] for s in list_specs(root)}
    assert "openspec/specs/signup/spec.md" in paths and "openspec/changes/add-email-check/proposal.md" in paths
    d = drift(root, CodeIndex(root))
    assert any(i["ref"] == "app/crud.py::gone" and i["issue"] == "missing" for i in d["issues"])


def test_uninstall_round_trip_keeps_user_content(tmp_path, monkeypatch):
    from specanvil.bootstrap import run
    from specanvil.uninstall import uninstall
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    (root / "AGENTS.md").write_text("# Team\n\nOur notes.\n", encoding="utf-8")
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text('{"mcpServers": {"mine": {"command": "x"}}}', encoding="utf-8")
    (root / ".cursor" / "hooks.json").write_text('{"version": 1, "hooks": {"afterFileEdit": [{"command": "fmt.sh"}]}}', encoding="utf-8")
    run(root, agents="claude,cursor,copilot,codex", offline=True)
    assert (root / ".github/hooks/specanvil.json").exists()
    hooks = json.loads((root / ".cursor/hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert {"command": "specanvil hook guard"} in hooks["beforeReadFile"] and {"command": "fmt.sh"} in hooks["afterFileEdit"]
    dry = uninstall(root, ["cursor"], dry_run=True)
    assert dry["dry_run"] and (root / ".cursor/commands/sa-groom.md").exists()
    res = uninstall(root, ["cursor", "copilot"])
    assert res["remaining_tools"] == ["claude", "codex"]
    assert not (root / ".cursor/commands").exists() and not (root / ".github/prompts").exists()
    assert json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8")) == {"mcpServers": {"mine": {"command": "x"}}}
    assert json.loads((root / ".cursor/hooks.json").read_text(encoding="utf-8"))["hooks"] == {"afterFileEdit": [{"command": "fmt.sh"}]}
    assert "specanvil:start" in (root / "AGENTS.md").read_text(encoding="utf-8")   # Codex still needs it
    assert (root / ".claude/commands/sa/groom.md").exists()
    uninstall(root)
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "# Team\n\nOur notes.\n"
    assert not (root / ".claude").exists() and not (root / "CLAUDE.md").exists() and not (root / ".codex").exists()
    assert (root / "specs/project.md").exists()                                  # specs are never removed


def test_guard_speaks_claude_cursor_and_copilot():
    from specanvil.cli import _guard_hit
    assert _guard_hit({"tool_name": "Read", "tool_input": {"file_path": ".env"}}) == (".env", "claude")
    assert _guard_hit({"hook_event_name": "beforeReadFile", "file_path": "/r/.env.production"})[1] == "cursor"
    assert _guard_hit({"hook_event_name": "beforeShellExecution", "command": "cat secrets.json"})[0] == "secrets.json"
    assert _guard_hit({"toolName": "readFile", "toolArgs": '{"filePath": "/r/id_rsa"}'}) == ("/r/id_rsa", "copilot")
    assert _guard_hit({"toolName": "readFile", "toolArgs": {"filePath": "src/app.py"}})[0] is None
    assert _guard_hit({"hook_event_name": "beforeReadFile", "file_path": "/r/.env.example"})[0] is None
