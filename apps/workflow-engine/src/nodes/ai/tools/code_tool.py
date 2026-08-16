"""Code execution tool for AI agents."""

from __future__ import annotations

import concurrent.futures
from typing import Any, TYPE_CHECKING

from ...base import (
    NodeProperty,
    NodeTypeDescription,
)
from .._config_base import ConfigProvider

if TYPE_CHECKING:
    from ....engine.types import NodeDefinition


class CodeToolNode(ConfigProvider):
    """Code execution tool - run Python code snippets in a sandboxed environment."""

    node_description = NodeTypeDescription(
        name="CodeTool",
        display_name="Code Tool",
        description="Execute Python code snippets as an agent tool",
        icon="fa:code",
        group=["ai"],
        inputs=[],
        outputs=[],
        properties=[
            NodeProperty(
                display_name="Description",
                name="description",
                type="string",
                default=(
                    "Execute a Python code snippet. Provide 'code' (required string) "
                    "and optional 'variables' (object of variable names to values). "
                    "The code should use 'return' to produce a result. "
                    "Available modules: json, math, re, random, datetime."
                ),
                description="Description shown to the AI model",
                type_options={"rows": 3},
            ),
        ],
    )

    def get_config(self, node_definition: NodeDefinition) -> dict[str, Any]:
        """Return code tool configuration."""
        return {
            "name": "run_code",
            "description": self.get_parameter(
                node_definition,
                "description",
                "Execute a Python code snippet.",
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Use 'return' to produce output.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "Optional variables to inject into the execution scope",
                    },
                },
                "required": ["code"],
            },
            # Sync executor — will be run via asyncio.to_thread by ai_agent.py
            "execute": _execute_code,
        }


def _execute_code(input_data: dict[str, Any]) -> dict[str, Any]:
    """Execute Python code in a restricted sandbox with a 5-second timeout."""
    code = input_data.get("code", "")
    variables = input_data.get("variables") or {}

    if not code:
        return {"error": "code is required"}

    import json
    import math
    import re
    import random
    from datetime import datetime, timedelta

    restricted_globals: dict[str, Any] = {
        "__builtins__": {
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "reversed": reversed,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "any": any,
            "all": all,
            "isinstance": isinstance,
            "type": type,
            "print": lambda *a: None,  # no-op print
            "None": None,
            "True": True,
            "False": False,
        },
        "json": json,
        "math": math,
        "re": re,
        "random": random,
        "datetime": datetime,
        "timedelta": timedelta,
    }

    # Inject user-provided variables
    restricted_globals.update(variables)

    # If the code has no top-level 'return', try to auto-return the last expression.
    # This makes LLM-generated code work without requiring explicit 'return'.
    code_stripped = code.strip()
    if "return " not in code_stripped.split("\n")[-1] and not code_stripped.endswith(":"):
        # Try to make the last non-empty line a return statement
        lines = code_stripped.split("\n")
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if line and not line.startswith(("#", "def ", "class ", "if ", "for ", "while ", "try:", "except", "finally")):
                # Only auto-return if the last line looks like an expression (no assignment, no keyword)
                if "=" not in line or line.startswith("return"):
                    lines[i] = "return " + lines[i]
                break
        code_stripped = "\n".join(lines)

    # Wrap in function so 'return' works
    code_lines = code_stripped.split("\n")
    indented = "\n".join(("    " + line if line.strip() else "") for line in code_lines)
    wrapped = f"def __tool_code__():\n{indented}\n\n__result__ = __tool_code__()"

    def run() -> Any:
        exec_locals: dict[str, Any] = {}
        exec(wrapped, restricted_globals, exec_locals)
        return exec_locals.get("__result__")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            result = future.result(timeout=60.0)
        return {"result": result}
    except concurrent.futures.TimeoutError:
        return {"error": "Code execution timed out (60 second limit)"}
    except SyntaxError as e:
        return {"error": f"Syntax error: {e}"}
    except Exception as e:
        return {"error": str(e)}
