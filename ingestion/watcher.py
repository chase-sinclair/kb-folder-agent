import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ingestion.chunker import UnsupportedFileTypeError, chunk_file
from ingestion.embedder import embed_chunks
from ingestion.quarantine import (
    ErrorType,
    clear_quarantine,
    increment_retry,
    is_quarantined,
    normalize_path,
    quarantine_file,
    should_retry,
)
from storage.db import get_db, init_db

load_dotenv()

log = logging.getLogger(__name__)

WATCHED_FOLDER = Path(os.environ["WATCHED_FOLDER"])
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".txt",
    ".xlsx", ".csv",
    ".py", ".js", ".ts", ".go", ".rs",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_collection_name(file_path: str) -> str:
    rel = Path(file_path).relative_to(WATCHED_FOLDER)
    top_folder = rel.parts[0]
    return re.sub(r"[^a-z0-9]+", "_", top_folder.lower()).strip("_")


def compute_file_hash(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


async def get_stored_chunks(file_path: str) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT chunk_index, chunk_hash FROM chunks WHERE file_path = ?",
            (file_path,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------

async def _ensure_collection(client, collection_name: str) -> None:
    from qdrant_client.models import Distance, VectorParams

    existing = await client.get_collections()
    names = {c.name for c in existing.collections}
    if collection_name not in names:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection %r", collection_name)


async def _delete_file_from_qdrant(client, collection_name: str, file_path: str) -> None:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    await client.delete(
        collection_name=collection_name,
        points_selector=Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
        ),
    )


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

async def ingest_file(file_path: str) -> None:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import PointStruct

    file_path = normalize_path(file_path)
    path = Path(file_path)

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return

    if await is_quarantined(file_path):
        log.debug("Skipping quarantined file: %s", file_path)
        return

    async def handle_error(exc: Exception, error_type: ErrorType) -> None:
        retry_count = 0
        if should_retry(error_type, retry_count):
            await increment_retry(file_path)
            log.warning("Retryable error for %s (%s): %s", file_path, error_type.value, exc)
        else:
            await quarantine_file(file_path, error_type, str(exc))
            log.error("Quarantined %s (%s): %s", file_path, error_type.value, exc)

    try:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds 50 MB: {file_path}")

        file_hash = compute_file_hash(file_path)
        stored = await get_stored_chunks(file_path)
        stored_by_index = {r["chunk_index"]: r["chunk_hash"] for r in stored}

        chunks = await chunk_file(file_path)
        if not chunks:
            return

        collection_name = get_collection_name(file_path)
        source_folder = Path(file_path).relative_to(WATCHED_FOLDER).parts[0]

        changed_chunks = [
            c for c in chunks
            if stored_by_index.get(c.chunk_index) != hashlib.sha256(
                c.content.encode()
            ).hexdigest()
        ]

        if not changed_chunks:
            log.debug("No changed chunks for %s", file_path)
            return

        embedded = await embed_chunks(changed_chunks)

        client = AsyncQdrantClient(url=QDRANT_URL)
        await _ensure_collection(client, collection_name)

        points = [
            PointStruct(
                id=abs(hash((file_path, item["chunk_index"]))) % (2**63),
                vector=item["embedding"],
                payload={
                    "content": item["content"],
                    "metadata": item["metadata"],
                    "chunk_type": item["chunk_type"],
                    "file_path": file_path,
                },
            )
            for item in embedded
        ]
        await client.upsert(collection_name=collection_name, points=points)
        await client.close()

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        async with get_db() as db:
            for chunk in changed_chunks:
                chunk_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
                await db.execute(
                    """
                    INSERT OR REPLACE INTO chunks
                        (file_path, file_hash, chunk_index, chunk_hash, chunk_type,
                         last_ingested_at, source_folder, collection_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_path, file_hash, chunk.chunk_index, chunk_hash,
                        chunk.chunk_type, now, source_folder, collection_name,
                    ),
                )
            await db.commit()

        log.info("Ingested %d changed chunk(s) from %s", len(changed_chunks), file_path)

    except (FileNotFoundError, PermissionError) as exc:
        await handle_error(exc, ErrorType.LOCKED_FILE)
    except UnsupportedFileTypeError as exc:
        await handle_error(exc, ErrorType.UNSUPPORTED_TYPE)
    except ValueError as exc:
        if "50 MB" in str(exc):
            await handle_error(exc, ErrorType.TOO_LARGE)
        else:
            await handle_error(exc, ErrorType.CORRUPT_FILE)
    except Exception as exc:
        await handle_error(exc, ErrorType.CORRUPT_FILE)


async def delete_file(file_path: str) -> None:
    from qdrant_client import AsyncQdrantClient

    collection_name = get_collection_name(file_path)
    try:
        client = AsyncQdrantClient(url=QDRANT_URL)
        await _delete_file_from_qdrant(client, collection_name, file_path)
        await client.close()
    except Exception as exc:
        log.warning("Could not remove %s from Qdrant: %s", file_path, exc)

    async with get_db() as db:
        await db.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
        await db.commit()

    log.info("Removed chunks for deleted file: %s", file_path)


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------

class KBEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = loop

    def _submit(self, coro) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def on_created(self, event) -> None:
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTENSIONS:
            self._submit(ingest_file(event.src_path))

    def on_modified(self, event) -> None:
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTENSIONS:
            self._submit(ingest_file(event.src_path))

    def on_deleted(self, event) -> None:
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTENSIONS:
            self._submit(delete_file(event.src_path))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def start_watcher() -> None:
    await init_db()
    log.info("Starting initial scan of %s", WATCHED_FOLDER)

    scan_tasks = []
    for ext in SUPPORTED_EXTENSIONS:
        for path in WATCHED_FOLDER.rglob(f"*{ext}"):
            scan_tasks.append(ingest_file(str(path)))

    if scan_tasks:
        await asyncio.gather(*scan_tasks, return_exceptions=True)
    log.info("Initial scan complete — %d file(s) processed", len(scan_tasks))

    loop = asyncio.get_running_loop()
    handler = KBEventHandler(loop)
    observer = Observer()
    observer.schedule(handler, str(WATCHED_FOLDER), recursive=True)
    observer.start()
    log.info("Watching %s", WATCHED_FOLDER)

    try:
        while observer.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        log.info("Watcher stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_watcher())
