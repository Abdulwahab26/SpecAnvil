"""Index, specs, bootstrap, unit-test plans, docs engine, code graph."""
import json

import pytest
from helpers import (
    _azure_repo,
    _graph_repo,
    _plan,
    make_repo,
)

from specanvil.bootstrap import MARK_START, run
from specanvil.index import CodeIndex, module_of, split_terms, stem
from specanvil.parser import _parse_regex, parse_source
from specanvil.specs import drift, lock_specs, read_section
from specanvil.tokens import redact


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
    assert (root / ".claude/commands/sa/flow.md").exists()
    assert (root / ".claude/skills/db-optimization/SKILL.md").read_text().count("PostgreSQL") >= 1
    mcp = json.loads((root / ".mcp.json").read_text())
    assert {"specanvil", "postgres"} <= set(mcp["mcpServers"])
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

    from specanvil import parser

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


def test_milestone_done_is_verified_by_running_tests(tmp_path):
    from specanvil import testing
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
    from specanvil import testing
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

    from specanvil import testing
    root = make_repo(tmp_path)
    d = testing.plan_dir(root, "orders")
    d.mkdir(parents=True)
    (d / "milestones.json").write_text(json.dumps(_plan("x_test.py")))
    with pytest.raises(ValueError):
        testing.update_milestone(root, "orders", "M1", "blocked")


def test_profile_models_render_into_agents(tmp_path, monkeypatch):
    import pytest

    from specanvil import profile
    from specanvil.bootstrap import sync_agents
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
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
    from specanvil.cli import _guard
    assert _guard({"tool_name": "Read", "tool_input": {"file_path": "/r/.env"}, "cwd": str(tmp_path)}) == 2
    assert _guard({"tool_name": "Read", "tool_input": {"file_path": "/r/.env.example"}, "cwd": str(tmp_path)}) == 0
    assert _guard({"tool_name": "Bash", "tool_input": {"command": "cat config/.env"}, "cwd": str(tmp_path)}) == 2
    assert _guard({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}, "cwd": str(tmp_path)}) == 0


def test_docs_archetypes_check_register_and_stale(tmp_path):
    from specanvil.docs import DocEngine
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
    from specanvil.facts import Facts
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

    from specanvil.session import Session, touch_compaction_marker
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
    from specanvil.analysis import excerpt
    body = "def big(x):\n" + "".join(f"    step{i} = {i}\n" for i in range(30)) + "    if coupon_expired(x):\n        raise E\n    return x\n"
    out, cut = excerpt(body, "coupon")
    assert cut and "coupon_expired" in out and "def big" in out and "step15" not in out and "… (" in out
    assert len(out) < len(body) / 3


def test_declarations_and_graph_tables(tmp_path):
    root = _graph_repo(tmp_path)
    ix = CodeIndex(root)
    ix.build()
    kinds = {(r["qualname"], r["kind"]) for r in ix.db.execute("SELECT qualname, kind FROM symbols")}
    assert ("MAX_USERS", "constant") in kinds and ("User.name", "field") in kinds and ("Admin.level", "field") in kinds
    assert ("app/routes.py", "app/crud.py") in {tuple(r) for r in ix.db.execute("SELECT src, dst FROM file_deps")}
    assert ("Admin", "User") in {tuple(r) for r in ix.db.execute("SELECT cls, base FROM bases")}


def test_call_and_class_hierarchy_use_receiver(tmp_path):
    from specanvil import graph
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
    from specanvil import graph
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
    from specanvil import astsearch, refactor
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

    from specanvil import semantic
    from specanvil.config import load_config, save_config

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
    from specanvil import sources
    monkeypatch.setenv("SPECANVIL_HOME", str(tmp_path / "home"))
    (tmp_path / "other").mkdir()
    other = _graph_repo(tmp_path / "other")
    res = sources.add(f"path:{other}", tmp_path)
    assert res["symbols"] > 5
    ix = sources.open_source(res["source"])
    assert ix.search("create user")[0]["name"] == "create_user"


def test_kotlin_functions_objects_and_properties_are_indexed(tmp_path):
    """The Kotlin grammar's structure view names classes only: member and top-level functions, `object`s and
    properties (name before type) are read from the source."""
    (tmp_path / "Owners.kt").write_text(
        "package clinic\n\n"
        "interface OwnerRepository {\n    fun findByLastName(lastName: String): Collection<Owner>\n}\n\n"
        "@Controller\nclass OwnerController(val owners: OwnerRepository) {\n\n"
        "    private lateinit var visits: VisitRepository\n\n"
        "    @GetMapping(\"/owners\")\n    fun processFindForm(owner: Owner): String {\n"
        "        val results = owners.findByLastName(owner.lastName)\n        return if (results.isEmpty()) \"none\" else \"list\"\n    }\n\n"
        "    fun count() = owners.findByLastName(\"\").size\n\n"
        "    companion object {\n        const val PAGE_SIZE = 5\n    }\n}\n\n"
        "object Slugs {\n    fun String.toSlug(): String =\n        lowercase()\n            .replace(' ', '-')\n}\n\n"
        "fun <T> firstOrNull(items: List<T>): T? {\n    return items.firstOrNull()\n}\n", encoding="utf-8")
    ix = CodeIndex(tmp_path)
    ix.build()
    if ix.db.execute("SELECT parser FROM files").fetchone()[0] != "tree-sitter":
        pytest.skip("kotlin grammar not cached")
    syms = {r["qualname"]: r for r in ix.db.execute("SELECT qualname, kind, start_line, end_line FROM symbols")}
    assert syms["OwnerRepository.findByLastName"]["kind"] == "method"
    find = syms["OwnerController.processFindForm"]
    assert (find["kind"], find["end_line"] - find["start_line"]) == ("method", 3)
    assert syms["OwnerController.count"]["kind"] == "method"
    assert syms["OwnerController.visits"]["kind"] == "field" and "OwnerController.VisitRepository" not in syms
    assert "OwnerController.results" not in syms                      # a local, not a symbol
    slug = syms["Slugs.toSlug"]
    assert slug["end_line"] - slug["start_line"] == 2                 # expression body continued on indented lines
    assert syms["firstOrNull"]["kind"] == "function"


def test_calls_through_injected_dependencies_follow_the_declared_type(tmp_path):
    """`this.users.findAll()` goes to the `findAll` of the class `users` is declared as, not to the caller's own
    `findAll`; an interface-typed field reaches the implementation; an unindexed type yields no edge."""
    from specanvil.graph import CodeGraph
    (tmp_path / "users.service.ts").write_text(
        "export class UsersService {\n  findAll() {\n    return [];\n  }\n  delete(id: number) {\n    return id;\n  }\n}\n")
    (tmp_path / "tags.service.ts").write_text("export class TagsService {\n  findAll() {\n    return [];\n  }\n}\n")
    (tmp_path / "users.controller.ts").write_text(
        "import { UsersService } from './users.service';\nimport { Repository } from 'typeorm';\n\n"
        "export class UsersController {\n"
        "  constructor(private readonly users: UsersService, private readonly repo: Repository<User>) {}\n\n"
        "  findAll() {\n    return this.users.findAll();\n  }\n\n"
        "  remove(id: number) {\n    this.repo.findAll();\n    return this.users.delete(id);\n  }\n}\n")
    (tmp_path / "Billing.java").write_text(
        "interface Gateway {\n    void charge(int cents);\n}\n\n"
        "class StripeGateway implements Gateway {\n    public void charge(int cents) {\n    }\n}\n\n"
        "class Checkout {\n    private final Gateway gateway;\n\n"
        "    Checkout(Gateway gateway) {\n        this.gateway = gateway;\n    }\n\n"
        "    void pay(int cents) {\n        gateway.charge(cents);\n    }\n}\n")
    ix = CodeIndex(tmp_path)
    ix.build()
    if {r[0] for r in ix.db.execute("SELECT parser FROM files")} != {"tree-sitter"}:
        pytest.skip("typescript / java grammars not cached")
    g = CodeGraph(ix)

    def callees(qualname: str) -> set[str]:
        sid = ix.db.execute("SELECT id FROM symbols WHERE qualname=?", (qualname,)).fetchone()[0]
        return {f"{g.sym[t]['path']}::{g.sym[t]['qualname']}" for t, _ in g.callees(sid)}
    assert callees("UsersController.findAll") == {"users.service.ts::UsersService.findAll"}
    assert callees("UsersController.remove") == {"users.service.ts::UsersService.delete"}   # Repository is not indexed
    assert callees("Checkout.pay") == {"Billing.java::Gateway.charge", "Billing.java::StripeGateway.charge"}
    ix.cfg["typed_receivers"] = False
    assert "users.service.ts::UsersService.findAll" not in {
        f"{CodeGraph(ix).sym[t]['path']}::{CodeGraph(ix).sym[t]['qualname']}"
        for t, _ in CodeGraph(ix).callees(ix.db.execute("SELECT id FROM symbols WHERE qualname='UsersController.findAll'").fetchone()[0])}
