"""Offline tests for check_not_found_handling. No network, no Qdrant, no spend.

Deliberately dependency-free (pytest is not in requirements.txt). Run:

    .\\.venv\\Scripts\\python.exe tests\\test_not_found_detection.py

Context: on the 2026-11-06 ConsultingKB benchmark run, 4 of 6 correct
abstentions scored 0.00 because the detector recognised only two refusal
templates ("cannot answer" and "no information"). Among the misses was an
answer that refused AND warned that the shift-staffing model present in the
retrieved context belonged to a different program -- the exact conflation that
case exists to catch. not_found_handling carries weight 0.10, and a miss is
read as a hallucination, so a brittle detector manufactures false failures.

The phrasings below are verbatim openings from that run. The adversarial cases
guard the other direction: this score is only computed when expected_not_found
is True, so an over-loose pattern would credit an answer that invented evidence
and merely mentioned an omission in passing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.metrics import check_not_found_handling  # noqa: E402

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        _failures.append(label)


def expect(label: str, answer: str, want: bool) -> None:
    detected, score = check_not_found_handling(answer)
    check(
        f"{label}: detected={want}",
        detected is want,
        f"got {detected} for {answer[:70]!r}",
    )
    check(f"{label}: score matches detection", score == (1.0 if want else 0.0))


# ---------------------------------------------------------------------------
# Refusals that the original literal list already caught. These must not break.
# ---------------------------------------------------------------------------

def test_original_templates_still_detected() -> None:
    print("test_original_templates_still_detected")
    expect(
        "cannot answer",
        "Based on the available context, I cannot answer this question. The documents "
        "for solicitation HQ-2024-SOC-0041 are internal pricing materials.",
        True,
    )
    expect("not found", "That contract is not found in the knowledge base.", True)
    expect("no relevant content", "There is no relevant content for this topic.", True)
    expect("not provided", "A rating is not provided in these documents.", True)
    expect("could not find", "I could not find a conformance report.", True)


# ---------------------------------------------------------------------------
# The four real misses from the 2026-11-06 run, verbatim.
# ---------------------------------------------------------------------------

def test_phrasings_missed_in_production() -> None:
    print("test_phrasings_missed_in_production")
    expect(
        "don't have information (n03)",
        "I don't have information about the 24/7 Tier 1 through Tier 3 analyst shift "
        "staffing model for the DHS Cyber Transformation program. According to "
        "2023_DHS_CyberTransformation.md, the program trained 142 DHS security analysts "
        "on new tooling, but the document does not include details about shift "
        "structures, tier designations, or the specific staffing model used.",
        True,
    )
    expect(
        "don't have information (n06)",
        "I don't have information about a Section 508 accessibility conformance report, "
        "VPAT, accessibility testing tooling, or remediation plan for a dashboard "
        "deliverable in the provided context.",
        True,
    )
    expect(
        "cannot provide the evidence (n07)",
        "Based on the context provided, I cannot provide the evidence you're requesting. "
        "The documents contain only limited references to FedRAMP.",
        True,
    )
    expect(
        "cannot FULLY answer (n08)",
        "Based on the context provided, I cannot fully answer your question. The "
        "documents reference past engagements but do not include the complete "
        "information you're requesting.",
        True,
    )
    # "cannot fully answer" is why a substring list is not enough: the adverb
    # breaks "cannot answer" even though the sentence means the same thing.
    check(
        "'cannot answer' is genuinely absent from the n08 phrasing",
        "cannot answer" not in "i cannot fully answer your question",
    )


def test_other_refusal_shapes() -> None:
    print("test_other_refusal_shapes")
    expect("unable to locate", "I am unable to locate a named past performance reference.", True)
    expect("no evidence", "The technical volume contains no evidence of FedRAMP High operations.", True)
    expect("does not contain any records", "This collection does not contain any records of that award.", True)
    expect("no record of", "There is no record of a protest on this solicitation.", True)
    expect("cannot confirm", "I cannot confirm an award outcome from these documents.", True)
    expect("lacks documentation", "The folder lacks documentation for that deliverable.", True)


# ---------------------------------------------------------------------------
# The other direction. A fabricated answer must not be credited as a refusal
# just because it mentions an omission somewhere.
# ---------------------------------------------------------------------------

def test_fabrications_are_not_credited() -> None:
    print("test_fabrications_are_not_credited")
    expect(
        "invented award",
        "We won HQ-2024-SOC-0041 on 12 March 2024 at a value of $4.9M.",
        False,
    )
    expect(
        "invented rating with an aside",
        "Our CPARS rating was Exceptional, though the file does not include the signed copy.",
        False,
    )
    expect(
        "confident factual answer",
        "The ceiling is $85,000,000, the maximum order limitation is $15,000,000, and "
        "the contracting officer is Patricia Huang.",
        False,
    )
    expect(
        "conflated staffing claim",
        "The DHS program ran 24/7 Tier 1 through Tier 3 coverage across three shifts.",
        False,
    )
    expect(
        "bare omission of a non-information noun",
        "The transition plan does not include weekend cutover windows, so the schedule "
        "assumes business days only.",
        False,
    )
    expect("empty answer", "", False)


if __name__ == "__main__":
    for test in (
        test_original_templates_still_detected,
        test_phrasings_missed_in_production,
        test_other_refusal_shapes,
        test_fabrications_are_not_credited,
    ):
        test()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("all checks passed")
