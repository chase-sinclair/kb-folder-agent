import json
from pathlib import Path


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _case_map(run_data: dict) -> dict[str, dict]:
    return {case["id"]: case for case in run_data.get("case_results", [])}


def _delta(old: float | None, new: float | None) -> float | None:
    if old is None or new is None:
        return None
    return round(new - old, 4)


def _judge_delta(case_old: dict, case_new: dict, field_name: str) -> float | None:
    old_scores = case_old.get("judge_scores")
    new_scores = case_new.get("judge_scores")
    if not old_scores or not new_scores:
        return None
    old_value = old_scores.get(field_name)
    new_value = new_scores.get(field_name)
    if old_value is None or new_value is None:
        return None
    return round((new_value - old_value) / 10.0, 4)


def _status_rank(status: str) -> int:
    return {"FAIL": 0, "WARN": 1, "PASS": 2}.get(status, -1)


def _transition_label(old_status: str, new_status: str) -> str:
    if _status_rank(new_status) > _status_rank(old_status):
        return "improved"
    if _status_rank(new_status) < _status_rank(old_status):
        return "regressed"
    return "unchanged"


def _hallucination_rank(value: str | None) -> int | None:
    mapping = {"Low": 3, "Medium": 2, "High": 1}
    if value is None:
        return None
    return mapping.get(value)


def _hallucination_delta(old_value: str | None, new_value: str | None) -> str | None:
    old_rank = _hallucination_rank(old_value)
    new_rank = _hallucination_rank(new_value)
    if old_rank is None or new_rank is None:
        return None
    if new_rank > old_rank:
        return "improved"
    if new_rank < old_rank:
        return "regressed"
    return "unchanged"


def compare_run_files(
    baseline_json: str,
    candidate_json: str,
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
) -> dict:
    baseline = _load_json(baseline_json)
    candidate = _load_json(candidate_json)

    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    shared_case_ids = sorted(set(baseline_cases) & set(candidate_cases))

    case_comparisons = []
    for case_id in shared_case_ids:
        old_case = baseline_cases[case_id]
        new_case = candidate_cases[case_id]
        transition = _transition_label(old_case["status"], new_case["status"])
        hallucination_change = _hallucination_delta(
            (old_case.get("judge_scores") or {}).get("hallucination_risk"),
            (new_case.get("judge_scores") or {}).get("hallucination_risk"),
        )
        case_comparisons.append(
            {
                "id": case_id,
                "name": new_case["name"],
                "task_type": new_case["task_type"],
                "baseline_status": old_case["status"],
                "candidate_status": new_case["status"],
                "status_transition": transition,
                "baseline_overall_score": old_case["overall_score"],
                "candidate_overall_score": new_case["overall_score"],
                "overall_score_delta": _delta(old_case["overall_score"], new_case["overall_score"]),
                "groundedness_delta": _judge_delta(old_case, new_case, "groundedness_score"),
                "completeness_delta": _judge_delta(old_case, new_case, "completeness_score"),
                "citation_accuracy_delta": _judge_delta(old_case, new_case, "citation_accuracy_score"),
                "hallucination_change": hallucination_change,
            }
        )

    improved = [case for case in case_comparisons if case["status_transition"] == "improved"]
    regressed = [case for case in case_comparisons if case["status_transition"] == "regressed"]
    unchanged = [case for case in case_comparisons if case["status_transition"] == "unchanged"]

    metric_deltas = {
        "overall_score_delta": _delta(baseline.get("overall_score"), candidate.get("overall_score")),
        "retrieval_quality_delta": _delta(baseline.get("retrieval_quality"), candidate.get("retrieval_quality")),
        "expected_fact_coverage_delta": _delta(baseline.get("expected_fact_coverage"), candidate.get("expected_fact_coverage")),
        "format_compliance_delta": _delta(baseline.get("format_compliance"), candidate.get("format_compliance")),
        "citation_presence_delta": _delta(baseline.get("citation_presence"), candidate.get("citation_presence")),
    }

    sorted_swings = sorted(
        case_comparisons,
        key=lambda case: abs(case["overall_score_delta"] or 0.0),
        reverse=True,
    )

    return {
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "baseline_path": baseline_json,
        "candidate_path": candidate_json,
        "baseline_timestamp": baseline.get("run_timestamp"),
        "candidate_timestamp": candidate.get("run_timestamp"),
        "case_count": len(shared_case_ids),
        "improved_count": len(improved),
        "regressed_count": len(regressed),
        "unchanged_count": len(unchanged),
        "metric_deltas": metric_deltas,
        "improved_cases": improved,
        "regressed_cases": regressed,
        "unchanged_cases": unchanged,
        "largest_swings": sorted_swings[:5],
        "baseline_scores_by_task_type": baseline.get("scores_by_task_type", {}),
        "candidate_scores_by_task_type": candidate.get("scores_by_task_type", {}),
    }


def build_comparison_report(comparison: dict) -> str:
    def fmt_delta(value: float | None) -> str:
        if value is None:
            return "N/A"
        return f"{value:+.0%}"

    lines = [
        "# KB Agent Evaluation Comparison",
        "",
        f"Baseline: {comparison['baseline_label']} ({comparison['baseline_timestamp']})",
        f"Candidate: {comparison['candidate_label']} ({comparison['candidate_timestamp']})",
        f"Cases Compared: {comparison['case_count']}",
        "",
        "## Overall Deltas",
        f"- Overall Score: {fmt_delta(comparison['metric_deltas']['overall_score_delta'])}",
        f"- Retrieval Quality: {fmt_delta(comparison['metric_deltas']['retrieval_quality_delta'])}",
        f"- Expected Fact Coverage: {fmt_delta(comparison['metric_deltas']['expected_fact_coverage_delta'])}",
        f"- Format Compliance: {fmt_delta(comparison['metric_deltas']['format_compliance_delta'])}",
        f"- Citation Presence: {fmt_delta(comparison['metric_deltas']['citation_presence_delta'])}",
        "",
        "## Status Summary",
        f"- Improved: {comparison['improved_count']}",
        f"- Regressed: {comparison['regressed_count']}",
        f"- Unchanged: {comparison['unchanged_count']}",
        "",
        "## Largest Score Swings",
    ]

    if comparison["largest_swings"]:
        for case in comparison["largest_swings"]:
            lines.append(
                f"- {case['id']}: {case['baseline_status']} -> {case['candidate_status']} ({fmt_delta(case['overall_score_delta'])})"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Improved Cases"])
    if comparison["improved_cases"]:
        for case in comparison["improved_cases"]:
            lines.append(
                f"- {case['id']}: {case['baseline_status']} -> {case['candidate_status']} ({fmt_delta(case['overall_score_delta'])})"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Regressed Cases"])
    if comparison["regressed_cases"]:
        for case in comparison["regressed_cases"]:
            lines.append(
                f"- {case['id']}: {case['baseline_status']} -> {case['candidate_status']} ({fmt_delta(case['overall_score_delta'])})"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## All Case Transitions"])
    for case in comparison["improved_cases"] + comparison["regressed_cases"] + comparison["unchanged_cases"]:
        judge_bits = []
        if case["groundedness_delta"] is not None:
            judge_bits.append(f"groundedness {fmt_delta(case['groundedness_delta'])}")
        if case["completeness_delta"] is not None:
            judge_bits.append(f"completeness {fmt_delta(case['completeness_delta'])}")
        if case["citation_accuracy_delta"] is not None:
            judge_bits.append(f"citation {fmt_delta(case['citation_accuracy_delta'])}")
        if case["hallucination_change"] and case["hallucination_change"] != "unchanged":
            judge_bits.append(f"hallucination {case['hallucination_change']}")
        suffix = f" | {'; '.join(judge_bits)}" if judge_bits else ""
        lines.append(
            f"- {case['id']}: {case['baseline_status']} -> {case['candidate_status']} ({fmt_delta(case['overall_score_delta'])}){suffix}"
        )

    return "\n".join(lines).strip() + "\n"


def write_comparison_report(comparison: dict, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_comparison_report(comparison), encoding="utf-8")
