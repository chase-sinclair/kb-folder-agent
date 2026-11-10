# KB Agent Demo — Accuracy & Consistency Review

**Reviewed:** 2026-11-09 · **Subject:** `KB_AGENT_PORTFOLIO_DEMO_SEQUENCE.md`

`KB_AGENT_PORTFOLIO_DEMO_SEQUENCE.md` is **unchanged**. Every raw response in it is preserved
verbatim. This file records what the review found, one replacement execution, and proposed changes
awaiting approval. The eight-step sequence is preserved.

---

## 1. Portfolio readiness by step

| Step | Command | Readiness | Blocking issue |
|---|---|---|---|
| 1 | `/kb list` | **Ready** | None |
| 2 | `/kb agent` | **Recapture recommended** | Variant A's conclusion is materially misleading on Splunk experience. Replacement captured below. |
| 3 | `/kb compare` | **Ready** | None. Its Splunk finding is correct and is what exposed the step 2 problem. |
| 4 | `/kb score` | **Ready with required annotation** | The 6.5 composite is not arithmetically derivable and must be labelled, not re-run. |
| 5 | `/kb gaps` | **Ready; recapture only if residue is cleaned** | Finding is sound; its retrieved context contains pre-existing test residue. |
| 6 | `/kb draft` | **Ready with wording correction** | Must not be described as "submission-grade". |
| 7 | `/kb changes` + `/kb diff` | **Ready with annotation correction** | Must not imply the rate conflict was resolved. |
| 8 | `/kb ticket` | **Open by design** | `MANUAL CAPTURE REQUIRED` — awaiting your Slack + Notion evidence. |

**Net:** no step needs to be discarded. One step warrants a replacement capture, three need annotation
corrections, and one is awaiting your manual evidence.

---

## 2. Issue 1 — Step 2's Splunk conclusion

### What you spotted is real, and the cause is not what it looks like

Variant A's table row reads `Splunk ES platform | DOE used Elastic Security, not Splunk`, and its
closing paragraph frames the AI/ML promises as unproven. A reader reasonably concludes the team has no
Splunk delivery record. Step 3 contradicts that: DHS migrated 14 legacy tools to **Splunk Enterprise
Security** across 6,200 endpoints.

**The agent did not overlook retrieved evidence. It never retrieved it.** I replayed the agent's three
exact queries against the live index using the same call the agent makes
(`orchestrator.search(collection, query, rerank=False)`, top-5):

| # | Collection | Query (verbatim from the capture) | `2023_DHS_CyberTransformation.md` present? |
|---|---|---|---|
| 1 | `pastperformance` | "24x7 security operations SOC monitoring continuous coverage" | **No** — 4 of 5 hits were the DOE file |
| 2 | `technicalvolume` | "24x7 security operations SOC modernization technical approach capabilities" | n/a — all 5 hits `TechnicalApproach_CyberSOC.md` |
| 3 | `pastperformance` | "AI machine learning automation SOAR playbooks threat hunting UEBA behavioral analytics" | **No** — DOE, VA, SSA only |

The DHS file was absent from every pastperformance result. So variant A's statement was **true about
its own context** and false about the knowledge base. That is a retrieval failure, not a hallucination
and not a reasoning lapse.

### Root cause

Two compounding factors, both documented in the project's own benchmark findings:

1. **`2021_DOE_SOC_Consolidation.md` is the designated lexical decoy for `PastPerformance`** — densely
   loaded with "cybersecurity modernization", "SIEM consolidation" and "Office of the CISO". A query
   phrased around *24x7 operations* lets it occupy 4 of 5 slots.
2. **The agent runs the weakest retrieval configuration in the system.** `agent/agent_loop.py` calls
   `orchestrator.search(..., rerank=False)` with the default `top_k=5`. By contrast
   `compare_collections` uses `top_k=10` per collection. Step 3 found DHS partly because it simply
   looked at twice as much.

This is worth stating plainly on the portfolio page rather than hiding: the reranker is deliberately
disabled for the agent to avoid multiplying latency across rounds, and this is the measurable cost of
that trade-off.

### Replacement execution (variant C)

Per your instruction, variant A is preserved and this is captured as a **separate execution**. The
question names the platform dimension explicitly so retrieval reaches both completed programs.

**Command:**

```text
/kb agent Across our completed federal security operations programs, which SIEM platforms did we actually deploy and what measured improvement did each one achieve, and which capabilities in our proposed SOC technical approach have no matching delivery record?
```

**Execution status:** `CAPTURED` — backend-executed, 13 events, 3 rounds, 29.56s. Not a Slack client capture.

**Raw backend response — reasoning stream:**

```text
[event 1]
🔍 I need to analyze completed federal security operations programs to find deployed SIEM platforms with their measured improvements, then compare proposed SOC capabilities against delivery records. Let me start by exploring what collections are available.

[event 2]
⚙️ Calling `list_collections` with `{}`

[event 3]
📄 Result: • contracts: 54 chunks
• technicalvolume: 56 chunks
• resumes: 58 chunks
• pastperformance: 57 chunks
• costvolume: 49 chunks

[event 4]
🔍 I can see several relevant collections. For this question, I need:
1. **pastperformance** - for completed programs, actual SIEM deployments, and measured improvements
2. **technicalvolume** - for proposed SOC technical approach capabilities

Let me query both simultaneously to gather the evidence.

[event 5]
⚙️ Calling `query_collection` with `{"collection": "pastperformance", "query": "SIEM platform deployment federal security operations measured improvement metrics results"}`

[event 6]
⚙️ Calling `query_collection` with `{"collection": "technicalvolume", "query": "SOC technical approach capabilities SIEM security operations center proposed solution"}`

[event 7]
📄 Result: [2023_DHS_CyberTransformation.md] ## Executive Summary
Delivered a full-scale cybersecurity modernization for DHS's Office of the CISO, migrating 14 legacy security tools to a unified SIEM platform (Splunk Enterprise Security) and implementing zero-trust network architecture across 6,200 endpoints.
...

[event 8]
📄 Result: [TechnicalApproach_CyberSOC.md] ## 1. Understanding of the Requirement
The Government seeks to modernize its Security Operations Center (SOC) by consolidating 9 disparate security tools into a unified platform, achieving 24/7 Tier 1–3 analyst coverage, and implementing AI-assisted threat detection t...

[event 9]
🔍 I have good information on SIEM deployments and the proposed SOC approach. Let me do one more search to ensure I capture all capabilities mentioned in the technical approach and any additional past performance details.

[event 10]
⚙️ Calling `query_collection` with `{"collection": "pastperformance", "query": "AI machine learning UEBA threat hunting phishing detection SOAR automated playbooks capabilities delivered"}`

[event 11]
⚙️ Calling `query_collection` with `{"collection": "technicalvolume", "query": "UEBA user entity behavior analytics NLP phishing AI machine learning anomaly detection"}`

[event 12]
📄 Result: [2021_DOE_SOC_Consolidation.md] ## Technical Environment
Elastic Security received endpoint events and identity records through collectors that retained the source timestamp and the original event identifier. Analysts could open a normalized record and trace it back to the raw message when a correla...

[event 13]
📄 Result: [TechnicalApproach_CyberSOC.md] ### 2.2 AI/ML Threat Detection
- Behavioral baselines for all 8,400 user accounts using UEBA models trained on 90-day activity window
- Anomaly detection triggering on >3σ deviation from individual user baseline
- Custom NLP model classifying phishing emails with 96.3...
```

**Raw backend response — final answer:**

```text
## Summary of Findings

### SIEM Platforms Deployed and Measured Improvements

| Program | SIEM Platform | Measured Improvement |
|---------|---------------|----------------------|
| **DHS Cyber Transformation** (2023) | **Splunk Enterprise Security** | **67% reduction in MTTD** — from 22 hours to 7.2 hours |
| **DOE SOC Consolidation** (2021-2023) | **Elastic Security** | **44% reduction in MTTR** — from 96 minutes to 54 minutes |

**Source citations:**
- *2023_DHS_CyberTransformation.md*: "migrating 14 legacy security tools to a unified SIEM platform (Splunk Enterprise Security)... Reduced mean time to detect (MTTD) incidents by 67%"
- *2021_DOE_SOC_Consolidation.md*: "Marcus Delgado led SIEM consolidation on Elastic Security... DOE reported mean time to respond falling from 96 to 54 minutes"

---

### Proposed SOC Capabilities With No Matching Delivery Record

Based on *TechnicalApproach_CyberSOC.md*, the following proposed capabilities have **no documented past delivery** in our performance records:

| Proposed Capability | Gap Analysis |
|---------------------|--------------|
| **UEBA (User Entity Behavior Analytics)** | Behavioral baselines for 8,400 users with anomaly detection at >3σ deviation — no UEBA delivery found |
| **NLP Phishing Classification Model** | Custom model with claimed 96.3% accuracy — validated on DHS corpus but never operationally deployed by us |
| **Splunk SOAR Automated Triage** | Proposed to auto-close ~60% of Tier 1 alerts — no SOAR playbook automation delivered (DHS had *threat-hunting* playbooks, not SOAR triage) |
| **Integrated Threat Intel Feeds** | FS-ISAC, CISA KEV, Mandiant Advantage integration proposed — no feed integration documented |
| **Jupyter-based Threat Hunting Platform** | Dedicated hunt team with notebook-to-data-lake connection — not in delivery history |

**Note:** DHS included "automated threat-hunting playbooks covering 38 MITRE ATT&CK techniques" (*2023_DHS_CyberTransformation.md*), but this differs from the SOAR-based *automated triage* proposed for closing alerts without analyst intervention.

---

### Bottom Line
We have proven SIEM deployment credentials on **both Splunk ES and Elastic Security** with quantified detection/response improvements. However, the AI/ML capabilities (UEBA, NLP phishing, SOAR auto-triage) and dedicated threat intel integrations in the current proposal represent **capability extensions beyond our demonstrated delivery record**.
```

### Why variant C is the better portfolio artifact

- It retrieves DHS (visible at event 7) and reports **both** platforms with their measured outcomes,
  so the Splunk inconsistency disappears.
- Its bottom line — *"proven SIEM deployment credentials on both Splunk ES and Elastic Security"* — is
  the opposite of variant A's implication and is the correct reading of the corpus.
- The retained gap findings are **sharper**, not weaker. It draws a distinction variant A missed: DHS
  delivered "automated threat-hunting playbooks covering 38 MITRE ATT&CK techniques" but that is not
  the SOAR-based *auto-triage* being proposed. That is a more defensible claim than "no SOAR".
- It independently separates proposal claims from delivery record, describing the NLP model as
  *"validated on DHS corpus but never operationally deployed by us"* — the distinction issue 2 asks for.

**Figures verified as literal corpus content:** `38 MITRE`, `FS-ISAC`, `CISA KEV`, `Mandiant`,
`Jupyter`, `Splunk Enterprise Security`, `6,200`, `3σ`, `8,400`, `96.3%`, `60%`, `22 hours`,
`7.2 hours`, `67%`.

**One derived figure:** `44% reduction in MTTR` is **computed by the model**, not quoted. The corpus
states "falling from 96 to 54 minutes"; (96−54)/96 = 43.75%, rounded to 44%. Arithmetically correct,
but it is a calculation and should be labelled as such rather than presented as a documented metric.

---

## 3. Issue 2 — Step 4's composite score does not reconcile

### The arithmetic

Criterion scores: **7, 7, 7, 6, 5, 3**. Sum 35, unweighted mean **5.83**. Reported composite: **6.5**.

### Are there weights? No.

`_SYSTEM_SCORE` in `agent/rag.py` instruction 3 says only *"Produce a weighted composite score."*
**No weighting scheme is defined anywhere** — not in the prompt, not in the handler, not in the
retrieval layer. There is no basis on which 6.5 is derivable from 7/7/7/6/5/3, and no weighting is
disclosed in the output.

**The composite must be treated as an unsubstantiated model estimate.** It is a plausible holistic
judgement, not a computation, and the output does not say which it is.

Two aggravating details:

- **It arguably violates its own prompt.** Instruction 5 reads *"Never round up — if evidence is thin,
  the score must reflect it."* 6.5 is 0.67 above the unweighted mean, in the direction the rule
  forbids.
- **6.5 falls in a gap in the scale.** The bands are 5–6 Acceptable and 7–8 Good. 6.5 belongs to
  neither; the model labelled it "Acceptable".

### The projection to ~8

*"this single addition would raise the composite from 6.5 to approximately 8"* is a model estimate,
and the prompt explicitly solicits exactly this shape of statement (instruction 4: *"a CPARS rating
specific to this capability would raise this from 7 to 9"*). It is **not verifiable** — it projects a
score for a document that does not exist. It should be presented as the model's suggested next action,
never as a forecast.

### Proposed vs independently validated evidence

Step 4 scores `technicalvolume`, which is **proposal material**, so almost every figure it credits is
something the company *proposes to do*, not something it has *done*:

| Figure | Nature | Source |
|---|---|---|
| `96.3%` phishing accuracy | **Proposal claim.** The document states it was validated on an 18-month DHS corpus; there is no delivery record of operating it. | `TechnicalApproach_CyberSOC.md` |
| `60%` Tier 1 auto-close | **Design target** for an unawarded pursuit | `TechnicalApproach_CyberSOC.md` |
| UEBA on `8,400` users, `>3σ` | **Proposed capability** | `TechnicalApproach_CyberSOC.md` |
| `67%` MTTD reduction, `22h → 7.2h` | **Delivered and customer-referenced** | `2023_DHS_CyberTransformation.md` |
| `96 → 54` minutes MTTR | **Delivered and customer-referenced** | `2021_DOE_SOC_Consolidation.md` |

The step 4 annotation in the sequence file calls these "specific figures" without this distinction.
That needs correcting: scoring a technical volume measures **the quality of what we have written**,
not proven capability.

### Incidental prompt finding (no change made)

`_SYSTEM_SCORE` instruction 4 uses a **CPARS rating** as its worked example. "CPARS" appears in **none
of the 40 corpus documents** — it is one of the deliberately protected absences. The prompt therefore
invites the model to recommend an artifact class the knowledge base is designed never to contain, and
a score variant tested during capture did exactly that. `CLAUDE.md` records that `_SYSTEM_DRAFT` rule 3
was cleaned of CPARS for this reason; `_SYSTEM_SCORE` rule 4 was not. **Flagged only — no production
scoring logic changed.**

---

## 4. Issues 3, 4 and 6 — annotation and description corrections

All of these are edits to my explanatory prose. **No captured response is touched.** Proposed wording,
for your approval:

| # | Location | Current (wrong) | Proposed |
|---|---|---|---|
| 4a | Section A row 2; step 2 body | "runs **two searches in parallel**" | "issues **two tool calls in a single round**, which the loop then executes **sequentially**" |
| 4b | Step 2 demonstration value | "tool selection, parallel execution, and a follow-up query" | "tool selection, **two tool calls batched into one round**, and a follow-up query conditioned on intermediate results" |
| 3a | Step 6 → 7 connection | "so a teammate goes and edits that document" | "**Meanwhile, in parallel**, a pricing teammate acts on the step 5 gap. Drafting the narrative and revising the cost documentation are **concurrent activities by different people**, not a sequence." |
| 3b | Step 7 "Why it's useful" | "the cost issue from step 5 was **addressed**" | "the cost issue from step 5 has been **documented and assigned — not resolved**. The conflicting rates still stand; what changed is that the conflict is now written down, owned and blocking submission." |
| 6c | Section A row 6; step 6 value | "**submission-grade** narrative / prose" | "an **evidence-backed first draft requiring human review**, generated from synthetic demonstration documents" |
| 4c | All step headers | `CAPTURED` | `CAPTURED (backend-executed)` — with a one-line reminder that no step except 8 was captured from a Slack client |

**Verification of 4a/4b.** `agent/agent_loop.py:158-176` iterates `for block in response.content:` and
`await _execute_tool(...)` inside that loop. Each tool completes before the next begins. The two calls
were *requested* together in one assistant turn, which is the genuinely notable behaviour, but
execution is strictly sequential. The `📄` result message is dispatched with `asyncio.create_task` and
never awaited, which is why result ordering can drift between runs.

**Issue 6b — wide table.** Already documented in the sequence file as a presentation limitation with a
width warning. Retained as-is: `_format_table_for_slack()` pads each column to its widest cell,
producing lines over 300 characters that scroll horizontally in Slack. **No captured response modified
and no UI work undertaken.**

---

## 5. Issue 5 — Two engineering issues. Proposed, NOT implemented.

### 5a. Orphaned SQLite chunk rows when a file shrinks

**Severity: High.** Silent, permanent, and it corrupts the chunk-count baseline the entire measurement
story depends on.

**Confirmed behaviour.** Editing `PricingStrategy_Notes.md` grew it 5 → 6 chunks; restoring it shrank
it back to 5. Qdrant was corrected to 49 for `costvolume`; `metadata.db` kept **50**, retaining
`chunk_index=5` with the *edit's* `last_ingested_at` while indices 0-4 carried the restore's. Only
`evals.audit_ingestion` detected it (`expected 5, got 6`; `in db 275 / in qdrant 274`).

**The Qdrant side is already solved; SQLite was never given the equivalent.**
`ingestion/watcher.py:161` has `_prune_stale_points(client, collection_name, file_path, chunk_count)`,
whose docstring names this exact failure — *"trailing points left behind when a file shrinks"* — called
at line 278 with `len(chunks)`. There is no counterpart for the `chunks` table.

**Proposed fix — both watchers, inside the existing transaction:**

```python
# After the INSERT OR REPLACE loop, BEFORE db.commit(), so it is atomic:
await db.execute(
    "DELETE FROM chunks WHERE file_path = ? AND chunk_index >= ?",
    (file_path, len(chunks)),
)
```

| File | Insert loop | Note |
|---|---|---|
| `ingestion/onedrive_watcher.py` | ~line 293 | Writes all `chunks`, so `len(chunks)` is directly in scope. |
| `ingestion/watcher.py` | ~line 309 | **Careful:** this loop iterates `changed_chunks` (incremental re-ingest). The bound must be `len(chunks)` — the full chunk set — **not** `len(changed_chunks)`, exactly as `_prune_stale_points` is already called at line 278. Using the wrong variable would delete live rows. |

**Proposed regression tests.** Follow the existing offline, dependency-free, no-pytest pattern of
`tests/test_quarantine_classify.py`; run with `.\.venv\Scripts\python.exe tests\<name>.py`. Suggested
file `tests/test_chunk_row_pruning.py`, against a temporary SQLite file, no network:

1. **Shrink** — seed 6 rows for a path, re-ingest 5, assert count 5 and `max(chunk_index) == 4`.
2. **Grow** — seed 3 rows, re-ingest 6, assert count 6 and nothing deleted.
3. **Unchanged** — seed 5, re-ingest 5, assert count 5.
4. **Isolation** — seed rows for two paths, shrink one, assert the other is untouched. Guards against a
   missing `file_path` predicate.
5. **Incremental bound** — the `watcher.py` regression specifically: 6 stored chunks, only 1 changed,
   full set now 5. Assert the prune uses the full count and does **not** delete the 4 unchanged live
   rows. This is the test that would catch the `len(changed_chunks)` mistake.
6. **Atomicity** — force the insert loop to raise mid-way and assert no rows were deleted, confirming
   prune and upsert share one transaction.

### 5b. Pre-existing test residue in `PricingStrategy_Notes.md`

**Severity: Medium.** Not introduced by this session; left in place deliberately.

Current on-disk state retains two artifacts of the 2026-11-04 `/kb diff` smoke test:

| Line | Current | Version-0 original |
|---|---|---|
| 28 | `- Overhead rate: 42% on labor = $3,228,147` | `- Overhead rate: 42% on labor = $2,228,147` |
| 31 | `- Status = Good` | *(line absent)* |

The inflated figure no longer reconciles with the file's own `Total Base Year Price: $5,541,766`.
The `42%` **rate** is genuine corpus content present in version 0, so **the step 5 conflict finding
stands regardless of this cleanup**.

**Proposed cleanup procedure (awaiting authorization):**

1. Back up the current file and record its SHA-256.
2. Restore line 28's value to `$2,228,147` and delete the `- Status = Good` line. Nothing else.
3. Confirm the chunk count stays **5** — both edits are inside the existing "Overhead & Profit" chunk,
   so the count should not move. If it does, 5a must be fixed first or a new orphan row appears.
4. Wait for watcher re-ingest; confirm `last_ingested_at` advances.
5. Run `python -m evals.audit_ingestion` and require **PASS at 40 files / 274 chunks**.
6. Re-capture the two dependent steps (below) and archive the originals rather than overwriting them.

**Do not fix 5b before 5a**, or the cleanup risks leaving exactly the orphan row that 5a describes.

---

## 6. Re-execution impact

Which captures depend on the residue, determined by inspecting each capture's retrieved context:

| Step | Retrieves `PricingStrategy_Notes.md`? | Answer quotes residue? | Action if 5b is applied |
|---|---|---|---|
| 1 `list` | No | No | None — chunk counts unchanged |
| 2 `agent` (A and C) | No (`pastperformance`, `technicalvolume`) | No | None |
| 3 `compare` | No | No | None |
| 4 `score` | No (`technicalvolume`) | No | None |
| 5 `gaps` | **Yes** — chunk 11 of 20 | **No** | **Re-capture.** Context changes even though the conclusion should hold. |
| 6 `draft` | No (`pastperformance`) | No | None |
| 7 `changes` + `diff` | **Yes** — it is the subject | No | **Re-capture.** The diff's base version contains the residue. |
| 8 `ticket` | No | n/a | None |

Verified mechanically: only `step_gaps_a.json` contains the residue strings anywhere, and only inside
`retrieved_items[11]` — never in the `answer` field. **Two steps need re-capture if and only if you
authorize 5b.** If you leave the residue, all eight captures stand as recorded.

---

## 7. Final checklist

| # | Issue | Severity | Resolved? | Action still required |
|---|---|---|---|---|
| 1 | Step 2 contradicts step 3 on Splunk experience | **High** | **Yes** — cause identified (retrieval, not reasoning); variant C captured | Choose: replace variant A with C, or publish both as a retrieval case study. Then apply to the sequence file. |
| 1b | Agent uses weakest retrieval (`top_k=5`, `rerank=False`) | Medium | Diagnosed only | Decide whether to disclose as a design trade-off on the page. No code change proposed. |
| 2 | Composite 6.5 vs 5.83 unweighted mean; no weights defined | **High** | **Yes** — confirmed unsubstantiated | Annotate step 4 as a model estimate. **Do not** alter `_SYSTEM_SCORE` without approval. |
| 2b | "~8" projection unverifiable | Medium | **Yes** — identified as prompt-solicited estimate | Label as suggested action, not forecast. |
| 2c | Proposed vs validated evidence conflated | Medium | **Yes** — table provided in §3 | Add the distinction to step 4's annotation. |
| 2d | `_SYSTEM_SCORE` rule 4 cites CPARS, absent from corpus | Low | Flagged only | Your call whether to clean it, as was done for `_SYSTEM_DRAFT`. |
| 3a | Steps 6 and 7 implied sequential, are parallel | Medium | **Yes** — wording drafted in §4 | Apply on approval. |
| 3b | Implied the rate conflict was resolved | **High** | **Yes** — wording drafted in §4 | Apply on approval. It was documented and assigned only. |
| 4a | "two searches in parallel" — they are sequential | Medium | **Yes** — confirmed at `agent_loop.py:158-176` | Apply wording fix. |
| 4b | Backend output vs Slack capture not explicit per step | Low | Partially — labels table exists | Append "(backend-executed)" to each status line. |
| 5a | Orphaned SQLite rows on chunk-count decrease | **High** | **No — not implemented** | Authorize the two-line fix in both watchers + 6 regression tests (§5a). |
| 5b | Pre-existing test residue in pricing file | Medium | **No — not implemented** | Authorize cleanup (§5b). Must follow 5a. |
| 6a | `/kb ticket` unexecuted | Low | **Open by design** | **You capture:** Slack confirmation + Notion page screenshot. Stays `MANUAL CAPTURE REQUIRED` until provided. |
| 6b | Slack comparison table exceeds 300 chars/line | Low | **Yes** — documented | None. Presentation note for implementation. |
| 6c | Drafts called "submission-grade" | Medium | **Yes** — replacement wording in §4 | Apply on approval. |
| 6d | Re-execution impact of a residue fix unknown | Medium | **Yes** — mapped in §6 | Re-capture steps 5 and 7 only if 5b is authorized. |

### Nothing was changed

- `KB_AGENT_PORTFOLIO_DEMO_SEQUENCE.md` — untouched.
- All original captures — preserved; variant C saved as a new file alongside variant A.
- Production code, prompts, documents and external systems — unmodified.
- One new backend execution was run (variant C, ~4 Opus calls) plus three free retrieval replays.
