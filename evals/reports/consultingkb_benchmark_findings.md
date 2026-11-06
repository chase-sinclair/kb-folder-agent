# ConsultingKB benchmark: findings

**Date:** 2026-11-06
**Corpus:** 40 documents / 274 chunks, `ConsultingKB_Final_Corrected.zip`
(SHA-256 `c42117177e…92b6b9`), deployed to `C:\Users\chase\OneDrive\ConsultingKB`
**Pack:** `evals/test_cases_benchmark.yaml` — 14 positives (P1-P14), 10 protected
negatives (N1-N10), all anchors verified against corpus text
**Runs:** 2 × rerank-off, 1 × rerank-on, judge off, deterministic metrics only

---

## 1. Headline

| | PASS | WARN | FAIL | Overall | MRR | Top-1 |
|---|---:|---:|---:|---:|---:|---:|
| rerank OFF (run 1, rescored) | 18 | 5 | 1 | 91.4% | 86.9% | 77.8% |
| rerank OFF (run 2) | 18 | 5 | 1 | 90.6% | 86.9% | 77.8% |
| **rerank ON** | **22** | **2** | **0** | **95.5%** | **89.7%** | **83.3%** |

Run 1's JSON stores `overall_score = 0.748` because it was scored with the
abstention detector described in §2.1; 91.4% is the same answers rescored with
the fix, which is the figure comparable to run 2 and to the on arm.

**Run-to-run noise floor: ±0.8 points on the aggregate, 4 of 24 cases moving
between two identical off-arm runs.** The +4.5 point rerank delta sits well
outside that, and the four cases driving it were stable across both off runs, so
the gain is attributable rather than sampled. Two cases that *appear* to move
under rerank (p04 down, p11 up) are inside the noise band and are **not** claimed
as reranker effects.

Measuring the noise floor first changed the conclusion. Compared naively against
a single off run, the reranker looks like it moved 6 cases; only 4 survive.

---

## 2. Two measurement defects found before any conclusion was drawn

### 2.1 The abstention detector recognised two templates (fixed)

`check_not_found_handling()` matched a fixed list of literal substrings.
On the first run, **4 of 6 correct abstentions scored 0.00** and were therefore
counted as hallucinations, because the model refuses in words the list did not
contain:

| Case | Answer opening | Old detector |
|---|---|---|
| N1, N2 | "I **cannot answer** this question" | fires |
| N3, N6 | "I **don't have information** about…" | misses |
| N7 | "I **cannot provide** the evidence…" | misses |
| N8 | "I **cannot fully answer**…" | misses — the adverb breaks the substring |

The N3 answer scored 0.00 is the best available response to that case: it
refuses, cites what the DHS file *does* say (142 analysts trained), then warns
explicitly that the shift-staffing model present in the retrieved context
belongs to the **DOE** program — the exact conflation N3 exists to detect.

`not_found_handling` carries weight 0.10 and a miss reads as a hallucination, so
the defect cost 16.6 points: **rescoring the identical answers moved the run from
74.8% to 91.4%.** Fixed with two regex families (inability, absence-of-evidence)
layered over the original literals, which are kept verbatim so historical scores
cannot shift for any other reason. `tests/test_not_found_detection.py` pins six
real phrasings plus six adversarial controls — including a fabricated award that
mentions an omission in passing, which must **not** be credited as a refusal.

### 2.2 `expected_source_hit_rate` cannot see the system's main failure

It is a **filename set** metric: a document counts as retrieved if any one of its
chunks comes back. Three cases scored a perfect 1.00 on it while the answer was
flatly wrong:

| Case | Hit rate | The answer asserted | The corpus holds |
|---|---:|---|---|
| P6 | 1.00 | "None of the documents state the overall contract ceiling" | `$85,000,000` in the retrieved file |
| P8 | 1.00 | "no actual dollar amounts are stated" | `$4,689,300` in the retrieved file |
| P14 | 1.00 | "No version number or date is specified" | `**Version:** 2.1`, chunk 1 of that file |

**Right file, wrong chunk.** The answer-bearing chunk ranked outside top-5.
`expected_fact_coverage` is the only metric that catches this, and it is the one
that dropped to 0.00.

The diagnostic signature to watch in any future run is
`hit_rate = 1.00` together with `fact_coverage = 0.00`.

This generalises the handoff's P10 caution from a single-case quirk into the
pipeline's dominant weakness. P14 also implicates the **19 short baseline
chunks**: the 19-word header chunk carrying `Version: 2.1` loses on embedding
similarity to body prose, so a question about the version cannot reach it.

---

## 3. What reranking actually fixed

Four cases were stable across both off-arm runs and moved under rerank. All four
are the right-file-wrong-chunk failure:

| Case | OFF (×2 stable) | ON | `expected_fact_coverage` | What changed |
|---|---:|---:|---|---|
| P13 throughput | 0.58 **FAIL** | 1.00 PASS | 0.50 → 1.00 | `TransitionPlan_Phase_In_Standard.md` was never retrieved at top-5; the 15-candidate pool reached it |
| P6 FCAIS terms | 0.67 WARN | 1.00 PASS | **0.00 → 1.00** | the ceiling / CO / rate-table chunk surfaced |
| P8 bid prices | 0.67 WARN | 0.83 PASS | **0.00 → 0.50** | one of the two pricing bodies surfaced |
| P10 SOC price + tech | 0.67 WARN | 0.83 PASS | **0.00 → 0.50** | the pricing **body** chunk surfaced, not just the header |

P10 is the case the handoff flagged as unresolved. Verified chunk map:
`PricingStrategy_Notes.md` chunk 1 is a 27-word header with no dollar figures,
and the win-price target lives in chunk 2. Off-arm, fan-out's three CostVolume
slots returned the cost-realism decoy, the SOC BOE and the header — perfect
`hit_rate`, zero usable price evidence. On-arm, the body chunk entered.

**The mechanism is the candidate pool as much as the model.** With rerank on,
`search()` fetches `min(top_k × 3, 40)` = 15 candidates and reorders them;
off, it fetches exactly 5. P13 was a pure *recall* miss — no reordering of 5
chunks could have fixed it.

Note P10's MRR stayed at 0.14 while its fact coverage doubled. That is expected,
not a contradiction: `answer_query_all_eval` builds `flat_sources`
collection-major, so fan-out MRR reflects collection iteration order folded over
within-collection rank. **Fan-out MRR is directional only**; the
single-collection cases carry the honest ranking signal.

---

## 4. Protected gaps and abstention

All 10 negatives pass in both arms once the detector is fixed. Specifically
verified:

- **N1** no award outcome invented for `HQ-2024-SOC-0041`, despite four files
  mentioning the solicitation and none stating an outcome.
- **N3** DOE shift staffing present in context was explicitly **excluded** from a
  DHS answer rather than imported.
- **N6/N7/N8** the three protected TechnicalVolume gaps hold. N7's scope
  distinction is respected: the corpus discusses a FedRAMP High *authorization*
  milestone, and the model refuses the *operations* evidence without denying the
  authorization.
- **N10** the baseline DoD PM contradiction (Brian Torres in the past-performance
  file vs Sarah Chen in her resume) is surfaced as a conflict, not resolved.

**Missing evidence, retrieval failure and hallucination stayed distinguishable**
throughout, which is only possible because the gaps are documented. A correct
refusal on N6-N8 is a pass, not a miss.

---

## 5. Honest limitations

- **One rerank-on run.** The off arm has two; the on arm has one. The +4.5
  aggregate exceeds the measured ±0.8 noise, and the four driving cases were
  stable off-arm, but a second on-arm run would tighten this.
- **P4 is unresolved, not a regression.** Under rerank its `hit_rate` fell
  1.00 → 0.67 (one of three migration documents dropped out), which suggests
  reranking 15 candidates down to 5 can favour depth over *breadth* on a
  multi-document comparison. But P4 also moved 1.00 → 0.93 between two identical
  off runs, so it is inside the noise band. **Hypothesis to test, not a finding.**
- **P14 still WARN in both arms** (fact coverage 0.33). The 19-word version
  header is the suspected cause and reranking did not reach it.
- **`unacceptable_claims` cannot catch misattribution.** It is a substring scan,
  so a correct answer saying "Nakamura lacks zero-trust experience" contains the
  same text as a wrong one. Attribution errors are caught by
  `expected_sources`/MRR plus manual trace review. Five negatives rely on
  `check_not_found_handling` alone, because their query names the missing
  artifact and any refusal echoes the term.
- **Judge off.** Deterministic metrics only, by design, so the arms differ in one
  variable.

---

## 6. Recommendations, ranked by measured failure

1. **Decouple the candidate pool from reranking.** P13's failure was recall, not
   ranking: the right chunk was never fetched. Over-fetching is cheap (one Qdrant
   query at a larger limit) while the rerank LLM call is not. Consider raising
   `fetch_k` even on the off path, so the two effects can be measured separately.
2. **Give `search_all_collections` a dynamic per-collection budget.** The fixed
   3-chunks-per-collection split is what starved P10. A query naming price *and*
   technical differentiators deserves more than three slots in the two
   collections that hold the answer.
3. **Instrument the 19 short baseline chunks** rather than rewriting them. They
   are weak-hit generators, and P14 shows a sub-20-word header can hold the only
   copy of a fact while being unreachable by similarity. Not a corpus defect —
   a retrieval-shape finding the corpus was built to expose.
4. **Add a filename-citation rule to `_SYSTEM_DRAFT`.** `_SYSTEM_SCORE` rule 6 and
   `_SYSTEM_COMPARE` rule 7 both require citations; the draft prompt does not.
   Known separately as the 1.39-point gap on the original 8-case pack. Deferred
   to its own commit so its delta stays attributable.
5. **Run the on arm a second time** before quoting +4.5 as a settled number.

No corpus changes. No architecture redesign.
