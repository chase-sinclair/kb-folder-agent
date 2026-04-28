import asyncio
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

_SYSTEM_GAPS = (
    "You are a proposal readiness analyst performing a critical gap analysis on a knowledge base. "
    "You are given content chunks from the knowledge base. Identify what is ABSENT or THIN — not what is present.\n\n"
    "Classify gaps as:\n"
    "- Hard Gap: Completely absent — no mention, no evidence anywhere in the provided content.\n"
    "- Soft Gap: Mentioned but thin — only one reference, no metrics, no COR/CO quotes, insufficient detail.\n\n"
    "Rules:\n"
    "- Be specific and actionable. 'No past performance with IL5 authorization' is good. "
    "'Documentation could be more comprehensive' is not.\n"
    "- For each gap, suggest the specific document or content that would fill it.\n"
    "- End with a single Priority line: the most impactful gap to address first.\n"
    "- Do not summarize what is present. Only report gaps.\n"
    "- Do not use markdown tables.\n\n"
    "Format your response exactly as:\n\n"
    "**Hard Gaps**\n"
    "• [specific gap]: [what would fill it]\n"
    "(maximum 4 hard gaps)\n\n"
    "**Soft Gaps**\n"
    "• [specific gap]: [what would fill it]\n"
    "(maximum 4 soft gaps)\n\n"
    "**Priority**\n"
    "[one-line recommendation]"
)

_SYSTEM_SCORE = (
    "You are a federal proposal evaluator applying an adjectival rating scale to assess how well a knowledge base "
    "supports a specific RFP requirement.\n\n"
    "Scoring scale:\n"
    "9–10 Outstanding: specific, measurable, directly relevant evidence with COR/CO validation\n"
    "7–8 Good: solid evidence but missing one key element (metrics, reference, or scale)\n"
    "5–6 Acceptable: relevant experience exists but is indirect or lacks detail\n"
    "3–4 Marginal: tangential evidence only, significant gaps\n"
    "1–2 Unacceptable: no relevant evidence found\n\n"
    "Instructions:\n"
    "1. Identify the distinct evaluation criteria embedded in the requirement.\n"
    "2. Score each criterion individually based on evidence in the knowledge base chunks.\n"
    "3. Produce a weighted composite score.\n"
    "4. Be explicit about what would raise the score — e.g. 'a CPARS rating specific to this capability would raise this from 7 to 9'.\n"
    "5. Never round up — if evidence is thin, the score must reflect it.\n"
    "6. Every strength and weakness point must cite a specific filename in parentheses.\n\n"
    "Format your response exactly as:\n\n"
    "COMPOSITE: [score]/10 — [adjectival rating]\n\n"
    "CRITERIA\n"
    "• [criterion name] — [sub-score]/10: [evidence summary] ([filename])\n\n"
    "STRENGTHS\n"
    "• [specific evidence point] ([filename])\n"
    "(2–3 points only)\n\n"
    "WEAKNESSES\n"
    "• [specific gap or missing element] ([filename or 'not found'])\n"
    "(2–3 points only)\n\n"
    "TO IMPROVE\n"
    "[single highest-leverage action that would most raise the composite score]"
)

_SYSTEM_DRAFT = (
    "You are a federal proposal writer drafting a compliant narrative section in response to an RFP requirement. "
    "You have been given content from a past performance and technical knowledge base.\n\n"
    "Rules:\n"
    "1. Write professional proposal prose — not bullet points, not a summary. This must read like a real proposal section.\n"
    "2. Directly address every element of the requirement. Mirror the requirement's language in your response.\n"
    "3. Pull specific facts from the knowledge base: contract values, dates, CPARS ratings, COR names, agency names, technical metrics.\n"
    "4. Where the knowledge base has no evidence for a specific required element, insert [EVIDENCE MISSING: <what's needed>] inline at that point.\n"
    "5. Never fabricate — every specific claim must trace to the provided content.\n"
    "6. Open with a strong topic sentence that directly addresses the requirement.\n"
    "7. Write 3–4 focused paragraphs, each addressing a distinct aspect of the requirement.\n"
    "8. End with a single line starting exactly with 'Coverage:' that summarizes which requirement elements were "
    "fully supported and which were flagged.\n\n"
    "Format:\n"
    "[3–4 paragraphs of proposal prose]\n\n"
    "Coverage: [supported elements] | Flagged: [missing elements, or 'none']"
)

_SYSTEM_COMPARE = (
    "You are a knowledge base analyst producing a structured comparative analysis between "
    "two collections: {{folder_a}} and {{folder_b}}. "
    "You are given chunks from each, labeled accordingly. "
    "Produce a genuine synthesis — not two summaries stapled together.\n\n"
    "Rules:\n"
    "1. Open with a DIRECT ANSWER: one sentence naming both collections that directly answers the question.\n"
    "2. Identify the specific comparison dimensions embedded in the question — use those, not generic categories.\n"
    "3. For each dimension: state what {{folder_a}} shows, what {{folder_b}} shows, and the meaningful difference.\n"
    "4. Identify where collections are COMPLEMENTARY (reinforce each other) vs where they DIVERGE.\n"
    "5. Close with a BOTTOM LINE paragraph synthesizing into an actionable conclusion.\n"
    "6. Never fabricate — if one collection has no evidence on a dimension, state it explicitly.\n"
    "7. Every specific claim must cite a filename in parentheses.\n\n"
    "Format your response exactly as:\n\n"
    "DIRECT ANSWER\n"
    "[one sentence]\n\n"
    "**Comparison Table**\n"
    "| Dimension | {{folder_a}} | {{folder_b}} |\n"
    "| --- | --- | --- |\n"
    "| [dimension] | [evidence] | [evidence] |\n\n"
    "**Complementary Strengths**\n"
    "• [where both collections reinforce each other] ([filename])\n\n"
    "**Divergences**\n"
    "• [where they meaningfully differ] ([filename])\n\n"
    "**Bottom Line**\n"
    "[actionable conclusion paragraph]"
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
    retrieved_items: list[dict] | None = None
    task_metadata: dict | None = None


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


def _serialize_results(results: list[dict], collection_name: str | None = None) -> list[dict]:
    items = []
    for result in results:
        filename = Path(result.get("file_path", "")).name
        items.append(
            {
                "collection_name": collection_name,
                "source_filename": filename,
                "file_path": result.get("file_path", ""),
                "score": result.get("score"),
                "content": result.get("content", ""),
                "chunk_type": result.get("chunk_type", "text"),
                "metadata": result.get("metadata", {}),
            }
        )
    return items


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
            retrieved_items=[],
        )
    try:
        results = await search(collection_name, query, top_k=TOP_K)

        if not results:
            return RagResult(
                answer="No relevant content was found in this knowledge base for your question.",
                sources=[],
                collection_name=collection_name,
                result_count=0,
                retrieved_items=[],
            )

        context = _build_context(results)
        user_prompt = f"Context:\n{context}\n\nQuestion: {query}"
        answer = await _call_claude(_SYSTEM_ANSWER, user_prompt)

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("answer_query failed for collection %r: %s", collection_name, exc)
        raise


async def answer_query_all(query: str) -> dict:
    if not query or not query.strip():
        return {
            "answer": "Please provide a question.",
            "sources_by_collection": {},
            "retrieved_items_by_collection": {},
            "total_result_count": 0,
        }
    try:
        results_by_collection = await search_all_collections(query)
        if not results_by_collection:
            return {
                "answer": "No relevant content found across any knowledge base.",
                "sources_by_collection": {},
                "retrieved_items_by_collection": {},
                "total_result_count": 0,
            }

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
        retrieved_items_by_collection = {
            col: _serialize_results(res, col) for col, res in results_by_collection.items()
        }
        total = sum(len(r) for r in results_by_collection.values())
        return {
            "answer": answer,
            "sources_by_collection": sources_by_collection,
            "retrieved_items_by_collection": retrieved_items_by_collection,
            "total_result_count": total,
        }
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
        return RagResult(
            answer="Please provide a question.",
            sources=[],
            collection_name=collection_name,
            result_count=0,
            retrieved_items=[],
        )
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
        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("answer_with_history failed for collection %r: %s", collection_name, exc)
        raise


async def draft_section(collection_name: str, requirement: str) -> RagResult:
    if not requirement or not requirement.strip():
        return RagResult(answer="Please provide a requirement.", sources=[], collection_name=collection_name, result_count=0)

    if len(requirement.split()) < 10:
        return RagResult(
            answer="Please provide the full requirement text — short inputs produce poor drafts.",
            sources=[],
            collection_name=collection_name,
            result_count=0,
        )

    try:
        results = await search(collection_name, requirement, top_k=20)

        if not results:
            return RagResult(
                answer="No relevant content found in this collection for the given requirement.",
                sources=[],
                collection_name=collection_name,
                result_count=0,
            )

        context = _build_context(results)
        user_prompt = f"Knowledge base content:\n{context}\n\nRFP requirement to address:\n{requirement}"
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=_SYSTEM_DRAFT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        answer = response.content[0].text

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("draft_section failed for collection %r: %s", collection_name, exc)
        raise


async def compare_collections(
    collection_name_a: str,
    collection_name_b: str,
    folder_a: str,
    folder_b: str,
    question: str,
) -> dict:
    if len(question.split()) < 8:
        return {"error": "Please be more specific — provide at least 8 words to define what dimensions you want compared."}

    try:
        results_a, results_b = await asyncio.gather(
            search(collection_name_a, question, top_k=10),
            search(collection_name_b, question, top_k=10),
        )

        if not results_a:
            return {"error": f"No content found in `{folder_a}` for this question."}
        if not results_b:
            return {"error": f"No content found in `{folder_b}` for this question."}

        paths_a = {r.get("file_path", "") for r in results_a}
        paths_b = {r.get("file_path", "") for r in results_b}
        overlap_files = sorted(Path(f).name for f in paths_a & paths_b if f)

        context_a = _build_context(results_a)
        context_b = _build_context(results_b)
        user_prompt = (
            f"Collection A ({folder_a}):\n{context_a}\n\n"
            f"Collection B ({folder_b}):\n{context_b}\n\n"
            f"Question: {question}"
        )
        system = _SYSTEM_COMPARE.replace("{{folder_a}}", folder_a).replace("{{folder_b}}", folder_b)
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        answer = response.content[0].text

        return {
            "answer": answer,
            "sources_a": _unique_sources(results_a),
            "sources_b": _unique_sources(results_b),
            "result_count_a": len(results_a),
            "result_count_b": len(results_b),
            "overlap_files": overlap_files,
            "retrieved_items_a": _serialize_results(results_a, collection_name_a),
            "retrieved_items_b": _serialize_results(results_b, collection_name_b),
        }
    except Exception as exc:
        log.error("compare_collections failed (%r vs %r): %s", collection_name_a, collection_name_b, exc)
        raise


async def score_requirement(collection_name: str, requirement: str) -> RagResult:
    if not requirement or not requirement.strip():
        return RagResult(answer="Please provide a requirement.", sources=[], collection_name=collection_name, result_count=0)

    if len(requirement.split()) < 10:
        return RagResult(
            answer="Please provide the full requirement text for accurate scoring — short inputs produce unreliable scores.",
            sources=[],
            collection_name=collection_name,
            result_count=0,
        )

    try:
        results = await search(collection_name, requirement, top_k=15)

        if not results:
            return RagResult(
                answer="No relevant content found in this collection for the given requirement.",
                sources=[],
                collection_name=collection_name,
                result_count=0,
            )

        context = _build_context(results)
        user_prompt = f"Knowledge base content:\n{context}\n\nRFP requirement to score:\n{requirement}"
        answer = await _call_claude(_SYSTEM_SCORE, user_prompt)

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("score_requirement failed for collection %r: %s", collection_name, exc)
        raise


async def find_gaps(collection_name: str, topic: str) -> RagResult:
    if not topic or not topic.strip():
        return RagResult(answer="Please provide a topic.", sources=[], collection_name=collection_name, result_count=0)
    try:
        results = await search(collection_name, topic, top_k=20)

        if len(results) < 5:
            return RagResult(
                answer="Not enough content to perform a meaningful gap analysis (fewer than 5 chunks found).",
                sources=[],
                collection_name=collection_name,
                result_count=len(results),
            )

        context = _build_context(results)
        user_prompt = f"Knowledge base content:\n{context}\n\nTopic for gap analysis: {topic}"
        answer = await _call_claude(_SYSTEM_GAPS, user_prompt)

        return RagResult(
            answer=answer,
            sources=_unique_sources(results),
            collection_name=collection_name,
            result_count=len(results),
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("find_gaps failed for collection %r: %s", collection_name, exc)
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
            retrieved_items=_serialize_results(results, collection_name),
        )
    except Exception as exc:
        log.error("summarize_recent_changes failed for collection %r: %s", collection_name, exc)
        raise


async def answer_query_eval(collection_name: str, query: str) -> dict:
    result = await answer_query(collection_name, query)
    return {
        "answer": result.answer,
        "retrieved_sources": result.sources,
        "collection_name": result.collection_name,
        "result_count": result.result_count,
        "retrieved_items": result.retrieved_items or [],
        "task_metadata": result.task_metadata or {},
    }


async def answer_query_all_eval(query: str) -> dict:
    result = await answer_query_all(query)
    sources_by_collection = result.get("sources_by_collection", {})
    retrieved_items_by_collection = result.get("retrieved_items_by_collection", {})
    flat_sources: list[str] = []
    flat_items: list[dict] = []

    for collection_name, sources in sources_by_collection.items():
        for source in sources:
            if source not in flat_sources:
                flat_sources.append(source)
        flat_items.extend(retrieved_items_by_collection.get(collection_name, []))

    return {
        "answer": result["answer"],
        "retrieved_sources": flat_sources,
        "result_count": result["total_result_count"],
        "retrieved_items": flat_items,
        "task_metadata": {"sources_by_collection": sources_by_collection},
    }


async def score_requirement_eval(collection_name: str, requirement: str) -> dict:
    result = await score_requirement(collection_name, requirement)
    return {
        "answer": result.answer,
        "retrieved_sources": result.sources,
        "collection_name": result.collection_name,
        "result_count": result.result_count,
        "retrieved_items": result.retrieved_items or [],
        "task_metadata": result.task_metadata or {},
    }


async def draft_section_eval(collection_name: str, requirement: str) -> dict:
    result = await draft_section(collection_name, requirement)
    return {
        "answer": result.answer,
        "retrieved_sources": result.sources,
        "collection_name": result.collection_name,
        "result_count": result.result_count,
        "retrieved_items": result.retrieved_items or [],
        "task_metadata": result.task_metadata or {},
    }


async def find_gaps_eval(collection_name: str, topic: str) -> dict:
    result = await find_gaps(collection_name, topic)
    return {
        "answer": result.answer,
        "retrieved_sources": result.sources,
        "collection_name": result.collection_name,
        "result_count": result.result_count,
        "retrieved_items": result.retrieved_items or [],
        "task_metadata": result.task_metadata or {},
    }


async def summarize_recent_changes_eval(collection_name: str, days: int = 3) -> dict:
    result = await summarize_recent_changes(collection_name, days=days)
    return {
        "answer": result.answer,
        "retrieved_sources": result.sources,
        "collection_name": result.collection_name,
        "result_count": result.result_count,
        "retrieved_items": result.retrieved_items or [],
        "task_metadata": {"days": days, **(result.task_metadata or {})},
    }


async def compare_collections_eval(
    collection_name_a: str,
    collection_name_b: str,
    folder_a: str,
    folder_b: str,
    question: str,
) -> dict:
    result = await compare_collections(collection_name_a, collection_name_b, folder_a, folder_b, question)
    if "error" in result:
        return {
            "answer": result["error"],
            "retrieved_sources": [],
            "result_count": 0,
            "retrieved_items": [],
            "task_metadata": {"error": result["error"]},
        }

    retrieved_sources = []
    for source in result.get("sources_a", []) + result.get("sources_b", []):
        if source not in retrieved_sources:
            retrieved_sources.append(source)

    return {
        "answer": result["answer"],
        "retrieved_sources": retrieved_sources,
        "result_count": result.get("result_count_a", 0) + result.get("result_count_b", 0),
        "retrieved_items": result.get("retrieved_items_a", []) + result.get("retrieved_items_b", []),
        "task_metadata": {
            "sources_a": result.get("sources_a", []),
            "sources_b": result.get("sources_b", []),
            "overlap_files": result.get("overlap_files", []),
            "collection_a": collection_name_a,
            "collection_b": collection_name_b,
        },
    }
