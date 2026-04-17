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
