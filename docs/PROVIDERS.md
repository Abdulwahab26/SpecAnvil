# SpecForge in any AI coding tool

SpecForge isn't tied to Claude Code. `specforge init` detects which AI coding tools a repo already uses and writes the same setup (rules, lifecycle workflows, role agents, skills, MCP connection) in each tool's own formats. The code index, the specs and the server-side guarantees (secret filtering, redaction, session dedupe, tool tiers) are identical everywhere.

```bash
specforge init                      # auto: tools detected from their config folders (Claude Code if none)
specforge init --agents cursor,copilot
specforge init --agents all
specforge agents list               # supported / detected / configured
specforge agents add kiro           # add a tool later; earlier choices are kept
```

## What each tool gets

| Tool | Always-on rules | Workflows (`groom`, `design`, … `flow`) | Role agents | Skills | MCP |
|---|---|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/commands/sf/*.md` → `/sf:groom` | `.claude/agents/*.md` (model per role) | `.claude/skills/` | `.mcp.json` |
| GitHub Copilot (VS Code) | `AGENTS.md` + pointer in `.github/copilot-instructions.md` | `.github/prompts/sf-*.prompt.md` → `/sf-groom` | `.github/agents/sf-*.agent.md` (tools `specforge/*`; optional model) | `.github/skills/` | `.vscode/mcp.json` (`servers`, `${workspaceFolder}`) |
| Cursor | `AGENTS.md` | `.cursor/commands/sf-*.md` → `/sf-groom` | role text inside each workflow | `.cursor/rules/*.mdc` (agent-requested) | `.cursor/mcp.json` |
| Augment Code | `AGENTS.md` | `.augment/commands/sf/*.md` → `/sf:groom` (`$ARGUMENTS`) | role text inside each workflow | `.augment/skills/` | `.specforge/mcp/augment-import.json` → Settings › MCP › Import from JSON |
| Windsurf / Devin Local | `AGENTS.md` | Agent Skills `.agents/skills/sf-*` | role text inside each workflow | `.agents/skills/` | `.devin/mcp_config.json` (specforge server) |
| OpenAI Codex | `AGENTS.md` | Agent Skills `.agents/skills/sf-*` | `.codex/agents/sf-*.toml` (optional model) | `.agents/skills/` | `.codex/config.toml` (trusted projects) |
| Kiro | `AGENTS.md` + `.kiro/steering/{product,tech,structure}.md` | `.kiro/steering/sf-*.md` (manual) → `#sf-groom` | role text inside each workflow | steering files (`inclusion: auto`) | `.kiro/settings/mcp.json` |
| Gemini CLI | `GEMINI.md` | `.gemini/commands/sf/*.toml` → `/sf:groom` (`{{args}}`) | role text inside each workflow | `.agents/skills/` | `.gemini/settings.json` |
| Replit Agent | `replit.md` | `.specforge/playbooks/*.md` | role text inside each workflow | `.specforge/playbooks/skills/` | none locally: `specforge tool <name> '<json>'` from the shell |

**Specs per tool.** Specs live in `specs/` for every tool. When Kiro is in use, feature specs go in Kiro's own layout, `.kiro/specs/<feature>/` (requirements, design, tasks), so they show up in Kiro's spec view; templates move to `specs/templates/`. Drift checks, `list_specs` and `read_spec` cover both folders.

**Models per role.** Claude Code agents get `model:` from your profile as before. Copilot, Augment and Codex accept a model per agent or command in their own naming, set with `specforge profile set providers.copilot.models.architect "Claude Opus 4.5"`. Without that, those tools use the model picked in the tool. Other tools have no per-agent model setting.

**MCP root.** The server finds the project on its own: `SPECFORGE_ROOT`, the client's project variable, then the nearest folder above its working directory with `.specforge/` or `.git`. Values a client leaves unexpanded (a literal `${workspaceFolder}`) are ignored. Tested by launching the server from a subfolder, with an unexpanded variable, and with an expanded one.

**Tools without local MCP (Replit).** `specforge tool <name> '<json args>'` runs any SpecForge tool from the shell, so the agent still reads one function instead of whole files. `specforge tool --list` shows them all.

## Limits by tool

- **Programmatic guard rails differ.** The PreToolUse secret-file guard and the post-edit re-index hook are Claude Code hooks. Elsewhere, secrets are still never indexed and are redacted from every tool result (server side), and the index refreshes itself every 20 seconds, but a tool's own file reader isn't blocked from opening a secret file.
- **Sub-agents.** `/flow` hands steps to role agents in Claude Code, Copilot and Codex. In the other tools the workflow tells the agent to run the steps itself, in order, stopping at each gate.
- **Connector servers.** Ticket and database MCP servers are written for Claude Code, Copilot, Cursor, Kiro and Gemini with each tool's env-var syntax. Windsurf and Codex get the SpecForge server only (their env syntax for project files isn't documented); add connectors in those tools' MCP settings. Augment has no project-level MCP file, so it gets an import file.
- **Formats change.** These tools ship new formats often. They were checked against each tool's documentation on 2026-11-11.

## Sources

- GitHub Copilot: [customization overview](https://code.visualstudio.com/docs/copilot/customization/overview), [custom agents](https://code.visualstudio.com/docs/copilot/customization/custom-agents), [prompt files](https://code.visualstudio.com/docs/copilot/customization/prompt-files), [agent skills](https://code.visualstudio.com/docs/copilot/customization/agent-skills), [MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- Cursor: [rules](https://cursor.com/docs/rules), [MCP](https://cursor.com/docs/mcp), [commands](https://docs.cursor.com/en/agent/chat/commands)
- Augment Code: [rules](https://docs.augmentcode.com/cli/rules), [custom commands](https://docs.augmentcode.com/using-augment/custom-commands), [skills](https://docs.augmentcode.com/using-augment/skills), [MCP](https://docs.augmentcode.com/setup-augment/mcp)
- Windsurf / Devin Local: [customizations](https://docs.devin.ai/desktop/devin-local/customizations)
- OpenAI Codex: [customisation stack overview](https://codex.danielvaughan.com/2026/04/12/codex-cli-customisation-stack-unified-system/) (third-party summary; the official manual page didn't load)
- Kiro: [steering](https://kiro.dev/docs/steering/), [specs](https://kiro.dev/docs/specs/), [MCP configuration](https://kiro.dev/docs/mcp/configuration/)
- Gemini CLI: [custom commands](https://geminicli.com/docs/cli/custom-commands/)
- Replit: [replit.md](https://docs.replit.com/core-concepts/agent/replit-dot-md), [connect via MCP](https://docs.replit.com/build/connect-via-mcp)
