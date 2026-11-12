# Contributing to SpecAnvil

## Setup
```bash
pip install -e ".[semantic]" pytest
specanvil grammars python javascript typescript tsx java go kotlin --path .
pytest -q
```

## Rules of the road
- **Every behaviour change ships with a test.** Retrieval/ranking changes also run both evals:
  `python tests/evals/run_context_eval.py <full-stack-fastapi-template> tests/evals/context_fullstack_fastapi.json`
  and `tests/evals/run_semantic_eval.py`. CI fails if recall@any < 0.95 or MRR < 0.75.
- **Claims need numbers.** If a change affects tokens, speed or quality, update `docs/BENCHMARKS.md` with the measurement
  and how to reproduce it. Never tune on a held-out eval case without saying so.
- **Local-first.** No new network calls in indexing or query paths. Anything that downloads must be an explicit user
  command and documented in `SECURITY.md`.
- **Enforce in code, guide in prompts.** If a rule has a right answer (schema, safety, completeness), enforce it in a tool
  or hook; keep prompts for judgement.
- **Regex extractors are benchmarked.** Bound every pattern to a line (`[ \t]*`, not `\s*` across newlines) and time new
  patterns on a large repo (Django) before merging.
- Architecture decisions go in `docs/ARCHITECTURE.md` as an ADR (context, decision, why, trade-off).

## Commit style
`area: what changed` (e.g. `search: down-rank generated code in hybrid mode`). Reference issues as `#123`.
