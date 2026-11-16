"""Proof, second half: the intent contract, sorting differences against it, risk routing and the evidence chain.

Nothing here calls a model. The intent contract is written by an agent that reads only the ticket; this module
checks its shape, uses it to sort what `proof.diff_captures` found, applies fixed risk rules and writes a
tamper-evident record. Every decision is stored together with the inputs that produced it.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import features_dir, load_config, state_dir

EXPECT = ("change", "no_change")
MATCH_KEYS = ("endpoint", "test", "path", "kind")
SENSITIVE_WORDS = ["auth", "authn", "authz", "authentication", "authorization", "login", "logout", "signin", "signup",
                   "password", "credential", "session", "oauth", "sso", "jwt", "token", "permission", "rbac", "acl",
                   "role", "security", "crypto", "encryption", "secret", "payment", "billing", "invoice", "checkout",
                   "refund", "payout", "ledger", "migration", "schema"]
MANIFESTS = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "pyproject.toml",
             "poetry.lock", "pipfile", "pipfile.lock", "uv.lock", "go.mod", "go.sum", "cargo.toml", "cargo.lock",
             "pom.xml", "build.gradle", "build.gradle.kts", "gemfile", "gemfile.lock", "composer.json", "composer.lock"}
ALWAYS_NOT_CHECKED = [
    "Code that no E2E test reaches: only behaviour exercised by the tests was compared.",
    "Performance and behaviour under load.",
    "Architecture and code quality (see /sa:verify).",
    "Visual appearance: page content is compared only where a test attaches a `proof:` snapshot.",
]


# ----------------------------------------------------------------------------- intent contract

def intent_path(root: Path, ticket: str) -> Path:
    """Where a ticket's intent contract lives: in its feature folder when there is one, else under .specanvil/proof."""
    safe = re.sub(r"[^A-Za-z0-9._#-]+", "-", ticket or "change").strip("-") or "change"
    features = root / features_dir(load_config(root))
    if ticket and features.is_dir():  # feature folders are named <ID>-<slug>
        for d in sorted(features.iterdir()):
            if d.is_dir() and (d.name == safe or d.name.startswith(safe + "-")):
                return d / "intent.json"
    return state_dir(root) / "proof" / "intents" / f"{safe}.json"


def validate_intent(data: Any) -> list[str]:
    """Problems with an intent contract; empty when it is well formed."""
    if not isinstance(data, dict):
        return ["The contract must be a JSON object."]
    errs: list[str] = []
    if not isinstance(data.get("ticket"), str) or not data["ticket"].strip():
        errs.append('"ticket" must name the ticket this contract was written from.')
    if not isinstance(data.get("summary"), str) or not data["summary"].strip():
        errs.append('"summary" must say in one sentence what the ticket asks for.')
    kind = data.get("kind", "feature")
    if kind not in ("feature", "fix", "refactor"):
        errs.append('"kind" must be feature, fix or refactor.')
    rules = data.get("rules")
    if not isinstance(rules, list):
        return errs + ['"rules" must be a list.']
    seen: set[str] = set()
    for i, r in enumerate(rules):
        where = f"rules[{i}]"
        if not isinstance(r, dict):
            errs.append(f"{where} must be an object.")
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid:
            errs.append(f'{where}: "id" is required (R1, R2, ...).')
        elif rid in seen:
            errs.append(f"{where}: id {rid} is used twice.")
        else:
            seen.add(rid)
        if r.get("expect") not in EXPECT:
            errs.append(f'{where}: "expect" must be "change" or "no_change".')
        if not isinstance(r.get("text"), str) or len(r["text"].strip()) < 8:
            errs.append(f'{where}: "text" must state the rule in words a reviewer can check.')
        match = r.get("match") or {}
        if not isinstance(match, dict) or any(k not in MATCH_KEYS or not isinstance(v, str) or not v for k, v in match.items()):
            errs.append(f'{where}: "match" may only hold non-empty strings under {", ".join(MATCH_KEYS)}.')
        elif r.get("expect") == "change" and not match:
            errs.append(f'{where}: a "change" rule needs a "match" (an endpoint, test or path pattern), otherwise no '
                        "difference can be recognised as the one the ticket asked for.")
    if kind != "refactor" and not any(isinstance(r, dict) and r.get("expect") == "change" for r in rules):
        errs.append('A feature or fix needs at least one "change" rule. If no behaviour should change, set "kind" to '
                    '"refactor".')
    unclear = data.get("unclear", [])
    if not isinstance(unclear, list) or any(not isinstance(u, str) for u in unclear):
        errs.append('"unclear" must be a list of questions (empty when the ticket is clear).')
    return errs


def load_intent(root: Path, ticket: str, file: str | None = None) -> tuple[dict | None, str | None]:
    """(contract, path relative to the repo) or (None, None) when there is none. Raises ValueError when it is invalid."""
    p = Path(file) if file else intent_path(root, ticket)
    if not p.is_absolute():
        p = root / p
    if not p.is_file():
        if file:
            raise ValueError(f"Intent contract not found: {file}")
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}: not valid JSON ({e})") from e
    errs = validate_intent(data)
    if errs:
        raise ValueError(f"{p} is not a valid intent contract:\n  - " + "\n  - ".join(errs))
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = str(p)
    return data, rel


def _rule_matches(rule: dict, d: dict) -> bool:
    m = rule.get("match") or {}
    if not m:
        return rule.get("expect") == "no_change"  # a bare "nothing else may change" covers every difference
    for key in MATCH_KEYS:
        pat = m.get(key)
        if pat and not fnmatch.fnmatchcase(str(d.get(key) or "").lower(), pat.lower()):
            return False
    return True


def classify(diffs: list[dict], intent: dict | None, before: dict, after: dict) -> dict:
    """Sort every difference: intended (a "change" rule asked for it), violation (a "no_change" rule forbids it),
    regression (a test that passed now fails), fixed (a test that failed now passes), info (tests added/removed)
    or unintended (nobody asked for it). Probe tests are reported separately, not here."""
    rules = (intent or {}).get("rules") or []
    probes = {k for cap in (before, after) for k, t in (cap.get("tests") or {}).items() if t.get("probe")}
    hits = {r["id"]: 0 for r in rules}
    out = []
    for d in diffs:
        if d["test"] in probes:
            continue
        d = dict(d)
        if d["kind"] == "status":
            d["class"] = "regression" if d["after"] in ("failed", "flaky") and d["before"] == "passed" else \
                "fixed" if d["after"] == "passed" else "unintended"
        elif d["kind"] in ("test_added", "test_removed"):
            d["class"] = "info"
        else:
            scoped_ban = next((r for r in rules if r["expect"] == "no_change" and r.get("match") and _rule_matches(r, d)), None)
            asked = next((r for r in rules if r["expect"] == "change" and _rule_matches(r, d)), None)
            if scoped_ban:
                d["class"], d["rule"] = "violation", scoped_ban["id"]
            elif asked:
                d["class"], d["rule"] = "intended", asked["id"]
            else:
                d["class"] = "unintended"
        if d.get("rule"):
            hits[d["rule"]] += 1
        out.append(d)
    unmet = [r["id"] for r in rules if r["expect"] == "change" and not hits[r["id"]]]
    return {"diffs": out, "rule_hits": hits, "unmet": unmet}


def probe_findings(before: dict, after: dict) -> list[dict]:
    """Adversarial tests that fail on the changed code, each with the command that reproduces it."""
    out = []
    for key, t in sorted((after.get("tests") or {}).items()):
        if t.get("probe") and t["status"] in ("failed", "flaky"):
            old = (before.get("tests") or {}).get(key)
            out.append({"test": key, "file": t.get("file"), "line": t.get("line"), "error": t.get("error", ""),
                        "failed_before": bool(old and old["status"] in ("failed", "flaky")),
                        "reproduce": f"npx playwright test {t.get('file')}:{t.get('line')}"})
    return out


# ----------------------------------------------------------------------------- risk routing

def _words(path: str) -> set[str]:
    out = set()
    for w in re.findall(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])", path):
        w = w.lower()
        out.add(w)
        if w.endswith("s"):
            out.add(w[:-1])
    return out


def sensitive_files(files: list[dict], words: list[str] | None = None) -> list[str]:
    wanted = set(words or SENSITIVE_WORDS)
    return [f["path"] for f in files if (_words(f["path"]) & wanted) or f["path"].lower().endswith(".sql")]


def score(pcfg: dict, files: list[dict], sorted_diffs: dict, probes: list[dict], intent: dict | None,
          independence: dict, before: dict, after: dict, counts: dict, test_paths: list[str]) -> dict:
    """Fixed rules, no model. `gates` are conditions that always send the change to a person; `points` set the level.
    A change is cleared automatically only with no gate and a low score."""
    gates: list[str] = []
    reasons: list[dict] = []

    def point(n: int, why: str) -> None:
        if n:
            reasons.append({"points": n, "why": why})

    by: dict[str, int] = {}
    for d in sorted_diffs["diffs"]:
        by[d["class"]] = by.get(d["class"], 0) + 1
    lines = sum(f["added"] + f["removed"] for f in files)
    point(20 if lines > 400 else 10 if lines > 150 else 5 if lines > 50 else 0, f"{lines} lines changed")
    point(10 if len(files) > 15 else 5 if len(files) > 5 else 0, f"{len(files)} files changed")

    if by.get("unintended"):
        n = by["unintended"]
        gates.append(f"{n} behaviour change{'s' if n != 1 else ''} the ticket did not ask for")
        point(min(45, 15 * n), f"{n} unintended difference{'s' if n != 1 else ''}")
    if by.get("violation"):
        n = by["violation"]
        gates.append(f"{n} difference{'s' if n != 1 else ''} in behaviour the ticket says must not change")
        point(min(60, 30 * n), f"{n} must-not-change rule violation{'s' if n != 1 else ''}")
    if by.get("regression"):
        n = by["regression"]
        gates.append(f"{n} test{'s' if n != 1 else ''} passed before the change and fail{'s' if n == 1 else ''} after it")
        point(min(60, 30 * n), f"{n} regression{'s' if n != 1 else ''}")
    if probes:  # a probe that also fails on the old code still counts: it usually asserts the new behaviour
        gates.append(f"{len(probes)} adversarial probe{'s' if len(probes) != 1 else ''} broke the change")
        point(min(60, 20 * len(probes)), f"{len(probes)} failed probe{'s' if len(probes) != 1 else ''}")

    if intent is None:
        gates.append("no intent contract: differences cannot be sorted into asked-for and not asked-for")
        point(15, "no intent contract")
    else:
        if intent.get("unclear"):
            gates.append(f"the ticket is unclear ({len(intent['unclear'])} open question"
                         f"{'s' if len(intent['unclear']) != 1 else ''})")
            point(10, "unclear ticket")
        if sorted_diffs["unmet"]:
            gates.append("no evidence for requested change " + ", ".join(sorted_diffs["unmet"])
                         + ": no test showed that behaviour changing")
            point(min(30, 10 * len(sorted_diffs["unmet"])), f"{len(sorted_diffs['unmet'])} requested change(s) without evidence")
    if independence.get("distinct") is False:
        gates.append("the intent contract was written by the same model that wrote the code")
        point(15, "verifier is not independent of the author")
    elif independence.get("distinct") is None and intent is not None:
        point(5, "independence of the intent contract not confirmed (author or verifier model not stated)")

    sens = sensitive_files(files, pcfg.get("sensitive_words"))
    if sens:
        gates.append("touches sensitive code: " + ", ".join(sens[:4]) + (f" and {len(sens) - 4} more" if len(sens) > 4 else ""))
        point(25, f"{len(sens)} sensitive file{'s' if len(sens) != 1 else ''}")
    deps = [f["path"] for f in files if f["path"].rsplit("/", 1)[-1].lower() in MANIFESTS
            or re.search(r"(^|/)requirements[^/]*\.txt$|\.csproj$", f["path"].lower())]
    if deps:
        gates.append("dependencies changed (" + ", ".join(deps[:3]) + "): the old code ran with the installed "
                     "packages, so the comparison may not be like for like")
        point(15, "dependency manifest changed")
    if pcfg.get("settings_changed"):
        gates.append("the change edits the proof settings in .specanvil/config.json that decide how it is checked")
        point(20, "proof settings changed")
    removed_tests = [f["path"] for f in files if f["status"] == "deleted" and (
        any(f["path"].startswith(t.rstrip("/") + "/") or f["path"] == t for t in test_paths)
        or re.search(r"(^|/)(tests?|e2e|__tests__|specs?)/|\.(test|spec)\.[a-z]+$", f["path"].lower()))]
    if removed_tests:
        gates.append("test files deleted: " + ", ".join(removed_tests[:3]))
        point(20, f"{len(removed_tests)} test file{'s' if len(removed_tests) != 1 else ''} deleted")

    for label, cap in (("before", before), ("after", after)):
        if not cap.get("report"):
            gates.append(f"the E2E run {label} the change produced no report (exit {cap.get('exit')})")
            point(30, f"no E2E report {label} the change")
    tests_after = after.get("tests") or {}
    still_failing = [k for k, t in tests_after.items() if t["status"] == "failed" and not t.get("probe")
                     and (before.get("tests") or {}).get(k, {}).get("status") == "failed"]
    point(min(15, 5 * len(still_failing)), f"{len(still_failing)} test(s) fail before and after the change")
    new_failing = [k for k, t in tests_after.items() if t["status"] == "failed" and not t.get("probe")
                   and k not in (before.get("tests") or {})]
    if new_failing:
        gates.append(f"{len(new_failing)} new test{'s' if len(new_failing) != 1 else ''} fail on the changed code")
        point(min(40, 20 * len(new_failing)), f"{len(new_failing)} new failing test(s)")
    if after.get("report") and not counts.get("tests"):
        gates.append("no test ran on both versions, so no behaviour was compared")
        point(30, "nothing compared")
    elif after.get("report") and not counts.get("exchanges") and not counts.get("snapshots"):
        gates.append("the tests made no API calls and attached no snapshots: only pass/fail was compared")
        point(20, "thin evidence")

    total = min(100, sum(r["points"] for r in reasons))
    level = "low" if total <= int(pcfg.get("auto_max_score", 24)) else "medium" if total < 60 else "high"
    route = "auto" if not gates and level == "low" else "review"
    return {"score": total, "level": level, "route": route, "gates": gates, "reasons": reasons,
            "auto_max_score": int(pcfg.get("auto_max_score", 24))}


def brief(risk: dict, sorted_diffs: dict, probes: list[dict]) -> str:
    """What a senior engineer needs to read first, instead of the whole diff."""
    if risk["route"] == "auto":
        return "No behaviour changed that the ticket did not ask for, no probe failed and the risk score is low."
    lines = ["Needs a person because:"] + [f"- {g}" for g in risk["gates"]]
    if not risk["gates"]:
        lines.append(f"- the risk score is {risk['score']} ({risk['level']})")
    surprises = [d for d in sorted_diffs["diffs"] if d["class"] in ("unintended", "violation", "regression")][:5]
    if surprises:
        lines.append("Look at these first:")
        lines += [f"- {_describe(d)}" for d in surprises]
    for p in probes[:3]:
        first = next((ln.strip() for ln in p["error"].splitlines() if ln.strip()), "")
        lines.append(f"- probe failed: {p['test']}" + (f": {first[:160]}" if first else "") + f" ({p['reproduce']})")
    return "\n".join(lines)


def _where(d: dict) -> str:
    if d["kind"] == "snapshot":
        return f"page snapshot '{d.get('path')}'"
    if d["kind"] == "status":
        return "test result"
    label = {"call_added": "new call ", "call_removed": "call no longer made ", "call_count": "number of calls ",
             "response_status": "status of ", "request_body": "request to "}.get(d["kind"], "")
    return label + " ".join(x for x in [d.get("endpoint"), d.get("path")] if x)


def _describe(d: dict) -> str:
    where = _where(d)
    return f"[{d['class']}] {d['test']}: {where} was {json.dumps(d['before'], ensure_ascii=False)}, " \
           f"now {json.dumps(d['after'], ensure_ascii=False)}"


# ----------------------------------------------------------------------------- evidence chain

def records_dir(root: Path) -> Path:
    return state_dir(root) / "proof" / "records"


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def record_hash(rec: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in rec.items() if k != "hash"})).hexdigest()


def load_records(root: Path) -> list[dict]:
    d = records_dir(root)
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rec = {"seq": -1, "type": "unreadable", "hash": ""}
        rec["_file"] = f.name
        out.append(rec)
    return out


def append_record(root: Path, rec: dict) -> dict:
    """Add a record to the chain: it stores the hash of the record before it, then its own hash."""
    existing = load_records(root)
    rec = dict(rec)
    rec["seq"] = (max((r.get("seq", 0) for r in existing), default=0)) + 1
    rec["prev"] = existing[-1].get("hash", "") if existing else ""
    rec["hash"] = record_hash(rec)
    d = records_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['seq']:05d}-{rec['id']}.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return rec


def verify_chain(root: Path) -> list[str]:
    """Problems in the evidence chain; empty when every record is intact and in order."""
    errs = []
    prev = ""
    for i, rec in enumerate(load_records(root), 1):
        name = rec.pop("_file", "?")
        if rec.get("type") == "unreadable":
            errs.append(f"{name}: not valid JSON")
            continue
        if rec.get("seq") != i:
            errs.append(f"{name}: expected record number {i}, found {rec.get('seq')} (a record is missing or was added)")
        if rec.get("hash") != record_hash(rec):
            errs.append(f"{name}: content does not match its hash (edited after it was written)")
        if rec.get("prev", "") != prev:
            errs.append(f"{name}: does not follow the record before it (chain broken)")
        prev = rec.get("hash", "")
    return errs


def find_record(root: Path, ref: str = "") -> dict | None:
    """A proof record by id, id prefix, sequence number, or the latest when `ref` is empty."""
    proofs = [r for r in load_records(root) if r.get("type") == "proof"]
    if not ref:
        return proofs[-1] if proofs else None
    for r in reversed(proofs):
        if r["id"] == ref or r["id"].startswith(ref) or str(r.get("seq")) == ref:
            return r
    return None


def decisions_for(root: Path, record_id: str) -> list[dict]:
    return [r for r in load_records(root) if r.get("type") == "decision" and r.get("ref") == record_id]


def status_of(root: Path, rec: dict) -> str:
    """Where a change stands: the last human decision wins over the engine's routing."""
    ds = decisions_for(root, rec["id"])
    if ds:
        return {"approve": "approved", "reject": "rejected", "hold": "held for review"}[ds[-1]["action"]] + \
            f" by {ds[-1]['by']}"
    return "cleared automatically" if rec["decision"]["route"] == "auto" else "waiting for review"


def decide(root: Path, ref: str, action: str, by: str, reason: str) -> dict:
    """Record a person's decision on a proof record. Approving a change routed to review, rejecting, or holding one
    the engine cleared are all allowed, and all are recorded with who and why."""
    if action not in ("approve", "reject", "hold"):
        raise ValueError("action must be approve, reject or hold")
    rec = find_record(root, ref)
    if not rec:
        raise ValueError(f"No proof record matches '{ref}'. See `specanvil proof list`.")
    if not by.strip():
        raise ValueError("--by is required: an approval has to name the person who made it.")
    if not reason.strip():
        raise ValueError("--reason is required: say why, so an auditor can follow the decision.")
    overrides = (action == "approve" and rec["decision"]["route"] == "review" and bool(rec["risk"]["gates"])) or \
        (action in ("reject", "hold") and rec["decision"]["route"] == "auto")
    return append_record(root, {
        "type": "decision", "id": f"{rec['id']}-{action}-{time.strftime('%H%M%S', time.gmtime())}",
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ref": rec["id"], "ref_hash": rec["hash"],
        "action": action, "by": by.strip(), "reason": reason.strip(), "overrides_routing": overrides})


def _git_user(root: Path) -> str:
    def get(key: str) -> str:
        return subprocess.run(["git", "config", key], cwd=root, capture_output=True, text=True).stdout.strip()
    name, mail = get("user.name"), get("user.email")
    return f"{name} <{mail}>" if name and mail else name or mail


def build_record(root: Path, pcfg: dict, *, run_id: str, ticket: str, scope: str, head: str, branch: str, tree: str,
                 files: list[dict], intent: dict | None, intent_src: str | None, author_model: str, author_tool: str,
                 before: dict, after: dict, comparison: dict, calibrated: bool, notes: list[str]) -> dict:
    from .proof import run_summary
    sorted_diffs = classify(comparison["diffs"], intent, before, after)
    probes = probe_findings(before, after)
    intent_model = (intent or {}).get("model") or ""
    independence = {"author_model": author_model or None, "intent_model": intent_model or None,
                    "distinct": (author_model.strip().lower() != intent_model.strip().lower())
                    if author_model and intent_model else None}
    risk = score(pcfg, files, sorted_diffs, probes, intent, independence, before, after, comparison["counts"],
                 pcfg.get("test_paths") or [])
    by: dict[str, int] = {}
    for d in sorted_diffs["diffs"]:
        by[d["class"]] = by.get(d["class"], 0) + 1
    not_checked = list(ALWAYS_NOT_CHECKED)
    masked = (before.get("masked") or 0) + (after.get("masked") or 0)
    if masked:
        not_checked.append(f"{masked} values that look like timestamps, ids or tokens were masked before comparing.")
    if comparison.get("noise_dropped"):
        not_checked.append(f"{comparison['noise_dropped']} differences were dropped because the same values also "
                           "differ between two runs of the unchanged code.")
    skipped = sum(1 for t in (after.get("tests") or {}).values() if t["status"] == "skipped")
    if skipped:
        not_checked.append(f"{skipped} test(s) were skipped.")
    if not pcfg.get("test_paths"):
        not_checked.append("proof.test_paths is not set: each version ran its own copy of the tests, so tests added "
                           "by the change were not run against the old code.")
    return {
        "type": "proof", "id": run_id, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ticket": ticket or (intent or {}).get("ticket") or "", "scope": scope,
        "git": {"base": head, "branch": branch, "tree": tree, "user": _git_user(root)},
        "author": {"model": author_model or None, "tool": author_tool or None},
        "change": {"files": files, "lines_added": sum(f["added"] for f in files),
                   "lines_removed": sum(f["removed"] for f in files)},
        "intent": {"source": intent_src, "hash": hashlib.sha256(_canon(intent)).hexdigest() if intent else None,
                   "contract": intent},
        "independence": independence,
        "runs": {"before": run_summary(before), "after": run_summary(after), "calibrated": calibrated,
                 "command": pcfg.get("e2e_cmd"), "tests_shared": bool(pcfg.get("test_paths"))},
        "behaviour": {"compared": comparison["counts"], "by_class": by, "unmet_rules": sorted_diffs["unmet"],
                      "rule_hits": sorted_diffs["rule_hits"], "differences": sorted_diffs["diffs"]},
        "probes": probes,
        "risk": risk,
        "decision": {"route": risk["route"], "brief": brief(risk, sorted_diffs, probes)},
        "not_checked": not_checked,
        "notes": notes,
        "tool": {"specanvil": __version__},
    }


# ----------------------------------------------------------------------------- rendering

def _cell(v: Any, n: int = 70) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(str(s).split()).replace("|", "\\|")
    return s if len(s) <= n else s[:n] + "…"


def render_md(root: Path, rec: dict, full: bool = True) -> str:
    """The evidence pack as Markdown: readable by an engineer, a client or an auditor."""
    risk, beh = rec["risk"], rec["behaviour"]
    by = beh["by_class"]
    b, a = rec["runs"]["before"], rec["runs"]["after"]
    out = [f"### Proof {rec['id']}" + (f" · {rec['ticket']}" if rec["ticket"] else ""), "",
           f"**Status: {status_of(root, rec)}** · risk {risk['score']}/100 ({risk['level']}) · "
           f"{len(rec['change']['files'])} files, +{rec['change']['lines_added']} −{rec['change']['lines_removed']}", "",
           "| Check | Result |", "|---|---|",
           f"| Intent contract | {_intent_line(rec)} |",
           f"| Tests on the old code | {b['passed']} passed, {b['failed']} failed of {b['tests']} |",
           f"| Tests on the change | {a['passed']} passed, {a['failed']} failed of {a['tests']} |",
           f"| Compared | {beh['compared']['tests']} tests, {beh['compared']['exchanges']} API responses, "
           f"{beh['compared']['snapshots']} page snapshots |",
           f"| Differences | {by.get('intended', 0)} asked for · {by.get('unintended', 0)} not asked for · "
           f"{by.get('violation', 0)} forbidden · {by.get('regression', 0)} regressions · {by.get('fixed', 0)} fixed |",
           f"| Adversarial probes | {len(rec['probes'])} failed |",
           f"| Written by | {rec['author'].get('model') or 'not stated'}"
           + (f" ({rec['author']['tool']})" if rec["author"].get("tool") else "") + " |", ""]
    if rec["decision"]["route"] == "review":
        out += [rec["decision"]["brief"], ""]
    shown = [d for d in beh["differences"] if d["class"] in ("unintended", "violation", "regression")]
    if shown:
        out += ["| | Test | Where | Before | After |", "|---|---|---|---|---|"]
        for d in shown[:15 if full else 6]:
            where = _where(d)
            out.append(f"| {d['class']} | {_cell(d['test'], 60)} | `{_cell(where, 60)}` | {_cell(d['before'], 50)} | "
                       f"{_cell(d['after'], 50)} |")
        if len(shown) > (15 if full else 6):
            out.append(f"\n{len(shown) - (15 if full else 6)} more in the record.")
        out.append("")
    if full:
        asked = [d for d in beh["differences"] if d["class"] == "intended"]
        if asked:
            out += ["Asked for by the ticket:"] + [
                f"- {d['rule']}: `{_cell(_where(d), 80)}` "
                f"{_cell(d['before'], 40)} → {_cell(d['after'], 40)}" for d in asked[:10]] + [""]
        for p in rec["probes"][:10]:
            out.append(f"- probe failed{' (also on the old code)' if p['failed_before'] else ''}: {p['test']} · "
                       f"`{p['reproduce']}`")
        if rec["probes"]:
            out.append("")
        for d in decisions_for(root, rec["id"]):
            out.append(f"- {d['time']} · **{d['action']}** by {d['by']}"
                       + (" (overrides the routing)" if d.get("overrides_routing") else "") + f": {d['reason']}")
        out += ["", "Not checked:"] + [f"- {n}" for n in rec["not_checked"]] + [""]
        for n in rec.get("notes") or []:
            out.append(f"> {n}")
    out.append(f"<sub>Record {rec['seq']} · hash `{rec['hash'][:16]}` · base `{rec['git']['base'][:10]}` · "
               f"tree `{rec['git']['tree'][:10]}` · computed locally, no AI calls in the comparison or the risk rules.</sub>")
    return "\n".join(out) + "\n"


def _intent_line(rec: dict) -> str:
    c = rec["intent"]["contract"]
    if not c:
        return "none"
    rules = c.get("rules") or []
    text = f"{sum(r['expect'] == 'change' for r in rules)} change rules, " \
           f"{sum(r['expect'] == 'no_change' for r in rules)} must-not-change rules"
    if c.get("model"):
        text += f", written by {c['model']}"
    if c.get("unclear"):
        text += f", {len(c['unclear'])} open questions"
    if rec["behaviour"]["unmet_rules"]:
        text += f"; no evidence for {', '.join(rec['behaviour']['unmet_rules'])}"
    return text


def records_added_since(root: Path, base: str) -> list[dict]:
    """Proof records that the current branch adds on top of `base` (for the pull-request comment)."""
    rel = records_dir(root).relative_to(root).as_posix() if records_dir(root).is_relative_to(root) else None
    if not rel:
        return []
    p = subprocess.run(["git", "diff", "--name-only", "--diff-filter=A", base, "--", rel], cwd=root,
                       capture_output=True, text=True)
    names = {Path(x).name for x in p.stdout.split()} if p.returncode == 0 else set()
    return [r for r in load_records(root) if r.get("type") == "proof" and r.get("_file") in names]
