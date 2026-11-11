"""AI coding tools: detect which ones a repo uses and write SpecAnvil's setup in each tool's own formats.

One provider-neutral bundle (rules, lifecycle commands, role agents, skills, MCP servers) is translated per tool:

  tool      always-on rules            commands / workflows            role agents              skills                  MCP config
  claude    CLAUDE.md                  .claude/commands/sa/*.md        .claude/agents/*.md      .claude/skills          .mcp.json
            (+ hooks: secret guard, re-index; Cursor gets .cursor/hooks.json, Copilot .github/hooks/specanvil.json)
  copilot   AGENTS.md (+ pointer in    .github/prompts/sa-*.prompt.md  .github/agents/          .github/skills          .vscode/mcp.json ("servers")
            .github/copilot-instructions.md)                           sa-*.agent.md
  cursor    AGENTS.md                  .cursor/commands/sa-*.md        (roles live in commands) .cursor/rules/*.mdc     .cursor/mcp.json
  augment   AGENTS.md                  .augment/commands/sa/*.md       (roles live in commands) .augment/skills         importable JSON (Settings > MCP)
  windsurf  AGENTS.md                  .agents/skills/sa-*             (roles live in commands) .agents/skills          .devin/mcp_config.json
  codex     AGENTS.md                  .agents/skills/sa-*             .codex/agents/sa-*.toml  .agents/skills          .codex/config.toml (trusted)
  kiro      AGENTS.md + .kiro/steering .kiro/steering/sa-*.md (manual) (roles live in commands) steering (auto)         .kiro/settings/mcp.json
            (product/tech/structure); feature specs in .kiro/specs/<feature>/
  gemini    GEMINI.md                  .gemini/commands/sa/*.toml      (roles live in commands) .agents/skills          .gemini/settings.json
  replit    replit.md                  .specanvil/playbooks/*.md       (roles live in commands) playbooks               none: CLI bridge `specanvil tool`

Formats were checked against each tool's documentation (Nov 2026); see docs/PROVIDERS.md for sources.
The code index, specs and enforcement in the MCP server (secret filtering, redaction, dedupe) are the same for all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MARK_START = "<!-- specanvil:start -->"
MARK_END = "<!-- specanvil:end -->"

# marker files/dirs that show a repo already uses a tool
DETECT = {
    "claude": [".claude", "CLAUDE.md"],
    "copilot": [".github/copilot-instructions.md", ".github/instructions", ".github/prompts", ".github/agents",
                ".github/chatmodes", ".vscode/mcp.json"],
    "cursor": [".cursor", ".cursorrules"],
    "augment": [".augment", ".augment-guidelines"],
    "windsurf": [".windsurf", ".windsurfrules", ".devin"],
    "codex": [".codex"],
    "kiro": [".kiro"],
    "gemini": [".gemini", "GEMINI.md"],
    "replit": [".replit", "replit.nix", "replit.md"],
}
LABELS = {"claude": "Claude Code", "copilot": "GitHub Copilot (VS Code)", "cursor": "Cursor", "augment": "Augment Code",
          "windsurf": "Windsurf / Devin Local", "codex": "OpenAI Codex", "kiro": "Kiro", "gemini": "Gemini CLI",
          "replit": "Replit Agent"}
ALL = list(DETECT)


def detect(root: Path) -> list[str]:
    return [p for p, marks in DETECT.items() if any((root / m).exists() for m in marks)]


def resolve(root: Path, requested: str | None, saved: list[str] | None = None) -> list[str]:
    """`requested`: None/"auto" (detected + previously chosen, Claude Code if nothing found), "all", or a list."""
    if requested and requested not in ("auto",):
        names = ALL if requested == "all" else [x.strip().lower() for x in requested.split(",") if x.strip()]
        bad = [n for n in names if n not in DETECT]
        if bad:
            raise ValueError(f"Unknown tool(s) {bad}. Supported: {', '.join(ALL)}")
        return list(dict.fromkeys(names))
    names = list(dict.fromkeys((saved or []) + detect(root)))
    return names or ["claude"]


# ---------------------------------------------------------------------------- the provider-neutral bundle

@dataclass
class Command:
    name: str          # "groom"
    description: str
    argument_hint: str
    body: str          # uses $ARGUMENTS, /sa:<cmd>, .claude/... paths (the Claude dialect; translated per tool)


@dataclass
class Agent:
    name: str          # "backend-engineer"
    description: str
    body: str
    role: str | None


@dataclass
class Skill:
    name: str
    description: str
    body: str
    keep: bool = False  # generated once from the repo, then owned by the team (never overwritten)


@dataclass
class Bundle:
    values: dict
    stack: dict
    commands: list[Command]
    agents: list[Agent]
    skills: list[Skill]
    servers: dict      # Claude-style MCP server configs, env as ${VAR}
    rules_core: str    # the shared rules text (markdown, no markers), written in the neutral dialect
    profile: dict = field(default_factory=dict)


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    head, _, body = text[3:].partition("\n---\n")
    meta = {}
    for ln in head.strip().splitlines():
        k, sep, v = ln.partition(":")
        if sep:
            meta[k.strip()] = v.strip()
    return meta, body.lstrip("\n")


# ---------------------------------------------------------------------------- per-tool dialect

CMD_SYNTAX = {  # how a person runs the "groom" workflow in each tool
    "claude": "/sa:{c}", "augment": "/sa:{c}", "gemini": "/sa:{c}",
    "copilot": "/sa-{c}", "cursor": "/sa-{c}", "kiro": "#sa-{c}",
    "codex": "the sa-{c} skill", "windsurf": "the sa-{c} skill", "replit": "the {c} playbook",
}
CMD_FILE = {
    "claude": ".claude/commands/sa/{c}.md", "augment": ".augment/commands/sa/{c}.md",
    "gemini": ".gemini/commands/sa/{c}.toml", "copilot": ".github/prompts/sa-{c}.prompt.md",
    "cursor": ".cursor/commands/sa-{c}.md", "kiro": ".kiro/steering/sa-{c}.md",
    "codex": ".agents/skills/sa-{c}/SKILL.md", "windsurf": ".agents/skills/sa-{c}/SKILL.md",
    "replit": ".specanvil/playbooks/{c}.md",
}
SKILL_DIR = {"claude": ".claude/skills", "copilot": ".github/skills", "augment": ".augment/skills",
             "codex": ".agents/skills", "windsurf": ".agents/skills", "gemini": ".agents/skills",
             "cursor": ".cursor/rules", "kiro": ".kiro/steering", "replit": ".specanvil/playbooks/skills"}
ARGS = {"claude": "$ARGUMENTS", "augment": "$ARGUMENTS", "gemini": "{{args}}", "copilot": "${input:target}",
        "cursor": "(the text typed after the command)", "kiro": "(the user's request)",
        "codex": "(the user's request)", "windsurf": "(the user's request)", "replit": "(the user's request)"}
HAS_SUBAGENTS = {"claude", "copilot", "codex"}
HAS_MCP = {"claude", "copilot", "cursor", "augment", "windsurf", "codex", "kiro", "gemini"}


def translate(text: str, tool: str) -> str:
    """Rewrite the Claude-dialect asset text for one tool: command syntax, file paths, argument placeholder."""
    if tool == "claude":
        return text
    text = re.sub(r"\.claude/commands/sa/([\w-]+)\.md", lambda m: CMD_FILE[tool].format(c=m.group(1)), text)
    text = text.replace(".claude/skills/", SKILL_DIR[tool] + "/")
    text = text.replace(".claude/agents/", ".github/agents/" if tool == "copilot" else
                        ".codex/agents/" if tool == "codex" else "the role sections of each workflow/")
    text = re.sub(r"/sa:([\w-]+)", lambda m: CMD_SYNTAX[tool].format(c=m.group(1)), text)
    text = text.replace("$ARGUMENTS", ARGS[tool])
    if tool not in HAS_SUBAGENTS:
        text = re.sub(r"\bsub-?agents?\b", "a separate pass", text)
    if tool not in HAS_MCP:
        text = _mcp_to_cli(text)
    return text


def _mcp_to_cli(text: str) -> str:
    """For tools without local MCP (Replit): point at the CLI bridge instead of MCP tool calls."""
    return text.replace("the specanvil MCP", "the SpecAnvil CLI (`specanvil tool <name> '<json args>'`)").replace(
        "specanvil MCP", "SpecAnvil CLI")


def _yaml(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)  # JSON strings are valid YAML scalars


def _toml_str(v: str) -> str:
    if "'''" not in v:
        return "'''\n" + v + "'''"
    return json.dumps(v)


# ---------------------------------------------------------------------------- rules (always-on)

def rules_block(bundle: Bundle, tools: list[str], header_tool: str | None = None) -> str:
    """Always-on rules. With several tools sharing one file (AGENTS.md), the workflow names are shown in each
    tool's syntax so nobody has to translate."""
    v = bundle.values
    shown = [t for t in tools if t != "claude"] or tools

    def how(c: str) -> str:
        if len(shown) == 1 or header_tool:
            f = CMD_SYNTAX[header_tool or shown[0]].format(c=c)
            return f"`{f}`" if f.startswith(("/", "#")) else f
        return f"`{c}`"
    forms: dict[str, list[str]] = {}
    for t in ([header_tool] if header_tool else shown):
        forms.setdefault(CMD_SYNTAX[t].format(c="<name>"), []).append(LABELS[t].split(" (")[0])
    run_line = "" if (header_tool or len(shown) == 1) else "Run a workflow by name: " + "; ".join(
        f"{', '.join(lbl)}: {'`' + f + '`' if f.startswith(('/', '#')) else f}" for f, lbl in forms.items()) + ".\n"
    if header_tool == "replit":
        run_line = ("Playbooks live in `.specanvil/playbooks/<name>.md` and skills in `.specanvil/playbooks/skills/`: when "
                    "asked to do one of these steps, open the matching file and follow it.\n")
    flow = " → ".join(["groom", "design", "plan", "be + fe", "db", "qa", "e2e", "verify", "ship"])
    mcp_line = ("Use the **specanvil** MCP server before reading files: `repo_map`, `get_context(task)`, `search_symbols`, "
                "`get_symbol`, `find_references`, `read_spec(path, section)`. Read a whole file only when editing most of it. "
                "It starts with a small tool set; call `set_tool_tier(\"standard\" | \"analysis\" | \"full\")` or "
                "`run_tool(name, arguments)` when a workflow names a tool you don't have.")
    cli_line = ("This tool can't run local MCP servers, so use the SpecAnvil CLI from the shell instead of reading whole "
                "files: `specanvil tool get_context '{\"task\": \"...\"}'`, `specanvil tool search_symbols '{\"query\": \"...\"}'`, "
                "`specanvil tool get_symbol '{\"ref\": \"path::Name\"}'`, `specanvil tool read_spec '{\"path\": \"...\"}'`. "
                "`specanvil tool --list` shows every tool.")
    nav = []
    if any(t in HAS_MCP for t in tools):
        nav.append(mcp_line)
    if any(t not in HAS_MCP for t in tools):
        nav.append(cli_line)
    cmds = "\n".join(f"- {k}: `{c}`" for k, c in bundle.stack.get("commands", {}).items()) or "- not detected"
    skills_where = sorted({SKILL_DIR[t] for t in tools})
    return f"""{MARK_START}
## Spec-driven development (SpecAnvil)

Specs in `{v['specs_dir']}/` are the source of truth: `project.md`, `conventions.md`, `modules/`; feature specs in
`{v['features_dir']}/<ID>/` (requirements, design, tasks, test cases).

Workflow: {flow}.
{run_line}- groom a ticket: {how('groom')}
- design / plan: {how('design')}, {how('plan')}
- implement: {how('be')}, {how('fe')}; database review: {how('db')}
- test cases / E2E: {how('qa')}, {how('e2e')}; review: {how('verify')}; ship: {how('ship')}
- whole chain for one ticket: {how('flow')}; small change: {how('spec')}; board: {how('status')}
- unit tests: {how('test-plan')} then {how('test-run')}; docs: {how('docs')}; ticket history: {how('backfill')}

{v.get('spec_note', '')}### Code navigation (saves tokens)
{chr(10).join(nav)}

### Commands
{cmds}

### Rules
- Code and specs change together. Reference code in specs as `` `path::Qualname` ``.
- Follow `{v['specs_dir']}/conventions.md` and the project skills in {', '.join(f'`{d}/`' for d in skills_where)}.
- Never commit secrets; never write to shared databases or tickets without asking.
{MARK_END}
"""


def merge_block(existing: str | None, block: str, title: str) -> str:
    if not existing:
        return f"# {title}\n\n{block}"
    if MARK_START in existing and MARK_END in existing:
        return re.sub(re.escape(MARK_START) + ".*?" + re.escape(MARK_END) + r"\n?", lambda _: block, existing, flags=re.S)
    return existing.rstrip() + "\n\n" + block


# ---------------------------------------------------------------------------- MCP config per tool

def _env_style(cfg: dict, style: str) -> dict:
    """Rewrite ${VAR} references for tools that spell them ${env:VAR}."""
    out = json.loads(json.dumps(cfg))
    if style == "env:" and "env" in out:
        out["env"] = {k: re.sub(r"\$\{(\w+)\}", r"${env:\1}", str(v)) for k, v in out["env"].items()}
    return out


def _merge_json(path: Path, key: str, servers: dict) -> str:
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    node = data.setdefault(key, {})
    for name, cfg in servers.items():
        node.setdefault(name, cfg)  # never overwrite a server the user configured themselves
    return json.dumps(data, indent=2) + "\n"


def mcp_files(root: Path, tool: str, servers: dict) -> list[tuple[str, str, str]]:
    own = {"specanvil": {"command": "specanvil-mcp", "args": []}}
    others = {k: v for k, v in servers.items() if k != "specanvil"}
    if tool == "copilot":
        conf = {k: {"type": "stdio", **_env_style(v, "env:")} for k, v in {**own, **others}.items()}
        conf["specanvil"]["env"] = {"SPECANVIL_ROOT": "${workspaceFolder}"}
        return [(".vscode/mcp.json", _merge_json(root / ".vscode/mcp.json", "servers", conf), "write")]
    if tool == "cursor":
        conf = {k: _env_style(v, "env:") for k, v in {**own, **others}.items()}
        conf["specanvil"]["env"] = {"SPECANVIL_ROOT": "${workspaceFolder}"}
        return [(".cursor/mcp.json", _merge_json(root / ".cursor/mcp.json", "mcpServers", conf), "write")]
    if tool == "kiro":
        conf = {**own, **others}
        return [(".kiro/settings/mcp.json", _merge_json(root / ".kiro/settings/mcp.json", "mcpServers", conf), "write")]
    if tool == "gemini":
        conf = {**own, **others}
        return [(".gemini/settings.json", _merge_json(root / ".gemini/settings.json", "mcpServers", conf), "write")]
    if tool == "windsurf":  # only the specanvil server: env interpolation for this file isn't documented
        return [(".devin/mcp_config.json", _merge_json(root / ".devin/mcp_config.json", "mcpServers", own), "write")]
    if tool == "codex":
        p = root / ".codex/config.toml"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if "[mcp_servers.specanvil]" not in text:
            text = (text.rstrip() + "\n\n" if text else "") + \
                "# SpecAnvil code index (project config applies once you trust this project in Codex)\n" \
                "[mcp_servers.specanvil]\ncommand = \"specanvil-mcp\"\nargs = []\n"
        return [(".codex/config.toml", text, "write")]
    if tool == "augment":  # no project-level MCP file: an import file for Settings > MCP > Import from JSON
        conf = {k: {kk: vv for kk, vv in v.items() if kk != "env"} for k, v in {**own, **others}.items()}
        return [(".specanvil/mcp/augment-import.json", json.dumps({"mcpServers": conf}, indent=2) + "\n", "write")]
    return []


# ---------------------------------------------------------------------------- hooks (programmatic guard rails)

def hook_files(root: Path, tool: str) -> list[tuple[str, str, str]]:
    """Secret-file guard (and re-index after edits) for the tools that document a hook system."""
    if tool == "cursor":
        p = root / ".cursor/hooks.json"
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        data.setdefault("version", 1)
        hooks = data.setdefault("hooks", {})
        for event, cmd in (("beforeReadFile", "specanvil hook guard"), ("beforeShellExecution", "specanvil hook guard"),
                           ("afterFileEdit", "specanvil hook post-edit")):
            lst = hooks.setdefault(event, [])
            if not any(h.get("command") == cmd for h in lst if isinstance(h, dict)):
                lst.append({"command": cmd})
        return [(".cursor/hooks.json", json.dumps(data, indent=2) + "\n", "write")]
    if tool == "copilot":
        conf = {"version": 1, "hooks": {"preToolUse": [{"type": "command", "command": "specanvil hook guard",
                                                        "timeoutSec": 10}]}}
        return [(".github/hooks/specanvil.json", json.dumps(conf, indent=2) + "\n", "write")]
    return []


# ---------------------------------------------------------------------------- per-tool renderers

def _cmd_files(tool: str, b: Bundle) -> list[tuple[str, str, str]]:
    out = []
    for c in b.commands:
        body = translate(c.body, tool)
        c = Command(c.name, translate(c.description, tool), c.argument_hint, c.body)
        if c.name == "flow" and tool not in HAS_SUBAGENTS:
            body = "If you can't hand steps to separate agents, run them yourself in order and stop at each gate.\n\n" + body
        rel = CMD_FILE[tool].format(c=c.name)
        if tool == "copilot":
            head = [f"name: sa-{c.name}", f"description: {_yaml(c.description)}"]
            if c.argument_hint:
                head.append(f"argument-hint: {_yaml(c.argument_hint)}")
            head.append("agent: agent")
            out.append((rel, "---\n" + "\n".join(head) + "\n---\n\n" + body, "write"))
        elif tool == "augment":
            head = [f"description: {_yaml(c.description)}"]
            if c.argument_hint:
                head.append(f"argument-hint: {_yaml(c.argument_hint)}")
            out.append((rel, "---\n" + "\n".join(head) + "\n---\n\n" + body, "write"))
        elif tool == "cursor":
            out.append((rel, f"<!-- {c.description} -->\n\n" + body, "write"))
        elif tool == "gemini":
            out.append((rel, f"description = {json.dumps(c.description)}\nprompt = {_toml_str(body)}\n", "write"))
        elif tool in ("codex", "windsurf"):
            desc = f"SpecAnvil workflow: {c.description}. Use when asked to {c.name.replace('-', ' ')}."
            out.append((rel, f"---\nname: sa-{c.name}\ndescription: {_yaml(desc[:1000])}\n---\n\n" + body, "write"))
        elif tool == "kiro":
            out.append((rel, f"---\ninclusion: manual\n---\n\n<!-- {c.description} -->\n\n" + body, "write"))
        elif tool == "replit":
            out.append((rel, f"<!-- {c.description} -->\n\n" + body, "write"))
    return out


def _agent_files(tool: str, b: Bundle) -> list[tuple[str, str, str]]:
    from .profile import provider_model
    out = []
    for a in b.agents:
        body = translate(a.body, tool)
        a = Agent(a.name, translate(a.description, tool), a.body, a.role)
        model = provider_model(tool, a.role, b.profile) if a.role else None
        if tool == "copilot":
            head = [f"name: sa-{a.name}", f"description: {_yaml(a.description)}", "tools: ['specanvil/*', 'edit', 'search', "
                    "'runCommands', 'fetch', 'todos']"]
            if model:
                head.append(f"model: {_yaml(model)}")
            out.append((f".github/agents/sa-{a.name}.agent.md", "---\n" + "\n".join(head) + "\n---\n\n" + body, "write"))
        elif tool == "codex":
            lines = [f"name = {json.dumps('sa-' + a.name)}", f"description = {json.dumps(a.description)}"]
            if model:
                lines.append(f"model = {json.dumps(model)}")
            lines.append(f"developer_instructions = {_toml_str(body)}")
            out.append((f".codex/agents/sa-{a.name}.toml", "\n".join(lines) + "\n", "write"))
    return out


def _skill_files(tool: str, b: Bundle) -> list[tuple[str, str, str]]:
    out = []
    for s in b.skills:
        body = translate(s.body, tool)
        s = Skill(s.name, translate(s.description, tool), s.body, s.keep)
        mode = "keep" if s.keep else "write"
        if tool == "cursor":
            out.append((f".cursor/rules/{s.name}.mdc", f"---\ndescription: {_yaml(s.description)}\nalwaysApply: false\n---\n\n"
                        + body, mode))
        elif tool == "kiro":
            out.append((f".kiro/steering/{s.name}.md", f"---\ninclusion: auto\nname: {s.name}\ndescription: "
                        f"{_yaml(s.description)}\n---\n\n" + body, mode))
        elif tool == "replit":
            out.append((f".specanvil/playbooks/skills/{s.name}.md", f"<!-- {s.description} -->\n\n" + body, mode))
        else:
            out.append((f"{SKILL_DIR[tool]}/{s.name}/SKILL.md", f"---\nname: {s.name}\ndescription: {_yaml(s.description)}\n---\n\n"
                        + body, mode))
    return out


def _kiro_steering(b: Bundle, project_md: str, conventions_md: str) -> list[tuple[str, str, str]]:
    """Kiro's foundational steering files, seeded from SpecAnvil's generated specs (kept if the user edited them)."""
    st = b.stack
    product = f"# Product\n\nSee `{b.values['specs_dir']}/project.md` for the reverse-engineered overview.\n\n" + \
        "\n".join(project_md.splitlines()[:40])
    langs = ", ".join(x["lang"] if isinstance(x, dict) else str(x) for x in st.get("languages", [])[:8])
    rows = [("Languages", langs)] + [(label, ", ".join(map(str, st.get(key) or [])))
                                     for label, key in (("Frontend", "frontend"), ("Backend", "backend"),
                                                        ("Databases", "databases"), ("Tests", "test_frameworks"))]
    tech = "# Tech stack\n\n" + "\n".join(f"- {k}: {v}" for k, v in rows if v) + "\n\n## Commands\n" + \
        ("\n".join(f"- {k}: `{c}`" for k, c in st.get("commands", {}).items()) or "- not detected") + "\n"
    structure = "# Structure and conventions\n\n" + conventions_md
    return [(".kiro/steering/product.md", translate(product, "kiro") + "\n", "keep"),
            (".kiro/steering/tech.md", tech, "keep"), (".kiro/steering/structure.md", translate(structure, "kiro"), "keep")]


def render(root: Path, tools: list[str], b: Bundle, project_md: str = "", conventions_md: str = "") -> list[tuple[str, str, str]]:
    """All files for the non-Claude tools in `tools`: (relative path, content, mode) with mode write | keep."""
    files: list[tuple[str, str, str]] = []
    others = [t for t in tools if t != "claude"]
    if not others:
        return files
    agents_md_tools = [t for t in others if t not in ("gemini", "replit")]
    if agents_md_tools:
        p = root / "AGENTS.md"
        files.append(("AGENTS.md", merge_block(p.read_text(encoding="utf-8") if p.exists() else None,
                                               rules_block(b, agents_md_tools), root.name), "write"))
    if "copilot" in others:
        p = root / ".github/copilot-instructions.md"
        pointer = f"{MARK_START}\nThis repo uses SpecAnvil. Follow the rules and workflows in `AGENTS.md` (always loaded by " \
                  f"Copilot agent mode); workflows are prompt files in `.github/prompts/` (`/sa-groom`, `/sa-flow` ...).\n{MARK_END}\n"
        files.append((".github/copilot-instructions.md", merge_block(p.read_text(encoding="utf-8") if p.exists() else None,
                                                                    pointer, "Copilot instructions"), "write"))
    if "gemini" in others:
        p = root / "GEMINI.md"
        files.append(("GEMINI.md", merge_block(p.read_text(encoding="utf-8") if p.exists() else None,
                                               rules_block(b, ["gemini"], "gemini"), root.name), "write"))
    if "replit" in others:
        p = root / "replit.md"
        files.append(("replit.md", merge_block(p.read_text(encoding="utf-8") if p.exists() else None,
                                               rules_block(b, ["replit"], "replit"), root.name), "write"))
    seen: set[str] = set()
    for t in others:
        for rel, content, mode in (_cmd_files(t, b) + _agent_files(t, b) + _skill_files(t, b) + mcp_files(root, t, b.servers)
                                   + hook_files(root, t)):
            if rel in seen:  # codex and windsurf share .agents/skills
                continue
            seen.add(rel)
            files.append((rel, content, mode))
        if t == "kiro":
            files += _kiro_steering(b, project_md, conventions_md)
    return files


def setup_notes(tools: list[str]) -> list[str]:
    """What the person still has to do by hand in each tool (shown after init)."""
    notes = {
        "copilot": "Copilot: AGENTS.md is read by agent mode by default (setting chat.useAgentsMdFile). Start the "
                   "specanvil server from .vscode/mcp.json (MCP: List Servers).",
        "cursor": "Cursor: enable the specanvil server under Settings > MCP the first time.",
        "augment": "Augment: add MCP servers via Settings > MCP > Import from JSON using .specanvil/mcp/augment-import.json "
                   "(set connector API keys in the Environment Variables section there).",
        "windsurf": "Windsurf / Devin Local: .devin/mcp_config.json holds the specanvil server; add ticket or database "
                    "servers in the MCP settings with your keys.",
        "codex": "Codex: mark this project as trusted so .codex/config.toml (specanvil MCP) and .codex/agents load.",
        "kiro": "Kiro: approve the env vars used by connector servers (setting 'Mcp Approved Env Vars'). Feature specs go "
                "in .kiro/specs/<feature>/.",
        "gemini": "Gemini CLI: run /mcp to check the specanvil server; /commands reload after updates.",
        "replit": "Replit: Agent can't run local MCP servers. replit.md tells it to use `specanvil tool ...` in the shell "
                  "(install with: pip install git+https://github.com/Abdulwahab26/SpecAnvil).",
    }
    return [notes[t] for t in tools if t in notes]
