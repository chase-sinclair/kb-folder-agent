"""Pins the stale-chunk-row prune in both watchers.

Offline: no network, no Qdrant, no OpenAI, no pytest. Run with
    .\\.venv\\Scripts\\python.exe tests\\test_chunk_row_pruning.py

These drive the real ingest_file / ingest_onedrive_file control flow against a
temporary SQLite database, with only the two genuinely external dependencies
(the embedder and the Qdrant client) replaced by fakes. That matters: the bug
this pins was not in the DELETE statement, it was in a control-flow branch that
returned before any cleanup ran. A test that exercised only the SQL would have
passed against the broken code.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENAI_API_KEY", "test-not-used")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, cond: bool) -> None:
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Fakes for the two external dependencies
# --------------------------------------------------------------------------

class FakeQdrant:
    """Records calls. Tracks per-file point indices so Qdrant-side pruning is
    observable, which is what lets the shrink-only test assert on both stores."""

    store: dict[tuple[str, int], dict] = {}
    deleted_filters: list = []

    def __init__(self, *a, **kw):
        pass

    async def get_collections(self):
        class _R:
            collections = []
        return _R()

    async def create_collection(self, *a, **kw):
        return None

    async def update_collection(self, *a, **kw):
        return None

    async def upsert(self, collection_name=None, points=None):
        for p in points or []:
            FakeQdrant.store[(p.payload["file_path"], p.payload["chunk_index"])] = p.payload

    async def delete(self, collection_name=None, points_selector=None):
        FakeQdrant.deleted_filters.append(points_selector)
        # Emulate the two prune selectors and the wholesale file delete by
        # reading the conditions off the Filter object.
        fp = None
        gte = None
        wholesale = True
        for cond in getattr(points_selector, "must", []) or []:
            key = getattr(cond, "key", None)
            match = getattr(cond, "match", None)
            rng = getattr(cond, "range", None)
            if key == "file_path" and match is not None:
                fp = match.value
            if key == "chunk_index" and rng is not None:
                gte = rng.gte
                wholesale = False
            if getattr(cond, "is_empty", None) is not None:
                wholesale = False
        if fp is None:
            return
        for (f, idx) in list(FakeQdrant.store):
            if f != fp:
                continue
            if wholesale or (gte is not None and idx >= gte):
                del FakeQdrant.store[(f, idx)]

    async def close(self):
        return None

    @classmethod
    def reset(cls):
        cls.store = {}
        cls.deleted_filters = []

    @classmethod
    def indices_for(cls, file_path):
        return sorted(idx for (f, idx) in cls.store if f == file_path)


async def fake_embed_chunks(chunks):
    return [
        {
            "chunk_index": c.chunk_index,
            "content": c.content,
            "chunk_type": c.chunk_type,
            "metadata": getattr(c, "metadata", {}) or {},
            "embedding": [0.0] * 8,
        }
        for c in chunks
    ]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

async def db_rows(file_path):
    from storage.db import get_db
    async with get_db() as db:
        async with db.execute(
            "SELECT chunk_index FROM chunks WHERE file_path = ? ORDER BY chunk_index",
            (file_path,),
        ) as cur:
            return [r["chunk_index"] for r in await cur.fetchall()]


async def seed_rows(file_path, indices, collection="kb", folder="kb", file_hash="seed"):
    """Write chunk rows directly, standing in for an earlier ingest."""
    from storage.db import get_db
    async with get_db() as db:
        for i in indices:
            await db.execute(
                """INSERT OR REPLACE INTO chunks
                   (file_path, file_hash, chunk_index, chunk_hash, chunk_type,
                    last_ingested_at, source_folder, collection_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (file_path, file_hash, i, f"seedhash{i}", "text",
                 "2026-01-01T00:00:00+00:00", folder, collection),
            )
        await db.commit()


def section(n):
    """A markdown body that chunks to exactly n paragraph chunks."""
    return "\n\n".join(f"## Section {i}\n\n" + f"Body text for section {i}. " * 12 for i in range(n))


async def run():
    import qdrant_client
    import storage.db as sdb

    tmpdir = Path(tempfile.mkdtemp(prefix="kbtest_"))
    kb = tmpdir / "kb"
    (kb / "Folder").mkdir(parents=True)

    sdb.DB_PATH = tmpdir / "test.db"
    qdrant_client.AsyncQdrantClient = FakeQdrant

    from storage.db import init_db
    await init_db()

    os.environ["WATCHED_FOLDER"] = str(kb)
    import ingestion.watcher as W
    import ingestion.onedrive_watcher as OW
    W.WATCHED_FOLDER = kb
    W.embed_chunks = fake_embed_chunks
    OW.embed_chunks = fake_embed_chunks

    from ingestion.chunker import chunk_file

    # ---------------------------------------------------------------- 1
    print("test_shrink_removes_trailing_rows (local watcher, real ingest_file)")
    FakeQdrant.reset()
    f = kb / "Folder" / "shrink.md"
    f.write_text(section(6), encoding="utf-8")
    await W.ingest_file(str(f))
    big = await db_rows(W.normalize_path(str(f)))
    check(f"6-section file ingested to {len(big)} rows", len(big) >= 2)
    n_before = len(big)

    f.write_text(section(max(2, n_before - 2)), encoding="utf-8")
    await W.ingest_file(str(f))
    after = await db_rows(W.normalize_path(str(f)))
    expected = len(chunk_file(str(f)))
    check(f"rows match current chunk count ({len(after)} == {expected})", len(after) == expected)
    check("no row index >= chunk count", all(i < expected for i in after))
    check("row indices are contiguous from 0", after == list(range(expected)))

    # ---------------------------------------------------------------- 2
    print("test_grow_keeps_all_rows")
    FakeQdrant.reset()
    g = kb / "Folder" / "grow.md"
    g.write_text(section(2), encoding="utf-8")
    await W.ingest_file(str(g))
    small = len(await db_rows(W.normalize_path(str(g))))
    g.write_text(section(5), encoding="utf-8")
    await W.ingest_file(str(g))
    grown = await db_rows(W.normalize_path(str(g)))
    expected_g = len(chunk_file(str(g)))
    check(f"grown file has {expected_g} rows", len(grown) == expected_g)
    check("growing deleted nothing", len(grown) > small)

    # ---------------------------------------------------------------- 3
    print("test_unchanged_file_is_stable")
    FakeQdrant.reset()
    u = kb / "Folder" / "stable.md"
    u.write_text(section(4), encoding="utf-8")
    await W.ingest_file(str(u))
    first = await db_rows(W.normalize_path(str(u)))
    await W.ingest_file(str(u))
    second = await db_rows(W.normalize_path(str(u)))
    check("re-ingesting an unchanged file changes nothing", first == second and len(first) > 0)

    # ---------------------------------------------------------------- 4
    print("test_other_files_untouched (guards a missing file_path predicate)")
    FakeQdrant.reset()
    a = kb / "Folder" / "a.md"
    b = kb / "Folder" / "b.md"
    a.write_text(section(5), encoding="utf-8")
    b.write_text(section(5), encoding="utf-8")
    await W.ingest_file(str(a))
    await W.ingest_file(str(b))
    b_before = await db_rows(W.normalize_path(str(b)))
    a.write_text(section(2), encoding="utf-8")
    await W.ingest_file(str(a))
    b_after = await db_rows(W.normalize_path(str(b)))
    check("shrinking one file left the other intact", b_before == b_after and len(b_after) > 0)

    # ---------------------------------------------------------------- 5
    print("test_prune_bound_is_full_count_not_changed_count")
    # The len(changed_chunks) regression: many stored chunks, only the tail
    # removed, so the changed subset is tiny while most rows must survive.
    FakeQdrant.reset()
    c = kb / "Folder" / "bound.md"
    c.write_text(section(6), encoding="utf-8")
    await W.ingest_file(str(c))
    fp_c = W.normalize_path(str(c))
    n_full = len(await db_rows(fp_c))
    # Rewrite keeping every surviving section byte-identical, dropping the last.
    kept = "\n\n".join(section(6).split("\n\n")[: -2])
    c.write_text(kept, encoding="utf-8")
    real_chunks = chunk_file(str(c))
    await W.ingest_file(str(c))
    rows_c = await db_rows(fp_c)
    check(
        f"survivors kept, tail dropped ({n_full} -> {len(rows_c)}, expected {len(real_chunks)})",
        len(rows_c) == len(real_chunks),
    )
    check("prune did not delete live rows", len(rows_c) >= 1)

    # ---------------------------------------------------------------- 6
    print("test_shrink_with_no_changed_chunks_still_prunes (the early-return bug)")
    # Seed a phantom trailing row for a file whose on-disk chunks are all
    # already recorded with matching hashes. changed_chunks is therefore empty
    # and the pre-fix code returned before any cleanup.
    FakeQdrant.reset()
    s = kb / "Folder" / "noop.md"
    s.write_text(section(3), encoding="utf-8")
    fp_s = W.normalize_path(str(s))
    real = chunk_file(str(s))
    from storage.db import get_db
    async with get_db() as db:
        for ch in real:
            await db.execute(
                """INSERT OR REPLACE INTO chunks
                   (file_path, file_hash, chunk_index, chunk_hash, chunk_type,
                    last_ingested_at, source_folder, collection_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (fp_s, "h", ch.chunk_index,
                 hashlib.sha256(ch.content.encode()).hexdigest(), ch.chunk_type,
                 "2026-01-01T00:00:00+00:00", "Folder", "folder"),
            )
        await db.commit()
    await seed_rows(fp_s, [len(real), len(real) + 1], collection="folder", folder="Folder")
    FakeQdrant.store[(fp_s, len(real))] = {"file_path": fp_s, "chunk_index": len(real)}
    FakeQdrant.store[(fp_s, len(real) + 1)] = {"file_path": fp_s, "chunk_index": len(real) + 1}
    pre = await db_rows(fp_s)
    check(f"phantom tail present before ingest ({len(pre)} rows for {len(real)} chunks)",
          len(pre) == len(real) + 2)
    await W.ingest_file(str(s))
    post = await db_rows(fp_s)
    check(f"shrink-only ingest pruned SQLite to {len(real)} rows", post == list(range(len(real))))
    check("shrink-only ingest pruned Qdrant points too",
          FakeQdrant.indices_for(fp_s) == list(range(len(real))) or
          all(i < len(real) for i in FakeQdrant.indices_for(fp_s)))

    # ---------------------------------------------------------------- 7
    print("test_prune_is_atomic_with_upsert")
    FakeQdrant.reset()
    at = kb / "Folder" / "atomic.md"
    at.write_text(section(5), encoding="utf-8")
    await W.ingest_file(str(at))
    fp_at = W.normalize_path(str(at))
    before_at = await db_rows(fp_at)
    original_embed = W.embed_chunks

    boom_calls: list[int] = []

    async def boom(chunks):
        boom_calls.append(len(chunks))
        raise RuntimeError("embedder exploded")

    W.embed_chunks = boom
    # The shrunken content must DIFFER from the survivors, or there are no
    # changed chunks, the embedder is never called, and this asserts nothing.
    at.write_text(
        "\n\n".join(f"## Rewritten {i}\n\nWholly new body {i}. " * 1 for i in range(2)),
        encoding="utf-8",
    )
    await W.ingest_file(str(at))  # swallowed into quarantine; must not half-apply
    W.embed_chunks = original_embed
    after_at = await db_rows(fp_at)
    check(f"the failing embedder was actually reached (calls={boom_calls})", len(boom_calls) == 1)
    check("a mid-ingest failure deleted nothing", after_at == before_at)

    # ---------------------------------------------------------------- 8
    print("test_onedrive_watcher_prunes (real ingest_onedrive_file)")
    FakeQdrant.reset()
    fp_od = "testkb/Folder/od.md"
    await seed_rows(fp_od, [0, 1, 2, 3, 4], collection="folder", folder="Folder")
    od_local = tmpdir / "od_payload.md"
    od_local.write_text(section(2), encoding="utf-8")
    od_chunks = chunk_file(str(od_local))

    # ingest_onedrive_file imports these from mcp_servers.onedrive_server at call
    # time, so patch the module attributes rather than the watcher's.
    import mcp_servers.onedrive_server as ODS

    ODS.get_metadata = lambda file_path: {
        "exists": True,
        "name": "od.md",
        "size_bytes": od_local.stat().st_size,
    }
    ODS._download_to_temp = lambda file_path: str(od_local)

    await OW.ingest_onedrive_file(fp_od, "Folder")
    rows_od = await db_rows(fp_od)
    check(f"onedrive shrink pruned to {len(od_chunks)} rows", len(rows_od) == len(od_chunks))
    check("onedrive rows contiguous from 0", rows_od == list(range(len(od_chunks))))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} of {CHECKS} checks FAILED:")
        for f_ in FAILURES:
            print("  -", f_)
        return 1
    print(f"all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
