---
description: Quick path - write a lightweight spec for a small change without the full grooming flow
argument-hint: <short description of the change>
---

# Quick spec: $ARGUMENTS

For small changes (≤ 1 day). Create `{{specs_dir}}/features/<slug>/requirements.md` from the template with:
- 2-5 Gherkin ACs (include at least one negative case),
- a Code map of touched symbols found via `get_context("$ARGUMENTS")`,
- a 5-10 line task list at the bottom instead of separate design/plan files,
- `status: planned`.

Then implement it test-first, run `{{test_cmd}}`, and finish with `/sf:verify <slug>`.
