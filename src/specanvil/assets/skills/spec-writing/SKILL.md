---
name: spec-writing
description: Format and rules for specs in this repo (requirements, design, tasks, test cases, module specs, code maps). Use when creating or editing anything under the specs folder.
---

# Writing specs here

Layout:
```
{{specs_dir}}/
  project.md          # stack, architecture, modules, commands (generated, then curated)
  conventions.md      # how code is written here (learned from the code)
  modules/<name>.md   # one per module: purpose, public API, flows, rules
  features/<ID>-<slug>/
    requirements.md   # status, ACs (Gherkin), NFRs, scope, Code map
    design.md         # options, architecture, API contract, data model, UI, test strategy
    tasks.md          # ordered, verifiable tasks mapped to ACs
    test-cases.md/.csv# manual QA cases with traceability
    e2e.md            # Playwright coverage and results
    db-review.md      # query plans and index changes
    pr.md             # PR description
```

Rules:
- First lines of requirements.md: `# <ID>: <title>` then `status: groomed|designed|planned|in-progress|verified|shipped`.
- ACs are numbered `AC-n` and written as Given/When/Then. Each must be testable on its own.
- Reference code only as `` `path::Qualname` `` (e.g. `` `src/orders/service.ts::OrderService.create` ``). These are checked by `spec_drift`; plain prose references are not.
- Keep each file under ~1,500 tokens; split by section rather than writing one huge file. Tools read specs by section.
- Specs describe intended behaviour. When code disagrees, either fix the code or update the spec in the same change — never leave them diverged.
