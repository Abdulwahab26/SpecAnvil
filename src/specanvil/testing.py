"""Unit-test planning support: test surface analysis, coverage parsing, milestone schema + enforcement.

Split of responsibilities (cost-aware model routing):
- This module does the cheap, deterministic work locally (complexity, side effects, existing tests,
  real coverage) so the expensive planner model spends tokens on judgement, not on reading files.
- The planner model writes milestones.json; `validate_plan` checks it with code, not with a prompt.
- The executor model may only mark a milestone done through `update_milestone`, which runs the
  milestone's tests itself and refuses "done" if they fail.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .index import GENERATED_RX, TEST_RX, CodeIndex

BRANCH_RX = re.compile(r"\b(if|elif|else if|case|catch|except|for|foreach|while|when|guard)\b|&&|\|\||\?\?|\?(?=[^:]*:)")
SIDE_EFFECTS = {
    "network": r"\b(fetch|axios|requests\.|httpx|urllib|HttpClient|WebClient|RestTemplate|http\.(get|post)|grpc)\b",
    "database": r"\b(session\.|query\(|execute\(|cursor|repository|Repository|\.objects\.|prisma\.|knex|mongoose|"
                r"DbContext|EntityManager|findBy|\.save\(|\.find\(|SELECT\s|INSERT\s|UPDATE\s)",
    "filesystem": r"\b(open\(|fs\.|readFile|writeFile|Path\(|File\.|os\.path|shutil|Files\.)",
    "time": r"\b(datetime\.now|date\.today|time\.time|Date\.now|new Date\(|LocalDateTime\.now|DateTime\.Now|"
            r"Instant\.now|time\.Now|setTimeout|setInterval|sleep\()",
    "randomness": r"\b(random\.|uuid[14]\(|uuid\.uuid[14]|Math\.random|randint|secrets\.|Guid\.NewGuid|"
                  r"UUID\.randomUUID|crypto\.random|randomUUID|nanoid)",
    "environment": r"\b(os\.environ|process\.env|getenv|System\.getenv|Environment\.GetEnvironmentVariable|"
                   r"settings\.|config\.)",
    "subprocess": r"(\bsubprocess\.|child_process|(?<![.\w])exec\(|os\.system|(?<![.\w])spawn\(|"
                  r"Process\.Start|Runtime\.getRuntime)",
    "concurrency": r"\b(async |await |Thread|Executor|Promise\.all|asyncio|goroutine|go func|Task\.Run|lock\()",
    "global_state": r"\b(global |static [A-Za-z<>]+ [a-z]\w* =|singleton|getInstance\(|window\.|globalThis)",
    "external_sdk": r"\b(boto3|stripe|twilio|sendgrid|firebase|s3\.|sqs|kafka|redis)\b",
}
SIDE_EFFECT_RX = {k: re.compile(v) for k, v in SIDE_EFFECTS.items()}
MOCK_LIBS = {
    "unittest.mock": r"unittest\.mock|from mock import|mocker\.", "pytest fixtures": r"@pytest\.fixture",
    "jest.mock": r"jest\.(mock|fn|spyOn)", "vitest vi.mock": r"\bvi\.(mock|fn|spyOn)", "sinon": r"sinon\.",
    "Mockito": r"Mockito|@Mock\b|@MockBean", "Moq": r"new Mock<|Mock\.Of<", "NSubstitute": r"Substitute\.For",
    "gomock/testify": r"gomock|testify/mock", "msw": r"\bmsw\b|setupServer\(",
}
COVERAGE_FILES = ["coverage/lcov.info", "lcov.info", "coverage.xml", "cobertura.xml", "coverage/cobertura-coverage.xml",
                  "target/site/jacoco/jacoco.xml", "build/reports/jacoco/test/jacocoTestReport.xml"]
STATUSES = ("todo", "in_progress", "done", "blocked")


# ----------------------------------------------------------------------------- coverage

def load_coverage(root: Path) -> tuple[dict[str, dict[int, int]], str | None]:
    """file -> {line: hits} from lcov, Cobertura (coverage.py, coverlet, istanbul) or JaCoCo."""
    found = [root / c for c in COVERAGE_FILES if (root / c).exists()]
    found += [p for p in root.glob("**/coverage.cobertura.xml") if "node_modules" not in p.parts][:3]
    for f in found:
        try:
            if f.suffix == ".info":
                return _lcov(f, root), str(f.relative_to(root))
            text = f.read_text(encoding="utf-8", errors="ignore")
            if "<report" in text[:2000] and "jacoco" in text[:2000].lower():
                return _jacoco(f), str(f.relative_to(root))
            return _cobertura(f, root), str(f.relative_to(root))
        except (OSError, ET.ParseError, ValueError):
            continue
    return {}, None


def _norm(path: str, root: Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix().lstrip("./")


def _lcov(f: Path, root: Path) -> dict:
    cov: dict[str, dict[int, int]] = {}
    cur = None
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("SF:"):
            cur = cov.setdefault(_norm(line[3:], root), {})
        elif line.startswith("DA:") and cur is not None:
            ln, hits = line[3:].split(",")[:2]
            cur[int(ln)] = int(float(hits))
    return cov


def _cobertura(f: Path, root: Path) -> dict:
    tree = ET.parse(f)
    sources = [s.text or "" for s in tree.iter("source")]
    cov: dict[str, dict[int, int]] = {}
    for cls in tree.iter("class"):
        fn = cls.get("filename") or ""
        candidates = [fn] + [str(Path(s) / fn) for s in sources if s]
        key = next((_norm(c, root) for c in candidates if (root / _norm(c, root)).exists()), _norm(fn, root))
        lines = cov.setdefault(key, {})
        for ln in cls.iter("line"):
            lines[int(ln.get("number", 0))] = int(ln.get("hits", 0))
    return cov


def _jacoco(f: Path) -> dict:
    tree = ET.parse(f)
    cov: dict[str, dict[int, int]] = {}
    for pkg in tree.iter("package"):
        for sf in pkg.iter("sourcefile"):
            name = f"{pkg.get('name')}/{sf.get('name')}"
            cov[name] = {int(l.get("nr")): int(l.get("ci", 0)) for l in sf.iter("line")}
    return cov


def _coverage_for(cov: dict, path: str, start: int, end: int) -> float | None:
    lines = cov.get(path)
    if lines is None:  # JaCoCo keys are package paths: match by suffix
        lines = next((v for k, v in cov.items() if path.endswith(k)), None)
    if not lines:
        return None
    relevant = [h for ln, h in lines.items() if start <= ln <= end]
    if not relevant:
        return None
    return round(100 * sum(1 for h in relevant if h > 0) / len(relevant), 1)


# ----------------------------------------------------------------------------- surface

def _resolve_files(ix: CodeIndex, target: str) -> list[str]:
    t = target.strip().rstrip("/")
    by_path = [r[0] for r in ix.db.execute(
        "SELECT path FROM files WHERE parser!='text' AND (path=? OR path LIKE ?) ORDER BY path", (t, t + "/%"))]
    files = sorted(set(by_path) | set(ix.module_files(t)))
    return [f for f in files if not TEST_RX.search(f) and not GENERATED_RX.search(f)]


def test_surface(ix: CodeIndex, target: str, limit: int = 60) -> dict:
    files = _resolve_files(ix, target)
    if not files:
        raise ValueError(f"No source files for '{target}'. Pass a module name from repo_map, a folder, or a file path.")
    test_files = [r[0] for r in ix.db.execute("SELECT path FROM files WHERE parser!='text'") if TEST_RX.search(r[0])]
    test_text = "\n".join(_read(ix.root / p) for p in test_files)
    cov, cov_src = load_coverage(ix.root)
    mocks = [name for name, rx in MOCK_LIBS.items() if re.search(rx, test_text)]

    q = ",".join("?" * len(files))
    rows = ix.db.execute(f"SELECT * FROM symbols WHERE path IN ({q}) AND kind IN ('function','method') "
                         "ORDER BY path, start_line", files).fetchall()
    file_cache: dict[str, list[str]] = {}
    items = []
    for r in rows:
        lines = file_cache.setdefault(r["path"], _read(ix.root / r["path"]).splitlines())
        body = "\n".join(lines[r["start_line"] - 1:r["end_line"]])
        n_lines = r["end_line"] - r["start_line"] + 1
        if n_lines < 2 and not BRANCH_RX.search(body):
            continue  # trivial one-liners rarely deserve their own tests
        branches = len(BRANCH_RX.findall(body))
        effects = [k for k, rx in SIDE_EFFECT_RX.items() if rx.search(body)]
        refs = len(re.findall(r"\b" + re.escape(r["name"]) + r"\b", test_text)) if len(r["name"]) > 2 else 0
        coverage = _coverage_for(cov, r["path"], r["start_line"], r["end_line"])
        gap = (1 - coverage / 100) if coverage is not None else (0.35 if refs else 1.0)
        risk = 1 + 0.3 * len([e for e in effects if e not in ("concurrency",)])
        priority = round((branches + 1) * gap * risk * (1 + min(n_lines, 80) / 80), 2)
        items.append({"id": r["id"], "ref": f"{r['path']}::{r['qualname']}", "lines": n_lines, "branches": branches,
                      "side_effects": effects, "test_refs": refs, "coverage_pct": coverage, "priority": priority,
                      "signature": r["signature"]})
    items.sort(key=lambda x: -x["priority"])
    effects_total: dict[str, int] = {}
    for it in items:
        for e in it["side_effects"]:
            effects_total[e] = effects_total.get(e, 0) + 1
    covered = [i["coverage_pct"] for i in items if i["coverage_pct"] is not None]
    return {
        "target": target, "source_files": len(files), "functions": len(items),
        "untested_functions": sum(1 for i in items if i["test_refs"] == 0 and not i["coverage_pct"]),
        "coverage_source": cov_src or "none found (run tests with coverage for exact numbers)",
        "avg_coverage_pct": round(sum(covered) / len(covered), 1) if covered else None,
        "side_effects_in_module": effects_total, "mocking_in_repo": mocks,
        "existing_test_files": [p for p in test_files if any(Path(f).stem.split(".")[0].lower() in p.lower()
                                                             for f in files)][:10],
        "functions_by_priority": items[:limit],
        "note": "test_refs counts name mentions in test files; code tested only through HTTP/UI shows 0. "
                "A coverage report (lcov/cobertura/jacoco) gives exact numbers and overrides this heuristic.",
    }


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ----------------------------------------------------------------------------- milestones

def plan_dir(root: Path, module: str, specs_dir: str = "specs") -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", module.lower()).strip("-") or "root"
    return root / specs_dir / "tests" / slug


def load_plan(root: Path, module: str, specs_dir: str = "specs") -> tuple[dict, Path]:
    p = plan_dir(root, module, specs_dir) / "milestones.json"
    if not p.exists():
        raise FileNotFoundError(f"No plan at {p.relative_to(root)}. Run /sf:test-plan {module} first.")
    return json.loads(p.read_text(encoding="utf-8")), p


# Executables a plan's test_command may start with. The plan is written by an AI agent, so the command is treated
# as untrusted input: no shell, no operators, and only a known test runner, the repo's own detected test command,
# or something the user approved in .specanvil/config.json ("allowed_test_commands").
TEST_RUNNERS = {"pytest", "py.test", "python", "python3", "py", "uv", "poetry", "pipenv", "hatch", "tox", "nox",
                "npm", "npx", "pnpm", "yarn", "bun", "bunx", "deno", "node", "jest", "vitest", "mocha", "ava", "playwright",
                "go", "cargo", "dotnet", "mvn", "mvnw", "gradle", "gradlew", "php", "phpunit", "pest", "composer",
                "bundle", "rspec", "rake", "ruby", "mix", "swift", "flutter", "dart", "make", "just", "task"}
SHELL_META_RX = re.compile(r"[;&|`<>\n\r]|\$\(|\$\{")


def safe_test_argv(command: str, root: Path, test_file: str | None = None) -> tuple[list[str], dict]:
    """Turn a plan's test_command into an argv list + env, or raise ValueError explaining why it is refused."""
    import shutil

    from .config import load_config
    if SHELL_META_RX.search(command):
        raise ValueError("test_command may not contain shell operators (; & | ` < > $( ) or newlines): "
                         "it is run directly, not through a shell.")
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as e:
        raise ValueError(f"test_command is not a valid command line: {e}") from e
    env = {}
    while parts and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[0]) and "{test_file}" not in parts[0]:
        k, _, v = parts.pop(0).partition("=")
        env[k] = v.strip("'\"")
    if not parts:
        raise ValueError("test_command has no executable")
    cfg = load_config(root)
    exe = Path(parts[0].strip("'\"")).name.lower()
    exe = re.sub(r"\.(exe|cmd|bat|sh|ps1)$", "", exe)
    detected = ((cfg.get("profile") or {}).get("stack") or {}).get("commands", {}).get("test", "")
    allowed = set(TEST_RUNNERS)
    for extra in [detected, *(cfg.get("allowed_test_commands") or [])]:
        try:
            first = shlex.split(extra, posix=os.name != "nt")[0] if extra else ""
        except (ValueError, IndexError):
            first = ""
        if first:
            allowed.add(re.sub(r"\.(exe|cmd|bat|sh|ps1)$", "", Path(first).name.lower()))
    if exe not in allowed:
        raise ValueError(f"test_command starts with '{parts[0]}', which is not a recognised test runner. Use the "
                         f"repo's test command{' (' + detected + ')' if detected else ''}, or approve it by adding it to "
                         "\"allowed_test_commands\" in .specanvil/config.json.")
    argv = [(a.strip("'\"") if os.name == "nt" else a).replace("{test_file}", test_file or "{test_file}") for a in parts]
    resolved = shutil.which(argv[0], path=os.pathsep.join([str(root), os.environ.get("PATH", "")]))
    if resolved:
        argv[0] = resolved  # e.g. npm -> npm.cmd on Windows, ./gradlew in the repo
    return argv, env


REQUIRED_TOP = {"module": str, "test_framework": str, "test_command": str, "coverage_target": (int, float),
                "milestones": list}
REQUIRED_MS = {"id": str, "title": str, "status": str, "targets": list, "test_file": str, "cases": list,
               "challenges": list, "done_when": list, "complexity": str}


def validate_plan(plan: dict, ix: CodeIndex | None = None) -> list[str]:
    """Programmatic schema + integrity checks. Returns a list of problems (empty = valid)."""
    errs: list[str] = []
    for k, t in REQUIRED_TOP.items():
        if not isinstance(plan.get(k), t):
            errs.append(f"top-level '{k}' missing or wrong type")
    if "{test_file}" not in str(plan.get("test_command", "")):
        errs.append("test_command must contain {test_file} so each milestone can be verified on its own")
    elif ix is not None:
        try:
            safe_test_argv(plan["test_command"], ix.root)
        except ValueError as e:
            errs.append(str(e))
    seen = set()
    for i, m in enumerate(plan.get("milestones") or []):
        where = m.get("id", f"milestones[{i}]")
        for k, t in REQUIRED_MS.items():
            if not isinstance(m.get(k), t):
                errs.append(f"{where}: '{k}' missing or wrong type")
        if m.get("id") in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(m.get("id"))
        if m.get("status") not in STATUSES:
            errs.append(f"{where}: status must be one of {STATUSES}")
        if m.get("complexity") not in ("S", "M", "L"):
            errs.append(f"{where}: complexity must be S, M or L")
        if not m.get("cases"):
            errs.append(f"{where}: needs at least one case")
        for c in m.get("cases") or []:
            for k in ("id", "name", "given", "when", "then", "kind"):
                if not c.get(k):
                    errs.append(f"{where}: case {c.get('id', '?')} missing '{k}'")
        for ch in m.get("challenges") or []:
            if not (isinstance(ch, dict) and ch.get("challenge") and ch.get("approach")):
                errs.append(f"{where}: each challenge needs 'challenge' and 'approach'")
        tf = str(m.get("test_file", ""))
        if tf.startswith("/") or ".." in Path(tf).parts:
            errs.append(f"{where}: test_file must be a relative path inside the repo")
        if ix is not None:
            for ref in m.get("targets") or []:
                path, _, qual = str(ref).partition("::")
                if not qual or not ix.db.execute("SELECT 1 FROM symbols WHERE path=? AND (qualname=? OR name=?)",
                                                 (path, qual, qual)).fetchone():
                    errs.append(f"{where}: target {ref} does not exist in the index")
    return errs


def next_milestone(plan: dict) -> dict | None:
    for status in ("in_progress", "todo"):
        for m in sorted(plan["milestones"], key=lambda x: x.get("priority", 99)):
            if m["status"] == status:
                return m
    return None


def update_milestone(root: Path, module: str, milestone_id: str, status: str, notes: str = "",
                     tests_added: int = 0, specs_dir: str = "specs", timeout: int = 900) -> dict:
    """The only sanctioned way to change a milestone's status. 'done' is verified by running its tests."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    plan, path = load_plan(root, module, specs_dir)
    m = next((x for x in plan["milestones"] if x["id"] == milestone_id), None)
    if m is None:
        raise ValueError(f"No milestone {milestone_id}. Ids: {', '.join(x['id'] for x in plan['milestones'])}")
    if m["status"] == "done" and status != "done":
        raise ValueError(f"{milestone_id} is already done; reopen it by editing the plan, not from the executor.")
    result = {"at": time.strftime("%Y-%m-%d %H:%M"), "notes": notes[:2000], "tests_added": tests_added}
    if status == "done":
        test_file = m["test_file"]
        if not (root / test_file).exists():
            raise ValueError(f"Cannot mark done: {test_file} does not exist.")
        argv, extra_env = safe_test_argv(plan["test_command"], root, test_file)  # refuses anything but a test runner
        cmd = " ".join(shlex.quote(a) for a in argv)
        try:
            proc = subprocess.run(argv, shell=False, cwd=root, capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace", env={**os.environ, **extra_env},
                                  stdin=subprocess.DEVNULL)
            passed, tail = proc.returncode == 0, (proc.stdout + proc.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            passed, tail = False, f"timed out after {timeout}s"
        except OSError as e:
            passed, tail = False, f"could not start the test runner: {e}"
        result.update({"command": cmd, "passed": passed, "output_tail": tail})
        if not passed:
            m["status"] = "in_progress"
            m["result"] = result
            path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            return {"accepted": False, "milestone": milestone_id, "status": "in_progress",
                    "reason": "tests failed; fix them (or mark blocked with a reason)", "output_tail": tail}
    if status == "blocked" and not notes.strip():
        raise ValueError("Marking blocked requires notes explaining what the planner must resolve.")
    m["status"] = status
    m["result"] = result
    path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    nxt = next_milestone(plan)
    return {"accepted": True, "milestone": milestone_id, "status": status,
            "next": nxt["id"] if nxt else None,
            "progress": f"{sum(x['status'] == 'done' for x in plan['milestones'])}/{len(plan['milestones'])} done"}


def runtime_coverage(ix: CodeIndex, limit: int = 30) -> dict:
    """Map a coverage report (lcov / Cobertura / JaCoCo) onto symbols: hot paths by hit count, code that never
    executed, and a coverage histogram."""
    cov, src = load_coverage(ix.root)
    if not cov:
        return {"coverage_source": None, "note": "No coverage report found. Run your tests with coverage "
                "(pytest --cov --cov-report=xml, vitest --coverage, jest --coverage, dotnet test /p:CollectCoverage=true)."}
    rows = ix.db.execute("SELECT id, path, qualname, kind, start_line, end_line FROM symbols WHERE kind IN "
                         "('function','method')").fetchall()
    hot, cold, buckets = [], [], [0] * 5
    for r in rows:
        lines = cov.get(r["path"]) or next((v for k, v in cov.items() if r["path"].endswith(k)), None)
        if not lines:
            continue
        hits = [h for ln, h in lines.items() if r["start_line"] <= ln <= r["end_line"]]
        if not hits:
            continue
        pct = sum(1 for h in hits if h > 0) / len(hits)
        buckets[min(4, int(pct * 5))] += 1
        ref = f"{r['path']}::{r['qualname']}"
        if max(hits) == 0:
            cold.append({"ref": ref, "lines": r["end_line"] - r["start_line"] + 1})
        else:
            hot.append({"ref": ref, "max_hits": max(hits), "covered_pct": round(100 * pct, 1)})
    hot.sort(key=lambda x: -x["max_hits"])
    cold.sort(key=lambda x: -x["lines"])
    return {"coverage_source": src, "symbols_measured": len(hot) + len(cold),
            "histogram": {"0-20%": buckets[0], "20-40%": buckets[1], "40-60%": buckets[2], "60-80%": buckets[3],
                          "80-100%": buckets[4]},
            "hot_paths": hot[:limit], "never_executed": cold[:limit]}
