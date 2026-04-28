from dataclasses import dataclass, field


@dataclass
class EvalTestCase:
    id: str
    name: str
    task_type: str
    query: str
    collection: str | None = None
    collection_b: str | None = None
    expected_sources: list[str] = field(default_factory=list)
    expected_facts: list[str] = field(default_factory=list)
    required_sections: list[str] = field(default_factory=list)
    unacceptable_claims: list[str] = field(default_factory=list)
    expected_missing_evidence_flags: list[str] = field(default_factory=list)
    expected_not_found: bool = False
    notes: str = ""


@dataclass
class EvalRunConfig:
    cases_path: str
    case_id: str | None = None
    task_type: str | None = None
    collection: str | None = None
    output_markdown: str | None = None
    output_json: str | None = None
    use_judge: bool = False


@dataclass
class DeterministicScores:
    expected_source_hit_rate: float | None = None
    expected_fact_coverage: float | None = None
    required_section_compliance: float | None = None
    missing_evidence_flag_compliance: float | None = None
    citation_presence: float | None = None
    not_found_handling: float | None = None
    matched_expected_sources: list[str] = field(default_factory=list)
    missing_expected_sources: list[str] = field(default_factory=list)
    matched_expected_facts: list[str] = field(default_factory=list)
    missing_expected_facts: list[str] = field(default_factory=list)
    matched_required_sections: list[str] = field(default_factory=list)
    missing_required_sections: list[str] = field(default_factory=list)
    matched_missing_evidence_flags: list[str] = field(default_factory=list)
    missing_expected_missing_evidence_flags: list[str] = field(default_factory=list)
    unacceptable_claims_present: list[str] = field(default_factory=list)
    citation_present: bool = False
    not_found_detected: bool = False


@dataclass
class JudgeScores:
    groundedness_score: int | None = None
    completeness_score: int | None = None
    citation_accuracy_score: int | None = None
    hallucination_risk: str | None = None
    unsupported_claims: list[str] = field(default_factory=list)
    missing_elements: list[str] = field(default_factory=list)
    judge_notes: str = ""


@dataclass
class EvalCaseResult:
    id: str
    name: str
    task_type: str
    collection: str | None
    query: str
    status: str
    overall_score: float
    deterministic_scores: DeterministicScores
    judge_scores: JudgeScores | None
    retrieved_sources: list[str]
    retrieved_items: list[dict]
    answer: str
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    task_metadata: dict = field(default_factory=dict)


@dataclass
class EvalRunSummary:
    run_timestamp: str
    judge_enabled: bool
    total_cases: int
    passed: int
    warnings: int
    failed: int
    overall_score: float
    retrieval_quality: float
    expected_fact_coverage: float
    format_compliance: float
    citation_presence: float
    hallucination_risk: str
    scores_by_task_type: dict[str, float]
    case_results: list[EvalCaseResult]
    recommendations: list[str]
    output_markdown: str
    output_json: str
