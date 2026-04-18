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
│   ├── orchestrator.py            # Routes MCP calls; backend-switchable via BACKEND env var
│   ├── rag.py                     # answer_query(), answer_query_all(), summarize_diff() → RagResult
│   └── digest.py                  # Daily digest builder + Slack poster + scheduler
├── slack/
│   └── bot.py                     # /kb slash command handler
└── storage/
    ├── db.py                      # init_db(), get_db() — aiosqlite async context manager
    └── metadata.db                # Created at runtime — never commit
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
```

**NEVER read or output the contents of `.env`.**

## Architecture

**Backend selection**: `BACKEND=local` uses filesystem_server.py + watchdog watcher. `BACKEND=onedrive` uses onedrive_server.py + poll-based watcher. RAG, chunker, embedder, and Slack bot are backend-agnostic.

**Collection routing**: `re.sub(r"[^a-z0-9]+", "_", folder.lower())` — camelCase folders with no separators produce no underscores (`PastPerformance` → `pastperformance`).

**Ingestion pipeline**: Watcher detects changes → sha256 hash compared → changed chunks re-embedded → upserted to Qdrant with deterministic point IDs `abs(hash((file_path, chunk_index))) % 2**63` → `metadata.db` updated → version snapshot stored.

**Quarantine**: `LOCKED_FILE` retries 3× with backoff `[30, 120, 600]s`. `CORRUPT_FILE`, `TOO_LARGE`, `UNSUPPORTED_TYPE` quarantine immediately.

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
| `/kb clear-quarantine <folder> <filename>` | Remove file from quarantine |

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
**V2-3** ✔ Version snapshots + diffs — `file_versions` table; `summarize_diff()` via difflib + Claude; `/kb diff`.
**V2-4** ✔ Richer file types — `.pptx`, `.eml`, `.html` chunkers; python-pptx, beautifulsoup4.
**V2-5** ✔ Scheduled digest — `agent/digest.py`; daily Slack post via `DIGEST_ENABLED/DIGEST_TIME/DIGEST_CHANNEL`.

## V3 Phases

**V3-1** ✔ OneDrive MCP Server — `mcp_servers/onedrive_server.py` mirrors filesystem_server.py interface; MSAL device-flow auth with serializable token cache; Graph API.
**V3-2** ✔ OneDrive poll-based watcher — `ingestion/onedrive_watcher.py`; hash-diff skip; same quarantine logic; `ONEDRIVE_POLL_INTERVAL`.
**V3-3** ✔ Backend selection — `BACKEND=local|onedrive` switches MCP imports in orchestrator.py and watcher in main.py; all other layers unchanged.
**V3-4** ✔ End-to-end validation — all RAG paths verified with `BACKEND=onedrive`: single-collection, multi-collection, inferred routing.

## Known Fixes

**`_ensure_collection` 409 race** — During initial scan, concurrent coroutines for the same collection both pass the `if name not in names` guard and race to `PUT /collections/{name}`. The second gets a 409 Conflict, which previously propagated as an exception and was caught by the generic handler, quarantining the file as `CORRUPT_FILE`. Fixed in both `ingestion/watcher.py` and `ingestion/onedrive_watcher.py`: catch `UnexpectedResponse(409)` inside `_ensure_collection` and treat it as a no-op; re-raise any other status code.

**Transient Qdrant errors misclassified as CORRUPT_FILE** — Qdrant 503/429/500 responses and `aiohttp` connection/timeout errors were caught by the generic `except Exception` handler and quarantined permanently. Added `ErrorType.TRANSIENT_ERROR` to `ingestion/quarantine.py`; added it to `RETRYABLE_ERRORS` so it gets the same 3-retry backoff as `LOCKED_FILE`. Both watchers now detect `UnexpectedResponse` with status 503/429/500 and `aiohttp` connection/timeout errors and route them to `TRANSIENT_ERROR` instead of `CORRUPT_FILE`.

**SQLite `database is locked` under concurrent access** — `aiosqlite.connect()` had no timeout, so concurrent writers (watcher + Slack handler) would immediately raise `OperationalError: database is locked`. Fixed in `storage/db.py`: `aiosqlite.connect(DB_PATH, timeout=30)` — SQLite will now retry for up to 30 seconds before raising.
