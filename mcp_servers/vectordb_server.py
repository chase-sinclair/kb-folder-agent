import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

load_dotenv()

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
VECTOR_SIZE = 1536
DISTANCE = Distance.COSINE

mcp = FastMCP("vectordb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=QDRANT_URL)


async def _ensure_collection(client: AsyncQdrantClient, collection_name: str) -> None:
    existing = await client.get_collections()
    names = {c.name for c in existing.collections}
    if collection_name not in names:
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=DISTANCE),
        )


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


@mcp.tool()
async def query_collection(
    collection_name: str,
    query_vector: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Queries a collection with an embedding vector and returns top_k results."""
    client = await _get_client()
    try:
        existing = await client.get_collections()
        names = {c.name for c in existing.collections}
        if collection_name not in names:
            return []

        response = await client.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "score": hit.score,
                "content": hit.payload.get("content", ""),
                "file_path": hit.payload.get("file_path", ""),
                "chunk_type": hit.payload.get("chunk_type", "text"),
                "metadata": hit.payload.get("metadata", {}),
            }
            for hit in response.points
        ]
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
