## Every engine
- Read real plans before changing anything; compare estimated vs actual rows.
- N+1: a query inside a loop or lazy relation access per row → eager load / batch `IN (...)` / join.
- Select only needed columns; avoid `SELECT *` on wide tables and in APIs.
- Paginate with keyset (`WHERE id > :last ORDER BY id LIMIT n`) instead of large `OFFSET`.
- Indexes follow the query: equality columns first, then range, then sort; covering indexes for hot reads. Every index costs writes and storage — check for overlapping/unused ones first.
- Functions or implicit casts on indexed columns (`LOWER(email)`, comparing varchar to int) disable the index; use expression indexes or fix types.
- Migrations on big tables: add nullable column → backfill in batches → add constraint; avoid long exclusive locks.
- Connection pooling sized to the DB, not to the app's thread count.
