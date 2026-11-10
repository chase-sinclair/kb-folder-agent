# KB Agent — Feature & Command Guide

AI-powered knowledge base agent. It watches a folder tree (local disk or OneDrive),
chunks and embeds every supported document into a per-folder Qdrant collection, and
answers questions about that content in Slack — with citations — through the `/kb`
slash command.

Each **top-level folder becomes one collection**. Everything downstream (RAG,
scoring, drafting, comparison) operates on collections, so the folder structure is
the knowledge architecture.

**Surfaces:** Slack (`/kb`, primary), a REST + SSE API (`python run_api.py`), and a
Next.js web UI (`web/`).
**Backends:** `BACKEND=local` (watchdog, instant) or `BACKEND=onedrive` (Graph API
polling, `ONEDRIVE_POLL_INTERVAL`). Every feature below works identically on both.

---

## 1. What's in the knowledge base right now

Captured from `storage/metadata.db` on 2026-11-04, `BACKEND=onedrive`,
`ONEDRIVE_FOLDER=ConsultingKB`:

| Collection | Folder | Files | Chunks |
|---|---|---|---|
| `contracts` | `ConsultingKB/Contracts` | `IDIQ_GovCloud_Services_2023.md`, `NDA_Template_Standard.md` | 8 |
| `costvolume` | `ConsultingKB/CostVolume` | `PricingStrategy_Notes.md` | 5 |
| `pastperformance` | `ConsultingKB/PastPerformance` | `2021_DoD_CloudMigration.md`, `2022_VA_DataAnalytics.md`, `2023_DHS_CyberTransformation.md` | 9 |
| `resumes` | `ConsultingKB/Resumes` | `James_Okafor_DataEngineer.md`, `Sarah_Chen_PM.md` | 10 |
| `technicalvolume` | `ConsultingKB/TechnicalVolume` | `DataModernization_WhitePaper.md`, `TechnicalApproach_CyberSOC.md` | 10 |

Folder names map to collection names via
`re.sub(r"[^a-z0-9]+", "_", folder.lower())`, so `PastPerformance` →
`pastperformance` (camelCase produces no underscore).

---

## 2. Command reference

| Command | What it does |
|---|---|
| `/kb list` | Every collection with its chunk count |
| `/kb ask <folder> "<question>"` | RAG answer from one collection, with sources |
| `/kb ask all "<question>"` | Fan-out: queries every collection, synthesises one answer |
| `/kb ask "<question>"` | No folder given — auto-routes to the best-matching collection, or escalates to the agent if the question looks multi-hop |
| `/kb agent <question>` | Multi-step agentic loop, reasoning posted live to the channel |
| `/kb score <folder> "<requirement>"` | Scores KB readiness against an RFP requirement, 1–10 federal adjectival scale |
| `/kb gaps <folder> "<topic>"` | Hard vs. soft gap analysis with a priority recommendation |
| `/kb draft <folder> "<requirement>"` | Writes a compliant proposal narrative from KB content, flagging unsupported claims |
| `/kb compare <folder1> <folder2> "<question>"` | Side-by-side comparative analysis of two collections |
| `/kb changes <folder>` | What was re-indexed or revised recently |
| `/kb diff <folder> <filename>` | AI summary of the differences between a file's last two versions |
| `/kb status` | Watcher state, collection sizes, quarantined files with reasons |
| `/kb clear-quarantine <folder> <filename>` | Release one file from quarantine so it retries |
| `/kb clear-quarantine-all` | Release every quarantined file |
| `/kb eval [all \| case <id> \| task-type <type> \| collection <folder>] [judge]` | Run the benchmark suite; `judge` adds the LLM judge |
| `/kb eval-report` | Show the most recent saved evaluation summary |
| `/kb ticket "<task name>" [high\|medium\|low] [YYYY-MM-DD]` | Opens a form to review, assign and annotate — then creates the Notion ticket |

### Input validation worth knowing

The bot refuses or warns rather than returning a weak answer:

- `/kb score` and `/kb draft` require a requirement of **10+ words** — short
  requirement text produces poor scoring and drafts.
- `/kb compare` requires a question of **8+ words**, and errors if either
  collection is missing or empty.
- `/kb gaps` warns when the collection holds **fewer than 5 chunks** (too little
  content for gap analysis to mean anything).
- `/kb diff` matches the stored path exactly, so **filename case matters**, and the
  folder argument excludes the OneDrive root (`CostVolume`, not
  `ConsultingKB/CostVolume`).

### Beyond the commands

- **Threaded follow-ups.** `/kb ask` posts in-channel, so you can reply in the
  thread with a follow-up question and get a contextual answer — the bot rebuilds
  the conversation history, re-searches the same collection, and answers with the
  thread as context. It only responds in threads it started. Multi-collection
  (`all`) threads are skipped, since there's no single collection to route to.
  Prefix a thread reply with `ticket ...` to start a Notion ticket from the thread. The bot replies with an **Open ticket form** button rather than creating the task immediately, so the thread route goes through the same review step as the slash command.
- **Auto-routing.** `/kb ask "<question>"` with no folder scores the question
  against every collection and routes to the best match above a 0.35 confidence
  threshold. If the question looks comparative or multi-hop, it silently escalates
  to the agentic loop instead.
- **Hybrid retrieval.** Every search issues two Qdrant queries — a dense vector
  query and a BM25-style sparse query, each pulling twice the requested depth —
  and fuses the two ranked lists with Reciprocal Rank Fusion (`k=60`) in Python,
  in `_rrf_merge()`. Fusion is client-side, not Qdrant's server-side `Fusion.RRF`.
  If either query fails the search falls back to dense-only. Exact terms
  (contract numbers, names) and semantic matches both land.
- **Daily digest.** With `DIGEST_ENABLED=true`, a scheduled summary of KB activity
  posts to `DIGEST_CHANNEL` at `DIGEST_TIME` (UTC).

### Ingestion, versioning, resilience

- **Supported types:** `.pdf` (tables kept as table chunks), `.docx`
  (section-aware), `.md` / `.txt` (paragraph split, code blocks preserved),
  `.xlsx` / `.csv` (markdown tables, 50-row groups), `.pptx` (one chunk per slide),
  `.eml` (HTML stripped, headers kept), `.html`, and source files
  (`.py .js .ts .go .rs`, split on function and class boundaries).
- **Change detection:** sha256 at file level, then per-chunk hashing, so only
  changed chunks are re-embedded.
- **Version snapshots:** up to 5 per file for `.md`, `.txt`, `.pdf`, `.docx` — this
  is what `/kb diff` reads.
- **Quarantine:** locked files and transient failures (429/5xx, connection resets,
  timeouts) retry 3× with `[30, 120, 600]s` backoff; genuinely unreadable,
  oversized, or unsupported files quarantine immediately and appear in `/kb status`.

### Other surfaces

**REST API** (`python run_api.py`, port 8000): `GET /collections`,
`GET /collections/{name}/info`, `POST /query/ask`, `POST /query/score`,
`POST /query/gaps`, `POST /query/draft`, `POST /query/compare`, and
`POST /query/agent` which streams the agent's reasoning over SSE.

**Web UI** (`web/`, Next.js 15): six tabs — Ask (chat with source pills), Agent
(live streaming reasoning chain with stop control), Score (colour-coded score with
strengths/weaknesses grid), Gaps (hard/soft badges), Draft (document card with
inline `[EVIDENCE MISSING]` highlighting and copy-to-clipboard), Compare (parsed
comparison table).

---

## 3. Worked example — responding to a DHS 24×7 SOC solicitation

A realistic end-to-end pass over the collections above. **Every output in this
section is real**, captured on 2026-11-04 against the live OneDrive-backed
knowledge base, except the three marked *(illustrative)*. Long outputs are
truncated with `[...]`.

### Step 1 — See what's available

```
/kb list
```

```
📚 Knowledge Bases
resumes          — 10 chunks
contracts        —  8 chunks
pastperformance  —  9 chunks
technicalvolume  — 10 chunks
costvolume       —  5 chunks
```

### Step 2 — Establish the past-performance story

```
/kb ask pastperformance "What cybersecurity work have we done for DHS, and what were the outcomes?"
```

> For DHS's Office of the CISO, we delivered a full-scale cybersecurity
> modernization program under contract HSHQDC-21-C-00412, valued at $18.4M, from
> March 2021 through February 2024 (2023_DHS_CyberTransformation.md).
>
> **Scope of Work:**
> - Migrated 14 legacy security tools to a unified SIEM platform (Splunk Enterprise Security)
> - Implemented zero-trust network architecture across 6,200 endpoints
> - Deployed automated threat-hunting playbooks covering 38 MITRE ATT&CK techniques
> - Trained 142 DHS security analysts on new tooling
>
> **Outcomes:**
> - Reduced mean time to detect (MTTD) incidents by 67% — from 22 hours to 7.2 hours
> - Achieved FedRAMP High authorization for the cloud-hosted SOC environment in 11 months
> - 98% of trained analysts passed the certification exam
> - The zero-trust implementation became a model for other DHS components, according to COR Marcus Webb
>
> *Sources: 2023_DHS_CyberTransformation.md, 2021_DoD_CloudMigration.md (5 hits)*

Note the citations and the specific contract number, dollar value, period of
performance, and COR name — all pulled from the documents, not invented.

**Follow up in the thread** (no command needed):

> **You:** What was the staffing model on that one?

The bot re-searches `pastperformance` with the thread as context and answers
in-place.

### Step 3 — Score the technical volume against the actual requirement

```
/kb score technicalvolume "The contractor shall operate a 24x7 Security Operations Center providing continuous monitoring, incident response, and threat hunting across hybrid cloud environments."
```

> # Requirement Evaluation: 24x7 Security Operations Center
>
> ## COMPOSITE: 6/10 — Acceptable
>
> The knowledge base demonstrates understanding of SOC operations and proposes a
> reasonable technical architecture, but lacks verifiable past performance
> evidence, specific metrics from previous 24x7 SOC operations, and validation of
> hybrid cloud monitoring capabilities.
>
> ## CRITERIA
>
> • **24x7 Continuous Operations Coverage** — 6/10: Proposal mentions "rotating
> shifts" for 4 Tier 1/2 analysts [...] but does not demonstrate how 4 analysts
> achieve true 24x7 coverage (typically requires 5-6 FTEs minimum)
> (TechnicalApproach_CyberSOC.md)
>
> • **Continuous Monitoring Capability** — 7/10: Strong technical approach with
> Splunk SOAR auto-triaging ~60% of alerts, UEBA behavioral baselines for 8,400
> users [...] However, no CPARS or past performance citations validating these
> capabilities in production. (TechnicalApproach_CyberSOC.md)
>
> • **Incident Response** — 5/10: Three-tier SOC model described [...] but no
> explicit IR procedures, SLAs, or response time metrics provided.
> DataModernization document references "<4hr P1 incident response" but this is for
> data pipelines, not security incidents.
>
> • **Threat Hunting** — 7/10: Specific commitment to "40 hours/week proactive
> hunting" [...]
>
> • **Hybrid Cloud Environment Support** — 4/10: No explicit mention of hybrid
> cloud monitoring architecture, multi-cloud log aggregation, or cloud-native
> security tool integration.
>
> ## WEAKNESSES
>
> • No past performance evidence (CPARS, references, contract numbers)
> demonstrating successful 24x7 SOC operation at comparable scale (not found)
>
> • Staffing model of 4 rotating Tier 1/2 analysts appears insufficient for true
> 24x7 coverage without overtime or burnout risk
>
> ## TO IMPROVE
>
> Add a past performance section citing a specific 24x7 SOC contract with
> measurable outcomes (e.g., "maintained 99.7% uptime, MTTR of 23 minutes, rated
> Exceptional on CPARS #XYZ") and explicitly address hybrid cloud monitoring
> architecture [...]

The score decomposes the requirement into criteria, scores each, cites the file
behind every judgement, and writes `(not found)` where the KB has nothing — it
does not pad a score with content that isn't there.

### Step 4 — Find what's missing in the cost volume

```
/kb gaps costvolume "cost realism and indirect rate justification"
```

> **Hard Gaps**
>
> • No indirect rate substantiation documentation: Need Forward Pricing Rate
> Agreement (FPRA) or DCAA-approved rate documentation showing how 42% overhead and
> 8% G&A were derived and validated
>
> • No cost realism methodology narrative: Need documentation explaining basis of
> estimates, labor hour calculations, and how proposed costs align with the
> solicitation's PWS requirements
>
> • No historical cost performance data [...]
>
> • No subcontractor/vendor cost documentation: Need Splunk licensing quotes,
> sole-source justifications, or competitive pricing evidence for the $680K
> pass-through
>
> **Soft Gaps**
>
> • Labor rate basis is asserted but not justified: GSA Schedule rates mentioned as
> benchmark but no actual GSA Schedule documentation, LCAT mappings, or market
> survey data provided
>
> • Competitive intelligence lacks sourcing: Competitor cost advantage ($400K) and
> win price target ($4.2M-$4.8M) stated without underlying analysis [...]
>
> • Risk associated with discount strategy not addressed [...]
>
> • No escalation factors for option years [...]
>
> **Priority**
>
> Obtain FPRA or develop a DCAA-compliant indirect rate package immediately —
> without auditable rate substantiation, the entire cost volume lacks credibility
> and invites government challenge during cost realism evaluation.

Hard gaps are absent entirely; soft gaps exist but are too thin to rely on. The
rates it quotes (42% overhead, 8% G&A, $680K pass-through) come from the single
`PricingStrategy_Notes.md` file.

### Step 5 — Draft the narrative

```
/kb draft pastperformance "Describe the offeror's demonstrated experience migrating federal workloads to cloud environments, including contract values, periods of performance, and customer outcomes."
```

> ## Demonstrated Experience Migrating Federal Workloads to Cloud Environments
>
> Our organization brings extensive, proven experience executing complex federal
> cloud migration initiatives across multiple defense and civilian agencies [...]
> as evidenced by our successful performance on contracts totaling over $57 million
> in value across the Department of Defense, Department of Homeland Security, and
> Department of Veterans Affairs.
>
> Our DoD DISA Cloud Migration contract (HC1047-20-F-0082), valued at $7.6 million
> under a firm-fixed-price arrangement, exemplifies our migration expertise at
> scale. Executed from January 2021 through December 2022 under the leadership of
> Program Manager Lt. Col. (Ret.) Brian Torres, PMP, we migrated 47 mission-critical
> DoD applications from on-premises DISA data centers to Azure Government cloud. We
> achieved Impact Level 4 and Impact Level 5 authorization for 12 of those
> applications within six months of migration [...] DISA realized $2.1 million in
> annualized infrastructure cost savings post-migration [...] As Col. Sandra Park,
> our Contracting Officer's Representative, noted: "Best-in-class migration team.
> Their documentation and knowledge transfer left our team fully self-sufficient."
>
> [...two further paragraphs covering the DHS and VA contracts...]
>
> Coverage: Contract values, periods of performance, and customer outcomes fully
> supported with specific metrics from DISA ($7.6M), DHS ($18.4M), and VA
> ($31.2M/$9.8M) contracts | Flagged: none

Everything is drawn from the indexed documents — contract numbers, COR quotes,
dollar figures. Where the KB can't support a claim the requirement demands, the
draft inserts `[EVIDENCE MISSING: ...]` inline instead of inventing it, and the
closing `Coverage:` line tallies what was supported versus flagged.

### Step 6 — Check the two volumes tell a consistent story

```
/kb compare pastperformance technicalvolume "What does each collection tell us about our cybersecurity capability?"
```

> DIRECT ANSWER
> PastPerformance demonstrates proven, measurable results from completed federal
> cybersecurity engagements, while TechnicalVolume articulates the proposed
> methodologies and forward-looking capabilities we would deploy on new
> opportunities.
>
> | Dimension | PastPerformance | TechnicalVolume |
> | --- | --- | --- |
> | SIEM/Platform Expertise | Migrated 14 legacy tools to unified Splunk ES at DHS; 67% MTTD reduction | Proposes same Splunk ES architecture with SOAR targeting 60% auto-triage |
> | Zero-Trust Implementation | Deployed across 6,200 DHS endpoints; "model for other DHS components" per COR | No explicit zero-trust methodology documented |
> | FedRAMP/Authorization | FedRAMP High in 11 months (DHS); IL4/IL5 for 12 DoD apps in 6 months | Identifies FedRAMP timeline slip as risk; mitigates via existing package |
> | Analyst Training | Trained 142 DHS analysts; 98% certification pass rate | Plans gamified training portal with champion program |
>
> *[table truncated — full output has 6 dimensions]*
>
> **Divergences**
> • Zero-trust capability is proven but not productized: DHS COR called our
> implementation a "model," yet TechnicalVolume contains no zero-trust methodology
> or offering — a gap given current federal mandates
>
> • Threat hunting approach has evolved: PastPerformance emphasizes automated
> playbooks (38 ATT&CK techniques), while TechnicalVolume shifts toward
> human-driven proactive hunting (40 hrs/week) — reflects maturation but creates
> messaging inconsistency
>
> **Bottom Line**
> Our cybersecurity capability is strongest when both collections are presented
> together [...] The most compelling win theme is Splunk-centric SOC modernization
> with ML augmentation. However, we should either add zero-trust methodology to our
> technical collateral or explicitly reference the DHS implementation as a reusable
> framework, since this proven capability is currently invisible in proposals.

This is the step that catches positioning problems: a proven zero-trust
implementation that appears nowhere in the technical collateral.

### Step 7 — Act on Step 4 and edit the cost volume

You revise `PricingStrategy_Notes.md` in OneDrive. Within one poll interval the
watcher re-ingests it, re-embeds the changed chunks, and stores a version snapshot.

```
/kb changes costvolume
```

> **Indexing activity for 'costvolume' (past 3 days)**
>
> - **PricingStrategy_Notes.md** — revised and re-indexed on 2026-11-04 at 16:17
>   UTC; contains 5 chunks across 2 version snapshots

```
/kb diff CostVolume PricingStrategy_Notes.md
```

> 🔍 Document Diff — PricingStrategy_Notes.md
>
> **Summary of Changes**
>
> Modifications:
> - Overhead calculation on labor increased from $2,228,147 to $3,228,147 (a
>   $1,000,000 increase)
>
> Additions:
> - New line item added: "Status = Good" under the Overhead & Profit section

### Step 8 — Turn the gap into a tracked task

```
/kb ticket "Obtain FPRA or build DCAA-compliant indirect rate package" high 2026-11-18
```

Running this **opens a form** rather than creating the task straight away:

| Field | Behaviour |
|---|---|
| Task name | Prefilled from the command; editable |
| Priority | Prefilled; High / Medium / Low |
| Due date | Prefilled; date picker; optional |
| Assignee | Optional. Lists real Notion users and sets the `Assignee` property. If the integration cannot list users, this becomes a free-text **Requested owner** field that is clearly *not* a Notion assignment. |
| Additional notes | Optional multiline text, saved to the page body under an **Additional Notes** heading |

**Create Ticket** writes to Notion and posts a confirmation with the task name, priority, due date,
assignee and a working **Open in Notion** link. **Cancel** creates nothing. The confirmation only
appears after Notion confirms creation, and a failure is reported without retrying — so a failed
attempt never leaves a duplicate task behind.

Both straight (`"`) and curly (`“ ”`) quotation marks work. If the command is malformed — an
unterminated quote, or an argument it cannot interpret — it reports the problem instead of creating a
ticket with a mangled title.

> 🎫 Ticket Created
> ✅ **Obtain FPRA or build DCAA-compliant indirect rate package** created
> (Priority: High), due 2026-11-18
> *Open in Notion*

The returned link opens the new page in the Notion Tasks Tracker.

### Also available during the same pass *(illustrative)*

```
/kb agent Which of our past performance contracts best supports a 24x7 SOC requirement, and what's missing from the technical volume?
```

The agentic loop posts its work to the channel as it goes — up to three rounds of
reasoning (`🔍`), tool calls (`⚙️`, from `list_collections`, `query_collection`,
`search_all_collections`, `get_collection_info`), and result summaries (`📄`) —
then a synthesised answer. Use it when a question spans collections; `/kb ask`
with no folder escalates to it automatically when the question looks multi-hop.

```
/kb ask all "What Splunk experience do we have?"
```

Queries all five collections and synthesises one answer, grouping sources by
collection — useful when you don't know which volume holds the evidence.

```
/kb status
```

> ⚙️ KB Agent Status
> **Watcher:** running
> **Collections:** 5 active
> `contracts` — 8 chunks · `costvolume` — 5 chunks · `pastperformance` — 9 chunks ·
> `resumes` — 10 chunks · `technicalvolume` — 10 chunks
> **Quarantined Files:** 0
> ✅ No quarantined files

If a file does appear here, `/kb clear-quarantine CostVolume PricingStrategy_Notes.md`
releases it for another attempt.

---

## 4. Evaluation

The agent benchmarks itself. `/kb eval` runs `evals/test_cases.yaml` against the
live KB and scores retrieval quality, fact coverage, and format compliance,
optionally adding an LLM judge (`/kb eval ... judge`). Scope it with
`case <id>`, `task-type <type>`, or `collection <folder>`. Results are written to
`evals/results/` as JSON and `evals/reports/` as Markdown; `/kb eval-report` shows
the latest, and `evals/compare_runs.py` diffs runs to catch regressions.

---

### Fidelity note

Outputs in Section 3 are verbatim captures from the live knowledge base on
2026-11-04 (`BACKEND=onedrive`, Qdrant on `localhost:6333`, `claude-opus-4-5`),
truncated where marked. The `/kb diff` output is the real Slack response from that
day's test. The three blocks marked *(illustrative)* — `/kb agent`, `/kb ask all`,
`/kb status` — plus the `/kb ticket` confirmation are rendered from the handler
code rather than captured, so wording may differ slightly in the channel.
