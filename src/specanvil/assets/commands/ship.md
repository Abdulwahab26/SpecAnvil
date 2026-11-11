---
description: Prepare a verified feature for review - PR description, changelog entry, ticket update, release notes
argument-hint: <feature-id>
---

# Ship: $ARGUMENTS

Only proceed if the feature's `requirements.md` has `status: verified`; otherwise run `/sa:verify` first.

1. Use `changed_symbols` against the main branch for an accurate change list.
2. Write `pr.md` in the feature folder:
   - Title: `<ticket-id>: <summary>`
   - Why (one paragraph, linked to the ticket), What changed (grouped FE / BE / DB), How it was tested (AC coverage table, test commands and results), Screenshots (from `/sa:e2e`), Migration & rollback, Risks, Reviewer checklist.
3. Add a changelog entry in the repo's existing changelog format (if one exists).
4. Draft a short, non-technical release note.
5. If a ticket MCP ({{tickets}}) is connected: **ask**, then comment the PR summary on the ticket and move it to the review status.

Do not push, open the PR, or merge unless the user explicitly asks. Set `status: shipped` only after the user confirms it merged.
