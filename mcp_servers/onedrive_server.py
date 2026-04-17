import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import msal
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

CLIENT_ID = os.environ["AZURE_CLIENT_ID"]
TENANT_ID = os.environ.get("AZURE_TENANT_ID", "consumers")
ONEDRIVE_FOLDER = os.environ.get("ONEDRIVE_FOLDER", "test-kb")
SCOPES = ["Files.Read", "Files.Read.All", "User.Read"]
TOKEN_CACHE_PATH = ".onedrive_token_cache.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".txt",
    ".xlsx", ".csv",
    ".py", ".js", ".ts", ".go", ".rs",
    ".pptx", ".eml", ".html",
}

mcp = FastMCP("onedrive")


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_PATH):
        with open(TOKEN_CACHE_PATH, "r") as f:
            cache.deserialize(f.read())
    return cache


def save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(TOKEN_CACHE_PATH, "w") as f:
            f.write(cache.serialize())


def get_token() -> str:
    cache = load_cache()
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            save_cache(cache)
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise ValueError("Failed to create device flow")

    print("\n" + "=" * 50)
    print("ACTION REQUIRED:")
    print(f"1. Go to: {flow['verification_uri']}")
    print(f"2. Enter code: {flow['user_code']}")
    print("=" * 50 + "\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise ValueError(f"Auth failed: {result.get('error_description')}")

    save_cache(cache)
    return result["access_token"]


def get_headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collection_name(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")


def _iso(ts_str: str) -> str:
    """Normalise Graph API lastModifiedDateTime to ISO 8601."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ts_str


def _download_to_temp(file_path: str) -> str:
    """Download a OneDrive file to a local temp path and return that path."""
    url = f"{GRAPH_BASE}/me/drive/root:/{file_path}:/content"
    response = requests.get(url, headers=get_headers(), stream=True)
    response.raise_for_status()

    suffix = Path(file_path).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        for chunk in response.iter_content(chunk_size=65536):
            tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def _extract_text(local_path: str, ext: str) -> str:
    if ext in {".md", ".txt"}:
        return Path(local_path).read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        import pdfplumber
        pages = []
        with pdfplumber.open(local_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)
    if ext == ".docx":
        from docx import Document
        doc = Document(local_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    raise ValueError(f"Text extraction not supported for {ext!r}")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_folders() -> list[dict]:
    """Lists all top-level folders inside ONEDRIVE_FOLDER."""
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}:/children"
    response = requests.get(url, headers=get_headers())
    response.raise_for_status()

    results = []
    for item in response.json().get("value", []):
        if "folder" not in item:
            continue
        name = item["name"]
        files = list_files(name)
        results.append({
            "name": name,
            "collection_name": _collection_name(name),
            "path": f"{ONEDRIVE_FOLDER}/{name}",
            "file_count": len(files),
        })
    return results


@mcp.tool()
def read_file(file_path: str) -> dict:
    """Downloads and returns the text content of a supported OneDrive file."""
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext!r}")

    meta_url = f"{GRAPH_BASE}/me/drive/root:/{file_path}"
    meta = requests.get(meta_url, headers=get_headers()).json()
    size_bytes = meta.get("size", 0)

    local_path = _download_to_temp(file_path)
    try:
        content = _extract_text(local_path, ext)
    finally:
        os.unlink(local_path)

    return {
        "file_path": file_path,
        "content": content,
        "file_type": ext.lstrip("."),
        "size_bytes": size_bytes,
    }


@mcp.tool()
def get_metadata(file_path: str) -> dict:
    """Returns Graph API metadata for a OneDrive file. Never raises."""
    ext = Path(file_path).suffix.lower()
    try:
        url = f"{GRAPH_BASE}/me/drive/root:/{file_path}"
        response = requests.get(url, headers=get_headers())
        if response.status_code == 404:
            return {"file_path": file_path, "size_bytes": 0,
                    "last_modified_at": "", "file_type": ext.lstrip("."), "exists": False}
        response.raise_for_status()
        item = response.json()
        return {
            "file_path": file_path,
            "size_bytes": item.get("size", 0),
            "last_modified_at": _iso(item.get("lastModifiedDateTime", "")),
            "file_type": ext.lstrip("."),
            "exists": True,
        }
    except Exception:
        return {"file_path": file_path, "size_bytes": 0,
                "last_modified_at": "", "file_type": ext.lstrip("."), "exists": False}


@mcp.tool()
def list_files(folder_name: str) -> list[dict]:
    """Lists all supported files inside a named folder in ONEDRIVE_FOLDER."""
    url = f"{GRAPH_BASE}/me/drive/root:/{ONEDRIVE_FOLDER}/{folder_name}:/children"
    response = requests.get(url, headers=get_headers())
    response.raise_for_status()

    results = []
    for item in response.json().get("value", []):
        if "folder" in item:
            continue
        name = item["name"]
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        results.append({
            "file_path": f"{ONEDRIVE_FOLDER}/{folder_name}/{name}",
            "file_name": name,
            "file_type": ext.lstrip("."),
            "size_bytes": item.get("size", 0),
            "last_modified_at": _iso(item.get("lastModifiedDateTime", "")),
        })
    return results


if __name__ == "__main__":
    mcp.run()
