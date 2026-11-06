"""LLM reranker for retrieved chunks.

Leaf module by design: it imports nothing from `agent/`, so `agent/orchestrator.py`
can depend on it without creating a cycle with `agent/rag.py` (which imports
orchestrator), and orchestrator stays importable without `ANTHROPIC_API_KEY` —
both the watcher path and `evals/runner.py` import it.

Hard contract: `rerank()` never raises. Every failure path logs a warning and
returns the original retrieval order, mirroring the dense-only fallback in
`mcp_servers/vectordb_server.py`.
"""

import asyncio
import logging
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 512
DEFAULT_CHUNK_CHARS = 700
DEFAULT_TIMEOUT_SECONDS = 8.0

_SYSTEM_RERANK = (
    "You are a search-result reranker. Order the candidates from most to least "
    "relevant to the query. Output ONLY candidate numbers separated by commas, "
    "most relevant first. Include every candidate number exactly once. No prose."
)

_INT_PATTERN = re.compile(r"\d+")

_client: anthropic.AsyncAnthropic | None = None


# ---------------------------------------------------------------------------
# Configuration — read at CALL time, not import time, because the eval runner
# forces RERANK_ENABLED per process and restores it afterwards.
# ---------------------------------------------------------------------------

def rerank_enabled() -> bool:
    return os.environ.get("RERANK_ENABLED", "").strip().lower() == "true"


def model_name() -> str:
    return os.environ.get("RERANK_MODEL", "").strip() or DEFAULT_MODEL


def _chunk_chars() -> int:
    raw = os.environ.get("RERANK_CHUNK_CHARS", "").strip()
    if not raw:
        return DEFAULT_CHUNK_CHARS
    try:
        value = int(raw)
    except ValueError:
        log.warning("Invalid RERANK_CHUNK_CHARS %r — using %d", raw, DEFAULT_CHUNK_CHARS)
        return DEFAULT_CHUNK_CHARS
    return value if value > 0 else DEFAULT_CHUNK_CHARS


def _timeout_seconds() -> float:
    raw = os.environ.get("RERANK_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning("Invalid RERANK_TIMEOUT_SECONDS %r — using %.1f", raw, DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def _get_client() -> anthropic.AsyncAnthropic:
    """Lazy singleton — instantiating at import would require ANTHROPIC_API_KEY
    on the watcher and eval import paths."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_order(text: str, n: int) -> list[int] | None:
    """Parse a model ordering response into a full permutation of range(n).

    Steps, in order: collect every integer, keep only 1..n, drop duplicates
    preserving first occurrence, bail out to None if nothing usable is left,
    then append every index the model omitted in its ORIGINAL retrieval order.

    That last step is load-bearing. It guarantees len(result) == n, which is
    what guarantees rerank() returns min(top_k, len(hits)) items. Without it a
    chatty or truncated response could shrink a 10-chunk pool to 4 and trip the
    `len(results) < 5` gate in agent/rag.py's find_gaps().
    """
    if n <= 0:
        return None

    seen: set[int] = set()
    order: list[int] = []
    for token in _INT_PATTERN.findall(text or ""):
        value = int(token)
        if not 1 <= value <= n:
            continue
        index = value - 1
        if index in seen:
            continue
        seen.add(index)
        order.append(index)

    if not order:
        return None

    order.extend(index for index in range(n) if index not in seen)
    return order


def _basename(file_path: str) -> str:
    return (file_path or "unknown").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _build_prompt(query: str, hits: list[dict]) -> str:
    limit = _chunk_chars()
    lines = [f"Query: {query}", "", "Candidates:"]
    for position, hit in enumerate(hits, start=1):
        content = " ".join((hit.get("content") or "").split())[:limit]
        lines.append(f"[{position}] {_basename(hit.get('file_path', ''))}")
        lines.append(content)
        lines.append("")
    lines.append(
        f"Output all {len(hits)} candidate numbers, most relevant first, separated by commas."
    )
    return "\n".join(lines)


def _response_text(response) -> str:
    parts = []
    for block in response.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def rerank(query: str, hits: list[dict], top_k: int) -> list[dict]:
    """Reorder `hits` by LLM-judged relevance and return the top `top_k`.

    Never raises. On timeout, API error, or an unparseable response it returns
    `hits[:top_k]`, i.e. the original retrieval order.
    """
    if top_k <= 0:
        return []
    if len(hits) <= 1:
        return hits[:top_k]

    model = model_name()
    try:
        response = await asyncio.wait_for(
            _get_client().messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                output_config={"effort": "low"},
                thinking={"type": "disabled"},
                system=_SYSTEM_RERANK,
                messages=[{"role": "user", "content": _build_prompt(query, hits)}],
            ),
            timeout=_timeout_seconds(),
        )
    except Exception as exc:
        log.warning(
            "Rerank call failed (%s: %s) — keeping retrieval order for query %r",
            type(exc).__name__, exc, query,
        )
        return hits[:top_k]

    try:
        order = _parse_order(_response_text(response), len(hits))
    except Exception as exc:
        log.warning("Rerank response unreadable (%s) — keeping retrieval order", exc)
        return hits[:top_k]

    if order is None:
        log.warning(
            "Rerank returned no usable ordering (model %s) — keeping retrieval order for query %r",
            model, query,
        )
        return hits[:top_k]

    reordered = [hits[index] for index in order][:top_k]
    moved = sum(
        1 for position, hit in enumerate(reordered)
        if position >= len(hits) or hits[position] is not hit
    )
    log.info(
        "Reranked %d candidate(s) to top %d via %s (%d position(s) changed)",
        len(hits), len(reordered), model, moved,
    )
    return reordered
