"""Output node - unified display node supporting HTML, Markdown, PDF, and Table formats."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

import httpx

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)

if TYPE_CHECKING:
    from ...engine.types import (
        ExecutionContext,
        NodeData,
        NodeDefinition,
        NodeExecutionResult,
    )


# Default content field per format
_DEFAULT_FIELDS: dict[str, str] = {
    "html": "html",
    "markdown": "markdown",
    "pdf": "pdf_base64",
    "table": "data",
}

# Markdown fallback field names
_MARKDOWN_FALLBACKS = ["markdown", "text", "content", "message", "summary", "body"]

# File extension → render format
_EXT_TO_FORMAT = {
    ".csv": "table", ".parquet": "table",
    ".xlsx": "table", ".xls": "table",
    ".html": "html", ".htm": "html",
    ".md": "markdown", ".markdown": "markdown",
    ".pdf": "pdf",
}


class OutputDisplayNode(BaseNode):
    """
    Unified Output node.

    Single node that handles HTML, Markdown, PDF, and Table display formats
    via a format dropdown. Emits the appropriate `_renderAs` hint for the
    frontend to render in the bottom panel.
    """

    node_description = NodeTypeDescription(
        name="Output",
        display_name="Output",
        icon="fa:monitor",
        description="Displays content in the output panel (HTML, Markdown, PDF, or Table)",
        group=["output"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Output",
                schema={
                    "type": "object",
                    "properties": {
                        "_renderAs": {
                            "type": "string",
                            "description": "Render hint: html | markdown | pdf | table",
                        },
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Source",
                name="source",
                type="options",
                default="input",
                description="Where to read content from",
                options=[
                    NodePropertyOption(name="From Input", value="input"),
                    NodePropertyOption(name="From File", value="file"),
                ],
            ),
            NodeProperty(
                display_name="File Path",
                name="filePath",
                type="string",
                default="",
                description="Path to file. Format auto-detected from extension (.csv, .xlsx, .parquet, .pdf, .html, .md)",
                display_options={"show": {"source": ["file"]}},
            ),
            NodeProperty(
                display_name="Format",
                name="format",
                type="options",
                default="html",
                description="How the content should be rendered in the output panel",
                display_options={"show": {"source": ["input"]}},
                options=[
                    NodePropertyOption(name="HTML", value="html"),
                    NodePropertyOption(name="Markdown", value="markdown"),
                    NodePropertyOption(name="PDF", value="pdf"),
                    NodePropertyOption(name="Table", value="table"),
                ],
            ),
            NodeProperty(
                display_name="Content",
                name="content",
                type="string",
                default="",
                description="Content expression (e.g. {{ $json.html }}, {{ $json.data }}). If empty, uses field lookup.",
                display_options={"show": {"source": ["input"]}},
            ),
            NodeProperty(
                display_name="Content Field",
                name="contentField",
                type="string",
                default="",
                description="Field name in input data. Auto-defaults per format (html, markdown, pdf_base64, data).",
                display_options={"show": {"source": ["input"]}},
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "Output"

    @property
    def description(self) -> str:
        return "Displays content in the output panel (HTML, Markdown, PDF, or Table)"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        source: str = self.get_parameter(node_definition, "source", "input")
        if source == "file":
            return await self._handle_file_source(context, node_definition, input_data)

        fmt: str = self.get_parameter(node_definition, "format", "html")
        raw_content: str = self.get_parameter(node_definition, "content", "")
        content_field: str = (
            self.get_parameter(node_definition, "contentField", "")
            or _DEFAULT_FIELDS.get(fmt, "")
        )

        items = input_data if input_data else [ND(json={})]
        results: list[ND] = []

        for item in items:
            if fmt == "html":
                results.append(self._handle_html(item, raw_content, content_field, context))
            elif fmt == "markdown":
                results.append(self._handle_markdown(item, raw_content, content_field, context))
            elif fmt == "pdf":
                results.append(self._handle_pdf(item, raw_content, content_field, context))
            elif fmt == "table":
                results.append(self._handle_table(item, raw_content, content_field, context))
            else:
                raise ValueError(f"Unknown output format: {fmt}")

        return self.output(results)

    # ------------------------------------------------------------------
    # File source
    # ------------------------------------------------------------------

    async def _handle_file_source(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData as ND
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        raw_path: str = self.get_parameter(node_definition, "filePath", "")
        if not raw_path:
            raise ValueError("filePath is required when source is 'file'.")

        # Resolve expressions in filePath (e.g. {{ $json.path }})
        items = input_data if input_data else [ND(json={})]
        first_item = items[0]
        if "{{" in raw_path:
            expr_ctx = ExpressionEngine.create_context(
                [first_item], context.node_states, context.execution_id,
            )
            raw_path = expression_engine.resolve(raw_path, expr_ctx, skip_json=False)

        if not raw_path:
            raise ValueError(
                "filePath resolved to empty. Check that the upstream node "
                "provides the expected field (e.g. $json.file_path)."
            )

        file_path = Path(str(raw_path))
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = file_path.suffix.lower()
        fmt = _EXT_TO_FORMAT.get(ext)
        if not fmt:
            raise ValueError(
                f"Unsupported file extension '{ext}'. "
                f"Supported: {', '.join(sorted(_EXT_TO_FORMAT))}"
            )

        if fmt == "table":
            result = await self._read_table_file(str(file_path))
        elif fmt == "html" or fmt == "markdown":
            text = file_path.read_text(encoding="utf-8")
            key = "html" if fmt == "html" else "markdown"
            result = {key: text, "_renderAs": fmt}
        elif fmt == "pdf":
            pdf_bytes = file_path.read_bytes()
            result = {
                "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                "_renderAs": "pdf",
            }
        else:
            raise ValueError(f"Unknown format: {fmt}")

        return self.output([ND(json=result)])

    async def _read_table_file(self, file_path: str) -> dict:
        analytics_url = os.getenv("ACCELERATOR_SERVICE_URL", "http://localhost:8001")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{analytics_url}/sample",
                json={
                    "file_path": file_path,
                    "method": "first_n",
                    "sample_size": 10000,
                    "return_data": True,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
        body = resp.json()
        return {"data": body["data"], "_renderAs": "table"}

    # ------------------------------------------------------------------
    # Per-format handlers (inline / input source)
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_expression(value: str) -> bool:
        """Check if a string looks like an expression rather than a plain field name."""
        s = value.strip()
        return "{{" in s or s.startswith("$")

    def _resolve_value(
        self,
        raw: str,
        item: NodeData,
        context: ExecutionContext,
    ) -> Any:
        """Resolve a user-provided value — handles {{ expr }}, bare $json.x, or plain strings.

        Returns the typed result (list, dict, str, etc.) so callers get the
        actual data structure, not a stringified version.
        """
        from ...engine.expression_engine import ExpressionEngine, expression_engine

        if not raw or not isinstance(raw, str):
            return None

        s = raw.strip()

        # Already wrapped in {{ }} — resolve directly
        if "{{" in s:
            expr_ctx = ExpressionEngine.create_context(
                [item], context.node_states, context.execution_id,
            )
            return expression_engine.resolve(s, expr_ctx, skip_json=False)

        # Bare expression like $json.data, $node["X"].json.field, $input, etc.
        if s.startswith("$"):
            expr_ctx = ExpressionEngine.create_context(
                [item], context.node_states, context.execution_id,
            )
            return expression_engine.resolve("{{ " + s + " }}", expr_ctx, skip_json=False)

        # Plain string (not an expression) — return as-is
        return s

    def _get_content(
        self,
        item: NodeData,
        raw_content: str,
        field: str,
        context: ExecutionContext,
    ) -> Any:
        """Unified content resolution used by all format handlers.

        Priority:
        1. ``content`` parameter — resolve as expression
        2. ``contentField`` parameter — resolve as expression if it looks
           like one ($json.x, {{ ... }}), otherwise plain field lookup
        3. Return None so the caller can apply format-specific fallbacks
        """
        # 1. Try the explicit content parameter
        if raw_content:
            result = self._resolve_value(raw_content, item, context)
            if result is not None:
                return result

        # 2. Try contentField — expression-aware
        if field:
            if self._looks_like_expression(field):
                result = self._resolve_value(field, item, context)
                if result is not None:
                    return result
            else:
                result = item.json.get(field)
                if result is not None:
                    return result

        return None

    def _handle_html(
        self,
        item: NodeData,
        raw_content: str,
        field: str,
        context: ExecutionContext,
    ) -> NodeData:
        from ...engine.types import NodeData as ND

        html = self._get_content(item, raw_content, field, context)
        if not html:
            raise ValueError(
                f'Missing HTML content. Tried "content" parameter and field "{field}".'
            )
        return ND(json={"html": html, "_renderAs": "html"})

    def _handle_markdown(
        self,
        item: NodeData,
        raw_content: str,
        field: str,
        context: ExecutionContext,
    ) -> NodeData:
        from ...engine.types import NodeData as ND

        md = self._get_content(item, raw_content, field, context)
        if not md:
            for fb in _MARKDOWN_FALLBACKS:
                val = item.json.get(fb)
                if val and isinstance(val, str):
                    md = val
                    break
        if not md:
            raise ValueError(
                f'Missing Markdown content in field "{field}" '
                f"and fallbacks {_MARKDOWN_FALLBACKS}."
            )
        return ND(json={"markdown": md, "_renderAs": "markdown"})

    def _handle_pdf(
        self,
        item: NodeData,
        raw_content: str,
        field: str,
        context: ExecutionContext,
    ) -> NodeData:
        from ...engine.types import NodeData as ND

        pdf = self._get_content(item, raw_content, field, context)
        if not pdf:
            raise ValueError(
                f'Missing PDF base64 content. Tried "content" parameter and field "{field}".'
            )
        return ND(json={"pdf_base64": pdf, "_renderAs": "pdf"})

    def _handle_table(
        self,
        item: NodeData,
        raw_content: str,
        field: str,
        context: ExecutionContext,
    ) -> NodeData:
        from ...engine.types import NodeData as ND

        data = self._get_content(item, raw_content, field, context)
        if data is None:
            # If the whole item looks like tabular data, pass it through
            data = [item.json]
        if isinstance(data, list):
            return ND(json={"data": data, "_renderAs": "table"})
        raise ValueError(
            f'Expected array data in field "{field}", got {type(data).__name__}.'
        )
