"""Chat Input node - capture user messages from chat interface."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeOutputDefinition,
    NodeProperty,
)

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class ChatInputNode(BaseNode):
    """Chat input trigger - captures user messages from the UI chat interface."""

    node_description = NodeTypeDescription(
        name="ChatInput",
        display_name="Chat Input",
        description="Accept user input from chat interface",
        icon="fa:message",
        group=["ui", "trigger"],
        inputs=[],  # No inputs - this is a trigger
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Message",
                schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "User message"},
                        "timestamp": {"type": "string", "description": "ISO timestamp"},
                        "session_id": {"type": "string", "description": "Chat session id (memory nodes key on it)"},
                        "history": {"type": "array", "description": "Earlier messages [{role, content, at}], oldest first; excludes this one"},
                        "user_label": {"type": "string", "description": "Typed name of the person chatting"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Placeholder",
                name="placeholder",
                type="string",
                default="Type a message...",
                description="Placeholder text shown in the input field",
            ),
            NodeProperty(
                display_name="Welcome Message",
                name="welcomeMessage",
                type="string",
                default="",
                description="Initial message shown when chat opens",
                type_options={"rows": 3},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "ChatInput"

    @property
    def description(self) -> str:
        return "Accept user input from chat interface"

    def contract(self, params, inputs, lookups=None):
        """Declared: ``params.input_schema`` (a Shape) is the item shape; otherwise the default below."""
        declared = sh.param_shape(params, "input_schema", "inputSchema")
        shape = declared if declared is not None else sh.object_of(
            {
                "message": sh.scalar("string", "declared"),
                "timestamp": sh.scalar("string", "declared"),
                "session_id": sh.scalar("string", "declared"),
                "history": sh.array_of(sh.object_of({
                    "role": sh.scalar("string", "declared"), "content": sh.scalar("string", "declared"),
                    "at": sh.scalar("string", "declared")}, ["role", "content"], "declared"), "declared"),
                "user_label": sh.scalar("string", "declared"),
            },
            ["message", "timestamp"], "declared")
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        # Input data comes from the frontend with user's message
        if input_data and input_data[0].json:
            # Ensure timestamp is present
            data = input_data[0].json.copy()
            if "timestamp" not in data:
                data["timestamp"] = datetime.now().isoformat()
            data.setdefault("session_id", "")
            data.setdefault("history", [])
            return self.output([NodeData(json=data)])

        # Fallback for manual execution without input
        return self.output([
            NodeData(json={
                "message": "",
                "timestamp": datetime.now().isoformat(),
                "session_id": "",
                "history": [],
            })
        ])
