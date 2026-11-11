"""Find indexable files: respects .gitignore, .specforgeignore and secret rules.

Performance notes (measured on Django, 7k files):
- File listing comes from `git ls-files` (already gitignore-aware, C-speed) with an os.walk fallback.
- Built-in ignores are matched with set lookups and a single suffix tuple instead of pathspec,
  because pathspec costs ~30 µs per file per pattern set. pathspec is only used for the user's
  own .specforgeignore patterns.
- One os.stat per file; the binary sniff happens later, only for files that actually changed.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pathspec

from .config import (
    DEFAULT_IGNORES,
    IGNORE_FILE,
    MAX_FILE_BYTES,
    SECRET_GLOBS,
    debug_exc,
)

try:  # pathspec >= 0.12.2 names the git-compatible style "gitignore"
    pathspec.PathSpec.from_lines("gitignore", ["x"])
    _STYLE = "gitignore"
except Exception:  # noqa: BLE001
    debug_exc("scanner:27")
    _STYLE = "gitwildmatch"


@dataclass(frozen=True)
class Candidate:
    path: str
    size: int
    mtime_ns: int


# ---- fast built-in ignore matcher ------------------------------------------------
_IGNORED_DIRS = {p.rstrip("/") for p in DEFAULT_IGNORES if p.endswith("/") and "*" not in p}
_IGNORED_SUFFIXES = tuple(p[1:] for p in DEFAULT_IGNORES if p.startswith("*.") and "/" not in p)
_IGNORED_NAMES = {p for p in DEFAULT_IGNORES if "*" not in p and not p.endswith("/")}
_OTHER = [p for p in DEFAULT_IGNORES
          if not (p.endswith("/") and "*" not in p) and not (p.startswith("*.") and "/" not in p)
          and "*" in p]
_OTHER_RX = re.compile("|".join(fnmatch.translate(p) for p in _OTHER)) if _OTHER else None


def builtin_ignored(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if name in _IGNORED_NAMES or name.endswith(_IGNORED_SUFFIXES):
        return True
    if any(p in _IGNORED_DIRS for p in parts[:-1]):
        return True
    return bool(_OTHER_RX and _OTHER_RX.match(name))


# ---- secrets: basename patterns compiled to one regex, with ! exceptions ---------
_SECRET_ALLOW = [g[1:] for g in SECRET_GLOBS if g.startswith("!")]
_SECRET_DENY = [g for g in SECRET_GLOBS if not g.startswith("!")]
_SECRET_RX = re.compile("|".join(fnmatch.translate(g) for g in _SECRET_DENY), re.I)
_SECRET_ALLOW_RX = re.compile("|".join(fnmatch.translate(g) for g in _SECRET_ALLOW), re.I) if _SECRET_ALLOW else None


def is_secret(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if _SECRET_ALLOW_RX and _SECRET_ALLOW_RX.match(name):
        return False
    return bool(_SECRET_RX.match(name))


def _user_spec(root: Path) -> pathspec.PathSpec | None:
    p = root / IGNORE_FILE
    if not p.exists():
        return None
    lines = [ln.strip() for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines()
             if ln.strip() and not ln.startswith("#")]
    return pathspec.PathSpec.from_lines(_STYLE, lines) if lines else None


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=120, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", errors="ignore").split("\0") if p]


def _only_relaxed_dirs(rel: str) -> bool:
    """True when the only reason a path is ignored is a dist/build/lib-style folder."""
    parts = rel.split("/")
    name = parts[-1]
    if name in _IGNORED_NAMES or name.endswith(_IGNORED_SUFFIXES) or (_OTHER_RX and _OTHER_RX.match(name)):
        return False
    hits = [p for p in parts[:-1] if p in _IGNORED_DIRS]
    return bool(hits) and all(p in RELAXED_KEEP for p in hits)


def _walk_files(root: Path, relaxed: bool = False) -> list[str]:
    gi_path = root / ".gitignore"
    gi = None
    if gi_path.exists():
        gi = pathspec.PathSpec.from_lines(_STYLE, gi_path.read_text(encoding="utf-8", errors="ignore").splitlines())
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [d for d in dirnames if (d not in _IGNORED_DIRS or (relaxed and d in RELAXED_KEEP))
                       and d != "node_modules" and not (gi and gi.match_file(rel_dir + d + "/"))]
        for f in filenames:
            rel = rel_dir + f
            if not (gi and gi.match_file(rel)):
                files.append(rel)
    return files


RELAXED_KEEP = {"dist", "build", "lib", "out", "bin", "vendor"}


def iter_candidates(root: Path, relaxed: bool = False) -> list[Candidate]:
    """Every file worth considering, with one stat each. No file contents are read here."""
    root = root.resolve()
    user = _user_spec(root)
    listed = None if relaxed else _git_files(root)
    if listed is None:
        listed = _walk_files(root, relaxed)
    out: list[Candidate] = []
    base = str(root) + os.sep
    for rel in listed:
        if (builtin_ignored(rel) and not (relaxed and _only_relaxed_dirs(rel))) or is_secret(rel) \
                or (user and user.match_file(rel)):
            continue
        try:
            st = os.stat(base + rel)
        except OSError:
            continue
        if not (st.st_mode & 0o100000) or st.st_size > MAX_FILE_BYTES:  # regular files only
            continue
        out.append(Candidate(rel, st.st_size, st.st_mtime_ns))
    out.sort(key=lambda c: c.path)
    return out


def looks_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]
