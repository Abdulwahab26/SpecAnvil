"""Proof: run the same Playwright tests on the code before and after a change, and compare what the app did.

The "before" run uses the last commit: the change is put in a git stash, so no container or second checkout is
needed and the installed dependencies are reused. The "after" run uses the staged change (`scope="staged"`) or the
whole working tree (`scope="all"`). Each run records, per test: its result, the API responses the test saw (from
the Playwright trace) and any text the test attached under a name starting with `proof:`.

Everything here is deterministic: no model is involved. Sorting the differences against the intent contract, the
risk rules and the evidence record are in proofrecord.py.

Safety of the stash (the user's uncommitted work is inside it while a run is in progress):
- a lock file in the git directory records the stash before anything else happens;
- the stash is restored in a `finally` block, and by `specanvil proof restore` after a hard kill;
- nothing is ever deleted: files that appeared during a run and are not ours are saved in a second stash.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from .config import load_config
from .tokens import redact

LOCK_NAME = "specanvil-proof.json"
WORK_DIR = "specanvil-proof"
STASH_PREFIX = "specanvil-proof"
ABSENT = "(absent)"

PROOF_DEFAULTS: dict[str, Any] = {
    "e2e_cmd": None,          # default: the detected E2E command, else "npx playwright test"
    "test_paths": [],         # E2E test folders/files; the changed version of these is used for both runs
    "start_cmd": None,        # only when the Playwright config has no webServer: how to start the app
    "ready_url": None,        # default: app_url
    "ready_timeout": 60,
    "timeout": 1800,          # seconds for one E2E run
    "env": {},                # extra environment for the E2E command, e.g. {"CI": "1"}
    "mask_keys": [r"created_?at", r"updated_?at", r"modified_?at", r"deleted_?at", r"timestamp", r"etag", r"nonce",
                  r"csrf_?token", r"request_?id", r"trace_?id", r"span_?id", r"correlation_?id",
                  # never stored in an evidence record:
                  r"password", r"passwd", r"secret", r"api_?key", r"access_?token", r"refresh_?token", r"id_?token",
                  r"client_?secret", r"authorization"],
    "mask_patterns": [],      # extra regexes; any matching text is replaced with <masked>
    "ignore_urls": [],        # globs matched against "METHOD /path?query"
    "probe_marker": "@probe",  # tests with this in the title or tags are adversarial probes
    "auto_max_score": 24,     # highest risk score that can still be cleared without a person
    "sensitive_words": None,  # default list in proofrecord.py
}

UUID_RX = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
TIME_RX = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
JWT_RX = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
ANSI_RX = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
MAX_BODY = 512_000
MAX_DIFFS = 400


class ProofError(Exception):
    """A proof run could not start or finish. The message says what to do next."""


def proof_config(root: Path) -> dict:
    cfg = load_config(root)
    out = dict(PROOF_DEFAULTS)
    out.update(cfg.get("proof") or {})
    if not out.get("e2e_cmd"):
        detected = ((cfg.get("profile") or {}).get("stack") or {}).get("commands", {}).get("e2e")
        out["e2e_cmd"] = detected or "npx playwright test"
    out["app_url"] = cfg.get("app_url")
    if not out.get("ready_url"):
        out["ready_url"] = cfg.get("app_url")
    return out


# ----------------------------------------------------------------------------- git

def _git(root: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    p = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if check and p.returncode:
        raise ProofError(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
    return p.stdout


def _git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode == 0


def _zlist(text: str) -> list[str]:
    return [x for x in text.split("\0") if x]


def git_dir(root: Path) -> Path:
    try:
        return Path(_git(root, "rev-parse", "--absolute-git-dir").strip())
    except (ProofError, OSError) as e:
        raise ProofError("Not a git repository. Proof compares the last commit with your change, so it needs git.") from e


def lock_path(root: Path) -> Path:
    return git_dir(root) / LOCK_NAME


def read_lock(root: Path) -> dict | None:
    p = lock_path(root)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"run_id": "unknown", "corrupt": True}


def _write_lock(root: Path, lock: dict) -> None:
    p = lock_path(root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _identity(root: Path) -> list[str]:
    """`git stash` writes commits, which need an identity; supply one only when the user has none."""
    if _git(root, "config", "user.email", check=False).strip() and _git(root, "config", "user.name", check=False).strip():
        return []
    return ["-c", "user.name=specanvil", "-c", "user.email=specanvil@localhost"]


def _find_stash(root: Path, sha: str | None = None, message: str | None = None) -> tuple[str, str] | None:
    """(ref, sha) of a stash entry, by commit id or by its message."""
    for line in _git(root, "stash", "list", "--format=%gd%x09%H%x09%gs", check=False).splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        if (sha and parts[1] == sha) or (message and not sha and parts[2].endswith(message)):
            return parts[0], parts[1]
    return None


def precheck(root: Path, scope: str) -> None:
    gd = git_dir(root)
    if scope not in ("staged", "all"):
        raise ProofError("scope must be 'staged' (the staged change) or 'all' (everything uncommitted).")
    if not _git_ok(root, "rev-parse", "--verify", "-q", "HEAD"):
        raise ProofError("This repository has no commit yet. Proof compares against the last commit: commit once first.")
    lock = read_lock(root)
    if lock:
        raise ProofError(f"A proof run ({lock.get('run_id')}) did not finish and your changes may still be in a git "
                         "stash. Run `specanvil proof restore` first.")
    for marker, what in [("MERGE_HEAD", "merge"), ("rebase-merge", "rebase"), ("rebase-apply", "rebase"),
                         ("CHERRY_PICK_HEAD", "cherry-pick"), ("BISECT_LOG", "bisect")]:
        if (gd / marker).exists():
            raise ProofError(f"A {what} is in progress. Finish or abort it before running proof.")
    if _git(root, "ls-files", "--unmerged", check=False).strip():
        raise ProofError("There are unmerged files. Resolve the conflicts before running proof.")
    if scope == "staged":
        if _git_ok(root, "diff", "--cached", "--quiet", "HEAD"):
            raise ProofError("Nothing is staged. Stage the change with `git add`, or use `--scope all` to prove "
                             "everything that is uncommitted.")
    elif not _git(root, "status", "--porcelain").strip():
        raise ProofError("There is no uncommitted change to prove.")


def changed_files(root: Path, scope: str) -> list[dict]:
    """Files in the change with lines added and removed. Read before the stash is made."""
    base = ["diff", "--no-renames", "-z"] + (["--cached"] if scope == "staged" else [])
    status: dict[str, str] = {}
    parts = _zlist(_git(root, *base, "--name-status", "HEAD"))
    for st, path in zip(parts[0::2], parts[1::2]):
        status[path] = {"A": "added", "D": "deleted", "M": "modified", "T": "modified"}.get(st[:1], "modified")
    out: dict[str, dict] = {}
    for row in _zlist(_git(root, *base, "--numstat", "HEAD")):
        cols = row.split("\t", 2)
        if len(cols) == 3:
            add, rem, path = cols
            out[path] = {"path": path, "status": status.get(path, "modified"),
                         "added": int(add) if add.isdigit() else 0, "removed": int(rem) if rem.isdigit() else 0}
    if scope == "all":
        for path in _zlist(_git(root, "ls-files", "--others", "--exclude-standard", "-z")):
            try:
                data = (root / path).read_bytes()[:2_000_000]
                lines = 0 if b"\0" in data[:8000] else data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
            except OSError:
                lines = 0
            out[path] = {"path": path, "status": "added", "added": lines, "removed": 0}
    records = _records_prefix(root)
    return sorted((f for f in out.values() if not (records and f["path"].startswith(records))), key=lambda f: f["path"])


def _records_prefix(root: Path) -> str | None:
    """Evidence records are written by proof itself; they are not part of the change being proven."""
    from .proofrecord import records_dir
    try:
        return records_dir(root).resolve().relative_to(root.resolve()).as_posix() + "/"
    except ValueError:
        return None


def settings_changed(root: Path) -> bool:
    """True when the change edits the settings that decide how it is checked (masks, thresholds, the E2E command).
    A change must not be able to loosen its own check unnoticed."""
    from .config import CONFIG_FILE, state_dir
    try:
        rel = (state_dir(root) / CONFIG_FILE).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False

    def pick(text: str) -> dict:
        try:
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            return {"unreadable": True}
        return {k: data.get(k) for k in ("proof", "allowed_test_commands", "app_url")}
    committed = _git(root, "show", f"HEAD:{rel}", check=False)
    current = (root / rel).read_text(encoding="utf-8") if (root / rel).is_file() else ""
    return pick(committed) != pick(current)


def candidate_tree(root: Path, scope: str) -> str:
    """The git tree id of exactly what the "after" run tests. The real index is not touched."""
    if scope == "staged":
        return _git(root, "write-tree").strip()
    gd = git_dir(root)
    fd, tmp = tempfile.mkstemp(prefix="specanvil-index-", dir=gd)
    os.close(fd)
    try:
        if (gd / "index").is_file():
            shutil.copyfile(gd / "index", tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp)
        _git(root, "add", "-A", env=env)
        return _git(root, "write-tree", env=env).strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _overlay_tests(root: Path, stash: str, scope: str, paths: list[str]) -> list[str]:
    """Put the changed version of the E2E tests on top of the old code, so both runs execute the same tests.
    Returns the files touched."""
    trees = [f"{stash}^2"] if scope == "staged" else [stash]
    if scope == "all" and _git_ok(root, "rev-parse", "--verify", "-q", f"{stash}^3"):
        trees.append(f"{stash}^3")  # untracked files live in the stash's third parent
    touched: set[str] = set()
    for path in paths:
        path = path.strip().rstrip("/")
        if not path:
            continue
        head = set(_zlist(_git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", path, check=False)))
        cand: set[str] = set()
        for tree in trees:
            names = set(_zlist(_git(root, "ls-tree", "-r", "--name-only", "-z", tree, "--", path, check=False)))
            if names:
                _git(root, "checkout", tree, "--", path)
                cand |= names
        for gone in head - cand:  # a test removed by the change is not run against the old code either
            (root / gone).unlink(missing_ok=True)
        touched |= head | cand
    return sorted(touched)


def _unexpected(root: Path, lock: dict) -> list[str]:
    """Files that differ from what this run itself put in the working tree: created or edited by the tests, or by
    the user while the run was going. They must be kept, not discarded."""
    expected = set(lock.get("overlay") or [])
    if lock.get("phase") == "candidate" and lock.get("tree"):
        tracked = set(_zlist(_git(root, "diff", "--name-only", "-z", lock["tree"], check=False)))
        tracked |= set(_zlist(_git(root, "diff", "--cached", "--name-only", "-z", lock["tree"], check=False)))
    else:
        tracked = set(_zlist(_git(root, "diff", "--name-only", "-z", "HEAD", check=False))) - expected
    untracked = set(_zlist(_git(root, "ls-files", "--others", "--exclude-standard", "-z", check=False)))
    return sorted(tracked | untracked)


def restore(root: Path, lock: dict | None = None, clean: bool = True) -> list[str]:
    """Put the working tree back as it was before the run. Safe to call again after a failure. Returns notes."""
    lock = lock or read_lock(root)
    if not lock:
        return ["No unfinished proof run: nothing to restore."]
    notes: list[str] = []
    run_id = lock.get("run_id", "unknown")
    found = _find_stash(root, sha=lock.get("stash")) if lock.get("stash") else \
        _find_stash(root, message=f"{STASH_PREFIX} {run_id}")
    if not found:
        lock_path(root).unlink(missing_ok=True)
        if clean:
            _cleanup_work(root, run_id)
        return [f"Proof run {run_id} left no stash behind: your working tree was not changed. Lock removed."]
    head = _git(root, "rev-parse", "HEAD").strip()
    if lock.get("head") and head != lock["head"]:
        raise ProofError(f"HEAD moved during the proof run (was {lock['head'][:10]}, now {head[:10]}). Your changes "
                         f"are safe in stash {found[1][:10]} ('{STASH_PREFIX} {run_id}'). Check out {lock['head'][:10]} "
                         "and run `specanvil proof restore`, or apply it yourself with "
                         f"`git stash apply --index {found[1]}`.")
    ident = _identity(root)
    extra = _unexpected(root, lock)
    if extra:
        _git(root, *ident, "stash", "push", "--include-untracked", "-m", f"{STASH_PREFIX} {run_id} leftovers")
        shown = ", ".join(extra[:5]) + (f" and {len(extra) - 5} more" if len(extra) > 5 else "")
        notes.append(f"Files that changed during the run were kept in a separate stash ('{STASH_PREFIX} {run_id} "
                     f"leftovers'): {shown}. If they are test output, add them to .gitignore and drop that stash.")
    _git(root, "reset", "--hard", "-q", "HEAD")  # nothing is lost: everything is in one of the two stashes
    found = _find_stash(root, sha=found[1])
    if not found:
        raise ProofError("The proof stash disappeared while restoring. Look at `git stash list` and `git fsck --lost-found`.")
    p = subprocess.run(["git", *ident, "stash", "pop", "--index", "-q", found[0]], cwd=root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise ProofError("Could not put your changes back automatically. They are safe in the stash "
                         f"'{STASH_PREFIX} {run_id}' ({found[1][:10]}). Fix the problem below, then run "
                         f"`specanvil proof restore`.\n{(p.stderr or p.stdout).strip()}")
    lock_path(root).unlink(missing_ok=True)
    if clean:
        _cleanup_work(root, run_id)
    notes.append("Working tree restored.")
    return notes


def _cleanup_work(root: Path, run_id: str) -> None:
    shutil.rmtree(git_dir(root) / WORK_DIR / run_id, ignore_errors=True)


# ----------------------------------------------------------------------------- running the tests

def _port_open(url: str | None) -> bool:
    if not url:
        return False
    u = urlsplit(url)
    if not u.hostname:
        return False
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=0.5):
            return True
    except OSError:
        return False


def _kill(proc: subprocess.Popen) -> None:
    """Stop a process and what it started. An interrupt comes first: Playwright answers it by stopping the web
    server it launched, which a plain kill would leave running on the app's port."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            import signal
            for sig, wait in ((signal.SIGINT, 10), (signal.SIGTERM, 5), (signal.SIGKILL, 5)):
                os.killpg(proc.pid, sig)
                try:
                    proc.wait(timeout=wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _popen(argv: list[str], root: Path, env: dict, out) -> subprocess.Popen:
    kw: dict[str, Any] = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" \
        else {"start_new_session": True}
    return subprocess.Popen(argv, cwd=root, env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)


def _start_app(root: Path, pcfg: dict, log_file: Path) -> subprocess.Popen | None:
    cmd = pcfg.get("start_cmd")
    if not cmd:
        return None
    import shlex

    from .testing import SHELL_META_RX
    if SHELL_META_RX.search(cmd):
        raise ProofError("proof.start_cmd may not contain shell operators: it is run directly, not through a shell.")
    argv = shlex.split(cmd, posix=os.name != "nt")
    resolved = shutil.which(argv[0], path=os.pathsep.join([str(root), os.environ.get("PATH", "")]))
    argv[0] = resolved or argv[0]
    out = open(log_file, "wb")
    try:
        proc = _popen(argv, root, dict(os.environ, **{k: str(v) for k, v in (pcfg.get("env") or {}).items()}), out)
    except OSError as e:
        out.close()
        raise ProofError(f"Could not start the app with proof.start_cmd ({cmd}): {e}") from e
    deadline = time.time() + float(pcfg.get("ready_timeout") or 60)
    while time.time() < deadline:
        if proc.poll() is not None:
            out.close()
            raise ProofError(f"The app exited right after starting (proof.start_cmd: {cmd}). Log: {log_file}")
        if _port_open(pcfg.get("ready_url")):
            return proc
        time.sleep(0.3)
    _kill(proc)
    out.close()
    raise ProofError(f"The app did not answer at {pcfg.get('ready_url')} within {pcfg.get('ready_timeout')}s.")


def e2e_argv(root: Path, pcfg: dict, out_dir: Path, grep: str | None) -> tuple[list[str], dict]:
    """The E2E command with tracing and the JSON reporter switched on. Never run through a shell."""
    from .testing import safe_test_argv
    try:
        argv, env = safe_test_argv(pcfg["e2e_cmd"], root)
    except ValueError as e:
        raise ProofError(f"proof.e2e_cmd is refused: {e}") from e
    exe = re.sub(r"\.(exe|cmd|bat|sh|ps1)$", "", Path(argv[0]).name.lower())
    if exe in ("npm", "pnpm") and "--" not in argv and not any("playwright" in a.lower() for a in argv[1:]):
        argv.append("--")  # `npm run e2e` passes options to the script only after `--`
    argv += ["--trace", "on", "--reporter=json", "--output", str(out_dir / "results")]
    if grep:
        argv += ["--grep", grep]
    report = str(out_dir / "report.json")
    env = dict(env, PLAYWRIGHT_JSON_OUTPUT_FILE=report, PLAYWRIGHT_JSON_OUTPUT_NAME=report, NO_COLOR="1", FORCE_COLOR="0")
    return argv, env


def run_e2e(root: Path, pcfg: dict, out_dir: Path, label: str, grep: str | None = None) -> dict:
    """One E2E run. Returns the capture (see load_capture) plus exit code and duration."""
    out_dir.mkdir(parents=True, exist_ok=True)
    argv, env = e2e_argv(root, pcfg, out_dir, grep)
    full_env = dict(os.environ, **{k: str(v) for k, v in (pcfg.get("env") or {}).items()}, **env, SPECANVIL_PROOF=label)
    app = _start_app(root, pcfg, out_dir / "app.log")
    t0 = time.time()
    log_file = out_dir / "e2e.log"
    exit_code: int | str
    try:
        with open(log_file, "wb") as out:
            try:
                proc = _popen(argv, root, full_env, out)
            except OSError as e:
                raise ProofError(f"Could not run the E2E command ({pcfg['e2e_cmd']}): {e}") from e
            try:
                exit_code = proc.wait(timeout=float(pcfg.get("timeout") or 1800))
            except subprocess.TimeoutExpired:
                exit_code = "timeout"
            finally:
                _kill(proc)
    finally:
        if app:
            _kill(app)
    capture = load_capture(out_dir, pcfg)
    capture.update(exit=exit_code, seconds=round(time.time() - t0, 1), label=label)
    if not capture["report"]:
        tail = log_file.read_text(encoding="utf-8", errors="replace")[-1500:] if log_file.is_file() else ""
        capture["log_tail"] = ANSI_RX.sub("", tail)
    return capture


# ----------------------------------------------------------------------------- reading what a run did

class Masker:
    def __init__(self, pcfg: dict, noise: set | None = None):
        self.key_rx = re.compile("^(?:" + "|".join(pcfg.get("mask_keys") or ["$^"]) + ")$", re.I)
        self.extra = [re.compile(p) for p in pcfg.get("mask_patterns") or []]
        self.ignore = pcfg.get("ignore_urls") or []
        self.hosts = set(LOCAL_HOSTS)
        if pcfg.get("app_url"):
            self.hosts.add(urlsplit(pcfg["app_url"]).hostname or "")
        self.masked = 0

    def text(self, s: str) -> str:
        new = JWT_RX.sub("<jwt>", TIME_RX.sub("<time>", UUID_RX.sub("<uuid>", s)))
        for rx in self.extra:
            new = rx.sub("<masked>", new)
        if new != s:
            self.masked += 1
        return redact(new)  # keys, tokens and passwords never reach an evidence record

    def value(self, v: Any) -> Any:
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if self.key_rx.match(str(k)):
                    out[k] = "<masked>"
                    self.masked += 1
                else:
                    out[k] = self.value(x)
            return out
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, str):
            return self.text(v)
        return v

    def url(self, url: str) -> str:
        u = urlsplit(url)
        query = "&".join(f"{k}={self.text(v)}" for k, v in sorted(parse_qsl(u.query, keep_blank_values=True)))
        host = "" if (u.hostname or "") in self.hosts else f"{u.scheme}://{u.netloc}"
        return host + self.text(u.path or "/") + ("?" + query if query else "")

    def ignored(self, endpoint: str) -> bool:
        return any(fnmatch.fnmatchcase(endpoint, pat) for pat in self.ignore)


def _zip_text(z: zipfile.ZipFile, ref: dict) -> str | None:
    """The body a trace entry points at. Playwright has named the pointer `_file`, `_sha1` and inline `text`."""
    if isinstance(ref.get("text"), str):
        return ref["text"][:MAX_BODY]
    name = ref.get("_file") or (f"resources/{ref['_sha1']}" if ref.get("_sha1") else None)
    if not name:
        return None
    for cand in (name, name if name.startswith("resources/") else f"resources/{name}"):
        try:
            with z.open(cand) as f:
                return f.read(MAX_BODY).decode("utf-8", errors="replace")
        except KeyError:
            continue
    return None


def _body(text: str | None, mime: str, m: Masker) -> Any:
    if text is None:
        return None
    if "json" in mime or text[:1] in "{[":
        try:
            return m.value(json.loads(text))
        except (json.JSONDecodeError, ValueError):
            pass
    return m.text(text[:4000])


def trace_exchanges(trace_zip: Path, m: Masker) -> list[dict]:
    """API calls recorded in one test's Playwright trace: browser fetch/XHR and calls made with the `request` fixture.
    Pages, scripts, styles, images and fonts are left out."""
    out: list[dict] = []
    try:
        z = zipfile.ZipFile(trace_zip)
    except (OSError, zipfile.BadZipFile):
        return out
    with z:
        for name in sorted(n for n in z.namelist() if n.endswith(".network")):
            for line in z.read(name).decode("utf-8", errors="replace").splitlines():
                try:
                    snap = json.loads(line).get("snapshot") or {}
                except json.JSONDecodeError:
                    continue
                req, resp = snap.get("request") or {}, snap.get("response") or {}
                url = req.get("url") or ""
                if not url.startswith(("http://", "https://")):
                    continue
                content = resp.get("content") or {}
                mime = (content.get("mimeType") or "").lower()
                kind = snap.get("_resourceType")
                if not ("json" in mime or kind in ("fetch", "xhr") or (kind is None and not mime.startswith(
                        ("text/html", "text/css", "image/", "font/", "application/javascript", "text/javascript")))):
                    continue
                endpoint = f"{req.get('method', 'GET')} {m.url(url)}"
                if m.ignored(endpoint):
                    continue
                post = req.get("postData") or {}
                out.append({"endpoint": endpoint, "status": resp.get("status"),
                            "request": _body(_zip_text(z, post), (post.get("mimeType") or "").lower(), m) if post else None,
                            "body": _body(_zip_text(z, content), mime, m)})
    return out


def load_capture(out_dir: Path, pcfg: dict) -> dict:
    """Read a run's Playwright JSON report and traces into a plain structure:
    {"report": bool, "tests": {key: {status, file, line, probe, error, exchanges[], snapshots{}}}, "masked": n}."""
    m = Masker(pcfg)
    data = None
    for cand in (out_dir / "report.json", out_dir / "e2e.log"):
        if cand.is_file():
            try:
                text = cand.read_text(encoding="utf-8", errors="replace")
                data = json.loads(text[text.index("{"):]) if "{" in text else None
            except (json.JSONDecodeError, ValueError):
                data = None
            if isinstance(data, dict) and "suites" in data:
                break
            data = None
    tests: dict[str, dict] = {}
    marker = (pcfg.get("probe_marker") or "@probe").lower()

    def walk(suite: dict, titles: list[str]) -> None:
        for spec in suite.get("specs") or []:
            for t in spec.get("tests") or []:
                results = t.get("results") or []
                last = results[-1] if results else {}
                status = {"expected": "passed", "unexpected": "failed", "flaky": "flaky",
                          "skipped": "skipped"}.get(t.get("status", ""), last.get("status") or "unknown")
                if status == "passed" and last.get("status") == "skipped":
                    status = "skipped"
                file = (spec.get("file") or suite.get("file") or "").replace("\\", "/")
                key = " › ".join([file, *titles, spec.get("title", "")])
                if t.get("projectName"):
                    key += f" [{t['projectName']}]"
                tags = [str(x).lower() for x in spec.get("tags") or []]
                exchanges: list[dict] = []
                snapshots: dict[str, str] = {}
                for att in last.get("attachments") or []:
                    name = att.get("name") or ""
                    if name == "trace" and att.get("path"):
                        exchanges += trace_exchanges(Path(att["path"]), m)
                    elif name.startswith("proof:"):
                        raw = None
                        if att.get("body"):
                            try:
                                raw = base64.b64decode(att["body"]).decode("utf-8", errors="replace")
                            except (ValueError, TypeError):
                                raw = None
                        elif att.get("path") and Path(att["path"]).is_file():
                            raw = Path(att["path"]).read_text(encoding="utf-8", errors="replace")
                        if raw is not None:
                            snapshots[name[6:]] = m.text(raw[:20000])
                err = ANSI_RX.sub("", ((last.get("error") or {}).get("message") or ""))[:600]
                tests[key] = {"status": status, "file": file, "line": spec.get("line"),
                              "probe": marker in key.lower() or marker.lstrip("@") in tags or ".probe." in file,
                              "error": err, "exchanges": exchanges, "snapshots": snapshots}
        for child in suite.get("suites") or []:
            walk(child, titles + ([child.get("title", "")] if child.get("title") and not child.get("file") == child.get("title") else []))

    if data:
        for top in data.get("suites") or []:
            walk(top, [])
    return {"report": data is not None, "tests": tests, "masked": m.masked}


# ----------------------------------------------------------------------------- comparing two runs

def json_diff(a: Any, b: Any, path: str = "$", out: list | None = None, cap: int = 40) -> list[tuple[str, Any, Any]]:
    out = [] if out is None else out
    if len(out) >= cap:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append((f"{path}.{k}", ABSENT, b[k]))
            elif k not in b:
                out.append((f"{path}.{k}", a[k], ABSENT))
            else:
                json_diff(a[k], b[k], f"{path}.{k}", out, cap)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((f"{path}.length", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff(x, y, f"{path}[{i}]", out, cap)
    elif a != b or type(a) is not type(b):
        out.append((path, a, b))
    return out


def _short(v: Any, n: int = 240) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + "…"


def _text_diff(a: str, b: str, n: int = 12) -> tuple[str, str]:
    la, lb = a.splitlines(), b.splitlines()
    gone = [x for x in la if x not in lb][:n]
    new = [x for x in lb if x not in la][:n]
    return "\n".join(gone) or "(reordered)", "\n".join(new) or "(reordered)"


def diff_captures(a: dict, b: dict, noise: set | None = None) -> dict:
    """Differences between the "before" capture `a` and the "after" capture `b`.
    `noise` holds (test, kind, endpoint, path) keys that differ between two runs of the same code; they are dropped.
    Returns {"diffs": [...], "counts": {...}, "noise_dropped": n}."""
    noise = noise or set()
    diffs: list[dict] = []
    dropped = 0
    counts = {"tests": 0, "exchanges": 0, "snapshots": 0, "tests_added": 0, "tests_removed": 0}

    def add(test: str, kind: str, endpoint: str | None, path: str | None, before: Any, after: Any) -> None:
        nonlocal dropped
        if (test, kind, endpoint, path) in noise:
            dropped += 1
            return
        if len(diffs) < MAX_DIFFS:
            diffs.append({"id": f"D{len(diffs) + 1}", "test": test, "kind": kind, "endpoint": endpoint, "path": path,
                          "before": _short(before), "after": _short(after)})

    ta, tb = a.get("tests") or {}, b.get("tests") or {}
    for key in sorted(set(ta) | set(tb)):
        x, y = ta.get(key), tb.get(key)
        if x is None or y is None:
            counts["tests_added" if x is None else "tests_removed"] += 1
            add(key, "test_added" if x is None else "test_removed", None, None,
                ABSENT if x is None else x["status"], ABSENT if y is None else y["status"])
            continue
        counts["tests"] += 1
        if x["status"] != y["status"]:
            add(key, "status", None, None, x["status"], y["status"])
        groups: dict[str, tuple[list, list]] = {}
        for side, t in ((0, x), (1, y)):
            for ex in t["exchanges"]:
                groups.setdefault(ex["endpoint"], ([], []))[side].append(ex)
        for endpoint in sorted(groups):
            la, lb = groups[endpoint]
            if not la or not lb:
                add(key, "call_added" if not la else "call_removed", endpoint, None, len(la), len(lb))
                continue
            if len(la) != len(lb):
                add(key, "call_count", endpoint, None, len(la), len(lb))
            for ea, eb in zip(la, lb):
                counts["exchanges"] += 1
                if ea["status"] != eb["status"]:
                    add(key, "response_status", endpoint, None, ea["status"], eb["status"])
                for p, before, after in json_diff(ea.get("request"), eb.get("request")):
                    add(key, "request_body", endpoint, p, before, after)
                for p, before, after in json_diff(ea["body"], eb["body"]):
                    add(key, "response_body", endpoint, p, before, after)
        for name in sorted(set(x["snapshots"]) | set(y["snapshots"])):
            sa, sb = x["snapshots"].get(name), y["snapshots"].get(name)
            if sa is not None and sb is not None:
                counts["snapshots"] += 1
            if sa != sb:
                before, after = _text_diff(sa or "", sb or "") if sa is not None and sb is not None else \
                    (ABSENT if sa is None else "(present)", ABSENT if sb is None else "(present)")
                add(key, "snapshot", None, name, before, after)
    return {"diffs": diffs, "counts": counts, "noise_dropped": dropped}


def noise_keys(a1: dict, a2: dict) -> set:
    """Differences between two runs of the same code: values that are not deterministic and must not be reported."""
    return {(d["test"], d["kind"], d["endpoint"], d["path"]) for d in diff_captures(a1, a2)["diffs"]}


def run_summary(c: dict) -> dict:
    tests = c.get("tests") or {}
    by = {s: sum(1 for t in tests.values() if t["status"] == s) for s in ("passed", "failed", "flaky", "skipped")}
    return {"exit": c.get("exit"), "seconds": c.get("seconds"), "report": c.get("report", False), "tests": len(tests),
            **by, "api_calls": sum(len(t["exchanges"]) for t in tests.values()),
            "snapshots": sum(len(t["snapshots"]) for t in tests.values()),
            **({"log_tail": c["log_tail"]} if c.get("log_tail") else {})}


# ----------------------------------------------------------------------------- the whole run

def run(root: Path, ticket: str = "", scope: str = "staged", intent_file: str | None = None, author_model: str = "",
        author_tool: str = "", grep: str | None = None, calibrate: bool = False, allow_running: bool = False,
        log: Callable[[str], None] = lambda s: None) -> dict:
    """Prove the uncommitted change: stash, test the old code, restore, test the new code, compare, score, record.
    Returns the evidence record that was written."""
    from . import proofrecord as R
    root = root.resolve()
    pcfg = proof_config(root)
    precheck(root, scope)
    if _port_open(pcfg.get("ready_url")) and not allow_running:
        raise ProofError(f"Something is already listening at {pcfg['ready_url']}. Both runs could end up testing "
                         "that running app instead of the old and the new code. Stop it, or pass --allow-running if "
                         "it is unrelated.")
    files = changed_files(root, scope)
    pcfg["settings_changed"] = settings_changed(root)
    tree = candidate_tree(root, scope)
    intent, intent_src = R.load_intent(root, ticket, intent_file)  # read now: the file may be uncommitted
    head = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    run_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + tree[:6]
    work = git_dir(root) / WORK_DIR / run_id
    lock = {"run_id": run_id, "scope": scope, "head": head, "tree": tree, "phase": "stashing", "started": time.time()}
    _write_lock(root, lock)
    notes: list[str] = []
    before: dict = {}
    after: dict = {}
    noise: set = set()
    try:
        message = f"{STASH_PREFIX} {run_id}"
        _git(root, *_identity(root), "stash", "push", "--include-untracked", "-m", message)
        found = _find_stash(root, message=message)
        if not found:
            raise ProofError("git stash did not save anything; nothing was changed.")
        lock.update(stash=found[1], phase="baseline")
        _write_lock(root, lock)
        lock["overlay"] = _overlay_tests(root, found[1], scope, pcfg.get("test_paths") or [])
        _write_lock(root, lock)
        log(f"before: running the E2E tests on the last commit ({head[:8]})")
        before = run_e2e(root, pcfg, work / "before", "baseline", grep)
        if calibrate:
            log("before: second run to find values that change on their own")
            noise = noise_keys(before, run_e2e(root, pcfg, work / "before2", "baseline", grep))
        if scope == "all":
            notes += restore(root, lock, clean=False)
        else:
            _git(root, "read-tree", "--reset", "-u", f"{found[1]}^2")
            lock["phase"] = "candidate"
            _write_lock(root, lock)
        log("after: running the same tests on the change")
        after = run_e2e(root, pcfg, work / "after", "candidate", grep)
    finally:
        try:
            if read_lock(root):
                notes += restore(root, lock, clean=False)
        finally:
            if not read_lock(root):
                _cleanup_work(root, run_id)
    comparison = diff_captures(before, after, noise)
    record = R.build_record(root, pcfg, run_id=run_id, ticket=ticket, scope=scope, head=head, branch=branch, tree=tree,
                            files=files, intent=intent, intent_src=intent_src, author_model=author_model,
                            author_tool=author_tool, before=before, after=after, comparison=comparison,
                            calibrated=calibrate, notes=[n for n in notes if n != "Working tree restored."])
    return R.append_record(root, record)
