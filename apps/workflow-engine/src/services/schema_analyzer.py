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

import hashlib
import json
import logging
from typing import Any

from ..engine.llm_provider import call_llm
from .schema_inference import truncate_sample

logger = logging.getLogger(__name__)

_ANALYZER_MODEL = "gemini-2.5-flash"
_MAX_SAMPLE_CHARS = 500  # cap any single string value before sending to analyzer

# ---------------------------------------------------------------------------
# Module-level LRU cache for analyze_schema results.
# Keyed by MD5 of the serialized sample. Avoids redundant LLM calls when the
# same workflow output is analyzed across multiple chat turns.
# ---------------------------------------------------------------------------
_ANALYSIS_CACHE: dict[str, list[dict[str, Any]]] = {}
_ANALYSIS_CACHE_MAX = 64


def _prepare_sample(output: Any, max_items: int = 2) -> Any:
    """Truncate + cap strings in a single recursive pass."""
    return _truncate_and_cap(output, max_items=max_items, max_str=_MAX_SAMPLE_CHARS)


def _truncate_and_cap(
    value: Any, *, max_items: int, max_str: int, _depth: int = 0
) -> Any:
    """Single-pass: truncate arrays, cap string lengths, limit depth."""
    if _depth > 20:
        return "..."

    if isinstance(value, str):
        if len(value) > max_str:
            return value[:max_str] + f"... ({len(value)} chars total)"
        return value

    if isinstance(value, list):
        items = value[:max_items]
        # Unwrap n8n-style [{"json": {...}}] items
        if items and isinstance(items[0], dict) and "json" in items[0]:
            items = [item["json"] for item in items]
        return [_truncate_and_cap(v, max_items=max_items, max_str=max_str, _depth=_depth + 1) for v in items]

    if isinstance(value, dict):
        return {
            k: _truncate_and_cap(v, max_items=max_items, max_str=max_str, _depth=_depth + 1)
            for k, v in value.items()
        }

    return value


_ANALYZER_PROMPT = """\
Analyze sample JSON data. For each user-facing field output a JSON object:
- "field": dot-path (e.g. "items[].title")
- "type": string|number|boolean|array|object|null
- "content_type": plain_text|markdown|code|url|date|id|enum (use markdown for rich text with headers/lists)
- "render_hint": heading|paragraph|collapsible|code_block|link|badge|table_cell|hidden
- "avg_length": short|medium|long
- "example": max 60 char plain-text snippet, no newlines
- "priority": primary|secondary|meta

Return ONLY a JSON array. No markdown fences, no explanation.
Skip internal IDs, hashes, LLM metadata. Max 10 fields.
"""


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
        {
            "role": "user",
            "content": f"Analyze this API response data:\n\n```json\n{sample_json}\n```",
        },
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
            logger.info(
                "Schema analyzer: produced %d field descriptors",
                len(catalog),
            )
            return catalog

        logger.warning(
            "Schema analyzer: unexpected output type %s",
            type(catalog),
        )
        return None

    except json.JSONDecodeError as e:
        # Try incremental parsing — extract as many complete objects as possible
        try:
            # Find all complete JSON objects within the array
            objects = []
            depth = 0
            start_pos = None

            for i, ch in enumerate(text):
                if ch == "{":
                    if depth == 0:
                        start_pos = i
                    depth += 1

                elif ch == "}":
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

        logger.warning(
            "Schema analyzer: JSON parse failed, falling back to raw schema",
            exc_info=True,
        )
        return None

    except Exception:
        logger.warning(
            "Schema analyzer failed, will fall back to raw schema",
            exc_info=True,
        )
        return None


async def analyze_schema_cached(output: Any) -> list[dict[str, Any]] | None:
    """Cached wrapper around analyze_schema — avoids redundant LLM calls."""
    try:
        key = hashlib.md5(
            json.dumps(output, sort_keys=True, default=str).encode()
        ).hexdigest()
    except (TypeError, ValueError):
        # Unhashable output — skip cache
        return await analyze_schema(output)

    if key in _ANALYSIS_CACHE:
        logger.debug("Schema analyzer: cache hit for %s", key[:8])
        return _ANALYSIS_CACHE[key]

    result = await analyze_schema(output)
    if result is not None:
        # Evict oldest entries if cache is full
        if len(_ANALYSIS_CACHE) >= _ANALYSIS_CACHE_MAX:
            oldest = next(iter(_ANALYSIS_CACHE))
            del _ANALYSIS_CACHE[oldest]
        _ANALYSIS_CACHE[key] = result

    return result


def format_field_catalog(catalog: list[dict[str, Any]]) -> str:
    """Format a field catalog as a compact text block for the UI generator prompt."""

    lines = []

    for f in catalog:
        field = f.get("field", "?")
        hint = f.get("render_hint", "table_cell")
        ctype = f.get("content_type", "unknown")
        prio = f.get("priority", "secondary")

        tag = " *" if prio == "primary" else ""
        lines.append(f"- {field} ({ctype}, {hint}){tag}")

    return "\n".join(lines)
