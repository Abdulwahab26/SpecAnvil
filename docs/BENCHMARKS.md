# Benchmarks

Machine: 2 vCPU cloud container, Python 3.13, SQLite 3 (WAL). Numbers are wall-clock for `CodeIndex.build()`.

## Indexing: Django (7,097 tracked files → 5,606 indexed, 44,211 symbols)

| Scenario | v0.1 | v0.2 | Change |
|---|---|---|---|
| Cold index | 10.2 s | 6.0 s (2 workers) · 8.1 s (1 worker) | 1.7× |
| No changes (refresh) | 1.62 s | **0.09 s** | 18× |
| One file edited | 1.68 s | **0.14 s** | 12× |
| File touched, content identical | ~1.6 s | 0.10 s | 16× |

What changed (see ADR-3): removed an O(n²) `set()` rebuild in the removal check; one `stat` per file with `(size, mtime_ns)` fingerprints instead of reading every file to sniff for binaries; set/suffix matching for built-in ignores instead of pathspec; BLAKE2b; batched `executemany` writes in one transaction; WAL + `synchronous=NORMAL`; process-pool parsing above 150 files.

## Search latency (Django)

| Query | Time |
|---|---|
| FTS symbol search | ~2 ms per query |
| `get_context` (4k budget) | ~12 ms |
| Text search, ripgrep path | 35–55 ms |
| Text search, pure Python, no match (full scan) | ~330 ms |

## Head-to-head with jCodeMunch (its own benchmark)

Same pinned repos, same 5 queries, same workflow, tiktoken cl100k: jCodeMunch 17,581 tokens · SpecForge **9,306** default (−47%) · **12,148** with every body in full (−31%). Cold index: express 0.23 s vs 1.72 s, fastapi 1.39 s vs 7.07 s, gin 0.27 s vs 1.56 s. Details and caveats: [COMPARISON.md](COMPARISON.md).

## Token savings (full-stack-fastapi-template, 13 typical lookups)

~22k tokens returned vs ~142k whole-file baseline: **84% saved** overall (repo_map 97%, search_symbols 84%, get_symbol 80%, get_context 77%, file_outline 71%).
Baseline = tokens of the whole files the model would otherwise open (≈4 chars/token). `find_references` and `search_code` report 0% because grep-style output is the honest baseline.

## Retrieval quality (`tests/evals`, 12 tasks, 4k-token budget)

| Metric | All 12 | 9 held-out* |
|---|---|---|
| recall@any (symbol in context as body or signature) | 1.00 | 1.00 |
| recall@full (full body included) | 1.00 | 1.00 |
| MRR of first expected symbol | 0.88 | 0.83 |

(v0.2 was recall@full 0.92, MRR 0.78; v0.3 added term-coverage ranking, test/example down-ranking and nested-result dedupe.)

\*3 tasks (password reset, pagination, delete users) were used while tuning ranking; the other 9 were written afterwards.

Reproduce:
```bash
git clone --depth 1 https://github.com/django/django && cd django && specforge index --full
python tests/evals/run_context_eval.py <full-stack-fastapi-template> tests/evals/context_fullstack_fastapi.json
```
