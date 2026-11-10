## MongoDB
- `explain("executionStats")`: want IXSCAN, `totalDocsExamined` ≈ `nReturned`; COLLSCAN or in-memory SORT are red flags.
- ESR rule for compound indexes: Equality fields, then Sort, then Range.
- Project only needed fields; avoid unbounded arrays and huge documents; use `$lookup` sparingly on hot paths.
- Profiler (`db.setProfilingLevel(1, { slowms: 100 })`) and `$indexStats` for unused indexes.
- Build indexes on large collections during low traffic; check write impact.
