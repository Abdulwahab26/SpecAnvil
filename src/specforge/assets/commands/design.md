---
description: Produce a technical design for a groomed feature (architecture, API contract, data model, UI breakdown, ADR)
argument-hint: <feature-id>
---

# Design: $ARGUMENTS

You are the **architect**. Read `{{specs_dir}}/features/$ARGUMENTS*/requirements.md` (use `read_spec` by section) and write `design.md` next to it.

## Inputs (token-cheap)
- `get_context` with the feature summary; `file_outline` on the 3-6 most relevant files; `get_symbol` only for code you must reason about line by line.
- `read_spec("{{specs_dir}}/conventions.md")` and the relevant module specs.
- If a **Figma** MCP is connected and the ticket links a frame, pull the frame's structure, variables and component names.
- Use `impact_of` on every shared file you plan to change.

## design.md must contain
1. **Context & goals**: one paragraph, link each goal to AC ids.
2. **Options considered**: at least two, with trade-offs; pick one and say why (a mini ADR).
3. **Architecture**: a Mermaid diagram (component or sequence) of the change.
4. **API contract**: for each endpoint/event: method, path, request/response schema (OpenAPI-style YAML), status codes, error model, auth, idempotency.
5. **Data model**: tables/collections/fields changed, indexes, migration plan (forward + rollback), backfill, expected row counts. For {{databases}}, call out query patterns and the index each one uses.
6. **Frontend**: component tree, state ownership, loading/empty/error states, accessibility notes, `data-testid` plan for E2E.
7. **Backend**: layers touched (controller/service/repository...), validation, transactions, background jobs.
8. **Security & privacy**: authz rules, input validation, PII handling, OWASP concerns.
9. **Observability**: logs, metrics, alerts.
10. **Test strategy**: unit / integration / E2E split mapped to ACs.
11. **Code map**: every symbol to add or change as `` `path::Qualname` `` (new ones marked *new*).

Follow the conventions spec; if you must deviate, say why. Set `status: designed` in requirements.md.

Next step: `/sf:plan $ARGUMENTS`.
