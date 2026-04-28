import argparse
import asyncio
from datetime import datetime, timezone

from evals.config import DEFAULT_CASES_PATH, REPORTS_DIR, RESULTS_DIR
from evals.report import write_json_report, write_markdown_report
from evals.runner import run_evaluations
from evals.schema import EvalRunConfig


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run KB Agent Evaluation Center cases.")
    parser.add_argument("--case", dest="case_id", help="Run a single case by id.")
    parser.add_argument("--task-type", help="Run only cases of a given task type.")
    parser.add_argument("--collection", help="Run only cases for a given collection label.")
    parser.add_argument("--cases-path", default=str(DEFAULT_CASES_PATH), help="Path to the eval YAML file.")
    parser.add_argument("--output", dest="output_markdown", help="Markdown report output path.")
    parser.add_argument("--results-json", dest="output_json", help="JSON report output path.")
    parser.add_argument("--use-judge", action="store_true", help="Enable LLM-as-judge scoring.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    slug = _timestamp_slug()
    output_markdown = args.output_markdown or str(REPORTS_DIR / f"eval_report_{slug}.md")
    output_json = args.output_json or str(RESULTS_DIR / f"eval_results_{slug}.json")

    config = EvalRunConfig(
        cases_path=args.cases_path,
        case_id=args.case_id,
        task_type=args.task_type,
        collection=args.collection,
        output_markdown=output_markdown,
        output_json=output_json,
        use_judge=args.use_judge,
    )
    summary = await run_evaluations(config)
    summary.output_markdown = output_markdown
    summary.output_json = output_json

    write_markdown_report(summary, output_markdown)
    write_json_report(summary, output_json)

    print(f"Completed {summary.total_cases} case(s)")
    print(f"PASS: {summary.passed}  WARN: {summary.warnings}  FAIL: {summary.failed}")
    print(f"Overall score: {summary.overall_score:.0%}")
    print(f"Markdown report: {output_markdown}")
    print(f"JSON results: {output_json}")


if __name__ == "__main__":
    asyncio.run(main())
