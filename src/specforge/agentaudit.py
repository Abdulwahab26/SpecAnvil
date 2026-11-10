"""Audit AI-agent configuration for wasted tokens and broken references.

Looks at what agents load on every turn (CLAUDE.md / AGENTS.md / Copilot & Cursor rules, skill descriptions,
MCP tool schemas) and on demand (agents, commands, skills), then reports size, duplication, stale paths,
missing env vars for MCP servers, and concrete savings.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from .tokens import estimate_tokens

ALWAYS = ["CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md", ".cursorrules",
          "CLAUDE.local.md", ".windsurfrules", "GEMINI.md"]
MCP_SCHEMA_ESTIMATE = {"specforge": 1400, "playwright": 3500, "github": 6000, "jira": 4000, "azure-devops": 5000,
                       "postgres": 1500, "mongodb": 2500, "figma": 1500, "sentry": 2500, "linear": 2500}
PATH_RX = re.compile(r"`((?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-*{}]+)+\.\w+)`")


def audit(root: Path) -> dict:
    root = root.resolve()
    findings, files = [], []
    always_tokens = 0
    line_owner: dict[str, list[str]] = {}

    def track(rel: str, text: str, always: bool):
        nonlocal always_tokens
        t = estimate_tokens(text)
        files.append({"file": rel, "tokens": t, "loaded": "every turn" if always else "on demand"})
        if always:
            always_tokens += t
        for ln in text.split("\n"):
            s = ln.strip()
            if len(s) > 40 and not s.startswith(("#", "|", "---", "```")):
                line_owner.setdefault(s, []).append(rel)
        for m in PATH_RX.finditer(text):
            p = m.group(1)
            if "*" in p or "{" in p or p.startswith(("specs/features/", "docs/")):
                continue
            if not (root / p.lstrip("./")).exists():
                findings.append({"severity": "medium", "file": rel, "issue": f"references missing path `{p}`",
                                 "fix": "update or remove the reference; agents waste turns looking for it"})

    for rel in ALWAYS:
        p = root / rel
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            track(rel, text, True)
            t = estimate_tokens(text)
            if t > 2500:
                findings.append({"severity": "high", "file": rel, "issue": f"{t} tokens loaded on every turn",
                                 "fix": "move procedures into skills/commands (loaded on demand); keep rules and pointers only",
                                 "saves_per_turn": t - 1500})
    skills = list((root / ".claude" / "skills").glob("*/SKILL.md"))
    for p in skills:
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(root))
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        desc = m.group(1) if m else ""
        always_tokens += estimate_tokens(desc)
        track(rel, text, False)
        if len(desc) > 400:
            findings.append({"severity": "low", "file": rel, "issue": f"skill description is {len(desc)} chars (loaded every turn)",
                             "fix": "shorten to one sentence about WHEN to use it"})
        if estimate_tokens(text) > 5000:
            findings.append({"severity": "medium", "file": rel, "issue": f"skill body {estimate_tokens(text)} tokens",
                             "fix": "split reference material into separate files the skill links to"})
    for folder in ("agents", "commands"):
        for p in (root / ".claude" / folder).rglob("*.md"):
            track(str(p.relative_to(root)), p.read_text(encoding="utf-8", errors="replace"), False)
    mcp = root / ".mcp.json"
    servers = {}
    if mcp.exists():
        try:
            servers = json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers", {})
        except json.JSONDecodeError:
            findings.append({"severity": "high", "file": ".mcp.json", "issue": "invalid JSON", "fix": "fix the syntax"})
    schema_tokens = 0
    for name, cfg in servers.items():
        est = MCP_SCHEMA_ESTIMATE.get(name, 2000)
        schema_tokens += est
        needed = set(re.findall(r"\$\{(\w+)(?::-[^}]*)?\}", json.dumps(cfg)))
        missing = sorted(v for v in needed if not os.environ.get(v) and ":-" not in json.dumps(cfg))
        if missing:
            findings.append({"severity": "medium", "file": ".mcp.json", "issue": f"server '{name}' needs unset env vars {missing}",
                             "fix": "set them in your shell, or remove the server so its tools don't load"})
    if schema_tokens > 12000:
        findings.append({"severity": "medium", "file": ".mcp.json",
                         "issue": f"~{schema_tokens} tokens of MCP tool schemas from {len(servers)} servers",
                         "fix": "disable servers you don't use in this repo; use specforge tool_tier 'core' for coding sessions"})
    dupes = {ln: owners for ln, owners in line_owner.items() if len(set(owners)) > 1}
    if dupes:
        waste = sum(estimate_tokens(ln) * (len(set(o)) - 1) for ln, o in dupes.items())
        top = Counter(tuple(sorted(set(o))) for o in dupes.values()).most_common(3)
        findings.append({"severity": "low", "file": ", ".join(top[0][0]) if top else "", "issue":
                         f"{len(dupes)} lines duplicated across config files (~{waste} tokens)",
                         "fix": "keep each rule in one place and reference it"})
    files.sort(key=lambda x: -x["tokens"])
    sev = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: sev[f["severity"]])
    return {"always_loaded_tokens": always_tokens + schema_tokens, "instructions_tokens": always_tokens,
            "mcp_schema_tokens_estimate": schema_tokens, "files": files[:30], "findings": findings,
            "potential_savings_per_turn": sum(f.get("saves_per_turn", 0) for f in findings)}
