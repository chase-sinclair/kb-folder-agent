import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "metadata.db"

CREATE_CHUNKS = """
CREATE TABLE IF NOT EXISTS chunks (
    file_path       TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL,
    chunk_hash      TEXT NOT NULL,
    chunk_type      TEXT NOT NULL DEFAULT 'text',
    last_ingested_at TEXT NOT NULL,
    source_folder   TEXT NOT NULL,
    collection_name TEXT NOT NULL,
    PRIMARY KEY (file_path, chunk_index)
)
"""

CREATE_QUARANTINE = """
CREATE TABLE IF NOT EXISTS quarantine (
    file_path       TEXT PRIMARY KEY,
    error_type      TEXT NOT NULL,
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    quarantined_at  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'quarantined'
)
"""

CREATE_FILE_VERSIONS = """
CREATE TABLE IF NOT EXISTS file_versions (
    file_path       TEXT NOT NULL,
    version_index   INTEGER NOT NULL,
    content_snapshot TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    PRIMARY KEY (file_path, version_index)
)
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_CHUNKS)
        await db.execute(CREATE_QUARANTINE)
        await db.execute(CREATE_FILE_VERSIONS)
        await db.commit()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        yield db
