"""Calls every v0.5 tool over MCP stdio against a real repo and fails on any tool error.

    python tests/mcp_v05_tools.py <repo-with-git-history>
"""
import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

CALLS = [
    ("index_status", {}), ("file_tree", {"prefix": "", "depth": 2}), ("repo_health", {}), ("architecture_metrics", {}),
    ("tectonic_map", {}), ("entry_points", {}), ("signal_chains", {}), ("decorator_census", {}),
    ("symbol_complexity", {"target": "backend"}), ("file_risk", {"path": "backend/app/crud.py"}), ("untested_symbols", {}),
    ("similar_symbols", {}), ("extraction_candidates", {}), ("winnow_symbols", {"kind": "function", "min_complexity": 3}),
    ("suggest_queries", {}), ("plan_turn", {"query": "add password reset email"}), ("flow_edges", {}),
    ("search_columns", {"query": "email"}), ("dependency_graph", {"path": "backend/app/api"}),
    ("find_implementations", {"name": "SQLModel"}), ("churn_rate", {"target": "backend", "days": 3650}),
    ("digest", {"since": "10 years ago"}), ("delivery_metrics", {"days": 3650}),
    ("symbol_diff", {"base": "HEAD~5", "head": "HEAD"}), ("render_diagram", {"kind": "modules"}),
    ("endpoint_impact", {"endpoint": "POST /users/signup"}),
    ("parity_map", {"left": "backend/app/api/routes/users.py", "right": "backend/app/api/routes/items.py"}),
    ("audit_agent_config", {}), ("runtime_coverage", {}), ("session_snapshot", {}), ("tool_stats", {}),
    ("redaction_log", {}), ("list_workspaces", {}), ("cross_repo_map", {}), ("list_packs", {}),
]


async def main(root: str) -> int:
    p = StdioServerParameters(command="specforge-mcp", cwd=root, env={**os.environ, "SPECFORGE_TOOLS": "full"})
    bad = 0
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            ref = "backend/app/crud.py::create_user"
            calls = CALLS + [("related_symbols", {"ref": ref}), ("symbol_provenance", {"ref": ref}),
                             ("check_safety", {"ref": ref, "action": "delete"}),
                             ("get_file_content", {"path": ref.split("::")[0], "start_line": 1, "end_line": 20})]
            for name, args in calls:
                res = await s.call_tool(name, args)
                err = getattr(res, "is_error", getattr(res, "isError", False))
                text = "".join(getattr(c, "text", "") for c in res.content)
                print(f"{'FAIL' if err else 'ok  '} {name:24} {len(text):>6} chars")
                if err:
                    bad += 1
                    print("     ", text[:300])
    print(f"\n{len(calls) - bad}/{len(calls)} tools ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
