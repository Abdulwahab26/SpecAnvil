---
description: Generate manual QA test cases with full AC traceability, using formal test design techniques; export to CSV for test tools
argument-hint: <feature-id>
---

# QA test cases: $ARGUMENTS

You are the **QA engineer**. Load the **test-case-design** skill. Read the feature's `requirements.md` (ACs, NFRs) and the UI/API sections of `design.md`.

## Design the cases
Apply, and name, the technique for each group:
- **Equivalence partitioning** and **boundary value analysis** for every input.
- **Decision tables** for rules with several conditions (pricing, permissions, feature flags).
- **State transitions** for anything with a lifecycle (orders, tickets, sessions).
- **Error guessing / negative** cases: invalid input, unauthorised, expired session, double submit, network failure, slow responses.
- **Cross-cutting**: accessibility (keyboard, screen reader, contrast), responsive breakpoints, browsers, localisation, data privacy.

## Write `test-cases.md`
A table per AC:

| ID | Title | Type | Priority | Preconditions | Steps | Test data | Expected result | AC |
|----|-------|------|----------|---------------|-------|-----------|-----------------|----|

- IDs `TC-<feature>-<n>`; Type = Functional / Negative / Boundary / UI / A11y / Security / Performance; Priority P1-P3.
- Steps are numbered and atomic; expected results are observable (text, state, status code), never "works correctly".
- Mark cases that should be automated as `auto: e2e` or `auto: api` so `/sf:e2e` picks them up.

Finish with a **traceability matrix** (AC → TC ids) and a **coverage gap** list. Also write `test-cases.csv` with the same columns, importable into TestRail, Xray, Zephyr or Azure Test Plans. If a ticket MCP is connected, ask before attaching the cases to the ticket.
