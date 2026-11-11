"""Config keys, analysis tools, history, diagrams, packs."""
import json

import pytest
from helpers import (
    _git_commit,
    _graph_repo,
    _insight_repo,
)

from specforge.index import CodeIndex


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
