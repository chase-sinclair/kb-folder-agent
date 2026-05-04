import os
import aiohttp

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


async def create_ticket(
    task_name: str,
    priority: str = "Medium",
    due_date: str | None = None,
) -> str:
    """Create a page in the Notion Tasks Tracker database. Returns the page URL."""
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DATABASE_ID")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY is not set")
    if not database_id:
        raise RuntimeError("NOTION_DATABASE_ID is not set")

    properties: dict = {
        "Task name": {"title": [{"text": {"content": task_name}}]},
        "Priority": {"select": {"name": priority}},
    }
    if due_date:
        properties["Due date"] = {"date": {"start": due_date}}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{_NOTION_API_BASE}/pages", headers=headers, json=payload
        ) as resp:
            data = await resp.json()
            if not resp.ok:
                msg = data.get("message", str(data))
                raise RuntimeError(f"Notion API error {resp.status}: {msg}")
            return data.get("url", "")
