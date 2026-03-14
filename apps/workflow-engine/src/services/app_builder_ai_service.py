"""AI service for App Builder — generates React TSX components via LLM.

The LLM generates a single React component as TSX source using raw HTML
elements + Tailwind CSS classes. No custom component library — just standard
React with useState/useEffect/useCallback and native HTML elements.
"""

from __future__ import annotations

import asyncio
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
from .schema_analyzer import analyze_schema_cached, format_field_catalog as _format_field_catalog
from .schema_inference import infer_json_schema, truncate_sample

logger = logging.getLogger(__name__)

_GENERATOR_MODEL = "gemini-2.5-pro"
_MAX_HISTORY = 20

# ---------------------------------------------------------------------------
# Module-level cache for extract_workflow_schema results.
# Keyed by (workflow_id, latest_exec_id) — invalidated automatically when
# a new execution succeeds.  Avoids redundant DB + LLM work across chat turns.
# ---------------------------------------------------------------------------
_SCHEMA_CACHE: dict[tuple[str, str], WorkflowSchemaResponse] = {}
_SCHEMA_CACHE_MAX = 32
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
                max_tokens=20_000,
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

        # 7. Emit code (preferred) or text fallback — never both.
        # Emitting both causes the frontend to receive split events that
        # break iframe embedding.

        if source_code:
            yield _sse("message", {"type": "code", "source": source_code})
        elif text_content:
            yield _sse("message", {"type": "text", "content": text_content})
        else:
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

        # Parallel DB lookups — workflow definition + latest execution ID
        stored, latest_exec_id = await asyncio.gather(
            self._workflow_repo.get(workflow_id),
            self._execution_repo.find_latest_successful_id(workflow_id),
        )

        if not stored:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Check module-level cache — keyed by (workflow_id, exec_id) so it
        # auto-invalidates when a new execution succeeds.
        cache_key = (workflow_id, latest_exec_id or "")
        if cache_key in _SCHEMA_CACHE:
            logger.debug("Schema extraction cache hit for %s", workflow_id)
            return _SCHEMA_CACHE[cache_key]

        workflow = stored.workflow
        node_type_map = {n.name: n.type for n in workflow.nodes}
        node_params_map = {n.name: n.parameters for n in workflow.nodes}

        # Extract input schema from Start node parameters
        input_schema = _extract_input_schema(workflow.nodes)

        # Detect webhook trigger and response mode
        webhook_path = _extract_webhook_path(workflow.nodes, workflow_id)
        webhook_response_mode = _extract_webhook_response_mode(workflow.nodes)

        if not latest_exec_id:
            response = WorkflowSchemaResponse(
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
            # Don't cache no-execution responses — next call may find one
            return response

        node_outputs = await self._node_output_repo.get_outputs(latest_exec_id)

        node_schemas = []
        webhook_body_schema = None
        webhook_body_sample = None
        # Track the last successful non-webhook output in a single pass
        last_raw_output: Any = None

        for output in node_outputs:
            if output.status != "success" or not output.output:
                continue

            ntype = node_type_map.get(output.node_name, "Unknown")

            # Extract the webhook body shape from the Webhook node's output
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
                    # Defer sample_data — only compute for the last node (prompt only uses it)
                )
            )
            # Keep overwriting — last iteration wins (single pass, no second loop)
            last_raw_output = output.output

        # Only the last node needs sample_data + field_catalog (prompt only uses these)
        if node_schemas and last_raw_output is not None:
            node_schemas[-1].sample_data = truncate_sample(last_raw_output, max_items=3)
            catalog = await analyze_schema_cached(last_raw_output)
            if catalog:
                node_schemas[-1].field_catalog = catalog

        response = WorkflowSchemaResponse(
            workflow_id=workflow_id,
            workflow_name=stored.name,
            input_schema=input_schema,
            webhook_path=webhook_path,
            webhook_response_mode=webhook_response_mode,
            webhook_body_schema=webhook_body_schema,
            webhook_body_sample=webhook_body_sample,
            node_schemas=node_schemas,
        )

        # Populate cache (evict oldest if full)
        if len(_SCHEMA_CACHE) >= _SCHEMA_CACHE_MAX:
            oldest = next(iter(_SCHEMA_CACHE))
            del _SCHEMA_CACHE[oldest]
        _SCHEMA_CACHE[cache_key] = response

        return response

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

        # -- API call snippet (kept compact — one example per mode) --
        if is_webhook:
            response_mode = ws.webhook_response_mode or "lastNode"

            lines.append("\nSend the request body directly (no envelope wrapper).")
            if ws.webhook_body_schema:
                lines.append(
                    f"\nRequest body schema:\n```json\n{json.dumps(ws.webhook_body_schema, separators=(',', ':'))}\n```"
                )

            if response_mode == "onReceived":
                lines.append(
                    f"\nCall: `await window.__apiFetch('{api_path}', "
                    f"{{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, "
                    f"body: JSON.stringify(yourBody) }})`"
                    f"\nResponse (async): `{{ status, executionId, message }}` — workflow runs in background."
                )
            else:
                lines.append(
                    f"\nCall: `await window.__apiFetch('{api_path}', "
                    f"{{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, "
                    f"body: JSON.stringify(yourBody) }})`"
                    f"\nResponse: `{{ status, executionId, data: [...items] }}` — access items via `result.data`."
                )
        else:
            lines.append(
                f"\nWrap body in `input_data`: `await window.__apiFetch('{api_path}', "
                f"{{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, "
                f"body: JSON.stringify({{ input_data: {{ ... }} }}) }})`"
                f"\nResponse: `{{ data: {{ \"{last_name}\": <output>, ... }}, status: \"success\" }}`"
            )

        # -- Response fields --
        if last_node:
            if last_node.field_catalog:
                lines.append(f"\n### Response Fields (\"{last_name}\")\n")
                lines.append(_format_field_catalog(last_node.field_catalog))
            else:
                lines.append(f"\n### Response Schema (\"{last_name}\")\n")
                lines.append(f"```json\n{json.dumps(last_node.output_schema, separators=(',', ':'))}\n```")
                if last_node.sample_data is not None:
                    lines.append(f"\nSample:\n```json\n{json.dumps(last_node.sample_data, separators=(',', ':'))}\n```")

        return "\n".join(lines)


# ── Constants ─────────────────────────────────────────────────────────

_BASE_PROMPT = """
You are an expert React developer. You generate complete, self-contained React components as TSX.

## CRITICAL — Output Rules

Your response MUST contain ONLY a single ```tsx fenced code block.
Do NOT include any text, explanation, or commentary before or after the code block.
No preambles, no summaries, no "Here's what I built." Code block only.

```tsx
export default function App() {
  return <div>...</div>;
}
```

## Component Rules

- SINGLE default-exported React function component.
- Raw HTML elements only (`<div>`, `<button>`, `<input>`, `<table>`, etc.) — no custom component imports.
- Tailwind CSS classes for all styling. No inline styles unless absolutely necessary.
- `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`, `useReducer` are available as globals — do NOT import anything.
- For API calls use `window.__apiFetch(url, opts)` — returns a Promise with JSON. Accepts `/api/...` and `/webhook/...` paths.
- Always return the COMPLETE component — never partial code or diffs.
- Handle loading and error states for async operations.
- Use semantic HTML and accessible patterns.
- Convert markdown into JSX elements (e.g. `**bold**` → `<strong>`, `# Heading` → `<h1>`, lists → `<ul><li>`)."""


# ── Helpers ───────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


# Opening fence pattern — matches ```tsx or ```typescript at the start of a line
_TSX_OPEN_RE = re.compile(r"^```(?:tsx|typescript)\s*$", re.MULTILINE)


def _parse_llm_output(text: str) -> tuple[str | None, str]:
    """
    Parse LLM output into (source_code, text_content).

    Extracts TSX code from the outermost ```tsx ... ``` fences. The closing
    fence is found by scanning "backwards" from the end of the text so that
    triple-backtick strings inside the generated code (e.g.
    "line.startsWith('```')") are not mistakenly treated as the closing
    fence.
    """

    source_code: str | None = None
    block_start: int | None = None
    block_end: int | None = None

    try:
        # Step 1: Find the first ```tsx opening fence.
        open_match = _TSX_OPEN_RE.search(text)
        if open_match:
            block_start = open_match.start()
            # Skip past the fence + trailing newline (if present)
            code_start = open_match.end()
            if code_start < len(text) and text[code_start] == "\n":
                code_start += 1

            # Step 2: Find the LAST ``` in the entire text — this is the
            # outermost closing fence and avoids false matches inside code.
            close_pos = text.rfind("```")
            if close_pos > code_start:
                block_end = close_pos + 3
                source_code = text[code_start:close_pos].strip()
    except Exception:
        logger.warning("App builder: failed to parse TSX block from LLM output", exc_info=True)
        source_code = None

    # Step 3: Derive leftover text (everything outside the code block).
    if source_code is not None and block_start is not None and block_end is not None:
        before = text[:block_start].strip()
        after = text[block_end:].strip()
        parts = [p for p in (before, after) if p]
        text_content = "\n\n".join(parts)
    else:
        text_content = text.strip()

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
