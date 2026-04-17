import asyncio
import logging
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

from ingestion.chunker import ChunkResult

load_dotenv()

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536
BATCH_SIZE = 100

_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

_MAX_ATTEMPTS = 3
_RETRY_WAIT = 2  # seconds


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await _client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            return [item.embedding for item in response.data]
        except Exception as exc:
            last_exc = exc
            log.warning("Embedding attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, exc)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_RETRY_WAIT)
    log.error("All %d embedding attempts failed", _MAX_ATTEMPTS)
    raise last_exc


async def embed_chunks(chunks: list[ChunkResult]) -> list[dict]:
    results: list[dict] = []
    for batch_start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[batch_start : batch_start + BATCH_SIZE]
        texts = [chunk.content for chunk in batch]
        embeddings = await _embed_texts(texts)
        for chunk, embedding in zip(batch, embeddings):
            results.append({
                "content": chunk.content,
                "embedding": embedding,
                "chunk_index": chunk.chunk_index,
                "chunk_type": chunk.chunk_type,
                "metadata": chunk.metadata,
            })
    return results


async def embed_query(query: str) -> list[float]:
    vectors = await _embed_texts([query])
    return vectors[0]
