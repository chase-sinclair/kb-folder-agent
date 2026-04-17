import logging
import os
from dataclasses import dataclass
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from agent.orchestrator import search

load_dotenv()

log = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-opus-4-5"
MAX_TOKENS = 1024
TOP_K = 5

_SYSTEM_ANSWER = (
    "You are a knowledge base assistant. Answer the user's question using only the "
    "provided context. Always cite your sources by filename. If the answer is not in "
    "the context, say so clearly."
)

_SYSTEM_CHANGES = (
    "You are a knowledge base assistant. Using only the provided context, summarize "
    "any recent changes, additions, or modifications you find. Focus on what is new or "
    "different. If no recent changes are evident in the context, say so clearly."
)


@dataclass
class RagResult:
    answer: str
    sources: list[str]
    collection_name: str
    result_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_context(results: list[dict]) -> str:
    parts = []
    for r in results:
        filename = Path(r.get("file_path", "unknown")).name
        parts.append(f"[Source: {filename}]\n{r['content']}")
    return "\n\n".join(parts)


def _unique_sources(results: list[dict]) -> list[str]:
    seen: list[str] = []
    for r in results:
        name = Path(r.get("file_path", "")).name
        if name and name not in seen:
            seen.append(name)
    return seen


async def _call_claude(system: str, user: str) -> str:
    try:
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
    except Exception as exc:
        log.error("Anthropic API call failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

async def answer_query(collection_name: str, query: str) -> RagResult:
    try:
        results = await search(collection_name, query, top_k=TOP_K)

        if not results:
            return RagResult(
                answer="No relevant content was found in this knowledge base for your question.",
                sources=[],
                collection_name=collection_name,
                result_count=0,
            )

        context = _build_context(results)
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        answer = await _call_claude(_SYSTEM_ANSWER, user_prompt)

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
        )
    except Exception as exc:
        log.error("answer_query failed for collection %r: %s", collection_name, exc)
        raise


async def summarize_recent_changes(collection_name: str, days: int = 3) -> RagResult:
    query = "What changed or was updated recently"
    try:
        results = await search(collection_name, query, top_k=TOP_K)

        if not results:
            return RagResult(
                answer="No recent changes were found in this knowledge base.",
                sources=[],
                collection_name=collection_name,
                result_count=0,
            )

        context = _build_context(results)
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        answer = await _call_claude(_SYSTEM_CHANGES, user_prompt)

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
        )
    except Exception as exc:
        log.error("summarize_recent_changes failed for collection %r: %s", collection_name, exc)
        raise
