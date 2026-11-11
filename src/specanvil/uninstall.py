"""Remove what `specanvil init` wrote for one or more AI tools, and nothing else.

`init` records every file it writes per tool in .specanvil/manifest.json, plus the MCP servers it added to
shared config files. Uninstall then:
  * deletes files SpecAnvil owns (commands, agents, skills, rules, playbooks);
  * strips only the marked SpecAnvil block from shared files (CLAUDE.md, AGENTS.md, GEMINI.md, replit.md ...);
  * removes only the servers and hook commands SpecAnvil added from JSON/TOML config, keeping the user's own;
  * keeps anything still needed by a tool that stays installed, and never touches specs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import load_config, save_config, state_dir
from .providers import MARK_END, MARK_START

MANIFEST = "manifest.json"
BLOCK_FILES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", "replit.md", ".github/copilot-instructions.md"}
SERVER_FILES = {".mcp.json": "mcpServers", ".vscode/mcp.json": "servers", ".cursor/mcp.json": "mcpServers",
                ".kiro/settings/mcp.json": "mcpServers", ".gemini/settings.json": "mcpServers",
                ".devin/mcp_config.json": "mcpServers"}
HOOK_FILES = {".cursor/hooks.json", ".claude/settings.json"}


def load_manifest(root: Path) -> dict:
    p = state_dir(root) / MANIFEST
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def save_manifest(root: Path, tools: dict[str, list[str]], servers: dict[str, list[str]]) -> None:
    old = load_manifest(root)
    merged_servers = {**old.get("servers", {})}
    for path, names in servers.items():
        merged_servers[path] = sorted(set(merged_servers.get(path, [])) | set(names))
    data = {"tools": {**old.get("tools", {}), **{t: sorted(set(v)) for t, v in tools.items()}}, "servers": merged_servers}
    p = state_dir(root) / MANIFEST
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _strip_block(text: str) -> str:
    out = re.sub(r"\n?" + re.escape(MARK_START) + ".*?" + re.escape(MARK_END) + r"\n?", "\n", text, flags=re.S)
    return out.strip("\n") + "\n" if out.strip() else ""


def _only_title(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return not lines or (len(lines) == 1 and lines[0].startswith("# "))


def _drop_hooks(node):
    """Remove hook entries that run `specanvil hook ...`; returns (new node, changed)."""
    if isinstance(node, list):
        kept = []
        for x in node:
            if "specanvil hook" in json.dumps(x):
                if isinstance(x, dict) and isinstance(x.get("hooks"), list):  # Claude: matcher group with several hooks
                    inner = [h for h in x["hooks"] if "specanvil hook" not in json.dumps(h)]
                    if inner:
                        kept.append({**x, "hooks": inner})
                continue
            kept.append(x)
        return kept
    return node


def uninstall(root: Path, tools: list[str] | None = None, dry_run: bool = False, purge: bool = False) -> dict:
    root = root.resolve()
    if not dry_run:
        from .config import migrate_legacy_files
        migrate_legacy_files(root)
    man = load_manifest(root)
    cfg = load_config(root)
    installed = list(man.get("tools", {}))
    if not installed:
        raise ValueError("Nothing to uninstall: no manifest. (Setups made before v0.8 have none; run `specanvil init` "
                         "once to record them, then uninstall.)")
    targets = installed if not tools else [t for t in tools if t in installed]
    unknown = [t for t in (tools or []) if t not in installed]
    if unknown:
        raise ValueError(f"Not installed here: {unknown}. Installed: {installed}")
    keep = {p for t in installed if t not in targets for p in man["tools"][t]}
    actions: list[tuple[str, str]] = []

    def act(kind: str, rel: str, new_text: str | None = None) -> None:
        actions.append((kind, rel))
        if dry_run:
            return
        p = root / rel
        if new_text is None:
            p.unlink(missing_ok=True)
            d = p.parent
            while d != root and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
        else:
            p.write_text(new_text, encoding="utf-8")

    for rel in sorted({p for t in targets for p in man["tools"][t]} - keep):
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if rel in BLOCK_FILES:
            rest = _strip_block(text)
            act("delete" if _only_title(rest) else "edit", rel, None if _only_title(rest) else rest)
        elif rel in SERVER_FILES:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            key = SERVER_FILES[rel]
            servers = data.get(key) or {}
            for name in man.get("servers", {}).get(rel, []):
                servers.pop(name, None)
            own = servers.get("specanvil")  # SpecAnvil's own server is always ours, even if no manifest recorded it
            if isinstance(own, dict) and own.get("command") in ("specanvil-mcp", "specforge-mcp"):
                servers.pop("specanvil")
            if rel == ".claude/settings.json":
                pass
            empty = not data.get(key) and set(data) <= {key}
            act("delete" if empty else "edit", rel, None if empty else json.dumps(data, indent=2) + "\n")
        elif rel in HOOK_FILES:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            hooks = data.get("hooks") or {}
            for ev in list(hooks):
                hooks[ev] = _drop_hooks(hooks[ev])
                if not hooks[ev]:
                    del hooks[ev]
            allow = (data.get("permissions") or {}).get("allow")
            if isinstance(allow, list) and "mcp__specanvil" in allow:
                allow.remove("mcp__specanvil")
                if not allow:
                    data["permissions"].pop("allow")
                if not data["permissions"]:
                    data.pop("permissions")
            if not hooks:
                data.pop("hooks", None)
            empty = not data or set(data) <= {"version"}
            act("delete" if empty else "edit", rel, None if empty else json.dumps(data, indent=2) + "\n")
        elif rel == ".codex/config.toml":
            rest = re.sub(r"(?:# SpecAnvil code index[^\n]*\n)?\[mcp_servers\.specanvil\]\n(?:[^\[\n][^\n]*\n)*", "", text)
            act("delete" if not rest.strip() else "edit", rel, None if not rest.strip() else rest.strip("\n") + "\n")
        else:
            act("delete", rel)
    if not dry_run:
        man["tools"] = {t: v for t, v in man["tools"].items() if t not in targets}
        man["servers"] = {k: v for k, v in man.get("servers", {}).items() if (root / k).exists()}
        cfg["agents"] = [t for t in (cfg.get("agents") or []) if t not in targets]
        if purge and not man["tools"]:
            import shutil
            shutil.rmtree(state_dir(root), ignore_errors=True)
            actions.append(("delete", ".specanvil/"))
        else:
            save_config(root, cfg)
            (state_dir(root) / MANIFEST).write_text(json.dumps(man, indent=1) + "\n", encoding="utf-8")
    return {"removed_tools": targets, "remaining_tools": [t for t in installed if t not in targets], "actions": actions,
            "dry_run": dry_run, "kept": "specs and docs are never removed"}
