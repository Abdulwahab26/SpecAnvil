## SQLite
- `EXPLAIN QUERY PLAN`: `SCAN table` = full scan, `SEARCH ... USING INDEX` = good; `USE TEMP B-TREE FOR ORDER BY` = missing sort index.
- Run `ANALYZE` so the planner has statistics; enable WAL mode for concurrent reads.
- Batch writes in transactions; one transaction per row is the most common slowdown.
