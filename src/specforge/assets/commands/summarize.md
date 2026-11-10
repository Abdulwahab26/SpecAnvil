---
description: Add AI one-line summaries for undocumented code to the index (runs on your summarizer model; improves search, outlines and semantic results)
argument-hint: [module | folder] [count, default 200]
---

# Summarize: $ARGUMENTS

Delegate to the **summarizer** subagent (it runs on the summarizer model in your profile, a cheap one by default). Give it the target (or the whole repo) and the count.

When it finishes:
1. Report how many symbols were summarised and how many remain (`summaries_needed` with limit 1 shows if any are left).
2. If semantic search is enabled, run `specforge embed` in the shell so the summaries feed the embeddings too (only changed symbols are re-embedded).

Summaries are stored by code hash: they stay valid until that code changes, and they are shown with a ≈ marker so nobody mistakes them for written docs.
