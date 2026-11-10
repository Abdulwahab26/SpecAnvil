---
description: Show a dashboard of all features by phase, spec drift, and token savings
---

# Spec status

1. `list_specs` from the specforge MCP; group features in `{{specs_dir}}/features/` by `status` (groomed → designed → planned → in-progress → verified → shipped).
2. For each in-progress feature, count ticked vs open tasks in `tasks.md` (read only the tasks section).
3. `spec_drift` — summarise changed/missing refs and modules with no spec coverage.
4. `token_savings` — total and per-tool savings.

Print one compact table (feature, status, tasks done/total, ACs, last change) followed by the top 5 actions to take next.
