"""PythonStep node — a project module function (or inline code) in the sandbox (phase 8)."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..base import BaseNode, NodeInputDefinition, NodeOutputDefinition, NodeProperty, NodeTypeDescription

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


class PythonStepNode(BaseNode):
    function_exposed = False
    node_description = NodeTypeDescription(
        name="PythonStep",
        display_name="Python Step",
        description="Run a project Python function (module:function) or inline code in the sandbox",
        icon="fa:python",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[NodeOutputDefinition(name="main", display_name="Output")],
        properties=[
            NodeProperty(display_name="Module", name="module", type="string", default="",
                         description="Project module name (e.g. orders)"),
            NodeProperty(display_name="Function", name="function", type="string", default="",
                         description="Function receiving `items` (list of dicts) and returning a list or dict"),
            NodeProperty(display_name="Inline Code", name="code", type="json", default="",
                         type_options={"language": "python", "rows": 10},
                         description="Used when no module is set; same globals as the Code node, `studio` available"),
            NodeProperty(display_name="Timeout (s)", name="timeout", type="number", default=30),
        ],
    )

    @property
    def type(self) -> str:
        return "PythonStep"

    @property
    def description(self) -> str:
        return "Run Python in the sandbox"

    async def execute(self, context: ExecutionContext, node_definition: NodeDefinition,
                      input_data: list[NodeData]) -> NodeExecutionResult:
        from ...sandbox.node_runner import SandboxNodeError, run_in_sandbox
        from .code import CodeNode, NodeCallError

        p = node_definition.parameters
        timeout = float(p.get("timeout") or 30)
        try:
            if p.get("module") and p.get("function"):
                result, trace = await run_in_sandbox(
                    context, kind="step", payload={"items": [i.json for i in input_data]},
                    entry=f"{p['module']}:{p['function']}", timeout_s=timeout, origin="python_step")
            elif p.get("code"):
                result, trace = await run_in_sandbox(
                    context, kind="code", payload={"code": p["code"], "items": [{"json": i.json} for i in input_data],
                                                   "execution": {"id": context.execution_id, "mode": context.mode}},
                    timeout_s=timeout, origin="python_step")
            else:
                raise ValueError("set module + function, or inline code")
        except SandboxNodeError as e:
            raise NodeCallError(str(e), e.code, e.details) from None
        items: Any = result
        return self.output(CodeNode()._normalize_output(items), metadata={
            "sandboxed": True, "sandbox": {k: trace[k] for k in ("exit_code", "duration_ms", "broker_calls")}})
