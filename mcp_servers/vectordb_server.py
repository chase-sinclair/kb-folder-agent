import logging
import math
import os
import re
from collections import Counter

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

load_dotenv()

log = logging.getLogger(__name__)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
VECTOR_SIZE = 1536
DISTANCE = Distance.COSINE
SPARSE_VECTOR_NAME = "text-sparse"

mcp = FastMCP("vectordb")


def build_sparse_vector(text: str) -> SparseVector:
    """BM25-style sparse vector: tokens hashed to 24-bit indices, TF-weighted."""
    tokens = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1]
    if not tokens:
        return SparseVector(indices=[], values=[])
    counts = Counter(tokens)
    indices, values, seen = [], [], set()
    for token, count in counts.items():
        idx = abs(hash(token)) % (2**24)
        if idx in seen:
            continue
        seen.add(idx)
        indices.append(idx)
        values.append(float(1 + math.log(count)))
    return SparseVector(indices=indices, values=values)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=QDRANT_URL)


async def purge_chunks_for_missing_collections() -> int:
    """Delete SQLite chunk records whose Qdrant collection no longer exists."""
    from storage.db import get_db
    client = AsyncQdrantClient(url=QDRANT_URL)
    try:
        response = await client.get_collections()
        existing = {c.name for c in response.collections}
    finally:
        await client.close()

    async with get_db() as db:
        async with db.execute("SELECT DISTINCT collection_name FROM chunks") as cursor:
            rows = await cursor.fetchall()
        stale = [r["collection_name"] for r in rows if r["collection_name"] not in existing]
        for col in stale:
            await db.execute("DELETE FROM chunks WHERE collection_name = ?", (col,))
        if stale:
            await db.commit()
    return len(stale)


async def purge_orphaned_qdrant_points(valid_prefix: str) -> int:
    """Delete points from all collections whose file_path payload doesn't start with valid_prefix."""
    import logging
    log = logging.getLogger(__name__)
    valid_prefix = valid_prefix.rstrip("/") + "/"
    total_deleted = 0
    client = AsyncQdrantClient(url=QDRANT_URL)
    try:
        collections = await client.get_collections()
        for col in collections.collections:
            name = col.name
            orphan_ids: list[int] = []
            next_offset = None
            while True:
                results, next_offset = await client.scroll(
                    collection_name=name,
                    limit=200,
                    offset=next_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in results:
                    fp = (point.payload or {}).get("file_path", "")
                    if not fp.replace("\\", "/").startswith(valid_prefix):
                        orphan_ids.append(point.id)
                if next_offset is None:
                    break
            if orphan_ids:
                await client.delete(collection_name=name, points_selector=orphan_ids)
                total_deleted += len(orphan_ids)
                log.info("Purged %d orphaned Qdrant point(s) from collection %r", len(orphan_ids), name)
    finally:
        await client.close()
    return total_deleted


async def _ensure_collection(client: AsyncQdrantClient, collection_name: str) -> None:
    sparse_config = {SPARSE_VECTOR_NAME: SparseVectorParams(index=SparseIndexParams())}
    try:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
            sparse_vectors_config=sparse_config,
        )
        log.info("Created collection %r with hybrid search support", collection_name)
    except Exception as exc:
        msg = str(exc)
        if "409" in msg or "already exists" in msg.lower():
            try:
                await client.update_collection(
                    collection_name=collection_name,
                    sparse_vectors_config=sparse_config,
                )
                log.debug("Added sparse vector config to existing collection %r", collection_name)
            except Exception:
                pass  # already has it or version doesn't support update; continue
        else:
            raise


def _point_id(file_path: str, chunk_index: int) -> int:
    return abs(hash((file_path, chunk_index))) % (2**63)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_collections() -> list[dict]:
    """Returns all Qdrant collections with name, vector count, and status."""
    client = await _get_client()
    try:
        response = await client.get_collections()
        results = []
        for col in response.collections:
            info = await client.get_collection(col.name)
            results.append({
                "name": col.name,
                "vector_count": info.points_count or 0,
                "status": str(info.status),
            })
        return results
    finally:
        await client.close()


def _hits_to_dicts(points) -> list[dict]:
    return [
        {
            "score": hit.score,
            "content": hit.payload.get("content", ""),
            "file_path": hit.payload.get("file_path", ""),
            "chunk_type": hit.payload.get("chunk_type", "text"),
            "metadata": hit.payload.get("metadata", {}),
        }
        for hit in points
    ]


def _rrf_merge(dense_hits: list, sparse_hits: list, top_k: int, k: int = 60) -> list:
    """Reciprocal Rank Fusion of two ranked lists."""
    scores: dict[int, float] = {}
    payloads: dict[int, object] = {}
    for rank, hit in enumerate(dense_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
        payloads[hit.id] = hit
    for rank, hit in enumerate(sparse_hits):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
        payloads.setdefault(hit.id, hit)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [payloads[pid] for pid, _ in ranked]


@mcp.tool()
async def query_collection(
    collection_name: str,
    query_vector: list[float],
    top_k: int = 5,
    query_text: str = "",
) -> list[dict]:
    """Hybrid dense+sparse search when query_text is provided; dense-only fallback otherwise."""
    client = await _get_client()
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if collection_name not in names:
            return []

        if query_text:
            sparse_vec = build_sparse_vector(query_text)
            if sparse_vec.indices:
                try:
                    dense_resp = await client.query_points(
                        collection_name=collection_name,
                        query=query_vector,
                        using="",
                        limit=top_k * 2,
                        with_payload=True,
                    )
                    sparse_resp = await client.query_points(
                        collection_name=collection_name,
                        query=sparse_vec,
                        using=SPARSE_VECTOR_NAME,
                        limit=top_k * 2,
                        with_payload=True,
                    )
                    merged = _rrf_merge(dense_resp.points, sparse_resp.points, top_k)
                    log.debug("Hybrid RRF on %r: dense=%d sparse=%d merged=%d", collection_name, len(dense_resp.points), len(sparse_resp.points), len(merged))
                    return _hits_to_dicts(merged)
                except Exception as exc:
                    log.warning("Hybrid search failed for %r, falling back to dense: %s", collection_name, exc)

        response = await client.query_points(
            collection_name=collection_name,
            query=query_vector,
            using="",
            limit=top_k,
            with_payload=True,
        )
        return _hits_to_dicts(response.points)
    finally:
        await client.close()


@mcp.tool()
async def add_documents(collection_name: str, documents: list[dict]) -> dict:
    """Upserts documents into a collection, creating it if needed.

    Each document must have: content, embedding, chunk_index, chunk_type, metadata, file_path
    """
    client = await _get_client()
    try:
        await _ensure_collection(client, collection_name)

        points = [
            PointStruct(
                id=_point_id(doc["file_path"], doc["chunk_index"]),
                vector=doc["embedding"],
                payload={
                    "content": doc["content"],
                    "file_path": doc["file_path"],
                    "chunk_type": doc["chunk_type"],
                    "metadata": doc["metadata"],
                },
            )
            for doc in documents
        ]

        await client.upsert(collection_name=collection_name, points=points)
        return {"upserted_count": len(points), "collection_name": collection_name}
    finally:
        await client.close()


@mcp.tool()
async def delete_document_chunks(collection_name: str, file_path: str) -> dict:
    """Deletes all points in a collection whose payload.file_path matches."""
    client = await _get_client()
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if collection_name not in names:
            return {"deleted_count": 0, "collection_name": collection_name, "file_path": file_path}

        file_filter = Filter(
            must=[FieldCondition(key="file_path", match=MatchValue(value=file_path))]
        )

        # Scroll to collect all matching point IDs
        point_ids: list[int] = []
        next_offset = None
        while True:
            results, next_offset = await client.scroll(
                collection_name=collection_name,
                scroll_filter=file_filter,
                limit=100,
                offset=next_offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.extend(p.id for p in results)
            if next_offset is None:
                break

        if point_ids:
            await client.delete(
                collection_name=collection_name,
                points_selector=point_ids,
            )

        return {
            "deleted_count": len(point_ids),
            "collection_name": collection_name,
            "file_path": file_path,
        }
    finally:
        await client.close()


@mcp.tool()
async def get_collection_info(collection_name: str) -> dict:
    """Returns info about a single collection. Never raises — returns exists: False if not found."""
    client = await _get_client()
    try:
        info = await client.get_collection(collection_name)
        return {
            "name": collection_name,
            "vector_count": info.points_count or 0,
            "status": str(info.status),
            "exists": True,
        }
    except Exception:
        return {
            "name": collection_name,
            "vector_count": 0,
            "status": "not_found",
            "exists": False,
        }
    finally:
        await client.close()


if __name__ == "__main__":
    mcp.run()
