---
description: Execute a module's unit-test milestones one by one (runs on your executor model from the profile), each verified by running its tests
argument-hint: <module> [milestone-id | all]
---

# Run unit-test milestones: $ARGUMENTS

Delegate to the **unit-test-executor** subagent (it runs on the executor model in your SpecAnvil profile). Give it the module name and, if provided, the single milestone id; otherwise it loops until every milestone is done or blocked.

Rules you enforce as the orchestrator:
- The executor changes status only through `update_test_milestone`; a milestone counts as done only when that tool accepted it (it runs the tests itself).
- If a milestone comes back `blocked`, do not let the executor improvise. Summarise the blocker; offer to send it back to **unit-test-planner** to revise that milestone.
- Large modules: after every 3 milestones, give the user a one-line progress update.

Final report: table of milestones with status, tests added, pass/fail, and blockers; then run the module's full test command once and report the result.
