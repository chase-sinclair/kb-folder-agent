# AGENTS.md

**Project instructions for this repo live in [`CLAUDE.md`](./CLAUDE.md). Read that
file first — it is the single source of truth.**

This file used to be a full copy of those instructions and drifted out of date
(stale model reference, the pre-sha256 point-ID formula, missing V6/V7 phases, a
retrieval description that did not match the code). It is deliberately a pointer
now so that cannot happen again. Do not reintroduce the stack, project structure,
environment variables, command table, rules or phase history here — change
`CLAUDE.md` instead.

## Where things are

| File | What it is |
|------|------------|
| `CLAUDE.md` | **Source of truth.** Stack, structure, env vars, architecture, chunking, DB schema, `/kb` command table, rules, phase history, known fixes. |
| `FEATURES.md` | User-facing `/kb` command guide with a worked end-to-end example. |
| `CODEX.md` | Chronological handoff log for the Evaluation Center workstream. Append-only notes, not instructions. |
| `README.md` | Setup guide. **UTF-16LE, no BOM** — patch with an explicit encoding or not at all. |
| `evals/README.md` | How to run the benchmark suite. |

## Non-negotiables (full set in `CLAUDE.md`)

- **Never read, print or modify `.env`.**
- Collection names must come from `re.sub(r"[^a-z0-9]+", "_", folder.lower())`.
- Qdrant point IDs must come from `point_id()` in `mcp_servers/vectordb_server.py`
  (sha256). Never Python `hash()` — it is salted per process.
- All DB access goes through `storage/db.py`; quarantine logic lives only in
  `ingestion/quarantine.py`; MCP calls are orchestrated only through
  `agent/orchestrator.py`.
- Do not add dependencies without asking first.
- This project runs on Windows. Use raw strings for paths.
