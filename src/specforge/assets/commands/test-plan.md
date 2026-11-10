---
description: Deep-analyse a module for unit testing and produce executable milestones (runs on your planner model from the profile)
argument-hint: <module | folder | file>
---

# Unit-test plan: $ARGUMENTS

Delegate this to the **unit-test-planner** subagent (it runs on the planner model in your SpecForge profile; see `get_profile`). Pass it the target `$ARGUMENTS` and nothing else from this conversation.

When it returns:
1. Run `validate_test_plan("$ARGUMENTS")` yourself; if it is not valid, send the problems back to the planner.
2. Show the user the milestone table (id, title, targets, complexity, key challenge) and the expected coverage gain.
3. Tell them the next step: `/sf:test-run $ARGUMENTS` (executes on the cheaper executor model). Models can be changed with `specforge profile set models.test_planner <model>` / `models.test_executor <model>`.
