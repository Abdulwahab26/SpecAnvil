"""Static configuration: ignore rules, secret protection, limits."""

from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = ".specanvil"
INDEX_FILE = "index.db"
CONFIG_FILE = "config.json"
IGNORE_FILE = ".specanvilignore"

MAX_FILE_BYTES = 1_000_000  # skip very large files (generated, minified, dumps)

# Always skipped, on top of .gitignore and .specanvilignore.
DEFAULT_IGNORES = [
    ".git/", ".specanvil/", ".specforge/", ".claude/", "node_modules/", "bower_components/", "vendor/",
    "dist/", "build/", "out/", "target/", "bin/", "obj/", ".next/", ".nuxt/",
    ".svelte-kit/", ".turbo/", ".cache/", "coverage/", ".nyc_output/",
    "__pycache__/", ".venv/", "venv/", "env/", ".tox/", ".mypy_cache/",
    ".pytest_cache/", ".gradle/", ".idea/", ".vscode/", ".terraform/",
    "Pods/", "DerivedData/", "playwright-report/", "test-results/",
    "*.min.js", "*.min.css", "*.map", "*.lock", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "*.pyc", "*.class", "*.dll", "*.exe",
    "*.so", "*.dylib", "*.jar", "*.war", "*.zip", "*.tar", "*.gz", "*.7z",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.svg",
    "*.pdf", "*.mp4", "*.mov", "*.mp3", "*.woff", "*.woff2", "*.ttf", "*.eot",
    "*.sqlite", "*.sqlite3", "*.db",
]

# Never indexed, never returned to the model. Protects credentials by default.
SECRET_GLOBS = [
    ".env", ".env.*", "*.env", "!.env.example", "!.env.sample", "!.env.template",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*",
    "id_ed25519*", "*.kdbx", "credentials*", "*secret*.json", "*secrets*.y*ml",
    ".npmrc", ".pypirc", ".netrc", "*.tfstate", "*.tfvars", "local.settings.json",
    "secrets.json", "*.publishsettings", "!local.settings.sample.json", "!local.settings.example.json",
]

# Languages we parse only as text (no symbols).

DEFAULT_CONFIG = {
    "version": 1,
    "specs_dir": "specs",
    "tickets": None,          # e.g. "jira", "azure-devops", "github", "linear", "gitlab"
    "databases": [],          # e.g. ["postgres", "mongodb"]
    "design": [],             # e.g. ["figma"]
    "e2e": "playwright",
    "app_url": "http://localhost:3000",
    "connectors": [],         # names from the connector catalog written into .mcp.json
    "context_budget_tokens": 6000,
    "redact_secrets": True,
}


LEGACY_STATE_DIR = ".specforge"  # name before the rename to SpecAnvil


def state_dir(root: Path) -> Path:
    """The per-repo state folder. A repo set up under the old name is moved over on first use."""
    new = root / STATE_DIR
    if not new.exists():
        old = root / LEGACY_STATE_DIR
        if old.is_dir():
            try:
                old.rename(new)
            except OSError:
                return old  # e.g. a read-only checkout: keep using the old folder
    return new


LEGACY_TEXT = [("specforge-mcp", "specanvil-mcp"), ("specforge hook", "specanvil hook"), ("mcp__specforge", "mcp__specanvil"),
               ("specforge:start", "specanvil:start"), ("specforge:end", "specanvil:end"), ("'specforge/*'", "'specanvil/*'"),
               ('"specforge":', '"specanvil":'), ("[mcp_servers.specforge]", "[mcp_servers.specanvil]")]


def migrate_legacy_text(text: str) -> str:
    """Rewrite what an old SpecForge setup wrote into a shared file (server names, hook commands, markers) so a
    re-run updates it in place instead of adding a second copy."""
    for a, b in LEGACY_TEXT:
        text = text.replace(a, b)
    return text


def load_config(root: Path) -> dict:
    path = state_dir(root) / CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(root: Path, cfg: dict) -> Path:
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / CONFIG_FILE
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path


def features_dir(cfg: dict) -> str:
    """Where feature specs live: <specs_dir>/features by default; tools with their own spec layout (Kiro) change it."""
    return (cfg.get("features_dir") or f"{cfg.get('specs_dir', 'specs')}/features").rstrip("/")


def discover_root(start: Path | None = None, env: dict | None = None) -> Path:
    """The project an MCP server should serve, for any client:
    SPECANVIL_ROOT, then the client's own project variable (Claude Code: CLAUDE_PROJECT_DIR), then the nearest
    folder above the working directory that holds .specanvil/ or .git, then the working directory itself.
    Values a client left unexpanded (e.g. a literal "${workspaceFolder}") are ignored."""
    import os
    env = os.environ if env is None else env
    for var in ("SPECANVIL_ROOT", "CLAUDE_PROJECT_DIR", "WORKSPACE_FOLDER", "PROJECT_ROOT"):
        v = (env.get(var) or "").strip()
        if v and "${" not in v and Path(v).is_dir():
            return Path(v).resolve()
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        if (d / ".specanvil").is_dir() or (d / ".specforge").is_dir():
            return d
    for d in [cur, *cur.parents]:
        if (d / ".git").exists():
            return d
    return cur


def debug_exc(where: str) -> None:
    """Record an error that is deliberately not raised (telemetry, optional features). Silent by default;
    set SPECANVIL_DEBUG=1 to see each one on stderr with its traceback."""
    import logging
    import os
    log = logging.getLogger("specanvil")
    if os.environ.get("SPECANVIL_DEBUG") and not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("specanvil debug: %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.DEBUG)
    log.debug("ignored error in %s", where, exc_info=True)


LEGACY_SHARED_FILES = ["CLAUDE.md", "AGENTS.md", "GEMINI.md", "replit.md", ".github/copilot-instructions.md",
                       ".claude/settings.json", ".mcp.json", ".vscode/mcp.json", ".cursor/mcp.json", ".cursor/hooks.json",
                       ".kiro/settings/mcp.json", ".gemini/settings.json", ".devin/mcp_config.json", ".codex/config.toml",
                       ".github/hooks/specforge.json"]


def migrate_legacy_files(root: Path) -> list[str]:
    """One-time upgrade of a repo set up as SpecForge: rewrite the old names in shared files, rename the Copilot
    hooks file and update the install manifest. Returns the files changed. Safe to run repeatedly."""
    changed = []
    sd = state_dir(root)  # also moves .specforge/ -> .specanvil/
    for rel in LEGACY_SHARED_FILES:
        p = root / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        new = migrate_legacy_text(text)
        if rel == ".github/hooks/specforge.json":
            target = root / ".github/hooks/specanvil.json"
            if not target.exists():
                target.write_text(new, encoding="utf-8")
            p.unlink()
            changed.append(rel)
        elif new != text:
            p.write_text(new, encoding="utf-8")
            changed.append(rel)
    man = sd / "manifest.json"
    if man.is_file():
        text = man.read_text(encoding="utf-8")
        new = text.replace('"specforge"', '"specanvil"').replace("hooks/specforge.json", "hooks/specanvil.json")
        if new != text:
            man.write_text(new, encoding="utf-8")
            changed.append(str(man.relative_to(root)))
    return changed

