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

## Fixed lookup workload (`benchmarks/token_benchmark.py`)

Three repositories at pinned commits (expressjs/express `1faf228`, fastapi/fastapi `a64dfbb`, gin-gonic/gin `75ccf94`), five queries each, one workflow per query: `search_symbols` (top 5), then fetch the top 3 symbols. Tokens are counted with tiktoken `cl100k_base` on the text the MCP server returns, with session dedupe off so every lookup is measured fresh. Results are committed in `benchmarks/token_benchmark_results.json`.

| Repo | Tokens for 5 queries (15 lookups in total) | Cold index through the MCP server, including call and import graphs | Symbols indexed (code / config keys / doc sections) |
|---|---|---|---|
| express | 2,005 | 0.26 to 0.28 s | 994 (712 / 179 / 103) |
| fastapi | 2,983 | 3.37 to 3.45 s | 26,842 (8,137 / 3,386 / 15,319) |
| gin | 1,811 | 0.32 to 0.39 s | 1,960 (1,680 / 108 / 172) |
| **Total** | **6,799** | | |

History of the total: 9,306 (0.3), 6,086 (0.4), 5,924 (0.6), 6,799 (0.8). The rise in 0.8 is deliberate: each search result now carries the symbol's doc comment, which costs about 13% more tokens on this workload and lifted multi-repo retrieval from 89% to 96% found.

The workload does not score relevance, so the top-3 picks were also read by hand. They are mostly the dispatch and registration code itself (`Layer.handle_request`, `RouterGroup.handle`, `Engine.Use`, `Context.Param`), and Go methods are receiver-qualified. One query (fastapi, "request response") returns two test helpers in its top 3.

**Tool schema per turn** (tiktoken `cl100k_base` over each tool's name, description and input schema): `lite` 6 tools, about 790 tokens; `core` 11, about 1,200; `standard` 36, about 3,220; `analysis` 66, about 5,700; `full` 96, about 8,120. Pydantic's generated titles are stripped and `Optional[X]` is collapsed to `X` in advertised schemas; arguments are still validated against the Python signatures.

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
| MRR with local embeddings built (`specanvil embed`) | 0.85 | — |

(v0.2 was recall@full 0.92, MRR 0.78; v0.3 added term-coverage ranking, test/example down-ranking and nested-result dedupe.)

\*3 tasks (password reset, pagination, delete users) were used while tuning ranking; the other 9 were written afterwards.

Reproduce:
```bash
git clone --depth 1 https://github.com/django/django && cd django && specanvil index --full
python tests/evals/run_context_eval.py <full-stack-fastapi-template> tests/evals/context_fullstack_fastapi.json
```

## 0.8: broader evidence

**Multi-repo retrieval** (`tests/evals/run_suite.py`, 74 questions written for four more repos; every expected symbol is verified to exist before a run):

| Repo | Language | Questions | Needed symbol in context | Full body | MRR |
|---|---|---|---|---|---|
| expressjs/express | JavaScript | 20 | 1.00 | 0.95 | 0.88 |
| gin-gonic/gin | Go | 20 | 1.00 | 0.95 | 0.60 |
| spring-projects/spring-petclinic | Java | 14 | 1.00 | 1.00 | 0.79 |
| fastapi/fastapi | Python (library) | 20 | 0.85 | 0.75 | 0.64 |
| **All** | | **74** | **0.96** | **0.91** | **0.72** |

The first run of this suite scored 0.89 / 0.61 and exposed three general defects, each fixed for every language rather than per question: doc comments were not indexed for Go, Java or most JavaScript (0 of 1,155 gin functions had one; now comment blocks above symbols and Python docstrings after long signatures are read); the stemmer did not connect verb/noun families (validate / validation, depend / dependency, authenticate / authentication); config keys and doc sections outranked code for plain-English questions. FastAPI is the known weak spot: its documentation examples and tests use the API far more often than the library defines it.

### 0.10: C# added to the suite

16 questions on [dotnet-architecture/eShopOnWeb](https://github.com/dotnet-architecture/eShopOnWeb) (ASP.NET Core, commit `4da8212`), written in the repo's own nouns like the other four sets. No ranking code was changed for them.

| Repo | Language | Questions | Needed symbol in context | Full body | MRR |
|---|---|---|---|---|---|
| dotnet-architecture/eShopOnWeb | C# | 16 | 0.88 | 0.81 | 0.75 |
| **All five repos** | | **90** | **0.94** | **0.89** | **0.73** |

The CI gate is unchanged (found >= 0.90, MRR >= 0.60) and now runs on all 90 questions.

Two C# questions miss. Both targets (`ComposePicUri`, `UpdateDetails`) match only one or two task words, while a dozen `CatalogItem` symbols match the common words "catalog" and "item" and fill the budget first: every task word counts the same, however common it is in the repo. Weighting rare words higher was tried and not kept: the suite's MRR rose from 0.73 to 0.74, but the original 12-question eval fell from 0.86 to 0.79.

**The same 16 targets asked in other words** (`tests/evals/paraphrase_eshoponweb.json`: "cart" for basket, "product" for catalog item, "JWT" for token) score found 0.62, MRR 0.31 without embeddings. This set is not a gate. It records the vocabulary gap that `specanvil embed` and learned synonym rules (`suggest_corrections`) exist to close, and it is the number to move next.

### 0.11: Rust, PHP, Ruby and Kotlin added; calls through injected dependencies

Four more question sets, each written in the repository's own nouns before its first run. No ranking code was changed for them.

| Repo (commit) | Language | Questions | Needed symbol in context | Full body | MRR |
|---|---|---|---|---|---|
| tokio-rs/mini-redis `3d93b42` | Rust | 14 | 1.00 | 1.00 | 0.95 |
| slimphp/Slim `3675bf6` | PHP | 16 | 1.00 | 0.94 | 0.74 |
| sinatra/sinatra `cb22afd` | Ruby | 18 | 1.00 | 0.94 | 0.84 |
| spring-petclinic/spring-petclinic-kotlin `4246f51` | Kotlin | 12 | 1.00 | 1.00 | 0.75 |
| **All nine repos** | | **150** | **0.97** | **0.92** | **0.76** |

The CI gate (found >= 0.90, MRR >= 0.60) runs on all 150 questions.

**What the Kotlin set exposed.** Before this release no Kotlin function was indexed: the grammar pack's structure view names classes but returns functions without a name and omits member functions and `object` declarations, and class properties were recorded under their type (`OwnerRepository`) instead of their name (`owners`). On spring-petclinic-kotlin the index held 0 functions; it now holds 103, read from the source with brace- and indentation-aware end lines. The dataset could not have scored at all before the fix, because it verifies that every expected symbol exists.

**Calls through injected dependencies** (`tests/evals/run_di_eval.py`, `tests/evals/di_edges.json`). Each case is a call site (`file:line`) and the method it really calls, written by reading the code. For an interface-typed dependency the target is the implementation.

| Repo | Framework | Call sites | Right | Wrong | Right without typed receivers |
|---|---|---|---|---|---|
| lujakob/nestjs-realworld-example-app | NestJS (TypeScript) | 18 | 18 | 0 | 6 |
| spring-projects/spring-petclinic | Spring (Java) | 9 | 9 | 0 | 7 |
| dotnet-architecture/eShopOnWeb | ASP.NET Core (C#) | 13 | 13 | 0 | 0 |
| spring-petclinic/spring-petclinic-kotlin | Spring (Kotlin) | 11 | 11 | 0 | 5 |
| spring-petclinic/spring-petclinic-rest (held out) | Spring (Java) | 15 | 15 | 0 | 0 (9 wrong) |
| **All** | | **66** | **66** | **0** | **18 (9 wrong)** |

The first four repositories were used while the resolver was written, so their 51 cases are a development set, not independent evidence. The fifth was not looked at until its 15 cases had been written; its first run scored 14 of 15. The miss was a method with two overloads in an interface and in each of two implementations: six candidates against a limit of four. The limit was raised to eight, which is the only change made after seeing the held-out result. CI gate: recall >= 0.95, wrong <= 0.05.

Not covered: field injection resolved by name or qualifier, factories and providers (`Provider<T>`, `Lazy<T>`), receivers returned by a call (`getRepo().find()`), and languages without declared types.

**Workflow eval** (`tests/evals/run_workflow_eval.py`). A fixed ticket (`SHOP-42`, discount codes at checkout) on a small fixture repository, scored on six checks: hidden acceptance tests, the tests that existed before, tests the agent added, a complete feature spec, no spec drift, and nothing outside the ticket's scope. Running it needs a model, so it is not a CI gate and no agent score is published yet. CI runs the harness self-check: the untouched fixture fails `acceptance`, and a reference solution passes every code check.

**Call graph against execution** (`tests/evals/run_graph_eval.py`): the unit tests run under a profiler. In 0.11, 779 distinct caller → callee pairs are observed and the static graph contains 92% of them. History: 751 distinct caller → callee pairs between functions in `src/specanvil` were observed. The static graph contains 91% of them. On the first, smaller trace (558 pairs) it was 87%, then 96% after linking class instantiation to constructors and reading calls inside interpolated strings; adding tests that call every tool and command widened the trace to 751 pairs and exposed callbacks passed as values and functions reached through re-exporting modules, both now resolved (88% → 91%). The remaining misses are attribute chains whose receiver type is unknown (`self.facts.get(...)`) and dispatch tables. Dead-code reports contradicted by execution: 0 of 4; all five reported items were genuinely unused and have been removed.

**Scale** (kubernetes/kubernetes at `7898e97f`, 2 vCPU): 25,126 files, 397,268 symbols, 453,521 call sites, 58,815 import edges. Cold index 67 s (parse 39 s, write 28 s); peak memory 393 MB (906 MB before batching the build); refresh with no changes 0.31 s; symbol search ~70 ms; `get_context` ~130 ms; index 508 MB. Search cost at this size is scoring very common words (113,562 symbols match `pod*`); prefix indexes were measured and do not help (59 vs 68 ms), so they were dropped, which with compaction after rebuilds halves the index on ordinary repos (fastapi 66 MB → 32–41 MB).

**Several sessions on one index** (`tests/stress_concurrency.py`): 4 readers searching, building context and writing ledgers while 2 writers edit files and re-index for 15 s: 0 lock errors, 0 empty results.

**Unit-test coverage**: 79% of lines (65% before the in-process tests that call every MCP tool and CLI command); CI floor 75%. **Type check**: `mypy` reports no errors and CI requires that (100 errors at 0.9).

