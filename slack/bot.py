import asyncio
import logging
import os
import re

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
from agent.rag import answer_query, answer_query_all, summarize_recent_changes
from ingestion.quarantine import clear_quarantine, get_quarantined_files
from storage.db import init_db

load_dotenv()

log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

app = AsyncApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_path(file_path: str) -> str:
    return file_path.replace("\\", "/")


def clean_for_slack(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
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

    if subcommand not in {"ask", "clear-quarantine", "changes"}:
        return subcommand, "", rest

    rest_parts = rest.split(None, 1)
    folder = rest_parts[0] if rest_parts else ""
    remainder = rest_parts[1].strip() if len(rest_parts) > 1 else ""
    query = re.sub(r'^["\']|["\']$', "", remainder)
    return subcommand, folder, query


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
            blocks = [_header("💬 All Knowledge Bases"), _divider(), _section(clean_for_slack(result["answer"])), _divider()]
            for col, sources in result["sources_by_collection"].items():
                blocks.append(_context(f"📄 {col}: {', '.join(sources)}"))
            if not result["sources_by_collection"]:
                blocks.append(_context("📄 No sources found"))
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
        _divider(),
        _context(f"📄 Sources: {', '.join(result.sources)}" if result.sources else "📄 No sources"),
    ]
    if inferred_label:
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


def _help_blocks() -> tuple[str, list]:
    text = (
        "*KB Agent Commands*\n"
        "• `/kb list` — Show all knowledge base collections\n"
        "• `/kb ask <FolderName> \"<question>\"` — Ask a question\n"
        "• `/kb changes <FolderName>` — Summarize recent changes\n"
        "• `/kb status` — Watcher status and quarantined files\n"
        "• `/kb clear-quarantine <FolderName> <filename>` — Remove file from quarantine"
    )
    return "KB Agent Commands", [_section(text)]


# ---------------------------------------------------------------------------
# Slash command handler
# ---------------------------------------------------------------------------

@app.command("/kb")
async def handle_kb(ack, respond, command):
    await ack()

    text = command.get("text", "")
    subcommand, folder, query = _parse_command(text)

    try:
        if subcommand == "list":
            fallback, blocks = await _handle_list()
        elif subcommand == "ask":
            fallback, blocks = await _handle_ask(folder, query)
        elif subcommand == "changes":
            fallback, blocks = await _handle_changes(folder)
        elif subcommand == "status":
            fallback, blocks = await _handle_status()
        elif subcommand == "clear-quarantine":
            fallback, blocks = await _handle_clear_quarantine(folder, query)
        else:
            fallback, blocks = _help_blocks()
    except Exception as exc:
        log.error("Error handling /kb %s: %s", subcommand, exc)
        fallback, blocks = _error_blocks(f"An error occurred: {exc}\nTry `/kb` for usage help.")

    await respond(text=fallback, blocks=blocks)


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

async def start_bot() -> None:
    await init_db()

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
