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
from agent.rag import answer_query, answer_query_all, answer_with_history, compare_collections, draft_section, find_gaps, score_requirement, summarize_diff, summarize_recent_changes
from evals.config import REPORTS_DIR, RESULTS_DIR
from evals.report import write_json_report, write_markdown_report
from evals.runner import run_evaluations
from evals.schema import EvalRunConfig
from ingestion.quarantine import clear_all_quarantine, clear_quarantine, get_quarantined_files
from storage.db import init_db

load_dotenv()

log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

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


def _parse_command(text: str) -> tuple[str, str, str]:
    """Returns (subcommand, folder_name, query). All fields may be empty strings."""
    text = (text or "").strip()
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
    tokens = [token for token in request.split() if token]

    normalized_tokens: list[str] = []
    for token in tokens:
        if token.lower() == "judge":
            use_judge = True
        else:
            normalized_tokens.append(token)

    output_slug = _timestamp_slug()
    config = EvalRunConfig(
        cases_path="evals/test_cases.yaml",
        output_markdown=str(REPORTS_DIR / f"eval_report_{output_slug}.md"),
        output_json=str(RESULTS_DIR / f"eval_results_{output_slug}.json"),
        use_judge=use_judge,
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
        "Usage: `/kb eval [all] [judge]`, `/kb eval case <id> [judge]`, "
        "`/kb eval task-type <type> [judge]`, or `/kb eval collection <FolderName> [judge]`"
    )
    return None, usage


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
            "judge_enabled": summary.judge_enabled,
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
        ),
        _context(
            f"Judge enabled: {'yes' if summary_data['judge_enabled'] else 'no'}"
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
                f"collection={config.collection or 'all'} | judge={'on' if config.use_judge else 'off'}"
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


# ---------------------------------------------------------------------------
# Response formatters — return (fallback_text, blocks)
# ---------------------------------------------------------------------------

async def _handle_list() -> tuple[str, list]:
    folders = await get_folder_list()
    collections = await get_available_collections()
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
            blocks = [_header("💬 All Knowledge Bases"), _divider(), _section(clean_for_slack(result["answer"]))]
            return "💬 All Knowledge Bases", blocks

        collection_name = folder_to_collection_name(folder_name)
        if not await collection_exists(collection_name):
            msg = f"No knowledge base found for `{folder_name}`. Use `/kb list` to see available folders."
            return _error_blocks(msg)

    result = await answer_query(collection_name, query)
    blocks = [
        _header(f"💬 {folder_name}"),
        _divider(),
        _section(clean_for_slack(result.answer)),
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
        _section(clean_for_slack(result.answer)),
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
            blocks.append(_context(f"⚠️ `{normalize_path(q['file_path'])}` — {q['error_type']}"))
    else:
        blocks.append(_context("✅ No quarantined files"))

    return "⚙️ KB Agent Status", blocks


async def _handle_clear_quarantine(folder_name: str, filename: str) -> tuple[str, list]:
    if not folder_name or not filename:
        return _error_blocks("Usage: `/kb clear-quarantine <FolderName> <filename>`")

    from ingestion.watcher import WATCHED_FOLDER
    file_path = normalize_path(str(WATCHED_FOLDER / folder_name / filename))
    await clear_quarantine(file_path)
    msg = f"✅ Cleared quarantine for `{filename}` in `{folder_name}`."
    return msg, [_section(msg)]


async def _handle_clear_quarantine_all() -> tuple[str, list]:
    count = await clear_all_quarantine()
    msg = f"✅ Cleared all quarantine entries ({count} file(s)). Restart the agent to re-ingest."
    return msg, [_section(msg)]


async def _handle_diff(folder_name: str, filename: str) -> tuple[str, list]:
    if not folder_name or not filename:
        return _error_blocks("Usage: `/kb diff <FolderName> <filename>`")

    from ingestion.watcher import WATCHED_FOLDER
    file_path = normalize_path(str(WATCHED_FOLDER / folder_name / filename))
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

    # One section block per paragraph to stay within Slack's 3000-char limit
    for para in (p.strip() for p in body_text.split("\n\n") if p.strip()):
        blocks.append(_section(clean_for_slack(para)))

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
    blocks.append(_section(clean_for_slack(body_text)))

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
    blocks.append(_section(clean_for_slack(body_text)))
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
        _section(clean_for_slack(result.answer)),
        _divider(),
        _context(f"📄 Analyzed {result.result_count} chunk(s) from {len(result.sources)} source(s)"),
    ]
    return f"🔍 Gaps — {folder_name} vs \"{topic}\"", blocks


def _help_blocks() -> tuple[str, list]:
    text = (
        "*KB Agent Commands*\n"
        "• `/kb list` — Show all knowledge base collections\n"
        "• `/kb ask <FolderName> \"<question>\"` — Ask a question\n"
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
        "• `/kb clear-quarantine-all` — Clear all quarantined files and re-ingest on restart"
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

        collection_name = _extract_collection_from_thread(first_msg)
        if not collection_name:
            return

        thread_msgs = [m for m in messages if m.get("ts") != ts]
        history = _build_thread_history(thread_msgs, _bot_id)

        rag_result = await answer_with_history(collection_name, history, user_text)
        answer_text = clean_for_slack(rag_result.answer)

        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=rag_result.answer,
            blocks=[_section(answer_text)],
        )
    except Exception as exc:
        log.error("Thread reply handler error in channel %s: %s", channel, exc)


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------

@app.command("/kb")
async def handle_kb(ack, respond, say, command):
    await ack()

    text = command.get("text", "")
    subcommand, folder, query = _parse_command(text)
    channel_id = command.get("channel_id", "")
    user_id = command.get("user_id", "")

    try:
        if subcommand == "list":
            fallback, blocks = await _handle_list()
        elif subcommand == "ask":
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
        log.error("Error handling /kb %s: %s", subcommand, exc)
        fallback, blocks = _error_blocks(f"An error occurred: {exc}\nTry `/kb` for usage help.")

    if subcommand == "ask":
        try:
            await say(text=fallback, blocks=blocks)
        except Exception as exc:
            if "not_in_channel" in str(exc):
                note = "\n\n_Tip: invite the bot to this channel with `/invite @<bot-name>` to enable threaded follow-ups._"
                await respond(text=fallback + note, blocks=blocks)
            else:
                log.error("say() failed for /kb ask: %s", exc)
                await respond(text=fallback, blocks=blocks)
    else:
        await respond(text=fallback, blocks=blocks)


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
