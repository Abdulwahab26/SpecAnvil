---
name: unit-test-planning
description: Schema and rules for unit-test milestone plans (milestones.json) shared by the planner and executor agents. Use for /sf:test-plan and /sf:test-run.
---

# Unit-test milestone plans

Location: `{{specs_dir}}/tests/<module-slug>/milestones.json` (authoritative) + `test-plan.md` (summary).
The real file is strict JSON: the `//` comments below only explain the fields.
Validated by code: `validate_test_plan(module)` / `specforge tests validate <module>`.

```json
{
  "module": "orders",
  "test_framework": "pytest",
  "test_command": "pytest {test_file} -q",          // MUST contain {test_file}
  "coverage_baseline": 41.0,                          // from test_surface, null if unknown
  "coverage_target": 80,
  "conventions": "pytest + pytest-mock; fixtures in tests/conftest.py (db_session); freeze time with freezegun ...",
  "milestones": [
    {
      "id": "M1",
      "title": "Discount rules in calculate_discount",
      "priority": 1,
      "status": "todo",                               // todo | in_progress | done | blocked (set via tools only)
      "complexity": "S",                              // S | M | L
      "targets": ["services/api/app/routers/orders.py::calculate_discount"],
      "test_file": "services/api/tests/test_discount.py",
      "cases": [
        {"id": "M1-C1", "name": "SAVE10 gives 10% off", "kind": "happy",
         "given": "total=100.0, coupon='SAVE10'", "when": "calculate_discount is called", "then": "returns 90.0"},
        {"id": "M1-C2", "name": "unknown coupon is ignored", "kind": "edge",
         "given": "total=100.0, coupon='NOPE'", "when": "called", "then": "returns 100.0"}
      ],
      "fixtures": [],
      "mocks": [{"target": "app.routers.orders.get_session", "why": "no DB in unit tests", "how": "mocker.patch returning MagicMock"}],
      "challenges": [{"challenge": "rounding of money", "approach": "assert with == on round(x, 2) values, not floats from arithmetic"}],
      "done_when": ["all cases pass", "no network or DB access"],
      "escalate_if": ["function signature differs from plan"],
      "requires_production_change": false
    }
  ]
}
```

Case kinds: happy, edge, boundary, error. Every target must exist in the index (`path::Qualname`).
Good milestones are small enough for a small model to finish in one pass and specific enough that two different engineers would write nearly the same tests.
