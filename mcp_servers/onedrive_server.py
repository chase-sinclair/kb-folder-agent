import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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

# Graph and the SharePoint download host return 503s and reset connections often
# enough that a single failure must not be read as a bad file. Requests also
# defaulted to no timeout at all, which is how a stalled read became a
# multi-minute hang ("read timeout=None" in the quarantine table).
REQUEST_TIMEOUT = (10, 60)  # (connect, read) seconds
HTTP_RETRIES = 3
HTTP_BACKOFF = [1, 3, 8]  # seconds to wait before attempts 2, 3, ...
TRANSIENT_STATUS = {429, 500, 502, 503, 504}

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


_token_lock = threading.Lock()
_token_value: str | None = None
_token_expires_at: float = 0.0


def _acquire_token() -> tuple[str, int]:
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
            return result["access_token"], int(result.get("expires_in", 3600))

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
    return result["access_token"], int(result.get("expires_in", 3600))


def get_token(force_refresh: bool = False) -> str:
    """Return a cached access token, acquiring one only when missing or stale.

    Every HTTP call used to re-read and re-deserialise the on-disk MSAL cache and
    ask MSAL for a token, which is most of why `/kb list` took ~15 seconds.
    """
    global _token_value, _token_expires_at
    with _token_lock:
        now = time.monotonic()
        if not force_refresh and _token_value and now < _token_expires_at:
            return _token_value
        token, expires_in = _acquire_token()
        _token_value = token
        # Renew early so a long operation cannot straddle expiry.
        _token_expires_at = now + max(expires_in - 300, 60)
        return token


def get_headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


_local = threading.local()


def _session() -> requests.Session:
    """One pooled Session per thread: keeps TLS connections warm without sharing
    a Session across the threads list_folders() fans out over.
    """
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        _local.session = session
    return session


_pool_lock = threading.Lock()
_folder_pool: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    """One executor for the process, deliberately never shut down.

    The sessions above are thread-local, so a fresh executor per call threw the
    warm connections away with its threads and every worker paid a new TLS
    handshake — which is why a repeat `/kb list` cost as much as the first one.
    """
    global _folder_pool
    with _pool_lock:
        if _folder_pool is None:
            _folder_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="onedrive")
        return _folder_pool


def _request(method: str, url: str, *, retries: int = HTTP_RETRIES, **kwargs) -> requests.Response:
    """Graph request with a timeout, one token refresh on 401, and backoff on the
    transient statuses and connection resets these endpoints routinely return.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_error: Exception | None = None

    for attempt in range(max(retries, 1)):
        if attempt:
            time.sleep(HTTP_BACKOFF[min(attempt - 1, len(HTTP_BACKOFF) - 1)])
        try:
            response = _session().request(method, url, headers=get_headers(), **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            continue

        if response.status_code == 401:
            get_token(force_refresh=True)
            last_error = requests.exceptions.HTTPError(
                "401 Unauthorized from Graph", response=response
            )
            continue
        if response.status_code in TRANSIENT_STATUS:
            last_error = requests.exceptions.HTTPError(
                f"{response.status_code} {response.reason} from Graph", response=response
            )
            continue
        return response

    raise last_error if last_error else RuntimeError(f"No response for {method} {url}")


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
    """Download a OneDrive file to a local temp path and return that path.

    The retry wraps the whole transfer, not just the request: a reset usually
    arrives mid-stream, after the response headers looked fine, leaving a partial
    temp file that would otherwise be chunked as if it were the real document.
    """
    url = f"{GRAPH_BASE}/me/drive/root:/{file_path}:/content"
    suffix = Path(file_path).suffix
    last_error: Exception | None = None

    for attempt in range(HTTP_RETRIES):
        if attempt:
            time.sleep(HTTP_BACKOFF[min(attempt - 1, len(HTTP_BACKOFF) - 1)])
        tmp_path = None
        try:
            response = _request("GET", url, retries=1, stream=True)
            response.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp_path = tmp.name
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    tmp.write(chunk)
            finally:
                tmp.close()
            return tmp_path
        except (requests.exceptions.RequestException, OSError) as exc:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)
            last_error = exc

    raise last_error if last_error else RuntimeError(f"Download failed for {file_path}")


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
    response = _request("GET", url)
    response.raise_for_status()

    names = [item["name"] for item in response.json().get("value", []) if "folder" in item]
    if not names:
        return []

    # Warm the token once so the workers reuse it instead of each racing into
    # MSAL, then count files concurrently: this was one blocking round trip per
    # folder, run back to back.
    get_token()
    counts = list(_pool().map(lambda folder: len(list_files(folder)), names))

    return [
        {
            "name": name,
            "collection_name": _collection_name(name),
            "path": f"{ONEDRIVE_FOLDER}/{name}",
            "file_count": count,
        }
        for name, count in zip(names, counts)
    ]


@mcp.tool()
def read_file(file_path: str) -> dict:
    """Downloads and returns the text content of a supported OneDrive file."""
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext!r}")

    meta_url = f"{GRAPH_BASE}/me/drive/root:/{file_path}"
    meta = _request("GET", meta_url).json()
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
        response = _request("GET", url)
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
    response = _request("GET", url)
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
