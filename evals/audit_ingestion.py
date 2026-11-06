"""Read-only reconciliation of source files -> metadata.db -> Qdrant.

    .\\.venv\\Scripts\\python.exe -m evals.audit_ingestion
    .\\.venv\\Scripts\\python.exe -m evals.audit_ingestion --source "C:/Users/chase/OneDrive/ConsultingKB" --verbose

Answers one question: does what is on disk match what is indexed? It re-chunks
every source file with the real ingestion chunker, compares per-file chunk counts
against the `chunks` table, compares the table against Qdrant point counts per
collection and per file, and lists any open quarantine rows.

Written after the 2026-11-05 ConsultingKB expansion, where the watcher ingested
32 of 40 files and quarantined the other 8 as CORRUPT_FILE -- a state in which
both Qdrant and SQLite were perfectly self-consistent at 213 chunks and nothing
in the system reported a problem. Agreement between the index and the database
proves nothing on its own; only the source tree is ground truth.

Strictly read-only: SELECTs through storage/db.py, and only get_collection /
scroll against Qdrant. It never writes, upserts, deletes or ingests.
"""

import argparse
import asyncio
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv

from ingestion.chunker import chunk_markdown
from storage.db import get_db

load_dotenv()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
SCROLL_PAGE = 256


def collection_name_for(folder_name: str) -> str:
    """The one mapping, per CLAUDE.md. Must stay identical to the watchers."""
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")


def default_source() -> Path | None:
    """Best guess at the watched root, overridable with --source."""
    backend = os.environ.get("BACKEND", "local").strip().lower()
    if backend == "onedrive":
        folder = os.environ.get("ONEDRIVE_FOLDER", "").strip()
        if not folder:
            return None
        # The watcher reads via Graph; this script needs real bytes, so it reads
        # the local sync mirror. Pass --source if OneDrive lives elsewhere.
        candidate = Path.home() / "OneDrive" / folder
        return candidate if candidate.is_dir() else None
    watched = os.environ.get("WATCHED_FOLDER", "").strip()
    if not watched:
        return None
    candidate = Path(watched)
    return candidate if candidate.is_dir() else None


def default_prefix() -> str:
    """The prefix metadata.db file_paths carry, so disk paths can be matched."""
    backend = os.environ.get("BACKEND", "local").strip().lower()
    if backend == "onedrive":
        return os.environ.get("ONEDRIVE_FOLDER", "").strip()
    return ""


# ---------------------------------------------------------------------------
# Sources of truth
# ---------------------------------------------------------------------------

def scan_source(source: Path, prefix: str) -> dict[str, dict]:
    """Re-chunk every .md under source with the real chunker.

    Keyed by the file_path the watcher would have stored, so the three sources
    can be joined without guessing.
    """
    expected: dict[str, dict] = {}
    for path in sorted(source.rglob("*.md")):
        rel = path.relative_to(source).as_posix()
        if "/" not in rel:
            # A file sitting at the root belongs to no collection; the watchers
            # key collections off the top-level folder, so flag rather than skip.
            folder = ""
        else:
            folder = rel.split("/")[0]
        key = f"{prefix}/{rel}" if prefix else rel
        try:
            chunks = chunk_markdown(str(path))
        except Exception as exc:  # pragma: no cover - surfaced in the report
            expected[key] = {"error": f"{type(exc).__name__}: {exc}", "folder": folder}
            continue
        expected[key] = {
            "chunks": len(chunks),
            "folder": folder,
            "collection": collection_name_for(folder) if folder else "",
            "short_chunks": [
                (i + 1, len(c.content.split()))
                for i, c in enumerate(chunks)
                if len(c.content.split()) < 50
            ],
        }
    return expected


async def read_db() -> tuple[dict[str, int], dict[str, str], list[dict], tuple[str | None, str | None]]:
    """Per-file chunk counts, per-file collection, open quarantine rows, ingest window."""
    async with get_db() as db:
        async with db.execute(
            "SELECT file_path, COUNT(*), MAX(collection_name) FROM chunks GROUP BY file_path"
        ) as cur:
            rows = await cur.fetchall()
        counts = {r[0]: r[1] for r in rows}
        collections = {r[0]: r[2] for r in rows}

        async with db.execute(
            """
            SELECT file_path, error_type, retry_count, last_attempted_at,
                   SUBSTR(COALESCE(error_message, ''), 1, 120)
            FROM quarantine WHERE status = 'quarantined' ORDER BY file_path
            """
        ) as cur:
            quarantined = [
                {
                    "file_path": r[0],
                    "error_type": r[1],
                    "retry_count": r[2],
                    "last_attempted_at": r[3],
                    "error_message": r[4],
                }
                for r in await cur.fetchall()
            ]

        async with db.execute("SELECT MIN(last_ingested_at), MAX(last_ingested_at) FROM chunks") as cur:
            window = tuple(await cur.fetchone())

    return counts, collections, quarantined, window


def read_qdrant(
    collections: set[str],
) -> tuple[dict[str, int | None], dict[str, Counter], list[str], list[str]]:
    """points_count per collection, plus points per file_path from a full scroll."""
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        return {}, {}, ["qdrant_client is not installed; skipped the Qdrant side"], []

    notes: list[str] = []
    legacy_notes: list[str] = []
    totals: dict[str, int | None] = {}
    per_file: dict[str, Counter] = {}
    try:
        client = QdrantClient(url=QDRANT_URL, timeout=20)
        existing = {c.name for c in client.get_collections().collections}
    except Exception as exc:
        return {}, {}, [f"Qdrant unreachable at {QDRANT_URL}: {type(exc).__name__}: {exc}"], []

    for name in sorted(collections | existing):
        if name not in existing:
            totals[name] = None
            notes.append(f"collection {name!r} expected from source folders but absent in Qdrant")
            continue
        try:
            totals[name] = client.get_collection(name).points_count
        except Exception as exc:
            totals[name] = None
            notes.append(f"collection {name!r}: get_collection failed: {type(exc).__name__}: {exc}")
            continue

        # Scroll the payloads to count points per source file and detect a
        # duplicated (file_path, chunk_index). Deterministic point IDs should
        # make duplicates impossible; this is the check that proves it.
        seen = Counter()
        pairs = Counter()
        legacy = Counter()
        offset = None
        try:
            while True:
                points, offset = client.scroll(
                    collection_name=name,
                    limit=SCROLL_PAGE,
                    offset=offset,
                    with_payload=["file_path", "chunk_index"],
                    with_vectors=False,
                )
                for p in points:
                    payload = p.payload or {}
                    fp = payload.get("file_path", "<missing file_path>")
                    seen[fp] += 1
                    idx = payload.get("chunk_index")
                    if idx is None:
                        # Points written before chunk_index joined the payload
                        # schema. Nothing reads it (point_id() derives the id
                        # from file_path:chunk_index at write time), so this is
                        # provenance, not a defect -- but it means duplicates
                        # cannot be detected for these points.
                        legacy[fp] += 1
                    else:
                        pairs[(fp, idx)] += 1
                if offset is None:
                    break
        except Exception as exc:
            notes.append(f"collection {name!r}: scroll failed: {type(exc).__name__}: {exc}")
        per_file[name] = seen
        for (fp, idx), n in pairs.items():
            if n > 1:
                notes.append(f"DUPLICATE points: {fp} chunk_index={idx} appears {n} times in {name}")
        if legacy:
            legacy_notes.append(
                f"{sum(legacy.values())} points in {name} predate the chunk_index payload "
                f"field ({len(legacy)} files); duplicate detection skipped for them"
            )

    if not existing:
        notes.append("Qdrant reports no collections at all")
    return totals, per_file, notes, legacy_notes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit: source files vs metadata.db vs Qdrant."
    )
    parser.add_argument("--source", help="Root folder of the corpus on disk.")
    parser.add_argument(
        "--prefix",
        help="Prefix that metadata.db file_paths carry (default: ONEDRIVE_FOLDER, or empty).",
    )
    parser.add_argument("--verbose", action="store_true", help="List every file, not just problems.")
    args = parser.parse_args()

    source = Path(args.source) if args.source else default_source()
    if source is None or not source.is_dir():
        print(
            "ERROR: could not determine the corpus root. Pass --source "
            '"C:/path/to/corpus".',
            file=sys.stderr,
        )
        return 2
    prefix = (args.prefix if args.prefix is not None else default_prefix()).strip("/")

    print(f"Source : {source}")
    print(f"Prefix : {prefix or '(none)'}")
    print(f"Qdrant : {QDRANT_URL}")
    print()

    expected = scan_source(source, prefix)
    db_counts, db_collections, quarantined, window = asyncio.run(read_db())
    wanted_collections = {v["collection"] for v in expected.values() if v.get("collection")}
    qdrant_totals, qdrant_per_file, qdrant_notes, legacy_notes = read_qdrant(wanted_collections)

    problems: list[str] = []
    unreadable = {k: v for k, v in expected.items() if "error" in v}
    for key, info in sorted(unreadable.items()):
        problems.append(f"UNREADABLE SOURCE: {key}: {info['error']}")

    readable = {k: v for k, v in expected.items() if "error" not in v}
    rootless = [k for k, v in readable.items() if not v["folder"]]
    for key in sorted(rootless):
        problems.append(f"FILE AT CORPUS ROOT (belongs to no collection): {key}")

    quarantine_by_path = {q["file_path"]: q for q in quarantined}

    absent: list[tuple[str, int, str]] = []
    partial: list[tuple[str, int, int]] = []
    matched = 0
    for key, info in sorted(readable.items()):
        got = db_counts.get(key)
        if got is None:
            q = quarantine_by_path.get(key)
            absent.append((key, info["chunks"], f"{q['error_type']} retries={q['retry_count']}" if q else "not quarantined"))
        elif got != info["chunks"]:
            partial.append((key, info["chunks"], got))
        else:
            matched += 1

    orphans = sorted(set(db_counts) - set(readable))

    # --- per-collection table -------------------------------------------------
    by_collection: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "expected": 0, "db": 0})
    for key, info in readable.items():
        col = info["collection"] or "(root)"
        by_collection[col]["files"] += 1
        by_collection[col]["expected"] += info["chunks"]
    for key, n in db_counts.items():
        col = db_collections.get(key) or "(unknown)"
        by_collection[col]["db"] += n

    print(f"{'collection':<20}{'files':>7}{'expected':>10}{'in db':>8}{'in qdrant':>11}")
    exp_total = db_total = 0
    for col in sorted(by_collection):
        row = by_collection[col]
        qd = qdrant_totals.get(col)
        print(
            f"{col:<20}{row['files']:>7}{row['expected']:>10}{row['db']:>8}"
            f"{('-' if qd is None else qd):>11}"
        )
        exp_total += row["expected"]
        db_total += row["db"]
        if qd is not None and qd != row["db"]:
            problems.append(
                f"QDRANT/DB MISMATCH in {col}: qdrant={qd} db={row['db']}"
            )
    qd_total = sum(v for v in qdrant_totals.values() if v is not None)
    print(f"{'TOTAL':<20}{len(readable):>7}{exp_total:>10}{db_total:>8}{qd_total:>11}")
    print()

    # --- per-file Qdrant cross-check -----------------------------------------
    for col, seen in qdrant_per_file.items():
        for fp, n in sorted(seen.items()):
            want = db_counts.get(fp)
            if want is None:
                problems.append(f"QDRANT POINTS WITH NO DB ROWS: {fp} ({n} points in {col})")
            elif want != n:
                problems.append(f"QDRANT/DB PER-FILE MISMATCH: {fp} qdrant={n} db={want}")

    if args.verbose:
        print("--- per file (expected / in db) ---")
        for key, info in sorted(readable.items()):
            got = db_counts.get(key)
            flag = "  " if got == info["chunks"] else "!!"
            print(f" {flag} {key:<62} {info['chunks']:>3} / {'-' if got is None else got}")
        print()

    # --- findings -------------------------------------------------------------
    print(f"files fully ingested : {matched}/{len(readable)}")
    print(f"ingest window        : {window[0]} .. {window[1]}")
    print()

    if absent:
        print(f"--- ABSENT FROM metadata.db ({len(absent)}) ---")
        for key, n, note in absent:
            print(f"  {key:<62} {n:>3} chunks   {note}")
            problems.append(f"ABSENT: {key} ({n} chunks, {note})")
        print()
    if partial:
        print(f"--- PARTIALLY INGESTED ({len(partial)}) ---")
        for key, want, got in partial:
            print(f"  {key:<62} expected {want}, got {got}")
            problems.append(f"PARTIAL: {key} expected {want} got {got}")
        print()
    if orphans:
        print(f"--- IN metadata.db BUT NOT ON DISK ({len(orphans)}) ---")
        for key in orphans:
            print(f"  {key:<62} {db_counts[key]:>3} chunks")
            problems.append(f"ORPHAN: {key} ({db_counts[key]} chunks)")
        print()
    if quarantined:
        print(f"--- OPEN QUARANTINE ROWS ({len(quarantined)}) ---")
        for q in quarantined:
            print(
                f"  {q['file_path']:<62} {q['error_type']:<16} "
                f"retries={q['retry_count']} last={q['last_attempted_at']}"
            )
            print(f"      {q['error_message']}")
            problems.append(f"QUARANTINED: {q['file_path']} ({q['error_type']})")
        print()

    short = [(k, v["short_chunks"]) for k, v in sorted(readable.items()) if v["short_chunks"]]
    if short:
        n = sum(len(v) for _, v in short)
        print(f"--- chunks under 50 words ({n} across {len(short)} files, informational) ---")
        for key, items in short:
            print(f"  {key}: " + ", ".join(f"#{i}={w}w" for i, w in items))
        print()

    for note in legacy_notes:
        print(f"INFO: {note}")
    if legacy_notes:
        print()

    for note in qdrant_notes:
        print(f"NOTE: {note}")
        problems.append(note)
    if qdrant_notes:
        print()

    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        return 1
    print(
        f"PASS: {len(readable)} files / {exp_total} chunks reconciled across disk, "
        f"metadata.db and Qdrant; no duplicates, orphans or quarantined files"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
