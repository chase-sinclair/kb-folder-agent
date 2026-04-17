import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

from ingestion.embedder import embed_query
from mcp_servers.vectordb_server import (
    delete_document_chunks,
    get_collection_info,
    list_collections,
    query_collection,
)

BACKEND = os.environ.get("BACKEND", "local")

if BACKEND == "onedrive":
    from mcp_servers.onedrive_server import list_files, list_folders, get_metadata
else:
    from mcp_servers.filesystem_server import list_files, list_folders, get_metadata

log = logging.getLogger(__name__)

INFERENCE_CONFIDENCE_THRESHOLD = 0.35


async def get_available_collections() -> list[dict]:
    try:
        return await list_collections()
    except Exception as exc:
        log.error("get_available_collections failed: %s", exc)
        raise


async def get_folder_list() -> list[dict]:
    try:
        return list_folders()
    except Exception as exc:
        log.error("get_folder_list failed: %s", exc)
        raise


async def search(collection_name: str, query: str, top_k: int = 5) -> list[dict]:
    try:
        vector = await embed_query(query)
        return await query_collection(
            collection_name=collection_name,
            query_vector=vector,
            top_k=top_k,
        )
    except Exception as exc:
        log.error("search failed for collection %r, query %r: %s", collection_name, query, exc)
        raise


async def collection_exists(collection_name: str) -> bool:
    try:
        info = await get_collection_info(collection_name)
        return info["exists"]
    except Exception as exc:
        log.error("collection_exists check failed for %r: %s", collection_name, exc)
        raise


async def get_collection_status(collection_name: str) -> dict:
    try:
        return await get_collection_info(collection_name)
    except Exception as exc:
        log.error("get_collection_status failed for %r: %s", collection_name, exc)
        raise


async def search_all_collections(query: str, top_k_per_collection: int = 3) -> dict[str, list[dict]]:
    collections = await get_available_collections()
    if not collections:
        return {}
    vector = await embed_query(query)
    results = {}
    for col in collections:
        name = col["name"]
        try:
            hits = await query_collection(collection_name=name, query_vector=vector, top_k=top_k_per_collection)
            if hits:
                results[name] = hits
                log.info("search_all_collections: %d results from %r", len(hits), name)
        except Exception as exc:
            log.warning("search_all_collections: skipping %r due to error: %s", name, exc)
    return results


async def infer_collection(query: str) -> dict:
    collections = await get_available_collections()
    if not collections:
        return {"collection_name": None, "confidence": 0.0, "reason": "No collections available"}
    if len(collections) == 1:
        name = collections[0]["name"]
        log.info("infer_collection: single collection %r, confidence 1.0", name)
        return {"collection_name": name, "confidence": 1.0, "reason": f"Only collection available: {name}"}

    vector = await embed_query(query)
    best_name = None
    best_score = -1.0
    for col in collections:
        name = col["name"]
        try:
            hits = await query_collection(collection_name=name, query_vector=vector, top_k=1)
            if hits:
                score = hits[0].get("score", 0.0)
                if score > best_score:
                    best_score = score
                    best_name = name
        except Exception as exc:
            log.warning("infer_collection: skipping %r due to error: %s", name, exc)

    if best_name is None or best_score < INFERENCE_CONFIDENCE_THRESHOLD:
        log.info("infer_collection: low confidence %.3f for query %r", best_score, query)
        return {"collection_name": None, "confidence": best_score, "reason": "Low confidence — no strong match found"}

    log.info("infer_collection: routed to %r (score: %.3f)", best_name, best_score)
    return {
        "collection_name": best_name,
        "confidence": best_score,
        "reason": f"Best match in {best_name} (score: {best_score:.2f})",
    }


def folder_to_collection_name(folder_name: str) -> str:
    return re.sub(r"[ \-]+", "_", folder_name).lower()
