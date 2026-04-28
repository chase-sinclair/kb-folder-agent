import json
import os

import anthropic

from evals.config import JUDGE_MAX_TOKENS, JUDGE_MODEL
from evals.schema import EvalTestCase, JudgeScores

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _extract_json(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Judge response did not contain JSON")
    return json.loads(candidate[start:end + 1])


async def judge_case(case: EvalTestCase, answer: str, retrieved_items: list[dict]) -> JudgeScores:
    context_parts = []
    for item in retrieved_items:
        label = item.get("collection_name") or case.collection or "unknown"
        source = item.get("source_filename") or "unknown"
        content = item.get("content", "")
        context_parts.append(f"[Collection: {label} | Source: {source}]\n{content}")
    context = "\n\n".join(context_parts) if context_parts else "No retrieved context."

    system = (
        "You are an evaluation judge for a retrieval-augmented knowledge base system. "
        "Return strict JSON only. Evaluate groundedness, completeness, citation accuracy, "
        "hallucination risk, unsupported claims, and missing elements."
    )
    user = (
        f"Task type: {case.task_type}\n"
        f"Query: {case.query}\n"
        f"Expected facts: {case.expected_facts}\n"
        f"Required sections: {case.required_sections}\n"
        f"Unacceptable claims: {case.unacceptable_claims}\n\n"
        f"Retrieved context:\n{context}\n\n"
        f"Answer:\n{answer}\n\n"
        "Return JSON with keys: groundedness_score, completeness_score, citation_accuracy_score, "
        "hallucination_risk, unsupported_claims, missing_elements, judge_notes."
    )

    response = await _get_client().messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    payload = _extract_json(response.content[0].text)
    return JudgeScores(
        groundedness_score=payload.get("groundedness_score"),
        completeness_score=payload.get("completeness_score"),
        citation_accuracy_score=payload.get("citation_accuracy_score"),
        hallucination_risk=payload.get("hallucination_risk"),
        unsupported_claims=payload.get("unsupported_claims", []),
        missing_elements=payload.get("missing_elements", []),
        judge_notes=payload.get("judge_notes", ""),
    )
