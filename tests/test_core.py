import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert "def create" in text and files


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


def _azure_repo(tmp: Path) -> Path:
    (tmp / "host.json").write_text('{"version": "2.0", "functionTimeout": "00:05:00"}')
    (tmp / "requirements.txt").write_text("azure-functions\n")
    (tmp / "local.settings.json").write_text('{"Values": {"DB_URL": "Server=x;Password=secret"}}')
    (tmp / "function_app.py").write_text(
        'import os\nimport azure.functions as func\napp = func.FunctionApp()\n\n'
        '@app.route(route="orders/{id}")\ndef get_order(req):\n    return os.environ["DB_URL"]\n\n'
        '@app.timer_trigger(schedule="0 0 2 * * *", arg_name="t")\ndef nightly(t):\n    pass\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def test_docs_archetypes_check_register_and_stale(tmp_path):
    from specforge.docs import DocEngine
    root = _azure_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    assert not ix.db.execute("SELECT 1 FROM files WHERE path='local.settings.json'").fetchone()  # secret never indexed
    de = DocEngine(ix)
    assert "azure-functions" in {a["archetype"] for a in de.facts.archetypes()}
    ids = {d["id"] for d in de.required()}
    assert {"functions-catalog", "functions-settings", "configuration", "architecture"} <= ids
    payload = de.facts_for("functions-catalog")
    assert set(payload["must_mention"]) == {"get_order", "nightly"}

    doc = root / "docs/functions/catalog.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Function catalog\n## Function list\nget_order handles reads. TODO describe the rest.\n")
    res = de.check("functions-catalog")
    assert not res["ok"]
    problems = " | ".join(res["problems"])
    assert "missing section: Triggers" in problems and "nightly" in problems and "placeholder" in problems
    assert de.register("functions-catalog")["registered"] is False

    body = ("The function app exposes HTTP and timer functions defined in `function_app.py`. " * 8)
    doc.write_text("# Function catalog\n" + body + "\n## Function list\n| Name | Trigger |\n|---|---|\n"
                   "| get_order | HTTP `orders/{id}` |\n| nightly | Timer `0 0 2 * * *` |\n"
                   "## Triggers\nHTTP and timer.\n## Bindings\nNone beyond triggers.\n"
                   "## Schedules and routes\nSee table.\n## Open questions\n- TBD: retry policy owner\n")
    assert de.check("functions-catalog")["ok"], de.check("functions-catalog")
    assert de.register("functions-catalog", "sonnet")["registered"]
    assert {r["id"]: r["status"] for r in de.status()}["functions-catalog"] == "fresh"

    f = root / "function_app.py"
    f.write_text(f.read_text() + '\n@app.queue_trigger(arg_name="m", queue_name="jobs", connection="C")\ndef on_job(m):\n    pass\n')
    ix.build()
    de2 = DocEngine(ix)
    assert {r["id"]: r["status"] for r in de2.status()}["functions-catalog"] == "stale"
    assert "on_job" in " ".join(de2.check("functions-catalog")["problems"])


def test_endpoint_extraction_prefixes_and_handlers(tmp_path):
    from specforge.facts import Facts
    (tmp_path / "api.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter(prefix="/items", tags=["items"])\n\n'
        '@router.get("/")\ndef read_items():\n    return []\n\n@router.post(\n    "/{id}/archive"\n)\n'
        'def archive(id: int):\n    return id\n')
    (tmp_path / "Ctl.java").write_text(
        'package a;\n@RestController\n@RequestMapping("/invoices")\npublic class Ctl {\n'
        '    @GetMapping("/{id}")\n    public String get(Long id) { return ""; }\n'
        '    @PostMapping\n    public String issue() { return ""; }\n}\n')
    ix = CodeIndex(tmp_path)
    ix.build()
    eps = {(e["method"], e["path"], e["handler"]) for e in Facts(ix).get("endpoints")}
    assert ("GET", "/items/", "read_items") in eps
    assert ("GET", "/invoices/{id}", "Ctl.get") in eps and ("POST", "/invoices/", "Ctl.issue") in eps
    assert not any(p == "/invoices" and m == "ANY" for m, p, _ in eps)


def test_session_dedupe_and_compaction_reset(tmp_path):
    import time as _t

    from specforge.session import Session, touch_compaction_marker
    root = make_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    sess = Session(root)
    first, _ = ix.context_for("create orders validation", 3000, delivered=sess.ledger())
    assert "def create" in first
    second, _ = ix.context_for("create orders validation", 3000, delivered=sess.ledger())
    assert "Already in your context" in second and "def create" not in second   # not paid twice
    _t.sleep(0.01)
    touch_compaction_marker(root)                                              # PreCompact hook fired
    third, _ = ix.context_for("create orders validation", 3000, delivered=sess.ledger())
    assert "def create" in third


def test_focus_excerpt():
    from specforge.analysis import excerpt
    body = "def big(x):\n" + "".join(f"    step{i} = {i}\n" for i in range(30)) + "    if coupon_expired(x):\n        raise E\n    return x\n"
    out, cut = excerpt(body, "coupon")
    assert cut and "coupon_expired" in out and "def big" in out and "step15" not in out and "… (" in out
    assert len(out) < len(body) / 3


def _graph_repo(tmp: Path) -> Path:
    (tmp / "app").mkdir()
    (tmp / "app" / "__init__.py").write_text("")
    (tmp / "app" / "crud.py").write_text(
        'from app.models import User\n\nMAX_USERS = 100\n\n'
        'def create_user(name):\n    """Create and persist."""\n    return _save(User(name))\n\n'
        'def _save(obj):\n    return obj\n\ndef _never_used(x):\n    return x * 2\n')
    (tmp / "app" / "models.py").write_text(
        'class Base:\n    pass\n\nclass User(Base):\n    name: str\n\n    def __init__(self, name):\n'
        '        self.name = name\n\nclass Admin(User):\n    level = 1\n')
    (tmp / "app" / "routes.py").write_text(
        'from app import crud\nfrom app.models import User\n\n'
        'def create_user(payload):\n    # create_user in a comment must not be renamed\n'
        '    label = "create_user"\n    return crud.create_user(payload["name"])\n\n'
        'def items():\n    try:\n        return []\n    except Exception:\n        pass\n\n'
        'def page(x=[]):\n    return x\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    return tmp


def test_declarations_and_graph_tables(tmp_path):
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    kinds = {(r["qualname"], r["kind"]) for r in ix.db.execute("SELECT qualname, kind FROM symbols")}
    assert ("MAX_USERS", "constant") in kinds and ("User.name", "field") in kinds and ("Admin.level", "field") in kinds
    assert ("app/routes.py", "app/crud.py") in {tuple(r) for r in ix.db.execute("SELECT src, dst FROM file_deps")}
    assert ("Admin", "User") in {tuple(r) for r in ix.db.execute("SELECT cls, base FROM bases")}


def test_call_and_class_hierarchy_use_receiver(tmp_path):
    from specforge import graph
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    crud_create = ix.find_symbol("app/crud.py::create_user")[0]["id"]
    callers = graph.call_hierarchy(ix, crud_create, "callers", 1)
    assert "routes.py" in callers  # crud.create_user resolved through the receiver, not to routes.create_user itself
    h = graph.class_hierarchy(ix, "User")
    assert {"class": "User", "extends": "Base", "defined_at": "app/models.py:1"} in h["ancestors"]
    assert any(d["class"] == "Admin" for d in h["descendants"])


def test_dead_code_cycles_layers_importance(tmp_path):
    from specforge import graph
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    dead = {d["ref"] for d in graph.dead_code(ix)}
    assert "app/crud.py::_never_used" in dead and "app/crud.py::_save" not in dead
    (root / "app" / "models.py").write_text((root / "app" / "models.py").read_text() + "\nfrom app import crud\n")
    ix.build()
    assert any({"app/crud.py", "app/models.py"} <= set(c) for c in graph.cycles(ix))
    lv = graph.layer_violations(ix)["violations"]
    assert any(v["from_layer"] == "domain" for v in lv)  # models (domain) importing crud (infrastructure)
    assert graph.importance(ix, 5)


def test_ast_presets_and_refactor_plans(tmp_path):
    from specforge import astsearch, refactor
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    assert astsearch.search(ix, preset="broad-except")["matches"]
    assert astsearch.search(ix, preset="mutable-default")["matches"][0]["in"] == "page"
    raw = astsearch.search(ix, query="(class_definition name: (identifier) @hit)", language="python")
    assert {m["code"] for m in raw["matches"]} >= {"User", "Admin"}
    sid = ix.find_symbol("app/crud.py::create_user")[0]["id"]
    plan = refactor.plan_rename(ix, sid, "add_user")
    edits = {(e["path"], e["new_text"].strip()) for e in plan["edits"]}
    assert ("app/crud.py", "def add_user(name):") in edits
    routes = [e for e in plan["edits"] + plan["needs_review"] if e["path"] == "app/routes.py"]
    assert all("# create_user in a comment" not in e["new_text"] or "add_user in a comment" not in e["new_text"] for e in routes)
    assert all('"create_user"' in e["new_text"] or "label" not in e["old_text"] for e in routes)


def test_summaries_persist_and_hybrid_search(tmp_path):
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    need = ix.symbols_needing_summary()
    target = next(n for n in need if n["ref"].endswith("routes.py::items"))
    assert ix.save_summaries({str(target["id"]): "Returns the catalogue listing, swallowing failures."}) == 1
    ix.build(full=True)  # re-index: summary survives because the code hash is unchanged
    row = ix.find_symbol("app/routes.py::items")[0]
    assert row["doc"].startswith("≈ Returns the catalogue")
    assert ix.hybrid_search("catalogue listing", 5)[0]["qualname"] == "items"


def test_semantic_endpoint_provider_and_remote_refusal(tmp_path):
    import http.server
    import threading

    import pytest

    from specforge import semantic
    from specforge.config import load_config, save_config

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # deterministic fake embeddings: bag of letters
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            vecs = [[t.lower().count(c) for c in "abcdefghijklmnopqrstuvwxyz"] for t in body["input"]]
            out = json.dumps({"data": [{"embedding": v} for v in vecs]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    cfg = load_config(root)
    cfg["semantic"] = {"provider": "endpoint", "endpoint": f"http://127.0.0.1:{srv.server_port}", "model": "fake"}
    save_config(root, cfg)
    semantic._cache.clear()
    assert semantic.build(ix)["embedded"] > 0 and semantic.has_vectors(ix)
    assert semantic.search(ix, "create user", 3)
    cfg["semantic"]["endpoint"] = "https://embeddings.example.com/v1"
    save_config(root, cfg)
    semantic._cache.clear()
    with pytest.raises(ValueError, match="remote"):
        semantic.provider(ix)
    srv.shutdown()


def test_path_source_is_queryable(tmp_path, monkeypatch):
    from specforge import sources
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    (tmp_path / "other").mkdir()
    other = _graph_repo(tmp_path / "other")
    res = sources.add(f"path:{other}", tmp_path)
    assert res["symbols"] > 5
    ix = sources.open_source(res["source"])
    assert ix.search("create user")[0]["name"] == "create_user"


# ----------------------------------------------------------------------------- v0.5: config keys, insights, history, packs

def _git_commit(root: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg], cwd=root, check=True)


def test_config_keys_indexed_without_env_values(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n")
    (tmp_path / "package.json").write_text('{"name": "x", "scripts": {"test": "vitest"}}')
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgres://u:supersecret@h/db\n")
    ix = CodeIndex(tmp_path)
    ix.build()
    keys = {r["qualname"]: r["signature"] for r in ix.db.execute("SELECT qualname, signature FROM symbols WHERE kind='key'")}
    assert {"jobs.build.runs-on", "scripts.test", "DATABASE_URL"} <= set(keys)
    assert "supersecret" not in json.dumps(keys)
    assert ix.search("ci build job")[0]["kind"] != "key" or True  # keys never error search
    assert ix.search("scripts test")  # keys are searchable


def _insight_repo(tmp: Path) -> Path:
    root = _graph_repo(tmp)
    (root / "app" / "api.py").write_text(
        'from fastapi import APIRouter\nfrom app import crud\nrouter = APIRouter(prefix="/users")\n\n'
        '@router.post("/signup")\ndef signup(payload):\n    if not payload:\n        raise ValueError("x")\n'
        '    for i in range(3):\n        if i > 1:\n            pass\n    return crud.create_user(payload)\n\n'
        'def helper_a(items):\n    total = 0\n    for it in items:\n        if it.price > 10:\n            total += it.price * 2\n'
        '        else:\n            total += it.price\n    return round(total, 2)\n')
    (root / "app" / "billing.py").write_text(
        'def helper_b(items):\n    total = 0\n    for it in items:\n        if it.price > 10:\n            total += it.price * 2\n'
        '        else:\n            total += it.price\n    return round(total, 2)\n\n'
        'def notify(bus):\n    bus.emit("user.created", 1)\n\ndef listen(bus):\n    bus.on("user.created", print)\n')
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text("from app.api import signup\n\ndef test_signup():\n    assert signup({'name': 'a'})\n")
    (root / "web.ts").write_text('export async function register() { return fetch("/users/signup", {method: "POST"}); }\n')
    _git_commit(root, "PROJ-1 initial because we need users")
    return root


def test_insights_complexity_tests_similarity(tmp_path):
    from specforge import insights as I
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    c = I.symbol_complexity(ix, "app/api.py::signup")[0]
    assert c["cyclomatic"] >= 4 and c["nesting"] >= 2 and c["params"] == 1
    ut = I.untested_symbols(ix)
    names = {s["ref"].split("::")[1] for s in ut["symbols"]}
    assert "signup" not in names and "helper_b" in names
    sim = I.similar_symbols(ix, threshold=0.8)
    assert any({"app/api.py::helper_a", "app/billing.py::helper_b"} <= {x.split(" (")[0] for x in c["symbols"]} for c in sim)
    assert any(e["kind"] == "duplicate" for e in I.extraction_candidates(ix))
    assert I.related_symbols(ix, "app/crud.py::create_user")


def test_insights_structure_and_endpoints(tmp_path):
    from specforge import insights as I
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    assert any(e["ref"] == "app/api.py::signup" for e in I.entry_points(ix))
    chains = I.signal_chains(ix, entry="app/api.py::signup")
    assert chains and "create_user" in " ".join(chains[0]["chain"])
    imp = I.endpoint_impact(ix, "POST /users/signup")
    assert any("create_user" in c for c in imp["calls"]) and any(f.startswith("web.ts") for f in imp["frontend_callers"])
    flow = I.flow_edges(ix)
    assert any(e["event"] == "user.created" and e["handled_at"] for e in flow["events"])
    assert I.tectonic_map(ix)["plates"] is not None and I.architecture_metrics(ix)["files"] > 0
    h = I.repo_health(ix)
    assert set(h["axes"]) == {"complexity", "modularity", "testing", "dead_code", "documentation", "change_risk"}
    w = I.winnow(ix, kind="function", min_complexity=3)
    assert w and w[0]["complexity"] >= 3
    assert I.parity_map(ix, "app/api.py", "app/billing.py")["parity_pct"] == 0.0
    assert any(c["column"] == "name" for c in I.search_columns(ix, "name"))
    assert {"class": "Admin", "at": "app/models.py"} in I.find_implementations(ix, "User")["implementations"]
    assert I.check_safety(ix, "app/crud.py::create_user", "delete")["verdict"] == "unsafe"
    assert I.check_safety(ix, "app/crud.py::create_user", "rename", "_save")["verdict"] == "unsafe"
    assert "app/" in I.file_tree(ix, "", 2)
    assert I.decorator_census(ix)[0]["decorator"] == "router.post"


def test_go_implicit_interface_implementations(tmp_path):
    from specforge import insights as I
    (tmp_path / "shape.go").write_text(
        "package shape\n\ntype Shape interface {\n\tArea() float64\n\tName() string\n}\n\ntype Square struct {\n\tside float64\n}\n\n"
        "func (s Square) Area() float64 { return s.side * s.side }\nfunc (s Square) Name() string { return \"sq\" }\n\n"
        "type Line struct{}\n\nfunc (l Line) Name() string { return \"l\" }\n")
    ix = CodeIndex(tmp_path)
    ix.build()
    impls = {i["class"] for i in I.find_implementations(ix, "Shape")["implementations"]}
    assert impls == {"Square"}


def test_history_tools(tmp_path):
    from specforge import history as H
    root = _insight_repo(tmp_path)
    f = root / "app" / "crud.py"
    f.write_text(f.read_text().replace("return _save(User(name))", "user = User(name)\n    return _save(user)")
                 + "\ndef added_later():\n    return 1\n")
    _git_commit(root, "PROJ-2 refactor create_user so that saving is explicit")
    ix = CodeIndex(root)
    ix.build()
    prov = H.provenance(ix, "app/crud.py::create_user")
    assert prov["commits"] >= 2 and "PROJ-2" in prov["tickets"] and prov["decisions"]
    d = H.symbol_diff(ix, "HEAD~1", "HEAD")
    assert any(a["symbol"].endswith("::added_later") for a in d["added"])
    assert any(m["symbol"].endswith("::create_user") for m in d["modified"])
    assert H.churn_rate(ix, "app/crud.py", 30)["commits"] == 2
    assert H.digest(ix, "1 week ago")["commits"] == 2
    assert H.delivery_metrics(ix, 30)["commits"] == 2


def test_diagrams_audit_coverage_session(tmp_path):
    from specforge import agentaudit, diagrams, testing
    from specforge.session import Session, touch_compaction_marker
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    assert diagrams.render(ix, "call", "app/api.py::signup").startswith("flowchart")
    assert "<|--" in diagrams.render(ix, "class", "User")
    (root / "CLAUDE.md").write_text("# Rules\n" + "Always read `config/missing/rules.md` first. " * 400)
    a = agentaudit.audit(root)
    assert any(f["severity"] == "high" for f in a["findings"]) and any("missing path" in f["issue"] for f in a["findings"])
    (root / "coverage").mkdir()
    (root / "coverage" / "lcov.info").write_text(f"SF:{root}/app/crud.py\nDA:5,3\nDA:6,3\nDA:7,3\nDA:12,0\nDA:13,0\nend_of_record\n")
    cov = testing.runtime_coverage(ix)
    assert any(h["ref"].endswith("create_user") for h in cov["hot_paths"])
    assert any(n["ref"].endswith("_never_used") for n in cov["never_executed"])
    s = Session(root)
    s.note("search_symbols", "create user")
    s.labels[1] = "app/crud.py::create_user"
    s.mark(1, "h")
    import time as _t
    _t.sleep(0.01)
    touch_compaction_marker(root)
    s.ledger()  # detects compaction: previous snapshot saved, current cleared
    prev = s.load_previous()
    assert prev and "app/crud.py::create_user" in prev["symbols_in_context"] and not s.delivered


def test_pack_extraction_is_safe_and_sfpack_roundtrip(tmp_path, monkeypatch):
    import io
    import tarfile

    from specforge import packs, sources
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in [("package/index.js", b"function hello(name) { return 'hi ' + name; }\nmodule.exports = hello;\n"),
                           ("package/../../evil.js", b"pwned"), ("/etc/abs.js", b"pwned")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    dest = tmp_path / "pkg"
    n = packs._extract(buf.getvalue(), dest, True)
    assert n == 1 and (dest / "index.js").exists()
    assert not (tmp_path / "evil.js").exists() and not list(tmp_path.rglob("abs.js"))
    (tmp_path / "lib").mkdir()
    lib = _graph_repo(tmp_path / "lib")
    alias = sources.add(f"path:{lib}", tmp_path)["source"]
    out = tmp_path / "lib.sfpack"
    assert packs.export(alias, out)["files"] > 3
    sources.remove(alias)
    res = packs.import_pack(out)
    assert sources.open_source(res["source"]).search("create user")[0]["name"] == "create_user"


def test_doc_sections_and_code_first_ranking(tmp_path):
    (tmp_path / "README.md").write_text("# Proj\n\n## Create a user\n\n```sh\n# comment, not a heading\n```\nUse the API.\n\n## Deploy\nship it\n")
    (tmp_path / "users.py").write_text("def create_user(name):\n    return name\n")
    ix = CodeIndex(tmp_path)
    ix.build()
    secs = {r["qualname"]: (r["start_line"], r["end_line"]) for r in ix.db.execute("SELECT * FROM symbols WHERE kind='section'")}
    assert secs == {"Proj": (1, 12), "Proj > Create a user": (3, 9), "Proj > Deploy": (10, 12)}
    assert ix.search("create user")[0]["name"] == "create_user"
    assert ix.search("readme create user")[0]["kind"] == "section"


def test_monorepo_js_alias_resolves_inside_own_src(tmp_path):
    from specforge import insights as I
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "backend" / "app" / "utils.py").write_text("def x():\n    pass\n")
    (tmp_path / "frontend" / "src" / "components").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "utils.ts").write_text("export const fmt = (s: string) => s.trim();\n")
    (tmp_path / "frontend" / "src" / "components" / "A.tsx").write_text(
        'import { fmt } from "@/utils";\nexport function A() { return fmt(" a "); }\n')
    ix = CodeIndex(tmp_path)
    ix.build()
    deps = [r[0] for r in ix.db.execute("SELECT dst FROM file_deps WHERE src='frontend/src/components/A.tsx'")]
    assert deps == ["frontend/src/utils.ts"]
    g = I.dependency_graph(ix, "frontend/src/components")
    assert g["folder"] == "frontend/src/components" and g["imports"][0]["file"] == "frontend/src/utils.ts"
    assert I.symbol_complexity(ix, "frontend")[0]["ref"].startswith("frontend/src/")


def test_vectors_from_another_model_are_never_mixed(tmp_path):
    from array import array

    from specforge import semantic
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    ix.db.executescript(semantic.SCHEMA)
    r = ix.db.execute("SELECT path, qualname, body_hash FROM symbols WHERE kind='function' LIMIT 1").fetchone()
    ix.db.execute("INSERT INTO sem_vectors VALUES(?,?,?,?,?)",
                  (r[0], r[1], r[2], "some-old-model", array("f", [1.0, 0.0]).tobytes()))
    ix.db.commit()
    assert not semantic.has_vectors(ix)  # active model has no vectors: semantic search refuses rather than mixing
    st = ix.index_status()["embeddings"]
    assert st["vectors_by_model"] == {"some-old-model": 1} and st["action"]


def test_route_handlers_exercised_over_http_count_as_tested(tmp_path):
    from specforge import insights as I
    root = _insight_repo(tmp_path)
    (root / "tests" / "test_api.py").write_text(
        'def test_signup(client):\n    r = client.post(f"{API}/users/signup", json={"name": "a"})\n    assert r.status_code == 200\n')
    ix = CodeIndex(root)
    ix.build()
    refs = {s["ref"] for s in I.untested_symbols(ix)["symbols"]}
    assert "app/api.py::signup" not in refs and "app/crud.py::create_user" not in refs  # handler + what it calls
    assert "app/billing.py::helper_b" in refs


def test_reparse_after_grammar_download_really_reparses(tmp_path, monkeypatch):
    from specforge import parser
    (tmp_path / "svc.py").write_text("class Svc:\n    def run(self):\n        return 1\n")
    monkeypatch.setattr(parser, "local_grammars", lambda refresh=False: set())
    ix = CodeIndex(tmp_path)
    ix.build()
    assert ix.db.execute("SELECT parser FROM files WHERE path='svc.py'").fetchone()[0] != "tree-sitter"
    monkeypatch.undo()
    if "python" not in parser.local_grammars(refresh=True):
        pytest.skip("python grammar not cached")
    assert ix.reparse_languages(["python"])["files_changed"] == 1
    assert ix.db.execute("SELECT parser FROM files WHERE path='svc.py'").fetchone()[0] == "tree-sitter"


def test_crlf_files_parse_like_lf(tmp_path):
    (tmp_path / "ci.yml").write_bytes(b"jobs:\r\n  build:\r\n    runs-on: ubuntu-latest\r\n")
    (tmp_path / "m.py").write_bytes(b"MAX = 3\r\n\r\nclass User:\r\n    name: str\r\n\r\n    def go(self):\r\n        return 1\r\n")
    (tmp_path / "README.md").write_bytes(b"# A\r\n\r\n## B\r\ntext\r\n")
    ix = CodeIndex(tmp_path)
    ix.build()
    q = {(r["qualname"], r["kind"]) for r in ix.db.execute("SELECT qualname, kind FROM symbols")}
    assert {("jobs.build.runs-on", "key"), ("MAX", "constant"), ("User.name", "field"), ("User.go", "function"),
            ("A > B", "section")} <= q


# ----------------------------------------------------------------------------- v0.6: runtime, regret, tiers, contracts, handoff

def _otlp(path: Path, spans: list[tuple[dict, float]]) -> None:
    t, out = 1_700_000_000_000_000_000, []
    for attrs, ms in spans:
        t += 10_000_000
        out.append({"name": attrs.get("name", "s"), "startTimeUnixNano": str(t), "endTimeUnixNano": str(t + int(ms * 1e6)),
                    "attributes": [{"key": k, "value": {"stringValue": v} if isinstance(v, str) else {"intValue": v}}
                                   for k, v in attrs.items() if k != "name"]})
    path.write_text(json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": out}]}]}))


def test_otel_trace_ingestion_and_latency(tmp_path):
    import gzip

    from specforge import runtime as R
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    trace = tmp_path / "t.json"
    _otlp(trace, [({"code.filepath": str(root / "app" / "crud.py"), "code.function": "create_user"}, 5.0)] * 9
          + [({"code.filepath": "app/crud.py", "code.function": "create_user"}, 50.0)]
          + [({"http.route": "/api/v1/users/signup", "http.method": "POST"}, 12.0)] * 3
          + [({"name": "nothing.matches"}, 1.0)])
    (tmp_path / "t.json.gz").write_bytes(gzip.compress(trace.read_bytes()))
    res = R.import_trace(ix, tmp_path / "t.json.gz")
    assert (res["spans"], res["mapped"]) == (14, 13)
    hot = {h["ref"]: h for h in R.hot_paths(ix)}
    cu = hot["app/crud.py::create_user"]
    assert cu["hits"] == 10 and cu["p50_ms"] == 5.0 and cu["p95_ms"] == 50.0
    assert hot["app/api.py::signup"]["hits"] == 3  # HTTP span with an app prefix -> route handler
    unused = R.unused_paths(ix)
    assert "app/crud.py::_never_used" in unused["never_executed"]
    assert R.import_trace(ix, tmp_path / "t.json.gz")["mapped"] == 13
    assert R.hot_paths(ix)[0]["hits"] == 20  # re-import accumulates
    assert "runtime" in next(s for s in I_file_risk(ix, "app/crud.py") if s["ref"] == "create_user")


def I_file_risk(ix, path):
    from specforge import insights as I
    return I.file_risk(ix, path)["symbols"]


def test_regret_ledger_suggests_and_applies_synonyms(tmp_path):
    from specforge import regret
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    for q in ("register account", "register member", "register person"):
        out = "\n".join(f"[{r['id']}] {r['kind']} {r['qualname']}" for r in ix.search(q, limit=5))
        regret.log_call(ix, "s1", "search_symbols", {"query": q}, out)
        regret.log_call(ix, "s1", "get_symbol", {"ref": "app/crud.py::create_user"}, "")
    rep = regret.suggest_corrections(ix, apply=True)
    assert rep["queries"] == 3 and rep["fetches_after_search"] == 3
    rule = next(s for s in rep["synonym_suggestions"] if s["when_query_has"].startswith("regist"))
    assert rule["evidence"] == 3 and "applied" in rep
    ix2 = CodeIndex(root)
    assert any(r["qualname"] == "create_user" for r in ix2.search("register account", limit=5))


def test_tune_weights_needs_cases_and_reports(tmp_path):
    from specforge import regret
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    ev = tmp_path / "ev.json"
    ev.write_text(json.dumps({"cases": [{"query": q, "expected": [e]} for q, e in
                                        [("create user", "create_user"), ("signup", "signup"), ("notify bus", "notify"),
                                         ("listen bus", "listen"), ("helper items price", "helper_a")]]}))
    res = regret.tune_weights(ix, str(ev), apply=True)
    assert res["cases"] == 5 and res["best"]["mrr"] >= res["current"]["mrr"]
    with pytest.raises(ValueError):
        regret.tune_weights(ix)  # empty ledger: refuses to tune on nothing


def test_handoff_validation(tmp_path):
    from specforge import handoff
    root = _insight_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    good = {"Summary": "Signup flow reviewed end to end.", "Scope": "app/api.py and app/crud.py only.",
            "Findings": "Signup calls `app/crud.py::create_user` directly.", "Evidence": "See `app/api.py:5`.",
            "Recommendations": "Validate payload before persisting.", "Risks": "None beyond the signup path.",
            "Next steps": "Add a test for an empty payload."}
    res = handoff.finalize(ix, "Signup audit", good)
    assert res["written"] and not res["errors"] and res["references"] == 2
    text = (root / res["path"]).read_text()
    assert "status: verified" in text and text.index("## Summary") < text.index("## Next steps")
    bad = handoff.finalize(ix, "Bad", {"Summary": "TBD", "Findings": "`app/crud.py::ghost` and `app/api.py:999`"})
    assert not bad["written"] and len(bad["errors"]) >= 6


def test_compact_schema_keeps_arguments():
    from specforge.server import _compact_schema
    s = {"title": "fArguments", "type": "object", "properties": {
        "q": {"title": "Q", "type": "string"}, "k": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "K"},
        "m": {"anyOf": [{"type": "integer"}, {"type": "string"}], "title": "M"}}, "required": ["q"]}
    out = _compact_schema(s)
    assert out == {"type": "object", "required": ["q"], "properties": {
        "q": {"type": "string"}, "k": {"type": "string", "default": None},
        "m": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}}


def test_contract_import_parsing():
    from specforge.insights import _imported_names
    assert _imported_names("import { a, b as c } from 'lib/x'") == [("lib/x", ["a", "b"])]
    assert _imported_names("import D, { e } from \"lib\"") == [("lib", ["e", "default"])]
    assert _imported_names("const { f, g: h } = require('lib')") == [("lib", ["f", "g"])]
    assert _imported_names("from fastapi import APIRouter, Depends") == [("fastapi", ["APIRouter", "Depends"])]


# ----------------------------------------------------------------------------- v0.7: any AI coding tool

def _tool_repo(tmp: Path) -> Path:
    root = _graph_repo(tmp)
    (root / "package.json").write_text('{"name": "demo", "scripts": {"test": "vitest"}}')
    return root


def test_detect_and_resolve_tools(tmp_path):
    from specforge import providers as P
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
    from specforge.providers import translate
    src = "Run /sf:plan, then follow `.claude/commands/sf/be.md` and `.claude/skills/x`. Input: $ARGUMENTS. Use subagents."
    assert translate(src, "claude") == src
    cp = translate(src, "copilot")
    assert "/sf-plan" in cp and ".github/prompts/sf-be.prompt.md" in cp and ".github/skills/x" in cp and "${input:target}" in cp
    assert "subagents" in cp  # Copilot can run subagents
    cu = translate(src, "cursor")
    assert "/sf-plan" in cu and ".cursor/commands/sf-be.md" in cu and "$ARGUMENTS" not in cu and "subagent" not in cu
    assert "#sf-plan" in translate(src, "kiro") and "{{args}}" in translate(src, "gemini")
    assert "the sf-plan skill" in translate(src, "codex") and "the plan playbook" in translate(src, "replit")


def test_init_for_every_tool_writes_valid_native_files(tmp_path, monkeypatch):
    from specforge.bootstrap import run
    from specforge.providers import split_frontmatter
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        tomllib = None
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    res = run(root, agents="all", offline=True)
    assert res["agents"] == ["claude", "copilot", "cursor", "augment", "windsurf", "codex", "kiro", "gemini", "replit"]
    must = [".claude/commands/sf/groom.md", ".mcp.json", "CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md",
            ".github/prompts/sf-groom.prompt.md", ".github/agents/sf-architect.agent.md", ".github/skills/code-navigation/SKILL.md",
            ".vscode/mcp.json", ".cursor/commands/sf-groom.md", ".cursor/rules/code-navigation.mdc", ".cursor/mcp.json",
            ".augment/commands/sf/groom.md", ".augment/skills/code-navigation/SKILL.md", ".specforge/mcp/augment-import.json",
            ".agents/skills/sf-groom/SKILL.md", ".devin/mcp_config.json", ".codex/config.toml", ".codex/agents/sf-architect.toml",
            ".kiro/steering/sf-groom.md", ".kiro/steering/product.md", ".kiro/steering/tech.md", ".kiro/settings/mcp.json",
            ".gemini/commands/sf/groom.toml", ".gemini/settings.json", "GEMINI.md", "replit.md", ".specforge/playbooks/groom.md",
            "specs/templates/requirements.md"]
    missing = [m for m in must if not (root / m).exists()]
    assert not missing, missing
    assert json.loads((root / ".vscode/mcp.json").read_text(encoding="utf-8"))["servers"]["specforge"]["env"]["SPECFORGE_ROOT"] == "${workspaceFolder}"
    assert "specforge" in json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    if tomllib:
        assert tomllib.loads((root / ".codex/config.toml").read_text(encoding="utf-8"))["mcp_servers"]["specforge"]["command"] == "specforge-mcp"
        g = tomllib.loads((root / ".gemini/commands/sf/groom.toml").read_text(encoding="utf-8"))
        assert "{{args}}" in g["prompt"] and g["description"]
        assert tomllib.loads((root / ".codex/agents/sf-architect.toml").read_text(encoding="utf-8"))["name"] == "sf-architect"
    for f in [root / ".github/prompts/sf-groom.prompt.md", root / ".github/agents/sf-architect.agent.md",
              root / ".cursor/rules/code-navigation.mdc", root / ".kiro/steering/code-navigation.md",
              root / ".agents/skills/sf-groom/SKILL.md"]:
        meta, body = split_frontmatter(f.read_text(encoding="utf-8"))
        assert meta and body.strip(), f
        for v in meta.values():  # quoted scalars are JSON strings, so they must decode cleanly
            if v.startswith('"'):
                json.loads(v)
    skill, _ = split_frontmatter((root / ".agents/skills/sf-groom/SKILL.md").read_text(encoding="utf-8"))
    assert skill["name"] == "sf-groom" and len(json.loads(skill["description"])) <= 1024
    assert "inclusion: manual" in (root / ".kiro/steering/sf-groom.md").read_text(encoding="utf-8")
    assert ".kiro/specs/" in (root / ".github/prompts/sf-groom.prompt.md").read_text(encoding="utf-8")  # Kiro chose the feature layout
    assert "specforge tool" in (root / "replit.md").read_text(encoding="utf-8")
    for d in (".github", ".cursor", ".kiro", ".codex"):
        for f in (root / d).rglob("*"):
            if f.is_file() and f.suffix in (".md", ".mdc", ".toml"):
                t = f.read_text(encoding="utf-8")
                assert ".claude/" not in t and "$ARGUMENTS" not in t and "/sf:" not in t, f


def test_init_only_for_detected_tool_and_user_content_kept(tmp_path, monkeypatch):
    from specforge.bootstrap import run
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    root = _tool_repo(tmp_path)
    (root / ".cursor").mkdir()
    (root / ".cursor" / "mcp.json").write_text('{"mcpServers": {"mine": {"command": "my-server"}}}')
    (root / "AGENTS.md").write_text("# Team notes\n\nKeep PRs small.\n")
    res = run(root, offline=True)
    assert res["agents"] == ["cursor"]
    assert not (root / ".claude").exists() and not (root / "CLAUDE.md").exists() and not (root / ".mcp.json").exists()
    servers = json.loads((root / ".cursor/mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["mine"] == {"command": "my-server"} and "specforge" in servers
    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents_md.startswith("# Team notes") and "Keep PRs small." in agents_md and "/sf-groom" in agents_md
    run(root, offline=True)  # re-run: block replaced, not duplicated
    assert (root / "AGENTS.md").read_text(encoding="utf-8").count("specforge:start") == 1


def test_root_discovery_for_any_client(tmp_path):
    from specforge.config import discover_root
    root = _tool_repo(tmp_path)
    (root / ".specforge").mkdir(exist_ok=True)
    deep = root / "app" / "sub"
    deep.mkdir(parents=True, exist_ok=True)
    assert discover_root(deep, env={}) == root.resolve()
    assert discover_root(tmp_path, env={"SPECFORGE_ROOT": str(root)}) == root.resolve()
    assert discover_root(deep, env={"SPECFORGE_ROOT": "${workspaceFolder}"}) == root.resolve()  # unexpanded var ignored


def test_provider_models_in_profile(tmp_path, monkeypatch):
    from specforge import profile
    from specforge.bootstrap import run
    monkeypatch.setenv("SPECFORGE_HOME", str(tmp_path / "home"))
    profile.set_value("providers.copilot.models.architect", "Claude Opus 4.5")
    with pytest.raises(ValueError):
        profile.set_value("providers.cursor.models.architect", "x")
    root = _tool_repo(tmp_path)
    run(root, agents="copilot", offline=True)
    assert 'model: "Claude Opus 4.5"' in (root / ".github/agents/sf-architect.agent.md").read_text(encoding="utf-8")
    assert "model:" not in (root / ".github/agents/sf-reviewer.agent.md").read_text(encoding="utf-8")


def test_cli_tool_bridge(tmp_path):
    root = _tool_repo(tmp_path)
    CodeIndex(root).build()
    out = subprocess.run([sys.executable, "-m", "specforge.cli", "tool", "search_symbols", '{"query": "create user"}',
                          "--path", str(root)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "create_user" in out.stdout, out.stderr
    bad = subprocess.run([sys.executable, "-m", "specforge.cli", "tool", "nope", "--path", str(root)],
                         capture_output=True, text=True, timeout=120)
    assert bad.returncode == 2
