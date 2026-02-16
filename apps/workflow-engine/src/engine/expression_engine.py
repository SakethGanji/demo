"""
Expression engine for resolving {{ }} template expressions.

Uses simpleeval for safe expression evaluation (no eval() or exec()).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from simpleeval import EvalWithCompoundTypes, DEFAULT_FUNCTIONS, DEFAULT_OPERATORS

from .types import NodeData

logger = logging.getLogger(__name__)


@dataclass
class ExpressionContext:
    """Context for expression evaluation."""

    json_data: dict[str, Any]  # $json
    input_data: list[NodeData]  # $input
    node_data: dict[str, dict[str, Any]]  # $node
    env: dict[str, str | None]  # $env
    execution: dict[str, str]  # $execution
    item_index: int  # $itemIndex


class ExpressionEngine:
    """
    Safe expression parser that doesn't use eval() or exec().

    Uses simpleeval library with a whitelist of allowed functions.
    """

    def __init__(self) -> None:
        self._setup_evaluator()

    def _setup_evaluator(self) -> None:
        """Set up the safe evaluator with allowed functions."""
        # Use EvalWithCompoundTypes to support list [...] and dict {...} literals
        self.evaluator = EvalWithCompoundTypes()
        self.evaluator.operators = DEFAULT_OPERATORS.copy()

        # Add safe helper functions
        self.evaluator.functions = {
            **DEFAULT_FUNCTIONS,
            # Type conversion
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            # String functions
            "lower": lambda s: str(s).lower(),
            "upper": lambda s: str(s).upper(),
            "trim": lambda s: str(s).strip(),
            "split": lambda s, sep=" ": str(s).split(sep),
            "join": lambda arr, sep="": sep.join(str(x) for x in arr),
            "includes": lambda s, search: search in str(s),
            "replace": lambda s, old, new: str(s).replace(old, new),
            "substring": lambda s, start, end=None: str(s)[start:end],
            "length": lambda x: len(x),
            "startswith": lambda s, prefix: str(s).startswith(prefix),
            "endswith": lambda s, suffix: str(s).endswith(suffix),
            # Array functions
            "first": lambda arr: arr[0] if arr else None,
            "last": lambda arr: arr[-1] if arr else None,
            "at": lambda arr, idx: arr[idx] if 0 <= idx < len(arr) else None,
            "slice": lambda arr, start, end=None: arr[start:end],
            "reverse": lambda arr: list(reversed(arr)),
            "sort": lambda arr: sorted(arr),
            "unique": lambda arr: list(dict.fromkeys(arr)),
            "flatten": lambda arr: [item for sublist in arr for item in sublist],
            # Math functions
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "round": round,
            "floor": math.floor,
            "ceil": math.ceil,
            # Date functions
            "now": lambda: int(datetime.now().timestamp() * 1000),
            "date_now": lambda: datetime.now().isoformat(),
            "timestamp": lambda: int(datetime.now().timestamp()),
            # JSON functions
            "json_stringify": lambda v: json.dumps(v),
            "json_parse": lambda s: json.loads(s) if s else None,
            # Type checking
            "typeof": lambda v: type(v).__name__,
            "is_array": lambda v: isinstance(v, list),
            "is_empty": lambda v: v is None or v == "" or (isinstance(v, list) and len(v) == 0),
            "is_none": lambda v: v is None,
            # Object functions
            "keys": lambda d: list(d.keys()) if isinstance(d, dict) else [],
            "values": lambda d: list(d.values()) if isinstance(d, dict) else [],
            "get": lambda d, key, default=None: d.get(key, default) if isinstance(d, dict) else default,
            # Array manipulation
            "concat": lambda arr, item: (arr if isinstance(arr, list) else []) + [item],
            "extend": lambda arr1, arr2: (arr1 if isinstance(arr1, list) else []) + (arr2 if isinstance(arr2, list) else []),
            "push": lambda arr, item: (arr if isinstance(arr, list) else []) + [item],  # Alias for concat
            # Object manipulation
            "merge": lambda a, b: {**a, **b} if isinstance(a, dict) and isinstance(b, dict) else (b if isinstance(b, dict) else a),
            "omit": lambda d, *keys: {k: v for k, v in d.items() if k not in keys} if isinstance(d, dict) else d,
            "pick": lambda d, *keys: {k: d[k] for k in keys if k in d} if isinstance(d, dict) else {},
            # Coalesce / default
            "coalesce": lambda *args: next((a for a in args if a is not None), None),
            "default": lambda val, fallback: fallback if val is None else val,
        }

    def resolve(self, value: Any, context: ExpressionContext, skip_json: bool = False) -> Any:
        """
        Resolve all {{ }} expressions in a value.

        Handles strings, objects, and arrays recursively.

        Args:
            value: The value to resolve expressions in
            context: Expression context with $json, $node, etc.
            skip_json: If True, leave $json expressions unresolved (for per-item evaluation)
        """
        if isinstance(value, str):
            return self._resolve_string(value, context, skip_json)

        if isinstance(value, list):
            return [self.resolve(item, context, skip_json) for item in value]

        if isinstance(value, dict):
            return {key: self.resolve(val, context, skip_json) for key, val in value.items()}

        return value

    def _resolve_string(self, string: str, context: ExpressionContext, skip_json: bool = False) -> Any:
        """
        Resolve expressions in a string.

        Supports: {{ $json.field }}, {{ $node["Name"].json.field }}
        """
        trimmed = string.strip()

        # Check if entire string is a single expression (return typed value)
        if trimmed.startswith("{{") and trimmed.endswith("}}"):
            inner = trimmed[2:-2].strip()
            # Check if it's a single expression without other text
            if "{{" not in inner:
                # Skip $json expressions if requested (for per-item evaluation later)
                if skip_json and ("$json" in inner or "$itemIndex" in inner):
                    return string  # Return original template
                return self._evaluate(inner, context)

        # Multiple expressions or mixed content - return string
        return self._replace_expressions(string, context, skip_json)

    def _replace_expressions(self, string: str, context: ExpressionContext, skip_json: bool = False) -> str:
        """Replace all {{ }} expressions in a string with evaluated values."""
        pattern = r"\{\{(.+?)\}\}"

        def replacer(match: re.Match[str]) -> str:
            expr = match.group(1).strip()
            # Skip $json expressions if requested
            if skip_json and ("$json" in expr or "$itemIndex" in expr):
                return match.group(0)  # Return original {{ expression }}
            result = self._evaluate(expr, context)
            return self._stringify(result)

        return re.sub(pattern, replacer, string)

    def _evaluate(self, expression: str, context: ExpressionContext) -> Any:
        """Evaluate a single expression safely using simpleeval."""
        try:
            # Transform n8n-style expressions to Python-compatible syntax
            transformed = self._transform_expression(expression)

            # Build evaluation context
            eval_context = self._build_eval_context(context)

            self.evaluator.names = eval_context
            return self.evaluator.eval(transformed)

        except Exception as e:
            logger.warning("Expression evaluation failed: %s (expression: %s)", e, expression)
            return f"[Expression Error: {e}]"

    def _transform_expression(self, expression: str) -> str:
        """Transform n8n-style expressions to Python-compatible syntax."""
        result = expression

        # Unescape JSON-escaped quotes so $node["Name"] regexes match
        # (inside JSON strings, quotes arrive as \" which breaks the patterns)
        result = result.replace('\\"', '"').replace("\\'", "'")

        # Handle JavaScript booleans -> Python booleans
        # Use word boundaries to avoid replacing inside strings
        result = re.sub(r'\bfalse\b', 'False', result)
        result = re.sub(r'\btrue\b', 'True', result)
        result = re.sub(r'\bnull\b', 'None', result)

        # Handle $node["NodeName"].json.field.nested or $node['NodeName'].json.field
        # Need to sanitize node names (replace spaces with underscores)
        def sanitize_node_ref_with_fields(match: re.Match) -> str:
            node_name = match.group(1)
            safe_name = self._sanitize_name(node_name)
            field_path = match.group(2)  # e.g. "body.text" or "text"
            # Build chained .get() calls for nested access
            fields = field_path.split(".")
            expr = f"node_{safe_name}_json"
            for f in fields:
                expr = f'get({expr}, "{f}")'
            return expr

        def sanitize_node_ref_json(match: re.Match) -> str:
            node_name = match.group(1)
            safe_name = self._sanitize_name(node_name)
            return f"node_{safe_name}_json"

        def sanitize_node_ref(match: re.Match) -> str:
            node_name = match.group(1)
            safe_name = self._sanitize_name(node_name)
            return f"node_{safe_name}"

        # Match both single and double quotes: $node["Name"] or $node['Name']
        # Capture the full dotted field path (e.g. "body.text", "field")
        result = re.sub(
            r'''\$node\[["']([^"']+)["']\]\.json\.([\w.]+)''',
            sanitize_node_ref_with_fields,
            result,
        )
        result = re.sub(
            r'''\$node\[["']([^"']+)["']\]\.json''',
            sanitize_node_ref_json,
            result,
        )
        result = re.sub(
            r'''\$node\[["']([^"']+)["']\]''',
            sanitize_node_ref,
            result,
        )

        # Handle $json.field.nested -> chained get() for nested access
        def expand_json_path(match: re.Match) -> str:
            field_path = match.group(1)  # e.g. "body.text" or "field"
            fields = field_path.split(".")
            expr = "json_data"
            for f in fields:
                expr = f'get({expr}, "{f}")'
            return expr

        result = re.sub(r"\$json\.([\w.]+)", expand_json_path, result)
        result = result.replace("$json", "json_data")

        # Handle $input -> input_data
        result = result.replace("$input", "input_data")

        # Handle $env.VAR -> env.get("VAR")
        result = re.sub(r"\$env\.(\w+)", r'env.get("\1")', result)
        result = result.replace("$env", "env")

        # Handle $execution -> execution
        result = result.replace("$execution", "execution")

        # Handle $itemIndex -> item_index
        result = result.replace("$itemIndex", "item_index")

        return result

    def _build_eval_context(self, context: ExpressionContext) -> dict[str, Any]:
        """Build the evaluation context dictionary."""
        eval_ctx: dict[str, Any] = {
            "json_data": context.json_data,
            "input_data": [item.json for item in context.input_data],
            "env": context.env,
            "execution": context.execution,
            "item_index": context.item_index,
        }

        # Flatten $node access: node_NodeName_json
        for node_name, node_info in context.node_data.items():
            safe_name = self._sanitize_name(node_name)
            eval_ctx[f"node_{safe_name}"] = node_info
            eval_ctx[f"node_{safe_name}_json"] = node_info.get("json", {})

        return eval_ctx

    def _sanitize_name(self, name: str) -> str:
        """Sanitize node name for use as variable name.

        Appends a short hash when the name contains non-alphanumeric characters
        to avoid collisions (e.g. 'foo-bar' vs 'foo_bar').
        """
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        if sanitized != name:
            # Name was modified — append hash to disambiguate
            short_hash = hashlib.md5(name.encode()).hexdigest()[:6]
            return f"{sanitized}_{short_hash}"
        return sanitized

    def _stringify(self, value: Any) -> str:
        """Convert value to string for interpolation."""
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    def resolve_json_template(self, json_string: str, context: ExpressionContext) -> Any:
        """Resolve expressions inside a JSON string safely.

        Instead of string-interpolating into JSON (which breaks on quotes,
        nested objects, etc.), this method:
        1. Finds all {{ expr }} placeholders
        2. Temporarily replaces them with safe tokens
        3. Parses the JSON
        4. Walks the parsed structure and replaces tokens with resolved values

        This ensures that expression results with quotes, arrays, objects, etc.
        are inserted as proper typed values, not string-interpolated.
        """
        if not json_string or not isinstance(json_string, str):
            return json_string

        trimmed = json_string.strip()
        if not trimmed:
            return {}

        # If the whole thing is a single expression, resolve directly
        if trimmed.startswith("{{") and trimmed.endswith("}}"):
            inner = trimmed[2:-2].strip()
            if "{{" not in inner:
                return self._evaluate(inner, context)

        # Find all {{ expr }} in the string
        pattern = r"\{\{(.+?)\}\}"
        expressions: list[tuple[str, str]] = []  # (token, expr)
        counter = 0

        def tokenize(match: re.Match[str]) -> str:
            nonlocal counter
            expr = match.group(1).strip()
            token = f"__EXPR_{counter}__"
            expressions.append((token, expr))
            counter += 1
            return token

        tokenized = re.sub(pattern, tokenize, trimmed)

        # If no expressions found, just parse directly
        if not expressions:
            try:
                return json.loads(trimmed)
            except json.JSONDecodeError:
                return trimmed

        # Evaluate all expressions
        resolved: dict[str, Any] = {}
        for token, expr in expressions:
            resolved[token] = self._evaluate(expr, context)

        # Now replace tokens in the tokenized string with JSON-safe representations
        # and parse the result
        for token, value in resolved.items():
            if isinstance(value, str):
                # Replace the token (which may be inside quotes) with the escaped string
                tokenized = tokenized.replace(f'"{token}"', json.dumps(value))
                tokenized = tokenized.replace(token, json.dumps(value)[1:-1])
            elif isinstance(value, bool):
                tokenized = tokenized.replace(f'"{token}"', json.dumps(value))
                tokenized = tokenized.replace(token, json.dumps(value))
            elif isinstance(value, (int, float)):
                tokenized = tokenized.replace(f'"{token}"', json.dumps(value))
                tokenized = tokenized.replace(token, str(value))
            elif isinstance(value, (dict, list)):
                tokenized = tokenized.replace(f'"{token}"', json.dumps(value))
                tokenized = tokenized.replace(token, json.dumps(value))
            elif value is None:
                tokenized = tokenized.replace(f'"{token}"', "null")
                tokenized = tokenized.replace(token, "null")
            else:
                tokenized = tokenized.replace(token, str(value))

        try:
            return json.loads(tokenized)
        except json.JSONDecodeError:
            logger.warning("Failed to parse resolved JSON template: %s", tokenized)
            return tokenized

    @staticmethod
    def create_context(
        current_data: list[NodeData],
        node_states: dict[str, list[NodeData]],
        execution_id: str,
        item_index: int = 0,
    ) -> ExpressionContext:
        """Create expression context from execution state."""
        current_item = current_data[item_index] if item_index < len(current_data) else NodeData(json={})

        # Build $node object for accessing any previous node
        node_data: dict[str, dict[str, Any]] = {}
        for node_name, data in node_states.items():
            node_data[node_name] = {
                "json": data[0].json if data else {},
                "data": [d.json for d in data],
            }

        return ExpressionContext(
            json_data=current_item.json,
            input_data=current_data,
            node_data=node_data,
            env=dict(os.environ),
            execution={"id": execution_id, "mode": "manual"},
            item_index=item_index,
        )


# Singleton instance
expression_engine = ExpressionEngine()
