import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp

from agent.orchestrator import (
    INFERENCE_CONFIDENCE_THRESHOLD,
    collection_exists,
    folder_to_collection_name,
    get_available_collections,
    get_folder_list,
    infer_collection,
)
from agent import orchestrator as _orch
from agent.agent_loop import run_agent
from agent.rag import answer_query, answer_query_all, answer_with_history, compare_collections, draft_section, find_gaps, score_requirement, summarize_diff, summarize_recent_changes
from integrations.notion import create_ticket, list_assignable_users
from evals.config import REPORTS_DIR, RESULTS_DIR
from evals.report import write_json_report, write_markdown_report
from evals.runner import run_evaluations
from evals.schema import EvalRunConfig
from ingestion.quarantine import (
    clear_all_quarantine,
    clear_quarantine,
    find_quarantined_path,
    get_quarantined_files,
)
from storage.db import init_db

load_dotenv()

log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

app = AsyncApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

_bot_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/")


def _format_table_for_slack(text: str) -> str:
    """Convert markdown pipe tables to monospace code blocks."""
    table_pattern = re.compile(
        r"((?:^\|.+\|\n)+)",
        re.MULTILINE,
    )

    def replace_table(match: re.Match) -> str:
        raw = match.group(1).rstrip("\n")
        rows = [row for row in raw.splitlines() if not re.match(r"^\|[-| :]+\|$", row)]
        cells = [[c.strip() for c in row.strip("|").split("|")] for row in rows]
        if not cells:
            return match.group(0)
        col_widths = [max(len(r[i]) if i < len(r) else 0 for r in cells) for i in range(len(cells[0]))]
        lines = ["  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) for row in cells]
        return "```\n" + "\n".join(lines) + "\n```"

    return table_pattern.sub(replace_table, text)


def clean_for_slack(text: str) -> str:
    text = _format_table_for_slack(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_QUOTE_CHARS = "\"'“”‘’«»"
_SMART_QUOTE_MAP = str.maketrans({
    "“": '"', "”": '"',   # curly double quotes
    "‘": "'", "’": "'",   # curly single quotes
    "«": '"', "»": '"',   # guillemets
})


def _normalize_quotes(text: str) -> str:
    """Fold typographic quotes to straight ASCII quotes.

    Slack clients (iOS/macOS autocorrect in particular) substitute curly quotes as
    you type, so a command that looks correct on screen arrives with U+201C/U+201D.
    Every quote-aware parser here normalises first, otherwise a quoted argument
    silently falls through to a whole-string fallback.
    """
    return (text or "").translate(_SMART_QUOTE_MAP)


def _parse_command(text: str) -> tuple[str, str, str]:
    """Returns (subcommand, folder_name, query). All fields may be empty strings."""
    text = _normalize_quotes(text or "").strip()
    if not text:
        return "", "", ""

    parts = text.split(None, 1)
    subcommand = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if subcommand not in {"ask", "clear-quarantine", "changes", "compare", "diff", "draft", "eval", "eval-report", "gaps", "score"}:
        return subcommand, "", rest

    # If rest starts with a quote, there's no folder — entire rest is the query
    if subcommand == "ask" and rest.startswith(('"', "'")):
        return subcommand, "", re.sub(r'^["\']|["\']$', "", rest)

    rest_parts = rest.split(None, 1)
    folder = rest_parts[0] if rest_parts else ""
    remainder = rest_parts[1].strip() if len(rest_parts) > 1 else ""
    query = re.sub(r'^["\']|["\']$', "", remainder)
    return subcommand, folder, query


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _find_latest_eval_results() -> Path | None:
    paths = sorted(RESULTS_DIR.glob("eval_results_*.json"))
    return paths[-1] if paths else None


def _parse_eval_request(text: str) -> tuple[EvalRunConfig | None, str | None]:
    request = (text or "").strip()
    use_judge = False
    rerank: bool | None = None
    tokens = [token for token in request.split() if token]

    normalized_tokens: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered == "judge":
            use_judge = True
        elif lowered == "rerank":
            rerank = True
        elif lowered in {"no-rerank", "norerank"}:
            rerank = False
        else:
            normalized_tokens.append(token)

    output_slug = _timestamp_slug()
    config = EvalRunConfig(
        cases_path="evals/test_cases.yaml",
        output_markdown=str(REPORTS_DIR / f"eval_report_{output_slug}.md"),
        output_json=str(RESULTS_DIR / f"eval_results_{output_slug}.json"),
        use_judge=use_judge,
        rerank=rerank,
    )

    if not normalized_tokens or normalized_tokens[0].lower() == "all":
        return config, None

    if len(normalized_tokens) >= 2 and normalized_tokens[0].lower() == "case":
        config.case_id = normalized_tokens[1]
        return config, None

    if len(normalized_tokens) >= 2 and normalized_tokens[0].lower() == "task-type":
        config.task_type = normalized_tokens[1]
        return config, None

    if len(normalized_tokens) >= 2 and normalized_tokens[0].lower() == "collection":
        config.collection = normalized_tokens[1]
        return config, None

    usage = (
        "Usage: `/kb eval [all] [judge] [rerank|no-rerank]`, `/kb eval case <id> [judge]`, "
        "`/kb eval task-type <type> [judge]`, or `/kb eval collection <FolderName> [judge]`. "
        "`rerank` / `no-rerank` force the LLM reranker on or off for that run only."
    )
    return None, usage


def _rerank_label(summary_data: dict) -> str:
    """Tolerates reports written before rerank tracking existed (key absent)."""
    enabled = summary_data.get("rerank_enabled")
    if enabled is None:
        return "unknown"
    if not enabled:
        return "off"
    model = summary_data.get("rerank_model")
    return f"on ({model})" if model else "on"


def _build_eval_summary_blocks(summary, header_text: str, *, latest: bool = False) -> list[dict]:
    if hasattr(summary, "case_results"):
        case_results = [
            {
                "id": case.id,
                "status": case.status,
                "warnings": case.warnings,
                "failures": case.failures,
            }
            for case in summary.case_results
        ]
        summary_data = {
            "total_cases": summary.total_cases,
            "passed": summary.passed,
            "warnings": summary.warnings,
            "failed": summary.failed,
            "overall_score": summary.overall_score,
            "retrieval_quality": summary.retrieval_quality,
            "expected_fact_coverage": summary.expected_fact_coverage,
            "format_compliance": summary.format_compliance,
            "citation_presence": summary.citation_presence,
            "judge_enabled": summary.judge_enabled,
            "rerank_enabled": summary.rerank_enabled,
            "rerank_model": summary.rerank_model,
            "hallucination_risk": summary.hallucination_risk,
            "recommendations": summary.recommendations,
            "output_markdown": summary.output_markdown,
            "case_results": case_results,
        }
    else:
        summary_data = summary

    blocks = [
        _header(header_text),
        _divider(),
        _section(
            f"*Cases:* {summary_data['total_cases']}   *PASS:* {summary_data['passed']}   "
            f"*WARN:* {summary_data['warnings']}   *FAIL:* {summary_data['failed']}"
        ),
        _section(
            f"*Overall:* {summary_data['overall_score']:.0%}   "
            f"*Retrieval:* {summary_data['retrieval_quality']:.0%}   "
            f"*Facts:* {summary_data['expected_fact_coverage']:.0%}   "
            f"*Format:* {summary_data['format_compliance']:.0%}"
            + (
                f"   *Citations:* {summary_data['citation_presence']:.0%}"
                if summary_data.get("citation_presence") is not None
                else ""
            )
        ),
        _context(
            f"Judge enabled: {'yes' if summary_data['judge_enabled'] else 'no'}"
            + f" | Rerank: {_rerank_label(summary_data)}"
            + (f" | Hallucination risk: {summary_data['hallucination_risk']}" if summary_data.get("hallucination_risk") else "")
        ),
    ]

    warning_cases = [case for case in summary_data.get("case_results", []) if case.get("status") == "WARN"][:3]
    failed_cases = [case for case in summary_data.get("case_results", []) if case.get("status") == "FAIL"][:3]

    if failed_cases:
        lines = [f"• `{case['id']}` — {case['failures'][0] if case.get('failures') else 'Failed'}" for case in failed_cases]
        blocks.extend([_divider(), _section("*Failed Cases*\n" + "\n".join(lines))])

    if warning_cases:
        lines = [f"• `{case['id']}` — {case['warnings'][0] if case.get('warnings') else 'Warning'}" for case in warning_cases]
        blocks.extend([_divider(), _section("*Warning Cases*\n" + "\n".join(lines))])

    recommendations = summary_data.get("recommendations", [])[:2]
    if recommendations:
        blocks.extend([_divider(), _section("*Recommendations*\n" + "\n".join(f"• {item}" for item in recommendations))])

    report_name = Path(summary_data["output_markdown"]).name if summary_data.get("output_markdown") else "n/a"
    if latest:
        blocks.append(_divider())
        blocks.append(_context(f"Latest saved report: `{report_name}`"))
    return blocks


async def _post_eval_completion(client, channel_id: str, user_id: str, summary) -> None:
    await client.chat_postEphemeral(
        channel=channel_id,
        user=user_id,
        text="Evaluation run complete.",
        blocks=_build_eval_summary_blocks(summary, "🧪 Evaluation Complete"),
    )


async def _run_eval_in_background(client, channel_id: str, user_id: str, config: EvalRunConfig) -> None:
    try:
        summary = await run_evaluations(config)
        write_markdown_report(summary, config.output_markdown)
        write_json_report(summary, config.output_json)
        await _post_eval_completion(client, channel_id, user_id, summary)
    except Exception as exc:
        log.error("Background eval run failed: %s", exc)
        await client.chat_postEphemeral(
            channel=channel_id,
            user=user_id,
            text=f"Evaluation failed: {exc}",
            blocks=[_section(f"Evaluation failed: {exc}")],
        )


async def _handle_eval(request: str, client, channel_id: str, user_id: str) -> tuple[str, list] | None:
    config, usage_error = _parse_eval_request(request)
    if usage_error:
        return _error_blocks(usage_error)

    await client.chat_postEphemeral(
        channel=channel_id,
        user=user_id,
        text="Starting evaluation run.",
        blocks=[
            _section("Starting evaluation run in the background. I’ll post a summary here when it completes."),
            _context(
                f"Scope: case={config.case_id or 'all'} | task-type={config.task_type or 'all'} | "
                f"collection={config.collection or 'all'} | judge={'on' if config.use_judge else 'off'} | "
                f"rerank={'env default' if config.rerank is None else ('on' if config.rerank else 'off')}"
            ),
        ],
    )
    asyncio.create_task(_run_eval_in_background(client, channel_id, user_id, config))
    return None


async def _handle_eval_report() -> tuple[str, list]:
    latest = _find_latest_eval_results()
    if latest is None:
        return _error_blocks("No saved evaluation results found yet. Run `/kb eval` first.")

    summary = json.loads(latest.read_text(encoding="utf-8"))
    blocks = _build_eval_summary_blocks(summary, "📊 Latest Evaluation Report", latest=True)
    return "📊 Latest Evaluation Report", blocks


# ---------------------------------------------------------------------------
# Block Kit builders
# ---------------------------------------------------------------------------

def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text}}

def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

def _divider() -> dict:
    return {"type": "divider"}

def _error_blocks(message: str) -> tuple[str, list]:
    return message, [_section(message)]


# Slack rejects a section block whose text exceeds 3000 chars, and any message
# carrying more than 50 blocks. Model answers routinely cross the first limit.
SECTION_TEXT_LIMIT = 2800
MAX_BLOCKS = 50


def _hard_split(piece: str, limit: int) -> list[str]:
    return [piece[i:i + limit] for i in range(0, len(piece), limit)]


def _split_text(text: str, limit: int = SECTION_TEXT_LIMIT) -> list[str]:
    """Break text into <=limit pieces, preferring paragraph then line boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    para_sep = "\n\n"
    units: list[str] = []
    for para in text.split(para_sep):
        if len(para) <= limit:
            units.append(para)
            continue
        buf = ""
        for line in para.split("\n"):
            candidate = line if not buf else buf + "\n" + line
            if len(candidate) <= limit:
                buf = candidate
                continue
            if buf:
                units.append(buf)
                buf = ""
            if len(line) <= limit:
                buf = line
            else:
                units.extend(_hard_split(line, limit))
        if buf:
            units.append(buf)

    chunks: list[str] = []
    buf = ""
    for unit in units:
        candidate = unit if not buf else buf + para_sep + unit
        if len(candidate) <= limit:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = unit
    if buf:
        chunks.append(buf)
    return chunks


def _text_sections(text: str) -> list[dict]:
    """Render model output as section blocks Slack will accept."""
    return [_section(chunk) for chunk in _split_text(clean_for_slack(text))]


def _cap_blocks(blocks: list[dict]) -> list[dict]:
    if len(blocks) <= MAX_BLOCKS:
        return blocks
    kept = blocks[:MAX_BLOCKS - 1]
    kept.append(_context("Response truncated to fit Slack's 50-block limit."))
    return kept


def _friendly_error(exc: Exception) -> str:
    """User-facing error text. Raw Slack API payloads are logged, never shown."""
    name = type(exc).__name__
    text = str(exc)
    if "invalid_blocks" in text or "SlackApiError" in name:
        return "I could not format that response for Slack. Details are in the server log."
    detail = text.splitlines()[0][:160] if text else ""
    suffix = f" ({detail})" if detail else ""
    return f"An error occurred{suffix}. Try `/kb` for usage help."


async def _safe_post(sender, text: str, blocks: list[dict], **kwargs):
    """Post blocks, falling back to plain text if Slack rejects the Block Kit payload."""
    try:
        return await sender(text=text, blocks=_cap_blocks(blocks), **kwargs)
    except Exception as exc:
        log.error("Block post failed, retrying as plain text: %s", exc)
        plain = text if len(text) <= 2900 else text[:2900] + "\n... (truncated)"
        try:
            return await sender(text=plain, **kwargs)
        except Exception as fallback_exc:
            log.error("Plain-text fallback also failed: %s", fallback_exc)
            return None


def _kb_root() -> str:
    """Root prefix that stored file_paths carry, for the active backend."""
    if os.environ.get("BACKEND", "local") == "onedrive":
        return normalize_path(os.environ.get("ONEDRIVE_FOLDER", "test-kb")).rstrip("/")
    from ingestion.watcher import WATCHED_FOLDER

    return normalize_path(str(WATCHED_FOLDER)).rstrip("/")


def _kb_file_path(folder_name: str, filename: str) -> str:
    return f"{_kb_root()}/{folder_name}/{filename}"


# ---------------------------------------------------------------------------
# Response formatters — return (fallback_text, blocks)
# ---------------------------------------------------------------------------

async def _handle_list() -> tuple[str, list]:
    # Independent calls — one hits the backend (Graph API or disk), the other
    # Qdrant — so run them together rather than back to back.
    folders, collections = await asyncio.gather(
        get_folder_list(), get_available_collections()
    )
    counts = {c["name"]: c.get("vector_count", 0) for c in collections}

    if not folders:
        return _error_blocks("No knowledge base folders found.")

    blocks = [_header("📚 Knowledge Bases"), _divider()]
    for f in folders:
        col = f["collection_name"]
        count = counts.get(col, 0)
        blocks.append(_section(f"*{f['name']}* → `{col}` — {count} chunks"))
    blocks.append(_divider())
    blocks.append(_context('Use `/kb ask <folder> "question"` to query'))
    return "📚 Knowledge Bases", blocks


async def _handle_ask(folder_name: str, query: str) -> tuple[str, list]:
    if not query and not folder_name:
        return _error_blocks("Usage: `/kb ask <FolderName> \"<question>\"`")

    # Auto-routing: no folder name supplied
    inferred_label = None
    if not folder_name:
        inferred = await infer_collection(query)
        if inferred["collection_name"] is None:
            msg = (
                "🤔 I couldn't determine which knowledge base to search.\n"
                "Use `/kb ask <folder> \"question\"` or `/kb list` to see available folders."
            )
            return msg, [_section(msg)]
        collection_name = inferred["collection_name"]
        folder_name = collection_name
        inferred_label = f"🔍 Auto-routed to: *{collection_name}* (confidence: {inferred['confidence']:.2f})"
    else:
        if not query:
            return _error_blocks(f"Usage: `/kb ask {folder_name} \"<question>\"`")

        if folder_name.lower() == "all":
            result = await answer_query_all(query)
            blocks = [_header("💬 All Knowledge Bases"), _divider(), *_text_sections(result["answer"])]
            return "💬 All Knowledge Bases", blocks

        collection_name = folder_to_collection_name(folder_name)
        if not await collection_exists(collection_name):
            msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
            return _error_blocks(msg)

    result = await answer_query(collection_name, query)
    blocks = [
        _header(f"💬 {folder_name}"),
        _divider(),
        *_text_sections(result.answer),
    ]
    if inferred_label:
        blocks.append(_divider())
        blocks.append(_context(inferred_label))
    return f"💬 {folder_name}", blocks


async def _handle_changes(folder_name: str) -> tuple[str, list]:
    if not folder_name:
        return _error_blocks("Usage: `/kb changes <FolderName>`")

    collection_name = folder_to_collection_name(folder_name)
    if not await collection_exists(collection_name):
        msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
        return _error_blocks(msg)

    result = await summarize_recent_changes(collection_name)
    if not result.result_count:
        return _error_blocks(f"No recent changes found in `{folder_name}`.")

    blocks = [
        _header(f"🕐 Recent Changes — {folder_name}"),
        _divider(),
        *_text_sections(result.answer),
        _divider(),
        _context(f"📄 Sources: {', '.join(result.sources)}" if result.sources else "📄 No sources"),
    ]
    return f"🕐 Recent Changes — {folder_name}", blocks


async def _handle_status() -> tuple[str, list]:
    collections = await get_available_collections()
    quarantined = await get_quarantined_files()

    blocks = [
        _header("⚙️ KB Agent Status"),
        _divider(),
        _section("*Watcher:* running"),
        _section(f"*Collections:* {len(collections)} active"),
    ]
    for c in collections:
        blocks.append(_context(f"`{c['name']}` — {c.get('vector_count', 0)} chunks"))

    blocks.append(_divider())
    blocks.append(_section(f"*Quarantined Files:* {len(quarantined)}"))
    if quarantined:
        for q in quarantined:
            reason = (q.get("error_message") or "").strip().splitlines()
            detail = f" - _{reason[0][:140]}_" if reason else ""
            blocks.append(
                _context(f"⚠️ `{normalize_path(q['file_path'])}` — {q['error_type']}{detail}")
            )
    else:
        blocks.append(_context("✅ No quarantined files"))

    return "⚙️ KB Agent Status", blocks


async def _handle_clear_quarantine(folder_name: str, filename: str) -> tuple[str, list]:
    if not folder_name or not filename:
        return _error_blocks("Usage: `/kb clear-quarantine <FolderName> <filename>`")

    cleared = await clear_quarantine(_kb_file_path(folder_name, filename))
    if not cleared:
        # Stored paths carry the active backend's root prefix, and the folder the
        # user typed may differ in case, so fall back to matching on the tail.
        resolved = await find_quarantined_path(folder_name, filename)
        if resolved:
            cleared = await clear_quarantine(resolved)

    if not cleared:
        return _error_blocks(
            f"No quarantined entry found for `{filename}` in `{folder_name}`. "
            "Check `/kb status` for the exact folder and filename."
        )

    msg = f"✅ Cleared quarantine for `{filename}` in `{folder_name}`."
    return msg, [_section(msg)]


async def _handle_clear_quarantine_all() -> tuple[str, list]:
    count = await clear_all_quarantine()
    msg = f"✅ Cleared all quarantine entries ({count} file(s)). Restart the agent to re-ingest."
    return msg, [_section(msg)]


async def _handle_diff(folder_name: str, filename: str) -> tuple[str, list]:
    if not folder_name or not filename:
        return _error_blocks("Usage: `/kb diff <FolderName> <filename>`")

    file_path = _kb_file_path(folder_name, filename)
    result = await summarize_diff(file_path)
    blocks = [
        _header(f"🔍 Document Diff — {filename}"),
        _divider(),
        _section(clean_for_slack(result["answer"])),
        _divider(),
        _context(f"Comparing last 2 versions of {filename}"),
    ]
    return f"🔍 Document Diff — {filename}", blocks


async def _handle_draft(folder_name: str, requirement: str) -> tuple[str, list]:
    if not folder_name or not requirement:
        return _error_blocks('Usage: `/kb draft <FolderName> "<RFP requirement>"`')

    if len(requirement.split()) < 10:
        return _error_blocks("Please provide the full requirement text — short inputs produce poor drafts.")

    collection_name = folder_to_collection_name(folder_name)
    if not await collection_exists(collection_name):
        msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
        return _error_blocks(msg)

    result = await draft_section(collection_name, requirement)

    if not result.result_count:
        return _error_blocks(result.answer)

    # Separate Coverage line from the draft body
    coverage_line = ""
    body_lines = []
    for line in result.answer.splitlines():
        if line.startswith("Coverage:"):
            coverage_line = line
        else:
            body_lines.append(line)
    body_text = "\n".join(body_lines).strip()

    blocks = [
        _header(f"✍️ Draft — {folder_name}"),
        _divider(),
        _section(f"*Requirement:* {requirement}"),
        _divider(),
    ]

    blocks.extend(_text_sections(body_text))

    blocks.append(_divider())
    if coverage_line:
        blocks.append(_context(f"📋 {coverage_line}"))
    blocks.append(_context(
        f"📄 Drafted from {result.result_count} chunk(s) | Sources: {', '.join(result.sources)}"
    ))

    return f"✍️ Draft — {folder_name}", blocks


async def _handle_compare(folder1: str, rest: str) -> tuple[str, list]:
    rest_parts = rest.split(None, 1)
    folder2 = rest_parts[0] if rest_parts else ""
    question = re.sub(r'^["\']|["\']$', "", rest_parts[1].strip()) if len(rest_parts) > 1 else ""

    if not folder2 or not question:
        return _error_blocks('Usage: `/kb compare <Folder1> <Folder2> "<question>"`')

    col1 = folder_to_collection_name(folder1)
    col2 = folder_to_collection_name(folder2)

    if not await collection_exists(col1):
        return _error_blocks(f"No knowledge base found for `{folder1}`. Use `/kb list` to see available folders.")
    if not await collection_exists(col2):
        return _error_blocks(f"No knowledge base found for `{folder2}`. Use `/kb list` to see available folders.")

    result = await compare_collections(col1, col2, folder1, folder2, question)

    if "error" in result:
        return _error_blocks(result["error"])

    answer = result["answer"]

    # Extract DIRECT ANSWER for prominent display; show the rest as the body
    direct_answer = ""
    da_match = re.search(r"DIRECT ANSWER\s*\n(.+?)(?=\n\s*\n|\n\*\*|\Z)", answer, re.DOTALL)
    if da_match:
        direct_answer = da_match.group(1).strip()
    body_text = re.sub(r"DIRECT ANSWER\s*\n.+?(?=\n\s*\n|\n\*\*|\Z)", "", answer, flags=re.DOTALL).strip()

    blocks = [
        _header(f"🔀 Compare — {folder1} vs {folder2}"),
        _divider(),
        _section(f"*Question:* {question}"),
    ]
    if direct_answer:
        blocks.append(_section(f"_{direct_answer}_"))
    blocks.append(_divider())
    blocks.extend(_text_sections(body_text))

    overlap = result.get("overlap_files", [])
    if overlap:
        blocks.append(_context(f"⚠️ Overlap: {', '.join(overlap)} appears in both collections"))

    blocks.append(_divider())
    src_a = result["sources_a"]
    src_b = result["sources_b"]
    blocks.append(_context(
        f"📄 *{folder1}:* {', '.join(src_a) or 'none'}   |   *{folder2}:* {', '.join(src_b) or 'none'}"
    ))

    return f"🔀 Compare — {folder1} vs {folder2}", blocks


async def _handle_score(folder_name: str, requirement: str) -> tuple[str, list]:
    if not folder_name or not requirement:
        return _error_blocks('Usage: `/kb score <FolderName> "<RFP requirement>"`')

    collection_name = folder_to_collection_name(folder_name)
    if not await collection_exists(collection_name):
        msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
        return _error_blocks(msg)

    result = await score_requirement(collection_name, requirement)

    if not result.result_count:
        return _error_blocks(result.answer)

    # Pull out the COMPOSITE line for prominent display; show the rest as the breakdown
    composite_line = ""
    body_lines = []
    for line in result.answer.splitlines():
        if line.startswith("COMPOSITE:"):
            composite_line = line.replace("COMPOSITE:", "").strip()
        else:
            body_lines.append(line)
    body_text = "\n".join(body_lines).strip()

    blocks = [
        _header(f"⭐ Score — {folder_name}"),
        _divider(),
        _section(f"*Requirement:* {requirement}"),
    ]
    if composite_line:
        blocks.append(_section(f"*{composite_line}*"))
    blocks.append(_divider())
    blocks.extend(_text_sections(body_text))
    blocks.append(_divider())
    blocks.append(_context(f"📄 Evaluated against {result.result_count} chunk(s) from {len(result.sources)} source(s)"))

    return f"⭐ Score — {folder_name}", blocks


async def _handle_gaps(folder_name: str, topic: str) -> tuple[str, list]:
    if not folder_name or not topic:
        return _error_blocks('Usage: `/kb gaps <FolderName> "<topic>"`')

    collection_name = folder_to_collection_name(folder_name)
    if not await collection_exists(collection_name):
        msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
        return _error_blocks(msg)

    result = await find_gaps(collection_name, topic)

    if result.result_count < 5:
        return _error_blocks(result.answer)

    blocks = [
        _header(f"🔍 Gaps — {folder_name} vs \"{topic}\""),
        _divider(),
        *_text_sections(result.answer),
        _divider(),
        _context(f"📄 Analyzed {result.result_count} chunk(s) from {len(result.sources)} source(s)"),
    ]
    return f"🔍 Gaps — {folder_name} vs \"{topic}\"", blocks


def _help_blocks() -> tuple[str, list]:
    text = (
        "*KB Agent Commands*\n"
        "• `/kb list` — Show all knowledge base collections\n"
        "• `/kb ask <FolderName> \"<question>\"` — Ask a question\n"
        "• `/kb agent <question>` — Multi-step agentic query with live reasoning\n"
        "• `/kb changes <FolderName>` — Summarize recent changes\n"
        "• `/kb status` — Watcher status and quarantined files\n"
        "• `/kb diff <FolderName> <filename>` — Summarize changes between last 2 versions\n"
        "• `/kb draft <FolderName> \"<RFP requirement>\"` — Draft a proposal narrative section from KB content\n"
        "• `/kb compare <Folder1> <Folder2> \"<question>\"` — Side-by-side comparative analysis of two collections\n"
        "• `/kb score <FolderName> \"<RFP requirement>\"` — Score KB readiness against a requirement (1–10)\n"
        "• `/kb gaps <FolderName> \"<topic>\"` — Gap analysis: what's missing relative to a topic\n"
        "• `/kb eval [all|case <id>|task-type <type>|collection <FolderName>] [judge]` — Run Evaluation Center cases in the background\n"
        "• `/kb eval-report` — Show the latest saved evaluation summary\n"
        "• `/kb clear-quarantine <FolderName> <filename>` — Remove file from quarantine\n"
        "• `/kb clear-quarantine-all` — Clear all quarantined files and re-ingest on restart\n"
        "• `/kb ticket \"<task name>\" [high|medium|low] [YYYY-MM-DD]` — Create a Notion ticket"
    )
    return "KB Agent Commands", [_section(text)]


# ---------------------------------------------------------------------------
# Thread follow-up helpers
# ---------------------------------------------------------------------------

def _extract_collection_from_thread(message: dict) -> str | None:
    """Parse the collection name from the header block of a bot's /kb ask reply."""
    for block in message.get("blocks", []):
        if block.get("type") == "header":
            text = block.get("text", {}).get("text", "")
            match = re.match(r"(?:💬|:speech_balloon:)\s+(.+)", text)
            if match:
                folder = match.group(1).strip()
                if "all knowledge bases" in folder.lower():
                    return None
                return folder_to_collection_name(folder)
    return None


def _build_thread_history(messages: list[dict], bot_id: str) -> list[dict]:
    """Convert a slice of thread messages into Claude conversation history."""
    history = []
    for msg in messages:
        if msg.get("subtype"):
            continue
        if msg.get("bot_id") == bot_id:
            # Prefer section block text over the fallback text field
            content = next(
                (b.get("text", {}).get("text", "") for b in msg.get("blocks", []) if b.get("type") == "section"),
                msg.get("text", ""),
            ).strip()
            if content:
                history.append({"role": "assistant", "content": content})
        elif not msg.get("bot_id"):
            text = msg.get("text", "").strip()
            if text:
                history.append({"role": "user", "content": text})
    return history


@app.event("message")
async def handle_thread_reply(event, client):
    """Reply in-thread when a user follows up in a thread the bot started."""
    subtype = event.get("subtype")
    thread_ts = event.get("thread_ts")
    ts = event.get("ts")
    if subtype:
        return

    if not thread_ts or thread_ts == ts:
        return

    if _bot_id is None:
        return

    channel = event.get("channel", "")
    user_text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
    if not user_text:
        return

    try:
        result = await client.conversations_replies(channel=channel, ts=thread_ts, limit=50)
        messages = result.get("messages", [])
        if not messages:
            log.warning("Thread reply: no messages returned for thread_ts=%s", thread_ts)
            return

        first_msg = messages[0]
        if first_msg.get("bot_id") != _bot_id:
            return

        # Ticket creation from thread: "ticket <name> [priority] [date]"
        if re.match(r"^ticket\b", user_text, re.IGNORECASE):
            rest = user_text[len("ticket"):].strip()
            task_name, _p, _d, error = parse_ticket_args(rest)
            fallback, blocks = (
                _error_blocks(error) if error else _ticket_prompt_blocks(rest)
            )
            await _safe_post(
                client.chat_postMessage,
                fallback,
                blocks,
                channel=channel,
                thread_ts=thread_ts,
            )
            return

        collection_name = _extract_collection_from_thread(first_msg)
        if not collection_name:
            return

        thread_msgs = [m for m in messages if m.get("ts") != ts]
        history = _build_thread_history(thread_msgs, _bot_id)

        rag_result = await answer_with_history(collection_name, history, user_text)

        await _safe_post(
            client.chat_postMessage,
            rag_result.answer,
            _text_sections(rag_result.answer),
            channel=channel,
            thread_ts=thread_ts,
        )
    except Exception as exc:
        log.error("Thread reply handler error in channel %s: %s", channel, exc)


# ---------------------------------------------------------------------------
# Notion ticket
# ---------------------------------------------------------------------------

TICKET_USAGE = 'Usage: `/kb ticket "<task name>" [high|medium|low] [YYYY-MM-DD]`'
_PRIORITIES = ("High", "Medium", "Low")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_ticket_args(rest: str) -> tuple[str | None, str, str | None, str | None]:
    """Parse `"<task name>" [priority] [YYYY-MM-DD]` into its parts.

    Returns (task_name, priority, due_date, error). `error` is None on success and
    task_name is None on failure, so a malformed command reports a problem instead of
    creating a ticket whose title has swallowed its own arguments.

    Quotes are normalised first: the original implementation matched only ASCII
    quotes, so a curly-quoted title fell through to "treat the whole string as the
    name", producing a title containing the quote marks and the trailing
    `high 2026-11-20`, with priority silently defaulting to Medium.
    """
    rest = _normalize_quotes(rest or "").strip()
    if not rest:
        return None, "Medium", None, TICKET_USAGE

    if rest[0] in _QUOTE_CHARS:
        quote = rest[0]
        closing = rest.find(quote, 1)
        if closing == -1:
            return None, "Medium", None, (
                f"Missing the closing `{quote}` around the task name.\n{TICKET_USAGE}"
            )
        task_name = rest[1:closing].strip()
        extra = rest[closing + 1:].strip()
    else:
        # Unquoted: the trailing priority/date tokens are still honoured, which is
        # what someone typing `/kb ticket Fix the thing high 2026-11-20` means.
        tokens = rest.split()
        tail: list[str] = []
        while tokens and (
            tokens[-1].lower() in {p.lower() for p in _PRIORITIES}
            or _DATE_RE.match(tokens[-1])
        ):
            tail.insert(0, tokens.pop())
        task_name = " ".join(tokens).strip()
        extra = " ".join(tail)

    if not task_name:
        return None, "Medium", None, TICKET_USAGE

    priority = "Medium"
    due_date: str | None = None
    unknown: list[str] = []
    for token in extra.split():
        low = token.lower()
        if low in {p.lower() for p in _PRIORITIES}:
            priority = token.capitalize()
        elif _DATE_RE.match(token):
            due_date = token
        else:
            unknown.append(token)

    if unknown:
        return None, "Medium", None, (
            "Didn't understand: " + ", ".join(f"`{u}`" for u in unknown)
            + f"\n{TICKET_USAGE}"
        )
    return task_name, priority, due_date, None


# ---------------------------------------------------------------------------
# Ticket modal
# ---------------------------------------------------------------------------

TICKET_CALLBACK_ID = "kb_ticket_submit"
TICKET_OPEN_ACTION_ID = "kb_open_ticket_modal"
_UNASSIGNED_VALUE = "__unassigned__"


def _ticket_modal_view(
    task_name: str,
    priority: str,
    due_date: str | None,
    notion_users: list[dict],
    private_metadata: str,
) -> dict:
    """Build the review-before-create modal. Prefilled from the command, editable."""
    blocks: list[dict] = [
        {
            "type": "input",
            "block_id": "task_name",
            "label": {"type": "plain_text", "text": "Task name"},
            "element": {
                "type": "plain_text_input",
                "action_id": "value",
                "initial_value": task_name,
            },
        },
        {
            "type": "input",
            "block_id": "priority",
            "label": {"type": "plain_text", "text": "Priority"},
            "element": {
                "type": "static_select",
                "action_id": "value",
                "initial_option": {
                    "text": {"type": "plain_text", "text": priority},
                    "value": priority,
                },
                "options": [
                    {"text": {"type": "plain_text", "text": p}, "value": p}
                    for p in _PRIORITIES
                ],
            },
        },
        {
            "type": "input",
            "block_id": "due_date",
            "optional": True,
            "label": {"type": "plain_text", "text": "Due date"},
            "element": (
                {"type": "datepicker", "action_id": "value", "initial_date": due_date}
                if due_date else
                {"type": "datepicker", "action_id": "value"}
            ),
        },
    ]

    if notion_users:
        blocks.append({
            "type": "input",
            "block_id": "assignee",
            "optional": True,
            "label": {"type": "plain_text", "text": "Assignee"},
            "element": {
                "type": "static_select",
                "action_id": "value",
                "placeholder": {"type": "plain_text", "text": "Unassigned"},
                "options": [
                    {
                        "text": {"type": "plain_text", "text": u["name"][:75]},
                        "value": u["id"],
                    }
                    for u in notion_users[:100]
                ],
            },
        })
    else:
        # The integration cannot list Notion users, so a real assignment is not
        # possible. Offer free text and say plainly that it is not an assignment --
        # never guess a Notion identity from a Slack one.
        blocks.append({
            "type": "input",
            "block_id": "requested_owner",
            "optional": True,
            "label": {"type": "plain_text", "text": "Requested owner (not a Notion assignment)"},
            "element": {"type": "plain_text_input", "action_id": "value"},
            "hint": {
                "type": "plain_text",
                "text": "Notion user list unavailable, so this is recorded as text in the page body.",
            },
        })

    blocks.append({
        "type": "input",
        "block_id": "notes",
        "optional": True,
        "label": {"type": "plain_text", "text": "Additional notes"},
        "element": {
            "type": "plain_text_input",
            "action_id": "value",
            "multiline": True,
            "placeholder": {"type": "plain_text", "text": "Optional context for whoever picks this up"},
        },
    })

    return {
        "type": "modal",
        "callback_id": TICKET_CALLBACK_ID,
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Create Ticket"},
        "submit": {"type": "plain_text", "text": "Create Ticket"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def extract_ticket_submission(view: dict) -> dict:
    """Pull the submitted field values out of a Slack view payload."""
    state = ((view or {}).get("state") or {}).get("values") or {}

    def val(block: str, key: str = "value"):
        return ((state.get(block) or {}).get(key)) or {}

    selected = val("assignee").get("selected_option") or {}
    assignee_id = selected.get("value") or None
    if assignee_id == _UNASSIGNED_VALUE:
        assignee_id = None

    priority_opt = val("priority").get("selected_option") or {}
    return {
        "task_name": (val("task_name").get("value") or "").strip(),
        "priority": priority_opt.get("value") or "Medium",
        "due_date": val("due_date").get("selected_date") or None,
        "assignee_id": assignee_id,
        "assignee_name": (selected.get("text") or {}).get("text") or None,
        "requested_owner": (val("requested_owner").get("value") or "").strip() or None,
        "notes": (val("notes").get("value") or "").strip() or None,
    }


def _ticket_success_blocks(fields: dict, url: str) -> tuple[str, list]:
    msg = f"✅ *{fields['task_name']}* created (Priority: {fields['priority']})"
    if fields.get("due_date"):
        msg += f", due {fields['due_date']}"
    if fields.get("assignee_name"):
        msg += f"\nAssigned to *{fields['assignee_name']}* in Notion"
    elif fields.get("requested_owner"):
        msg += f"\nRequested owner: *{fields['requested_owner']}* (recorded as text, not a Notion assignment)"
    else:
        msg += "\nUnassigned"
    if fields.get("notes"):
        msg += "\n_Additional notes saved to the page body._"

    blocks = [_header("🎫 Ticket Created"), _divider(), _section(msg)]
    if url:
        blocks.append(_context(f"<{url}|Open in Notion>"))
    return msg, blocks


async def create_ticket_from_submission(fields: dict) -> tuple[str, list]:
    """The single Notion-creating path. Both the slash command and the thread route
    reach Notion only through here, via the modal, so creation logic is not
    duplicated and nothing can bypass the review step."""
    url = await create_ticket(
        fields["task_name"],
        fields["priority"],
        fields.get("due_date"),
        assignee_id=fields.get("assignee_id"),
        notes=fields.get("notes"),
        requested_owner=fields.get("requested_owner"),
    )
    return _ticket_success_blocks(fields, url)


def _ticket_prompt_blocks(rest: str) -> tuple[str, list]:
    """Thread route: offer a button that opens the same modal, rather than creating
    a ticket immediately and bypassing review."""
    msg = "Open the ticket form to review and assign before creating."
    return msg, [
        _header("🎫 Create Ticket"),
        _divider(),
        _section(msg),
        {
            "type": "actions",
            "elements": [{
                "type": "button",
                "action_id": TICKET_OPEN_ACTION_ID,
                "style": "primary",
                "text": {"type": "plain_text", "text": "Open ticket form"},
                "value": (rest or "")[:1900],
            }],
        },
    ]


async def _open_ticket_modal(client, trigger_id: str, rest: str, channel_id: str, user_id: str) -> None:
    task_name, priority, due_date, error = parse_ticket_args(rest)
    if error:
        raise ValueError(error)
    users = await list_assignable_users()
    metadata = json.dumps({"channel_id": channel_id, "user_id": user_id})
    await client.views_open(
        trigger_id=trigger_id,
        view=_ticket_modal_view(task_name, priority, due_date, users, metadata),
    )


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def _is_complex_query(question: str) -> bool:
    """Heuristic: route to agent if query looks multi-hop or comparative."""
    signals = [
        "compare", "vs", "versus", "difference between",
        "across all", "which folder", "which collection",
        "best", "most relevant", "where can i find",
        "summarize everything", "all collections",
    ]
    q = question.lower()
    return any(s in q for s in signals)


async def _handle_agent_in_channel(question: str, say, respond) -> None:
    """Run the agentic loop and post each reasoning step to the channel."""
    if not question:
        await respond(text="Usage: `/kb agent <question>`")
        return

    await respond(
        text="🤖 Agent starting...",
        blocks=[_section("🤖 Agent starting — I'll post my reasoning in the channel.")],
    )

    opener = await say(text=f"🤖 *Agent Query:* {question}")
    thread_ts = opener.get("ts") if opener else None

    async def post_step(text: str) -> None:
        # Tool results can be long, so step messages need the same splitting.
        for piece in _split_text(text) or [text]:
            try:
                await say(text=piece, thread_ts=thread_ts)
            except Exception as exc:
                log.warning("Agent post_step failed: %s", exc)

    try:
        answer = await run_agent(question, _orch, post_step, max_rounds=3)
    except Exception as exc:
        log.error("Agent run failed: %s", exc, exc_info=exc)
        message = _friendly_error(exc)
        await _safe_post(say, message, [_section(message)], thread_ts=thread_ts)
        return

    await _safe_post(
        say,
        answer,
        [_header("Agent Answer"), _divider(), *_text_sections(answer)],
        thread_ts=thread_ts,
    )


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------

@app.command("/kb")
async def handle_kb(ack, respond, say, command, client):
    await ack()

    text = command.get("text", "")
    subcommand, folder, query = _parse_command(text)
    channel_id = command.get("channel_id", "")
    user_id = command.get("user_id", "")

    try:
        if subcommand == "agent":
            await _handle_agent_in_channel(query, say, respond)
            return
        elif subcommand == "ticket":
            # Opens a review modal; nothing is created until the user submits it.
            try:
                await _open_ticket_modal(
                    client,
                    command.get("trigger_id", ""),
                    query,
                    channel_id,
                    user_id,
                )
                return
            except ValueError as exc:
                fallback, blocks = _error_blocks(str(exc))
        elif subcommand == "list":
            fallback, blocks = await _handle_list()
        elif subcommand == "ask":
            if not folder and _is_complex_query(query):
                await _handle_agent_in_channel(query, say, respond)
                return
            fallback, blocks = await _handle_ask(folder, query)
        elif subcommand == "changes":
            fallback, blocks = await _handle_changes(folder)
        elif subcommand == "status":
            fallback, blocks = await _handle_status()
        elif subcommand == "diff":
            fallback, blocks = await _handle_diff(folder, query)
        elif subcommand == "clear-quarantine-all":
            fallback, blocks = await _handle_clear_quarantine_all()
        elif subcommand == "clear-quarantine":
            fallback, blocks = await _handle_clear_quarantine(folder, query)
        elif subcommand == "gaps":
            fallback, blocks = await _handle_gaps(folder, query)
        elif subcommand == "draft":
            fallback, blocks = await _handle_draft(folder, query)
        elif subcommand == "compare":
            fallback, blocks = await _handle_compare(folder, query)
        elif subcommand == "score":
            fallback, blocks = await _handle_score(folder, query)
        elif subcommand == "eval":
            result = await _handle_eval(text[len("eval"):].strip(), app.client, channel_id, user_id)
            if result is None:
                return
            fallback, blocks = result
        elif subcommand == "eval-report":
            fallback, blocks = await _handle_eval_report()
        else:
            fallback, blocks = _help_blocks()
    except Exception as exc:
        log.error("Error handling /kb %s: %s", subcommand, exc, exc_info=exc)
        fallback, blocks = _error_blocks(_friendly_error(exc))

    if subcommand == "ask":
        try:
            await say(text=fallback, blocks=_cap_blocks(blocks))
            return
        except Exception as exc:
            if "not_in_channel" in str(exc):
                note = "\n\n_Tip: invite the bot to this channel with `/invite @<bot-name>` to enable threaded follow-ups._"
                await _safe_post(respond, fallback + note, blocks)
                return
            log.error("say() failed for /kb ask: %s", exc)

    await _safe_post(respond, fallback, blocks)


# ---------------------------------------------------------------------------
# Ticket modal interactions
# ---------------------------------------------------------------------------

@app.action(TICKET_OPEN_ACTION_ID)
async def handle_open_ticket_modal(ack, body, client):
    """Thread route: the button opens the same modal the slash command does."""
    await ack()
    rest = ""
    for action in (body.get("actions") or []):
        if action.get("action_id") == TICKET_OPEN_ACTION_ID:
            rest = action.get("value") or ""
    channel_id = (body.get("channel") or {}).get("id", "")
    user_id = (body.get("user") or {}).get("id", "")
    try:
        await _open_ticket_modal(
            client, body.get("trigger_id", ""), rest, channel_id, user_id
        )
    except Exception as exc:
        log.error("Could not open ticket modal: %s", exc, exc_info=exc)
        if channel_id:
            await client.chat_postMessage(
                channel=channel_id, text=f"❌ Could not open the ticket form: {exc}"
            )


@app.view(TICKET_CALLBACK_ID)
async def handle_ticket_submission(ack, body, client, view):
    """Create the Notion page only after the user submits the modal.

    Validation errors are returned through ack(response_action="errors") so the modal
    stays open with the field flagged. Once validation passes the modal is closed,
    then Notion is called -- the success message is posted only after the API
    confirms, and a failure is reported without any retry, since a create is not
    idempotent.
    """
    fields = extract_ticket_submission(view)

    if not fields["task_name"]:
        await ack(response_action="errors", errors={"task_name": "A task name is required."})
        return
    if fields["priority"] not in _PRIORITIES:
        await ack(response_action="errors", errors={"priority": "Choose High, Medium or Low."})
        return
    if fields.get("due_date") and not _DATE_RE.match(fields["due_date"]):
        await ack(response_action="errors", errors={"due_date": "Use YYYY-MM-DD."})
        return

    await ack()

    try:
        meta = json.loads(view.get("private_metadata") or "{}")
    except (ValueError, TypeError):
        meta = {}
    channel_id = meta.get("channel_id") or ""
    user_id = meta.get("user_id") or (body.get("user") or {}).get("id", "")

    try:
        fallback, blocks = await create_ticket_from_submission(fields)
    except Exception as exc:
        log.error("Notion ticket creation failed: %s", exc, exc_info=exc)
        fallback, blocks = _error_blocks(f"❌ Failed to create ticket: {exc}")

    if not channel_id:
        log.warning("No channel in private_metadata; ticket result not posted")
        return
    try:
        await client.chat_postMessage(
            channel=channel_id, text=fallback, blocks=_cap_blocks(blocks)
        )
    except Exception as exc:
        # The channel may not include the bot; fall back to a DM so the result and
        # its Notion link are never lost after a successful create.
        log.warning("chat_postMessage failed (%s); trying DM", exc)
        if user_id:
            try:
                await client.chat_postMessage(
                    channel=user_id, text=fallback, blocks=_cap_blocks(blocks)
                )
            except Exception as exc2:
                log.error("Could not deliver ticket confirmation: %s", exc2)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

async def get_app() -> AsyncApp:
    return app


async def start_bot() -> None:
    global _bot_id
    await init_db()

    auth = await app.client.auth_test()
    _bot_id = auth.get("bot_id")
    log.info("Bot ID resolved: %s", _bot_id)

    slack_app_token = os.environ.get("SLACK_APP_TOKEN")
    if slack_app_token:
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        log.info("Starting Slack bot in Socket Mode")
        handler = AsyncSocketModeHandler(app, slack_app_token)
        await handler.start_async()
    else:
        log.info("Starting Slack bot in HTTP mode on port 3000")
        await app.start_async(port=3000)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_bot())
