# Planted changes for the proof demo

Each folder holds one version of `pricing.js` for ticket SHOP-77 ("gold customers get 15% on orders of 100.00 or more").
All three pass the fixture's own tests.

| Change | What it does | What proof should report |
|---|---|---|
| `safe` | Gold only, from 100.00 | Only asked-for differences; low risk; cleared automatically |
| `wrong` | Reads well, but also gives silver customers the bonus | A must-not-change violation for silver quotes; routed to a person |
| `boundary` | Uses `>` instead of `>=` | The boundary probe fails (an order of exactly 100.00); routed to a person |

Run one: `python tests/evals/run_proof_demo.py wrong`
