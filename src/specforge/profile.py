"""User profile: personal choices (models per agent role, ticket backfill preferences).

Stored per user at ~/.specforge/profile.json (override the folder with SPECFORGE_HOME), so it
follows the person across repos and is never committed. Precedence, lowest to highest:
built-in defaults < project defaults in .specforge/config.json ("profile_defaults") < user profile.
Choices take effect in a repo when agent files are re-rendered (`specforge profile sync`, run
automatically after `set` / `unset` inside a SpecForge repo).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from .config import load_config

MODEL_ALIASES = {"opus", "sonnet", "haiku", "fable", "inherit"}
MODEL_RX = re.compile(r"^claude-[a-z0-9.\-]+$")

# role -> agent file name
ROLES = {
    "test_planner": "unit-test-planner",
    "test_executor": "unit-test-executor",
    "spec_analyst": "spec-analyst",
    "architect": "architect",
    "frontend": "frontend-engineer",
    "backend": "backend-engineer",
    "db": "db-optimizer",
    "qa": "qa-engineer",
    "e2e": "e2e-tester",
    "reviewer": "reviewer",
    "backfill": "ticket-archaeologist",
    "docs": "doc-writer",
}

DEFAULTS: dict = {
    "models": {
        "test_planner": "opus",     # deep analysis: strongest reasoning
        "test_executor": "haiku",   # follows explicit milestones: cheap and fast
        "docs": "sonnet",           # long-form writing from extracted facts
    },
    "backfill": {
        "enabled": False,           # opt-in: pulling ticket history is never automatic
        "statuses": ["Done", "In Progress", "In Review"],
        "since": None,              # e.g. "2025-01-01"; None = no date filter
        "limit": 200,
        "batch_size": 10,
    },
}


def home() -> Path:
    return Path(os.environ.get("SPECFORGE_HOME") or Path.home() / ".specforge")


def profile_path() -> Path:
    return home() / "profile.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_user() -> dict:
    p = profile_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_user(data: dict) -> Path:
    p = profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def effective(root: Path | None = None) -> dict:
    project = load_config(root).get("profile_defaults", {}) if root and (root / ".specforge").exists() else {}
    return _merge(_merge(DEFAULTS, project), load_user())


def validate_model(value: str) -> str:
    v = value.strip()
    if v in MODEL_ALIASES or MODEL_RX.match(v):
        return v
    raise ValueError(f"'{value}' is not a model Claude Code accepts. Use one of {sorted(MODEL_ALIASES)} "
                     "or a full model ID such as claude-opus-5-5.")


def _coerce(key: str, raw: str):
    if key.startswith("models."):
        role = key.split(".", 1)[1]
        if role not in ROLES:
            raise ValueError(f"Unknown role '{role}'. Roles: {', '.join(ROLES)}")
        return validate_model(raw)
    if key == "backfill.statuses":
        return [s.strip() for s in raw.split(",") if s.strip()]
    if key in ("backfill.limit", "backfill.batch_size"):
        n = int(raw)
        if n <= 0:
            raise ValueError(f"{key} must be positive")
        return n
    if key == "backfill.enabled":
        if raw.lower() not in ("true", "false", "yes", "no", "1", "0"):
            raise ValueError("backfill.enabled must be true or false")
        return raw.lower() in ("true", "yes", "1")
    if key == "backfill.since":
        if raw.lower() in ("none", "null", ""):
            return None
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            raise ValueError("backfill.since must be YYYY-MM-DD")
        return raw
    raise ValueError(f"Unknown setting '{key}'. Settings: models.<role>, backfill.enabled, backfill.statuses, "
                     "backfill.since, backfill.limit, backfill.batch_size")


def set_value(key: str, raw: str) -> dict:
    value = _coerce(key, raw)
    data = load_user()
    node = data
    parts = key.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value
    save_user(data)
    return data


def unset_value(key: str) -> dict:
    data = load_user()
    node = data
    parts = key.split(".")
    for p in parts[:-1]:
        node = node.get(p, {})
    node.pop(parts[-1], None)
    save_user(data)
    return data


def model_for_agent(agent_name: str, prof: dict) -> str | None:
    for role, name in ROLES.items():
        if name == agent_name:
            return prof.get("models", {}).get(role)
    return None
