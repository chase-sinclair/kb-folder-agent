from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agent import orchestrator

router = APIRouter()


@router.get("")
async def list_collections():
    try:
        cols = await orchestrator.get_available_collections()
        return [
            {
                "name": c["name"],
                "chunk_count": c.get("vector_count", 0),
                "last_updated": "",
            }
            for c in cols
        ]
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/{name}/info")
async def get_collection_info(name: str):
    try:
        info = await orchestrator.get_collection_info(name)
        return {
            "name": info["name"],
            "chunk_count": info.get("vector_count", 0),
            "files": [],
            "last_updated": "",
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
