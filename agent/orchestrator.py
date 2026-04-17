import logging
import re

from dotenv import load_dotenv

from ingestion.embedder import embed_query
from mcp_servers.filesystem_server import list_files, list_folders, get_metadata
from mcp_servers.vectordb_server import (
    delete_document_chunks,
    get_collection_info,
    list_collections,
    query_collection,
)

load_dotenv()

log = logging.getLogger(__name__)


async def get_available_collections() -> list[dict]:
    try:
        return await list_collections()
    except Exception as exc:
        log.error("get_available_collections failed: %s", exc)
        raise


async def get_folder_list() -> list[dict]:
    try:
        return await list_folders()
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


def folder_to_collection_name(folder_name: str) -> str:
    return re.sub(r"[ \-]+", "_", folder_name).lower()
