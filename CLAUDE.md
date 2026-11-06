# kb-folder-agent

AI-powered knowledge base agent: local folders or OneDrive → searchable Slack knowledge bases. Each top-level folder maps to a Qdrant collection. Users query via `/kb` and receive RAG-powered answers with citations.

## Stack
- **Python 3.13**, **MCP** (Model Context Protocol)
- **Vector DB**: Qdrant (local Docker, port 6333) — **Embeddings**: OpenAI `text-embedding-3-small`
- **LLM**: Anthropic Claude (`claude-opus-4-5`) — **Slack**: `slack-bolt` AsyncApp, Socket Mode
- **File watching**: `watchdog` (local) / poll-based (OneDrive via MSAL + Graph API)

## Project Structure
```
kb-folder-agent/
├── main.py                        # Entry point — watcher + Slack bot + digest scheduler
├── mcp_servers/
│   ├── filesystem_server.py       # FastMCP sync tools: list_folders, read_file, get_metadata, list_files
│   ├── vectordb_server.py         # FastMCP async tools: list_collections, query_collection, get_collection_info
│   └── onedrive_server.py         # FastMCP sync tools (same interface): Graph API + MSAL auth
├── ingestion/
│   ├── watcher.py                 # Watchdog monitor, ingest_file(), delete_file(), version snapshots
│   ├── onedrive_watcher.py        # Poll-based OneDrive sync, same ingest/quarantine logic
│   ├── chunker.py                 # File-type chunkers → ChunkResult dataclass
│   ├── embedder.py                # OpenAI embedding calls, batched, with retry
│   └── quarantine.py              # Quarantine table ops, ErrorType enum
├── agent/
│   ├── agent_loop.py              # run_agent() — agentic loop with tool use and live Slack reasoning posts
│   ├── orchestrator.py            # Routes MCP calls; backend-switchable via BACKEND env var
│   ├── rag.py                     # answer_query(), answer_query_all(), summarize_diff() → RagResult
│   ├── reranker.py                # LLM rerank pass over retrieved chunks; leaf module, never raises
│   └── digest.py                  # Daily digest builder + Slack poster + scheduler
├── evals/
│   ├── config.py                  # REPORTS_DIR, RESULTS_DIR path constants
│   ├── schema.py                  # EvalRunConfig, EvalSummary dataclasses
│   ├── runner.py                  # run_evaluations() — main eval orchestration
│   ├── metrics.py                 # Retrieval quality, fact coverage, format compliance scoring
│   ├── judge.py                   # Optional LLM-based judge (use_judge=True)
│   ├── report.py                  # write_json_report(), write_markdown_report()
│   ├── compare.py                 # Per-run comparison utilities
│   ├── compare_runs.py            # Cross-run diff reporting
│   ├── audit_ingestion.py        # Read-only reconciliation: disk vs metadata.db vs Qdrant
│   ├── run_evals.py               # CLI entry point
│   ├── test_cases.yaml            # Benchmark test cases
│   ├── test_cases_rerank.yaml     # Separate harder pack with real rerank headroom
│   └── test_cases_benchmark.yaml  # ConsultingKB pack: 14 positives (P1-P14), 10 negatives (N1-N10)
├── integrations/
│   └── notion.py                  # create_ticket() — Notion Tasks Tracker via REST API
├── api/
│   ├── main.py                    # FastAPI app — CORS, router mounts for /collections and /query
│   └── routers/
│       ├── collections.py         # GET /collections, GET /collections/{name}/info
│       └── query.py               # POST /query/ask|agent|score|gaps|draft|compare; agent uses SSE stream
├── web/                           # Next.js 15 frontend (src/app router, TypeScript, Tailwind v4)
│   └── src/
│       ├── app/layout.tsx         # Dark layout shell (gray-950 bg, gray-900 sidebar)
│       ├── app/page.tsx           # Root page — CollectionSidebar + MainPane
│       ├── lib/api.ts             # Typed fetch wrappers for all API endpoints
│       └── components/
│           ├── CollectionSidebar.tsx
│           ├── MainPane.tsx       # Tab bar routing (Ask · Agent · Score · Gaps · Draft · Compare)
│           ├── LoadingSpinner.tsx
│           └── tabs/              # AskTab, AgentTab, ScoreTab, GapsTab, DraftTab, CompareTab
├── run_api.py                     # uvicorn entry point: python run_api.py (port 8000)
├── slack/
│   └── bot.py                     # /kb slash command handler
├── tests/
│   ├── test_reranker.py           # Offline, dependency-free (no pytest): python tests/test_reranker.py
│   ├── test_quarantine_classify.py # Pins exception -> ErrorType mapping
│   └── test_not_found_detection.py # Pins refusal detection against real + adversarial answers
└── storage/
    ├── db.py                      # init_db(), get_db() — aiosqlite async context manager
    └── metadata.db                # Created at runtime — never commit
```

## Commands
```bash
pip install -r requirements.txt
python main.py                           # Start watcher + Slack bot
python main.py connect ~/path/to/folder  # Write WATCHED_FOLDER to .env
python tests/test_reranker.py            # Offline — no network, no API spend
python tests/test_quarantine_classify.py # Offline
python tests/test_not_found_detection.py # Offline
python -m evals.audit_ingestion --source "C:/Users/chase/OneDrive/ConsultingKB" --prefix ConsultingKB
python -m evals.run_evals --no-rerank --cases-path evals/test_cases_benchmark.yaml   # A/B arm A
python -m evals.run_evals --rerank    --cases-path evals/test_cases_benchmark.yaml   # A/B arm B
```

> On this machine bare `python` resolves to another project venv. Use
> `.\.venv\Scripts\python.exe`.

## Environment Variables

> **Note: This project runs on Windows. Use raw strings (`r'...'`) for all file paths.**

```
ANTHROPIC_API_KEY       # Claude reasoning
OPENAI_API_KEY          # text-embedding-3-small embeddings
SLACK_BOT_TOKEN         # xoxb-...
SLACK_SIGNING_SECRET    # Slack request verification
SLACK_APP_TOKEN         # xapp-... (Socket Mode; omit for HTTP mode on port 3000)
QDRANT_URL              # http://localhost:6333
WATCHED_FOLDER          # Absolute path to root folder (local backend)
BACKEND                 # local (default) or onedrive
DIGEST_ENABLED          # true to enable scheduled digest (default: disabled)
DIGEST_TIME             # HH:MM UTC for daily digest (default: 09:00)
DIGEST_CHANNEL          # Slack channel for digest (default: #general)
AZURE_CLIENT_ID         # App registration client ID (OneDrive backend)
AZURE_TENANT_ID         # consumers (personal) or tenant ID
AZURE_CLIENT_SECRET     # App secret (if using confidential client)
ONEDRIVE_FOLDER         # Root folder name in OneDrive (e.g. test-kb)
ONEDRIVE_POLL_INTERVAL  # Seconds between OneDrive polls (default: 60)
RERANK_ENABLED          # true to enable the LLM reranker (default: disabled)
RERANK_MODEL            # Reranker model (default: claude-haiku-5-5)
RERANK_CANDIDATE_MULTIPLIER  # Fetch top_k * this before reranking (default: 3)
RERANK_MAX_CANDIDATES   # Hard cap on the candidate pool (default: 40)
RERANK_CHUNK_CHARS      # Chars of each chunk shown to the reranker (default: 700)
RERANK_TIMEOUT_SECONDS  # Per-call rerank timeout before falling back (default: 8)
SEARCH_OVERFETCH_MULTIPLIER  # Deepen the RRF pool WITHOUT reranking (default: 1 = off)
NOTION_API_KEY          # Notion integration API key (optional)
NOTION_DATABASE_ID      # Notion Tasks Tracker database ID (optional)
```

**NEVER read or output the contents of `.env`.**

## Architecture

**Backend selection**: `BACKEND=local` uses filesystem_server.py + watchdog watcher. `BACKEND=onedrive` uses onedrive_server.py + poll-based watcher. RAG, chunker, embedder, and Slack bot are backend-agnostic.

**Collection routing**: `re.sub(r"[^a-z0-9]+", "_", folder.lower())` — camelCase folders with no separators produce no underscores (`PastPerformance` → `pastperformance`).

**Ingestion pipeline**: Watcher detects changes → sha256 hash compared → changed chunks re-embedded → upserted to Qdrant with deterministic point IDs from `point_id()` in `vectordb_server.py` — sha256 of `file_path:chunk_index`, first 8 bytes mod `2**63` (never Python `hash()`, which is salted per process) → `metadata.db` updated → version snapshot stored.

**Quarantine**: `LOCKED_FILE` and `TRANSIENT_ERROR` retry 3× with backoff `[30, 120, 600]s` — `classify_exception()` routes 429/5xx, connection resets and timeouts to `TRANSIENT_ERROR`. `CORRUPT_FILE`, `TOO_LARGE`, `UNSUPPORTED_TYPE` quarantine immediately.

## Chunking Strategy

| File Type | Strategy |
|-----------|----------|
| `.pdf` | `pdfplumber` → 600-token chunks, 75-token overlap; tables as `chunk_type="table"` |
| `.docx` | Section-aware; heading in metadata |
| `.md`, `.txt` | Paragraph split; fenced code blocks as `chunk_type="code"` |
| `.xlsx`, `.csv` | Markdown table per sheet, 50-row groups |
| `.py .js .ts .go .rs` | Split on `def`/`class`/`func`/`fn` boundaries |
| `.pptx` | One chunk per slide; `chunk_type="slide"` |
| `.eml` | Single chunk; HTML-stripped; headers in metadata; `chunk_type="email"` |
| `.html` | BeautifulSoup paragraph chunks; `chunk_type="html"` |

## Metadata Schema (SQLite)

**chunks** (PK: `file_path, chunk_index`): `file_path, file_hash, chunk_index, chunk_hash, chunk_type, last_ingested_at, source_folder, collection_name`

**quarantine** (PK: `file_path`): `file_path, error_type, error_message, retry_count, last_attempted_at, quarantined_at, status`

**file_versions** (PK: `file_path, version_index`): `file_path, version_index, content_snapshot, file_hash, captured_at` — max 5 versions per file

All timestamps: ISO 8601 UTC.

## Slack Commands

| Command | Description |
|---------|-------------|
| `/kb list` | All collections with chunk counts |
| `/kb ask <folder> "<question>"` | RAG query against a collection |
| `/kb ask all "<question>"` | Fan-out query across all collections |
| `/kb ask "<question>"` | Auto-routes to best-matching collection |
| `/kb changes <folder>` | Summarize recent changes in a collection |
| `/kb diff <folder> <filename>` | AI summary of last 2 versions of a file |
| `/kb status` | Watcher status + quarantined files |
| `/kb draft <folder> "<requirement>"` | Draft a compliant proposal narrative section from KB content |
| `/kb compare <folder1> <folder2> "<question>"` | Side-by-side comparative analysis of two collections |
| `/kb score <folder> "<requirement>"` | Score KB readiness against an RFP requirement (1–10 scale) |
| `/kb gaps <folder> "<topic>"` | Gap analysis: hard vs soft gaps relative to a topic |
| `/kb clear-quarantine <folder> <filename>` | Remove file from quarantine |
| `/kb clear-quarantine-all` | Clear every quarantine entry |
| `/kb agent <question>` | Multi-step agentic query with live tool-use reasoning posted to channel |
| `/kb eval [all\|case <id>\|task-type <type>\|collection <folder>] [judge] [rerank\|no-rerank]` | Run evaluation suite against KB benchmark |
| `/kb eval-report` | Show the latest saved evaluation summary |
| `/kb ticket "<task name>" [high\|medium\|low] [YYYY-MM-DD]` | Create a Notion ticket in the Tasks Tracker database |

## Rules
- **NEVER modify or output `.env`**
- Collection name mapping must use: `re.sub(r"[^a-z0-9]+", "_", folder.lower())`
- All DB reads/writes go through `storage/metadata.db` via `storage/db.py`
- Quarantine logic lives exclusively in `ingestion/quarantine.py`
- MCP tool calls are orchestrated only through `agent/orchestrator.py`
- Do not add new dependencies without confirming first

## Completed Phases

**Phase 1–12** ✔ Core build: SQLite schema, quarantine, chunker, embedder, file watcher, filesystem MCP, vectorDB MCP, orchestrator, RAG pipeline, Slack bot, main entry point, end-to-end validation.

## V2 Phases

**Polish A** ✔ Windows path normalization — forward slashes throughout.
**Polish B** ✔ `/kb changes` subcommand wired to `summarize_recent_changes()`.
**Polish C** ✔ Block Kit formatting for all Slack responses; `clean_for_slack()` strips markdown.
**Polish D** ✔ README.md with full setup guide and command reference.
**V2-1** ✔ Multi-collection search — `search_all_collections()` + `/kb ask all`.
**V2-2** ✔ Agent-inferred routing — `infer_collection()` scores top-1 per collection; `INFERENCE_CONFIDENCE_THRESHOLD=0.35`.
**V2-3** ✔ Version snapshots + diffs — `file_versions` table; `summarize_diff()` via difflib + Claude; `/kb diff`. Written by **both** watchers (local since V2-3, OneDrive since 2026-11-04) for `.md`, `.txt`, `.pdf`, `.docx` only; other types index without snapshots and `/kb diff` reports no history for them.
**V2-4** ✔ Richer file types — `.pptx`, `.eml`, `.html` chunkers; python-pptx, beautifulsoup4.
**V2-5** ✔ Scheduled digest — `agent/digest.py`; daily Slack post via `DIGEST_ENABLED/DIGEST_TIME/DIGEST_CHANNEL`.

## V3 Phases

**V3-1** ✔ OneDrive MCP Server — `mcp_servers/onedrive_server.py` mirrors filesystem_server.py interface; MSAL device-flow auth with serializable token cache; Graph API.
**V3-2** ✔ OneDrive poll-based watcher — `ingestion/onedrive_watcher.py`; hash-diff skip; same quarantine logic; `ONEDRIVE_POLL_INTERVAL`.
**V3-3** ✔ Backend selection — `BACKEND=local|onedrive` switches MCP imports in orchestrator.py and watcher in main.py; all other layers unchanged.
**V3-4** ✔ End-to-end validation — all RAG paths verified with `BACKEND=onedrive`: single-collection, multi-collection, inferred routing.

## V4 Phases

**V4-1** ✔ Hybrid search — dense vector + BM25-style sparse vectors stored in Qdrant, **fused client-side in Python**.
- `mcp_servers/vectordb_server.py`: `build_sparse_vector(text)` tokenises with regex, hashes tokens to 24-bit indices, weights by `1+log(tf)`; `SPARSE_VECTOR_NAME = "text-sparse"`; `_ensure_collection` provisions sparse config on create and calls `update_collection` on existing 409. `query_collection` accepts optional `query_text` and, when the sparse vector is non-empty, issues **two separate** `query_points` calls — dense (`using=""`) and sparse (`using=SPARSE_VECTOR_NAME`), each at `limit=top_k * 2` — then merges the two ranked lists with `_rrf_merge()`, a plain-Python Reciprocal Rank Fusion at `k=60`. This is **not** Qdrant's server-side `Prefetch`/`Fusion.RRF`; there is no `Prefetch` anywhere in the codebase. Any exception in the hybrid path logs a warning and falls back to a single dense-only query.
- `agent/orchestrator.py`: `search()` threads `query_text=query` into `query_collection` (public API unchanged).
- Both watchers: `_ensure_collection` updated to match; `PointStruct.vector` changed to dict `{"": dense, "text-sparse": sparse}` so each point carries both vectors at index time.
- Existing collections: startup `update_collection` adds sparse config; points re-indexed with sparse vectors on next ingest cycle. Dense-only fallback ensures zero downtime during migration.
**V4-2** ✔ Threaded follow-up questions — `/kb ask` now posts in-channel via `say()` (not ephemeral) so users can reply in the thread. A `@app.event("message")` handler fires on all thread replies; it verifies the thread root was posted by the bot (via `_bot_id` resolved at startup with `auth_test()`), extracts the collection name from the header block text (regex on `"💬 FolderName"`), builds conversation history from prior thread messages, and calls `answer_with_history()` in `agent/rag.py`. `answer_with_history` searches the collection with the new query, prepends the existing conversation history, and sends all messages plus retrieved context to Claude in a single API call. Bot only responds to threads it owns; all other threads are ignored. Requires `channels:history` scope and `message.channels` event subscription. Multi-collection (`all`) threads are skipped (no single collection to route to).
**V4-3** ✔ Gap analysis — `/kb gaps <folder> "<topic>"`: retrieves top-20 chunks, passes to Claude with a prompt distinguishing hard gaps (completely absent) vs soft gaps (mentioned but thin), suggests filling content, ends with a priority recommendation. `find_gaps()` in `agent/rag.py`; `_handle_gaps()` in `slack/bot.py`. Returns a warning if the collection has fewer than 5 chunks.
**V4-4** ✔ Requirement scoring — `/kb score <folder> "<requirement>"`: retrieves top-15 chunks, scores KB readiness against an RFP requirement using a federal adjectival scale (Outstanding 9–10 → Unacceptable 1–2). Claude identifies distinct criteria, scores each individually, produces a weighted composite, and cites specific filenames for every strength/weakness. Returns warning if requirement is under 10 words. `score_requirement()` in `agent/rag.py`; `_handle_score()` in `slack/bot.py`.
**V4-5** ✔ Collection comparison — `/kb compare <folder1> <folder2> "<question>"`: parallel top-10 queries on both collections, synthesized by Claude into a structured comparative analysis with a comparison table, complementary strengths, divergences, and a bottom line. Flags file overlap between collections. Returns errors if either collection is missing or empty; warns if question is under 8 words. `compare_collections()` in `agent/rag.py`; `_handle_compare()` in `slack/bot.py`.
**V4-6** ✔ Proposal draft — `/kb draft <folder> "<requirement>"`: retrieves top-20 chunks and has Claude write a 3–4 paragraph compliant proposal narrative directly from KB content. Pulls specific contract values, dates, CPARS ratings, and COR names; inserts `[EVIDENCE MISSING: ...]` inline where the KB can't support a claim. Draft is split into per-paragraph section blocks. Ends with a Coverage line summarizing supported vs. flagged elements. Returns warning if requirement is under 10 words. `draft_section()` in `agent/rag.py`; `_handle_draft()` in `slack/bot.py`.


## V5 Phases

**V5-1** ✔ Evaluation Center — `evals/` package with YAML-driven benchmark suite, retrieval quality / fact coverage / format compliance metrics, optional LLM judge (`use_judge=True`), JSON + Markdown report output, and cross-run comparison. `/kb eval` runs the suite in the background and posts an ephemeral summary; `/kb eval-report` shows the latest saved results. `run_evaluations()` in `evals/runner.py`; `_handle_eval()` / `_handle_eval_report()` in `slack/bot.py`. Eval scope filters: `all`, `case <id>`, `task-type <type>`, `collection <folder>`.
**V5-2** ✔ Notion integration — `integrations/notion.py` wraps the Notion REST API to create pages in a Tasks Tracker database. `/kb ticket "<task name>" [high|medium|low] [YYYY-MM-DD]` creates a ticket and returns the Notion page URL. Also works from threads (prefix message with `ticket …`). Requires `NOTION_API_KEY` and `NOTION_DATABASE_ID`.

## V6 Phases

**V6-1** ✔ Agentic loop — `agent/agent_loop.py` implements a multi-step Claude tool-use loop with four tools: `list_collections`, `query_collection`, `search_all_collections`, and `get_collection_info`. Each round, reasoning text is posted to Slack before tool calls (`🔍`), tool calls are announced (`⚙️`), and results are summarized (`📄`). Max 3 rounds; if rounds are exhausted, a final non-tool call synthesizes the findings. `/kb agent <question>` always routes through the agent. `/kb ask <question>` (auto-routed, no explicit folder) silently routes to the agent when `_is_complex_query()` detects comparative/multi-hop signals. `run_agent()` takes the orchestrator module as a parameter for testability. `orchestrator.py` gained two public methods: `get_collection_info()` and `search_all()` (flat list with `collection_name` key added per hit).

## V7 Phases

**V7-1** ✔ FastAPI REST + SSE layer — `api/` package exposes the existing agent functions as HTTP endpoints. `GET /collections` and `GET /collections/{name}/info` serve collection metadata. `POST /query/ask` handles single-collection and fan-out RAG. `POST /query/agent` streams the agentic loop via SSE using an `asyncio.Queue` bridge between `run_agent()`'s `post_step` callback and the `StreamingResponse` generator. `POST /query/score|gaps|draft|compare` parse Claude's structured output into typed JSON. All endpoints are async, wrap errors as `JSONResponse(500)`. Entry point: `python run_api.py` (uvicorn, port 8000). No changes to Slack bot or agent layer. Added `fastapi` and `uvicorn[standard]` to `requirements.txt`.

**V7-2** ✔ Next.js web UI — `web/` Next.js 15 App Router project (TypeScript, Tailwind v4). Dark sidebar layout (`gray-950` bg, `gray-900` sidebar, violet-500 accent). `CollectionSidebar` fetches from API with skeleton loader and refresh button. Six tab components: **Ask** (chat history, source pill badges), **Agent** (streaming SSE with step-by-step reasoning chain, abort/stop control, pulsing indicator), **Score** (color-coded 1–10 score, strengths/weaknesses grid), **Gaps** (hard/soft gap badges, priority recommendation), **Draft** (document card with inline `[EVIDENCE MISSING]` red highlighting, copy-to-clipboard), **Compare** (two-collection selector, parsed markdown table as real HTML table, complementary/divergences sections, bottom line callout). All tabs show SVG empty states. No external component libraries — all Tailwind. `src/lib/api.ts` is the typed fetch wrapper; SSE handled inline in `AgentTab` via `fetch` + `ReadableStream`.

## V8 Phases

**V8-1** ✔ LLM reranker — an optional second-stage rerank over the hybrid candidate pool, off by default (`RERANK_ENABLED`).
- `agent/reranker.py` is a **leaf module**: it imports nothing from `agent/`, because `agent/rag.py` imports `orchestrator`, so putting the reranker in orchestrator would be a cycle — and orchestrator must stay importable without `ANTHROPIC_API_KEY` (the watcher path and `evals/runner.py` both import it). The `anthropic` client is a **lazy singleton**, never built at import.
- Model is `claude-haiku-5-5` via `RERANK_MODEL`, called with `output_config={"effort": "low"}` and `thinking={"type": "disabled"}`. **No `temperature`** (non-default sampling params return 400 on current models) and **no `budget_tokens`** (400 on Haiku 5.5). At $0.10/$0.50 per MTok a 40-candidate rerank is ~$0.001.
- The model returns **ordered candidate numbers, not scores** — a permutation is trivially validatable, where per-candidate scores would invite a threshold cut that could silently shrink the result list.
- `_parse_order()` is the load-bearing invariant: collect every integer, keep 1..n, dedupe preserving first occurrence, return `None` if nothing usable survives, then **append every omitted index in original retrieval order**. That last step guarantees `len(rerank(...)) == min(top_k, len(hits))`, which is what stops a truncated or chatty response from dropping a result list below `find_gaps()`'s `if len(results) < 5` gate.
- `rerank()` **never raises into a Slack handler**: `asyncio.wait_for` + bare `except Exception` → `log.warning` → `return hits[:top_k]`, mirroring the dense-only fallback in `query_collection`. Short-circuits with no API call when `len(hits) <= 1`.
- `orchestrator.search()` gained a **keyword-only** `rerank: bool = True`. When enabled it over-fetches `min(top_k * RERANK_CANDIDATE_MULTIPLIER, RERANK_MAX_CANDIDATES)` candidates, reranks, then truncates to `top_k`. With `RERANK_ENABLED` unset, `fetch_k == top_k` and the slice is a no-op, so the off-path is unchanged — that is what makes the A/B baseline arm trustworthy. Keyword-only means all 7 `search()` call sites in `agent/rag.py` (and therefore every `/kb` command and every `api/` endpoint) picked this up with **zero edits to `rag.py`**.
- `search_all_collections()` reranks **per collection**, in parallel via `asyncio.gather`, not over a merged pool — its return type is keyed by collection and `answer_query_all()` builds `sources_by_collection` from it. Cross-collection budget reallocation is a separate experiment.
- **`infer_collection()` deliberately bypasses `search()`** and calls `query_collection(top_k=1)` with no `query_text`: it compares raw scores *across* collections against `INFERENCE_CONFIDENCE_THRESHOLD`, which reranking would destroy, at a cost of one LLM call per collection per routing hop. The bypass is load-bearing — do not tidy it into `search()`.
- `agent/agent_loop.py` opts out explicitly (`rerank=False` at both orchestrator call sites): the agent already runs several retrieval rounds, and reranking each would multiply latency. Default `True` + 2 opt-outs beats default `False` + 7 opt-ins, because a missed opt-in fails silently.
- **Measurement.** `check_expected_source_rank()` in `evals/metrics.py` adds MRR and top-1 over the *ordered* source list. Both are **report-only, weight 0** — deliberately absent from `OVERALL_SCORE_WEIGHTS`, because adding a weight would renormalise every historical `overall_score` and re-run `assign_status()`, conflating "a metric was added" with "retrieval improved". `check_expected_sources()` is an order-insensitive set hit rate, so on a 42-chunk KB where `score`/`draft`/`gaps` already retrieve whole collections it cannot see a reranker at all; MRR is the metric that can.
- `evals/test_cases_rerank.yaml` is a **separate 3-case pack** with queries whose lexical bait pulls the wrong chunks up and whose `top_k` sits below the collection chunk count. Kept out of `test_cases.yaml` so the archived 8-case baseline stays directly comparable. Run the rerank-OFF arm first: if it already scores 100%, the cases are too easy.
- `EvalRunConfig.rerank` is **tri-state** (`None` = honour the env, `True`/`False` = force). `run_evaluations()` forces `RERANK_ENABLED`, reads the *effective* value back for the report, and **restores the previous value in a `finally`** — without that, a Slack-triggered `/kb eval rerank` would leave the live bot reranking for the rest of its uptime. `evals/compare.py` reports each arm's rerank state from the run files themselves rather than trusting `--baseline-label`.
- `tests/test_reranker.py` covers all of the above offline — no network, no Qdrant, no API spend, and no pytest dependency. Run it with `.\.venv\Scripts\python.exe tests	est_reranker.py`.

**V8-2** ✔ ConsultingKB benchmark — the corpus grew from 10 files / 42 chunks to **40 files / 274 chunks** (57 pastperformance, 58 resumes, 54 contracts, 49 costvolume, 56 technicalvolume), built as an adversarial retrieval fixture: near-miss clusters, a named lexical decoy per folder, dated tensions, and three protected TechnicalVolume evidence gaps that must stay open.
- `evals/test_cases_benchmark.yaml` is a **separate 24-case pack** (14 positives, 10 protected negatives). Every `expected_fact` was verified as a literal substring of its expected source rather than copied from the corpus handoff — five handoff anchors did not survive that check and would have scored 0 as false failures.
- **`expected_source_hit_rate` cannot see the dominant failure.** It is a *filename set* metric, so a file counts as retrieved when any one of its chunks returns. Three cases scored a perfect 1.00 on it while the answer asserted that figures present in the retrieved file were not stated. **Right file, wrong chunk.** `expected_fact_coverage` is the only metric that catches it; the signature to watch is `hit_rate = 1.00` with `fact_coverage = 0.00`.
- **`check_not_found_handling()` matched only two literal templates**, so 4 of 6 correct abstentions scored 0.00 and were read as hallucinations — worth 16.6 points. The model refuses with "I don't have information about" and "I cannot fully answer", where the adverb breaks the `cannot answer` substring. Now two regex families layered over the original literals, which are kept verbatim so historical scores cannot shift for any other reason. Pinned by `tests/test_not_found_detection.py`, including adversarial controls: this score is only computed when `expected_not_found` is true, so an over-loose pattern would credit a fabrication that merely mentions an omission.
- **The rerank gain is ranking, not recall — measured.** Three arms on the 24-case pack against a **±0.8 noise floor** established from two identical baseline runs: rerank-off **91.0%**, `SEARCH_OVERFETCH_MULTIPLIER=3` with rerank off (same 15-candidate pool, no LLM) **90.8%**, rerank-on **95.5%** with the only FAIL eliminated. A deeper pool on its own buys nothing. The one exception is the fan-out case, where the fixed 3-chunks-per-collection budget in `search_all_collections()` was the binding constraint and pool depth alone sufficed.
- **Reranking is scoped to `question_answering`** (`answer_query`, `answer_with_history`; `top_k=5`, ~9% of a 54-chunk collection). `score` (15), `draft` (20), `gaps` (20) and `compare` (10) pass `rerank=False`: they already retrieve 19-37% of a collection, so reordering barely changes the context. On the re-baselined 8-case pack reranking moved 7 of 8 cases not at all and cost 0.05 on `requirement_scoring`, while adding a Haiku call to each. Measured latency: **+0.8s** at `top_k=5`, **+1.7s** at `top_k=20` where the 40-candidate cap binds. End to end a `/kb ask` is ~5-7s, of which Opus generation is 4-5s — the reranker is not what makes it feel slow.
- `_SYSTEM_DRAFT` rule 5 now requires an inline source filename, which `_SYSTEM_SCORE` rule 6 and `_SYSTEM_COMPARE` rule 7 already did. `citation_presence` had scored 0.0 in **8 of 8** archived runs back to April, pinning `draft_fedramp_001` at exactly 0.89; it now scores 1.00 and the 8-case pack reaches 100%. Rule 3 also no longer names CPARS ratings, a category the corpus deliberately never contains.
- `python -m evals.audit_ingestion` is the gate before any benchmark run. It re-chunks the source tree with the real chunker and reconciles it against `metadata.db` and Qdrant, because **agreement between the index and the database proves nothing** — both read a self-consistent 213 chunks while 61 were missing, and nothing reported a problem. Only the source tree is ground truth.
- **19 baseline chunks fall under 50 words** and are weak-hit generators: one 19-word header holds the only copy of a whitepaper version number and is unreachable by similarity. Instrument them in retrieval tests; do not rewrite them without redesigning the benchmark.

## Known Fixes

**Qdrant connection failures quarantined healthy files permanently** — On 2026-11-05 the OneDrive watcher ingested 32 of 40 ConsultingKB files and quarantined the other 8 as `CORRUPT_FILE` with the message `ResponseHandlingException: ` and no detail. That is `qdrant_client`'s wrapper for a connection-level failure during the 30-file burst, not a bad file. `classify_exception()` caught `UnexpectedResponse` with a 429/5xx status, `requests` connection errors, `aiohttp` errors and bare `OSError` — but `ResponseHandlingException` is none of those: its MRO is `ApiException -> Exception`, it does **not** subclass `OSError`, and its empty `str()` defeats the `_TRANSIENT_MARKERS` text scan too, so it fell through to the `CORRUPT_FILE` default. `CORRUPT_FILE` is not in `RETRYABLE_ERRORS` and `onedrive_watcher.py` returns early on `is_quarantined()`, so the watcher re-checked those 8 files every 60s and skipped them **forever**. Three of them were the benchmark corpus's primary lexical decoys, so the eval suite would have scored well on exactly the cases built to be hard. Now routed to `TRANSIENT_ERROR`, which gets the existing 3-retry `[30, 120, 600]s` backoff, so a recurrence self-heals instead of needing a manual clear-quarantine. Same class as the transient-Qdrant fix below, which covered the HTTP-status exception but missed the connection-level one. Pinned by `tests/test_quarantine_classify.py`.

**`_ensure_collection` 409 race** — During initial scan, concurrent coroutines for the same collection both pass a check-then-create guard and race to `PUT /collections/{name}`. The second gets a 409 Conflict, quarantining the file as `CORRUPT_FILE`. Fixed in both watchers: dropped the `get_collections()` pre-check entirely; now unconditionally attempts `create_collection` and catches any exception whose string contains `"409"` or `"already exists"` — re-raises all others. This eliminates the TOCTOU window and avoids a fragile `UnexpectedResponse` import path dependency.

**Stale quarantine entries from removed folders** — `/kb status` showed quarantined files from folders no longer under `WATCHED_FOLDER`. Added `purge_stale_quarantine(watched_root)` to `ingestion/quarantine.py`; called from `start_watcher()` after `init_db()`. On every startup, records whose `file_path` doesn't start with the current `WATCHED_FOLDER` are deleted from the quarantine table.

**Transient Qdrant errors misclassified as CORRUPT_FILE** — Qdrant 503/429/500 responses and `aiohttp` connection/timeout errors were caught by the generic `except Exception` handler and quarantined permanently. Added `ErrorType.TRANSIENT_ERROR` to `ingestion/quarantine.py`; added it to `RETRYABLE_ERRORS` so it gets the same 3-retry backoff as `LOCKED_FILE`. Both watchers now detect `UnexpectedResponse` with status 503/429/500 and `aiohttp` connection/timeout errors and route them to `TRANSIENT_ERROR` instead of `CORRUPT_FILE`.

**SQLite `database is locked` under concurrent access** — `aiosqlite.connect()` had no timeout, so concurrent writers (watcher + Slack handler) would immediately raise `OperationalError: database is locked`. Fixed in `storage/db.py`: `aiosqlite.connect(DB_PATH, timeout=30)` — SQLite will now retry for up to 30 seconds before raising.

**Slack rejected long answers outright** — Slack caps a section block at 3000 characters and a message at 50 blocks, and model output routinely crossed both. `slack/bot.py` now splits text into section blocks on paragraph then line boundaries (`_split_text`/`_text_sections`), caps block count (`_cap_blocks`), and `_safe_post()` retries as plain text if Slack still rejects the Block Kit payload. `_friendly_error()` keeps raw API payloads in the log rather than the channel.

**`/kb list` took ~15 seconds on the OneDrive backend** — Three causes, all in `mcp_servers/onedrive_server.py`. Every HTTP call re-read and re-deserialised the on-disk MSAL cache and asked MSAL for a token: `get_token()` now caches in-process with a 5-minute early renewal. Each call also paid a fresh TLS handshake: `_session()` pools one `requests.Session` per thread and `_pool()` reuses a single process-wide `ThreadPoolExecutor` (a per-call executor discarded the warm connections with its threads). Finally `get_folder_list()` in `agent/orchestrator.py` ran the blocking `list_folders()` inline, stalling the event loop past Slack's 3s socket-mode ack deadline; it now goes through `asyncio.to_thread`.

**Graph and SharePoint transience quarantined healthy files** — `requests` defaulted to no timeout, so a stalled read became a multi-minute hang, and any 503 or connection reset was read as a bad file. `_request()` adds `(10, 60)` connect/read timeouts, 3 attempts with `[1, 3, 8]s` backoff on `{429, 500, 502, 503, 504}` and connection errors, and one forced token refresh on 401. `_download_to_temp()` wraps the whole transfer in that retry, not just the request: resets arrive mid-stream after healthy headers, leaving a partial temp file that would otherwise be chunked as if it were the real document. `classify_exception()` in `ingestion/quarantine.py` is the single place that decides transient vs. corrupt, and deliberately settles `requests.RequestException` as `CORRUPT_FILE` because every one of them subclasses `OSError` and would otherwise retry forever.

**Qdrant point IDs changed on every restart** — Point IDs came from `abs(hash((file_path, chunk_index)))`, but CPython salts `str` hashing per process, so the same chunk got a new ID after each restart and re-ingest left the old points behind as orphans. `point_id()` in `mcp_servers/vectordb_server.py` now derives the ID from sha256. Both watchers use it, and the OneDrive watcher deletes a file's existing points before upserting so legacy randomised IDs get cleared.

**Quarantine retry counts never advanced** — `increment_retry()` UPDATEd a row that did not exist yet, so `retry_count` stayed at 0 and backoff never progressed. `record_retry()` INSERTs instead and returns the new count, leaving `status = 'retrying'` so `is_quarantined()` keeps letting the file through. `clear_quarantine()` now returns rows cleared (so `/kb clear-quarantine` can report an honest miss) and resets `retry_count`; `find_quarantined_path()` matches on the path tail case-insensitively, because the stored prefix differs per backend and the folder the user types may differ in case.

**`/kb changes` summarised document text, not changes** — It vector-searched the collection for "What changed or was updated recently", which retrieves documents that talk about change rather than files that changed. `_collection_activity()` in `agent/rag.py` now reads `chunks.last_ingested_at` and `file_versions.captured_at` from `metadata.db` directly, falls back to the most recent files when the window is empty, and `_SYSTEM_CHANGES` instructs Claude to report indexing activity only.

**OneDrive backend wrote no version snapshots** — Snapshots were written only by `ingestion/watcher.py`, so under `BACKEND=onedrive` the `file_versions` table stayed empty and `summarize_diff()` always returned "Not enough version history to compare." `ingestion/onedrive_watcher.py` gained `get_latest_version_hash()`, `store_version_snapshot()` and `_extract_text_for_snapshot(local_path, original_name)` (the download lands in a temp file, so extraction keys off the OneDrive filename for its extension), snapshotting after the hash-change check and before chunking while the temp file still exists. Guarded by `try/except` + `log.warning` so a snapshot failure can never quarantine the file.

**A cold Qdrant at startup killed the Slack bot** — `start_watcher()` / `start_onedrive_watcher()` called `purge_orphaned_qdrant_points()` and `purge_chunks_for_missing_collections()` unguarded, and `main.py` gathers those coroutines with `start_bot()`. With Qdrant stopped, `ResponseHandlingException: All connection attempts failed` propagated out of `asyncio.gather` and took the whole process down seconds after "Bolt app is running!". Both watchers now wrap just those two Qdrant purges, log a warning and continue with zero counts; files that fail to ingest while Qdrant is cold are retried as `TRANSIENT_ERROR`.

**Dead `add_documents` tool reintroduced the salted point-ID bug** — `mcp_servers/vectordb_server.py` carried a second, older ID helper `_point_id()` (`abs(hash((file_path, chunk_index))) % 2**63`) alongside the fixed `point_id()`. Its only caller was the `add_documents` MCP tool, which also passed `vector=doc["embedding"]` — a bare dense list that no longer matches the named-vector schema (`{"": dense, "text-sparse": sparse}`) the watchers write. Nothing in the project called `add_documents`: both watchers upsert directly via `AsyncQdrantClient`. Both the tool and `_point_id()` are deleted, along with the now-unused `PointStruct` import. Writes to Qdrant happen only in the two watchers; `point_id()` in `vectordb_server.py` is the single ID function. `_ensure_collection()` in that module is now also uncalled (the watchers have their own copies) but is kept as the reference implementation.

## Documentation Map

`CLAUDE.md` (this file) is the **single source of truth** for project instructions. `AGENTS.md` is a pointer to it — do not let it grow its own copy of the stack, structure, commands or rules. `CODEX.md` is a chronological handoff log for the Evaluation Center workstream, not an instruction file; append to it, don't mirror this file into it. `FEATURES.md` is the user-facing `/kb` command guide. `README.md` is setup documentation and is **UTF-16LE with no BOM** — patch it with an explicit encoding or not at all.
