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
    if start == -1:
        raise ValueError("Judge response did not contain JSON")
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge response JSON parse failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Judge response JSON was not an object")
    return payload


def _coerce_score(value) -> int | None:
    if value is None:
        return None
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(score, 10))


def _coerce_risk(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    mapping = {
        "low": "Low",
        "medium": "Medium",
        "high": "High",
    }
    return mapping.get(normalized)


def _coerce_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


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
        "Return strict JSON only with no markdown and no surrounding commentary. "
        "Evaluate groundedness, completeness, citation accuracy, hallucination risk, "
        "unsupported claims, and missing elements. "
        "Do not reward polished prose if it is not supported by retrieved context."
    )
    user = (
        f"Task type: {case.task_type}\n"
        f"Collection: {case.collection}\n"
        f"Query: {case.query}\n"
        f"Expected sources: {case.expected_sources}\n"
        f"Expected facts: {case.expected_facts}\n"
        f"Required sections: {case.required_sections}\n"
        f"Unacceptable claims: {case.unacceptable_claims}\n\n"
        f"Retrieved context:\n{context}\n\n"
        f"Answer:\n{answer}\n\n"
        "Judge using these rules:\n"
        "- groundedness_score: 1-10 based on whether claims are supported by retrieved context\n"
        "- completeness_score: 1-10 based on whether the answer addresses the whole request\n"
        "- citation_accuracy_score: 1-10 based on whether cited files meaningfully support the claims\n"
        "- hallucination_risk: Low, Medium, or High\n"
        "- unsupported_claims: only list claims that are asserted without support\n"
        "- missing_elements: list important missing pieces of the answer\n"
        "- judge_notes: one short paragraph\n\n"
        "Return exactly this JSON schema:\n"
        "{\n"
        '  "groundedness_score": 1,\n'
        '  "completeness_score": 1,\n'
        '  "citation_accuracy_score": 1,\n'
        '  "hallucination_risk": "Low",\n'
        '  "unsupported_claims": [],\n'
        '  "missing_elements": [],\n'
        '  "judge_notes": ""\n'
        "}"
    )

    response = await _get_client().messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    payload = _extract_json(response.content[0].text)
    return JudgeScores(
        groundedness_score=_coerce_score(payload.get("groundedness_score")),
        completeness_score=_coerce_score(payload.get("completeness_score")),
        citation_accuracy_score=_coerce_score(payload.get("citation_accuracy_score")),
        hallucination_risk=_coerce_risk(payload.get("hallucination_risk")),
        unsupported_claims=_coerce_list(payload.get("unsupported_claims")),
        missing_elements=_coerce_list(payload.get("missing_elements")),
        judge_notes=str(payload.get("judge_notes", "")).strip(),
    )
