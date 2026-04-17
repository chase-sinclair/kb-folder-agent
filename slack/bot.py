import asyncio
import logging
import os
import re

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp

from agent.orchestrator import (
    collection_exists,
    folder_to_collection_name,
    get_available_collections,
    get_folder_list,
)
from agent.rag import answer_query, summarize_recent_changes
from ingestion.quarantine import clear_quarantine, get_quarantined_files
from storage.db import init_db

load_dotenv()

log = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]

app = AsyncApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_command(text: str) -> tuple[str, str, str]:
    """Returns (subcommand, folder_name, query). All fields may be empty strings."""
    text = (text or "").strip()
    if not text:
        return "", "", ""

    parts = text.split(None, 1)
    subcommand = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if subcommand not in {"ask", "clear-quarantine"}:
        return subcommand, "", rest

    # Extract folder name (first token) and optional quoted query
    rest_parts = rest.split(None, 1)
    folder = rest_parts[0] if rest_parts else ""
    remainder = rest_parts[1].strip() if len(rest_parts) > 1 else ""

    # Strip surrounding quotes from query
    query = re.sub(r'^["\']|["\']$', "", remainder)
    return subcommand, folder, query


# ---------------------------------------------------------------------------
# Response formatters
# ---------------------------------------------------------------------------

async def _handle_list() -> str:
    folders = await get_folder_list()
    collections = await get_available_collections()
    counts = {c["name"]: c.get("vector_count", 0) for c in collections}

    if not folders:
        return "No knowledge base folders found."

    lines = ["*Available Knowledge Bases*"]
    for f in folders:
        col = f["collection_name"]
        count = counts.get(col, 0)
        lines.append(f"• {f['name']} → `{col}` ({count} chunks)")
    return "\n".join(lines)


async def _handle_ask(folder_name: str, query: str) -> str:
    if not folder_name:
        return "Usage: `/kb ask <FolderName> \"<question>\"`"
    if not query:
        return f"Usage: `/kb ask {folder_name} \"<question>\"`"

    collection_name = folder_to_collection_name(folder_name)

    if not await collection_exists(collection_name):
        return (
            f"Collection `{collection_name}` not found. "
            f"Use `/kb list` to see available knowledge bases."
        )

    result = await answer_query(collection_name, query)

    lines = [result.answer]
    if result.sources:
        lines.append(f"\n*Sources:* {', '.join(result.sources)}")
    return "\n".join(lines)


async def _handle_status() -> str:
    collections = await get_available_collections()
    quarantined = await get_quarantined_files()

    lines = [
        "*KB Agent Status*",
        f"• Watcher: running",
        f"• Collections: {len(collections)}",
    ]

    if collections:
        for c in collections:
            lines.append(f"  - `{c['name']}` — {c.get('vector_count', 0)} chunks")

    if quarantined:
        lines.append(f"• Quarantined files: {len(quarantined)}")
        for q in quarantined:
            lines.append(f"  - `{q['file_path']}` ({q['error_type']})")
    else:
        lines.append("• Quarantined files: none")

    return "\n".join(lines)


async def _handle_clear_quarantine(folder_name: str, filename: str) -> str:
    if not folder_name or not filename:
        return "Usage: `/kb clear-quarantine <FolderName> <filename>`"

    from ingestion.watcher import WATCHED_FOLDER
    file_path = str(WATCHED_FOLDER / folder_name / filename)

    await clear_quarantine(file_path)
    return f"Cleared quarantine for `{filename}` in `{folder_name}`."


def _help_message() -> str:
    return (
        "*KB Agent Commands*\n"
        "• `/kb list` — Show all knowledge base collections\n"
        "• `/kb ask <FolderName> \"<question>\"` — Ask a question\n"
        "• `/kb status` — Show watcher status and quarantined files\n"
        "• `/kb clear-quarantine <FolderName> <filename>` — Remove file from quarantine"
    )


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
            response = await _handle_list()
        elif subcommand == "ask":
            response = await _handle_ask(folder, query)
        elif subcommand == "status":
            response = await _handle_status()
        elif subcommand == "clear-quarantine":
            response = await _handle_clear_quarantine(folder, query)
        else:
            response = _help_message()
    except Exception as exc:
        log.error("Error handling /kb %s: %s", subcommand, exc)
        response = f"An error occurred: {exc}\nTry `/kb` for usage help."

    await respond(response)


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
