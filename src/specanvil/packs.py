"""Starter packs: index a library's published source without installing it, and share prebuilt packs.

Fetch (explicit, network):  npm:<name>[@ver]   pypi:<name>[@ver]   go:<module>[@ver]   gh:<owner>/<repo>[@tag]
Catalog:                     `specanvil pack add react` resolves through assets/packs/catalog.json
Share (offline):             `specanvil pack export npm:react -o react.sapack` / `specanvil pack import react.sapack`

Archives are extracted safely (no absolute paths, no `..`, size caps) under ~/.specanvil/sources/packs and
registered as sources, so every tool accepts source="npm:react".
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import time
import urllib.request
import zipfile
from importlib import resources
from pathlib import Path

from .index import CodeIndex
from .profile import home

MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
UA = {"User-Agent": "specanvil-packs"}


def catalog() -> dict:
    return json.loads(resources.files("specanvil").joinpath("assets/packs/catalog.json").read_text(encoding="utf-8"))["packs"]


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed registry hosts
        data = r.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"Archive larger than {MAX_ARCHIVE_BYTES // 1024 // 1024} MB: {url}")
    return data


def _unsafe_name(name: str) -> bool:
    name = name.replace("\\", "/")
    return name.startswith("/") or re.match(r"^[A-Za-z]:", name) is not None or ".." in name.split("/")


def _safe_target(base: Path, name: str) -> Path | None:
    name = name.replace("\\", "/")
    if name.startswith("/") or ".." in Path(name).parts:
        return None
    t = (base / name).resolve()
    return t if str(t).startswith(str(base.resolve())) else None


def _strip(name: str, strip_first: bool | str) -> str:
    if isinstance(strip_first, str):  # explicit prefix (Go module zips: "<module>@<version>/")
        return name[len(strip_first):] if name.startswith(strip_first) else name
    return name.split("/", 1)[1] if strip_first and "/" in name else name


def _extract(data: bytes, dest: Path, strip_first: bool | str) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > MAX_FILE_BYTES or _unsafe_name(info.filename):
                    continue
                name = _strip(info.filename, strip_first)
                t = _safe_target(dest, name)
                if t:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    t.write_bytes(z.read(info))
                    n += 1
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile() or m.size > MAX_FILE_BYTES or _unsafe_name(m.name):
                    continue
                name = _strip(m.name, strip_first)
                t = _safe_target(dest, name)
                if t:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    f = tf.extractfile(m)
                    if f:
                        t.write_bytes(f.read())
                        n += 1
    return n


def _npm(name: str, ver: str | None) -> tuple[str, bytes, bool]:
    meta = json.loads(_get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}"))
    v = ver or meta["dist-tags"]["latest"]
    if v not in meta["versions"]:
        raise ValueError(f"npm:{name} has no version {v}")
    return v, _get(meta["versions"][v]["dist"]["tarball"]), True


def _pypi(name: str, ver: str | None) -> tuple[str, bytes, bool]:
    url = f"https://pypi.org/pypi/{name}/{ver}/json" if ver else f"https://pypi.org/pypi/{name}/json"
    meta = json.loads(_get(url))
    v = meta["info"]["version"]
    files = meta.get("urls") or []
    pick = next((f for f in files if f["packagetype"] == "sdist"), None) or \
        next((f for f in files if f["filename"].endswith(".whl")), None)
    if not pick:
        raise ValueError(f"pypi:{name}=={v} has no downloadable files")
    return v, _get(pick["url"]), pick["packagetype"] == "sdist"


def _go(module: str, ver: str | None) -> tuple[str, bytes, str]:
    esc = re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), module)
    v = ver or json.loads(_get(f"https://proxy.golang.org/{esc}/@latest"))["Version"]
    return v, _get(f"https://proxy.golang.org/{esc}/@v/{v}.zip"), f"{module}@{v}/"


def _register(alias: str, key: str, root: Path, version: str, relaxed: bool = True) -> dict:
    from .sources import _save, registry
    state = home() / "sources" / "state" / re.sub(r"[^\w.@-]+", "_", key)
    ix = CodeIndex(root, state=state, relaxed=relaxed)
    stats = ix.build()
    reg = registry()
    reg[alias] = {"key": key, "root": str(root), "state": str(state), "relaxed": relaxed, "version": version,
                  "indexed_at": time.strftime("%Y-%m-%d %H:%M"), "files": stats["files_total"],
                  "symbols": stats["symbols_total"], "kind": "pack"}
    _save(reg)
    return {"source": alias, **reg[alias], "seconds": stats["seconds"]}


def add(spec: str) -> dict:
    """Fetch and index a pack. spec: catalog name, or npm:/pypi:/go:/gh: with optional @version."""
    cat = catalog()
    if ":" not in spec:
        base, _, ver = spec.partition("@") if not spec.startswith("@") else (spec, "", "")
        if spec.startswith("@") and spec.count("@") == 2:
            base, ver = spec.rsplit("@", 1)
        if base not in cat:
            raise ValueError(f"'{base}' is not in the pack catalog. Use npm:/pypi:/go:/gh:, or `specanvil pack list`.")
        spec = cat[base]["source"] + (f"@{ver}" if ver else "")
    kind, _, target = spec.partition(":")
    if kind == "gh":
        from .sources import add as add_source
        return add_source(spec, Path.cwd(), allow_network=True)
    if target.startswith("@"):  # scoped npm package: @scope/name[@ver]
        name, _, ver = target[1:].partition("@")
        name = "@" + name
    else:
        name, _, ver = target.partition("@")
    fetch = {"npm": _npm, "pypi": _pypi, "go": _go}.get(kind)
    if not fetch:
        raise ValueError("Pack spec must be npm:, pypi:, go:, gh: or a catalog name")
    version, data, strip = fetch(name, ver or None)
    safe = re.sub(r"[^\w.@-]+", "_", name)
    root = home() / "sources" / "packs" / kind / f"{safe}@{version}"
    if not root.exists():
        files = _extract(data, root, strip)
        if not files:
            shutil.rmtree(root, ignore_errors=True)
            raise ValueError(f"Nothing to index in {spec}")
    return _register(f"{kind}:{name}", f"{kind}:{name}@{version}", root, version)


def export(source: str, out: Path) -> dict:
    """Write a shareable .sapack (zip: meta.json + the indexed source files). Recipients index locally on import."""
    from .sources import open_source, registry
    ix = open_source(source)
    reg = registry()
    meta: dict = next((v for k, v in reg.items() if k == source or v["key"] == source), {})
    files = [r[0] for r in ix.db.execute("SELECT path FROM files")]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.json", json.dumps({"source": source, "key": meta.get("key", source),
                                            "version": meta.get("version", ""), "files": len(files),
                                            "created": time.strftime("%Y-%m-%d %H:%M"), "format": 1}, indent=2))
        for p in files:
            fp = ix.root / p
            if fp.is_file():
                z.write(fp, "src/" + p)
    return {"pack": str(out), "files": len(files), "bytes": out.stat().st_size}


def import_pack(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("meta.json"))
        if meta.get("format") != 1:
            raise ValueError("Unsupported .sapack format")
        root = home() / "sources" / "packs" / "imported" / re.sub(r"[^\w.@-]+", "_", meta["key"])
        root.mkdir(parents=True, exist_ok=True)
        for info in z.infolist():
            if not info.filename.startswith("src/") or info.is_dir() or info.file_size > MAX_FILE_BYTES:
                continue
            t = _safe_target(root, info.filename[4:])
            if t:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(z.read(info))
    return _register(meta["source"], meta["key"], root, meta.get("version", ""))


def list_catalog() -> list[dict]:
    from .sources import registry
    reg = registry()
    out = []
    for name, c in sorted(catalog().items()):
        src = c["source"]
        out.append({"pack": name, "source": src, "ecosystem": c["ecosystem"], "about": c["about"],
                    "installed": src in reg})
    return out
