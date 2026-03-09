"""Centralized tool schema hardening, validation, and JSON repair.

Provides a pipeline to prepare tool schemas for each LLM provider,
ensuring structural correctness that prevents malformed tool calls.

Public API:
    prepare_tools_for_provider(tools, provider) -> list[dict]
    validate_tool_definition(tool) -> list[str]
    safe_repair_json(raw, schema) -> dict | None
    validate_tool_args(args, schema) -> list[str]
"""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schema extraction (moved from llm_provider.py)
# ---------------------------------------------------------------------------

def _tool_to_schema(tool: Any) -> dict[str, Any]:
    """Convert a tool (callable or dict) to {name, description, parameters}."""
    if isinstance(tool, dict):
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": (
                tool.get("parameters")
                or tool.get("input_schema")
                or {}
            ),
        }
    if callable(tool):
        return _function_to_schema(tool)
    raise TypeError(f"Tool must be a callable or dict, got {type(tool)}")


def _function_to_schema(func: Any) -> dict[str, Any]:
    """Extract tool schema from a function's docstring and type hints."""
    import inspect

    try:
        from docstring_parser import parse as parse_docstring
    except ImportError:
        return {
            "name": func.__name__,
            "description": (func.__doc__ or "").strip().split("\n")[0],
            "parameters": {"type": "object", "properties": {}},
        }

    docstring = parse_docstring(func.__doc__ or "")
    type_map = {
        "str": "string", "int": "integer",
        "float": "number", "bool": "boolean",
    }

    properties: dict[str, Any] = {}
    required: list[str] = []

    sig = inspect.signature(func)
    for param_name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    for param_info in docstring.params:
        param_name = param_info.arg_name
        param_type = "string"
        if param_name in func.__annotations__:
            type_name = (
                str(func.__annotations__[param_name])
                .split("[")[0].split(".")[0].split("|")[-1].lower()
            )
            param_type = type_map.get(type_name, "string")
        properties[param_name] = {
            "type": param_type,
            "description": param_info.description or "",
        }

    return {
        "name": func.__name__,
        "description": docstring.short_description or "",
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _normalize_schema_types(schema: Any, to_case: str = "lower") -> Any:
    """Recursively normalize 'type' fields in a JSON schema."""
    if isinstance(schema, dict):
        result = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                result[key] = value.upper() if to_case == "upper" else value.lower()
            else:
                result[key] = _normalize_schema_types(value, to_case)
        return result
    if isinstance(schema, list):
        return [_normalize_schema_types(item, to_case) for item in schema]
    return schema


# ---------------------------------------------------------------------------
# Schema hardening
# ---------------------------------------------------------------------------

# Keywords that specific providers reject
_OPENAI_STRIP_KEYWORDS = frozenset({
    "$ref", "oneOf", "anyOf", "allOf", "patternProperties", "not",
    "if", "then", "else",
})

_GEMINI_STRIP_KEYWORDS = frozenset({
    "additionalProperties", "default", "examples", "$schema",
    "title", "$id", "$comment",
})


def ensure_complete_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Ensure a tool parameter schema has required structural fields.

    - Root must have "type": "object" and "properties"
    - Every property must have a "type" (default "string")
    """
    if not schema:
        return {"type": "object", "properties": {}}

    schema = dict(schema)

    # Ensure root type
    if "type" not in schema:
        schema["type"] = "object"

    # Ensure properties dict exists
    if schema.get("type", "").lower() in ("object", "OBJECT") and "properties" not in schema:
        schema["properties"] = {}

    # Fill missing type on properties
    props = schema.get("properties", {})
    if isinstance(props, dict):
        for prop_name, prop_def in props.items():
            if isinstance(prop_def, dict) and "type" not in prop_def:
                prop_def["type"] = "string"
                logger.debug(
                    "Schema property '%s' missing type, defaulted to 'string'",
                    prop_name,
                )

    return schema


def harden_schema(schema: dict[str, Any], provider: str) -> dict[str, Any]:
    """Recursively harden a JSON schema for a specific provider.

    Args:
        schema: Tool parameter schema dict.
        provider: One of "openai", "gemini", "anthropic", "llama".
    """
    if not isinstance(schema, dict):
        return schema

    schema = dict(schema)  # shallow copy to avoid mutating originals

    # Determine strip set
    if provider == "openai":
        strip_keys = _OPENAI_STRIP_KEYWORDS
    elif provider == "gemini":
        strip_keys = _GEMINI_STRIP_KEYWORDS
    else:
        strip_keys = frozenset()

    # Strip unsupported keywords
    for key in strip_keys:
        schema.pop(key, None)

    type_val = schema.get("type", "")
    is_object = isinstance(type_val, str) and type_val.lower() == "object"

    if is_object:
        # Add additionalProperties: false for OpenAI strict mode and Anthropic
        if provider in ("openai", "anthropic"):
            schema["additionalProperties"] = False

        # Ensure required lists ALL property keys for OpenAI strict mode
        if provider == "openai":
            props = schema.get("properties", {})
            if isinstance(props, dict) and props:
                schema["required"] = list(props.keys())

    # Recurse into properties
    if "properties" in schema and isinstance(schema["properties"], dict):
        schema["properties"] = {
            k: harden_schema(v, provider) if isinstance(v, dict) else v
            for k, v in schema["properties"].items()
        }

    # Recurse into items (for arrays)
    if "items" in schema and isinstance(schema["items"], dict):
        schema["items"] = harden_schema(schema["items"], provider)

    return schema


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------

def prepare_tools_for_provider(
    tools: list[Any], provider: str,
) -> list[dict[str, Any]]:
    """Convert and harden a list of tools for a specific LLM provider.

    Args:
        tools: List of tool dicts or callables.
        provider: "openai", "gemini", "anthropic", "llama".

    Returns:
        List of {name, description, parameters} dicts with hardened schemas.
    """
    to_case = "upper" if provider == "gemini" else "lower"
    result = []

    for t in tools:
        schema = _tool_to_schema(t)
        params = schema["parameters"] or {"type": "object", "properties": {}}
        params = ensure_complete_schema(params)
        params = harden_schema(params, provider)
        params = _normalize_schema_types(params, to_case=to_case)
        result.append({
            "name": schema["name"],
            "description": schema["description"],
            "parameters": params,
        })

    return result


# ---------------------------------------------------------------------------
# Build-time validation
# ---------------------------------------------------------------------------

_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def validate_tool_definition(tool: dict[str, Any]) -> list[str]:
    """Validate a tool definition at build time. Returns warnings (does not block)."""
    warnings: list[str] = []

    name = tool.get("name", "")
    if not name:
        warnings.append("Tool has empty name")
    elif not _TOOL_NAME_PATTERN.match(name):
        warnings.append(
            f"Tool name '{name}' contains invalid characters "
            f"(must match [a-zA-Z_][a-zA-Z0-9_]*)"
        )

    desc = tool.get("description", "")
    if not desc:
        warnings.append(f"Tool '{name}' has empty description")

    schema = tool.get("input_schema") or tool.get("parameters") or {}
    if isinstance(schema, dict):
        props = schema.get("properties", {})
        if isinstance(props, dict):
            for prop_name, prop_def in props.items():
                if isinstance(prop_def, dict) and "type" not in prop_def:
                    warnings.append(
                        f"Tool '{name}': property '{prop_name}' missing 'type'"
                    )

    return warnings


# ---------------------------------------------------------------------------
# JSON repair
# ---------------------------------------------------------------------------

def safe_repair_json(
    raw: str, schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Attempt to repair malformed JSON. Returns parsed dict or None.

    Uses the json-repair library to handle: missing quotes, trailing commas,
    single quotes, truncated JSON, comments, mixed formats.
    """
    try:
        from json_repair import loads as repair_loads
    except ImportError:
        logger.debug("json-repair not installed, skipping repair")
        return None

    try:
        result = repair_loads(raw)
    except Exception:
        return None

    if not isinstance(result, dict):
        return None

    # Post-repair: validate against schema if provided
    if schema:
        errors = validate_tool_args(result, schema)
        if errors:
            logger.debug("Repaired JSON failed schema validation: %s", errors)
            return None

    return result


def validate_tool_args(
    args: dict[str, Any], schema: dict[str, Any],
) -> list[str]:
    """Validate tool arguments against a JSON schema. Returns error strings."""
    errors: list[str] = []
    if not schema:
        return errors

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for req in required:
        if req not in args:
            errors.append(f"Missing required field: '{req}'")

    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for key, value in args.items():
        if key in properties:
            expected_type = properties[key].get("type", "").lower()
            if expected_type and expected_type in type_map:
                py_type = type_map[expected_type]
                if not isinstance(value, py_type):
                    if expected_type == "number" and isinstance(value, int):
                        continue
                    errors.append(
                        f"Field '{key}': expected {expected_type}, "
                        f"got {type(value).__name__}"
                    )

    return errors
