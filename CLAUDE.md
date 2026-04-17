# kb-folder-agent

An AI-powered knowledge base agent that turns local folder structures into
searchable, conversational knowledge bases accessible via Slack. Each top-level
folder becomes an independent collection in a vector database. Users ask
natural-language questions in Slack and receive RAG-powered answers with citations.

Built on MCP (Model Context Protocol) with a modular, backend-agnostic architecture.

---

## Stack

- **Python 3.13**
- **MCP servers**: filesystem server, Qdrant vector DB server
- **Slack**: slash command bot (`/kb`)
- **Vector DB**: Qdrant (local via Docker on port 6333)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **LLM Reasoning**: Anthropic Claude (RAG answer generation)
- **File watching**: `watchdog`
- **PDF extraction**: `pdfplumber`
- **DOCX extraction**: `python-docx`

---

## Project Structure

```
kb-folder-agent/
├── CLAUDE.md
├── .env                        # Never read or output this file
├── .env.example
├── requirements.txt
├── main.py                     # Entry point — starts watcher + Slack bot
│
├── mcp_servers/
│   ├── filesystem_server.py    # MCP server: list folders, read files, file metadata
│   └── vectordb_server.py      # MCP server: add docs, query embeddings, manage collections
│
├── ingestion/
│   ├── watcher.py              # Watchdog file monitor
│   ├── chunker.py              # File-type-specific chunking strategies
│   ├── embedder.py             # OpenAI embedding calls
│   └── quarantine.py           # Quarantine list for failed ingestion
│
├── agent/
│   ├── orchestrator.py         # MCP client — routes tool calls across servers
│   └── rag.py                  # RAG pipeline — retrieval + Anthropic reasoning
│
├── slack/
│   └── bot.py                  # Slash command handler (/kb ask, list, status, clear-quarantine)
│
└── storage/
    ├── db.py                   # async SQLite setup, init_db(), get_db()
    └── metadata.db             # created at runtime, never commit this
```

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Start the full agent (watcher + Slack bot)
python main.py

# Connect a folder to watch
python main.py connect ~/path/to/folder

# Run Slack bot only
python slack/bot.py

# Run file watcher only
python ingestion/watcher.py
```

---

## Environment Variables

Required in `.env` (see `.env.example`):

```
ANTHROPIC_API_KEY       # Claude reasoning
OPENAI_API_KEY          # text-embedding-3-small embeddings
SLACK_BOT_TOKEN         # xoxb-...
SLACK_SIGNING_SECRET    # Slack request verification
QDRANT_URL              # http://localhost:6333 (local Docker)
WATCHED_FOLDER          # Absolute path to the root folder being monitored
```

**NEVER read or output the contents of `.env`.**

---

## Architecture

### MCP Servers
- `filesystem_server.py` exposes tools: `list_folders`, `read_file`, `get_metadata`
- `vectordb_server.py` exposes tools: `add_documents`, `query_collection`, `list_collections`, `delete_collection`

### Ingestion Pipeline
1. Watcher detects new/modified files via `watchdog`
2. Compare `sha256(content)` hash against stored chunk-level hash in `metadata.db`
3. Only re-ingest chunks whose hash has changed
4. Chunk by file type (see Chunking section below)
5. Embed via OpenAI → store in Qdrant collection named after parent folder
6. Update `metadata.db` with new hashes and `last_ingested_at`

### Collection Routing
- Each top-level folder maps to one Qdrant collection
- Folder name → collection name (lowercased, underscored): `PastPerformance` → `past_performance`
- Slack users specify the folder explicitly: `/kb ask PastPerformance "question"`

### Quarantine
- Files that fail ingestion are logged in `metadata.db` with `error_type`, `retry_count`, `quarantined_at`
- `LockedFile`: retry up to 3x with backoff (30s → 2min → 10min), then quarantine
- `CorruptFile`, `TooLarge`, `UnsupportedType`: quarantine immediately, no retries
- Quarantined files are skipped in all future watcher cycles until manually cleared

---

## Chunking Strategy

Use file-type-specific chunkers in `ingestion/chunker.py`:

| File Type | Strategy |
|-----------|----------|
| `.pdf` | `pdfplumber` text extraction → 500–800 token semantic chunks, 50–100 token overlap. Extract tables separately as `chunk_type: "table"` |
| `.docx` | `python-docx` → section-aware chunks preserving headings. Strip tracked changes markup before chunking |
| `.md`, `.txt` | Paragraph-based — split on blank lines, keep code blocks intact |
| `.xlsx`, `.csv` | Convert each sheet to markdown table → chunk by logical row sections |
| `.py`, code files | Chunk by function/class boundaries, never mid-function |

---

## Metadata Schema (SQLite)

Each chunk stored with:
```
file_path, file_hash, chunk_index, chunk_hash, chunk_type,
last_ingested_at, source_folder, collection_name
```

Quarantine records:
```
file_path, error_type, error_message, retry_count,
last_attempted_at, quarantined_at, status
```

---

## Slack Commands

| Command | Description |
|---------|-------------|
| `/kb list` | Show all collections with file count and last updated |
| `/kb ask <folder> "<question>"` | Query a specific collection |
| `/kb status` | Show watcher status and quarantined files |
| `/kb clear-quarantine <folder> <filename>` | Remove file from quarantine |

---

## Rules

- **NEVER modify or output `.env`**
- Folder name → collection name mapping must be consistent: lowercase + underscores
- All database reads/writes go through `storage/metadata.db`
- Quarantine logic lives exclusively in `ingestion/quarantine.py`
- MCP tool calls are orchestrated only through `agent/orchestrator.py`
- Do not add new dependencies without confirming first

---

## Completed Phases

### Phase 1 — SQLite Schema ✔
- `storage/db.py` — async SQLite via `aiosqlite`
- `init_db()` — creates tables on startup, safe to call every run
- `get_db()` — async context manager, rows accessible by column name
- DB path resolves relative to file, works from any working directory
- Two tables: `chunks` (primary key: file_path + chunk_index), `quarantine` (primary key: file_path)
- All timestamps stored as ISO 8601 strings via `datetime.utcnow().isoformat()`

### Phase 2 — Quarantine System ✔
- `ingestion/quarantine.py` — all quarantine logic isolated here
- `ErrorType` enum: `LOCKED_FILE`, `CORRUPT_FILE`, `TOO_LARGE`, `UNSUPPORTED_TYPE`
- `should_retry()` — sync predicate; only `LOCKED_FILE` retries, max 3 times
- `RETRY_BACKOFF = [30, 120, 600]` — 30s, 2min, 10min escalation
- `quarantine_file()`, `increment_retry()`, `clear_quarantine()` — state mutations
- `is_quarantined()`, `get_retry_count()`, `get_quarantined_files()` — queries
- `ErrorType` extends `str` for direct DB serialization

### Phase 3 — Chunker ✔
- `ingestion/chunker.py` — 322 lines, file-type-specific chunking
- `ChunkResult` dataclass — content, chunk_index, chunk_type, metadata
- `UnsupportedFileTypeError` — raised for unsupported extensions, caught by quarantine system
- `chunk_file(file_path)` — async router, dispatches to correct chunker by extension
- Sub-chunkers are sync (file I/O is blocking), chunk_file is async to match caller contract
- Lazy imports for pdfplumber, docx, openpyxl — startup never fails if a library is missing
- `estimate_tokens()` — word count / 0.75 ratio
- `split_into_chunks()` — token-based splitting with overlap, uses same ratio as estimate_tokens
- `chunk_pdf()` — extracts tables separately as chunk_type='table', includes page_number in metadata
- `chunk_docx()` — section-aware, includes section_heading in metadata
- `chunk_markdown()` — single-pass line scan for fenced code blocks, chunk_type='code' for fences
- `chunk_spreadsheet()` — markdown table per sheet, 50-row groups, includes sheet_name + row_range
- `chunk_code()` — lookahead regex split on def/class/func/fn, language in metadata

### Phase 4 — Embedder ✔
- `ingestion/embedder.py` — 62 lines, OpenAI text-embedding-3-small
- `_embed_texts()` — internal helper owning retry logic; both public functions funnel through it
- `embed_chunks(chunks)` — batches ChunkResult list into groups of 100, returns list of dicts
- `embed_query(query)` — thin wrapper over `_embed_texts` for query-time embedding
- `_client` is module-level `AsyncOpenAI` — one instance reused across all calls, owns connection pool
- `os.environ["OPENAI_API_KEY"]` (not `.get()`) — fails loudly at import if key is missing
- Retry: up to 3 attempts, 2s sleep between attempts only; no sleep after final failure
- Errors logged via `logging.getLogger(__name__)` then re-raised — never swallowed

### Phase 5 — File Watcher ✔
- `ingestion/watcher.py` — 288 lines, watchdog-based file monitor with async ingestion
- `get_collection_name()` — top-level folder → lowercase+underscores via regex
- `compute_file_hash()` — sha256 in 64 KB blocks, never loads whole file into memory
- `ingest_file()` — chunk-level diff: only re-embeds chunks whose hash changed
- Point IDs are `abs(hash((file_path, chunk_index))) % 2**63` — deterministic, safe upserts
- `_ensure_collection()` — lazy Qdrant collection creation on first ingest
- Error routing: `FileNotFoundError/PermissionError` → LOCKED_FILE, `UnsupportedFileTypeError` → UNSUPPORTED_TYPE, >50 MB → TOO_LARGE, all else → CORRUPT_FILE
- `KBEventHandler` bridges watchdog OS threads → asyncio loop via `run_coroutine_threadsafe`
- `AsyncQdrantClient` instantiated per-call — no idle persistent connection
- `start_watcher()` — runs initial full scan via `asyncio.gather`, then starts observer loop
- `git commit -m "feat(ingestion): add file watcher with chunk-level diff ingestion and quarantine routing"`

### Phase 6 — Filesystem MCP Server ✔
- `mcp_servers/filesystem_server.py` — 138 lines, FastMCP decorator-based tool registration
- `FastMCP("filesystem")` — docstrings become tool descriptions exposed to MCP client
- Tools are sync — all local filesystem reads, FastMCP handles sync tools natively
- `list_folders()` — iterates top-level dirs, returns name, collection_name, path, file_count
- `read_file()` — guards on SUPPORTED_EXTENSIONS before extraction; lazy imports for pdfplumber/docx
- `get_metadata()` — never raises; returns `exists: False` with zeroed fields for missing files
- `list_files()` — rglob over supported extensions, returns per-file stat metadata
- `_collection_name()` — same regex logic as watcher.py for consistency
- `_iso()` — `fromtimestamp(..., tz=timezone.utc)` aware datetime, UTC ISO 8601
- `git commit -m "feat(mcp): add filesystem MCP server with list_folders, read_file, get_metadata, list_files"`

### Phase 7 — VectorDB MCP Server ✔
- `mcp_servers/vectordb_server.py` — 203 lines, FastMCP async tools over AsyncQdrantClient
- All tools are async — every operation awaits a Qdrant network call
- `try/finally` with `client.close()` on every tool — connection released even on error
- `list_collections()` — calls `get_collection` per collection for vector_count + status
- `query_collection()` — returns empty list if collection missing, no raise
- `add_documents()` — calls `_ensure_collection` then upserts; same `_point_id` logic as watcher.py
- `delete_document_chunks()` — scroll + delete-by-IDs pattern; works without a payload index
- `get_collection_info()` — catches bare `Exception` for missing collection; Qdrant raises non-public type
- `_point_id()` — sync pure math, consistent with watcher.py
- `QDRANT_URL` uses `.get()` with default — sensible local default, unlike API keys
- `git commit -m "feat(mcp): add vectordb MCP server with query, upsert, delete, and collection management"`

### Phase 8 — MCP Orchestrator ✔
- `agent/orchestrator.py` — 68 lines, single routing layer between RAG/Slack and MCP servers
- Direct function imports from MCP server modules — full transport is a future enhancement
- `search()` — embeds query via `embed_query()`, calls `query_collection()`, returns results
- `collection_exists()` — thin wrapper extracting `exists` bool from `get_collection_info()`
- `folder_to_collection_name()` — sync pure string transform; spaces+hyphens → underscores, lowercased
- `list_files` and `get_metadata` imported but not yet wrapped — available for future callers
- All async functions follow log-and-reraise pattern; orchestrator is routing layer, not error boundary
- `git commit -m "feat(agent): add MCP orchestrator routing search, collections, and folder queries"`

### Phase 9 — RAG Pipeline ✔
- `agent/rag.py` — 133 lines, Anthropic-powered retrieval-augmented generation
- `_call_claude()` — single Anthropic boundary; both public functions route through it
- `_SYSTEM_ANSWER`, `_SYSTEM_CHANGES` — module-level prompt constants for easy tuning
- `_unique_sources()` — preserves insertion order (relevance rank) via seen-list, not set
- `_build_context()` — formats each chunk as `[Source: filename]\ncontent`
- `answer_query()` — retrieves via orchestrator, builds context, calls Claude, returns RagResult
- `summarize_recent_changes()` — same pipeline with alternate system prompt; `days` param reserved for future metadata filter
- `RagResult` dataclass — answer, sources, collection_name, result_count
- `_client` is module-level `AsyncAnthropic`; `os.environ[]` fails loudly on missing key
- `git commit -m "feat(agent): add RAG pipeline with answer_query and summarize_recent_changes"`

### Phase 10 — Slack Bot ✔
- `slack/bot.py` — 193 lines, AsyncApp slash command handler for `/kb`
- `_parse_command()` — returns 3-tuple `(subcommand, folder, query)` for all subcommands
- `clear-quarantine` reuses the `query` slot for `filename` — two positional args, same parse path
- `WATCHED_FOLDER` imported lazily inside `_handle_clear_quarantine` — avoids circular import at module level
- `ack()` is always the first `await` — Slack requires acknowledgement within 3 seconds
- Socket Mode import is lazy — only pulled in when `SLACK_APP_TOKEN` is present
- `summarize_recent_changes` imported but no subcommand wired yet — available for future `/kb changes`
- All handlers wrapped in try/except; user-facing error message always returned, never unhandled exception
- `git commit -m "feat(slack): add /kb bot with ask, list, status, and clear-quarantine commands"`
