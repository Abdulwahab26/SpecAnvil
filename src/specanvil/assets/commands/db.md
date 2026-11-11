---
description: Optimise database access for any engine - find slow/N+1 queries, read real plans, propose indexes and safe migrations
argument-hint: <feature-id | file | symbol | raw query | "audit">
---

# Database optimisation: $ARGUMENTS

You are the **database optimiser**. Detected engines: **{{databases}}** (see `project_profile`). Load the **db-optimization** skill first; it has the engine-specific checklist.

## 1. Find the queries
- Feature id → the Code map symbols in its specs. File/symbol → `get_symbol`. Raw query → use it directly.
- `audit` → scan with `search_code` for ORM and SQL patterns (e.g. `.query(`, `.find(`, `findBy`, `SELECT `, `@Query`, `include:`, `.objects.`, `Where(`), then `find_references` on repository/DAO methods to see which run inside loops (N+1) or per request.

## 2. Get evidence, not guesses
- If a database MCP is connected (postgres / mysql / sqlserver / mongodb / sqlite), run **read-only** plans: `EXPLAIN (ANALYZE, BUFFERS)` on Postgres, `EXPLAIN ANALYZE` on MySQL 8+, estimated/actual plans on SQL Server, `explain("executionStats")` on MongoDB. Use the server's index-advisor / top-queries tools where available.
- Never run DDL or writes against a shared database. If no DB MCP is connected, reason from schema/migrations and say the plan is unverified.

## 3. Report (write to `{{features_dir}}/<id>/db-review.md` or `{{specs_dir}}/db-audit.md`)
For each finding: query location (`path:line`), problem (full scan, N+1, missing/unused index, implicit cast, OFFSET pagination, SELECT *, lock risk), evidence (plan excerpt, rows examined), fix, expected impact, risk.

## 4. Fixes
- Code fixes (eager loading, batching, projection, keyset pagination) in the repo's ORM style.
- Index proposals with exact DDL, written as a **migration** in the repo's migration tool, including rollback and online/concurrent creation where supported. Check write amplification and existing overlapping indexes first.
- Re-run the plan after the fix when possible and show before/after.
