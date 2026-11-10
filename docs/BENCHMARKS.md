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

Same pinned repos, same 5 queries, same workflow, tiktoken cl100k: jCodeMunch 17,581 tokens · SpecForge 0.5 **5,924 (−66%)** (0.4: 6,086; 0.3: 9,306). Cold index through the MCP server, including call/import graphs: express 0.27 s vs 1.72 s, fastapi 3.44 s vs 7.07 s, gin 0.39 s vs 1.56 s. Symbols: 994 / 26,842 / 1,960 vs 1,085 / 13,699 / 1,822 (0.5 adds config keys and doc sections). Details and caveats: [COMPARISON.md](COMPARISON.md).

**Lessons recorded (0.5).** (1) Indexing doc sections first doubled fastapi's index time: a parent section's body contains all its children, so translated docs were tokenised many times over. Sections now contribute only their heading and first 800 characters to the body index (5.6 s → 3.9 s). (2) A first `reindex(full=True)` built the index twice (once on first use, once for the full rebuild), and a full rebuild deleted rows file by file; fixing both took the benchmark's fastapi timing from 9.3 s to 3.4 s.

## Semantic retrieval (paraphrased tasks, `tests/evals/semantic_fullstack_fastapi.json`)

| Mode | hit@5 | MRR |
|---|---|---|
| lexical | 0.08 | 0.08 |
| hybrid (body vocabulary, no download) | 0.25 | 0.19 |
| semantic (bge-small-en-v1.5, local ONNX) | 0.50 | 0.22 |
| hybrid + vectors (weights 1.0 / 0.5 / 2.0) | 0.42 | 0.16 |

`get_context` on the same paraphrases: expected symbol in the full-body context **3/12 → 8/12** with vectors. The fusion weights were chosen by grid search over both evals together; the natural-language task eval moved from hit@5 0.83 / MRR 0.48 (lexical list) to 0.92 / 0.65 (fused). Embedding throughput in this 2-vCPU container: ~5–7 symbols/s with bge-small (627 symbols in ~2 min, incremental afterwards); expect several times faster on a developer laptop.

## Indexing with graphs (Django, 5.6k files, 58.9k symbols, 114k call sites, 9.6k import edges)

Cold 9.9 s (v0.3 without graphs: 6.0 s) · no-op refresh 0.11 s · one file 0.29 s · analyses: PageRank 1.2 s, dead code 1.2 s (incl. verification search), cycles 0.01 s, layers 1.1 s, AST preset 0.08 s.

## Token savings (full-stack-fastapi-template, 13 typical lookups)

~22k tokens returned vs ~142k whole-file baseline: **84% saved** overall (repo_map 97%, search_symbols 84%, get_symbol 80%, get_context 77%, file_outline 71%).
Baseline = tokens of the whole files the model would otherwise open (≈4 chars/token). `find_references` and `search_code` report 0% because grep-style output is the honest baseline.

## Retrieval quality (`tests/evals`, 12 tasks, 4k-token budget)

| Metric | All 12 | 9 held-out* |
|---|---|---|
| recall@any (symbol in context as body or signature) | 1.00 | 1.00 |
| recall@full (full body included) | 1.00 | 1.00 |
| MRR of first expected symbol | 0.88 | 0.83 |
| MRR with local embeddings built (`specforge embed`) | 0.85 | — |

(v0.2 was recall@full 0.92, MRR 0.78; v0.3 added term-coverage ranking, test/example down-ranking and nested-result dedupe.)

\*3 tasks (password reset, pagination, delete users) were used while tuning ranking; the other 9 were written afterwards.

Reproduce:
```bash
git clone --depth 1 https://github.com/django/django && cd django && specforge index --full
python tests/evals/run_context_eval.py <full-stack-fastapi-template> tests/evals/context_fullstack_fastapi.json
```
