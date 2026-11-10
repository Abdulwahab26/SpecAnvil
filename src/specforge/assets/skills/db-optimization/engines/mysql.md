## MySQL / MariaDB
- `EXPLAIN ANALYZE` (8.0.18+) or `EXPLAIN FORMAT=JSON`; red flags: `type: ALL`, `Using filesort`, `Using temporary`, large `rows` examined.
- InnoDB clustered index: keep primary keys short and monotonic; secondary indexes carry the PK.
- Leftmost-prefix rule for composite indexes; covering index shows `Using index`.
- `performance_schema` / `sys.statements_with_full_table_scans`, slow query log with `long_query_time`.
- Online DDL: `ALGORITHM=INPLACE, LOCK=NONE` where supported, or gh-ost / pt-online-schema-change for large tables.
