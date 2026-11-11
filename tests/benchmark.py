"""Token benchmark: typical agent lookups through specanvil-mcp vs reading the files whole."""
import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = sys.argv[1]
CALLS = [tuple(x.split("|", 2)) for x in sys.argv[2:]]


async def main():
    params = StdioServerParameters(command="specanvil-mcp", args=[], cwd=ROOT)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for tool, key, val in CALLS:
                args = {key: val} if key else {}
                res = await s.call_tool(tool, args)
                text = "".join(getattr(c, "text", "") for c in res.content)
                print(f"--- {tool}({val}) -> {len(text)//4} tokens; first line: {text.splitlines()[0][:110] if text else ''}")
            res = await s.call_tool("token_savings", {})
            print("".join(getattr(c, "text", "") for c in res.content))


asyncio.run(main())
