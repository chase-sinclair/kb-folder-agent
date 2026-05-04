# CODEX Handoff Notes

## Purpose
This file tracks implementation details and decisions that matter for developer handoff while Codex is working in the repo. It is intentionally brief and focused on code-level context, not product brainstorming.

## Active Workstream
Evaluation Center implementation for `kb-folder-agent`.

## Current Phase
Phase 4: Slack integration for Evaluation Center workflows.

## Goals
- Add a local-first `evals/` package.
- Reuse existing `agent.rag` workflows instead of duplicating Slack behavior.
- Expose enough retrieval metadata for evaluation without breaking current Slack callers.
- Produce Markdown and JSON evaluation reports.
- Keep judge-based scoring optional behind `--use-judge`.

## Decisions So Far
- Keep the Evaluation Center below Slack and above `agent.rag`.
- Use retrieval-aware helper wrappers in `agent.rag` for eval flows.
- Preserve existing Slack-facing function signatures and behavior.
- Use YAML for starter test cases because `PyYAML` is already available in the environment.
- Fold in two small retrieval correctness fixes during implementation:
  - `search_all_collections()` should pass `query_text` so hybrid retrieval is used consistently.
  - `folder_to_collection_name()` should match the documented normalization rule.

## Repo-Specific Notes
- `README.md` is UTF-16 encoded in this repo. Avoid casual patching unless there is a good reason to rewrite encoding.
- `AGENTS.md` is currently untracked in git and should be left alone.

## Phase Status
### Phase 1
- Completed.
- Added the `evals/` package:
  - `config.py`
  - `schema.py`
  - `metrics.py`
  - `judge.py`
  - `runner.py`
  - `report.py`
  - `run_evals.py`
  - `README.md`
  - `test_cases.yaml`
  - `results/.gitkeep`
  - `reports/.gitkeep`
- Added retrieval-aware evaluation helpers in `agent/rag.py`.
- Extended `RagResult` with optional retrieval metadata for eval use.
- Fixed `agent/orchestrator.search_all_collections()` so multi-collection evals use hybrid retrieval too.
- Fixed `agent/orchestrator.folder_to_collection_name()` to match documented collection normalization.
- Verified syntax with a compile pass over `agent/` and `evals/`.
- Verified the CLI runs end-to-end with starter sample data and produces Markdown/JSON reports. The sample case fails cleanly when `SampleCollection` is absent, which is expected behavior for the synthetic starter dataset.

## Validation Notes
- `python -m evals.run_evals --case qa_example_001` now completes and writes reports under:
  - `evals/reports/`
  - `evals/results/`
- The starter YAML is intentionally synthetic. Developers should replace `SampleCollection` and example filenames with real KB data before using the harness as a meaningful benchmark.

## Real Benchmark Dataset Update
- Replaced the synthetic starter cases in `evals/test_cases.yaml` with 8 real benchmark cases based on `C:\Users\chase\Documents\ConsultingKB`.
- Collections covered:
  - `PastPerformance`
  - `TechnicalVolume`
  - `Resumes`
  - `Contracts`
- Key benchmark themes:
  - DISA cloud migration evidence
  - Cross-collection analytics + staffing synthesis
  - SOC modernization requirement scoring
  - Proposal drafting with explicit missing-evidence checks
  - Gap analysis against data modernization pursuit needs
  - Collection comparison between historical proof and technical approach
  - Resume grounding
  - Not-found / hallucination resistance on award outcome questions

## Real Benchmark Validation
- Ran one focused live case successfully:
  - `python -m evals.run_evals --case pp_cloud_001`
  - Result: PASS
- Ran the full live benchmark suite successfully:
  - `python -m evals.run_evals`
  - Result summary: 8 total, 6 PASS, 1 WARN, 1 FAIL
  - Overall score: 85%
- Most important findings from the first real run:
  - Retrieval quality is strong: 100%
  - Fact coverage is strong: 100%
  - Format compliance is strong: 100%
  - The main weakness is not-found handling / hallucination resistance on award questions
  - Gap analysis is somewhat weaker than the other structured tasks and deserves closer review

## Benchmark Tuning Follow-Up
- Tuned deterministic evaluation logic after reviewing the first real run:
  - `not_found` detection now recognizes refusal phrasing such as `cannot answer` and `there is no information`
  - unacceptable-claim checks now ignore clearly negated/refusal contexts instead of flagging every raw token match
  - citation scoring now only applies to task types where citations are actually expected
- Re-ran targeted weak cases:
  - `not_found_award_001` -> PASS
  - `gaps_data_mod_001` -> PASS
- Re-ran the full live benchmark suite:
  - `python -m evals.run_evals`
  - Result summary: 8 total, 8 PASS, 0 WARN, 0 FAIL
  - Overall score: 99%

## Current Benchmark Artifacts
- First real full-run report with issues exposed:
  - `evals/reports/eval_report_20260428_224304.md`
- Latest clean full-run report:
  - `evals/reports/eval_report_20260428_224744.md`

## Phase 2
- Completed.
- Hardened `evals/judge.py`:
  - stricter JSON-only judge prompt
  - explicit score/risk normalization
  - more tolerant JSON extraction using `json.JSONDecoder().raw_decode()` so trailing text no longer breaks parsing
- Improved `evals/runner.py`:
  - judge findings now add actionable warnings
  - judge failures and unsupported-claim findings can promote a deterministic PASS to WARN
  - run-level recommendations now include judge-derived follow-up guidance
- Improved `evals/report.py`:
  - judge average scores appear in the Overview section
  - warning/failure sections include judge notes
  - all-case results show judge hallucination risk and groundedness summaries
- Validated live judge mode with:
  - `python -m evals.run_evals --use-judge`

## Judge-Mode Validation
- First judge-enabled full run:
  - `evals/reports/eval_report_20260428_230010.md`
  - confirmed end-to-end success and surfaced hidden semantic issues
- Final judge-enabled full run after Phase 2 refinements:
  - `evals/reports/eval_report_20260428_230416.md`
  - result summary: 8 total, 5 PASS, 3 WARN, 0 FAIL
  - overall deterministic score remained 99%
  - average judge scores:
    - groundedness: 9.4/10
    - completeness: 9.0/10
    - citation accuracy: 9.4/10
  - aggregate hallucination risk: Low

## Meaningful Judge Findings
- `qa_all_analytics_001`
  - citation drift: an extra whitepaper citation was attached to VA-specific evidence
- `gaps_data_mod_001`
  - one unsupported IL-level inference not present in retrieved context
- `compare_soc_001`
  - slight mischaracterization of the VA cATO timing and a missed opportunity to compare evidence strength more explicitly

These are the first genuinely semantic issues surfaced by the Evaluation Center beyond deterministic structure checks.

## Phase 3
- Completed.
- Added comparison tooling:
  - `evals/compare.py`
  - `evals/compare_runs.py`
- Comparison features:
  - compares two saved JSON evaluation runs
  - computes deltas for overall score, retrieval quality, expected fact coverage, format compliance, and citation presence
  - tracks per-case status transitions (`PASS/WARN/FAIL`)
  - highlights improved, regressed, and unchanged cases
  - shows largest score swings
  - includes judge-score deltas when both runs contain judge output
- Updated `evals/README.md` with comparison usage and current benchmark-pack notes.

## Phase 3 Validation
- Validated comparison mode against real run artifacts:
  - baseline: `evals/results/eval_results_20260428_224744.json`
  - candidate: `evals/results/eval_results_20260428_230416.json`
- Ran:
  - `python -m evals.compare_runs evals/results/eval_results_20260428_224744.json evals/results/eval_results_20260428_230416.json --baseline-label deterministic --candidate-label judge`
- Generated:
  - `evals/reports/eval_compare_20260428_231228.md`
- Result:
  - 8 cases compared
  - 0 improved
  - 3 regressed
  - 5 unchanged

## Why Phase 3 Matters
- The comparison report confirms that judge mode did not change deterministic benchmark totals materially, but it did surface three semantic regressions/warnings that deterministic metrics alone would have hidden.
- This is now the main tool for evaluating future prompt, retrieval, and model changes safely.

## Phase 4
- Completed.
- Added Slack-facing Evaluation Center commands in `slack/bot.py`:
  - `/kb eval [all|case <id>|task-type <type>|collection <FolderName>] [judge]`
  - `/kb eval-report`
- Integration design:
  - `/kb eval` acks immediately, posts an ephemeral "starting" message, then runs the selected eval scope in a background `asyncio` task.
  - Background completion writes the normal Markdown + JSON artifacts first, then posts an ephemeral summary back into Slack.
  - `/kb eval-report` reads the latest saved JSON artifact from `evals/results/` and formats a compact Slack summary with PASS/WARN/FAIL counts, core metrics, notable warning/failure cases, and top recommendations.
- Kept the implementation isolated from existing KB command behavior:
  - no changes to ingestion
  - no changes to watcher flow
  - no changes to eval scoring logic
  - Slack is only an orchestration layer over `evals.runner.run_evaluations()`
- Updated `evals/README.md` with Slack command usage and behavior notes.

## Phase 4 Validation
- Verified syntax/compile health with:
  - `python -m py_compile slack/bot.py evals/runner.py evals/report.py`
- Slack-path validation is code-level only in this phase:
  - command parsing
  - background execution path
  - latest-report artifact lookup
  - summary block formatting
- No live Slack posting test was run from Codex during this pass.

## Known Follow-Ups
- `README.md` in the repo root was not updated because it is UTF-16 encoded and rewriting it during this phase would create avoidable encoding churn. `evals/README.md` contains the Evaluation Center usage details instead.
- `summarize_recent_changes(days=3)` still does not use time-bounded version evidence. The eval harness measures current behavior but does not change that implementation.
- OneDrive deletion handling remains unchanged.
- Slack summaries intentionally report local artifact filenames only; they do not attempt to expose local filesystem paths inside Slack.
