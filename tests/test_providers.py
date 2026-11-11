"""Setup for every AI coding tool."""
import json
import subprocess
import sys

import pytest
from helpers import (
    _tool_repo,
)

from specanvil.index import CodeIndex


def test_detect_and_resolve_tools(tmp_path):
    from specanvil import providers as P
    root = _tool_repo(tmp_path)
    assert P.resolve(root, None) == ["claude"]  # nothing detected: Claude Code
    (root / ".cursor").mkdir()
    (root / ".github").mkdir()
    (root / ".github" / "copilot-instructions.md").write_text("# rules\n")
    assert P.resolve(root, None) == ["copilot", "cursor"]
    assert P.resolve(root, "kiro,replit") == ["kiro", "replit"]
    assert P.resolve(root, "all") == P.ALL
    with pytest.raises(ValueError):
        P.resolve(root, "notepad")


def test_translate_dialects():
    from specanvil.providers import translate
    src = "Run /sa:plan, then follow `.claude/commands/sa/be.md` and `.claude/skills/x`. Input: $ARGUMENTS. Use subagents."
    assert translate(src, "claude") == src
    cp = translate(src, "copilot")
    assert "/sa-plan" in cp and ".github/prompts/sa-be.prompt.md" in cp and ".github/skills/x" in cp and "${input:target}" in cp
    assert "subagents" in cp  # Copilot can run subagents
    cu = translate(src, "cursor")
    assert "/sa-plan" in cu and ".cursor/commands/sa-be.md" in cu and "$ARGUMENTS" not in cu and "subagent" not in cu
    assert "#sa-plan" in translate(src, "kiro") and "{{args}}" in translate(src, "gemini")
    assert "the sa-plan skill" in translate(src, "codex") and "the plan playbook" in translate(src, "replit")


def test_init_for_every_tool_writes_valid_native_files(tmp_path, monkeypatch):
    from specanvil.bootstrap import run
    from specanvil.providers import split_frontmatter
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        tomllib = None
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    res = run(root, agents="all", offline=True)
    assert res["agents"] == ["claude", "copilot", "cursor", "augment", "windsurf", "codex", "kiro", "gemini", "replit"]
    must = [".claude/commands/sa/groom.md", ".mcp.json", "CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md",
            ".github/prompts/sa-groom.prompt.md", ".github/agents/sa-architect.agent.md", ".github/skills/code-navigation/SKILL.md",
            ".vscode/mcp.json", ".cursor/commands/sa-groom.md", ".cursor/rules/code-navigation.mdc", ".cursor/mcp.json",
            ".augment/commands/sa/groom.md", ".augment/skills/code-navigation/SKILL.md", ".specanvil/mcp/augment-import.json",
            ".agents/skills/sa-groom/SKILL.md", ".devin/mcp_config.json", ".codex/config.toml", ".codex/agents/sa-architect.toml",
            ".kiro/steering/sa-groom.md", ".kiro/steering/product.md", ".kiro/steering/tech.md", ".kiro/settings/mcp.json",
            ".gemini/commands/sa/groom.toml", ".gemini/settings.json", "GEMINI.md", "replit.md", ".specanvil/playbooks/groom.md",
            "specs/templates/requirements.md"]
    missing = [m for m in must if not (root / m).exists()]
    assert not missing, missing
    assert json.loads((root / ".vscode/mcp.json").read_text(encoding="utf-8"))["servers"]["specanvil"]["env"]["SPECANVIL_ROOT"] == "${workspaceFolder}"
    assert "specanvil" in json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    if tomllib:
        assert tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["specanvil"]["command"] == "specanvil-mcp"
        g = tomllib.loads((root / ".gemini/commands/sa/groom.toml").read_text(encoding="utf-8"))
        assert "{{args}}" in g["prompt"] and g["description"]
        assert tomllib.loads((root / ".codex/agents/sa-architect.toml").read_text(encoding="utf-8"))["name"] == "sa-architect"
    for f in [root / ".github/prompts/sa-groom.prompt.md", root / ".github/agents/sa-architect.agent.md",
              root / ".cursor/rules/code-navigation.mdc", root / ".kiro/steering/code-navigation.md",
              root / ".agents/skills/sa-groom/SKILL.md"]:
        meta, body = split_frontmatter(f.read_text(encoding="utf-8"))
        assert meta and body.strip(), f
        for v in meta.values():  # quoted scalars are JSON strings, so they must decode cleanly
            if v.startswith('"'):
                json.loads(v)
    skill, _ = split_frontmatter((root / ".agents/skills/sa-groom/SKILL.md").read_text(encoding="utf-8"))
    assert skill["name"] == "sa-groom" and len(json.loads(skill["description"])) <= 1024
    assert "inclusion: manual" in (root / ".kiro/steering/sa-groom.md").read_text(encoding="utf-8")
    assert ".kiro/specs/" in (root / ".github/prompts/sa-groom.prompt.md").read_text(encoding="utf-8")  # Kiro chose the feature layout
    assert "specanvil tool" in (root / "replit.md").read_text(encoding="utf-8")
    for d in (".github", ".cursor", ".kiro", ".codex"):
        for f in (root / d).rglob("*"):
            if f.is_file() and f.suffix in (".md", ".mdc", ".toml"):
                t = f.read_text(encoding="utf-8")
                assert ".claude/" not in t and "$ARGUMENTS" not in t and "/sa:" not in t, f


def test_init_only_for_detected_tool_and_user_content_kept(tmp_path, monkeypatch):
    from specanvil.bootstrap import run
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text('{"mcpServers": {"mine": {"command": "my-server"}}}')
    (root / "AGENTS.md").write_text("# Team notes\n\nKeep PRs small.\n")
    res = run(root, offline=True)
    assert res["agents"] == ["cursor"]
    assert not (root / ".claude").exists() and not (root / "CLAUDE.md").exists() and not (root / ".mcp.json").exists()
    servers = json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["mine"] == {"command": "my-server"} and "specanvil" in servers
    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# Team notes") and "Keep PRs small." in agents_md and "/sa-groom" in agents_md
    run(root, offline=True)  # re-run: block replaced, not duplicated
    assert (root / "AGENTS.md").read_text(encoding="utf-8").count("specanvil:start") == 1


def test_root_discovery_for_any_client(tmp_path):
    from specanvil.config import discover_root
    root = _tool_repo(tmp_path)
    (root / ".specanvil").mkdir(exist_ok=True)
    deep = root / "app" / "sub"
    deep.mkdir(parents=True, exist_ok=True)
    assert discover_root(deep, env={}) == root.resolve()
    assert discover_root(tmp_path, env={"SPECANVIL_ROOT": str(root)}) == root.resolve()
    assert discover_root(deep, env={"SPECANVIL_ROOT": "${workspaceFolder}"}) == root.resolve()  # unexpanded var ignored


def test_provider_models_in_profile(tmp_path, monkeypatch):
    from specanvil import profile
    from specanvil.bootstrap import run
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    profile.set_value("providers.copilot.models.architect", "Claude Opus 4.5")
    with pytest.raises(ValueError):
        profile.set_value("providers.cursor.models.architect", "x")
    root = _tool_repo(tmp_path)
    run(root, agents="copilot", offline=True)
    assert 'model: "Claude Opus 4.5"' in (root / ".github/agents/sa-architect.agent.md").read_text(encoding="utf-8")
    assert "model:" not in (root / ".github/agents/sa-reviewer.agent.md").read_text(encoding="utf-8")


def test_cli_tool_bridge(tmp_path):
    root = _tool_repo(tmp_path)
    CodeIndex(root).build()
    out = subprocess.run([sys.executable, "-m", "specanvil.cli", "tool", "search_symbols", '{"query": "create user"}',
                          "--path", str(root)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "create_user" in out.stdout, out.stderr
    bad = subprocess.run([sys.executable, "-m", "specanvil.cli", "tool", "nope", "--path", str(root)],
                         capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2
