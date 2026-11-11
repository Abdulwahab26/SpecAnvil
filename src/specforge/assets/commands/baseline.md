---
description: Write baseline OpenSpec specs for code that no capability spec covers yet, from what the code does today
argument-hint: [module | folder] (default: the biggest uncovered modules)
---

# Baseline specs: $ARGUMENTS

You are the **spec analyst**. This repo uses OpenSpec. Some code has no capability spec; write one from the code's **current** behaviour so future changes have something to modify.

1. Find the gaps: `run_tool("openspec_gaps")` lists modules no spec mentions. Use the argument if one was given, otherwise take the largest three.
2. For each module, learn what it does with the code index, not by reading whole files: `repo_map`, `file_outline`, `get_context("<module> behaviour")`, `entry_points`, and `doc_facts` for endpoints, commands and settings.
3. Write `openspec/specs/<capability>/spec.md` in OpenSpec's format:
   - `# <capability> Specification`, `## Purpose` (one or two sentences), `## Requirements`.
   - Each `### Requirement: <name>` is followed by normative text using SHALL or MUST, then at least one `#### Scenario: <name>` with `- **WHEN** ...` and `- **THEN** ...` bullets.
   - Describe observable behaviour, not implementation. One capability per coherent area; keep names in kebab-case.
   - Under the Purpose, add a `## Code map` list of `` `path::Qualname` `` references for the main symbols, so drift checks can tell when the code moves on.
4. Mark anything you could not confirm from the code as an open question at the end of the Purpose; do not guess behaviour.
5. Check your work: `run_tool("openspec_check", {"target": "<capability>"})` must report no errors. If the OpenSpec CLI is installed, also run `openspec validate --specs`.
6. Report which capabilities you wrote, how many requirements and scenarios each has, and which scenarios have no test yet (`run_tool("openspec_coverage", {"target": "<capability>"})`).

These specs describe what the code does, which may include bugs. Say so in the summary and ask for review.
