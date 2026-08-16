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
            return self.output([NodeData(json=data)])

        # Fallback for manual execution without input
        return self.output([
            NodeData(json={
                "message": "",
                "timestamp": datetime.now().isoformat(),
            })
        ])
