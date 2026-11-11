"""End-to-end smoke test: start specanvil-mcp over stdio like Claude Code does and call every tool."""
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = sys.argv[1]

CALLS = [
    ("repo_map", {}),
    ("file_outline", {"path": "services/api/app/routers/orders.py"}),
    ("search_symbols", {"query": "invoice issue"}),
    ("get_symbol", {"ref": "InvoiceService.issue"}),
    ("get_context", {"task": "add coupon expiry validation to order discount", "budget_tokens": 800}),
    ("find_references", {"name": "calculate_discount"}),
    ("search_code", {"pattern": "DATABASE_URL"}),
    ("impact_of", {"path": "services/api/app/models/order.py"}),
    ("list_specs", {}),
    ("read_spec", {"path": "specs/project.md", "section": "Commands"}),
    ("spec_drift", {}),
    ("changed_symbols", {}),
    ("project_profile", {}),
    ("token_savings", {}),
]


async def main():
    params = StdioServerParameters(command="specanvil-mcp", args=[], cwd=ROOT, env={**os.environ, "SPECANVIL_TOOLS": "full"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", ", ".join(t.name for t in tools.tools))
            for name, args in CALLS:
                res = await s.call_tool(name, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                print(f"\n===== {name} {json.dumps(args)}\n{text[:1500]}")


asyncio.run(main())
