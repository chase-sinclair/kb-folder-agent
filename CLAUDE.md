# kb-folder-agent

AI-powered knowledge base agent: local folder structures → searchable Slack knowledge bases. Each top-level folder maps to a Qdrant collection. Users query via `/kb` and receive RAG-powered answers with citations.

## Stack
- **Python 3.13**, **MCP** (Model Context Protocol)
- **Vector DB**: Qdrant (local Docker, port 6333) — **Embeddings**: OpenAI `text-embedding-3-small`
- **LLM**: Anthropic Claude (`claude-opus-4-5`) — **Slack**: `slack-bolt` AsyncApp, Socket Mode
- **File watching**: `watchdog`

## Project Structure
```
kb-folder-agent/
├── main.py                     # Entry point — starts watcher + Slack bot concurrently
├── mcp_servers/
│   ├── filesystem_server.py    # FastMCP sync tools: list_folders, read_file, get_metadata, list_files
│   └── vectordb_server.py      # FastMCP async tools: list_collections, query_collection, add_documents, delete_document_chunks, get_collection_info
├── ingestion/
│   ├── watcher.py              # Watchdog monitor, ingest_file(), delete_file()
│   ├── chunker.py              # File-type chunkers → ChunkResult dataclass
│   ├── embedder.py             # OpenAI embedding calls, batched, with retry
│   └── quarantine.py           # Quarantine table ops, ErrorType enum
├── agent/
│   ├── orchestrator.py         # Routes calls to MCP servers; single entry point for RAG + Slack
│   └── rag.py                  # answer_query(), summarize_recent_changes() → RagResult
├── slack/
│   └── bot.py                  # /kb slash command handler
└── storage/
    ├── db.py                   # init_db(), get_db() — aiosqlite async context manager
    └── metadata.db             # Created at runtime — never commit
```

## Commands
```bash
pip install -r requirements.txt
python main.py                           # Start watcher + Slack bot
python main.py connect ~/path/to/folder  # Write WATCHED_FOLDER to .env
```

## Environment Variables

> **Note: This project runs on Windows. Use raw strings (`r'...'`) for all file paths.**

```
ANTHROPIC_API_KEY       # Claude reasoning
OPENAI_API_KEY          # text-embedding-3-small embeddings
SLACK_BOT_TOKEN         # xoxb-...
SLACK_SIGNING_SECRET    # Slack request verification
SLACK_APP_TOKEN         # xapp-... (Socket Mode; omit for HTTP mode on port 3000)
QDRANT_URL              # http://localhost:6333
WATCHED_FOLDER          # Absolute path to root folder (set via connect command)
DIGEST_ENABLED          # true to enable scheduled digest (default: disabled)
DIGEST_TIME             # HH:MM UTC time to send daily digest (default: 09:00)
DIGEST_CHANNEL          # Slack channel for digest (default: #general)
```

**NEVER read or output the contents of `.env`.**

## Architecture

**Collection routing**: `re.sub(r"[^a-z0-9]+", "_", folder.lower())` — camelCase folders with no separators produce no underscores (`PastPerformance` → `pastperformance`). Slack queries use the plain folder name.

**Ingestion pipeline**: Watcher detects changes → sha256 hash compared at chunk level → only changed chunks re-embedded → upserted to Qdrant with deterministic point IDs `abs(hash((file_path, chunk_index))) % 2**63` → `metadata.db` updated.

**Quarantine**: `LOCKED_FILE` retries 3× with backoff `[30, 120, 600]s`. `CORRUPT_FILE`, `TOO_LARGE`, `UNSUPPORTED_TYPE` quarantine immediately. Quarantined files skipped until manually cleared.

## Chunking Strategy

| File Type | Strategy |
|-----------|----------|
| `.pdf` | `pdfplumber` → 600-token chunks, 75-token overlap; tables as `chunk_type="table"` |
| `.docx` | Section-aware; heading in metadata |
| `.md`, `.txt` | Paragraph split; fenced code blocks as `chunk_type="code"` |
| `.xlsx`, `.csv` | Markdown table per sheet, 50-row groups |
| `.py .js .ts .go .rs` | Split on `def`/`class`/`func`/`fn` boundaries |

## Metadata Schema (SQLite)

**chunks** (PK: `file_path, chunk_index`): `file_path, file_hash, chunk_index, chunk_hash, chunk_type, last_ingested_at, source_folder, collection_name`

**quarantine** (PK: `file_path`): `file_path, error_type, error_message, retry_count, last_attempted_at, quarantined_at, status`

All timestamps: ISO 8601 UTC.

## Slack Commands

| Command | Description |
|---------|-------------|
| `/kb list` | All collections with chunk counts |
| `/kb ask <folder> "<question>"` | RAG query against a collection |
| `/kb status` | Watcher status + quarantined files |
| `/kb clear-quarantine <folder> <filename>` | Remove file from quarantine |

## Rules
- **NEVER modify or output `.env`**
- Collection name mapping must use: `re.sub(r"[^a-z0-9]+", "_", folder.lower())`
- All DB reads/writes go through `storage/metadata.db` via `storage/db.py`
- Quarantine logic lives exclusively in `ingestion/quarantine.py`
- MCP tool calls are orchestrated only through `agent/orchestrator.py`
- Do not add new dependencies without confirming first

## Completed Phases

**Phase 1 — SQLite Schema** ✔ `storage/db.py`: `init_db()` creates both tables; `get_db()` yields `aiosqlite.Row`-factory connection.
**Phase 2 — Quarantine System** ✔ `ingestion/quarantine.py`: `ErrorType(str, Enum)`; `should_retry()` sync; all DB ops async.
**Phase 3 — Chunker** ✔ `ingestion/chunker.py`: `chunk_file()` async router; sub-chunkers sync; lazy imports for heavy libs. Token estimate: `len(text.split()) / 0.75`.
**Phase 4 — Embedder** ✔ `ingestion/embedder.py`: `embed_chunks()` (batches 100), `embed_query()`. Single `_embed_texts()` owns 3-attempt retry with 2s sleep.
**Phase 5 — File Watcher** ✔ `ingestion/watcher.py`: `KBEventHandler` bridges watchdog threads → asyncio via `run_coroutine_threadsafe`. Initial scan via `asyncio.gather`.
**Phase 6 — Filesystem MCP Server** ✔ `mcp_servers/filesystem_server.py`: Four sync `@mcp.tool()` functions. `get_metadata()` never raises.
**Phase 7 — VectorDB MCP Server** ✔ `mcp_servers/vectordb_server.py`: All async, `try/finally client.close()`. Uses `points_count` and `query_points()` — qdrant-client 1.13+/1.17+ API.
**Phase 8 — MCP Orchestrator** ✔ `agent/orchestrator.py`: Direct function imports (no transport). Filesystem tools are sync — do not `await` them. `folder_to_collection_name()` is sync.
**Phase 9 — RAG Pipeline** ✔ `agent/rag.py`: `answer_query()` guards empty query before embedding. `_unique_sources()` preserves relevance order.
**Phase 10 — Slack Bot** ✔ `slack/bot.py`: Uses `respond()` not `say()` — slash commands must use response URL. `WATCHED_FOLDER` imported lazily in `_handle_clear_quarantine`.
**Phase 11 — Main Entry Point** ✔ `main.py`: Heavy imports deferred inside `main()`. `handle_connect()` uses lambda in `re.sub` for Windows backslash paths.
**Phase 12 — End-to-End Validation** ✔ All 5 integration tests passed: live watcher, quarantine, re-ingestion diff, RAG quality, edge cases.

## V2 Phases

### Polish A — Windows Path Fix ✔ `normalize_path()` added to quarantine.py, bot.py, watcher.py — all stored/displayed paths use forward slashes.
### Polish B — Wire /kb changes subcommand ✔ `_handle_changes()` added to bot.py; routes via `summarize_recent_changes()`; `changes` added to parse routing and help message.
### Polish C — Block Kit Slack Formatting ✔ All handlers return `(fallback_text, blocks)` tuples; `respond(text=fallback, blocks=blocks)` used throughout; header/section/divider/context builders extracted. `clean_for_slack()` strips markdown headers, `**bold**`→`*bold*`, blockquotes, and `---` from Claude answers before display.
### Polish D — README.md ✔ Full README with setup guide, Slack app creation steps, command reference, file type table, Windows notes, and project structure.
### V2-1 — Multi-Collection Search (/kb ask all) ✔ `search_all_collections()` in orchestrator.py embeds query once, fans out to all collections. `answer_query_all()` in rag.py builds grouped context with `[Collection: name]` headers. `/kb ask all "question"` routes through `_handle_ask()` special-case in bot.py.
### V2-2 — Agent-Inferred Routing (no folder name required) ✔ `infer_collection()` in orchestrator.py embeds query, scores top-1 hit per collection, picks highest; single-collection shortcut returns confidence 1.0. `INFERENCE_CONFIDENCE_THRESHOLD=0.35` gates low-confidence fallback. `/kb ask "question"` auto-routes with a context block showing the inferred folder and score.
### V2-3 — Version Snapshots + Diffs ✔ `file_versions` table (max 5 per file) stores text snapshots on each hash change; `summarize_diff()` in rag.py diffs last 2 versions via `difflib` and asks Claude to summarize; `/kb diff <folder> <file>` renders the summary in Slack.
### V2-4 — Richer File Types ✔ Added `chunk_pptx()` (slide chunks via python-pptx), `chunk_email()` (single chunk per .eml, HTML-stripped), `chunk_html()` (BeautifulSoup paragraph chunks); `.pptx`, `.eml`, `.html` added to `SUPPORTED_EXTENSIONS` in watcher.py and filesystem_server.py.
### V2-5 — Scheduled Digest ✔ `agent/digest.py`: `build_digest()` fans out `summarize_recent_changes()` across all collections; `send_digest()` posts Block Kit message to `DIGEST_CHANNEL`; `start_digest_scheduler()` sleeps until `DIGEST_TIME` UTC daily. Wired into `main.py` via `asyncio.gather`. Controlled by `DIGEST_ENABLED`, `DIGEST_TIME`, `DIGEST_CHANNEL` env vars.
