from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
REPORTS_DIR = Path(__file__).parent / "reports"
DEFAULT_CASES_PATH = Path(__file__).parent / "test_cases.yaml"

PASS_THRESHOLD = 0.80
WARN_THRESHOLD = 0.60

OVERALL_SCORE_WEIGHTS = {
    "expected_source_hit_rate": 0.30,
    "expected_fact_coverage": 0.20,
    "required_section_compliance": 0.20,
    "missing_evidence_flag_compliance": 0.10,
    "citation_presence": 0.10,
    "not_found_handling": 0.10,
}

JUDGE_MODEL = "claude-opus-4-5"
JUDGE_MAX_TOKENS = 900
