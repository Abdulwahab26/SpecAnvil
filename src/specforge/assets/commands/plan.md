---
description: Break a designed feature into small, ordered, verifiable tasks mapped to acceptance criteria
argument-hint: <feature-id>
---

# Plan: $ARGUMENTS

Read `requirements.md` and `design.md` for the feature (section by section with `read_spec`). Write `tasks.md` next to them.

Rules for tasks:
- Each task is **≤ half a day**, touches a clear set of files, and ends in a check that can pass or fail (a test, a command, a screenshot).
- Format: `- [ ] T-<n> [FE|BE|DB|QA|OPS] <title> — files: <paths> — covers: AC-x — done when: <check>`.
- Order by dependency: contracts and migrations first, then backend, then frontend, then E2E.
- Mark tasks that can run **in parallel** with `(parallel)`; these can go to separate agents in `/sf:flow`.
- Include test tasks explicitly; no task is "write code" without its test.
- Add a final `T-last [QA] Verify` task: `{{test_cmd}}`, lint, and `/sf:verify $ARGUMENTS`.

End with a table: AC id → task ids, so no AC is uncovered. Set `status: planned`.

Next: `/sf:be $ARGUMENTS`, `/sf:fe $ARGUMENTS`, or `/sf:flow $ARGUMENTS` to run the whole team.
