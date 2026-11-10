import logging
import os

import aiohttp

log = logging.getLogger(__name__)

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

# Notion caps a single rich_text content string at 2000 characters.
_RICH_TEXT_LIMIT = 2000


def _credentials() -> tuple[str, str]:
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY is not set")
    if not database_id:
        raise RuntimeError("NOTION_DATABASE_ID is not set")
    return api_key, database_id


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _paragraphs(text: str) -> list[dict]:
    """Split free text into Notion paragraph blocks, respecting the 2000-char cap."""
    blocks: list[dict] = []
    for para in [p for p in (text or "").split("\n\n") if p.strip()]:
        chunk = para.strip()
        while chunk:
            head, chunk = chunk[:_RICH_TEXT_LIMIT], chunk[_RICH_TEXT_LIMIT:]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": head}}]},
            })
    return blocks


def build_page_payload(
    database_id: str,
    task_name: str,
    priority: str = "Medium",
    due_date: str | None = None,
    assignee_id: str | None = None,
    notes: str | None = None,
    requested_owner: str | None = None,
) -> dict:
    """Build the POST /v1/pages body. Pure function, so it is directly testable.

    `assignee_id` must be a **Notion** user id (a UUID from GET /v1/users), never a
    Slack user id -- the two identity spaces are unrelated. `requested_owner` is the
    free-text fallback for when the integration cannot list Notion users; it is
    written into the page body and explicitly labelled as not a real assignment, so
    an unverified owner can never be mistaken for one.
    """
    properties: dict = {
        "Task name": {"title": [{"text": {"content": task_name}}]},
        "Priority": {"select": {"name": priority}},
    }
    if due_date:
        properties["Due date"] = {"date": {"start": due_date}}
    if assignee_id:
        properties["Assignee"] = {"people": [{"object": "user", "id": assignee_id}]}

    children: list[dict] = []
    if notes and notes.strip():
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Additional Notes"}}]},
        })
        children.extend(_paragraphs(notes))
    if requested_owner and requested_owner.strip():
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Requested Owner"}}]},
        })
        children.extend(_paragraphs(
            f"{requested_owner.strip()} (requested in Slack; not assigned in Notion)"
        ))

    payload: dict = {"parent": {"database_id": database_id}, "properties": properties}
    if children:
        payload["children"] = children
    return payload


async def list_assignable_users() -> list[dict]:
    """Return assignable Notion people as [{"id", "name"}].

    Never raises: the caller builds a Slack modal and must not fail because the
    integration lacks the "read user information" capability. An empty list means
    "cannot offer a real assignee selector", which the caller renders as a clearly
    labelled free-text field instead of guessing at an identity.
    """
    try:
        api_key, _ = _credentials()
    except RuntimeError as exc:
        log.warning("list_assignable_users: %s", exc)
        return []

    users: list[dict] = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_NOTION_API_BASE}/users",
                headers=_headers(api_key),
                params={"page_size": "100"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if not resp.ok:
                    log.warning(
                        "Notion user list unavailable (%s): %s",
                        resp.status, data.get("message", data),
                    )
                    return []
        for user in data.get("results", []):
            if user.get("type") != "person":
                continue
            name = user.get("name") or (user.get("person") or {}).get("email") or "Unnamed"
            if user.get("id"):
                users.append({"id": user["id"], "name": name})
    except Exception as exc:
        log.warning("Notion user list failed: %s", exc)
        return []
    return users


async def create_ticket(
    task_name: str,
    priority: str = "Medium",
    due_date: str | None = None,
    assignee_id: str | None = None,
    notes: str | None = None,
    requested_owner: str | None = None,
) -> str:
    """Create a page in the Notion Tasks Tracker database. Returns the page URL.

    Raises RuntimeError on any non-2xx response. There is deliberately **no retry**:
    a create is not idempotent, and a retry after an ambiguous failure is how you end
    up with duplicate tickets.
    """
    api_key, database_id = _credentials()
    payload = build_page_payload(
        database_id, task_name, priority, due_date, assignee_id, notes, requested_owner
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_NOTION_API_BASE}/pages", headers=_headers(api_key), json=payload
        ) as resp:
            data = await resp.json()
            if not resp.ok:
                msg = data.get("message", str(data))
                raise RuntimeError(f"Notion API error {resp.status}: {msg}")
            return data.get("url", "")
