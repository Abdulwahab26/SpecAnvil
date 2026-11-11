"""End-to-end over MCP stdio, starting in the default `lite` tier:
tier switching (tools/list_changed), run_tool, OTel ingestion, regret ledger, weight tuning, contracts, handoff.

    python tests/mcp_v06_tools.py <full-stack-fastapi-template checkout>
"""
import asyncio
import json
import os
import random
import sys
import tempfile

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def otlp_trace(path: str) -> None:
    """Synthetic OTLP JSON export: code.* spans for crud functions, HTTP server spans for two routes."""
    rnd = random.Random(7)
    spans, t = [], 1_700_000_000_000_000_000

    def span(name, attrs, ms):
        nonlocal t
        t += 1_000_000
        spans.append({"name": name, "startTimeUnixNano": str(t), "endTimeUnixNano": str(t + int(ms * 1e6)),
                      "attributes": [{"key": k, "value": {"stringValue" if isinstance(v, str) else "intValue": v}}
                                     for k, v in attrs.items()]})
    for _ in range(120):
        span("crud.create_user", {"code.filepath": "/srv/app/backend/app/crud.py", "code.function": "create_user"},
             rnd.uniform(3, 9))
    for _ in range(40):
        span("authenticate", {"code.filepath": "backend/app/crud.py", "code.function": "authenticate"}, rnd.uniform(20, 60))
    for _ in range(30):
        span("GET /api/v1/items/", {"http.route": "/api/v1/items/", "http.method": "GET"}, rnd.uniform(5, 15))
    span("unknown.thing", {}, 1)
    with open(path, "w") as f:
        json.dump({"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}, f)


async def main(root: str) -> int:
    env = {k: v for k, v in os.environ.items() if k != "SPECANVIL_TOOLS"}
    p = StdioServerParameters(command="specanvil-mcp", cwd=root, env=env)
    bad = 0
    changed = []

    async def on_message(msg):
        if "ToolListChanged" in type(getattr(msg, "root", msg)).__name__ or "list_changed" in str(msg):
            changed.append(1)

    def check(label, cond, detail=""):
        nonlocal bad
        print(f"{'ok  ' if cond else 'FAIL'} {label} {detail}")
        bad += not cond

    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w, message_handler=on_message) as s:
            await s.initialize()

            async def call(name, args=None):
                res = await s.call_tool(name, args or {})
                text = "".join(getattr(c, "text", "") for c in res.content)
                err = getattr(res, "is_error", getattr(res, "isError", False))
                if err:
                    print(f"     {name} error: {text[:300]}")
                return text, err

            names = {t.name for t in (await s.list_tools()).tools}
            check("default tier is lite (6 tools)", len(names) == 6, str(sorted(names)))
            out, err = await call("run_tool", {"name": "repo_health"})
            check("run_tool reaches a hidden tool", not err and "axes" in out)
            out, err = await call("set_tool_tier", {"tier": "analysis"})
            await asyncio.sleep(0.3)
            names = {t.name for t in (await s.list_tools()).tools}
            check("set_tool_tier('analysis') updates tools/list", "repo_health" in names and len(names) > 50, f"{len(names)} tools")
            check("server sent tools/list_changed", bool(changed))
            out, err = await call("set_tool_tier", {"model": "claude-haiku-5-5"})
            check("model id picks a tier", json.loads(out)["tier"] == "core")
            await call("set_tool_tier", {"tier": "full"})

            trace = os.path.join(tempfile.mkdtemp(), "trace.json")
            otlp_trace(trace)
            out, err = await call("import_runtime_trace", {"path": trace})
            imp = json.loads(out) if not err else {}
            check("OTel import maps spans", imp.get("mapped", 0) >= 190, f"{imp.get('mapped')}/{imp.get('spans')}")
            out, err = await call("runtime_hot_paths", {"sort_by": "p95"})
            hot = json.loads(out) if not err else []
            check("per-symbol p50/p95", hot and hot[0]["ref"].endswith("::authenticate") and hot[0]["p95_ms"] > 20,
                  json.dumps(hot[:1]))
            check("HTTP route span mapped to handler", any("read_items" in h["ref"] for h in hot))
            out, err = await call("runtime_unused", {})
            check("runtime_unused lists traced-file functions never hit", not err and "never_executed" in out)
            out, err = await call("file_risk", {"path": "backend/app/crud.py"})
            check("file_risk carries runtime latency", not err and '"runtime"' in out)

            for q, ref in [("encrypt credential", "backend/app/core/security.py::get_password_hash"),
                           ("encrypt credential storage", "backend/app/core/security.py::get_password_hash"),
                           ("encrypt the secret", "backend/app/core/security.py::get_password_hash"),
                           ("mail a new user", "backend/app/utils.py::send_email"),
                           ("mail the owner", "backend/app/utils.py::send_email")]:
                await call("search_symbols", {"query": q})
                await call("get_symbol", {"ref": ref})
            out, err = await call("suggest_corrections", {})
            rep = json.loads(out) if not err else {}
            check("regret mining sees queries and fetches", rep.get("queries", 0) >= 5 and rep.get("fetches_after_search", 0) >= 5,
                  json.dumps({k: rep.get(k) for k in ("queries", "fetched_outside_top5", "mean_rank_of_fetched")}))
            check("vocabulary gap -> synonym rule", any(x["when_query_has"].startswith("encrypt") for x in rep.get("synonym_suggestions", [])),
                  json.dumps(rep.get("synonym_suggestions", [])[:3]))
            out, err = await call("tune_weights", {"eval_file": os.path.abspath("tests/evals/semantic_fullstack_fastapi.json")})
            tw = json.loads(out) if not err else {}
            check("tune_weights grid-searches fusion weights", "best" in tw, json.dumps({k: tw.get(k) for k in ("current", "best")}))
            out, err = await call("group_contracts", {})
            check("group_contracts runs", not err and "contracts" in out)
            out, err = await call("finalize_handoff", {"title": "Auth audit", "kind": "audit", "sections": {
                "Summary": "Password hashing and token checks reviewed end to end.",
                "Scope": "backend/app/core and the login routes.",
                "Findings": "Hashing lives in `backend/app/core/security.py::get_password_hash`.",
                "Evidence": "See `backend/app/crud.py::authenticate` and `backend/app/crud.py:1`.",
                "Recommendations": "Keep bcrypt cost configurable via settings.",
                "Risks": "Changing the hash scheme invalidates sessions only on next login.",
                "Next steps": "Add a test for the inactive-user branch."}})
            h = json.loads(out) if not err else {}
            check("validated handoff written", h.get("written") and not h.get("errors"), h.get("path", ""))
            out, err = await call("finalize_handoff", {"title": "Bad", "kind": "audit", "sections": {
                "Summary": "TODO", "Findings": "See `backend/app/nope.py::ghost`."}})
            h2 = json.loads(out) if not err else {}
            check("invalid handoff refused", h2.get("written") is False and len(h2.get("errors", [])) >= 3)
    print(f"\n{'all ok' if not bad else f'{bad} failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
