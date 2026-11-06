import asyncio
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

from agent import reranker
from ingestion.embedder import embed_query
from mcp_servers.vectordb_server import (
    delete_document_chunks,
    get_collection_info as _vdb_get_collection_info,
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


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("Invalid %s %r — using %d", name, raw, default)
        return default
    return value if value > 0 else default


# How deep to fetch before reranking. The reranker only reorders, so a wider
# candidate pool is the only way it can change which chunks reach the prompt.
RERANK_CANDIDATE_MULTIPLIER = _int_env("RERANK_CANDIDATE_MULTIPLIER", 3)
RERANK_MAX_CANDIDATES = _int_env("RERANK_MAX_CANDIDATES", 40)

# Over-fetch WITHOUT reranking. query_collection sizes each RRF leg at
# fetch_k * 2, so a deeper fetch_k changes which chunks survive fusion even
# with no LLM in the path -- recall and ranking are separate effects and this
# knob is what lets them be measured apart. The 2026-11-06 benchmark showed one
# case (P13) failing purely on recall: the answer-bearing chunk was never
# fetched, so no amount of reordering 5 chunks could have reached it.
# Default 1 is a no-op, which keeps the no-rerank path byte-identical to the
# pre-reranker behaviour and therefore keeps the archived baselines valid.
SEARCH_OVERFETCH_MULTIPLIER = _int_env("SEARCH_OVERFETCH_MULTIPLIER", 1)


def _candidate_k(top_k: int, multiplier: int | None = None) -> int:
    """Candidate pool size, capped. multiplier=None uses the rerank multiplier."""
    if multiplier is None:
        multiplier = RERANK_CANDIDATE_MULTIPLIER
    return max(top_k, min(top_k * multiplier, RERANK_MAX_CANDIDATES))


async def get_available_collections() -> list[dict]:
    try:
        return await list_collections()
    except Exception as exc:
        log.error("get_available_collections failed: %r", exc)
        raise


async def get_folder_list() -> list[dict]:
    try:
        # Both backends expose list_folders() synchronously, and the OneDrive one
        # is a blocking Graph API call. Running it inline stalls the event loop,
        # which delays Slack's socket-mode ack past its 3s deadline.
        return await asyncio.to_thread(list_folders)
    except Exception as exc:
        log.error("get_folder_list failed: %s", exc)
        raise


async def search(collection_name: str, query: str, top_k: int = 5, *, rerank: bool = True) -> list[dict]:
    """Hybrid search, optionally rerank-refined.

    `rerank` is keyword-only so no positional caller can break, and defaults to
    True so every rag.py entry point picks it up without an edit there. The
    agent loop opts out explicitly (see agent/agent_loop.py).

    With RERANK_ENABLED unset, fetch_k == top_k and the slice is a no-op, so
    this path is identical to the pre-reranker behaviour.
    """
    try:
        use_rerank = rerank and reranker.rerank_enabled()
        fetch_k = (
            _candidate_k(top_k)
            if use_rerank
            else _candidate_k(top_k, SEARCH_OVERFETCH_MULTIPLIER)
        )
        vector = await embed_query(query)
        hits = await query_collection(
            collection_name=collection_name,
            query_vector=vector,
            top_k=fetch_k,
            query_text=query,
        )
        if not use_rerank or len(hits) <= 1:
            return hits[:top_k]
        return await reranker.rerank(query, hits, top_k)
    except Exception as exc:
        log.error("search failed for collection %r, query %r: %s", collection_name, query, exc)
        raise


async def collection_exists(collection_name: str) -> bool:
    try:
        info = await _vdb_get_collection_info(collection_name)
        return info["exists"]
    except Exception as exc:
        log.error("collection_exists check failed for %r: %s", collection_name, exc)
        raise


async def get_collection_status(collection_name: str) -> dict:
    try:
        return await _vdb_get_collection_info(collection_name)
    except Exception as exc:
        log.error("get_collection_status failed for %r: %s", collection_name, exc)
        raise


async def get_collection_info(collection_name: str) -> dict:
    return await _vdb_get_collection_info(collection_name)


async def search_all(query: str, *, rerank: bool = True) -> list[dict]:
    """Flattened fan-out search across all collections. Each hit includes a collection_name key."""
    per_collection = await search_all_collections(query, rerank=rerank)
    flat: list[dict] = []
    for col_name, hits in per_collection.items():
        for hit in hits:
            flat.append({**hit, "collection_name": col_name})
    return flat


async def search_all_collections(
    query: str,
    top_k_per_collection: int = 3,
    *,
    rerank: bool = True,
) -> dict[str, list[dict]]:
    collections = await get_available_collections()
    if not collections:
        return {}
    use_rerank = rerank and reranker.rerank_enabled()
    fetch_k = (
        _candidate_k(top_k_per_collection)
        if use_rerank
        else _candidate_k(top_k_per_collection, SEARCH_OVERFETCH_MULTIPLIER)
    )
    vector = await embed_query(query)
    results = {}
    for col in collections:
        name = col["name"]
        try:
            hits = await query_collection(
                collection_name=name,
                query_vector=vector,
                top_k=fetch_k,
                query_text=query,
            )
            if hits:
                results[name] = hits
                log.info("search_all_collections: %d results from %r", len(hits), name)
        except Exception as exc:
            log.warning("search_all_collections: skipping %r due to error: %s", name, exc)

    if not use_rerank or not results:
        return {name: hits[:top_k_per_collection] for name, hits in results.items()}

    # Rerank per collection, not over a merged pool: the return type is keyed by
    # collection and answer_query_all() builds sources_by_collection from it.
    # Cross-collection budget reallocation is a separate, larger experiment.
    names = list(results)
    reranked = await asyncio.gather(
        *(reranker.rerank(query, results[name], top_k_per_collection) for name in names)
    )
    return dict(zip(names, reranked))


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
            # Deliberately bypasses search(): this compares raw scores ACROSS
            # collections against INFERENCE_CONFIDENCE_THRESHOLD. Reranking would
            # destroy that comparability (each call reorders within one
            # collection only) and cost one LLM call per collection per routing
            # hop. Do not "tidy" this into search().
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
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")
