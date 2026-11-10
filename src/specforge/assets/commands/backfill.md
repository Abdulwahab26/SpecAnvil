---
description: Pull done / in-progress / in-review tickets from your ticket MCP and create linked feature specs for them (opt-in, resumable)
argument-hint: [statuses e.g. "Done,In Review"] [--since YYYY-MM-DD] [--limit N]
---

# Backfill specs from ticket history: $ARGUMENTS

1. **Opt-in check**: `get_profile`. If `backfill.enabled` is false, explain what will happen (read-only queries to the ticket system, one spec folder per ticket) and ask the user to confirm. If they agree, run `specforge profile set backfill.enabled true` in the shell. Never proceed without this.
2. **Settings**: statuses, since and limit come from `$ARGUMENTS` if given, else from the profile's `backfill` section.
3. **Ticket system**: use whichever ticket MCP is connected ({{tickets}}). Build the query natively:
   - Jira: JQL `status in (<statuses>) [AND updated >= <since>] ORDER BY updated DESC`
   - Azure DevOps: WIQL on `[System.State] IN (<states>)` (map Done→Closed/Done, In Review→Resolved/In Review as the process template uses)
   - GitHub: issues `is:closed reason:completed` for Done, linked open PRs for In Review, `is:open label:"in progress"` or project status for In Progress
   - Linear / GitLab: filter by workflow state / labels.
   Paginate up to the limit. If no ticket MCP is connected, offer `specforge connect <name>` and stop.
4. **Resume**: `backfill_progress()` returns keys already done and keys that already have specs; skip both. Record the query with `backfill_progress(query=...)`.
5. **Cross-check with the repo**: `ticket_keys()` shows which keys appear in git history; prioritise those (they have code to link).
6. **Process in batches** of `backfill.batch_size`: run up to 3 **ticket-archaeologist** subagents in parallel, each with its list of keys. After each batch, call `backfill_progress(done=[...], failed={key: reason}, skipped=[...])`.
7. **Index**: update `{{specs_dir}}/features/_index.md` with one row per ticket: key, title, status, spec link, linked refs count.
8. **Report**: counts by status, specs written, failures, and tickets with no linked code (candidates for `/sf:learn` or human review). Suggest `specforge drift --lock` after a human review.
