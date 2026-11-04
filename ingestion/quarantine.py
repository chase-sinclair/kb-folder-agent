from datetime import datetime, timezone
from enum import Enum

from storage.db import get_db

MAX_RETRIES = 3
RETRY_BACKOFF = [30, 120, 600]  # seconds: 30s, 2min, 10min


class ErrorType(str, Enum):
    LOCKED_FILE = "LOCKED_FILE"
    CORRUPT_FILE = "CORRUPT_FILE"
    TOO_LARGE = "TOO_LARGE"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/")


RETRYABLE_ERRORS = {ErrorType.LOCKED_FILE, ErrorType.TRANSIENT_ERROR}


def should_retry(error_type: ErrorType, retry_count: int) -> bool:
    return error_type in RETRYABLE_ERRORS and retry_count < MAX_RETRIES


_TRANSIENT_MARKERS = (
    "connection aborted",
    "connection reset",
    "forcibly closed",
    "timed out",
    "timeout",
    "service unavailable",
    "temporarily unavailable",
    "bad gateway",
    "too many requests",
    " 429",
    " 500",
    " 502",
    " 503",
    " 504",
)


def classify_exception(exc: Exception) -> ErrorType:
    """Map an ingestion exception to an ErrorType.

    Network and upstream-service failures are TRANSIENT_ERROR (retried); only a
    genuinely unreadable or unparseable file should end up as CORRUPT_FILE.
    """
    try:
        from qdrant_client.http.exceptions import UnexpectedResponse

        if isinstance(exc, UnexpectedResponse) and exc.status_code in (429, 500, 502, 503, 504):
            return ErrorType.TRANSIENT_ERROR
    except Exception:
        pass

    try:
        import requests.exceptions as rexc

        if isinstance(exc, (rexc.ConnectionError, rexc.Timeout, rexc.ChunkedEncodingError)):
            return ErrorType.TRANSIENT_ERROR
        if isinstance(exc, rexc.HTTPError):
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (429, 500, 502, 503, 504):
                return ErrorType.TRANSIENT_ERROR
            if status is not None:
                # 400/401/403/404 will not fix themselves; retrying just delays
                # the report.
                return ErrorType.CORRUPT_FILE
        if isinstance(exc, rexc.RequestException):
            # Every requests exception subclasses OSError, so any that reached
            # here has to be settled now: the generic OSError check below would
            # otherwise read it as a network blip and retry it forever.
            return ErrorType.CORRUPT_FILE
    except ImportError:
        pass

    try:
        import aiohttp

        if isinstance(exc, (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError)):
            return ErrorType.TRANSIENT_ERROR
    except Exception:
        pass

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ErrorType.TRANSIENT_ERROR

    text = f" {exc} ".lower()
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return ErrorType.TRANSIENT_ERROR

    return ErrorType.CORRUPT_FILE


async def quarantine_file(file_path: str, error_type: ErrorType, error_message: str) -> None:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO quarantine
                (file_path, error_type, error_message, retry_count, quarantined_at,
                 last_attempted_at, status)
            VALUES (?, ?, ?, 0, ?, NULL, 'quarantined')
            """,
            (file_path, error_type.value, error_message, _now()),
        )
        await db.commit()


async def get_retry_count(file_path: str) -> int:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        async with db.execute(
            "SELECT retry_count FROM quarantine WHERE file_path = ?", (file_path,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["retry_count"] if row else 0


async def increment_retry(file_path: str) -> None:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        await db.execute(
            """
            UPDATE quarantine
            SET retry_count = retry_count + 1, last_attempted_at = ?
            WHERE file_path = ?
            """,
            (_now(), file_path),
        )
        await db.commit()


async def record_retry(file_path: str, error_type: ErrorType, error_message: str) -> int:
    """Persist a retryable failure and return the new attempt count.

    Status stays 'retrying' so is_quarantined() keeps letting the file through on
    the next pass. increment_retry() alone could not do this: it UPDATEs a row
    that does not exist yet, so retry_count never left 0.
    """
    file_path = normalize_path(file_path)
    async with get_db() as db:
        async with db.execute(
            "SELECT retry_count, quarantined_at FROM quarantine WHERE file_path = ?", (file_path,)
        ) as cursor:
            row = await cursor.fetchone()
        count = (row["retry_count"] if row else 0) + 1
        first_seen = row["quarantined_at"] if row else _now()
        await db.execute(
            """
            INSERT OR REPLACE INTO quarantine
                (file_path, error_type, error_message, retry_count, quarantined_at,
                 last_attempted_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'retrying')
            """,
            (file_path, error_type.value, error_message, count, first_seen, _now()),
        )
        await db.commit()
    return count


async def is_quarantined(file_path: str) -> bool:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM quarantine WHERE file_path = ? AND status = 'quarantined'",
            (file_path,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def clear_quarantine(file_path: str) -> int:
    """Clear one file. Returns the number of rows actually cleared (0 = no match)."""
    file_path = normalize_path(file_path)
    async with get_db() as db:
        cursor = await db.execute(
            """
            UPDATE quarantine
            SET status = 'cleared', retry_count = 0, last_attempted_at = ?
            WHERE file_path = ? AND status != 'cleared'
            """,
            (_now(), file_path),
        )
        await db.commit()
        return cursor.rowcount or 0


async def find_quarantined_path(folder_name: str, filename: str) -> str | None:
    """Locate a stored quarantine path by folder/filename, ignoring root prefix and case.

    The stored prefix depends on the active backend (a Windows path for local, the
    OneDrive root for onedrive), so matching on the tail is what makes the Slack
    command work under either one.
    """
    needle = f"/{normalize_path(folder_name).strip('/')}/{filename}".lower()
    async with get_db() as db:
        async with db.execute(
            "SELECT file_path FROM quarantine WHERE status != 'cleared'"
        ) as cursor:
            rows = await cursor.fetchall()
    for row in rows:
        if normalize_path(row["file_path"]).lower().endswith(needle):
            return row["file_path"]
    return None


async def clear_all_quarantine() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM quarantine WHERE status = 'quarantined'") as cursor:
            row = await cursor.fetchone()
            count = row[0]
        await db.execute("UPDATE quarantine SET status = 'cleared', retry_count = 0, last_attempted_at = ? WHERE status != 'cleared'", (_now(),))
        await db.commit()
    return count


async def purge_orphaned_chunks(valid_prefix: str) -> int:
    """Delete chunk records whose file_path doesn't start with valid_prefix."""
    valid_prefix = normalize_path(valid_prefix).rstrip("/") + "/"
    async with get_db() as db:
        async with db.execute("SELECT DISTINCT file_path FROM chunks") as cursor:
            rows = await cursor.fetchall()
        orphans = [r["file_path"] for r in rows if not normalize_path(r["file_path"]).startswith(valid_prefix)]
        for fp in orphans:
            await db.execute("DELETE FROM chunks WHERE file_path = ?", (fp,))
        if orphans:
            await db.commit()
    return len(orphans)


async def purge_stale_quarantine(watched_root: str) -> int:
    """Delete quarantine records whose file_path no longer falls under watched_root."""
    watched_root = normalize_path(watched_root).rstrip("/") + "/"
    async with get_db() as db:
        async with db.execute("SELECT file_path FROM quarantine WHERE status = 'quarantined'") as cursor:
            rows = await cursor.fetchall()
        stale = [r["file_path"] for r in rows if not normalize_path(r["file_path"]).startswith(watched_root)]
        for fp in stale:
            await db.execute("DELETE FROM quarantine WHERE file_path = ?", (fp,))
        if stale:
            await db.commit()
    return len(stale)


async def get_quarantined_files(folder: str | None = None) -> list[dict]:
    async with get_db() as db:
        if folder is not None:
            async with db.execute(
                "SELECT * FROM quarantine WHERE status = 'quarantined' AND file_path LIKE ?",
                (f"{normalize_path(folder)}%",),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM quarantine WHERE status = 'quarantined'"
            ) as cursor:
                rows = await cursor.fetchall()
        result = [dict(row) for row in rows]
        for r in result:
            r["file_path"] = normalize_path(r["file_path"])
        return result
