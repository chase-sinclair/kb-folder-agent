import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from agent.orchestrator import get_available_collections
from agent.rag import summarize_recent_changes

log = logging.getLogger(__name__)


def _clean_for_slack(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s+(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.+)$", r"\1", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def build_digest() -> list[dict]:
    collections = await get_available_collections()
    results = []
    for col in collections:
        name = col["name"]
        try:
            result = await summarize_recent_changes(name)
            if result.result_count == 0:
                continue
            results.append({
                "collection_name": name,
                "answer": result.answer,
                "sources": result.sources,
            })
        except Exception as exc:
            log.warning("build_digest: skipping %r due to error: %s", name, exc)
    return results


async def send_digest(app) -> None:
    channel = os.environ.get("DIGEST_CHANNEL", "#general")
    entries = await build_digest()

    if not entries:
        log.info("Digest: no changes to report")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "📋 Daily Knowledge Base Digest"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Generated {today}"}]},
        {"type": "divider"},
    ]

    for entry in entries:
        sources_text = ", ".join(entry["sources"]) if entry["sources"] else "No sources"
        blocks += [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{entry['collection_name']}*"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": _clean_for_slack(entry["answer"])}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"📄 Sources: {sources_text}"}]},
            {"type": "divider"},
        ]

    await app.client.chat_postMessage(
        channel=channel,
        text="📋 Daily Knowledge Base Digest",
        blocks=blocks,
    )
    log.info("Digest sent to %s (%d collection(s))", channel, len(entries))


async def start_digest_scheduler(app) -> None:
    if os.environ.get("DIGEST_ENABLED", "").lower() != "true":
        log.info("Digest scheduler disabled (DIGEST_ENABLED != true)")
        return

    digest_time = os.environ.get("DIGEST_TIME", "09:00")
    try:
        hour, minute = (int(p) for p in digest_time.split(":"))
    except ValueError:
        log.error("Invalid DIGEST_TIME %r — expected HH:MM. Digest disabled.", digest_time)
        return

    log.info("Digest scheduler started — daily at %02d:%02d UTC", hour, minute)

    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_run <= now:
            # Already passed today — schedule for tomorrow
            from datetime import timedelta
            next_run = next_run + timedelta(days=1)

        delay = (next_run - now).total_seconds()
        log.info("Next digest in %.0f seconds (at %s UTC)", delay, next_run.strftime("%Y-%m-%d %H:%M"))
        await asyncio.sleep(delay)

        try:
            await send_digest(app)
        except Exception as exc:
            log.error("Digest send failed: %s", exc)
