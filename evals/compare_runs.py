import argparse
from datetime import datetime, timezone

from evals.config import REPORTS_DIR
from evals.compare import compare_run_files, write_comparison_report


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two KB Agent evaluation runs.")
    parser.add_argument("baseline_json", help="Path to the baseline eval JSON results file.")
    parser.add_argument("candidate_json", help="Path to the candidate eval JSON results file.")
    parser.add_argument("--baseline-label", default="baseline", help="Display label for the baseline run.")
    parser.add_argument("--candidate-label", default="candidate", help="Display label for the candidate run.")
    parser.add_argument("--output", dest="output_markdown", help="Markdown comparison report output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_markdown = args.output_markdown or str(REPORTS_DIR / f"eval_compare_{_timestamp_slug()}.md")
    comparison = compare_run_files(
        args.baseline_json,
        args.candidate_json,
        baseline_label=args.baseline_label,
        candidate_label=args.candidate_label,
    )
    write_comparison_report(comparison, output_markdown)

    print(f"Compared {comparison['baseline_label']} -> {comparison['candidate_label']}")
    print(f"Cases compared: {comparison['case_count']}")
    print(f"Improved: {comparison['improved_count']}  Regressed: {comparison['regressed_count']}  Unchanged: {comparison['unchanged_count']}")
    print(f"Comparison report: {output_markdown}")


if __name__ == "__main__":
    main()
