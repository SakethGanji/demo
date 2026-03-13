"""Schema Analyzer — uses a fast LLM to produce compact field catalogs.

Given raw workflow execution output, produces per-field metadata:
  - content_type (markdown, code, url, date, id, plain_text, json, html, email, number, boolean)
  - render_hint (paragraph, collapsible, code_block, link, badge, table_cell, hidden, tag_list)
  - avg_length (short, medium, long)
  - truncated example

This replaces sending raw schemas + full sample data to the UI generator model,
dramatically reducing token usage and giving the UI model rendering guidance.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..engine.llm_provider import call_llm
from .schema_inference import truncate_sample

logger = logging.getLogger(__name__)

_ANALYZER_MODEL = "gemini-2.5-flash"
_MAX_SAMPLE_CHARS = 500  # cap any single string value before sending to analyzer


def _prepare_sample(output: Any, max_items: int = 2) -> Any:
    """Truncate sample data to keep analyzer input small."""
    sample = truncate_sample(output, max_items=max_items)
    return _cap_strings(sample, _MAX_SAMPLE_CHARS)


def _cap_strings(value: Any, max_len: int) -> Any:
    """Recursively cap string values to max_len chars."""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"... ({len(value)} chars total)"
        return value
    if isinstance(value, list):
        return [_cap_strings(v, max_len) for v in value]
    if isinstance(value, dict):
        return {k: _cap_strings(v, max_len) for k, v in value.items()}
    return value


_ANALYZER_PROMPT = """\
You are a data schema analyzer. Given sample JSON data from an API response, \
produce a compact field catalog describing each field's content and how a UI should render it.

For each field in the data, output a JSON object with these keys:
- "field": dot-path to the field (e.g. "results[].title", "metadata.author")
- "type": JSON type (string, number, boolean, array, object, null)
- "content_type": what the string actually contains. One of:
  plain_text, markdown, code, url, date, datetime, email, id, html, json_string, enum, number_string
  (use "plain_text" for short labels/names, "markdown" for formatted text with headers/lists/bold)
- "render_hint": how the UI should display it. One of:
  heading, paragraph, collapsible, code_block, link, badge, tag_list, table_cell, hidden, image, timestamp, list
  - Use "collapsible" for long text (>200 chars) like prompts, descriptions, markdown content
  - Use "hidden" for internal IDs, hashes, metadata the user doesn't need to see
  - Use "badge" for status fields, short enums, tags
  - Use "table_cell" for short values that work in a data table
- "avg_length": "short" (<50 chars), "medium" (50-500 chars), "long" (>500 chars)
- "example": one SHORT plain-text example (max 60 chars, NO newlines, NO code fences, NO special chars — just a simple string snippet)
- "priority": "primary" (show prominently), "secondary" (show but less prominent), "meta" (show on demand)

Return ONLY a valid JSON array of field objects. No explanation, no markdown fences.
IMPORTANT: The "example" field must be a short, single-line string. Do NOT put code blocks, newlines, or multi-line content in it.

If the data is an array of objects, describe the fields of a single item using "items[]." prefix notation.

Focus on USER-FACING fields only. Skip internal/infrastructure fields like tool call details, \
intermediate LLM metadata, function arguments, chain-of-thought internals, etc. \
Aim for 5-15 field descriptors max — only the fields a UI developer needs to display."""


async def analyze_schema(output: Any) -> list[dict[str, Any]] | None:
    """Analyze raw execution output and return a field catalog.

    Returns None if the analysis fails (caller should fall back to raw schema).
    """
    sample = _prepare_sample(output)
    sample_json = json.dumps(sample, indent=2, default=str)

    # Don't bother analyzing trivial data
    if len(sample_json) < 20:
        return None

    messages = [
        {"role": "system", "content": _ANALYZER_PROMPT},
        {"role": "user", "content": f"Analyze this API response data:\n\n```json\n{sample_json}\n```"},
    ]

    try:
        response = await call_llm(
            model=_ANALYZER_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=8000,
        )
        text = (response.text or "").strip()

        # Strip markdown fences if the model added them
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        # Extract just the JSON array if there's extra text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start : end + 1]

        catalog = json.loads(text)
        if isinstance(catalog, list):
            logger.info("Schema analyzer: produced %d field descriptors", len(catalog))
            return catalog

        logger.warning("Schema analyzer: unexpected output type %s", type(catalog))
        return None

    except json.JSONDecodeError as e:
        # Try incremental parsing — extract as many complete objects as possible
        try:
            import re
            # Find all complete JSON objects within the array
            objects = []
            depth = 0
            start_pos = None
            for i, ch in enumerate(text):
                if ch == '{':
                    if depth == 0:
                        start_pos = i
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0 and start_pos is not None:
                        obj_str = text[start_pos : i + 1]
                        try:
                            objects.append(json.loads(obj_str))
                        except json.JSONDecodeError:
                            pass  # skip malformed objects
                        start_pos = None

            if objects:
                logger.info(
                    "Schema analyzer: recovered %d field descriptors via incremental parse",
                    len(objects),
                )
                return objects
        except Exception:
            pass
        logger.warning("Schema analyzer: JSON parse failed, falling back to raw schema", exc_info=True)
        return None

    except Exception:
        logger.warning("Schema analyzer failed, will fall back to raw schema", exc_info=True)
        return None


def format_field_catalog(catalog: list[dict[str, Any]]) -> str:
    """Format a field catalog as a compact text block for the UI generator prompt."""
    lines = []
    for f in catalog:
        field = f.get("field", "?")
        content_type = f.get("content_type", "unknown")
        render_hint = f.get("render_hint", "table_cell")
        avg_length = f.get("avg_length", "short")
        example = f.get("example", "")
        priority = f.get("priority", "secondary")

        line = f"- **{field}** ({f.get('type', '?')}): {content_type}, render as {render_hint}, {avg_length}"
        if priority == "primary":
            line += " [PRIMARY]"
        elif priority == "meta":
            line += " [META]"
        if example:
            ex = str(example)[:80]
            line += f' — e.g. `{ex}`'
        lines.append(line)

    return "\n".join(lines)
