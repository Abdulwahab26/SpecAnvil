"""External sources: dependency packs and remote repositories, indexed once and reused everywhere.

- pkg:<name>   an installed dependency, from node_modules, the project's virtualenv / current Python,
               or the Go module cache. Indexed per version, cached under ~/.specforge/sources, shared by
               every repo that uses the same version. Always matches the code you actually run.
- gh:<owner>/<repo>[@ref]  a GitHub repository, shallow-cloned on explicit request (network).
- path:<dir>   any local folder (another service, a monorepo sibling, a vendored SDK).

Agents query them through the same MCP tools with `source="pkg:react"` etc.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import time
from pathlib import Path

from .index import CodeIndex
from .profile import home


def _registry_path() -> Path:
    return home() / "sources" / "sources.json"


def registry() -> dict:
    p = _registry_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _save(reg: dict) -> None:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def _locate_pkg(name: str, project: Path) -> tuple[Path, str]:
    # npm / pnpm / yarn: walk up from the project for node_modules/<name>
    for base in [project, *project.parents]:
        d = base / "node_modules" / name
        if (d / "package.json").exists():
            try:
                ver = json.loads((d / "package.json").read_text(encoding="utf-8")).get("version", "0")
            except json.JSONDecodeError:
                ver = "0"
            return d.resolve(), ver
    # Python: the project's virtualenv first, then this interpreter
    mod = name.replace("-", "_")
    for venv in (".venv", "venv", "env"):
        for sp in (project / venv).glob("lib/python*/site-packages"):
            if (sp / mod).is_dir():
                dist = next(sp.glob(f"{mod}-*.dist-info"), None) or next(sp.glob(f"{name}-*.dist-info"), None)
                ver = dist.name.split("-")[1].replace(".dist-info", "") if dist else "0"
                return (sp / mod).resolve(), ver
    spec = importlib.util.find_spec(mod)
    if spec and spec.origin:
        d = Path(spec.origin).parent
        try:
            from importlib.metadata import version
            ver = version(name)
        except Exception:  # noqa: BLE001
            ver = "0"
        return d.resolve(), ver
    # Go module cache
    gomod = project / "go.mod"
    if gomod.exists():
        m = re.search(rf"^\s*(?:require\s+)?{re.escape(name)}\s+(v[\w.\-+]+)", gomod.read_text(encoding="utf-8"), re.M)
        if m:
            cache = Path.home() / "go" / "pkg" / "mod" / f"{name}@{m.group(1)}"
            if cache.exists():
                return cache.resolve(), m.group(1)
    raise ValueError(f"Could not find installed package '{name}' (looked in node_modules, the project's "
                     f"virtualenv, the current Python, and the Go module cache). Install it first, or use path:<dir>.")


def add(spec: str, project: Path, allow_network: bool = False) -> dict:
    kind, _, target = spec.partition(":")
    reg = registry()
    if kind == "pkg":
        root, ver = _locate_pkg(target, project)
        key = f"pkg:{target}@{ver}"
        relaxed = True
    elif kind == "gh":
        if not allow_network:
            raise ValueError("Indexing a GitHub repo clones it over the network: run `specforge source add gh:owner/repo`.")
        repo, _, ref = target.partition("@")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise ValueError("Use gh:owner/repo or gh:owner/repo@ref")
        root = home() / "sources" / "gh" / repo.replace("/", "__")
        if not root.exists():
            cmd = ["git", "clone", "--depth", "1"] + (["--branch", ref] if ref else []) + \
                  [f"https://github.com/{repo}", str(root)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        ver = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
        key = f"gh:{repo}@{ver}"
        relaxed = False
    elif kind == "path":
        root, ver, relaxed = Path(target).expanduser().resolve(), "local", False
        if not root.is_dir():
            raise ValueError(f"Not a folder: {root}")
        key = f"path:{root}"
    else:
        raise ValueError("Source spec must be pkg:<name>, gh:<owner>/<repo>[@ref] or path:<dir>")
    state = home() / "sources" / "state" / re.sub(r"[^\w.@-]+", "_", key)
    ix = CodeIndex(root, state=state, relaxed=relaxed)
    stats = ix.build()
    alias = f"{kind}:{target.split('@')[0]}" if kind != "path" else key
    reg[alias] = {"key": key, "root": str(root), "state": str(state), "relaxed": relaxed, "version": ver,
                  "indexed_at": time.strftime("%Y-%m-%d %H:%M"), "files": stats["files_total"],
                  "symbols": stats["symbols_total"]}
    _save(reg)
    return {"source": alias, **reg[alias], "seconds": stats["seconds"]}


def open_source(name: str) -> CodeIndex:
    reg = registry()
    e = reg.get(name) or next((v for k, v in reg.items() if k.split(":", 1)[-1] == name or v["key"] == name), None)
    if not e:
        raise ValueError(f"Unknown source '{name}'. Known: {', '.join(reg) or 'none'}. "
                         "Add one with add_source('pkg:<name>') or `specforge source add ...`.")
    return CodeIndex(e["root"], state=e["state"], relaxed=e["relaxed"])


def remove(name: str) -> bool:
    reg = registry()
    if name in reg:
        reg.pop(name)
        _save(reg)
        return True
    return False
