"""Find indexable files: respects .gitignore, .specforgeignore and secret rules."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pathspec

from .config import DEFAULT_IGNORES, IGNORE_FILE, MAX_FILE_BYTES, SECRET_GLOBS

try:  # pathspec >= 0.12.2 names the git-compatible style "gitignore"
    pathspec.PathSpec.from_lines("gitignore", ["x"])
    _STYLE = "gitignore"
except Exception:  # noqa: BLE001
    _STYLE = "gitwildmatch"


def _spec_from_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip() and not ln.startswith("#")]


def build_ignore(root: Path) -> pathspec.PathSpec:
    patterns = list(DEFAULT_IGNORES) + _spec_from_file(root / IGNORE_FILE)
    return pathspec.PathSpec.from_lines(_STYLE, patterns)


SECRET_SPEC = pathspec.PathSpec.from_lines(_STYLE, SECRET_GLOBS)


def is_secret(rel: str) -> bool:
    return SECRET_SPEC.match_file(rel)


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", errors="ignore").split("\0") if p]


def _walk_files(root: Path) -> list[str]:
    gi = pathspec.PathSpec.from_lines(_STYLE, _spec_from_file(root / ".gitignore"))
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [d for d in dirnames if not gi.match_file(rel_dir + d + "/")]
        for f in filenames:
            rel = rel_dir + f
            if not gi.match_file(rel):
                files.append(rel)
    return files


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\0" in fh.read(8192)
    except OSError:
        return True


def iter_files(root: Path) -> list[str]:
    """Return sorted repo-relative POSIX paths that are safe and useful to index."""
    root = root.resolve()
    ignore = build_ignore(root)
    candidates = _git_files(root)
    if candidates is None:
        candidates = _walk_files(root)
    result = []
    for rel in candidates:
        if ignore.match_file(rel) or is_secret(rel):
            continue
        p = root / rel
        try:
            if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if looks_binary(p):
            continue
        result.append(rel)
    return sorted(result)
