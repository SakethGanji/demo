"""AI service for App Builder — generates React TSX components via LLM.

The LLM generates a single React component as TSX source using raw HTML
elements + Tailwind CSS classes. No custom component library — just standard
React with useState/useEffect/useCallback and native HTML elements.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncGenerator

from ..engine.llm_provider import call_llm
from ..repositories.execution_repository import ExecutionRepository
from ..repositories.node_output_repository import NodeOutputRepository
from ..repositories.workflow_repository import WorkflowRepository
from .app_service import AppService
from ..schemas.app_builder import (
    AppBuilderChatRequest,
    NodeSchema,
    WorkflowSchemaResponse,
)
from .schema_analyzer import analyze_schema, format_field_catalog
from .schema_inference import infer_json_schema, truncate_sample

logger = logging.getLogger(__name__)

_GENERATOR_MODEL = "gemini-2.5-pro"
_MAX_HISTORY = 20
_MAX_MESSAGE_CHARS = 4000


class AppBuilderAIService:
    """Generates and modifies React TSX components via LLM with workflow context."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
        node_output_repo: NodeOutputRepository,
        app_service: AppService,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._execution_repo = execution_repo
        self._node_output_repo = node_output_repo
        self._app_service = app_service

    # ── Public ────────────────────────────────────────────────────────

    async def stream_chat(
        self, request: AppBuilderChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream app builder response as SSE event dicts."""

        # 1. Resolve current code from DB (preferred) or fallback to request body
        current_code = request.current_code
        if request.app_id:
            try:
                if request.current_version_id:
                    version = await self._app_service.get_version(
                        request.app_id, request.current_version_id
                    )
                    if version:
                        current_code = version.source_code
                elif current_code is None:
                    # No version specified — use draft_source_code from app
                    app_detail = await self._app_service.get_app(request.app_id)
                    if app_detail and app_detail.source_code:
                        current_code = app_detail.source_code
            except Exception:
                logger.warning("Failed to resolve code for app %s", request.app_id, exc_info=True)

        # 2. Extract workflow schemas for all linked workflows
        workflow_schemas: list[WorkflowSchemaResponse] = []
        for wf_id in request.workflow_ids:
            try:
                schema = await self.extract_workflow_schema(wf_id)
                workflow_schemas.append(schema)
            except Exception:
                logger.warning("Failed to extract schema for workflow %s", wf_id, exc_info=True)

        # 3. Build system prompt
        system_prompt = self._build_system_prompt(
            workflow_schemas=workflow_schemas,
            current_code=current_code,
        )

        # 4. Build message history
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in request.conversation_history[-_MAX_HISTORY:]:
            if msg.role in ("user", "assistant") and msg.content:
                messages.append({"role": msg.role, "content": msg.content[:_MAX_MESSAGE_CHARS]})
        messages.append({"role": "user", "content": request.message})

        # 5. Generate (single call, no component selection stage needed)
        full_text = ""
        try:
            logger.info("App builder: calling %s with %d messages", _GENERATOR_MODEL, len(messages))
            response = await call_llm(
                model=_GENERATOR_MODEL,
                messages=messages,
                temperature=0.3,
                max_tokens=65_000,
            )
            full_text = response.text or ""
            logger.info("App builder: got %d chars from LLM", len(full_text))
            if full_text:
                logger.debug("App builder LLM output (first 500 chars):\n%s", full_text[:500])
        except Exception as e:
            logger.error("App builder LLM error: %s", e, exc_info=True)
            yield _sse("message", {"type": "error", "message": str(e)})
            yield _sse("done", {"type": "done"})
            return

        # 6. Parse LLM output — extract TSX code and text
        source_code, text_content = _parse_llm_output(full_text)
        logger.info(
            "App builder parse: code=%s text=%d chars",
            "yes" if source_code else "no",
            len(text_content),
        )

        # 7. Emit text explanation
        if text_content:
            yield _sse("message", {"type": "text", "content": text_content})

        # 8. Emit code
        if source_code:
            yield _sse("message", {"type": "code", "source": source_code})
        elif not text_content:
            yield _sse("message", {
                "type": "text",
                "content": "I wasn't able to generate an app from that request. Please try again with more detail.",
            })
            logger.warning("App builder: no TSX code block found in LLM output")

        yield _sse("done", {"type": "done"})

    # ── Workflow Schema Extraction ────────────────────────────────────

    async def extract_workflow_schema(
        self, workflow_id: str
    ) -> WorkflowSchemaResponse:
        """Pull schema from the latest successful execution of a workflow."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow = stored.workflow
        node_type_map = {n.name: n.type for n in workflow.nodes}
        node_params_map = {n.name: n.parameters for n in workflow.nodes}

        # Extract input schema from Start node parameters
        input_schema = _extract_input_schema(workflow.nodes)

        # Detect webhook trigger and response mode
        webhook_path = _extract_webhook_path(workflow.nodes, workflow_id)
        webhook_response_mode = _extract_webhook_response_mode(workflow.nodes)

        # Find latest successful execution efficiently
        latest_exec = await self._execution_repo.find_latest_successful(
            workflow_id=workflow_id
        )

        if not latest_exec:
            return WorkflowSchemaResponse(
                workflow_id=workflow_id,
                workflow_name=stored.name,
                input_schema=input_schema,
                webhook_path=webhook_path,
                webhook_response_mode=webhook_response_mode,
                node_schemas=[
                    NodeSchema(
                        node_name=name,
                        node_type=ntype,
                        parameters=_sanitize_params(node_params_map.get(name, {})),
                        output_schema={"type": "unknown"},
                    )
                    for name, ntype in node_type_map.items()
                    if ntype not in ("Start", "Webhook")
                ],
            )

        node_outputs = await self._node_output_repo.get_outputs(latest_exec.id)

        node_schemas = []
        webhook_body_schema = None
        webhook_body_sample = None

        for output in node_outputs:
            if output.status != "success" or not output.output:
                continue

            ntype = node_type_map.get(output.node_name, "Unknown")

            # Extract the webhook body shape from the Webhook node's output
            # Output format: [{"json": {"body": {...}, "headers": {...}, ...}, "binary": null}]
            if ntype == "Webhook":
                webhook_json = _unwrap_node_output(output.output)
                if isinstance(webhook_json, dict):
                    body = webhook_json.get("body")
                    if body is not None:
                        webhook_body_schema = infer_json_schema(body)
                        webhook_body_sample = truncate_sample(body, max_items=3)
                continue  # Don't include Webhook node in node_schemas

            node_schemas.append(
                NodeSchema(
                    node_name=output.node_name,
                    node_type=ntype,
                    parameters=_sanitize_params(node_params_map.get(output.node_name, {})),
                    output_schema=infer_json_schema(output.output),
                    sample_data=truncate_sample(output.output, max_items=3),
                )
            )

        # Run schema analyzer on the last node (the one the UI will render)
        if node_schemas:
            last = node_schemas[-1]
            last_output = None
            for output in node_outputs:
                if output.node_name == last.node_name and output.status == "success":
                    last_output = output.output
                    break
            if last_output is not None:
                catalog = await analyze_schema(last_output)
                if catalog:
                    last.field_catalog = catalog

        return WorkflowSchemaResponse(
            workflow_id=workflow_id,
            workflow_name=stored.name,
            input_schema=input_schema,
            webhook_path=webhook_path,
            webhook_response_mode=webhook_response_mode,
            webhook_body_schema=webhook_body_schema,
            webhook_body_sample=webhook_body_sample,
            node_schemas=node_schemas,
        )

    # ── System Prompt ─────────────────────────────────────────────────

    def _build_system_prompt(
        self,
        workflow_schemas: list[WorkflowSchemaResponse],
        current_code: str | None,
    ) -> str:
        sections = [_BASE_PROMPT]

        for ws in workflow_schemas:
            sections.append(self._workflow_context_section(ws))

        if current_code:
            sections.append(
                "## Current Code\n\n"
                "The user already has this app. Modify it based on their request.\n"
                "Always return the COMPLETE updated component — never partial code.\n\n"
                f"```tsx\n{current_code}\n```"
            )

        return "\n\n".join(sections)

    def _workflow_context_section(self, ws: WorkflowSchemaResponse) -> str:
        lines = [
            f'## Connected Workflow: "{ws.workflow_name}"',
            "",
            "This app is connected to a workflow. Use the data below to make API calls.",
        ]

        # Show the last node's output as the response shape
        last_node = ws.node_schemas[-1] if ws.node_schemas else None
        last_name = last_node.node_name if last_node else "output"

        # Determine the API endpoint
        api_path = ws.webhook_path or f"/api/workflows/{ws.workflow_id}/run"
        is_webhook = ws.webhook_path is not None

        lines.append("\n### How to Call the Workflow\n")
        lines.append(
            "Use `window.__apiFetch()` (provided in the sandbox) to call the workflow.\n"
        )

        if is_webhook:
            response_mode = ws.webhook_response_mode or "lastNode"

            lines.append(
                "**IMPORTANT**: Send the request body directly — do NOT wrap it in any envelope.\n"
                "The workflow receives the POST body as-is.\n"
            )
            # Show the expected body shape from past execution
            if ws.webhook_body_schema:
                lines.append(
                    f"### Expected Request Body\n\n"
                    f"```json\n{json.dumps(ws.webhook_body_schema, indent=2)}\n```"
                )
            if ws.webhook_body_sample is not None:
                lines.append(
                    f"\nExample request body:\n```json\n{json.dumps(ws.webhook_body_sample, indent=2)}\n```"
                )

            if response_mode == "onReceived":
                # Async mode — no data in response, workflow runs in background
                lines.append(f"""
### Calling the Workflow

```tsx
const result = await window.__apiFetch('{api_path}', {{
  method: 'POST',
  headers: {{ 'Content-Type': 'application/json' }},
  body: JSON.stringify({{ /* match the request body schema above */ }})
}});
// Response (immediate, workflow runs in background):
// {{ status: "success", executionId: "...", message: "Workflow triggered" }}
```

**Note**: This webhook responds immediately and runs the workflow in the background. The response does NOT contain the workflow output. If you need the workflow output in the response, suggest the user switch the webhook response mode to "Last Node".""")
            else:
                # lastNode mode — synchronous, returns data
                lines.append(f"""
### Calling the Workflow

```tsx
const result = await window.__apiFetch('{api_path}', {{
  method: 'POST',
  headers: {{ 'Content-Type': 'application/json' }},
  body: JSON.stringify({{ /* match the request body schema above */ }})
}});
// Response format:
// {{ status: "success", executionId: "...", data: [ ...items ] }}
// Access the result items via result.data
```""")
        else:
            lines.append(
                "**IMPORTANT**: The request body MUST be wrapped in `input_data`:\n"
            )
            lines.append(f"""```tsx
const result = await window.__apiFetch('{api_path}', {{
  method: 'POST',
  headers: {{ 'Content-Type': 'application/json' }},
  body: JSON.stringify({{ input_data: {{ /* your input fields here */ }} }})
}});
// result shape: {{ data: {{ "{last_name}": <output>, ... }}, status: "success" }}
```""")

        if last_node:
            if last_node.field_catalog:
                # Use the analyzed field catalog — compact, with rendering hints
                lines.append(f'\n### Response Fields (from node "{last_name}")\n')
                lines.append(
                    "Each field below includes its content type and recommended rendering approach:\n"
                )
                lines.append(format_field_catalog(last_node.field_catalog))
                lines.append(
                    "\nUse these render hints to build the UI. For example:\n"
                    "- `collapsible` → expandable/accordion section\n"
                    "- `code_block` → `<pre><code>` with monospace\n"
                    "- `markdown` content → render as formatted HTML (headings, lists, bold, etc.)\n"
                    "- `badge` → small colored label\n"
                    "- `hidden` → don't display to user\n"
                    "- `table_cell` → good for data tables/grids\n"
                )
            else:
                # Fallback to raw schema + sample
                lines.append(f'\n### Response Data Shape (from node "{last_name}")\n')
                lines.append(f"```json\n{json.dumps(last_node.output_schema, indent=2)}\n```")
                if last_node.sample_data is not None:
                    lines.append(f"\nSample data:\n```json\n{json.dumps(last_node.sample_data, indent=2)}\n```")

        return "\n".join(lines)


# ── Constants ─────────────────────────────────────────────────────────

_BASE_PROMPT = """\
You are an expert React developer. You generate complete, self-contained React components as TSX.

## Output Format

Return a single React component wrapped in ```tsx fences:

```tsx
export default function App() {
  // your component code here
  return (
    <div>...</div>
  );
}
```

## Rules

- Generate a SINGLE default-exported React function component.
- Use raw HTML elements (`<div>`, `<button>`, `<input>`, `<table>`, etc.) — NOT custom component imports.
- Use Tailwind CSS classes for ALL styling. No inline styles unless absolutely necessary.
- Use `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`, `useReducer` for state and effects. These are available as globals (no import needed).
- Do NOT import anything. React, useState, useEffect, etc. are pre-loaded globals.
- For API calls to connected workflows, use `window.__apiFetch(url, opts)` which returns a Promise with the JSON response. It accepts `/api/...` and `/webhook/...` paths.
- Always return the COMPLETE component — never partial code or diffs.
- Keep the UI clean, modern, and functional with good Tailwind styling.
- Use semantic HTML and accessible patterns (labels, aria attributes where appropriate).
- Handle loading and error states for any async operations.
- When displaying text that may contain markdown (e.g. LLM responses, rich descriptions), render it as HTML elements with Tailwind classes — NOT raw markdown strings. For example, render `**bold**` as `<strong>`, `# Heading` as `<h1>`, bullet lists as `<ul><li>`, code blocks as `<pre><code>`, etc. No markdown parsing library is available in the sandbox — you must convert markdown content to JSX elements yourself using a simple parser function or pre-render it as HTML.

## Complete Example — Todo App

```tsx
export default function TodoApp() {
  const [todos, setTodos] = useState([
    { id: 1, text: 'Buy groceries', done: false },
    { id: 2, text: 'Read docs', done: true },
  ]);
  const [input, setInput] = useState('');
  const nextId = useRef(3);

  const remaining = todos.filter(t => !t.done).length;

  const addTodo = () => {
    if (!input.trim()) return;
    setTodos(prev => [...prev, { id: nextId.current++, text: input.trim(), done: false }]);
    setInput('');
  };

  const toggleTodo = (id: number) => {
    setTodos(prev => prev.map(t => t.id === id ? { ...t, done: !t.done } : t));
  };

  const removeTodo = (id: number) => {
    setTodos(prev => prev.filter(t => t.id !== id));
  };

  return (
    <div className="max-w-md mx-auto p-6 mt-10">
      <h1 className="text-2xl font-bold mb-1">My Todos</h1>
      <p className="text-sm text-gray-500 mb-4">{remaining} remaining</p>

      <div className="flex gap-2 mb-4">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && addTodo()}
          placeholder="Add a todo..."
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={addTodo}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          Add
        </button>
      </div>

      <ul className="space-y-1">
        {todos.map(todo => (
          <li key={todo.id} className="flex items-center gap-2 p-2 rounded-lg bg-gray-50">
            <input
              type="checkbox"
              checked={todo.done}
              onChange={() => toggleTodo(todo.id)}
              className="h-4 w-4 rounded"
            />
            <span className={`flex-1 text-sm ${todo.done ? 'line-through text-gray-400' : ''}`}>
              {todo.text}
            </span>
            <button
              onClick={() => removeTodo(todo.id)}
              className="text-xs text-gray-400 hover:text-red-500 transition-colors"
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

This example shows: useState for state, event handlers, conditional classes, list rendering, keyboard events, and clean Tailwind styling."""


# ── Helpers ───────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


# Match ```tsx or ```typescript fenced blocks
_TSX_BLOCK_RE = re.compile(r"```(?:tsx|typescript)\s*\n(.*?)```", re.DOTALL)
_ANY_CODE_BLOCK_RE = re.compile(r"```(?:tsx|typescript)\s*\n.*?```", re.DOTALL)


def _parse_llm_output(text: str) -> tuple[str | None, str]:
    """Parse LLM output into (source_code, text_content).

    Extracts TSX code from ```tsx fences and returns the text explanation.
    """
    source_code: str | None = None

    # Find the last ```tsx block (in case the LLM explains with code snippets first)
    matches = list(_TSX_BLOCK_RE.finditer(text))
    if matches:
        # Use the last match — that's typically the complete component
        source_code = matches[-1].group(1).strip()

    # Strip all code blocks from text to get explanation only
    text_content = _ANY_CODE_BLOCK_RE.sub("", text).strip()

    return source_code, text_content


_SENSITIVE_PARAM_KEYS = {"password", "token", "secret", "api_key", "apikey", "authorization"}


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return node parameters with sensitive values redacted."""
    sanitized = {}
    for key, value in params.items():
        if key.lower() in _SENSITIVE_PARAM_KEYS:
            sanitized[key] = "***"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_params(value)
        else:
            sanitized[key] = value
    return sanitized


def _extract_input_schema(nodes: list) -> dict[str, Any]:
    """Extract input parameter schema from the Start/Trigger node."""
    for node in nodes:
        if node.type == "Start":
            schema = node.parameters.get("input_schema", {})
            if schema:
                return schema
    return {}


def _extract_webhook_path(nodes: list, workflow_id: str) -> str | None:
    """Return the webhook path if the workflow has a Webhook trigger node."""
    for node in nodes:
        if node.type == "Webhook":
            custom_path = node.parameters.get("path", "")
            if custom_path:
                return f"/webhook/p/{custom_path}"
            return f"/webhook/{workflow_id}"
    return None


def _extract_webhook_response_mode(nodes: list) -> str | None:
    """Return the webhook response mode if a Webhook trigger exists."""
    for node in nodes:
        if node.type == "Webhook":
            return node.parameters.get("responseMode", "onReceived")
    return None


def _unwrap_node_output(output: Any) -> Any:
    """Unwrap n8n-style node output list to get the first item's json data.

    Node outputs are stored as: [{"json": {...}, "binary": null}]
    This returns the inner json dict.
    """
    if isinstance(output, list) and output:
        first = output[0]
        if isinstance(first, dict) and "json" in first:
            return first["json"]
        if isinstance(first, dict):
            return first
    if isinstance(output, dict):
        return output
    return None
