import asyncio
import json
import logging
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-opus-4-5"

AGENT_TOOLS = [
    {
        "name": "list_collections",
        "description": (
            "List all available knowledge base collections with their chunk counts and last-updated "
            "timestamps. Call this first when you don't know which collection(s) are relevant."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "query_collection",
        "description": "Search a specific collection for chunks relevant to a query. Use when you know which collection to search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Exact collection name"},
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["collection", "query"],
        },
    },
    {
        "name": "search_all_collections",
        "description": "Fan-out search across all collections simultaneously. Use for broad questions or when unsure which collection is relevant.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_collection_info",
        "description": (
            "Get metadata about a collection: chunk count, file list, last ingested timestamp. "
            "Use to assess whether a collection is likely to contain relevant content before querying it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Exact collection name"},
            },
            "required": ["collection"],
        },
    },
]


async def _execute_tool(name: str, inputs: dict, orchestrator) -> str:
    """Dispatch a tool call to the orchestrator and return a string result."""
    try:
        if name == "list_collections":
            cols = await orchestrator.get_available_collections()
            if not cols:
                return "No collections found."
            lines = [f"• {c['name']}: {c.get('vector_count', 0)} chunks" for c in cols]
            return "\n".join(lines)

        elif name == "query_collection":
            hits = await orchestrator.search(inputs["collection"], inputs["query"])
            if not hits:
                return "No results found."
            lines = [
                f"[{h['file_path'].split('/')[-1]}] {h['content']}"
                for h in hits
            ]
            return "\n\n".join(lines)

        elif name == "search_all_collections":
            hits = await orchestrator.search_all(inputs["query"])
            if not hits:
                return "No results found across any collection."
            lines = [
                f"[{h['collection_name']} / {h['file_path'].split('/')[-1]}] {h['content']}"
                for h in hits
            ]
            return "\n\n".join(lines)

        elif name == "get_collection_info":
            info = await orchestrator.get_collection_info(inputs["collection"])
            return (
                f"Collection: {info['name']}\n"
                f"Chunks: {info['vector_count']}\n"
                f"Status: {info['status']}\n"
                f"Exists: {info['exists']}"
            )

        else:
            return "Error: unknown tool"

    except Exception as exc:
        log.error("Tool %r failed: %s", name, exc)
        return f"Error executing {name}: {exc}"


async def run_agent(
    question: str,
    orchestrator,
    post_step,
    max_rounds: int = 3,
) -> str:
    """
    Run the agentic loop. Returns the final answer string.

    Each round:
    1. Call Claude with current messages + AGENT_TOOLS
    2. If response has tool_use blocks: execute each tool, post reasoning to Slack, append results, loop
    3. If response is text-only (stop_reason == "end_turn"): return the text
    4. If max_rounds exceeded: synthesize from gathered context
    """
    system = (
        f"You are a knowledge base agent. You have tools to explore and query knowledge base collections. "
        f"When given a question:\n\n"
        f"Think about which collection(s) might have relevant information\n"
        f"Use tools to retrieve evidence\n"
        f"Synthesize a precise answer citing specific filenames\n\n"
        f"Always explain your reasoning before each tool call. Be concise.\n"
        f"Max {max_rounds} rounds of tool use."
    )

    messages: list[dict] = [{"role": "user", "content": question}]

    for _ in range(max_rounds):
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            tools=AGENT_TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return next(
                (block.text for block in response.content if block.type == "text"),
                "",
            )

        # Append the assistant turn
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                await post_step(f"🔍 {block.text.strip()}")
            elif block.type == "tool_use":
                await post_step(
                    f"⚙️ Calling `{block.name}` with `{json.dumps(block.input)}`"
                )
                result = await _execute_tool(block.name, block.input, orchestrator)
                preview = result[:300] + ("..." if len(result) > 300 else "")
                asyncio.create_task(post_step(f"📄 Result: {preview}"))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})

    # Max rounds exceeded — force synthesis without tools
    messages.append({
        "role": "user",
        "content": "Please synthesize your findings into a final answer now.",
    })
    final = await _client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=system,
        messages=messages,
    )
    return next(
        (block.text for block in final.content if block.type == "text"),
        "I was unable to synthesize a final answer.",
    )
