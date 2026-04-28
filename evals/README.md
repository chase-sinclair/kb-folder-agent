# Evaluation Center

The Evaluation Center is a local-first harness for checking whether `kb-folder-agent`
retrieves the right evidence, cites correctly, follows expected output structure, and
avoids unsupported claims across both normal KB Q&A and proposal-specific workflows.

## What It Evaluates

- `question_answering`
- `question_answering_all`
- `requirement_scoring`
- `proposal_drafting`
- `gap_analysis`
- `collection_compare`
- `changes_summary`
- `not_found`

## Usage

```bash
python -m evals.run_evals
python -m evals.run_evals --case pp_cloud_001
python -m evals.run_evals --task-type requirement_scoring
python -m evals.run_evals --collection PastPerformance
python -m evals.run_evals --use-judge
python -m evals.run_evals --output evals/reports/latest_eval_report.md
python -m evals.compare_runs evals/results/baseline.json evals/results/candidate.json
```

## Outputs

Each run writes:

- a timestamped Markdown report under `evals/reports/`
- a timestamped JSON result file under `evals/results/`

Comparison runs write:

- a timestamped Markdown comparison report under `evals/reports/`

## Test Case Schema

`evals/test_cases.yaml` stores cases under a top-level `test_cases` key.

Supported fields:

- `id`
- `name`
- `task_type`
- `collection`
- `collection_b`
- `query`
- `expected_sources`
- `expected_facts`
- `required_sections`
- `unacceptable_claims`
- `expected_missing_evidence_flags`
- `expected_not_found`
- `notes`

## Notes

- The evaluator calls `agent.rag` functions directly. It does not drive Slack.
- Judge mode is optional and disabled by default.
- `evals/test_cases.yaml` now contains real benchmark cases based on the current `ConsultingKB` dataset.
- `python -m evals.compare_runs` compares two saved JSON runs and highlights improved, regressed, and unchanged cases.
