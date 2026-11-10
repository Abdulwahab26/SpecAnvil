import json
import subprocess
from pathlib import Path

from specforge.bootstrap import MARK_START, run
from specforge.index import CodeIndex, module_of, split_terms, stem
from specforge.parser import _parse_regex, parse_source
from specforge.specs import drift, lock_specs, read_section
from specforge.tokens import redact


def make_repo(tmp: Path) -> Path:
    (tmp / "src" / "orders").mkdir(parents=True)
    (tmp / "src" / "orders" / "service.py").write_text(
        'class OrderService:\n    """Creates orders."""\n    def create(self, items):\n'
        '        if not items:\n            raise ValueError("empty")\n        return {"items": items}\n\n'
        'def total(items):\n    return sum(i["price"] for i in items)\n')
    (tmp / "src" / "orders" / "test_service.py").write_text("def test_total():\n    assert True\n")
    (tmp / "requirements.txt").write_text("fastapi\nsqlalchemy\npsycopg\npytest\n")
    (tmp / ".env").write_text("SECRET=abc123456\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def test_split_terms_and_stem():
    assert split_terms("getUserByID", "orders/service.py") == "get user by id orders service py"
    assert stem("tokens") == stem("token")
    assert stem("expired").startswith("expir") and stem("expiry").startswith("expir")


def test_module_of():
    assert module_of("src/orders/service.py") == "orders"
    assert module_of("services/billing/src/main/java/com/acme/shop/billing/X.java") == "services/billing"
    assert module_of("packages/ui/src/button/Button.tsx") == "packages/ui/button"
    assert module_of("main.go") == "(root)"


def test_regex_fallback_finds_nested_symbols():
    src = "export class Api {\n  public async get(id: string) {\n    return id;\n  }\n}\nfunction helper() {}\n"
    fp = _parse_regex(src, "unknownlang")
    names = {s.qualname: s for s in fp.symbols}
    assert "Api" in names and "helper" in names


def test_tree_sitter_parse_python():
    fp = parse_source("a.py", "class A:\n    def m(self):\n        pass\n")
    assert [s.qualname for s in fp.symbols] == ["A", "A.m"]
    assert fp.symbols[1].start_line == 2


def test_redaction():
    out = redact('password = "hunter2hunter2"\nurl = "postgres://u:supersecret@db/x"\nAKIAABCDEFGHIJKLMNOP')
    assert "hunter2" not in out and "supersecret" not in out and "AKIA" not in out


def test_index_skips_secrets_and_searches(tmp_path):
    root = make_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    paths = {r[0] for r in ix.db.execute("SELECT path FROM files")}
    assert ".env" not in paths
    hits = ix.search("order create")
    assert hits[0]["qualname"] == "OrderService.create"
    text, files = ix.context_for("validate empty orders when creating", 2000)
    assert "OrderService.create" in text and files


def test_section_read():
    md = "# T\n## A\none\n### A1\nsub\n## B\ntwo\n"
    assert read_section(md, "A") == "## A\none\n### A1\nsub"
    assert read_section(md, "b") == "## B\ntwo"


def test_init_is_idempotent_and_preserves_user_content(tmp_path):
    root = make_repo(tmp_path)
    (root / "CLAUDE.md").write_text("# Mine\n\nKeep this line.\n")
    res = run(root, offline=True)
    assert "postgres" in res["config"]["databases"]
    claude = (root / "CLAUDE.md").read_text()
    assert "Keep this line." in claude and claude.count(MARK_START) == 1
    assert (root / ".claude/commands/sf/flow.md").exists()
    assert (root / ".claude/skills/db-optimization/SKILL.md").read_text().count("PostgreSQL") >= 1
    mcp = json.loads((root / ".mcp.json").read_text())
    assert {"specforge", "postgres"} <= set(mcp["mcpServers"])
    # user edits a generated spec; a second run must keep it
    spec = root / "specs/conventions.md"
    spec.write_text(spec.read_text() + "\nTEAM RULE\n")
    run(root, offline=True)
    assert "TEAM RULE" in spec.read_text()
    assert (root / "CLAUDE.md").read_text().count(MARK_START) == 1
    settings = json.loads((root / ".claude/settings.json").read_text())
    assert len(settings["hooks"]["PostToolUse"]) == 1


def test_drift_detects_changed_code(tmp_path):
    root = make_repo(tmp_path)
    run(root, offline=True)
    ix = CodeIndex(root)
    assert not [i for i in drift(root, ix)["issues"] if i["issue"] in ("changed", "missing")]
    f = root / "src/orders/service.py"
    f.write_text(f.read_text().replace('raise ValueError("empty")', 'raise ValueError("no items")'))
    ix.build()
    issues = drift(root, ix)["issues"]
    assert any(i["issue"] == "changed" and i["ref"].endswith("OrderService") for i in issues)
    lock_specs(root, ix)
    assert not [i for i in drift(root, ix)["issues"] if i["issue"] == "changed"]


def test_indexing_makes_no_network_calls(tmp_path, monkeypatch):
    """Indexing must be fully local: block sockets and index languages whose grammar is not cached."""
    import socket

    from specforge import parser

    root = make_repo(tmp_path)
    (root / "lib.rb").write_text("class Greeter\n  def hello(name)\n    puts name\n  end\nend\n")
    (root / "main.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")

    def blocked(*a, **k):
        raise AssertionError("network access attempted during indexing")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(parser, "_LOCAL_GRAMMARS", parser.local_grammars() - {"ruby", "rust"})
    ix = CodeIndex(root)
    ix.build()
    names = {r[0] for r in ix.db.execute("SELECT qualname FROM symbols")}
    assert {"Greeter", "add", "OrderService.create"} <= names  # regex fallback + cached grammar


def test_incremental_fingerprints(tmp_path):
    root = make_repo(tmp_path)
    ix = CodeIndex(root)
    assert ix.build()["files_changed"] > 0
    assert ix.build()["files_changed"] == 0            # nothing re-read
    f = root / "src/orders/service.py"
    f.touch()
    assert ix.build()["files_changed"] == 0            # touched, same content: hash check only
    f.write_text(f.read_text() + "\ndef extra():\n    return 1\n")
    r = ix.build()
    assert r["files_changed"] == 1
    assert ix.find_symbol("extra")


def _plan(test_file: str) -> dict:
    return {"module": "orders", "test_framework": "pytest", "test_command": "python -m pytest {test_file} -q -p no:cacheprovider",
            "coverage_target": 80, "milestones": [{
                "id": "M1", "title": "total()", "priority": 1, "status": "todo", "complexity": "S",
                "targets": ["src/orders/service.py::total"], "test_file": test_file,
                "cases": [{"id": "M1-C1", "name": "sums prices", "kind": "happy", "given": "two items",
                           "when": "total", "then": "sum"}],
                "challenges": [{"challenge": "none", "approach": "pure function"}], "done_when": ["passes"]}]}


def test_milestone_done_is_verified_by_running_tests(tmp_path):
    from specforge import testing
    root = make_repo(tmp_path)
    run(root, offline=True)
    d = testing.plan_dir(root, "orders")
    d.mkdir(parents=True)
    tf = "src/orders/test_total_ms.py"
    (d / "milestones.json").write_text(json.dumps(_plan(tf)))
    plan, _ = testing.load_plan(root, "orders")
    assert testing.validate_plan(plan, CodeIndex(root)) == []

    (root / tf).write_text("def test_bad():\n    assert 1 == 2\n")
    res = testing.update_milestone(root, "orders", "M1", "done", tests_added=1)
    assert res["accepted"] is False and testing.load_plan(root, "orders")[0]["milestones"][0]["status"] == "in_progress"

    (root / tf).write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n"
                           "from service import total\n\ndef test_ok():\n    assert total([{'price': 2}, {'price': 3}]) == 5\n")
    res = testing.update_milestone(root, "orders", "M1", "done", tests_added=1)
    assert res["accepted"] is True and res["progress"] == "1/1 done"


def test_plan_validation_catches_bad_plans(tmp_path):
    from specforge import testing
    root = make_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    bad = _plan("../outside.py")
    bad["test_command"] = "pytest"
    bad["milestones"][0]["targets"] = ["src/orders/service.py::does_not_exist"]
    bad["milestones"][0]["status"] = "finished"
    errs = " | ".join(testing.validate_plan(bad, ix))
    assert "{test_file}" in errs and "does not exist" in errs and "status" in errs and "relative path" in errs


def test_blocked_requires_reason(tmp_path):
    import pytest
    from specforge import testing
    root = make_repo(tmp_path)
    d = testing.plan_dir(root, "orders")
    d.mkdir(parents=True)
    (d / "milestones.json").write_text(json.dumps(_plan("x_test.py")))
    with pytest.raises(ValueError):
        testing.update_milestone(root, "orders", "M1", "blocked")


def test_profile_models_render_into_agents(tmp_path, monkeypatch):
    import pytest
    from specforge import profile
    from specforge.bootstrap import sync_agents
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    root = make_repo(tmp_path / "repo")
    run(root, offline=True)
    planner = root / ".claude/agents/unit-test-planner.md"
    assert "model: opus" in planner.read_text()
    profile.set_value("models.test_planner", "sonnet")
    assert sync_agents(root) == [".claude/agents/unit-test-planner.md"]
    text = planner.read_text()
    assert "model: sonnet" in text and text.count("model:") == 1 and text.startswith("---\nname: unit-test-planner")
    with pytest.raises(ValueError):
        profile.set_value("models.test_planner", "gpt-4")
    with pytest.raises(ValueError):
        profile.set_value("models.nobody", "opus")


def test_guard_hook_blocks_secrets(tmp_path):
    from specforge.cli import _guard
    assert _guard({"tool_name": "Read", "tool_input": {"file_path": "/r/.env"}, "cwd": str(tmp_path)}) == 2
    assert _guard({"tool_name": "Read", "tool_input": {"file_path": "/r/.env.example"}, "cwd": str(tmp_path)}) == 0
    assert _guard({"tool_name": "Bash", "tool_input": {"command": "cat config/.env"}, "cwd": str(tmp_path)}) == 2
    assert _guard({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "cwd": str(tmp_path)}) == 0
