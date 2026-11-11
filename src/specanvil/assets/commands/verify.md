---
description: Verify spec ↔ code ↔ tests for a feature (AC coverage, drift, quality gates) and lock the spec when it passes
argument-hint: <feature-id>
---

# Verify: $ARGUMENTS

You are the **reviewer**. Be strict; your job is to find what is missing.

1. `changed_symbols` (against the main branch if on a feature branch) to see what actually changed.
2. For every AC in `requirements.md`: find the code that implements it and the test(s) that prove it (unit, integration or E2E). Build a table AC → code → tests → status (✅ / ⚠️ partial / ❌ missing).
3. `spec_drift` — every `` `path::Qualname` `` in the feature's specs must exist; new symbols in the diff should appear in a Code map.
4. Review the diff for: correctness against the design, error handling, security (authz, injection, secrets, PII in logs), performance (N+1, unbounded queries), accessibility, and convention violations from `{{specs_dir}}/conventions.md`.
5. Run gates: `{{test_cmd}}`, `{{lint_cmd}}`, and the E2E tests tagged with the feature id.

Output a verdict: **PASS** or **CHANGES NEEDED** with a numbered list of fixes (file:line). On PASS: set `status: verified`, and tell the user to run `specanvil drift --lock` to record the verified state.
