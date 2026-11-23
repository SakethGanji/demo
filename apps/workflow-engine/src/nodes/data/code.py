"""Code node - execute custom Python code in a sandboxed environment."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class NodeCallError(RuntimeError):
    """A node failure with a stable code (e.g. a broker policy error) — surfaced as error_code."""

    def __init__(self, message: str, code: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class CodeNode(BaseNode):
    """Code node - execute custom Python code in a sandboxed environment."""

    node_description = NodeTypeDescription(
        name="Code",
        display_name="Code",
        description="Execute custom Python code",
        icon="fa:code",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "unknown",
                    "description": "User-defined output from Python code execution",
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Python Code",
                name="code",
                type="json",
                default="return items",
                type_options={"language": "python", "rows": 15},
                description="""Available variables:
- items: Input data list (list of dicts with 'json' key)
- json_data: First input item's json
- input_data: All input items
- node_data: Access data from previous nodes (e.g., node_data["NodeName"]["json"])
- execution: { "id", "mode" }

Helper functions:
- get_item(index): Get item at index
- new_item(data): Create new {"json": data} item
- log(*args): Print to console

Return a list of {"json": {...}} objects.

Note: Code runs in a restricted environment with a 5 second timeout.
External imports and file system access are limited.""",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Code"

    @property
    def description(self) -> str:
        return "Execute custom Python code"

    def contract(self, params, inputs, lookups=None):
        """Declared: ``params.output_schema`` (a Shape) when set, else UNKNOWN (arbitrary user code)."""
        declared = sh.param_shape(params, "output_schema", "outputSchema")
        return sh.make_contract(inputs, {"main": declared if declared is not None else sh.unknown()})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        """Phase 8: run in the bubblewrap sandbox; legacy in-process exec only when unavailable."""
        from ...sandbox.node_runner import SandboxNodeError, legacy_code_allowed, run_in_sandbox

        if legacy_code_allowed():
            return await self._execute_in_process(context, node_definition, input_data)
        code = self.get_parameter(node_definition, "code", "return items")
        node_data = {name: {"json": data[0].json if data else {}, "data": [d.json for d in data]}
                     for name, data in context.node_states.items()}
        payload = {"code": code, "items": [{"json": i.json} for i in input_data],
                   "node_data": node_data, "execution": {"id": context.execution_id, "mode": context.mode}}
        try:
            result, trace = await run_in_sandbox(context, kind="code", payload=payload, timeout_s=5.0, origin="code_node")
        except SandboxNodeError as e:
            raise NodeCallError(str(e), e.code, e.details) from None
        return self.output(self._normalize_output(result), metadata={
            "sandboxed": True, "sandbox": {k: trace[k] for k in ("exit_code", "duration_ms", "broker_calls", "logs")}})

    async def _execute_in_process(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        """Legacy path (pre-phase-8): restricted exec in a thread — NOT a security boundary."""

        code = self.get_parameter(node_definition, "code", "return items")

        # Build context for code execution
        node_data: dict[str, dict[str, Any]] = {}
        for node_name, data in context.node_states.items():
            node_data[node_name] = {
                "json": data[0].json if data else {},
                "data": [d.json for d in data],
            }

        items = [{"json": item.json} for item in input_data]
        json_data = input_data[0].json if input_data else {}
        execution = {"id": context.execution_id, "mode": context.mode}

        # Captured logs
        logs: list[list[Any]] = []

        def log(*args: Any) -> None:
            if len(logs) < 100:  # Limit logs to prevent memory issues
                logs.append(list(args))
                print("[Code Node]", *args)

        def get_item(index: int) -> dict[str, Any] | None:
            return items[index] if 0 <= index < len(items) else None

        def new_item(data: dict[str, Any]) -> dict[str, Any]:
            return {"json": data}

        # Build restricted globals
        restricted_globals: dict[str, Any] = {
            "__builtins__": {
                # Safe builtins
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
                "chr": chr,
                "ord": ord,
                "print": log,
                "None": None,
                "True": True,
                "False": False,
            },
            "items": items,
            "json_data": json_data,
            "input_data": items,
            "node_data": node_data,
            "execution": execution,
            "get_item": get_item,
            "new_item": new_item,
            "log": log,
        }

        # Nodes-as-functions (phase 4): `studio.nodes.<fn>(...)` via the broker, in-process.
        # Unsandboxed until phase 8 moves this node into the sandbox.
        import asyncio as _asyncio
        loop_for_broker = _asyncio.get_running_loop()
        broker_token = None
        studio_transport = None
        try:
            from ...broker.service import BrokerService, mint_for_context
            from ...sdk.generate import load_in_process

            studio_mod = load_in_process()
            broker_token = mint_for_context(context, "code_node")

            def studio_transport(node: str, parameters: dict, items_: list) -> dict:
                fut = _asyncio.run_coroutine_threadsafe(
                    BrokerService().call_with_token(broker_token, None, node, parameters, items_), loop_for_broker)
                status, body = fut.result(timeout=120)
                body["status"] = status
                return body

            restricted_globals["studio"] = studio_mod
        except Exception:  # SDK unavailable → Code node keeps working without `studio`
            studio_mod = None

        # Add safe modules
        import json
        import math
        import re
        import random
        import base64
        import io
        from datetime import datetime, timedelta

        restricted_globals["json"] = json
        restricted_globals["math"] = math
        restricted_globals["re"] = re
        restricted_globals["random"] = random
        restricted_globals["base64"] = base64
        restricted_globals["io"] = io
        restricted_globals["datetime"] = datetime
        restricted_globals["timedelta"] = timedelta

        # Add pandas if available (for data processing)
        try:
            import pandas as pd
            restricted_globals["pd"] = pd
            restricted_globals["pandas"] = pd
        except ImportError:
            pass

        try:
            # Unescape literal \n and \t that arrive from LLM tool-call JSON
            # (only outside string literals and comments: a "\n" inside a string is the string's own escape)
            from ...sandbox.code_escapes import unescape_code

            code = unescape_code(code)

            # Wrap code in a function - properly indent each line
            code_lines = code.split('\n')
            indented_lines = []
            for line in code_lines:
                if line.strip():  # Non-empty line
                    indented_lines.append('    ' + line)
                else:
                    indented_lines.append('')
            indented_code = '\n'.join(indented_lines)

            wrapped_code = f"""def __user_code__():
{indented_code}

__result__ = __user_code__()
"""
            # Execute with timeout
            import asyncio
            import concurrent.futures

            def execute_code() -> Any:
                exec_locals: dict[str, Any] = {}
                if studio_mod is not None and studio_transport is not None:
                    studio_mod._transport.use(studio_transport)
                try:
                    exec(wrapped_code, restricted_globals, exec_locals)
                    return exec_locals.get("__result__")
                except SyntaxError as e:
                    # Report the user's line: the code is wrapped in `def __user_code__():` (+1 line).
                    line = max(1, (e.lineno or 2) - 1)
                    err = SyntaxError(f"Line {line}: {e.msg}")
                    err.user_line = line  # type: ignore[attr-defined]
                    raise err from e

            import contextvars
            loop = asyncio.get_event_loop()
            ctx = contextvars.copy_context()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = loop.run_in_executor(executor, ctx.run, execute_code)
                result = await asyncio.wait_for(future, timeout=5.0)

            # Normalize result
            output = self._normalize_output(result)
            return self.output(output, metadata={"sandboxed": False})

        except asyncio.TimeoutError:
            raise RuntimeError("Code execution timed out (5 second limit)")
        except Exception as e:
            if studio_mod is not None and isinstance(e, studio_mod._transport.BrokerError):
                raise NodeCallError(f"Code execution failed: {e}", e.code, {**e.details}) from e
            if isinstance(e, SyntaxError):
                raise NodeCallError(f"Syntax Error in code: {e}", "syntax_error", {"line": getattr(e, "user_line", None)})
            raise RuntimeError(f"Code execution failed: {e}")
        finally:
            if broker_token:
                from ...broker.service import revoke_for_context
                revoke_for_context(context, "code_node")

    def _normalize_output(self, result: Any) -> list[NodeData]:
        """Normalize code output to NodeData list."""
        from ...engine.types import NodeData

        if not result:
            return []

        if not isinstance(result, list):
            # Single object - wrap in list
            if isinstance(result, dict):
                if "json" in result:
                    return [NodeData(json=result["json"])]
                return [NodeData(json=result)]
            return [NodeData(json={"value": result})]

        # List - ensure each item has json property
        output = []
        for item in result:
            if isinstance(item, dict):
                if "json" in item:
                    output.append(NodeData(json=item["json"]))
                else:
                    output.append(NodeData(json=item))
            else:
                output.append(NodeData(json={"value": item}))

        return output
