"""Token cost of a fixed lookup workload, measured on the text the MCP server returns.

Three repositories at pinned commits (expressjs/express 1faf228, fastapi/fastapi a64dfbb, gin-gonic/gin 75ccf94),
five queries each, one workflow per query: search_symbols (top 5), then fetch the top 3 symbols. That is 15 lookups.
Tokens are counted with tiktoken cl100k_base. Session dedupe is off (force=True) so every lookup is measured fresh.
Two passes are reported: `default` (large symbols returned as skeletons) and `uncapped` (every body in full).

    python benchmarks/token_benchmark.py <express> <fastapi> <gin> > benchmarks/token_benchmark_results.json
"""
import asyncio
import json
import os
import sys
import time

import tiktoken
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

QUERIES = ["router route handler", "middleware", "error exception", "request response", "param bind"]
ENC = tiktoken.get_encoding("cl100k_base")


def tok(s: str) -> int:
    return len(ENC.encode(s))


def text_of(res) -> str:
    return "".join(getattr(c, "text", "") for c in res.content)


async def run_repo(path: str, dedupe: bool, full: bool = False) -> dict:
    out = {"repo": path.rstrip("/").split("/")[-1], "queries": []}
    params = StdioServerParameters(command="specanvil-mcp", args=[], cwd=path, env={**os.environ, "SPECANVIL_TOOLS": "full"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t0 = time.time()
            await s.call_tool("reindex", {"full": True})
            out["index_seconds"] = round(time.time() - t0, 2)
            for q in QUERIES:
                search = text_of(await s.call_tool("search_symbols", {"query": q, "limit": 5}))
                ids = [line.split("]")[0][1:] for line in search.splitlines() if line.startswith("[")][:3]
                fetch = 0
                names = []
                for i in ids:
                    t = text_of(await s.call_tool("get_symbol", {"ref": i, "force": not dedupe, "full": full}))
                    fetch += tok(t)
                    names.append(t.split("\n", 1)[0].split("  ")[1] if "  " in t else t[:60])
                out["queries"].append({"query": q, "search_tokens": tok(search), "fetch_tokens": fetch,
                                       "total": tok(search) + fetch, "fetched": names})
    out["total"] = sum(x["total"] for x in out["queries"])
    return out


async def main(paths):
    default = [await run_repo(p, dedupe=False) for p in paths]           # default behaviour, no dedupe
    uncapped = [await run_repo(p, dedupe=False, full=True) for p in paths]  # every body in full
    print(json.dumps({"default": default, "uncapped": uncapped}, indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
