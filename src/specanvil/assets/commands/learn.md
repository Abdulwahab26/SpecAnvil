---
description: Deep-learn this codebase - enrich the generated module specs, conventions and project skills with real understanding
argument-hint: [module name | "all"]
---

# Learn the codebase: $ARGUMENTS

`specanvil init` generated specs and skills from static analysis only (no AI, nothing sent anywhere). Now enrich them using your own understanding, cheaply.

For each module spec in `{{specs_dir}}/modules/` (or only the one named):
1. `read_spec` on it; `file_outline` on its files; `get_symbol` on the 3-5 most central symbols (most referenced via `find_references`, or entry points like controllers/routes/pages).
2. Fill in **Purpose**, **Responsibilities**, **Key flows** (numbered steps with `path::Qualname` refs), **Business rules** you can see in code (validation, limits, state machines), **Invariants**, and **Gotchas**.
3. Keep "Needs human review" items for anything you inferred but could not confirm. Reverse-engineered specs describe what the code *does*, not necessarily what it *should* do; flag suspected bugs separately.

Then improve the project skills in `.claude/skills/`:
- **<project>-conventions**: replace generic statements with concrete rules and one short real example each (path:line).
- **frontend-patterns** / **backend-patterns**: document how this repo builds a component, an endpoint, a repository, a test — the exact recipe a new developer would follow.

Keep every spec under ~1,500 tokens; split long ones. Finish with a list of the questions a human should answer.
