---
name: test-case-design
description: Formal manual test design techniques and the test case format used here. Use for /sa:qa and whenever writing test cases or test plans.
---

# Test case design

## Techniques (name the one you used per case group)
- **Equivalence partitioning**: split inputs into classes that should behave the same; one case per class (valid and invalid).
- **Boundary value analysis**: for every range test min-1, min, min+1, max-1, max, max+1; for strings: empty, 1 char, max length, max+1, unicode, whitespace-only.
- **Decision table**: list conditions as columns, every relevant combination as a row, expected action per row; collapse impossible rows.
- **State transition**: draw states and events; cover every valid transition once and the most likely invalid ones (e.g. ship a cancelled order).
- **Pairwise**: when many independent options (browser × role × locale), cover all pairs instead of all combinations.
- **Error guessing**: double-click submit, back button after submit, session expiry mid-flow, paste huge input, slow/failed network, concurrent edits, timezone/DST edges, rounding of money.

## Quality bar for a case
- One behaviour per case; title says the behaviour ("Rejects coupon after expiry date").
- Preconditions include user role and data state.
- Steps are atomic and numbered; test data is explicit.
- Expected result is observable: exact message, status code, UI state, DB/record effect.
- Linked to an AC; priority P1 = blocks release, P2 = important, P3 = nice to have.

## Non-functional checklist
Accessibility (keyboard-only path, focus order, screen-reader names, 200% zoom, contrast), responsive (mobile/tablet/desktop), security (authz on every endpoint, IDOR by changing ids, input injection), performance (large lists, slow 3G), privacy (PII not in URLs/logs).
