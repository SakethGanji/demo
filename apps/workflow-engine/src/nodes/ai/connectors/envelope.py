"""Shaping a remote tool result into something an agent loop can hold.

Two constraints, from opposite directions.

*From below*: the analytics MCP server caps a tool response at 60,000
characters, and its tools happily return that much (``query_rows`` on a wide
sheet). MCP itself caps nothing.

*From above*: ``AIAgentNode._compress_tool_result`` (``ai_agent.py:2191``)
truncates anything over 8,000 characters — and for non-JSON text its fallback
is head+tail with the middle replaced by an ellipsis marker. Applied to a
pipe-delimited table that means the header row survives, the last rows survive,
and the rows in between vanish silently mid-line. A model reading that cannot
tell it is looking at two disjoint fragments.

So results are cut *here*, to 6,000 characters, **at a line boundary**, with an
explicit count of what was dropped and a pointer at the right fix (narrow the
query, page it). Under the engine's threshold, so the engine's blunter cut
never runs.

One further rule: a single text block is returned as a **raw string**. Wrapping
it in ``{"result": ...}`` would make ``_compress_tool_result`` parse it as JSON
and take the object branch, and it costs the model a level of indirection for
nothing.
"""

from __future__ import annotations

import json
from typing import Any

#: Below ai_agent.py's 8,000-char compression threshold, with room for the
#: truncation notice itself.
MAX_RESULT_CHARS = 6_000

#: Never cut a table down to just its header: if the last newline is this far
#: back, take the hard cut instead.
_MIN_LINE_CUT_RATIO = 0.5


def cap_text(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """Truncate at a line boundary, and say how much was dropped."""
    if not isinstance(text, str) or len(text) <= limit:
        return text
    head = text[:limit]
    cut = head.rfind("\n")
    if cut < int(limit * _MIN_LINE_CUT_RATIO):
        cut = limit
    kept = text[:cut].rstrip()
    dropped = len(text) - len(kept)
    lines_dropped = text.count("\n", cut)
    note = (
        f"\n\n[truncated: {dropped} more characters ({lines_dropped} more lines) "
        f"were not shown. Narrow the request — filter, project fewer columns, "
        f"or page — rather than asking for everything again.]"
    )
    return kept + note


def _block_to_text(block: dict[str, Any]) -> str | None:
    kind = block.get("type")
    if kind == "text":
        text = block.get("text")
        return text if isinstance(text, str) else None
    if kind == "resource":
        resource = block.get("resource") or {}
        text = resource.get("text")
        if isinstance(text, str):
            uri = resource.get("uri")
            return f"[{uri}]\n{text}" if uri else text
        return f"[binary resource {resource.get('uri', '')} ({resource.get('mimeType', 'unknown')})]"
    if kind == "resource_link":
        return f"[resource {block.get('uri', '')} — {block.get('name', '')}]"
    if kind in ("image", "audio"):
        return f"[{kind} content, {block.get('mimeType', 'unknown')}, not renderable as text]"
    return None


def shape_result(result: Any, *, limit: int = MAX_RESULT_CHARS) -> Any:
    """Turn an MCP ``CallToolResult`` into a tool return value.

    * ``isError: true`` -> ``{"error": ...}``, so the model sees a failure it
      can correct rather than prose it might quote as an answer.
    * all-text content -> one raw string.
    * anything else -> a dict, because the shape genuinely is not a string.
    """
    if not isinstance(result, dict):
        return cap_text(result) if isinstance(result, str) else result

    content = result.get("content")
    blocks = content if isinstance(content, list) else []
    texts = [t for t in (_block_to_text(b) for b in blocks if isinstance(b, dict)) if t is not None]
    joined = "\n".join(texts).strip()

    if result.get("isError"):
        message = joined or "the tool reported an error but returned no detail"
        return {"error": cap_text(message, limit)}

    if joined:
        return cap_text(joined, limit)

    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        # A lone {"result": "..."} wrapper (FastMCP's output schema for a
        # string-returning tool) is the string, not a structure.
        if set(structured) == {"result"} and isinstance(structured["result"], str):
            return cap_text(structured["result"], limit)
        return cap_text(json.dumps(structured, default=str, ensure_ascii=False), limit)

    if blocks:
        return cap_text(json.dumps(blocks, default=str, ensure_ascii=False), limit)

    return "(the tool returned no content)"


def error_envelope(message: str, **extra: Any) -> dict[str, Any]:
    """The one error shape every connector tool returns.

    A dict with ``error``: ``ai_agent`` renders a returned value into the
    transcript verbatim, so this is what the model reads and self-corrects
    from. Raising instead would abort the agent's turn.
    """
    envelope: dict[str, Any] = {"error": cap_text(str(message), MAX_RESULT_CHARS)}
    for key, value in extra.items():
        if value is not None:
            envelope[key] = value
    return envelope
