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
    MAX_RETRIES,
    RETRYABLE_ERRORS,
    ErrorType,
    classify_exception,
    is_quarantined,
    purge_orphaned_chunks,
    purge_stale_quarantine,
    quarantine_file,
    record_retry,
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

# ---------------------------------------------------------------------------
# Version snapshot helpers
#
# Mirrors ingestion/watcher.py. Kept local rather than imported, because
# watcher.py reads os.environ["WATCHED_FOLDER"] at import time and that is not
# set on an OneDrive-only install.
# ---------------------------------------------------------------------------

async def get_latest_version_hash(file_path: str) -> str | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT file_hash FROM file_versions WHERE file_path = ? "
            "ORDER BY version_index DESC LIMIT 1",
            (file_path,),
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def store_version_snapshot(file_path: str, content: str, file_hash: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        async with db.execute(
            "SELECT COALESCE(MAX(version_index), -1) FROM file_versions WHERE file_path = ?",
            (file_path,),
        ) as cursor:
            row = await cursor.fetchone()
        next_index = row[0] + 1
        await db.execute(
            "INSERT INTO file_versions (file_path, version_index, content_snapshot, file_hash, captured_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_path, next_index, content, file_hash, now),
        )
        await db.execute(
            "DELETE FROM file_versions WHERE file_path = ? AND version_index <= ?",
            (file_path, next_index - 5),
        )
        await db.commit()
    log.debug("Stored version %d snapshot for %s", next_index, file_path)


def _extract_text_for_snapshot(local_path: str, original_name: str) -> str | None:
    """Plain-text snapshot of a downloaded file, keyed off the OneDrive name.

    local_path is a temp file; original_name carries the real extension.
    """
    suffix = Path(original_name).suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            return Path(local_path).read_text(encoding="utf-8", errors="replace")
        if suffix == ".pdf":
            import pdfplumber
            with pdfplumber.open(local_path) as pdf:
                return "\n".join(page.extract_text() or "" for page in pdf.pages)
        if suffix == ".docx":
            from docx import Document
            doc = Document(local_path)
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        log.debug("Could not extract snapshot text for %s: %s", original_name, exc)
    return None


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


async def _prune_stale_chunk_rows(db, file_path: str, chunk_count: int) -> int:
    """Drop metadata rows for trailing chunks a shrunken file no longer has.

    Qdrant is already correct on this path because _delete_file_from_qdrant
    wipes the file's points before the re-upsert, but nothing removed the
    equivalent `chunks` rows, so metadata.db over-reported while the index was
    right. The divergence is silent and only evals.audit_ingestion notices it.

    `chunk_count` must be the FULL current chunk count. Mirrors
    ingestion/watcher.py; kept per-watcher like _ensure_collection and
    _delete_file_from_qdrant. Runs inside the caller's transaction.
    """
    cursor = await db.execute(
        "DELETE FROM chunks WHERE file_path = ? AND chunk_index >= ?",
        (file_path, chunk_count),
    )
    return cursor.rowcount or 0


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

async def ingest_onedrive_file(file_path: str, folder_name: str) -> None:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import PointStruct
    from mcp_servers.onedrive_server import get_metadata, read_file as od_read_file

    if await is_quarantined(file_path):
        log.warning("Skipping quarantined OneDrive file: %s", file_path)
        return

    async def handle_error(exc: Exception, error_type: ErrorType) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        if error_type in RETRYABLE_ERRORS:
            attempts = await record_retry(file_path, error_type, detail)
            if attempts <= MAX_RETRIES:
                log.warning(
                    "Retryable error for %s (%s, attempt %d/%d): %s",
                    file_path, error_type.value, attempts, MAX_RETRIES, detail,
                    exc_info=exc,
                )
                return
            log.error(
                "Giving up on %s after %d attempts (%s)", file_path, attempts - 1, error_type.value
            )
        await quarantine_file(file_path, error_type, detail)
        log.error("Quarantined %s (%s): %s", file_path, error_type.value, detail, exc_info=exc)

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

        # Version snapshot before chunking, while the temp file still exists.
        # Failures here must not block ingestion or quarantine the file.
        try:
            if await get_latest_version_hash(file_path) != file_hash:
                snapshot_text = await asyncio.to_thread(
                    _extract_text_for_snapshot, tmp_path, file_path
                )
                if snapshot_text is not None:
                    await store_version_snapshot(file_path, snapshot_text, file_hash)
        except Exception as exc:
            log.warning("Version snapshot failed for %s: %s", file_path, exc)

        chunks = await asyncio.to_thread(chunk_file, tmp_path)
        Path(tmp_path).unlink(missing_ok=True)
        tmp_path = None

        if not chunks:
            log.warning("No chunks extracted from OneDrive file (skipping): %s", file_path)
            return

        collection_name = get_collection_name(folder_name)

        # All chunks are "changed" since we compare at file-hash level
        embedded = await embed_chunks(chunks)

        from mcp_servers.vectordb_server import SPARSE_VECTOR_NAME, build_sparse_vector, point_id
        client = AsyncQdrantClient(url=QDRANT_URL)
        try:
            await _ensure_collection(client, collection_name)
            # Every chunk is re-embedded here (hash-level comparison), so drop the
            # file's existing points first: that clears both legacy points written
            # under the old randomised IDs and any chunks a shrinking file no
            # longer has.
            await _delete_file_from_qdrant(client, collection_name, file_path)
            points = [
                PointStruct(
                    id=point_id(file_path, item["chunk_index"]),
                    vector={
                        "": item["embedding"],
                        SPARSE_VECTOR_NAME: build_sparse_vector(item["content"]),
                    },
                    payload={
                        "content": item["content"],
                        "metadata": item["metadata"],
                        "chunk_type": item["chunk_type"],
                        "file_path": file_path,
                        "chunk_index": item["chunk_index"],
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
            pruned = await _prune_stale_chunk_rows(db, file_path, len(chunks))
            await db.commit()

        if pruned:
            log.info(
                "Pruned %d stale chunk row(s) for OneDrive:%s", pruned, file_path
            )
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
        await handle_error(exc, classify_exception(exc))
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
    # Qdrant may not be up yet. These purges are housekeeping, and main.py
    # gathers this coroutine with the Slack bot, so letting a connection error
    # escape took the bot down with it. Skip the round and carry on; files that
    # fail to ingest while Qdrant is cold are retried as TRANSIENT_ERROR.
    purged_v = purged_m = 0
    try:
        purged_v = await purge_orphaned_qdrant_points(valid_prefix)
        purged_m = await purge_chunks_for_missing_collections()
    except Exception as exc:
        log.warning("Skipping Qdrant startup cleanup (%s): %s", type(exc).__name__, exc)
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
