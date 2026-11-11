"""Runtime traces, ranking regret, tiers, contracts, handoff."""
import json

import pytest
from helpers import (
    I_file_risk,
    _insight_repo,
    _otlp,
)

from specanvil.index import CodeIndex


def test_otel_trace_ingestion_and_latency(tmp_path):
    import gzip

    from specanvil import runtime as R
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


def test_regret_ledger_suggests_and_applies_synonyms(tmp_path):
    from specanvil import regret
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
    from specanvil import regret
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
    from specanvil import handoff
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
    from specanvil.server import _compact_schema
    s = {"title": "fArguments", "type": "object", "properties": {
        "q": {"title": "Q", "type": "string"}, "k": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "K"},
        "m": {"anyOf": [{"type": "integer"}, {"type": "string"}], "title": "M"}}, "required": ["q"]}
    out = _compact_schema(s)
    assert out == {"type": "object", "required": ["q"], "properties": {
        "q": {"type": "string"}, "k": {"type": "string", "default": None},
        "m": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}}


def test_contract_import_parsing():
    from specanvil.insights import _imported_names
    assert _imported_names("import { a, b as c } from 'lib/x'") == [("lib/x", ["a", "b"])]
    assert _imported_names("import D, { e } from \"lib\"") == [("lib", ["e", "default"])]
    assert _imported_names("const { f, g: h } = require('lib')") == [("lib", ["f", "g"])]
    assert _imported_names("from fastapi import APIRouter, Depends") == [("fastapi", ["APIRouter", "Depends"])]
