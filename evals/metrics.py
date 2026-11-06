from evals.config import OVERALL_SCORE_WEIGHTS, PASS_THRESHOLD, WARN_THRESHOLD
from evals.schema import DeterministicScores


def _lower(text: str) -> str:
    return text.lower()


def _has_negated_context(answer_lower: str, claim: str) -> bool:
    claim_lower = _lower(claim)
    negation_markers = [
        "no information",
        "not found",
        "cannot answer",
        "can't answer",
        "could not find",
        "did not find",
        "not in the context",
        "not in this knowledge base",
        "there is no information",
        "there is no evidence",
    ]
    for marker in negation_markers:
        if marker in answer_lower and claim_lower in answer_lower:
            return True
    return False


def _score(matched: list[str], expected: list[str]) -> float | None:
    if not expected:
        return None
    return len(matched) / len(expected)


def check_expected_sources(retrieved_sources: list[str], expected_sources: list[str]) -> tuple[float | None, list[str], list[str]]:
    if not expected_sources:
        return None, [], []
    retrieved_lower = {_lower(source): source for source in retrieved_sources}
    matched = []
    missing = []
    for source in expected_sources:
        if _lower(source) in retrieved_lower:
            matched.append(source)
        else:
            missing.append(source)
    return _score(matched, expected_sources), matched, missing


def check_expected_source_rank(
    retrieved_sources: list[str],
    expected_sources: list[str],
) -> tuple[float | None, float | None]:
    """Order-sensitive companion to check_expected_sources().

    Returns (mrr, top1):
      mrr  - 1 / (1-based rank of the first expected source); 0.0 if none of
             them were retrieved at all.
      top1 - 1.0 if the highest-ranked retrieved source is an expected one.
    Both are None when the case declares no expected sources.

    REPORT-ONLY: deliberately absent from OVERALL_SCORE_WEIGHTS. Giving it a
    weight would renormalise every historical overall_score and re-run
    assign_status(), conflating "a metric was added" with "retrieval improved".

    check_expected_sources() is a set hit rate, so on a collection small enough
    that top_k covers all of it the set is frozen and only the order can move.
    This is the metric that can see that movement.

    Caveat: for question_answering_all, answer_query_all_eval() in agent/rag.py
    builds its source list collection-major, so fan-out MRR folds collection
    iteration order over within-collection rank. Treat fan-out MRR as
    directional only; single-collection cases are the honest signal.
    """
    if not expected_sources:
        return None, None
    expected_lower = {_lower(source) for source in expected_sources}
    mrr = 0.0
    for rank, source in enumerate(retrieved_sources, start=1):
        if _lower(source) in expected_lower:
            mrr = round(1.0 / rank, 4)
            break
    top1 = 1.0 if retrieved_sources and _lower(retrieved_sources[0]) in expected_lower else 0.0
    return mrr, top1


def check_expected_facts(answer: str, expected_facts: list[str]) -> tuple[float | None, list[str], list[str]]:
    if not expected_facts:
        return None, [], []
    answer_lower = _lower(answer)
    matched = [fact for fact in expected_facts if _lower(fact) in answer_lower]
    missing = [fact for fact in expected_facts if fact not in matched]
    return _score(matched, expected_facts), matched, missing


def check_required_sections(answer: str, required_sections: list[str]) -> tuple[float | None, list[str], list[str]]:
    if not required_sections:
        return None, [], []
    answer_lower = _lower(answer)
    matched = [section for section in required_sections if _lower(section) in answer_lower]
    missing = [section for section in required_sections if section not in matched]
    return _score(matched, required_sections), matched, missing


def check_missing_evidence_flags(answer: str, expected_flags: list[str]) -> tuple[float | None, list[str], list[str]]:
    if not expected_flags:
        return None, [], []
    answer_lower = _lower(answer)
    matched = [
        flag for flag in expected_flags
        if "[evidence missing:" in answer_lower and _lower(flag) in answer_lower
    ]
    missing = [flag for flag in expected_flags if flag not in matched]
    return _score(matched, expected_flags), matched, missing


def check_unacceptable_claims(answer: str, unacceptable_claims: list[str]) -> list[str]:
    if not unacceptable_claims:
        return []
    answer_lower = _lower(answer)
    present = []
    for claim in unacceptable_claims:
        if _lower(claim) in answer_lower and not _has_negated_context(answer_lower, claim):
            present.append(claim)
    return present


def check_citation_presence(
    answer: str,
    retrieved_sources: list[str],
    collection_name: str | None = None,
    collection_names: list[str] | None = None,
    require_collection: bool = False,
) -> tuple[bool, float | None]:
    answer_lower = _lower(answer)
    has_source = any(_lower(source) in answer_lower for source in retrieved_sources)
    if require_collection and collection_name:
        has_collection = _lower(collection_name) in answer_lower
        return has_source and has_collection, 1.0 if has_source and has_collection else 0.0
    if require_collection and collection_names:
        has_collection = any(_lower(name) in answer_lower for name in collection_names)
        return has_source and has_collection, 1.0 if has_source and has_collection else 0.0
    return has_source, 1.0 if has_source else 0.0


def check_not_found_handling(answer: str) -> tuple[bool, float]:
    answer_lower = _lower(answer)
    signals = [
        "not found",
        "not in the context",
        "not in this knowledge base",
        "no relevant content",
        "i could not find",
        "not provided",
        "cannot answer",
        "can't answer",
        "there is no information",
        "no information",
    ]
    detected = any(signal in answer_lower for signal in signals)
    return detected, 1.0 if detected else 0.0


def build_deterministic_scores(
    *,
    answer: str,
    retrieved_sources: list[str],
    expected_sources: list[str],
    expected_facts: list[str],
    required_sections: list[str],
    unacceptable_claims: list[str],
    expected_missing_evidence_flags: list[str],
    expected_not_found: bool,
    collection_name: str | None,
    collection_names: list[str] | None,
    citation_expected: bool,
    require_collection_citation: bool,
) -> DeterministicScores:
    source_score, matched_sources, missing_sources = check_expected_sources(retrieved_sources, expected_sources)
    source_mrr, source_top1 = check_expected_source_rank(retrieved_sources, expected_sources)
    fact_score, matched_facts, missing_facts = check_expected_facts(answer, expected_facts)
    section_score, matched_sections, missing_sections = check_required_sections(answer, required_sections)
    missing_flag_score, matched_flags, missing_flags = check_missing_evidence_flags(answer, expected_missing_evidence_flags)
    bad_claims = check_unacceptable_claims(answer, unacceptable_claims)
    if citation_expected:
        citation_present, citation_score = check_citation_presence(
            answer,
            retrieved_sources,
            collection_name=collection_name,
            collection_names=collection_names,
            require_collection=require_collection_citation,
        )
    else:
        citation_present, citation_score = False, None
    not_found_detected, not_found_score = check_not_found_handling(answer) if expected_not_found else (False, None)

    return DeterministicScores(
        expected_source_hit_rate=source_score,
        expected_source_mrr=source_mrr,
        expected_source_top1=source_top1,
        expected_fact_coverage=fact_score,
        required_section_compliance=section_score,
        missing_evidence_flag_compliance=missing_flag_score,
        citation_presence=citation_score,
        not_found_handling=not_found_score,
        matched_expected_sources=matched_sources,
        missing_expected_sources=missing_sources,
        matched_expected_facts=matched_facts,
        missing_expected_facts=missing_facts,
        matched_required_sections=matched_sections,
        missing_required_sections=missing_sections,
        matched_missing_evidence_flags=matched_flags,
        missing_expected_missing_evidence_flags=missing_flags,
        unacceptable_claims_present=bad_claims,
        citation_present=citation_present,
        not_found_detected=not_found_detected,
    )


def compute_overall_score(scores: DeterministicScores) -> float:
    weighted_total = 0.0
    weights_used = 0.0
    for field_name, weight in OVERALL_SCORE_WEIGHTS.items():
        value = getattr(scores, field_name)
        if value is None:
            continue
        weighted_total += value * weight
        weights_used += weight

    overall = weighted_total / weights_used if weights_used else 0.0
    if scores.unacceptable_claims_present:
        overall = min(overall, 0.25)
    return round(overall, 4)


def assign_status(scores: DeterministicScores, overall_score: float, expected_not_found: bool) -> str:
    if scores.unacceptable_claims_present:
        return "FAIL"
    if scores.expected_source_hit_rate == 0.0:
        return "FAIL"
    if expected_not_found and not scores.not_found_detected:
        return "FAIL"
    if overall_score >= PASS_THRESHOLD:
        return "PASS"
    if overall_score >= WARN_THRESHOLD:
        return "WARN"
    return "FAIL"
