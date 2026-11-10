# Security policy

SpecForge reads source code, so its security posture is a feature. What it guarantees, and how each claim is enforced:

| Guarantee | Enforcement |
|---|---|
| Indexing never uses the network | grammar policy in `parser.py`; test `test_indexing_makes_no_network_calls` blocks sockets |
| Semantic search is offline after the one explicit `specforge embed` | model loads with `local_files_only` + `HF_HUB_OFFLINE`; remote embedding endpoints refused unless `semantic.allow_remote` |
| Secret files are never indexed or returned | `scanner.is_secret` (`.env*`, keys, keystores, `local.settings.json`, tfstate, …); `PreToolUse` guard hook blocks reads/writes |
| Secret-looking values are redacted from tool output | `tokens.redact`; counts (never values) via the `redaction_log` tool |
| No telemetry | no network code outside explicit `grammars`, `embed`, `pack add`, `source add gh:` commands |
| Generated MCP config holds no secrets | `.mcp.json` uses `${ENV}` references; DB connectors default to read-only modes |
| Pack archives can't escape their folder | `packs._safe_target` rejects absolute paths and `..`; size caps per archive and per file |

## Reporting a vulnerability

Please report privately via GitHub's **Security → Report a vulnerability** on this repository (or email the maintainer).
Include steps to reproduce and the affected version. You can expect an acknowledgement within 3 working days and a fix
or mitigation plan within 14 days for confirmed issues. Please do not open public issues for vulnerabilities.
