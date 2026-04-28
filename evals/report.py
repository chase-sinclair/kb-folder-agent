import json
from dataclasses import asdict
from pathlib import Path

from evals.schema import EvalRunSummary


def write_json_report(summary: EvalRunSummary, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")


def build_markdown_report(summary: EvalRunSummary) -> str:
    lines = [
        "# KB Agent Evaluation Report",
        "",
        f"Run Date: {summary.run_timestamp}",
        f"Judge Enabled: {'true' if summary.judge_enabled else 'false'}",
        f"Total Test Cases: {summary.total_cases}",
        f"Passed: {summary.passed}",
        f"Warnings: {summary.warnings}",
        f"Failed: {summary.failed}",
        "",
        "## Overall Scores",
        f"- Overall Score: {summary.overall_score:.0%}",
        f"- Retrieval Quality: {summary.retrieval_quality:.0%}",
        f"- Expected Fact Coverage: {summary.expected_fact_coverage:.0%}",
        f"- Format Compliance: {summary.format_compliance:.0%}",
        f"- Citation Presence: {summary.citation_presence:.0%}",
        f"- Hallucination Risk: {summary.hallucination_risk}",
        "",
        "## Scores by Task Type",
    ]

    for task_type, score in sorted(summary.scores_by_task_type.items()):
        lines.append(f"- {task_type}: {score:.0%}")

    failed_cases = [case for case in summary.case_results if case.status == "FAIL"]
    warning_cases = [case for case in summary.case_results if case.status == "WARN"]

    lines.extend(["", "## Failed Cases"])
    if failed_cases:
        for case in failed_cases:
            lines.extend(
                [
                    f"### {case.id} - {case.name}",
                    f"- Status: {case.status}",
                    f"- Overall Score: {case.overall_score:.0%}",
                    f"- Failure Reasons: {', '.join(case.failures) if case.failures else 'None recorded'}",
                    f"- Retrieved Sources: {', '.join(case.retrieved_sources) if case.retrieved_sources else 'None'}",
                    f"- Recommendation: {case.recommendations[0] if case.recommendations else 'Review retrieved context and prompts.'}",
                    "",
                ]
            )
    else:
        lines.append("None.")

    lines.extend(["", "## Warning Cases"])
    if warning_cases:
        for case in warning_cases:
            lines.extend(
                [
                    f"### {case.id} - {case.name}",
                    f"- Status: {case.status}",
                    f"- Overall Score: {case.overall_score:.0%}",
                    f"- Warning Reasons: {', '.join(case.warnings) if case.warnings else 'None recorded'}",
                    f"- Retrieved Sources: {', '.join(case.retrieved_sources) if case.retrieved_sources else 'None'}",
                    f"- Recommendation: {case.recommendations[0] if case.recommendations else 'Tighten retrieval or output formatting.'}",
                    "",
                ]
            )
    else:
        lines.append("None.")

    lines.extend(["", "## All Case Results"])
    for case in summary.case_results:
        lines.append(f"- {case.id}: {case.status} ({case.overall_score:.0%})")

    lines.extend(["", "## Recommendations"])
    if summary.recommendations:
        for recommendation in summary.recommendations:
            lines.append(f"- {recommendation}")
    else:
        lines.append("- No cross-run recommendations generated.")

    return "\n".join(lines).strip() + "\n"


def write_markdown_report(summary: EvalRunSummary, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown_report(summary), encoding="utf-8")
