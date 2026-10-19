"""HTML node - turn Markdown (or an HTML fragment) into a standalone report document."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...utils.report_html import html_to_text, render_markdown_report, report_document

if TYPE_CHECKING:
    from ...engine.types import ExecutionContext, NodeData, NodeDefinition, NodeExecutionResult


from ...engine import shapes as sh

class HtmlNode(BaseNode):
    """HTML node - render a report body to a full HTML document plus its plain-text version. Pure: no network."""

    node_description = NodeTypeDescription(
        name="Html",
        display_name="HTML",
        description="Render Markdown or HTML as a standalone, styled report document",
        icon="fa:file-code",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "html": {"type": "string"},
                        "text": {"type": "string"},
                        "title": {"type": "string"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Mode",
                name="mode",
                type="options",
                default="markdown",
                options=[
                    NodePropertyOption(name="Markdown", value="markdown", description="Write Markdown; headings, lists, tables, code and links are styled"),
                    NodePropertyOption(name="HTML", value="html", description="HTML; a fragment is wrapped in the report document, a full document is kept as it is"),
                ],
            ),
            NodeProperty(
                display_name="Content",
                name="content",
                type="string",
                default="",
                required=True,
                placeholder="{{ $json.response }}",
                description="The report body (supports expressions)",
                type_options={"rows": 10},
            ),
            NodeProperty(
                display_name="Title",
                name="title",
                type="string",
                default="",
                placeholder="Dataset report",
                description="Document title; also the heading when the content has no top-level heading (supports expressions)",
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Html"

    @property
    def description(self) -> str:
        return "Render Markdown or HTML as a standalone, styled report document"

    def contract(self, params, inputs, lookups=None):
        """Declared: ``{html, text, title}`` for every item."""
        s = sh.scalar
        shape = sh.object_of(
            {"html": s("string", "declared"), "text": s("string", "declared"), "title": s("string", "declared")},
            ["html", "text", "title"], "declared")
        return sh.make_contract(inputs, {"main": shape})

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        mode = self.get_parameter(node_definition, "mode", "markdown")
        content_raw = self.get_parameter(node_definition, "content", "")
        title_raw = self.get_parameter(node_definition, "title", "")
        if mode not in ("markdown", "html"):
            raise ValueError(f"Unknown mode '{mode}': use 'markdown' or 'html'")

        results: list[ND] = []
        items = input_data if input_data else [ND(json={})]
        for idx, _item in enumerate(items):
            expr_context = ExpressionEngine.create_context(input_data, context.node_states, context.execution_id, idx)
            content = expression_engine.resolve(content_raw, expr_context)
            content = "" if content is None else str(content)
            title = expression_engine.resolve(title_raw, expr_context)
            title = "" if title is None else str(title).strip()

            document = render_markdown_report(content, title) if mode == "markdown" else report_document(content, title)
            results.append(ND(json={"html": document, "text": html_to_text(document), "title": title}))

        return self.output(results)
