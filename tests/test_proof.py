"""`specanvil proof`: the stash is always restored, differences are sorted against the intent contract, risk rules
route the change, and the evidence chain detects tampering. A stand-in for Playwright (same report and trace format)
keeps these tests free of Node and browsers; tests/evals/run_proof_demo.py runs the real thing."""
import json
import subprocess
import sys
import zipfile

import pytest

from specanvil import proof as P
from specanvil import proofrecord as R

GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]

# Reads the app's "behaviour" from app.json and the tests from e2e/tests.json, then writes what Playwright writes:
# a JSON report plus one trace.zip per test with the API responses.
FAKE_E2E = r'''
import json, os, sys, time, uuid, zipfile
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--output") + 1])
app = json.loads(Path("app.json").read_text())
specs = []
for i, t in enumerate(json.loads(Path("e2e/tests.json").read_text())):
    d = out / f"t{i}"
    d.mkdir(parents=True, exist_ok=True)
    body = {"total": app["totals"].get(t["customer"]), "quoteId": str(uuid.uuid4()),
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "rand": time.time_ns() if app.get("noisy") else 1}
    with zipfile.ZipFile(d / "trace.zip", "w") as z:
        z.writestr("resources/b.json", json.dumps(body))
        z.writestr("0-trace.network", json.dumps({"type": "resource-snapshot", "snapshot": {
            "request": {"method": "GET", "url": f"http://localhost:4173/api/quote?qty=6&customer={t['customer']}"},
            "response": {"status": 200, "content": {"mimeType": "application/json", "_file": "resources/b.json"}}}})
            + "\n" + json.dumps({"type": "resource-snapshot", "snapshot": {"_resourceType": "document",
            "request": {"method": "GET", "url": "http://localhost:4173/"},
            "response": {"status": 200, "content": {"mimeType": "text/html"}}}}))
    ok = "expect" not in t or body["total"] == t["expect"]
    import base64
    specs.append({"title": t["title"], "file": "quotes.spec.js", "line": i + 1, "tags": [], "tests": [{
        "projectName": "", "status": "expected" if ok else "unexpected", "results": [{
            "status": "passed" if ok else "failed", "error": None if ok else {"message": f"expected {t.get('expect')}"},
            "attachments": [{"name": "trace", "contentType": "application/zip", "path": str(d / "trace.zip")},
                            {"name": "proof:page", "contentType": "text/plain",
                             "body": base64.b64encode(f"Total: {body['total']}".encode()).decode()}]}]}]})
if app.get("junk"):
    Path("leftover.txt").write_text("made by the tests")
Path(os.environ["PLAYWRIGHT_JSON_OUTPUT_FILE"]).write_text(json.dumps(
    {"config": {}, "suites": [{"title": "quotes.spec.js", "file": "quotes.spec.js", "specs": specs}]}))
'''

INTENT = {"ticket": "SHOP-77", "model": "sonnet", "kind": "feature", "summary": "Gold customers pay less on large orders.",
          "rules": [{"id": "R1", "expect": "change", "text": "Gold totals for large orders go down.",
                     "match": {"endpoint": "GET /api/quote*customer=gold*"}},
                    {"id": "R2", "expect": "change", "text": "The page shows the new gold total.",
                     "match": {"test": "*gold*", "kind": "snapshot"}},
                    {"id": "R3", "expect": "no_change", "text": "Silver totals do not change.",
                     "match": {"endpoint": "GET /api/quote*customer=silver*"}}],
          "unclear": []}


def git(root, *args):
    return subprocess.run(GIT + list(args), cwd=root, check=True, capture_output=True, text=True).stdout


def app(root, **totals):
    data = {"totals": {"standard": 15000, "silver": 14250, "gold": 13500}}
    extra = {k: totals.pop(k) for k in ("junk", "noisy") if k in totals}
    data["totals"].update(totals)
    data.update(extra)
    (root / "app.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def e2e_tests(root, *customers, extra=()):
    (root / "e2e").mkdir(exist_ok=True)
    (root / "e2e" / "tests.json").write_text(json.dumps(
        [{"title": f"{c} quote", "customer": c} for c in customers] + list(extra)), encoding="utf-8")


@pytest.fixture
def shop(tmp_path):
    """A committed repo with the stand-in app, three E2E tests and an intent contract for SHOP-77."""
    root = tmp_path / "shop"
    root.mkdir()
    (root / "fake_e2e.py").write_text(FAKE_E2E, encoding="utf-8")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "notes.md").write_text("notes\n", encoding="utf-8")
    app(root)
    e2e_tests(root, "standard", "silver", "gold")
    exe = f'"{sys.executable}"'
    (root / ".specanvil").mkdir()
    (root / ".specanvil" / "config.json").write_text(json.dumps({
        "app_url": "http://localhost:4173", "allowed_test_commands": [exe],
        "proof": {"e2e_cmd": f"{exe} fake_e2e.py", "test_paths": ["e2e"]}}), encoding="utf-8")
    (root / ".specanvil" / "proof" / "intents").mkdir(parents=True)
    (root / ".specanvil" / "proof" / "intents" / "SHOP-77.json").write_text(json.dumps(INTENT), encoding="utf-8")
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")        # keep file bytes identical through stash and checkout
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


def snapshot(root):
    """Everything a user could lose: index, working tree, untracked and ignored files, stashes."""
    status = [ln for ln in git(root, "status", "--porcelain", "-uall").splitlines() if "proof/records/" not in ln]
    return {"status": status, "staged": git(root, "diff", "--cached"),
            "unstaged": git(root, "diff"), "stash": git(root, "stash", "list"),
            "files": {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*"))
                      if p.is_file() and ".git/" not in p.as_posix() and ".specanvil/proof/records" not in p.as_posix()}}


def test_staged_change_is_proven_and_every_kind_of_local_work_survives(shop):
    """The before run sees the last commit, the after run sees only what is staged, and the index, unstaged edits,
    untracked and ignored files are exactly as they were."""
    app(shop, gold=12750)
    git(shop, "add", "app.json")
    app(shop, gold=12750, silver=1)                      # unstaged edit on top of the staged one, same file
    (shop / "notes.md").write_text("notes\nmore\n", encoding="utf-8")   # unstaged edit, other file
    (shop / "scratch.txt").write_text("untracked", encoding="utf-8")
    (shop / "ignored.txt").write_text("ignored", encoding="utf-8")
    before = snapshot(shop)
    rec = P.run(shop, ticket="SHOP-77", author_model="opus")
    assert snapshot(shop) == before
    assert P.read_lock(shop) is None and not (P.git_dir(shop) / P.WORK_DIR / rec["id"]).exists()
    diffs = rec["behaviour"]["differences"]
    assert {(d["class"], d["endpoint"], d["before"], d["after"]) for d in diffs if d["kind"] == "response_body"} == {
        ("intended", "GET /api/quote?customer=gold&qty=6", 13500, 12750)}   # silver=1 was not staged: not tested
    assert rec["behaviour"]["by_class"] == {"intended": 2} and rec["behaviour"]["unmet_rules"] == []
    assert rec["risk"]["route"] == "auto" and rec["risk"]["gates"] == [] and rec["decision"]["route"] == "auto"
    assert rec["independence"] == {"author_model": "opus", "intent_model": "sonnet", "distinct": True}
    assert rec["behaviour"]["compared"] == {"tests": 3, "exchanges": 3, "snapshots": 3, "tests_added": 0, "tests_removed": 0}
    assert rec["runs"]["before"]["api_calls"] == 3   # the page load in the trace is not an API call
    assert any("masked" in n for n in rec["not_checked"])   # quoteId and createdAt differ on every call


def test_change_nobody_asked_for_is_routed_to_a_person(shop):
    app(shop, gold=12750, silver=13500)                  # the ticket is about gold; silver changes too
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    by = {d["class"] for d in rec["behaviour"]["differences"] if "silver" in d["test"]}
    assert by == {"violation", "unintended"}             # the API response breaks R3; the page text has no rule
    assert rec["risk"]["route"] == "review" and rec["risk"]["level"] != "low"
    assert any("must not change" in g for g in rec["risk"]["gates"])
    assert "silver quote" in rec["decision"]["brief"] and "14250" in rec["decision"]["brief"]
    assert snapshot(shop)["status"] == [" M app.json"] and not git(shop, "stash", "list")


def test_gates_no_contract_same_model_sensitive_files_and_missing_evidence(shop):
    app(shop, gold=12750)
    rec = P.run(shop, ticket="NOPE-1", scope="all")
    assert rec["intent"]["contract"] is None and any("no intent contract" in g for g in rec["risk"]["gates"])
    assert {d["class"] for d in rec["behaviour"]["differences"]} == {"unintended"}
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="Sonnet")
    assert rec["independence"]["distinct"] is False and any("same model" in g for g in rec["risk"]["gates"])
    app(shop)                                            # no behaviour change at all: R1 and R2 have no evidence
    (shop / "src").mkdir()
    (shop / "src" / "PaymentService.py").write_text("x = 1\n", encoding="utf-8")
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    assert rec["behaviour"]["unmet_rules"] == ["R1", "R2"]
    gates = " | ".join(rec["risk"]["gates"])
    assert "no evidence for requested change R1, R2" in gates and "sensitive code: src/PaymentService.py" in gates
    assert R.sensitive_files([{"path": "docs/authors.md"}, {"path": "api/auth/login.ts"}, {"path": "db/001_init.sql"}]) == \
        ["api/auth/login.ts", "db/001_init.sql"]


def test_new_tests_run_against_the_old_code_and_probes_gate(shop):
    """E2E tests under proof.test_paths come from the change for both runs, so a test written for the change shows
    the old behaviour failing; a failing @probe test is a finding with a reproduce command."""
    app(shop, gold=12750)
    e2e_tests(shop, "standard", "silver", "gold", extra=[
        {"title": "gold total is 127.50", "customer": "gold", "expect": 12750},
        {"title": "@probe silver is not discounted further", "customer": "silver", "expect": 99}])
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    assert rec["runs"]["before"]["tests"] == rec["runs"]["after"]["tests"] == 5
    fixed = [d for d in rec["behaviour"]["differences"] if d["class"] == "fixed"]
    assert [(d["test"], d["before"], d["after"]) for d in fixed] == [("quotes.spec.js › gold total is 127.50", "failed", "passed")]
    assert [p["test"] for p in rec["probes"]] == ["quotes.spec.js › @probe silver is not discounted further"]
    assert rec["probes"][0]["reproduce"] == "npx playwright test quotes.spec.js:5" and rec["probes"][0]["failed_before"]
    assert rec["risk"]["route"] == "review" and any("probe" in g for g in rec["risk"]["gates"])
    assert (shop / "e2e" / "tests.json").read_text().count("title") == 5   # the working tree has the new tests back


def test_interrupted_run_restores_and_a_killed_run_is_recovered(shop, monkeypatch):
    app(shop, gold=12750)
    git(shop, "add", "app.json")
    (shop / "scratch.txt").write_text("untracked", encoding="utf-8")
    before = snapshot(shop)
    real = P.run_e2e

    def interrupted(root, pcfg, out_dir, label, grep=None):
        if label == "candidate":
            raise KeyboardInterrupt
        return real(root, pcfg, out_dir, label, grep)
    monkeypatch.setattr(P, "run_e2e", interrupted)
    with pytest.raises(KeyboardInterrupt):
        P.run(shop, ticket="SHOP-77")
    assert snapshot(shop) == before and P.read_lock(shop) is None

    monkeypatch.setattr(P, "restore", lambda *a, **k: [])      # a hard kill: the `finally` never gets to restore
    with pytest.raises(KeyboardInterrupt):
        P.run(shop, ticket="SHOP-77")
    monkeypatch.undo()
    assert not (shop / "scratch.txt").exists() and P.read_lock(shop)["phase"] == "candidate"
    with pytest.raises(P.ProofError, match="specanvil proof restore"):
        P.run(shop, ticket="SHOP-77")                           # refuses to stack a second run on top
    from specanvil.cli import main
    assert main(["proof", "restore", "--path", str(shop)]) == 0
    assert snapshot(shop) == before and P.read_lock(shop) is None
    assert P.restore(shop) == ["No unfinished proof run: nothing to restore."]


def test_files_created_during_a_run_are_kept_not_deleted(shop):
    app(shop, gold=12750, junk=True)                     # the tests write leftover.txt, which is not ignored
    git(shop, "add", "app.json")
    rec = P.run(shop, ticket="SHOP-77")
    assert any("leftovers" in n and "leftover.txt" in n for n in rec["notes"])
    stashes = git(shop, "stash", "list")
    assert stashes.count("leftovers") == 1 and stashes.count("specanvil-proof") == 1   # only the leftovers remain
    assert snapshot(shop)["status"] == ["M  app.json"]


def test_refuses_to_start_when_it_cannot_compare(shop, tmp_path):
    with pytest.raises(P.ProofError, match="Nothing is staged"):
        P.run(shop)
    with pytest.raises(P.ProofError, match="no uncommitted change"):
        P.run(shop, scope="all")
    assert not git(shop, "status", "--porcelain")
    app(shop, gold=1)
    cfg = shop / ".specanvil" / "config.json"
    data = json.loads(cfg.read_text())
    data["proof"]["e2e_cmd"] = "npx playwright test; rm -rf ."
    cfg.write_text(json.dumps(data))
    before = snapshot(shop)
    with pytest.raises(P.ProofError, match="shell operators"):
        P.run(shop, scope="all")
    assert snapshot(shop) == before                       # refused after the stash was made: still restored
    data["proof"].update(e2e_cmd=f'"{sys.executable}" fake_e2e.py', auto_max_score=100)
    cfg.write_text(json.dumps(data))
    rec = P.run(shop, ticket="SHOP-77", scope="all")      # a change that loosens its own check goes to a person
    assert any("edits the proof settings" in g for g in rec["risk"]["gates"]) and rec["risk"]["route"] == "review"
    git(shop, "checkout", "--", ".specanvil/config.json", "app.json")
    for f in R.records_dir(shop).glob("*.json"):
        f.unlink()
    assert not git(shop, "status", "--porcelain")


def test_calibration_drops_values_that_change_on_their_own(shop):
    app(shop, gold=12750, noisy=True)                    # "rand" differs on every call, with or without a change
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    assert sum(d["path"] == "$.rand" for d in rec["behaviour"]["differences"]) == 3 and rec["risk"]["route"] == "review"
    git(shop, "commit", "-qam", "noisy app")
    app(shop, gold=12000, noisy=True)
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus", calibrate=True)
    assert not any(d["path"] == "$.rand" for d in rec["behaviour"]["differences"])
    assert rec["risk"]["route"] == "auto" and any("dropped" in n for n in rec["not_checked"])


def test_evidence_chain_records_decisions_and_detects_tampering(shop):
    from specanvil.cli import main
    app(shop, gold=12750, silver=13500)
    assert main(["proof", "run", "SHOP-77", "--scope", "all", "--author-model", "opus", "--json", "--path", str(shop)]) == 3
    with pytest.raises(ValueError, match="--reason is required"):
        R.decide(shop, "", "approve", "Dana", "")
    assert main(["proof", "approve", "--by", "Dana Reviewer", "--reason", "silver change agreed with the client",
                 "--path", str(shop)]) == 0
    rec = R.find_record(shop)
    assert R.status_of(shop, rec) == "approved by Dana Reviewer"
    assert R.decisions_for(shop, rec["id"])[0]["overrides_routing"] is True
    assert "approved by Dana Reviewer" in R.render_md(shop, rec) and "overrides the routing" in R.render_md(shop, rec)
    git(shop, "add", "-A")
    git(shop, "commit", "-qm", "gold and silver")
    app(shop, gold=12000, silver=13500)
    assert main(["proof", "run", "SHOP-77", "--scope", "all", "--author-model", "opus", "--path", str(shop)]) == 0
    assert [f["path"] for f in R.find_record(shop)["change"]["files"]] == ["app.json"]
    R.decide(shop, "", "hold", "Sam", "want a second look")       # a person can also stop what the engine cleared
    assert R.status_of(shop, R.find_record(shop)) == "held for review by Sam"
    assert R.verify_chain(shop) == [] and main(["proof", "verify", "--path", str(shop)]) == 0
    assert main(["proof", "list", "--path", str(shop)]) == 0 and main(["proof", "show", "1", "--path", str(shop)]) == 0

    files = sorted(R.records_dir(shop).glob("*.json"))
    assert len(files) == 4
    first = json.loads(files[0].read_text(encoding="utf-8"))
    first["risk"]["route"] = first["decision"]["route"] = "auto"          # quietly clear the risky change
    files[0].write_text(json.dumps(first, indent=2), encoding="utf-8")
    assert any("edited after it was written" in e for e in R.verify_chain(shop))
    first["hash"] = R.record_hash(first)                                 # re-hash it: the next record no longer follows
    files[0].write_text(json.dumps(first, indent=2), encoding="utf-8")
    assert any("chain broken" in e for e in R.verify_chain(shop))
    files[2].unlink()
    assert any("missing" in e for e in R.verify_chain(shop)) and main(["proof", "verify", "--path", str(shop)]) == 1


def test_intent_contract_is_checked_before_it_is_used(shop):
    assert R.validate_intent(INTENT) == []
    bad = {"ticket": "", "kind": "feature", "summary": "x", "unclear": "no", "rules": [
        {"id": "R1", "expect": "maybe", "text": "short", "match": {"url": "x"}},
        {"id": "R1", "expect": "change", "text": "A rule with no way to recognise it."}]}
    errs = " ".join(R.validate_intent(bad))
    for part in ['"ticket"', '"expect" must be', "used twice", '"match" may only hold', 'needs a "match"', '"unclear" must be']:
        assert part in errs
    assert "at least one" in " ".join(R.validate_intent({"ticket": "T", "summary": "s", "rules": []}))
    assert R.validate_intent({"ticket": "T", "summary": "s", "kind": "refactor", "rules": []}) == []
    from specanvil.cli import main
    assert main(["proof", "intent", "SHOP-77", "--path", str(shop)]) == 0
    assert main(["proof", "intent", "OTHER-1", "--path", str(shop)]) == 1
    (shop / "specs" / "features" / "SHOP-77-gold-discount").mkdir(parents=True)
    assert R.intent_path(shop, "SHOP-77").relative_to(shop).as_posix() == "specs/features/SHOP-77-gold-discount/intent.json"
    R.intent_path(shop, "SHOP-77").write_text(json.dumps(bad), encoding="utf-8")
    app(shop, gold=1)
    before = snapshot(shop)
    with pytest.raises(ValueError, match="not a valid intent contract"):
        P.run(shop, ticket="SHOP-77", scope="all")
    assert snapshot(shop) == before                       # refused before anything was stashed
    unclear = dict(INTENT, unclear=["Does the limit include tax?"])
    R.intent_path(shop, "SHOP-77").write_text(json.dumps(unclear), encoding="utf-8")
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    assert any("unclear" in g for g in rec["risk"]["gates"])


def test_masking_json_diff_and_older_trace_layout(tmp_path):
    m = P.Masker(dict(P.PROOF_DEFAULTS, app_url="http://shop.test:8080", ignore_urls=["GET /health*"],
                      mask_patterns=[r"ord_[a-z0-9]+"]))
    assert m.value({"createdAt": "x", "id": "6f1c7a52-0d0f-4f0e-9a52-3b0a6c1d2e3f", "note": "at 2026-11-16T07:23:50.123Z",
                    "items": [{"ref": "ord_9xk2", "requestId": 7}], "total": 5}) == \
        {"createdAt": "<masked>", "id": "<uuid>", "note": "at <time>", "items": [{"ref": "<masked>", "requestId": "<masked>"}],
         "total": 5}
    assert m.value({"user": "a", "Password": "hunter22", "note": "key sk-abcdefghijklmnopqrstuvwx"}) == \
        {"user": "a", "Password": "<masked>", "note": "key [REDACTED API KEY]"}
    assert m.url("http://shop.test:8080/api/orders/6f1c7a52-0d0f-4f0e-9a52-3b0a6c1d2e3f?b=2&a=1") == "/api/orders/<uuid>?a=1&b=2"
    assert m.url("https://pay.example.com/charge") == "https://pay.example.com/charge" and m.ignored("GET /healthz")
    assert P.json_diff({"a": 1, "b": [1, 2], "c": {"d": "x"}}, {"a": 1, "b": [1, 3, 4], "c": {}, "e": None}) == [
        ("$.b.length", 2, 3), ("$.b[1]", 2, 3), ("$.c.d", "x", P.ABSENT), ("$.e", P.ABSENT, None)]
    assert P.json_diff(1, True) == [("$", 1, True)] and P.json_diff({"a": [1]}, {"a": [1]}) == []
    z = tmp_path / "trace.zip"
    with zipfile.ZipFile(z, "w") as f:                    # older Playwright: bodies by sha1, POST data inline
        f.writestr("resources/abc123", '{"ok": true}')
        f.writestr("trace.network", json.dumps({"snapshot": {"_resourceType": "xhr", "request": {
            "method": "POST", "url": "http://localhost:3000/api/orders", "postData": {"mimeType": "application/json", "text": '{"qty": 2}'}},
            "response": {"status": 201, "content": {"mimeType": "application/json", "_sha1": "abc123"}}}})
            + "\n" + json.dumps({"snapshot": {"_resourceType": "image", "request": {"method": "GET", "url": "http://localhost:3000/a.png"},
                                              "response": {"status": 200, "content": {"mimeType": "image/png"}}}}) + "\nnot json\n")
    assert P.trace_exchanges(z, m) == [{"endpoint": "POST /api/orders", "status": 201, "request": {"qty": 2}, "body": {"ok": True}}]
    assert P.trace_exchanges(tmp_path / "missing.zip", m) == []
    argv, env = P.e2e_argv(tmp_path, dict(P.PROOF_DEFAULTS, e2e_cmd="npm run e2e"), tmp_path / "o", "@SHOP-77")
    assert argv[1:4] == ["run", "e2e", "--"] and argv[-2:] == ["--grep", "@SHOP-77"] and "--trace" in argv
    assert "--" not in P.e2e_argv(tmp_path, dict(P.PROOF_DEFAULTS, e2e_cmd="npx playwright test"), tmp_path / "o", None)[0]
    assert env["PLAYWRIGHT_JSON_OUTPUT_FILE"].endswith("report.json")


def test_proof_shows_up_in_the_report_the_pr_comment_and_the_generated_workflows(shop, monkeypatch):
    from specanvil.bootstrap import run
    from specanvil.index import CodeIndex
    from specanvil.report import pr_comment, write_report
    monkeypatch.setenv("SPECANVIL_HOME", str(shop.parent / "home"))
    res = run(shop, offline=True, agents="claude")
    assert res["agents"] == ["claude"]
    for rel in [".claude/commands/sa/proof.md", ".claude/agents/intent-verifier.md", ".claude/agents/adversary.md"]:
        assert (shop / rel).is_file(), rel
    verifier = (shop / ".claude/agents/intent-verifier.md").read_text(encoding="utf-8")
    assert "\ntools: Write\n" in verifier and "\nmodel: sonnet\n" in verifier   # cannot read code; model from the profile
    assert "/sa:proof" in (shop / ".claude/commands/sa/flow.md").read_text(encoding="utf-8")
    git(shop, "add", "-A")
    git(shop, "commit", "-qm", "specanvil init")
    app(shop, gold=12750, silver=13500)
    rec = P.run(shop, ticket="SHOP-77", scope="all", author_model="opus")
    ix = CodeIndex(shop)
    ix.build()
    page = write_report(shop, ix).read_text(encoding="utf-8")
    assert "Proof of changes" in page and rec["id"] in page and "waiting for review" in page and "evidence chain intact" in page
    assert "Proof of this change" not in pr_comment(shop, ix, "HEAD")           # the record is not committed yet
    git(shop, "add", "-A")
    git(shop, "commit", "-qm", "SHOP-77")
    text = pr_comment(shop, ix, "HEAD~1")
    assert "Proof of this change (1 record)" in text and "waiting for review" in text and "Evidence chain intact." in text
    assert "GET /api/quote?customer=silver&qty=6 $.total" in text
