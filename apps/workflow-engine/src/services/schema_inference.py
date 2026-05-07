"""Infer JSON Schema descriptors and compact response summaries.

The summary structure is stored on `ApiTestExecutionModel.response_summary` so
the LLM-context renderer never has to re-decode raw response bytes on every
chat turn.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

_MAX_DEPTH = 20

# Cap how big a body we'll attempt to parse for schema inference. Bigger
# than this we just emit a short snippet so we don't hang the server on
# a multi-MB response.
_MAX_PARSE_BYTES = 10 * 1024 * 1024


def infer_json_schema(value: Any, *, _depth: int = 0) -> dict[str, Any]:
    """Walk a JSON value and produce a JSON-Schema-like descriptor.

    Handles the n8n-style item wrapper: [{"json": {...}}, ...].
    Stops recursing beyond _MAX_DEPTH to avoid stack overflow on deeply nested data.
    """
    if _depth > _MAX_DEPTH:
        return {"type": "unknown"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        first = value[0]
        # Unwrap n8n-style [{"json": {...}}] items
        if isinstance(first, dict) and "json" in first and len(first) <= 2:
            return {"type": "array", "items": infer_json_schema(first["json"], _depth=_depth + 1)}
        return {"type": "array", "items": infer_json_schema(first, _depth=_depth + 1)}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {k: infer_json_schema(v, _depth=_depth + 1) for k, v in value.items()},
        }
    return {"type": "unknown"}


def truncate_sample(
    output: Any,
    *,
    max_str_len: int = 200,
    max_object_array_items: int = 1,
    max_primitive_array_items: int = 8,
    _depth: int = 0,
) -> Any:
    """Return a truncated sample suitable for inclusion in an LLM prompt.

    Goal: preserve enough concrete value signal (enums, formats, ID prefixes)
    while bounding token cost. Rules:
      - Long strings → first `max_str_len` chars + a marker noting total length.
      - Arrays of objects → cap at `max_object_array_items` (schema already
        encodes the repeated shape; one example is enough).
      - Arrays of primitives → cap at `max_primitive_array_items` (preserves
        enum-like signals: ["pending", "shipped", ...]).
      - Anything deeper than `_MAX_DEPTH` collapses to a marker.
    """
    if _depth > _MAX_DEPTH:
        return "<truncated: depth>"

    if isinstance(output, str):
        if len(output) > max_str_len:
            return f"{output[:max_str_len]}… <truncated, {len(output)} chars total>"
        return output

    if isinstance(output, list):
        if not output:
            return []
        # Unwrap n8n items so the LLM sees clean objects
        if isinstance(output[0], dict) and "json" in output[0] and len(output[0]) <= 2:
            unwrapped = [item.get("json") for item in output]
            return truncate_sample(
                unwrapped,
                max_str_len=max_str_len,
                max_object_array_items=max_object_array_items,
                max_primitive_array_items=max_primitive_array_items,
                _depth=_depth,
            )

        is_object_array = isinstance(output[0], (dict, list))
        cap = max_object_array_items if is_object_array else max_primitive_array_items
        truncated = [
            truncate_sample(
                v,
                max_str_len=max_str_len,
                max_object_array_items=max_object_array_items,
                max_primitive_array_items=max_primitive_array_items,
                _depth=_depth + 1,
            )
            for v in output[:cap]
        ]
        if len(output) > cap:
            truncated.append(f"… <{len(output) - cap} more items omitted>")
        return truncated

    if isinstance(output, dict):
        return {
            k: truncate_sample(
                v,
                max_str_len=max_str_len,
                max_object_array_items=max_object_array_items,
                max_primitive_array_items=max_primitive_array_items,
                _depth=_depth + 1,
            )
            for k, v in output.items()
        }

    return output


# ── Response summary ─────────────────────────────────────────────────


def summarize_response(
    *,
    response_body_b64: str | None,
    content_type: str | None,
    response_truncated: bool = False,
    response_headers: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compute a compact, JSON-serializable summary of a captured HTTP response.

    The summary is stored on the row and rendered into the LLM prompt later,
    so we don't redo decode/parse work on every chat turn.

    Returns None when there's nothing useful to summarize (no body captured).

    Summary `kind` values:
      json   → schema + sample
      csv    → header + row_count + delimiter (handles TSV)
      xml    → root + first-level tag names
      yaml   → top-level keys
      text   → snippet + total_chars (covers HTML, plaintext, JSON-parse-fail)
      binary → just metadata; body bytes are intentionally not decoded
    """
    if not response_body_b64:
        return None

    ctype = (content_type or "").lower()
    is_json = "json" in ctype
    is_csv = "csv" in ctype or "tab-separated" in ctype
    is_xml = (
        ctype.startswith("application/xml")
        or ctype.endswith("+xml")
        or ctype.startswith("text/xml")
    )
    is_yaml = "yaml" in ctype
    is_text = is_json or is_csv or is_xml or is_yaml or ctype.startswith("text/")

    headers = response_headers or {}

    # Binary path: never decode
    if not is_text:
        out: dict[str, Any] = {"kind": "binary"}
        filename = _extract_filename(headers)
        if filename:
            out["filename"] = filename
        if response_truncated:
            out["partial"] = True
        return out

    # Decode the body. Wrap broadly because malformed b64 / unexpected
    # content shouldn't bring down a chat turn.
    try:
        raw = base64.b64decode(response_body_b64)
    except Exception as e:
        return {"kind": "binary", "decode_error": f"base64: {type(e).__name__}"}

    if not raw:
        return None

    # Don't try to parse multi-MB bodies — emit a short snippet and bail.
    if len(raw) > _MAX_PARSE_BYTES:
        head = raw[:1024].decode("utf-8", errors="replace")
        return {
            "kind": "text",
            "snippet": head,
            "total_chars": len(raw),
            "partial": True,
            "decode_error": "body too large to summarize",
        }

    # utf-8-sig strips BOM if present
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # text-ish content-type but body isn't utf-8 — treat as binary
        return {"kind": "binary", "decode_error": "utf-8 decode failed"}

    if not decoded:
        return None

    if is_json:
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as e:
            out = {
                "kind": "text",
                "snippet": _trim_text(decoded, 300),
                "total_chars": len(decoded),
                "decode_error": f"JSON parse failed: {e.msg}",
            }
            if response_truncated:
                out["partial"] = True
            return out

        out = {
            "kind": "json",
            "schema": infer_json_schema(parsed),
            "sample": truncate_sample(parsed),
        }
        if response_truncated:
            out["partial"] = True
            out["note"] = "Body was truncated at capture; schema may be incomplete."
        return out

    if is_csv:
        delim = "\t" if "tab-separated" in ctype else ","
        first_nl = decoded.find("\n")
        header = (decoded if first_nl == -1 else decoded[:first_nl]).rstrip("\r\n ")
        body_after = "" if first_nl == -1 else decoded[first_nl + 1:]
        # Approximate row count: count line breaks in body, plus 1 if it
        # ends without a trailing newline. Won't be exact for CSVs with
        # quoted multi-line cells, but good enough for an LLM hint.
        body_after = body_after.rstrip("\r\n")
        row_count = body_after.count("\n") + (1 if body_after else 0)
        out = {
            "kind": "csv",
            "header": header,
            "row_count": row_count,
            "delimiter": delim,
        }
        if response_truncated:
            out["partial"] = True
        return out

    if is_xml:
        # The regex requires a letter after `<`, so DOCTYPE (`<!`),
        # comments (`<!--`), and processing instructions (`<?xml`) are
        # naturally skipped.
        root_match = re.search(r"<\s*([A-Za-z_][\w:.\-]*)", decoded)
        root = root_match.group(1) if root_match else None
        tags: list[str] = []
        if root_match:
            inner = decoded[root_match.end():]
            for m in re.finditer(r"<\s*([A-Za-z_][\w:.\-]*)", inner):
                t = m.group(1)
                if t == root or t in tags:
                    continue
                tags.append(t)
                if len(tags) >= 8:
                    break
        out = {"kind": "xml", "root": root, "tags": tags}
        if response_truncated:
            out["partial"] = True
        return out

    if is_yaml:
        keys: list[str] = []
        for line in decoded.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped == "---" or stripped == "...":
                continue
            if not line[0].isspace() and ":" in line:
                key = line.split(":", 1)[0].strip()
                if key and key not in keys:
                    keys.append(key)
                if len(keys) >= 12:
                    break
        out = {"kind": "yaml", "top_keys": keys}
        if response_truncated:
            out["partial"] = True
        return out

    # Generic text/* (covers text/plain, text/html, anything text-ish we
    # don't have a special template for).
    out = {
        "kind": "text",
        "snippet": _trim_text(decoded, 300),
        "total_chars": len(decoded),
    }
    if response_truncated:
        out["partial"] = True
    return out


def _trim_text(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return f"{s[:n]}… <truncated, {len(s)} chars total>"


def _extract_filename(headers: dict[str, Any]) -> str | None:
    """Pull a filename hint from a Content-Disposition header.

    Handles `filename="x.pdf"` and `filename=x.pdf`. Skips RFC 5987
    `filename*=UTF-8''...` form (rare in practice; falls through if both
    are present and only filename* is set).
    """
    disp = headers.get("content-disposition") or headers.get("Content-Disposition")
    if not disp or not isinstance(disp, str):
        return None
    m = re.search(r'filename\s*=\s*"([^"]+)"', disp, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", disp, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None
