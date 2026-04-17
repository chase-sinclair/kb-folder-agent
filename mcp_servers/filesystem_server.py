import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

WATCHED_FOLDER = Path(os.environ["WATCHED_FOLDER"])

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".md", ".txt",
    ".xlsx", ".csv",
    ".py", ".js", ".ts", ".go", ".rs",
}

mcp = FastMCP("filesystem")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collection_name(folder_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", folder_name.lower()).strip("_")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _supported_files(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]


def _extract_text(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in {".md", ".txt"}:
        return file_path.read_text(encoding="utf-8")
    if ext == ".pdf":
        import pdfplumber
        pages = []
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n\n".join(pages)
    if ext == ".docx":
        from docx import Document
        doc = Document(str(file_path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    raise ValueError(f"Unsupported file type for read_file: {ext!r}")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_folders() -> list[dict]:
    """Lists all top-level folders inside WATCHED_FOLDER."""
    results = []
    for entry in sorted(WATCHED_FOLDER.iterdir()):
        if not entry.is_dir():
            continue
        results.append({
            "name": entry.name,
            "collection_name": _collection_name(entry.name),
            "path": str(entry),
            "file_count": len(_supported_files(entry)),
        })
    return results


@mcp.tool()
def read_file(file_path: str) -> dict:
    """Reads and returns the text content of a supported file."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext!r}")
    content = _extract_text(path)
    return {
        "file_path": file_path,
        "content": content,
        "file_type": ext.lstrip("."),
        "size_bytes": path.stat().st_size,
    }


@mcp.tool()
def get_metadata(file_path: str) -> dict:
    """Returns OS-level metadata for a file."""
    path = Path(file_path)
    exists = path.exists()
    if not exists:
        return {
            "file_path": file_path,
            "size_bytes": 0,
            "last_modified_at": "",
            "file_type": path.suffix.lower().lstrip("."),
            "exists": False,
        }
    stat = path.stat()
    return {
        "file_path": file_path,
        "size_bytes": stat.st_size,
        "last_modified_at": _iso(stat.st_mtime),
        "file_type": path.suffix.lower().lstrip("."),
        "exists": True,
    }


@mcp.tool()
def list_files(folder_name: str) -> list[dict]:
    """Lists all supported files inside a named top-level folder."""
    folder = WATCHED_FOLDER / folder_name
    if not folder.is_dir():
        raise ValueError(f"Folder not found: {folder_name!r}")
    results = []
    for path in sorted(_supported_files(folder)):
        stat = path.stat()
        results.append({
            "file_path": str(path),
            "file_name": path.name,
            "file_type": path.suffix.lower().lstrip("."),
            "size_bytes": stat.st_size,
            "last_modified_at": _iso(stat.st_mtime),
        })
    return results


if __name__ == "__main__":
    mcp.run()
