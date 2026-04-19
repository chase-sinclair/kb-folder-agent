import difflib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from agent.orchestrator import search, search_all_collections

load_dotenv()

log = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-opus-4-5"
MAX_TOKENS = 1024
TOP_K = 5

_SYSTEM_ANSWER = (
    "You are a knowledge base assistant. Answer the user's question using only the provided context. "
    "Cite sources by filename inline (e.g. 'According to Resume.pdf, ...'). "
    "If the answer is not in the context, say so clearly. "
    "Do not mention duplicates, retrieval mechanics, or that you were given context — just answer. "
    "Do not use markdown tables; present tabular data as a plain bulleted list instead. "
    "Lead directly with the answer — no preamble like 'Based on the context provided'."
)

_SYSTEM_CHANGES = (
    "You are a knowledge base assistant. Summarize the recent changes, additions, or modifications "
    "found in the provided context. Lead directly with the changes — do not open with phrases like "
    "'Based on the provided context' or 'I can identify'. Use a short bulleted list. "
    "If no recent changes are evident, say so in one sentence. "
    "Do not mention duplicates, retrieval mechanics, or that you were given context."
)

_SYSTEM_DIFF = (
    "You are a document analyst. Summarize the changes between two versions of a document in plain English. "
    "Lead directly with what changed — no preamble. Use bullet points: additions, removals, modifications. "
    "Be concise and specific."
)

_SYSTEM_ALL = (
    "You are a knowledge base assistant with access to multiple knowledge bases. "
    "Answer the user's question using only the provided context. "
    "Cite both the collection name and source filename for each fact inline. "
    "If the answer is not in the context, say so clearly. "
    "Do not mention duplicates, retrieval mechanics, or that you were given context. "
    "Do not use markdown tables; present tabular data as a plain bulleted list instead. "
    "Lead directly with the answer — no preamble."
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
    if not query or not query.strip():
        return RagResult(
            answer="Please provide a question.",
            sources=[],
            collection_name=collection_name,
            result_count=0,
        )
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


async def answer_query_all(query: str) -> dict:
    if not query or not query.strip():
        return {"answer": "Please provide a question.", "sources_by_collection": {}, "total_result_count": 0}
    try:
        results_by_collection = await search_all_collections(query)
        if not results_by_collection:
            return {"answer": "No relevant content found across any knowledge base.", "sources_by_collection": {}, "total_result_count": 0}

        context_parts = []
        for col_name, results in results_by_collection.items():
            context_parts.append(f"[Collection: {col_name}]")
            for r in results:
                filename = Path(r.get("file_path", "unknown")).name
                context_parts.append(f"[Source: {filename}]\n{r['content']}")
        context = "\n\n".join(context_parts)
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        answer = await _call_claude(_SYSTEM_ALL, user_prompt)

        sources_by_collection = {col: _unique_sources(res) for col, res in results_by_collection.items()}
        total = sum(len(r) for r in results_by_collection.values())
        return {"answer": answer, "sources_by_collection": sources_by_collection, "total_result_count": total}
    except Exception as exc:
        log.error("answer_query_all failed: %s", exc)
        raise


async def summarize_diff(file_path: str) -> dict:
    from storage.db import get_db
    async with get_db() as db:
        async with db.execute(
            "SELECT version_index, content_snapshot FROM file_versions "
            "WHERE file_path = ? ORDER BY version_index DESC LIMIT 2",
            (file_path,),
        ) as cursor:
            rows = await cursor.fetchall()

    if len(rows) < 2:
        return {"answer": "Not enough version history to compare.", "file_path": file_path}

    v_new, v_old = dict(rows[0]), dict(rows[1])
    diff_lines = list(difflib.unified_diff(
        v_old["content_snapshot"].splitlines(keepends=True),
        v_new["content_snapshot"].splitlines(keepends=True),
        fromfile=f"v{v_old['version_index']}",
        tofile=f"v{v_new['version_index']}",
        lineterm="",
    ))

    if not diff_lines:
        return {"answer": "No differences found between the two most recent versions.", "file_path": file_path}

    diff_text = "".join(diff_lines)
    if len(diff_text) > 3000:
        diff_text = diff_text[:3000] + "\n...[truncated]"

    user_prompt = f"Diff:\n```\n{diff_text}\n```"
    try:
        answer = await _call_claude(_SYSTEM_DIFF, user_prompt)
    except Exception as exc:
        log.error("summarize_diff failed for %r: %s", file_path, exc)
        raise

    return {
        "answer": answer,
        "file_path": file_path,
        "versions_compared": [v_old["version_index"], v_new["version_index"]],
    }


async def answer_with_history(collection_name: str, history: list[dict], query: str) -> RagResult:
    if not query or not query.strip():
        return RagResult(answer="Please provide a question.", sources=[], collection_name=collection_name, result_count=0)
    try:
        results = await search(collection_name, query, top_k=TOP_K)
        context = _build_context(results) if results else "No relevant content found."
        messages = list(history) + [{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"}]
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_ANSWER,
            messages=messages,
        )
        answer = response.content[0].text
        return RagResult(answer=answer, sources=_unique_sources(results), collection_name=collection_name, result_count=len(results))
    except Exception as exc:
        log.error("answer_with_history failed for collection %r: %s", collection_name, exc)
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
