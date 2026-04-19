import asyncio
import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ingestion.chunker import UnsupportedFileTypeError, chunk_file
from ingestion.embedder import embed_chunks
from ingestion.quarantine import (
    ErrorType,
    increment_retry,
    is_quarantined,
    purge_orphaned_chunks,
    purge_stale_quarantine,
    quarantine_file,
    should_retry,
)
from storage.db import get_db, init_db

load_dotenv()

log = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
ONEDRIVE_FOLDER = os.environ.get("ONEDRIVE_FOLDER", "test-kb")
ONEDRIVE_POLL_INTERVAL = int(os.environ.get("ONEDRIVE_POLL_INTERVAL", "60"))

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_collection_name(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")


async def get_stored_file_hash(file_path: str) -> str | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT file_hash FROM chunks WHERE file_path = ? LIMIT 1",
            (file_path,),
        ) as cursor:
            row = await cursor.fetchone()
    return row["file_hash"] if row else None


def _compute_hash_from_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Qdrant helpers  (mirrors watcher.py)
# ---------------------------------------------------------------------------

async def _ensure_collection(client, collection_name: str) -> None:
    from qdrant_client.models import Distance, SparseIndexParams, SparseVectorParams, VectorParams
    from mcp_servers.vectordb_server import SPARSE_VECTOR_NAME

    sparse_config = {SPARSE_VECTOR_NAME: SparseVectorParams(index=SparseIndexParams())}
    try:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            sparse_vectors_config=sparse_config,
        )
        log.info("Created Qdrant collection %r", collection_name)
    except Exception as exc:
        msg = str(exc)
        if "409" in msg or "already exists" in msg.lower():
            log.debug("Collection %r already exists (concurrent create), continuing", collection_name)
            try:
                await client.update_collection(
                    collection_name=collection_name,
                    sparse_vectors_config=sparse_config,
                )
            except Exception:
                pass
        else:
            raise


# ---------------------------------------------------------------------------
# Core ingestion
# ---------------------------------------------------------------------------

async def ingest_onedrive_file(file_path: str, folder_name: str) -> None:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import PointStruct
    from mcp_servers.onedrive_server import get_metadata, read_file as od_read_file

    if await is_quarantined(file_path):
        log.warning("Skipping quarantined OneDrive file: %s", file_path)
        return

    async def handle_error(exc: Exception, error_type: ErrorType) -> None:
        retry_count = 0
        if should_retry(error_type, retry_count):
            await increment_retry(file_path)
            log.warning("Retryable error for %s (%s): %s", file_path, error_type.value, exc)
        else:
            await quarantine_file(file_path, error_type, str(exc))
            log.error("Quarantined %s (%s): %s", file_path, error_type.value, exc)

    tmp_path = None
    try:
        from mcp_servers.onedrive_server import _download_to_temp
        meta = await asyncio.to_thread(get_metadata, file_path)
        if not meta.get("exists"):
            log.warning("OneDrive file not found (skipping): %s", file_path)
            return

        if meta.get("size_bytes", 0) > MAX_FILE_SIZE:
            raise ValueError(f"File exceeds 50 MB: {file_path}")

        # Download to temp to compute hash
        tmp_path = await asyncio.to_thread(_download_to_temp, file_path)
        file_bytes = await asyncio.to_thread(Path(tmp_path).read_bytes)
        file_hash = _compute_hash_from_bytes(file_bytes)

        stored_hash = await get_stored_file_hash(file_path)
        if stored_hash == file_hash:
            log.debug("No change for %s", file_path)
            Path(tmp_path).unlink(missing_ok=True)
            tmp_path = None
            return

        # chunk_file needs a real local path with the correct extension
        ext = Path(file_path).suffix.lower()

        def _write_named_tmp() -> str:
            t = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            t.write(file_bytes)
            t.close()
            return t.name

        Path(tmp_path).unlink(missing_ok=True)
        tmp_path = await asyncio.to_thread(_write_named_tmp)

        chunks = await asyncio.to_thread(chunk_file, tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        tmp_path = None

        if not chunks:
            log.warning("No chunks extracted from OneDrive file (skipping): %s", file_path)
            return

        collection_name = get_collection_name(folder_name)

        # All chunks are "changed" since we compare at file-hash level
        embedded = await embed_chunks(chunks)

        from mcp_servers.vectordb_server import SPARSE_VECTOR_NAME, build_sparse_vector
        client = AsyncQdrantClient(url=QDRANT_URL)
        try:
            await _ensure_collection(client, collection_name)
            points = [
                PointStruct(
                    id=abs(hash((file_path, item["chunk_index"]))) % (2**63),
                    vector={
                        "": item["embedding"],
                        SPARSE_VECTOR_NAME: build_sparse_vector(item["content"]),
                    },
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
        finally:
            await client.close()

        now = datetime.now(timezone.utc).isoformat()
        async with get_db() as db:
            for chunk in chunks:
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
                        chunk.chunk_type, now, folder_name, collection_name,
                    ),
                )
            await db.commit()

        log.info("Ingested %d chunk(s) from OneDrive:%s", len(chunks), file_path)

    except PermissionError as exc:
        await handle_error(exc, ErrorType.LOCKED_FILE)
    except UnsupportedFileTypeError as exc:
        await handle_error(exc, ErrorType.UNSUPPORTED_TYPE)
    except ValueError as exc:
        if "50 MB" in str(exc):
            await handle_error(exc, ErrorType.TOO_LARGE)
        else:
            await handle_error(exc, ErrorType.CORRUPT_FILE)
    except Exception as exc:
        from qdrant_client.http.exceptions import UnexpectedResponse
        import aiohttp
        if isinstance(exc, UnexpectedResponse) and exc.status_code in (503, 429, 500):
            await handle_error(exc, ErrorType.TRANSIENT_ERROR)
        elif isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, TimeoutError)):
            await handle_error(exc, ErrorType.TRANSIENT_ERROR)
        else:
            await handle_error(exc, ErrorType.CORRUPT_FILE)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


async def _scan_all() -> int:
    from mcp_servers.onedrive_server import list_folders, list_files

    folders = await asyncio.to_thread(list_folders)
    tasks = []
    for folder in folders:
        folder_name = folder["name"]
        files = await asyncio.to_thread(list_files, folder_name)
        for f in files:
            tasks.append(ingest_onedrive_file(f["file_path"], folder_name))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                log.error("Unhandled exception during OneDrive scan: %r", result)

    return len(tasks)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def start_onedrive_watcher() -> None:
    from mcp_servers.vectordb_server import purge_chunks_for_missing_collections, purge_orphaned_qdrant_points
    await init_db()
    valid_prefix = ONEDRIVE_FOLDER
    purged_q = await purge_stale_quarantine(valid_prefix)
    purged_c = await purge_orphaned_chunks(valid_prefix)
    purged_v = await purge_orphaned_qdrant_points(valid_prefix)
    purged_m = await purge_chunks_for_missing_collections()
    if purged_q or purged_c or purged_v or purged_m:
        log.info("Startup cleanup: %d quarantine, %d chunk DB, %d Qdrant point(s), %d missing-collection record(s) removed",
                 purged_q, purged_c, purged_v, purged_m)
    log.info("Starting initial OneDrive scan of %s/%s", ONEDRIVE_FOLDER, "*")

    count = await _scan_all()
    log.info("Initial OneDrive scan complete — %d file(s) processed", count)

    while True:
        try:
            await asyncio.sleep(ONEDRIVE_POLL_INTERVAL)
            log.info("Polling OneDrive for changes...")
            changed = await _scan_all()
            log.info("Poll complete — %d file(s) checked", changed)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("OneDrive poll error: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_onedrive_watcher())
