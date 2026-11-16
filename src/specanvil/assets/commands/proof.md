---
description: Prove a change before it merges - intent contract from the ticket, before/after Playwright comparison, adversarial probes, risk routing, evidence record
argument-hint: <ticket-id | feature-id> [--scope staged|all]
---

# Proof: $ARGUMENTS

You are the **proof orchestrator**. You collect evidence about a change; you do not judge it and you never approve it. The comparison, the risk score and the routing are computed by `specanvil proof`, not by you.

Independence rules (enforced where code can, binding everywhere else):
- The intent contract is written **from the ticket only**, by the `intent-verifier` agent, which has no tool that can read code. Do not paste code, diffs or file names from the change into its prompt.
- The agent that wrote the change does not write the contract or the probes.
- Never run `specanvil proof approve`, `reject` or `hold`: those record a **person's** decision.

## 1. Intent contract (from the ticket, before looking at the change)
1. Get the ticket text: from the ticket MCP ({{tickets}}) if connected, else the ticket section of `{{features_dir}}/<id>*/requirements.md`, else ask the user for it.
2. Get a description of the public behaviour **as it is on the last commit**: the route list from `run_tool("entry_points")` and the request/response shapes from the feature's `design.md` or the API docs. Routes and shapes only, no implementation code.
3. Delegate to `intent-verifier` with exactly: the ticket text, that behaviour description, the E2E test titles that already exist, and the output path printed by `specanvil proof intent <id>` (it prints where the contract is expected).
4. Run `specanvil proof intent <id>`. Fix shape errors by sending them back to `intent-verifier`. If the contract lists `unclear` questions, show them to the user now: the change will be routed to a person until they are answered.

## 2. Adversarial probes
Delegate to `adversary` with the ticket text and the intent contract. It writes Playwright tests marked `@probe` under the E2E folder that try to break the change: boundaries of every number in the ticket, empty/huge/malformed input, the other roles or customer types the ticket did not mention, authorisation on changed endpoints, and repeated or out-of-order calls. A probe asserts what the ticket requires, never what the code currently does.

## 3. Run the proof
- Stage what should be proven (`git add`), or use `--scope all` for everything uncommitted.
- Run in the shell (it can take as long as two E2E runs):
  `specanvil proof run <id> --author-model <model that wrote the change> --author-tool <tool>`
  Add `--calibrate` if the first report is full of values that change on their own.
- The run puts the change in a git stash, runs `{{e2e_cmd}}` on the last commit, restores the change and runs the same tests again. If it is interrupted, run `specanvil proof restore` before anything else.
- Exit code 0: cleared automatically. Exit code 3: routed to a person. Exit code 1: the run could not be made; read the message.

## 4. Report
Show the user the output of `specanvil proof show`: status, the differences nobody asked for, failed probes with their reproduce commands, and the "Not checked" list. Do not soften or summarise away a finding.
- **Cleared automatically**: say so and suggest `/sa:verify <id>`.
- **Routed to a person**: give the brief, then stop. If the findings are real defects, route each to the engineer agent that owns the code, then run this workflow again; do not edit the intent contract or delete a probe to make a finding go away. A reviewer who accepts the change records it with `specanvil proof approve <record> --by "<name>" --reason "<why>"`.
