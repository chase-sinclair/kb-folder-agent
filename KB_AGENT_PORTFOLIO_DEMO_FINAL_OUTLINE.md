# KB Agent — Portfolio Demonstration: Final Outline

**Status: LOCKED.** This is the implementation handoff for the portfolio page. The command sequence,
the captured outputs and the narrative are fixed. No further command discovery, scoring experiments
or engineering work is required to begin building.

| | |
|---|---|
| Captured | 2026-11-09 |
| Dataset | `ConsultingKB` — 40 synthetic documents, 274 chunks, 5 collections |
| Backend | `BACKEND=onedrive` (Microsoft Graph + MSAL) |
| Retrieval | Qdrant hybrid: dense vectors + BM25-style sparse, fused client-side with RRF (k=60) |
| Generation | `claude-opus-4-5`; reranker `claude-haiku-5-5` on `ask`/`agent` paths only |
| Evidence archive | `KB_AGENT_PORTFOLIO_DEMO_SEQUENCE.md` (full responses, verbatim) |
| Steps captured | **8 of 8.** Steps 1-7 backend-executed; Step 8 Slack-executed and Notion-verified |

> **The knowledge base is synthetic.** Every contract number, agency name, dollar figure, person and
> outcome in `ConsultingKB` is **fabricated test data**, purpose-built with planted evidence gaps,
> near-miss document clusters and one deliberate internal inconsistency. Nothing here represents a
> real government contract, award or past-performance outcome. Say so on the page.

**Excerpt convention.** Every quoted block below is a **verbatim excerpt** of a real captured
response, shortened for display and asserted at build time to be a literal substring of its capture
file. Excerpts are never edited, reworded or improved. Full responses live in the evidence archive —
Section C maps each step to its location.

---

## A. Demonstration Overview

### The scenario

A small government-contracting consultancy is bidding a **federal Security Operations Center (SOC)
modernization contract** — synthetic solicitation `HQ-2024-SOC-0041`. To bid, the team must prove it
has done comparable work before, price it credibly, and write it all up. Their evidence is roughly 40
documents spread across five folders: past project records, staff résumés, contracts, pricing files
and technical write-ups.

Nobody has read all of it. That is the normal condition of any document library that grows faster than
the people using it.

### What the team is trying to accomplish

Answer one question honestly before committing to the bid: **can we actually back up what we are about
to promise?** Then produce the first draft, catch what is missing, and assign the loose ends.

### The central narrative

**Understand what we have → analyse it → find the problems → produce real work → track a change →
assign what is left.**

The thread is causal, and a viewer can follow it without knowing anything about government
contracting. Step 2's discovery is what makes Step 3 worth running. Step 5's finding is why the
document in Step 7 gets edited at all. Step 6 is the deliverable the whole session exists to support.

It is also **one session with several people in it**: a capture manager orienting, a proposal manager
investigating, a writer drafting, and — in parallel — a pricing teammate revising a cost file. Step 7
is what the rest of the team sees when that other person's work lands.

### The sequence

| Step | Command | One-line purpose |
|---|---|---|
| 1 | `/kb list` | See the whole library at once |
| 2 | `/kb agent` | Let the AI investigate across folders on its own |
| 3 | `/kb compare` | Put proven work beside proposed work |
| 4 | `/kb score` | Grade readiness against the customer's actual words |
| 5 | `/kb gaps` | Find what is missing — and what disagrees |
| 6 | `/kb draft` | Write the section, flagging anything unsupported |
| 7 | `/kb changes` + `/kb diff` | Notice and explain a teammate's edit |
| 8 | `/kb ticket` | Turn the loose end into a tracked task in Notion |

---

## B. Final Scene-by-Scene Outline

### Step 1 — The whole library, in one line

**The situation.** The bid has just been assigned. Before anything else, the capture manager wants to
know what evidence the team actually has.

**The manual problem.** Opening five folders one at a time to see what is in them, and getting an
answer that is stale the moment someone adds a file.

**The command:**

```text
/kb list
```

**Verbatim excerpt (complete response — it is this short):**

```text
*Contracts* -> `contracts` - 54 chunks
*CostVolume* -> `costvolume` - 49 chunks
*PastPerformance* -> `pastperformance` - 57 chunks
*Resumes* -> `resumes` - 58 chunks
*TechnicalVolume* -> `technicalvolume` - 56 chunks
```

**Emphasise.** This is read live: the chunk counts come from the vector database at call time and the
folder list from the Microsoft Graph API. It is not a fixture. It also makes no AI call, so it is
instant and free.

**Business value.** Five folders of loose documents are now one searchable system, and the viewer gets
the vocabulary for everything that follows.

**Transition.** The team can see it has *past project records* and *technical write-ups*. It cannot see
whether the two agree with each other — which is the next question.

**Status.** `CAPTURED (backend-executed)` · no LLM call · archive line 89.

---

### Step 2 — The AI investigates on its own

**The situation.** Before writing a word, the proposal manager wants to know where the bid is exposed:
what are we claiming that we cannot actually back up?

**The manual problem.** That question cannot be answered by one search. It needs several, in different
folders, each depending on what the last one turned up. Done by hand it means reading dozens of
documents and holding the comparison in your head — and it only works if one person has read
everything.

**The command:**

```text
/kb agent Across our completed federal security operations programs, which SIEM platforms did we actually deploy and what measured improvement did each one achieve, and which capabilities in our proposed SOC technical approach have no matching delivery record?
```

**Verbatim excerpt — the AI choosing its own next move.** In Slack these arrive as separate messages
while it works, so the viewer watches the investigation happen:

```text
🔍 I can see several relevant collections. For this question, I need:
1. **pastperformance** - for completed programs, actual SIEM deployments, and measured improvements
2. **technicalvolume** - for proposed SOC technical approach capabilities

Let me query both simultaneously to gather the evidence.

⚙️ Calling `query_collection` with `{"collection": "pastperformance", "query": "SIEM platform deployment federal security operations measured improvement metrics results"}`

⚙️ Calling `query_collection` with `{"collection": "technicalvolume", "query": "SOC technical approach capabilities SIEM security operations center proposed solution"}`
```

**Verbatim excerpt — what it found:**

```text
| Program | SIEM Platform | Measured Improvement |
|---------|---------------|----------------------|
| **DHS Cyber Transformation** (2023) | **Splunk Enterprise Security** | **67% reduction in MTTD** — from 22 hours to 7.2 hours |
| **DOE SOC Consolidation** (2021-2023) | **Elastic Security** | **44% reduction in MTTR** — from 96 minutes to 54 minutes |
```

```text
We have proven SIEM deployment credentials on **both Splunk ES and Elastic Security** with quantified detection/response improvements. However, the AI/ML capabilities (UEBA, NLP phishing, SOAR auto-triage) and dedicated threat intel integrations in the current proposal represent **capability extensions beyond our demonstrated delivery record**.
```

**Emphasise.** Two things. First, **the AI decided what to look at** — it listed the folders, issued
two searches in one round, read the results, then issued two more based on what it had just learned.
Nobody told it which folders to open. Second, it produced **a specific, actionable business finding**:
the proposal commits to AI-driven automation for which there is *no documented delivery record*. That
is the kind of thing that loses a bid when it goes unnoticed.

Note the careful wording — *"capability extensions beyond our demonstrated delivery record"*, not a
contradiction. The documents do not conflict; the evidence is simply absent. **"No documented
operational deployment" is the accurate framing. A document library can show an absence of evidence;
it can never prove something was definitively never done.**

**Business value.** A risk assessment with a mitigation, in about 30 seconds, from one plain-English
question. The manual equivalent is a senior person's week.

**Transition.** The agent reached that through a reasoning chain. A bid review needs the same analysis
as something a human can check line by line.

**Status.** `CAPTURED (backend-executed)` · 13 reasoning events, 3 tool rounds, 29.56s · archive
line 135. **This is Variant C.** An earlier Variant A is preserved in archive Section F (line 1109) as
a documented retrieval failure — see Section E.

---

### Step 3 — Proven work beside proposed work

**The situation.** The capture manager needs a written, checkable view of *what we have done* versus
*what we are proposing* to take into a bid review.

**The manual problem.** Opening two sets of documents side by side, reading in parallel, and writing up
the differences by hand — where whatever you notice depends on which files you happened to open.

**The command:**

```text
/kb compare pastperformance technicalvolume "For the SOC modernization pursuit, what security operations capability does each of these collections actually establish, and where do they disagree about what we can prove?"
```

**Verbatim excerpt — the observation that takes three documents at once to notice:**

```text
• **Metric definitions differ**: DOE measured MTTR (response), DHS measured MTTD (detection)—the Technical Volume doesn't specify which metric it will target, making direct comparison to past results difficult (2021_DOE_SOC_Consolidation.md, 2023_DHS_CyberTransformation.md, TechnicalApproach_CyberSOC.md)
```

**Verbatim excerpt — the bottom line:**

```text
An evaluator asking "have you done this before?" will find strong evidence for platform migration and analyst workflow design, but will find the AI-assisted threat detection and SOAR automation are forward commitments without historical validation.
```

**Emphasise.** The full response is a **six-dimension comparison table with a filename beside every
claim**, so a reviewer can verify any row. The standout is the metric-definition catch: one past
project measured how fast it *detected* incidents, the other how fast it *responded*, and the proposal
specifies neither — so no past result can be cited as a like-for-like benchmark. That is a subtle,
genuinely useful finding.

**Business value.** A decision-ready artifact instead of a conversation. Every row is traceable, which
is what makes it usable in a review where someone will challenge it.

**Transition.** The comparison shows where the evidence is thin. Next: how thin, measured against the
customer's own requirement language.

**Status.** `CAPTURED (backend-executed)` · top-10 per collection, 20 chunks · archive line 290.

---

### Step 4 — Grading the evidence against the customer's words

**The situation.** Before committing to bid, somebody has to judge honestly whether the material on
hand satisfies what the customer actually asked for.

**The manual problem.** That judgement is usually one senior person's opinion, formed under deadline,
and almost never written down criterion by criterion — so it cannot be challenged or revisited.

**The command:**

```text
/kb score technicalvolume "The contractor shall operate a 24x7 Security Operations Center providing continuous monitoring, incident detection, and incident response across hybrid cloud environments, and shall report mean time to detect and mean time to respond monthly."
```

**Verbatim excerpt — two of the six criteria:**

```text
• Hybrid Cloud Coverage — 5/10: No explicit hybrid cloud monitoring architecture described. Cloud migration documents reference AWS GovCloud and GCP but are separate engagements, not integrated into SOC proposal. (TechnicalApproach_CloudMigration.md)

• MTTD/MTTR Monthly Reporting — 3/10: No mention of mean time to detect or mean time to respond metrics, measurement methodology, reporting cadence, or baseline targets. Quality documents discuss availability metrics but not security-specific KPIs. (not found)
```

**Verbatim excerpt — a named weakness:**

```text
• MTTD/MTTR reporting requirement completely unaddressed — no metrics, baselines, or reporting methodology mentioned (not found)
```

**Emphasise — and this is the important display decision.** Lead with **the individual criterion
judgements, the missing evidence and the named weaknesses**. The system breaks a requirement into six
distinct criteria, scores each separately, and cites a file for every judgement — including marking one
criterion `(not found)` rather than inventing support for it. It also applies real scepticism rather
than retrieval: elsewhere in the response it questions whether four rotating analysts can
arithmetically sustain round-the-clock coverage.

> **Do not present the composite score as a validated calculation.** The response opens with
> `COMPOSITE: 6.5/10`, but the six criterion scores (7, 7, 7, 6, 5, 3) have an unweighted mean of
> **5.83**, and the prompt asks for a *"weighted composite"* without defining any weights. 6.5 is a
> holistic model estimate that cannot be reproduced or audited. The accompanying projection that one
> fix would reach *"approximately 8"* is likewise an estimate, not a forecast. **Showcase the
> criterion-level reasoning; omit or explicitly caveat the headline number.** Tracked in Section E.
>
> Also worth a line on the page: this step scores a *technical volume*, which is **proposal
> material** — what the company says it will do. The 96.3% accuracy and 60% automation figures it
> credits are claims and design targets, not delivered results. Only the DHS and DOE outcomes
> elsewhere in this sequence are delivered and customer-referenced.

**Business value.** A vague worry becomes a reviewable scorecard that names exactly which requirement
element has no supporting evidence.

**Transition.** The technical side is graded. The cost side is where unsupported numbers do the most
damage.

**Status.** `CAPTURED (backend-executed)` · top-15, 15 chunks · archive line 407.

---

### Step 5 — What is missing, and what disagrees

**The situation.** With the technical side graded, the team turns to the cost file — the part an
evaluator scrutinises hardest.

**The manual problem.** Knowing what a document set *does not* contain is far harder than searching
what it does; absences are invisible. So are two documents that quietly state different numbers for
the same thing — nobody notices until a reviewer does, at the worst possible moment.

**The command:**

```text
/kb gaps costvolume "cost realism justification and indirect rate support for the SOC pursuit"
```

**Verbatim excerpt — absences, with what would fill each:**

```text
**Hard Gaps**
• No DCAA audit history or forward pricing rate agreement documentation: Include most recent DCAA audit report or FPRA letter showing approved indirect rates
• No cost realism narrative explaining why proposed labor rates are realistic for the SOC pursuit: Develop narrative comparing proposed rates to salary surveys, BLS data, or actual incumbent compensation
```

**Verbatim excerpt — the finding nobody asked for:**

```text
• Indirect rate calculation for SOC pursuit uses inconsistent factors (42% overhead in PricingStrategy_Notes vs. 30% in IndirectRates_Memo): Reconcile which rate schedule applies and document the variance explanation
```

**Verbatim excerpt — the single priority:**

```text
**Priority**
Obtain and include DCAA audit documentation or FPRA approval to substantiate indirect rates—without this, cost realism evaluation will likely result in upward adjustments.
```

**Emphasise.** Two distinct capabilities in one output. It separates **hard gaps** (nothing on the
subject at all) from **soft gaps** (mentioned but too thin to rely on) — real triage, not a list. And
unprompted, it compared figures *across two different documents* and found they disagree: one file
applies overhead at 42%, while the rate memo of record specifies 30%.

> **Describe the discrepancy precisely.** The two rates also use **different allocation bases** — 42%
> on labour versus 30% on wages plus allocated fringe — so they are not directly comparable, and the
> variance cannot be resolved by substituting one for the other. **This does not prove either rate is
> wrong.** It is a genuine inconsistency requiring human reconciliation, which is exactly what the
> agent recommends. Both figures are real content in the synthetic dataset.

**Business value.** Reasoning about absence is qualitatively harder than search, and the
cross-document catch is the behaviour that most resembles a careful human reviewer — the kind of thing
found by someone who has read both files on the same afternoon.

**Transition.** The gaps are known and ranked. The team can now write the parts the evidence *does*
support — and needs the system to be explicit about where it does not.

**Status.** `CAPTURED (backend-executed)` · top-20, 20 chunks · archive line 499.

---

### Step 6 — Writing the section, and admitting what it cannot support

**The situation.** With readiness graded and gaps known, the proposal writer needs a first draft of the
past-performance narrative.

**The manual problem.** Turning scattered source material into prose means pulling exact figures,
dates and quotes out of different files without mistyping any of them. It takes hours. The dangerous
failure is a confident sentence that no document actually supports.

**The command:**

```text
/kb draft pastperformance "Describe the offeror's demonstrated experience operating and modernizing federal security operations centers, including contract values, periods of performance, and measurable detection and response outcomes."
```

**Verbatim excerpt — prose built from real retrieved detail:**

```text
We further demonstrated SOC modernization expertise through the DOE SOC Consolidation effort for the Department of Energy's Office of the CISO (Contract DE-SOC-21-T-0118, $11.5M CPFF, April 2021–March 2023). Under Program Manager Marcus Delgado, we executed SIEM consolidation on Elastic Security, integrating records from 11 legacy tools into a unified operational view covering 5,300 endpoints.
```

**Verbatim excerpt — the system flagging its own gaps, inline:**

```text
[EVIDENCE MISSING: additional SOC operations contracts demonstrating 24/7/365 sustained operations metrics over multi-year periods] [EVIDENCE MISSING: SOC staffing levels and FTE counts for operational engagements]
```

**Emphasise.** The full response is four paragraphs carrying two contract numbers, two dollar values,
two periods of performance, measured before-and-after outcomes and **two verbatim customer quotes** —
then it inserts `[EVIDENCE MISSING: ...]` markers exactly where the library cannot support a claim,
and closes with a coverage line separating what is substantiated from what is flagged. Generation and
honesty in the same output.

> **Describe this as an evidence-backed first draft requiring human review.** It is **not**
> submission-ready and **not** independently verified, and it is generated from synthetic
> demonstration documents. The value is that it is grounded and that it marks its own weak points for
> a person, rather than smoothing them over.

**Business value.** The step that saves the most hours — and the self-flagging is what makes the time
saved trustworthy rather than a liability.

**Transition.** Meanwhile, **in parallel**, a pricing teammate acts on the Step 5 gap. Drafting and
revising the cost file are concurrent activities by different people; Step 7 is what the rest of the
team sees when that work lands.

**Status.** `CAPTURED (backend-executed)` · top-20, 20 chunks · archive line 569.

---

### Step 7 — Noticing a teammate's edit, and explaining it

**The situation.** Acting on the gap analysis, a pricing teammate edits the strategy note to document
the rate conflict and assign it. Nobody announces it. The rest of the team finds out from the agent.

**The manual problem.** Shared documents change constantly and silently. Finding out what moved means
re-reading the file and comparing it against your memory of it — and until you do, you are working
from a stale understanding.

**The commands:**

```text
/kb changes costvolume
/kb diff CostVolume PricingStrategy_Notes.md
```

**Verbatim excerpt — detected with no one telling it:**

```text
• **PricingStrategy_Notes.md** – revised and re-indexed on 2026-11-09 at 21:33 UTC; contains 6 chunks across 3 version snapshots
```

**Verbatim excerpt — the change explained in prose:**

```text
**Addition: Rate Reconciliation Section**

A new section titled "Rate Reconciliation - Open Action" was added on 2026-11-09, documenting:

- **Discrepancy identified**: The indirect rates in this pricing summary don't match the official FY2024 rates memo
- **Specific conflict**: This file uses 42% overhead on labor and 8% G&A on labor plus overhead, while the FY2024 memo specifies 30% delivery overhead on direct wages plus fringe, and 10% G&A on a broader base including other direct costs
- **Complication noted**: The allocation bases differ, making the schedules not directly comparable
- **Action required**: Finance must confirm in writing which rate schedule governs bid HQ-2024-SOC-0041
```

**Emphasise.** The detection is genuinely autonomous: the system was not told the file changed and
picked it up **10 seconds** after the edit, through the OneDrive API. The stronger half is the
explanation — it does not print a patch, it reads the change and explains the *substance*: which two
rate schedules conflict, that the allocation bases differ so the variance cannot be resolved by
substitution, who owns it, and that it blocks submission. That is derived from the edit, not copied
from it.

> **The discrepancy was documented and assigned — not resolved.** The two rate schedules still
> disagree. What changed is that the conflict is now written down, has an owner, and blocks
> submission. Do not let the page imply a fix.
>
> **Dataset provenance.** This pair was captured on **2026-11-09** against the dataset state of that
> date, in a controlled, fully disclosed edit that was reverted immediately afterwards (the file's
> SHA-256 is byte-identical before and after; the post-restore ingestion audit passed at 40 files /
> 274 chunks). **The `/kb diff` output is not reproducible today**: the file now carries four version
> snapshots, and `/kb diff` reads the most recent two, so re-running it would describe the
> reconciliation note being *removed* rather than added. Treat the captured output as a dated artifact.
> Archive Section B (line 639) discloses the edit in full.

**Business value.** The clearest argument for connecting AI to a living document library rather than a
one-time upload. The library changes under you; this is the system keeping up and explaining the
change in a sentence.

**Transition.** The conflict is recorded but the underlying question is still open, so it needs an
owner and a deadline where the team actually tracks work.

**Status.** `CAPTURED (backend-executed)` · `/kb changes` 1 source; `/kb diff` compared snapshots 1→2
of 3 as of capture · archive line 639.

---

### Step 8 — Handing off what still needs a human

**The situation.** The rate reconciliation needs an owner and a deadline before the cost volume can be
submitted.

**The manual problem.** Analysis is worthless if the follow-up is never assigned. The findings live in
a chat thread; the team's work lives in a task tracker. Somebody has to retype it, and often nobody
does.

**The command:**

```text
/kb ticket "Confirm which indirect rate schedule governs the HQ-2024-SOC-0041 cost volume" high 2026-11-20
```

**Execution status:** **`CAPTURED (Slack-executed, Notion-verified)`** — 2026-11-09, 6:22 PM.
**The only step captured from a real Slack client**; Steps 1-7 were backend-executed.

**The interaction has two beats, and both are worth showing.** The command does not create anything.
It opens a modal, prefilled from what was typed, so the person can review, assign and annotate before
anything is written:

| Field | As presented |
|---|---|
| Task name | `Confirm which indirect rate schedule governs the HQ-2024-SOC-0041 cost volume` |
| Priority | `High` |
| Due date | `November 20th, 2026` |
| Assignee | `Chase Sinclair` |
| Additional notes | The 42% / 30% overhead conflict, in two paragraphs |

**Verbatim excerpt — the Slack confirmation after submitting:**

```text
🎫 Ticket Created
────────────────────────────────────────────────────────────
✅ Confirm which indirect rate schedule governs the HQ-2024-SOC-0041 cost volume created (Priority: High), due 2026-11-20
Assigned to Chase Sinclair in Notion
Additional notes saved to the page body.

Open in Notion
```

**The Notion task that resulted:**

| Property | Type | Value |
|---|---|---|
| Task name | title | `Confirm which indirect rate schedule governs the HQ-2024-SOC-0041 cost volume` |
| Assignee | **people** | `Chase Sinclair` — a real Notion person, not a text field |
| Due date | date | `11/20/2026` |
| Priority | select | `High` |
| Status | status | `Not started` |
| Page body | blocks | `Additional Notes` heading + the two-paragraph rate conflict |

**Emphasise.** Three things. The system **reaches outside its own boundary** into the team's real
tooling — the difference between an analysis tool and something that participates in how work moves.
The assignee is a **genuine Notion people property**, not a name typed into a title. And the
**review step is the point**: the agent proposes, a human confirms, and only then does anything get
written to a system of record.

**Business value.** The session ends as a tracked commitment with an owner and a date, not a message
that scrolls away. The finding from Step 5 is now someone's job.

**Transition.** End of session. The bid has a graded readiness assessment, a drafted narrative, a
documented inconsistency and an assigned owner.

**Status.** `CAPTURED (Slack-executed, Notion-verified)` · archive line 732 · screenshots
`create_ticket.png`, `slack_confirmation.png`, `notion_ticket.png`, `notion_ticket_2.png`.

---

## C. Complete Source References

Full, unedited responses are in **`KB_AGENT_PORTFOLIO_DEMO_SEQUENCE.md`**. That file is the evidence
archive and must be preserved. Retrieve full outputs there whenever an excerpt above is insufficient.

| Step | Command | Full response location | Capture file |
|---|---|---|---|
| 1 | `/kb list` | archive line 89 | `out/step_list.json` |
| 2 | `/kb agent` (Variant C) | archive line 135 | `out/step_agent_c.json` |
| 3 | `/kb compare` | archive line 290 | `out/step_compare_a.json` |
| 4 | `/kb score` | archive line 407 | `out/step_score_a.json` |
| 5 | `/kb gaps` | archive line 499 | `out/step_gaps_a.json` |
| 6 | `/kb draft` | archive line 569 | `out/step_draft_a.json` |
| 7 | `/kb changes` + `/kb diff` | archive line 639 | `out/step_changes.json`, `out/step_diff.json` |
| 8 | `/kb ticket` | archive line 732 | *Slack client + Notion page (screenshots)* |

**Supporting documents in the archive**

| Content | Location |
|---|---|
| Slack-rendered output (what a Slack user sees, via the real `clean_for_slack`) | Step 3, archive line 290 — includes the pipe-table-to-monospace conversion |
| Variant A of Step 2, preserved verbatim with its retrieval-failure diagnosis | archive Section F, line 1109 |
| A captured-but-excluded refusal demonstration (`/kb ask` on an absent record) | archive Section D, line 911 |
| Commands considered and deliberately excluded, with reasons | archive Section D, line 906 |
| Fact-check and restoration verification | archive Section E, line 980 |

**Other files to preserve:** `KB_AGENT_DEMO_REVIEW.md` (the accuracy review and the retrieval-failure
investigation) and `KB_AGENT_PENDING_APPROVALS.md` (the pricing arithmetic audit and the scoring
recommendation).

> **Note on the capture files.** The `out/*.json` files were written to a session scratchpad and are
> **not** in the repository. The archive Markdown contains every response verbatim, so it is the
> durable record. If raw JSON is wanted alongside the page, it must be copied into the repo
> deliberately.

---

## D. Missing Assets

### Required — none outstanding

**All eight steps are captured.** The Notion integration was executed live and verified, so nothing
is unproven and no step needs a caveat about being unexecuted.

| Asset | Status |
|---|---|
| Notion ticket confirmation (Slack message) | **Held** — `slack_confirmation.png` |
| Notion page screenshot | **Held** — `notion_ticket.png` (page) and `notion_ticket_2.png` (tracker row) |
| Slack modal screenshot | **Held** — `create_ticket.png`, showing review-before-create |

These four files live in the user's `Downloads`, **not in the repository**. Copy them into the
portfolio project deliberately; they are the only visual evidence for Step 8.

**One caution when using `notion_ticket_2.png`.** The tracker screenshot also shows earlier rows
whose titles begin with a literal `"` and run on into their own arguments — artifacts of the
quote-parsing bug that this live test exposed, captured before cleanup. The duplicate from the failed
run has since been deleted; the screenshot predates that. Crop to the `6:22 PM` row, or caption the
others honestly as the bug.

### Optional — visual reference only

| Asset | Why it helps |
|---|---|
| **Slack-native screenshots of Steps 1-7** | The captured strings are the model's exact output, but Slack adds Block Kit headers (`💬 PastPerformance`, `🔍 Gaps — …`), context footers (`📄 Evaluated against 15 chunk(s) from 6 source(s)`), and pulls the composite and coverage lines into their own blocks. Useful for visual fidelity; the commands are already validated and will return. |
| **Screen recording of `/kb agent`** | Step 2's reasoning events arrive as **separate sequential messages** in Slack. The feel of watching it think cannot be reconstructed from a static capture. Note `run_agent` is non-deterministic: a re-run may use different search terms or fewer rounds. |
| **Thread follow-up screenshot** | `/kb ask` posts in-channel so users can reply in-thread, and the bot answers with conversation history. Slack-only; demonstrates that a follow-up needs no repetition of folder or subject. |

### Presentation constraint to design around

`clean_for_slack()` converts Markdown pipe tables into monospace blocks and pads every column to its
widest cell. Step 3's comparison table becomes lines **over 300 characters long**, which Slack renders
in a horizontally scrolling code block. A faithful reproduction needs horizontal scroll or narrower
source cells; it will not wrap on its own. **Do not modify the captured response to fit a layout.**

---

## E. Engineering Backlog

Documentation only. **None of this blocks website development.**

### Completed and verified

| Item | Status |
|---|---|
| Orphaned SQLite `chunks` rows when a file's chunk count decreases | **Fixed** in both watchers — prune bounded by `len(chunks)`, inside the existing transaction |
| Local watcher's early return skipping cleanup on a shrink with unchanged survivors | **Fixed** — the guard now also checks for a stale tail |
| Regression coverage | **Added** — `tests/test_chunk_row_pruning.py`, 17 checks across 8 scenarios, driving real ingestion; a negative control fails 6 of 17 against pre-fix code |
| Dead `IsEmpty` import in `ingestion/watcher.py` (should be `IsEmptyCondition`) | **Fixed.** It raised `ImportError` outside a `try`, which quarantined the file as `CORRUPT_FILE` — **every local-backend ingest was failing.** Hidden because the project runs `BACKEND=onedrive`. |
| Controlled-edit restoration | **Verified** — SHA-256 identical before and after; ingestion audit passes at 40 files / 274 chunks |

### Outstanding — awaiting decision

| # | Issue | Impact |
|---|---|---|
| 1 | **Scoring composite is not deterministic.** `_SYSTEM_SCORE` requests a *"weighted composite"* and defines no weights; 6.5 is unreproducible from criterion scores averaging 5.83, and arguably breaches the prompt's own "never round up" rule. The adjectival bands also have a gap (5-6 / 7-8 leaves 6.5 unassigned). | **Affects the page directly** — Step 4 must not present the composite as validated. Recommendation (compute in Python, floor the result, close the band gap) is in `KB_AGENT_PENDING_APPROVALS.md` §2. Not implemented. |
| 2 | **Agent retrieval is the weakest in the system.** `agent_loop.py` uses `top_k=5` with the reranker off, deliberately, to avoid multiplying latency across rounds. A lexical decoy took 4 of 5 slots and produced a confident, well-cited, wrong conclusion (Variant A). | Diagnosed and worked around by question wording. Preserved as archive Section F. No code change proposed. |
| 3 | **Pricing document arithmetic.** `PricingStrategy_Notes.md` states overhead of `$3,228,147`; 42% of the stated labour subtotal is `$1,228,147`. The version-0 value `$2,228,147` is *also* wrong. The corrected value reconciles the document's stated total exactly, and that total is a benchmark anchor. | Two-line correction proposed and **not applied**; file is untouched. Applying it would require re-capturing Steps 5 and 7 only. See `KB_AGENT_PENDING_APPROVALS.md` §1. |
| 4 | **Leftover `- Status = Good` line** in the same file, from a 2026-11-04 smoke test. | Cosmetic; bundled with #3. |
| 5 | **`_SYSTEM_SCORE` rule 4 cites CPARS** as its worked example, a record type that appears in **none** of the 40 corpus documents and is a deliberately protected absence. | Invites recommendations for an artifact the dataset never contains. Same cleanup was already done for `_SYSTEM_DRAFT`. |
| 6 | **Unchanged chunk rows keep a stale `file_hash`** after an incremental re-ingest. | Pre-existing, cosmetic; nothing reads it for skip decisions. Noted, not fixed. |
| 7 | **27 Qdrant points predate the `chunk_index` payload field**, so duplicate detection is skipped for them in the audit. | Reported as INFO by the audit. Exact count agreement across all three stores makes an undetected duplicate very unlikely. |

---

## F. Final Readiness Confirmation

| Confirmation | Status |
|---|---|
| **The demonstration sequence is locked** | **Yes.** Eight steps, fixed order, Variant C at Step 2. No further discovery or reordering. |
| **Each step has a clear business purpose** | **Yes.** Every step states situation, manual problem, value and transition in plain English, with no procurement jargon left unexplained. |
| **Captured outputs are authentic and traceable** | **Yes.** All 8 steps executed against the live system — 7 through the backend, Step 8 through a real Slack client with the Notion page inspected directly. Every backend excerpt is asserted at build time to be a literal substring of its capture file. |
| **Main findings accurately represented** | **Yes.** Figures were verified against the corpus. The one derived figure (`44%` MTTR reduction, computed from 96→54 minutes) is labelled as computed. Step 2's finding is framed as absence of documented evidence, not as proof of absence. |
| **Limitations documented** | **Yes.** The unreproducible composite, the rate discrepancy requiring human reconciliation, the draft's first-draft status, Step 7's documented-not-resolved outcome and its dated provenance are all stated at the point of use. |
| **Missing manual captures clearly marked** | **Not applicable — none remain.** Step 8 is captured and verified. Section D lists only optional visual polish. |
| **No additional engineering required to begin** | **Yes.** All authorized fixes are complete and verified. Every outstanding item is documentation or an awaiting-approval decision, and none blocks building the page. |

**One thing the implementer must not do:** present the Step 4 composite score as a validated
calculation. It is a holistic model estimate that cannot be reproduced from the criterion scores.

**Evidence status: complete.** Eight of eight steps captured, every output traceable to a real
execution.
