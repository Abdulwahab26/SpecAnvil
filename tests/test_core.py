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
    res = run(root)
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
    run(root)
    assert "TEAM RULE" in spec.read_text()
    assert (root / "CLAUDE.md").read_text().count(MARK_START) == 1
    settings = json.loads((root / ".claude/settings.json").read_text())
    assert len(settings["hooks"]["PostToolUse"]) == 1


def test_drift_detects_changed_code(tmp_path):
    root = make_repo(tmp_path)
    run(root)
    ix = CodeIndex(root)
    assert not [i for i in drift(root, ix)["issues"] if i["issue"] in ("changed", "missing")]
    f = root / "src/orders/service.py"
    f.write_text(f.read_text().replace('raise ValueError("empty")', 'raise ValueError("no items")'))
    ix.build()
    issues = drift(root, ix)["issues"]
    assert any(i["issue"] == "changed" and i["ref"].endswith("OrderService") for i in issues)
    lock_specs(root, ix)
    assert not [i for i in drift(root, ix)["issues"] if i["issue"] == "changed"]
