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


async def is_quarantined(file_path: str) -> bool:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM quarantine WHERE file_path = ? AND status = 'quarantined'",
            (file_path,),
        ) as cursor:
            return await cursor.fetchone() is not None


async def clear_quarantine(file_path: str) -> None:
    file_path = normalize_path(file_path)
    async with get_db() as db:
        await db.execute(
            """
            UPDATE quarantine
            SET status = 'cleared', last_attempted_at = ?
            WHERE file_path = ?
            """,
            (_now(), file_path),
        )
        await db.commit()


async def clear_all_quarantine() -> int:
    async with get_db() as db:
        async with db.execute("SELECT COUNT(*) FROM quarantine WHERE status = 'quarantined'") as cursor:
            row = await cursor.fetchone()
            count = row[0]
        await db.execute("UPDATE quarantine SET status = 'cleared', last_attempted_at = ? WHERE status = 'quarantined'", (_now(),))
        await db.commit()
    return count


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
