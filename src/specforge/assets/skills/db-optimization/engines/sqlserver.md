## SQL Server
- Actual execution plan (`SET STATISTICS IO, TIME ON`); look for Index/Table Scans, Key Lookups (add INCLUDE columns), implicit conversions (warning icon), spills, and parallelism skew.
- `sys.dm_exec_query_stats` + Query Store for top/regressed queries; `sys.dm_db_missing_index_details` as hints (review, don't blindly create).
- Parameter sniffing: compare plans for typical vs skewed parameters; consider `OPTION (RECOMPILE)` or plan guides sparingly.
- `CREATE INDEX ... WITH (ONLINE = ON)` on Enterprise/Azure SQL; filtered indexes for hot subsets.
