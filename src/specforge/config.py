"""Static configuration: ignore rules, secret protection, limits."""

from __future__ import annotations

import json
from pathlib import Path

STATE_DIR = ".specforge"
INDEX_FILE = "index.db"
CONFIG_FILE = "config.json"
IGNORE_FILE = ".specforgeignore"

MAX_FILE_BYTES = 1_000_000  # skip very large files (generated, minified, dumps)

# Always skipped, on top of .gitignore and .specforgeignore.
DEFAULT_IGNORES = [
    ".git/", ".specforge/", ".claude/", "node_modules/", "bower_components/", "vendor/",
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
DATA_LANGUAGES = {
    "json", "yaml", "toml", "markdown", "xml", "csv", "ini", "properties",
    "dockerfile", "make", "html", "css", "scss", "less", "sql", "graphql",
    "proto", "hcl", "bash", "powershell", "gitignore", "requirements",
}

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


def state_dir(root: Path) -> Path:
    return root / STATE_DIR


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
    SPECFORGE_ROOT, then the client's own project variable (Claude Code: CLAUDE_PROJECT_DIR), then the nearest
    folder above the working directory that holds .specforge/ or .git, then the working directory itself.
    Values a client left unexpanded (e.g. a literal "${workspaceFolder}") are ignored."""
    import os
    env = os.environ if env is None else env
    for var in ("SPECFORGE_ROOT", "CLAUDE_PROJECT_DIR", "WORKSPACE_FOLDER", "PROJECT_ROOT"):
        v = (env.get(var) or "").strip()
        if v and "${" not in v and Path(v).is_dir():
            return Path(v).resolve()
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        if (d / ".specforge").is_dir():
            return d
    for d in [cur, *cur.parents]:
        if (d / ".git").exists():
            return d
    return cur

