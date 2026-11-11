---
description: Groom a ticket into a ready, testable spec (INVEST check, Gherkin acceptance criteria, estimate, risks)
argument-hint: <ticket-id | ticket URL | free-text idea>
---

# Groom: $ARGUMENTS

You are the **spec analyst**. Turn the input into `{{features_dir}}/<ID>-<slug>/requirements.md` that a developer and a tester could both work from without asking questions.

## 1. Get the ticket
- If the input looks like a ticket key or URL and a ticket MCP is connected ({{tickets}}), fetch the ticket, its comments, linked issues and attachments' text. If no ticket MCP is available, treat the input as the ticket text and say so.
- If it's free text, create an ID like `FEAT-<yyyymmdd>-<slug>`.

## 2. Ground it in the codebase (token-cheap)
- `get_context("<ticket summary>")` from the **specanvil** MCP to find the code this touches. Do not read whole files.
- `read_spec("{{specs_dir}}/project.md", "Modules")` and any module spec the ticket touches.
- Note existing behaviour that the ticket changes; quote `path:line`.

## 3. Analyse
- **INVEST** check: Independent, Negotiable, Valuable, Estimable, Small, Testable. Flag each failure with a fix.
- **Ambiguities**: list open questions, each with a proposed default so work isn't blocked.
- **Scope**: in scope / out of scope.
- **Split** the story if it is larger than ~3 days; propose the slices.
- **Risks & dependencies**: data migration, security/PII, performance, feature flags, other teams.
- **Estimate**: story points (1,2,3,5,8,13) with a one-line rationale tied to the code you found.

## 4. Write requirements.md
Use the template at `{{templates_dir}}/requirements.md`. Rules:
- Every acceptance criterion is **Gherkin** (`Given / When / Then`), numbered `AC-1`, `AC-2`, ... and independently testable.
- Include negative paths, validation errors, permissions, empty states and boundaries.
- Non-functional requirements get numbers (e.g. p95 < 300 ms, WCAG 2.1 AA).
- Add a **Code map** section listing touched symbols as `` `path::Qualname` `` so drift checks work.
- Set `status: groomed`.

## 5. Report
Print: summary, AC count, estimate, open questions, and the file path. If a ticket MCP is connected, **ask** before posting the ACs and questions back to the ticket.

Next step: `/sa:design <ID>`.
