"""Write docs/TOOLS.md from the MCP server's own tool registry, so the reference cannot drift from the code.

    python scripts/gen_tool_reference.py            # rewrite docs/TOOLS.md
    python scripts/gen_tool_reference.py --check    # exit 1 if docs/TOOLS.md is out of date (used by the tests)
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["lite", "core", "standard", "analysis", "full"]
INTRO = {
    "lite": "The default tier. Enough to navigate and read code within a token budget, and to reach every other tool.",
    "core": "Adds outlines, batch reads, references and spec reads. Suggested for small, fast models.",
    "standard": "Adds change analysis, specs, the unit-test pipeline, documentation, tickets and sources.",
    "analysis": "Adds architecture, quality, history and runtime analysis.",
    "full": "Everything else: session and index introspection, tuning, cross-repository tools and handoffs.",
}


def render() -> str:
    os.environ.setdefault("SPECANVIL_ROOT", tempfile.mkdtemp())  # the registry does not depend on a repository
    os.environ["SPECANVIL_TOOLS"] = "full"
    sys.path.insert(0, str(ROOT / "src"))
    from specanvil import server
    funcs = dict(server._FUNCS)
    funcs.setdefault("set_tool_tier", server.set_tool_tier)  # registered on its own: it must never be hidden
    seen: set[str] = set()
    out = ["# Tool reference", "",
           f"`specanvil-mcp` exposes {len(funcs)} tools in five tiers. A session starts in `lite`; an agent raises the "
           "tier with `set_tool_tier` when a task needs more, or calls any single tool through `run_tool`. Pin a tier "
           "with `\"tool_tier\"` in `.specanvil/config.json` or the `SPECANVIL_TOOLS` environment variable. Every tool "
           "is also available from the shell: `specanvil tool <name> '<json arguments>'`.", "",
           "This file is generated from the server's tool registry by `scripts/gen_tool_reference.py`; a test fails "
           "when it is out of date.", ""]
    for tier in ORDER:
        names = sorted((set(funcs) if tier == "full" else server.TIERS[tier] & set(funcs)) - seen)
        seen |= set(names)
        total = len(funcs) if tier == "full" else len(server.TIERS[tier] & set(funcs))
        out += [f"## `{tier}` ({total} tools{'' if tier == 'lite' else f', {len(names)} added'})", "", INTRO[tier], "",
                "| Tool | Description |", "|---|---|"]
        for n in names:
            doc = " ".join((funcs[n].__doc__ or "").split())
            doc = re.sub(r"\s*\|\s*", " / ", doc)
            out.append(f"| `{n}` | {doc} |")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    text = render()
    target = ROOT / "docs" / "TOOLS.md"
    if "--check" in sys.argv:
        sys.exit(0 if target.is_file() and target.read_text(encoding="utf-8") == text else 1)
    target.write_text(text, encoding="utf-8")
    print(f"{target.relative_to(ROOT)}: {text.count(chr(10))} lines")
