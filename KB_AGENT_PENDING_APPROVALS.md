# KB Agent — Pending Approvals

Two items awaiting your decision. **Nothing in either section has been applied.**

- **§1** Pricing document cleanup (Issue 5b) — arithmetic audited, corrections proposed.
- **§2** Requirement-scoring determinism (Issue 2) — separate product issue, recommendation only.

Completed and authorized work (the Issue 5a ingestion fix and regression tests) is in the repo and
summarised at the end.

---

## §1 — `PricingStrategy_Notes.md` arithmetic audit

You were right on every point. The audit also found that the correct value is **neither** of the two
figures on record.

### What is already correct

| Item | Check | Result |
|---|---|---|
| Labor line items | 1,920×195 + 3,840×215 + 7,680×158 + 1,920×172 + 960×188 | **$2,924,160** — matches the stated subtotal exactly |
| ODC subtotal | 680,000 + 24,000 + 18,000 | **$722,000** — matches |

### The overhead line

42% of $2,924,160 = **$1,228,147.20 → $1,228,147**

| Version | Value | Error |
|---|---|---|
| Current on disk | `$3,228,147` | **+$2,000,000** |
| Version-0 snapshot | `$2,228,147` | **+$1,000,000** |
| **Arithmetically correct** | **`$1,228,147`** | — |

So the 2026-11-04 smoke test added $1,000,000 to a figure that was **already wrong by $1,000,000**.
Restoring "the original" would not have fixed it. Your catch.

### The decisive constraint

With overhead at $1,228,147 the components reconcile to the stated total **exactly**:

```text
labor      $2,924,160
ODC        $  722,000
overhead   $1,228,147   <- corrected
G&A        $  331,625   (as stated)
fee        $  335,834   (as stated)
           -----------
total      $5,541,766   <- matches the document's stated total exactly
```

**`$5,541,766` appears in `evals/test_cases_benchmark.yaml`.** It is a benchmark anchor and must not
move. The single-value correction is the only change that both fixes the arithmetic and preserves it.

### G&A and fee, as you asked

Both are slightly inconsistent with their own stated formulas, and this is **pre-existing**, not test
damage:

| Line | Stated | Its formula implies | Delta |
|---|---|---|---|
| G&A, "8% on (labor + overhead)" | `$331,625` | 8% × $4,152,307 = **$332,185** | **−$560** (implied rate 7.9865%) |
| Fee, "8%" (base unstated) | `$335,834` | implies a base of **$4,197,925**, which matches **no** combination of the listed components | 7.49% of labor+OH+G&A, or 8.09% of labor+OH |

**Recommendation: do not "fix" these.** Recomputing both at a true 8% gives G&A $332,185, fee
$358,759 and a new total of **$5,565,251** — which **breaks the benchmark anchor**. Full formula
coherence and preserving the anchor are mutually exclusive, and the anchor matters more.

### Also inconsistent, also pre-existing — recommend leaving alone

The discount section's savings mostly do not derive from the line items above:

| Claim | Derives to | Stated | Verdict |
|---|---|---|---|
| "Reduce PM to 75% (saves ~$49K)" | 25% × $374,400 = $93,600 | ~$49K | mismatch |
| "Negotiate 12% discount on Splunk licensing ($81K savings)" | 12% × $680,000 = $81,600 | $81K | **correct** |
| "Reduce fee to 6% ($112K savings)" | 2/8 × $335,834 = $83,958 | $112K | mismatch |
| "Adjusted total: $5,300,000" | $5,541,766 − 49,000 − 81,600 − 112,000 = $5,299,166 | $5,300,000 | **self-consistent with its own stated savings** |

The section is internally consistent with the savings it states, even though two of them do not
follow from the line items. Correcting them would ripple into the adjusted total and widen a
benchmark-fixture edit for cosmetic gain.

### Proposed change — minimal, two lines

```diff
  ## Overhead & Profit
- - Overhead rate: 42% on labor = $3,228,147
+ - Overhead rate: 42% on labor = $1,228,147
  - G&A: 8% on (labor + overhead) = $331,625
  - Fee/profit: 8% = $335,834
- - Status = Good
```

**What this preserves deliberately:**

- The **42% rate itself** — the intentional disagreement with the 30% delivery overhead in
  `IndirectRates_Memo_FY2024.md`. The Step 5 gap finding depends on it and remains valid.
- `$5,541,766`, the benchmark anchor, now genuinely reconciled rather than coincidental.
- `$4.2M–$4.8M` win price target (also a benchmark anchor), untouched.
- Every other figure, including the G&A, fee and discount-section values.

**No supporting financial documentation is invented.** This corrects an arithmetic error and removes a
line of test debris; it adds nothing.

### Execution plan on approval

1. Back up the file; record SHA-256 (current: `78bf8282c4bae8c129b016653e13e3a92a57868e0779fb2324a7953e75279944`).
2. Apply the two-line change above. Nothing else.
3. Confirm the chunk count stays **5** — both edits sit inside the existing "Overhead & Profit" chunk.
4. Wait for watcher re-ingest; confirm `last_ingested_at` advances.
5. Run `python -m evals.audit_ingestion` and require **PASS at 40 files / 274 chunks**.
6. Re-capture **Step 5 (`/kb gaps`)** and **Step 7 (`/kb changes` + `/kb diff`)** only — the two steps
   whose retrieved context includes this file. Archive the current captures as superseded rather than
   overwriting them, following the Section F pattern.

Steps 1, 2, 3, 4, 6 and 8 need no re-capture: none retrieves this file. Verified mechanically — only
`step_gaps_a.json` contains the residue strings anywhere, and only inside `retrieved_items[11]`, never
in the answer. **The Step 5 conclusion is expected to survive unchanged**, since the 42% rate is
untouched; re-capture is for fidelity, not because the finding is at risk.

---

## §2 — Requirement scoring: deterministic composite and score bands

**Separate product issue. No scoring logic has been changed.** This is a recommendation for when you
decide to showcase numerical scoring.

### The three defects

1. **The composite is not reproducible.** `_SYSTEM_SCORE` instruction 3 asks for a *"weighted
   composite score"* and **defines no weights**. Criterion scores 7/7/7/6/5/3 have an unweighted mean
   of 5.83; the model returned 6.5. Nothing in the system can reproduce or audit that.
2. **It can violate its own rule.** Instruction 5 says *"Never round up"*. 6.5 is +0.67 above the
   unweighted mean, in exactly the forbidden direction.
3. **The bands have a gap.** 5–6 is Acceptable, 7–8 is Good. 6.5 belongs to neither; the model picked
   "Acceptable" by chance, and 6.5 → "Good" would have been equally defensible.

### Recommendation: compute the composite in Python, never in the model

The model is good at judging each criterion against evidence. It should not be doing arithmetic the
code can do deterministically and verifiably.

**1. Change the prompt contract.** Keep per-criterion scoring; remove composite arithmetic from the
model's job. Have it emit the criteria and an optional declared weight each, and **omit** the
composite line:

```text
CRITERIA
• [criterion name] — [sub-score]/10 [weight: 0.NN]: [evidence] ([filename])
```

**2. Compute the composite after parsing.** A small pure function in `agent/rag.py`, directly
testable offline:

```python
def composite_score(criteria: list[tuple[float, float | None]]) -> tuple[float, str]:
    """Weighted mean of (sub_score, weight) pairs. Weight None => equal weighting.

    Deterministic and auditable: same criteria always give the same composite,
    which is what makes a score comparable across runs and across corpora.
    """
    if not criteria:
        return 0.0, "Unacceptable"
    weights = [w if w is not None else 1.0 for _, w in criteria]
    total = sum(weights)
    raw = sum(s * w for (s, _), w in zip(criteria, weights)) / total
    # Never round up: instruction 5 is a scoring policy, so enforce it in code.
    score = math.floor(raw * 10) / 10
    return score, band_for(score)
```

**3. Close the band gap explicitly.** Assign the adjectival label from `floor(score)`, so every value
in `[6.0, 7.0)` is Acceptable and the 6.5 ambiguity disappears:

```python
_BANDS = [(9, "Outstanding"), (7, "Good"), (5, "Acceptable"), (3, "Marginal"), (1, "Unacceptable")]

def band_for(score: float) -> str:
    floor = math.floor(score)
    for threshold, label in _BANDS:
        if floor >= threshold:
            return label
    return "Unacceptable"
```

**4. Reframe the projection.** *"would raise the composite from 6.5 to approximately 8"* is a forecast
for a document that does not exist. Either drop the number from instruction 4 and ask only for the
highest-leverage action, or have the code compute it honestly: re-run `composite_score` with the named
criterion raised to its target and label the output `projected`.

**5. Remove CPARS from instruction 4's example.** "CPARS" appears in **none of the 40 corpus
documents** — it is one of the deliberately protected absences. The prompt currently invites the model
to recommend an artifact class the knowledge base is designed never to contain, and a score variant
tested during capture did exactly that. `CLAUDE.md` records this cleanup being done for
`_SYSTEM_DRAFT` rule 3; `_SYSTEM_SCORE` rule 4 was missed.

**6. Label proposed vs delivered evidence.** Scoring `technicalvolume` scores *proposal material*. The
`96.3%` accuracy, `60%` auto-close and UEBA figures are claims and design targets; only the DHS/DOE
outcomes are delivered and customer-referenced. Either have the prompt mark each criterion
`[proposed]` or `[delivered]`, or state the limitation in the output footer.

### Tests to add

Offline, no-pytest, matching `tests/test_chunk_row_pruning.py`:

1. `composite_score` on 7/7/7/6/5/3 equal-weighted returns exactly **5.8** and "Acceptable".
2. Declared weights summing to 1.0 produce the stated weighted mean.
3. Never-round-up: a raw 6.49 and a raw 6.5 both floor to 6.4 / 6.5 and never present as 7.
4. Band boundaries at 2.9/3.0/4.9/5.0/6.9/7.0/8.9/9.0 map to the intended labels, pinning the gap fix.
5. Empty criteria list returns 0.0 / "Unacceptable" rather than raising.
6. A parser test: given a real captured `CRITERIA` block, the extracted sub-scores match the six
   values by hand.

### What not to do

Do not back-fit weights to reproduce 6.5. There is no principled weighting that yields it, and
inventing one to match a historical output would make every archived score incomparable — the same
reasoning that kept MRR at weight 0 in the eval harness.

---

## Completed: Issue 5a (authorized and implemented)

| Change | File |
|---|---|
| `_prune_stale_chunk_rows()` + prune inside the existing transaction, bounded by `len(chunks)` | `ingestion/watcher.py`, `ingestion/onedrive_watcher.py` |
| Shrink-only path no longer returns early; prunes both stores when no chunk needs re-embedding | `ingestion/watcher.py` |
| 17 regression checks across 8 scenarios, driving the real `ingest_file` / `ingest_onedrive_file` | `tests/test_chunk_row_pruning.py` |

**Verification:** all 17 checks pass. A negative control confirms they bite — reverting the fix fails
6 of 17, including both shrink-only assertions. The three existing offline suites still pass,
`compileall` is clean, the real `metadata.db` was not touched by the tests (274 chunks, zero test
rows), and `evals.audit_ingestion` still reports **PASS at 40 files / 274 chunks**.

### One pre-existing bug fixed along the way, because it blocked the tests

`ingestion/watcher.py` imported **`IsEmpty`** from `qdrant_client.models`. That symbol does not exist
in `qdrant_client` 1.17.1 — the correct name is **`IsEmptyCondition`**. Because the import sits
*outside* the `try` in `_prune_stale_points`, the `ImportError` escaped to `ingest_file`'s handler and
**quarantined the file as `CORRUPT_FILE`**. Every local-backend ingest failed this way. It went
unnoticed because the project runs `BACKEND=onedrive`, so `watcher.py` is dormant.

It had to be repaired for the regression tests to exercise real control flow. Fixed to
`IsEmptyCondition` in the import and at the call site, with a comment recording why. **Flagged rather
than assumed approved** — if you would rather this were a separate commit, it is a two-line change to
isolate.
