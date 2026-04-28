from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agent.orchestrator import collection_exists, folder_to_collection_name
from agent.rag import (
    answer_query_all_eval,
    answer_query_eval,
    compare_collections_eval,
    draft_section_eval,
    find_gaps_eval,
    score_requirement_eval,
    summarize_recent_changes_eval,
)
from evals.judge import judge_case
from evals.metrics import assign_status, build_deterministic_scores, compute_overall_score
from evals.schema import EvalCaseResult, EvalRunConfig, EvalRunSummary, EvalTestCase


def load_test_cases(path: str) -> list[EvalTestCase]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw_cases = data.get("test_cases", [])
    return [EvalTestCase(**raw_case) for raw_case in raw_cases]


def filter_cases(cases: list[EvalTestCase], config: EvalRunConfig) -> list[EvalTestCase]:
    filtered = cases
    if config.case_id:
        filtered = [case for case in filtered if case.id == config.case_id]
    if config.task_type:
        filtered = [case for case in filtered if case.task_type == config.task_type]
    if config.collection:
        filtered = [case for case in filtered if case.collection == config.collection]
    return filtered


async def _require_collection(folder_name: str | None) -> tuple[str | None, str | None]:
    if not folder_name:
        return None, "This task requires a collection."
    collection_name = folder_to_collection_name(folder_name)
    if not await collection_exists(collection_name):
        return None, f"Collection `{folder_name}` does not exist."
    return collection_name, None


async def _dispatch_case(case: EvalTestCase) -> dict:
    if case.task_type in {"question_answering", "not_found"}:
        collection_name, error = await _require_collection(case.collection)
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await answer_query_eval(collection_name, case.query)

    if case.task_type == "question_answering_all":
        return await answer_query_all_eval(case.query)

    if case.task_type == "requirement_scoring":
        collection_name, error = await _require_collection(case.collection)
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await score_requirement_eval(collection_name, case.query)

    if case.task_type == "proposal_drafting":
        collection_name, error = await _require_collection(case.collection)
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await draft_section_eval(collection_name, case.query)

    if case.task_type == "gap_analysis":
        collection_name, error = await _require_collection(case.collection)
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await find_gaps_eval(collection_name, case.query)

    if case.task_type == "changes_summary":
        collection_name, error = await _require_collection(case.collection)
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await summarize_recent_changes_eval(collection_name)

    if case.task_type == "collection_compare":
        collection_name_a, error_a = await _require_collection(case.collection)
        collection_name_b, error_b = await _require_collection(case.collection_b)
        error = error_a or error_b
        if error:
            return {"answer": error, "retrieved_sources": [], "result_count": 0, "retrieved_items": [], "task_metadata": {"error": error}}
        return await compare_collections_eval(
            collection_name_a,
            collection_name_b,
            case.collection,
            case.collection_b,
            case.query,
        )

    raise ValueError(f"Unsupported task type: {case.task_type}")


def _build_recommendations(case: EvalTestCase, result: EvalCaseResult) -> list[str]:
    recommendations: list[str] = []
    scores = result.deterministic_scores
    if scores.missing_expected_sources:
        recommendations.append("Improve retrieval so expected source files appear in the top results.")
    if scores.missing_expected_facts:
        recommendations.append("Tighten prompting so the answer explicitly covers the expected facts.")
    if scores.missing_required_sections:
        recommendations.append("Strengthen the task prompt or formatter to enforce required sections.")
    if scores.missing_expected_missing_evidence_flags:
        recommendations.append("Ensure unsupported proposal details are flagged with explicit evidence-missing markers.")
    if scores.unacceptable_claims_present:
        recommendations.append("Add stronger guardrails against unsupported or overstated claims.")
    if case.expected_not_found and not scores.not_found_detected:
        recommendations.append("Bias not-found cases toward refusal when the KB lacks supporting evidence.")
    if not recommendations and result.status != "PASS":
        recommendations.append("Review retrieved chunks and answer grounding for this case.")
    return recommendations


async def run_case(case: EvalTestCase, use_judge: bool) -> EvalCaseResult:
    payload = await _dispatch_case(case)
    answer = payload.get("answer", "")
    retrieved_sources = payload.get("retrieved_sources", [])
    retrieved_items = payload.get("retrieved_items", [])
    task_metadata = payload.get("task_metadata", {})
    collection_name = payload.get("collection_name")
    collection_names = list(task_metadata.get("sources_by_collection", {}).keys()) or None
    require_collection_citation = case.task_type == "question_answering_all"
    citation_expected_task_types = {
        "question_answering",
        "question_answering_all",
        "requirement_scoring",
        "proposal_drafting",
        "collection_compare",
    }
    citation_expected = case.task_type in citation_expected_task_types

    scores = build_deterministic_scores(
        answer=answer,
        retrieved_sources=retrieved_sources,
        expected_sources=case.expected_sources,
        expected_facts=case.expected_facts,
        required_sections=case.required_sections,
        unacceptable_claims=case.unacceptable_claims,
        expected_missing_evidence_flags=case.expected_missing_evidence_flags,
        expected_not_found=case.expected_not_found,
        collection_name=collection_name or case.collection,
        collection_names=collection_names,
        citation_expected=citation_expected,
        require_collection_citation=require_collection_citation,
    )
    overall_score = compute_overall_score(scores)
    status = assign_status(scores, overall_score, case.expected_not_found)

    warnings: list[str] = []
    failures: list[str] = []
    if task_metadata.get("error"):
        failures.append(task_metadata["error"])
        status = "FAIL"
        overall_score = 0.0
    if scores.missing_expected_sources:
        warnings.append(f"Missing expected sources: {', '.join(scores.missing_expected_sources)}")
    if scores.missing_expected_facts:
        warnings.append(f"Missing expected facts: {', '.join(scores.missing_expected_facts)}")
    if scores.missing_required_sections:
        warnings.append(f"Missing required sections: {', '.join(scores.missing_required_sections)}")
    if scores.unacceptable_claims_present:
        failures.append(f"Unacceptable claims present: {', '.join(scores.unacceptable_claims_present)}")
    if case.expected_not_found and not scores.not_found_detected:
        failures.append("Expected a not-found response but the answer did not clearly refuse.")

    judge_scores = None
    if use_judge:
        try:
            judge_scores = await judge_case(case, answer, retrieved_items)
        except Exception as exc:
            warnings.append(f"Judge evaluation failed: {exc}")

    result = EvalCaseResult(
        id=case.id,
        name=case.name,
        task_type=case.task_type,
        collection=case.collection,
        query=case.query,
        status=status,
        overall_score=overall_score,
        deterministic_scores=scores,
        judge_scores=judge_scores,
        retrieved_sources=retrieved_sources,
        retrieved_items=retrieved_items,
        answer=answer,
        warnings=warnings,
        failures=failures,
        recommendations=[],
        task_metadata=task_metadata,
    )
    result.recommendations = _build_recommendations(case, result)
    return result


def _average(values: list[float | None]) -> float:
    usable = [value for value in values if value is not None]
    if not usable:
        return 0.0
    return round(sum(usable) / len(usable), 4)


def _risk_label(case_results: list[EvalCaseResult]) -> str:
    risks = [case.judge_scores.hallucination_risk for case in case_results if case.judge_scores and case.judge_scores.hallucination_risk]
    if not risks:
        return "Unknown"
    counts = Counter(risks)
    return counts.most_common(1)[0][0]


def build_summary(
    case_results: list[EvalCaseResult],
    *,
    use_judge: bool,
    output_markdown: str,
    output_json: str,
) -> EvalRunSummary:
    passed = sum(1 for case in case_results if case.status == "PASS")
    warnings_count = sum(1 for case in case_results if case.status == "WARN")
    failed = sum(1 for case in case_results if case.status == "FAIL")

    scores_by_task_type_map: dict[str, list[float]] = defaultdict(list)
    for case in case_results:
        scores_by_task_type_map[case.task_type].append(case.overall_score)
    scores_by_task_type = {
        task_type: round(sum(values) / len(values), 4)
        for task_type, values in scores_by_task_type_map.items()
    }

    cross_recommendations: list[str] = []
    if any(case.deterministic_scores.missing_expected_sources for case in case_results):
        cross_recommendations.append("Review retrieval settings or top_k where expected source hit rate is low.")
    if any(case.deterministic_scores.missing_required_sections for case in case_results):
        cross_recommendations.append("Formalize task prompts around required output sections to improve format compliance.")
    if any(case.deterministic_scores.unacceptable_claims_present for case in case_results):
        cross_recommendations.append("Add stricter hallucination and unsupported-claim guardrails before expanding Slack-facing workflows.")

    return EvalRunSummary(
        run_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        judge_enabled=use_judge,
        total_cases=len(case_results),
        passed=passed,
        warnings=warnings_count,
        failed=failed,
        overall_score=_average([case.overall_score for case in case_results]),
        retrieval_quality=_average([case.deterministic_scores.expected_source_hit_rate for case in case_results]),
        expected_fact_coverage=_average([case.deterministic_scores.expected_fact_coverage for case in case_results]),
        format_compliance=_average(
            [
                case.deterministic_scores.required_section_compliance
                for case in case_results
            ] + [
                case.deterministic_scores.missing_evidence_flag_compliance
                for case in case_results
            ]
        ),
        citation_presence=_average([case.deterministic_scores.citation_presence for case in case_results]),
        hallucination_risk=_risk_label(case_results),
        scores_by_task_type=scores_by_task_type,
        case_results=case_results,
        recommendations=cross_recommendations,
        output_markdown=output_markdown,
        output_json=output_json,
    )


async def run_evaluations(config: EvalRunConfig) -> EvalRunSummary:
    cases = filter_cases(load_test_cases(config.cases_path), config)
    case_results = []
    for case in cases:
        case_results.append(await run_case(case, config.use_judge))
    return build_summary(
        case_results,
        use_judge=config.use_judge,
        output_markdown=config.output_markdown or "",
        output_json=config.output_json or "",
    )
