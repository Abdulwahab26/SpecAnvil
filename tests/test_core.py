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
