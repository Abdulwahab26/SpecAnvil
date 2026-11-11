## PostgreSQL
- `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)`; look for Seq Scan on large tables, large `Rows Removed by Filter`, misestimates (actual ≫ estimated → `ANALYZE`, extended statistics), Sort/Hash spilling to disk (`work_mem`).
- `pg_stat_statements` for top queries by total time; `pg_stat_user_indexes` (idx_scan = 0) for unused indexes.
- `CREATE INDEX CONCURRENTLY` (outside a transaction); partial indexes for hot subsets (`WHERE status = 'pending'`); `INCLUDE` for covering; GIN for jsonb/arrays/full-text; BRIN for append-only time series.
- Watch for bloat and autovacuum lag on heavily updated tables; `idle in transaction` sessions holding locks.
