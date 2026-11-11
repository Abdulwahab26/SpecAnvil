"""Pick MCP connectors from the catalog based on what the repo uses, and write .mcp.json."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path


def catalog() -> dict:
    text = resources.files("specanvil").joinpath("assets/connectors/catalog.json").read_text(encoding="utf-8")
    return json.loads(text)["connectors"]


def recommend(cfg: dict, stack: dict) -> list[str]:
    """Connectors to enable: core + matches for tickets, databases, design and frontend."""
    cat = catalog()
    tickets = cfg.get("tickets") or (stack.get("ticket_systems") or [None])[0]
    dbs = set(cfg.get("databases") or stack.get("databases") or [])
    design = set(cfg.get("design") or stack.get("design") or [])
    chosen = []
    for name, c in cat.items():
        m = c.get("match") or {}
        if c.get("always"):
            chosen.append(name)
        elif m.get("has_frontend") and (stack.get("has_frontend") or cfg.get("e2e") == "playwright"):
            chosen.append(name)
        elif "tickets" in m and m["tickets"] == tickets:
            chosen.append(name)
        elif "databases" in m and m["databases"] in dbs:
            chosen.append(name)
        elif "design" in m and m["design"] in design:
            chosen.append(name)
    for extra in cfg.get("connectors") or []:
        if extra in cat and extra not in chosen:
            chosen.append(extra)
    return chosen


def write_mcp_json(root: Path, names: list[str], remove: list[str] | None = None) -> tuple[Path, list[str]]:
    """Merge chosen servers into .mcp.json without touching servers the user added themselves."""
    cat = catalog()
    path = root / ".mcp.json"
    data: dict = {"mcpServers": {}}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("mcpServers", {})
        except json.JSONDecodeError:
            pass
    added = []
    for n in names:
        if n in cat and n not in data["mcpServers"]:
            data["mcpServers"][n] = cat[n]["server"]
            added.append(n)
    for n in remove or []:
        data["mcpServers"].pop(n, None)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path, added


def env_requirements(names: list[str]) -> dict[str, list[str]]:
    cat = catalog()
    return {n: cat[n].get("env", []) for n in names if n in cat and cat[n].get("env")}
