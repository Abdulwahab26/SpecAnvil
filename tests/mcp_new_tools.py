import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main(root):
    async with stdio_client(StdioServerParameters(command="specforge-mcp", args=[], cwd=root, env={**os.environ, "SPECFORGE_TOOLS": "full"})) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            print(len(names), "tools:", ", ".join(names))
            for name, args in [("get_profile", {}), ("ticket_keys", {}), ("ticket_trace", {"key": "SHOP-31"}),
                               ("backfill_progress", {"done": ["SHOP-12"], "query": "status in (Done)"}),
                               ("test_surface", {"target": "services/api", "limit": 3}),
                               ("next_test_milestone", {"module": "orders"}),
                               ("get_symbol", {"ref": "doesNotExist"})]:
                res = await s.call_tool(name, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                print(f"\n== {name} isError={res.is_error}\n{text[:400]}")

asyncio.run(main(sys.argv[1]))
