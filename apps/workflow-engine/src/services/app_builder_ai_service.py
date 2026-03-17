"""AI service for App Builder — tool-based agent loop.

The LLM iteratively builds/modifies a React app using file-manipulation tools
(list_files, read_file, write_file, edit_file, delete_file, search_files,
get_project_summary).  When it stops issuing tool calls the loop ends and
the final working set is emitted.

The LLM generates React components as TSX source using raw HTML
elements + Tailwind CSS classes. No custom component library — just standard
React with useState/useEffect/useCallback and native HTML elements.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from .tsx_parser import parse_tsx_file

logger = logging.getLogger(__name__)

_HEAVY_MODEL = "claude-sonnet-4-6"
_LIGHT_MODEL = "claude-sonnet-4-6"
MAX_AGENT_TURNS = 25
MAX_CONSECUTIVE_ERRORS = 3

# ---------------------------------------------------------------------------
# Module-level cache for extract_workflow_schema results.
# Keyed by (workflow_id, latest_exec_id) — invalidated automatically when
# a new execution succeeds.  Avoids redundant DB + LLM work across chat turns.
# ---------------------------------------------------------------------------
_SCHEMA_CACHE: dict[tuple[str, str], WorkflowSchemaResponse] = {}
_SCHEMA_CACHE_MAX = 32


# ── Helpers ───────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


def _tool_call_signature(tool_calls: list) -> str:
    """Create a hashable signature from tool calls for duplicate detection."""
    parts = []
    for tc in tool_calls:
        args_str = json.dumps(tc.args, sort_keys=True)
        parts.append(f"{tc.name}:{args_str}")
    return "|".join(sorted(parts))


def _summarize_tool_args(args: dict) -> dict:
    """Summarize tool args for SSE display (truncate long content)."""
    summary = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 200:
            summary[k] = v[:200] + f"... ({len(v)} chars)"
        else:
            summary[k] = v
    return summary


def _truncate_for_display(result: str) -> str:
    """Truncate tool result for SSE display."""
    if len(result) > 500:
        return result[:500] + f"... ({len(result)} chars total)"
    return result


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


# ── Agent System Prompt ───────────────────────────────────────────────

_AGENT_SYSTEM_PROMPT = """\
You are an React app builder. You create and modify multi-file React TSX apps.

## Environment
Sandboxed iframe with React 18, ReactDOM, Tailwind CSS pre-loaded. \
The sandbox mounts the default export from `App.tsx` automatically. \
Do NOT create index.html, index.tsx, index.css, or bootstrap code.

## Rules
- `App.tsx` is the entry point — use `export default function App()` (NOT a separate `export default App` at the bottom).
- Standard `import`/`export` between files. Tailwind classes for styling.
- Use `fetch()` for API calls. The exact URL is provided in the workflow context below.
- No npm packages beyond React. Raw HTML elements only.
- Handle loading/error states for async operations.
- You MUST use tools to create/modify files. If the user asks a question (not requesting changes), respond with text only.

## Sandbox Constraints
Sandboxed iframe: `html, body, #root` are `height:100%; overflow:clip`. Many browser APIs are blocked.

**Layout:** Outermost wrapper must be `h-full flex flex-col` (NEVER `h-screen`/`100vh`). \
Scrollable areas: `flex-1 min-h-0 overflow-y-auto` (min-h-0 is required). \
No `position:fixed` for layout — use flex. Only `fixed inset-0 z-50` for modal overlays. \
`sticky` only works inside an `overflow-y-auto` container.

**Scrolling:** Use `ref.current.scrollTop = ref.current.scrollHeight` — NEVER `scrollIntoView()`, `window.scrollTo()`, or `document.scrollTop`.

**Banned APIs (use alternatives):**
- `localStorage`/`sessionStorage`/`document.cookie` → `useState`/refs
- `window.history`/`window.location`/`popstate` → `useState` for routing
- `alert()`/`confirm()`/`prompt()`/`window.open()` → React UI
- `navigator.clipboard` → hidden textarea + `document.execCommand('copy')`
- `document.body.style`/`document.documentElement.style` → don't touch
- `requestFullscreen()` → `absolute inset-0 z-50`
- `ReactDOM.createPortal(x, document.body)` → inline overlay in `relative` container
- `document.title` → render as heading
- `Worker`/`ServiceWorker`/`EventSource`/`WebSocket` → use polling with `fetch`

**Fetch:** Only `/api/` and `/webhook/` paths work (proxied). Returns complete JSON only. \
No streaming/ReadableStream. No `AbortController` signal. No `FormData`/`Blob` bodies — read files with `FileReader`, send as JSON. External URLs fail CORS.

**Assets:** All URLs must be absolute (`https://...`). Use inline SVG or `https://placehold.co/` for placeholders. \
Audio/video `play()` only in click handlers. Canvas external images need `crossOrigin='anonymous'`.

**Cleanup:** Always return cleanup from `useEffect` for intervals/timeouts/listeners — sandbox re-renders on code updates.

## Tool Strategy
- For new apps: one `write_files` call with all files. Done in 1 turn.
- For edits: use `read_definition` to inspect specific functions, then `edit_file` for surgical changes.
- Use `read_files` / `write_files` batch when touching 3+ files at once.
- Target: **1-2 turns** for simple changes. Do NOT guess — inspect first when unsure.

## Comments — these are parsed and used as project context
Every file MUST start with a `/** ... */` file comment describing what the file does, its role in the app, and key design decisions. Example:
```
/** Dashboard stats cards — displays KPI metrics with trend indicators.
 * Uses gradient backgrounds per card type. Data is static/mock for now.
 * Each card shows: metric value, trend percentage, and a mini sparkline. */
```
Every exported function/component MUST have a `/** ... */` JSDoc comment above it explaining:
- What it does and why it exists
- Props/params and their purpose
- Key behaviors (e.g. "Fetches data on mount", "Collapses on mobile")
Keep comments architectural, not obvious. "Renders a button" is useless. "Primary CTA with loading state — disables during API calls to prevent double-submit" is useful.

## Response Style
- When done, respond with ONE short sentence (max 15 words). Example: "Created a dashboard with stats, charts, and activity feed."
- Do NOT list components, features, or design details. The user can see the preview.
- No markdown tables, no bullet lists, no emojis in your final response.
"""


# ── Service Class ─────────────────────────────────────────────────────


class AppBuilderAIService:
    """Generates and modifies React TSX apps via a tool-based agent loop."""

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

        # 1. Resolve current files (multi-file aware)
        working_set, resolve_warning = await self._resolve_current_files(request)
        if resolve_warning:
            yield _sse("message", {"type": "text", "content": resolve_warning})

        # 2. Extract workflow schemas for all linked workflows
        workflow_schemas: list[WorkflowSchemaResponse] = []
        for wf_id in request.workflow_ids:
            try:
                schema = await self.extract_workflow_schema(wf_id)
                workflow_schemas.append(schema)
            except Exception:
                logger.warning("Failed to extract schema for workflow %s", wf_id, exc_info=True)

        # 3. Snapshot initial state to detect if agent modified anything
        initial_snapshot = {f["path"]: f["content"] for f in working_set}

        # 4. Run agent loop
        async for event in self._run_agent_loop(
            working_set, workflow_schemas, request.message,
            request.conversation_history,
        ):
            yield event

        # 5. Emit final files only if the working set was modified
        current_snapshot = {f["path"]: f["content"] for f in working_set}
        files_changed = current_snapshot != initial_snapshot

        if working_set and files_changed:
            _ENTRY_PATHS = {"App.tsx", "src/App.tsx", "app.tsx", "src/app.tsx"}
            entry = next(
                (f for f in working_set if f["path"] in _ENTRY_PATHS),
                working_set[0] if working_set else None,
            )
            # Strip parsed_index before sending — frontend doesn't need it
            client_files = [{"path": f["path"], "content": f["content"]} for f in working_set]
            yield _sse("message", {
                "type": "code",
                "files": client_files,
                "source": entry["content"] if entry else "",
            })
        elif not working_set and not files_changed:
            # No files at all and nothing was created — genuine failure
            yield _sse("message", {
                "type": "text",
                "content": "I wasn't able to create anything this time. Could you describe what you'd like in more detail?",
            })
        # else: question-only flow — text already emitted, no code event needed

        yield _sse("done", {"type": "done"})

    # ── Workflow Schema Extraction ────────────────────────────────────

    async def extract_workflow_schema(
        self, workflow_id: str
    ) -> WorkflowSchemaResponse:
        """Pull schema from the latest successful execution of a workflow."""

        # Sequential DB lookups — same session can't run concurrent queries
        stored = await self._workflow_repo.get(workflow_id)
        latest_exec_id = await self._execution_repo.find_latest_successful_id(workflow_id)

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

    # ── Agent Loop ────────────────────────────────────────────────────

    async def _run_agent_loop(
        self,
        working_set: list[dict[str, str]],
        workflow_schemas: list[WorkflowSchemaResponse],
        user_message: str,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Tool-use agent loop. Modifies working_set in place.

        Always starts with the fast model. The LLM can call ``escalate``
        to switch to the heavy model for the remaining turns. This way
        the fast model handles simple edits end-to-end and does the
        groundwork (reads, searches) before handing off complex tasks.
        """

        # Build system prompt
        system_prompt = self._build_agent_system_prompt(workflow_schemas)

        # Build initial user message — project index prepended to first user content
        project_preamble = ""
        if working_set:
            project_preamble = self._build_project_index(working_set) + "\n\n"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Conversation history: last 3 user/assistant pairs for multi-turn context.
        # Older user messages are truncated — intent matters more than exact wording.
        if conversation_history:
            recent = conversation_history[-6:]  # 3 pairs = 6 entries
            for i, entry in enumerate(recent):
                role = entry.get("role", "")
                content = entry.get("content", "")
                if role not in ("user", "assistant") or not content:
                    continue
                # Truncate older user messages (not the most recent pair)
                if role == "user" and i < len(recent) - 2 and len(content) > 150:
                    content = content[:150] + "…"
                messages.append({"role": role, "content": content})

        # Current request — prepend project index to the user message
        messages.append({"role": "user", "content": project_preamble + user_message})

        # First request (no existing files) → use heavy model for better
        # initial scaffolding.  Subsequent requests start with the fast model
        # and can escalate via the `escalate` tool if needed.
        is_first_request = not working_set and not conversation_history
        current_model = _HEAVY_MODEL if is_first_request else _LIGHT_MODEL

        # Build tool definitions — for first requests (empty project), only
        # expose write_files.  Fewer tools = fewer MALFORMED_FUNCTION_CALL
        # errors from Gemini 2.5 Pro's thinking mode.
        if is_first_request:
            tools = [t for t in self._build_agent_tools() if t["name"] == "write_files"]
        else:
            tools = self._build_agent_tools()

        turn = 0
        consecutive_errors = 0
        last_tool_sig: str | None = None
        duplicate_count = 0

        while turn < MAX_AGENT_TURNS:
            turn += 1
            yield _sse("message", {"type": "phase", "phase": "thinking", "message": f"Turn {turn}..."})

            # Trim stale read results from history to keep context lean
            if turn > 2:
                messages = self._trim_tool_context(messages)

            response = await call_llm(
                model=current_model,
                messages=messages,
                tools=tools,
                temperature=0.3,
                max_tokens=16_000,
            )

            # Malformed tool call — count as error and retry the turn
            if response.malformed_tool_call:
                consecutive_errors += 1
                logger.warning("Turn %d: malformed tool call (consecutive_errors=%d)", turn, consecutive_errors)
                if consecutive_errors >= 2 and current_model == _LIGHT_MODEL:
                    logger.info("App builder: auto-escalating after malformed tool calls")
                    yield _sse("message", {"type": "phase", "phase": "escalating", "message": "Switching to advanced model"})
                    current_model = _HEAVY_MODEL
                    consecutive_errors = 0
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    yield _sse("message", {"type": "text", "content": "I ran into some trouble generating the code. Let's try again — could you rephrase your request?"})
                    return
                # Add the garbled text as assistant message so context moves forward
                if response.text:
                    messages.append({"role": "assistant", "content": response.text})
                    messages.append({"role": "user", "content": "Your previous response had a malformed tool call. Please try again."})
                continue

            # No tool calls = agent is done
            if not response.tool_calls:
                text = response.text or ""
                if text:
                    yield _sse("message", {"type": "text", "content": text})
                return

            # Detect duplicate tool calls (semantic loop)
            current_sig = _tool_call_signature(response.tool_calls)
            if current_sig == last_tool_sig:
                duplicate_count += 1
                if duplicate_count >= 2:
                    yield _sse("message", {"type": "text", "content": "I seem to be going in circles. Could you try rephrasing what you'd like changed?"})
                    return
            else:
                duplicate_count = 0
            last_tool_sig = current_sig

            # Append assistant message
            messages.append(response.get_assistant_message())

            if response.text:
                yield _sse("message", {"type": "thinking", "content": response.text})

            # Execute each tool call
            for tc in response.tool_calls:
                # Handle escalate — switch to heavy model for remaining turns
                if tc.name == "escalate":
                    reason = tc.args.get("reason", "complex task")
                    logger.info("App builder: escalating to heavy model — %s", reason)
                    yield _sse("message", {"type": "phase", "phase": "escalating", "message": reason})
                    current_model = _HEAVY_MODEL
                    messages.append({
                        "role": "tool",
                        "content": f"Switched to advanced model. Continue with the task.",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                    })
                    continue

                yield _sse("message", {"type": "tool_call", "id": tc.id, "tool": tc.name, "args": _summarize_tool_args(tc.args)})

                try:
                    result = self._execute_agent_tool(tc.name, tc.args, working_set)
                    consecutive_errors = 0
                except Exception as e:
                    result = f"Error: {e}"
                    consecutive_errors += 1

                yield _sse("message", {"type": "tool_result", "id": tc.id, "tool": tc.name, "result": _truncate_for_display(result)})

                messages.append({
                    "role": "tool",
                    "content": result if isinstance(result, str) else json.dumps(result),
                    "tool_call_id": tc.id,
                    "name": tc.name,
                })

            # Auto-escalate after 2 consecutive errors (before hitting the hard stop at 3)
            if consecutive_errors >= 2 and current_model == _LIGHT_MODEL:
                logger.info("App builder: auto-escalating after %d consecutive errors", consecutive_errors)
                yield _sse("message", {"type": "phase", "phase": "escalating", "message": "Switching to advanced model after errors"})
                current_model = _HEAVY_MODEL
                consecutive_errors = 0

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                yield _sse("message", {"type": "text", "content": "I hit a few errors in a row and stopped to avoid making things worse. Want to try a different approach?"})
                return

        yield _sse("message", {"type": "text", "content": "I've been working on this for a while and want to check in. Does the current state look right, or should I keep going?"})

    # ── Agent Tools ───────────────────────────────────────────────────

    def _build_agent_tools(self) -> list[dict]:
        return [
            {
                "name": "list_files",
                "description": "List all files with sizes and definition summaries.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "read_files",
                "description": "Read one or more files with line numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["paths"],
                },
            },
            {
                "name": "write_files",
                "description": "Create or overwrite one or more files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                "required": ["path", "content"],
                            },
                        },
                    },
                    "required": ["files"],
                },
            },
            {
                "name": "edit_files",
                "description": "Replace exact unique strings in one or more files. Read first to get exact content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "old_string": {"type": "string", "description": "Exact text to find (must be unique)"},
                                    "new_string": {"type": "string", "description": "Replacement text"},
                                },
                                "required": ["path", "old_string", "new_string"],
                            },
                        },
                    },
                    "required": ["edits"],
                },
            },
            {
                "name": "delete_file",
                "description": "Delete a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
            {
                "name": "search_files",
                "description": "Search all files for a regex/substring. Returns matches with context lines.",
                "parameters": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            },
            {
                "name": "escalate",
                "description": "Switch to a stronger model for complex tasks touching 4+ files or after 2+ errors.",
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        ]

    def _execute_agent_tool(self, name: str, args: dict, working_set: list[dict]) -> str:
        dispatch = {
            "list_files": self._tool_list_files,
            "read_files": self._tool_read_files,
            "write_files": self._tool_write_files,
            "edit_files": self._tool_edit_files,
            "delete_file": self._tool_delete_file,
            "search_files": self._tool_search_files,
        }
        handler = dispatch.get(name)
        if not handler:
            return f"Error: Unknown tool '{name}'"
        return handler(args, working_set)

    def _tool_read_files(self, args: dict, working_set: list[dict]) -> str:
        paths = args.get("paths", [])
        if not paths:
            return "Error: 'paths' array is required."
        results = []
        for path in paths:
            f = next((f for f in working_set if f["path"] == path), None)
            if not f:
                results.append(f"Error: File '{path}' not found.")
                continue
            lines = f["content"].split("\n")
            numbered = [f"{i+1:4d} | {line}" for i, line in enumerate(lines)]
            results.append(f"## {path}\n\n" + "\n".join(numbered))
        return "\n\n".join(results)

    def _tool_write_files(self, args: dict, working_set: list[dict]) -> str:
        files = args.get("files", [])
        if not files:
            return "Error: 'files' array is required."
        results = []
        for entry in files:
            path = entry.get("path", "")
            content = entry.get("content", "")
            if not path or not content:
                results.append("Error: 'path' and 'content' are required.")
                continue
            parsed_index = parse_tsx_file(content) if path.endswith((".tsx", ".ts", ".jsx", ".js")) else None
            existing = next((f for f in working_set if f["path"] == path), None)
            if existing:
                existing["content"] = content
                existing["parsed_index"] = parsed_index
                action = "Updated"
            else:
                working_set.append({"path": path, "content": content, "parsed_index": parsed_index})
                action = "Created"
            info = f"{action} {path} ({len(content)} chars)"
            if parsed_index and parsed_index.get("definitions"):
                names = [d["name"] for d in parsed_index["definitions"][:5]]
                info += f" — definitions: {', '.join(names)}"
            results.append(info)
        return "\n".join(results)

    def _tool_list_files(self, args: dict, working_set: list[dict]) -> str:
        if not working_set:
            return "No files in project."
        lines = []
        for f in working_set:
            size = len(f["content"].encode())
            line = f"- {f['path']} ({size} bytes)"
            idx = f.get("parsed_index")
            if idx:
                if idx.get("file_comment"):
                    line += f"\n  {idx['file_comment']}"
                defs = idx.get("definitions", [])
                if defs:
                    parts = [f"{d['name']}({d['kind']})" for d in defs[:8]]
                    line += f"\n  Definitions: {', '.join(parts)}"
            lines.append(line)
        return "\n".join(lines)

    def _tool_edit_files(self, args: dict, working_set: list[dict]) -> str:
        edits = args.get("edits", [])
        if not edits:
            return "Error: 'edits' array is required."
        results = []
        for edit in edits:
            path = edit.get("path", "")
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")
            if not path or not old_string:
                results.append("Error: 'path' and 'old_string' are required.")
                continue
            f = next((f for f in working_set if f["path"] == path), None)
            if not f:
                results.append(f"Error: File '{path}' not found.")
                continue
            if old_string not in f["content"]:
                results.append(f"Error: old_string not found in {path}. Read the file first.")
                continue
            count = f["content"].count(old_string)
            if count > 1:
                results.append(f"Error: old_string found {count} times in {path}. Provide more context.")
                continue
            f["content"] = f["content"].replace(old_string, new_string, 1)
            if path.endswith((".tsx", ".ts", ".jsx", ".js")):
                f["parsed_index"] = parse_tsx_file(f["content"])
            results.append(f"Edited {path}: replaced {len(old_string)} chars with {len(new_string)} chars")
        return "\n".join(results)

    def _tool_delete_file(self, args: dict, working_set: list[dict]) -> str:
        path = args.get("path", "")
        idx = next((i for i, f in enumerate(working_set) if f["path"] == path), None)
        if idx is None:
            return f"Error: File '{path}' not found."
        working_set.pop(idx)
        return f"Deleted {path}"

    def _tool_search_files(self, args: dict, working_set: list[dict]) -> str:
        import re
        pattern = args.get("pattern", "")
        if not pattern:
            return "Error: 'pattern' is required."
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        results = []
        for f in working_set:
            lines = f["content"].split("\n")
            for i, line in enumerate(lines):
                if regex.search(line):
                    snippet_lines = []
                    if i > 0:
                        snippet_lines.append(f"  {i}: {lines[i-1]}")
                    snippet_lines.append(f"  {i+1}: {line}  <- match")
                    if i < len(lines) - 1:
                        snippet_lines.append(f"  {i+2}: {lines[i+1]}")
                    results.append(f"{f['path']}:{i+1}\n" + "\n".join(snippet_lines))
        if not results:
            return f"No matches for '{pattern}'."
        return f"Found {len(results)} match(es):\n\n" + "\n\n".join(results[:20])

    # ── System Prompt Building ────────────────────────────────────────

    @staticmethod
    def _trim_tool_context(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop large read-only tool results from earlier turns to keep context lean.

        Keeps write/edit/delete results (small) and the last 4 read results.
        Also protects any read whose file was edited in the next turn — the LLM
        needs that content to construct accurate edit_file old_string values.
        """
        _READ_TOOLS = {"read_files", "search_files", "list_files"}
        _WRITE_TOOLS = {"write_files", "edit_files"}
        _STUB = "[result trimmed — call again if needed]"
        _KEEP_RECENT = 4

        # Find read-tool result indices
        read_indices: list[int] = []
        for i, m in enumerate(messages):
            if m.get("role") == "tool" and m.get("name") in _READ_TOOLS:
                read_indices.append(i)

        if len(read_indices) <= _KEEP_RECENT:
            return messages

        # Protect reads that were followed by an edit of the same file
        protected: set[int] = set()
        for idx in read_indices:
            read_msg = messages[idx]
            read_path = ""
            # Extract the path from the read result content (starts with "## path")
            content = read_msg.get("content", "")
            if content.startswith("## "):
                read_path = content.split("\n")[0].removeprefix("## ").split(" :: ")[0].strip()

            if read_path:
                # Check if any subsequent tool message (before the next assistant msg) edits this file
                for j in range(idx + 1, min(idx + 6, len(messages))):
                    m2 = messages[j]
                    if m2.get("role") == "tool" and m2.get("name") in _WRITE_TOOLS:
                        # The edit result mentions the path
                        if read_path in m2.get("content", ""):
                            protected.add(idx)
                            break
                    if m2.get("role") == "assistant":
                        break  # different turn

        # Trim old reads that aren't protected and aren't in the last N
        to_trim = set(read_indices[:-_KEEP_RECENT]) - protected
        if not to_trim:
            return messages

        trimmed = []
        for i, m in enumerate(messages):
            if i in to_trim:
                trimmed.append({**m, "content": _STUB})
            else:
                trimmed.append(m)
        return trimmed

    def _build_agent_system_prompt(self, workflow_schemas: list[WorkflowSchemaResponse]) -> str:
        parts = [_AGENT_SYSTEM_PROMPT]
        for ws in workflow_schemas:
            parts.append(self._workflow_context_section(ws))
        return "\n\n".join(parts)

    # ── Context Building ─────────────────────────────────────────────

    def _build_project_index(self, working_set: list[dict]) -> str:
        """Build a compact pseudo-code index from parsed definitions.

        This is sent as the initial context instead of full file contents,
        so the LLM can reason about structure cheaply and pull only what it needs.
        """
        if not working_set:
            return "## Project\n\nEmpty project — no files yet."

        lines = [f"## Project Index ({len(working_set)} files)\n"]
        for f in working_set:
            size = len(f["content"].encode())
            lines.append(f"### {f['path']} ({size} bytes)")
            idx = f.get("parsed_index")
            if idx:
                if idx.get("file_comment"):
                    lines.append(idx["file_comment"])
                for d in idx.get("definitions", []):
                    exported = " [exported]" if d.get("exported") else ""
                    doc = f" — {d['doc']}" if d.get("doc") else ""
                    lines.append(f"- {d['name']} ({d['kind']}) lines {d['line']}-{d['end_line']}{exported}{doc}")
            else:
                lines.append(f"({f.get('file_type', 'unknown')} file)")
            lines.append("")
        return "\n".join(lines)

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

        lines.append("\n### API Request\n")

        if is_webhook:
            response_mode = ws.webhook_response_mode or "lastNode"

            body_hint = "{ /* your data */ }"
            if ws.webhook_body_schema:
                lines.append(
                    f"Request body schema:\n```json\n{json.dumps(ws.webhook_body_schema, separators=(',', ':'))}\n```\n"
                )

            lines.append("```typescript")
            lines.append(f'const res = await fetch("{api_path}", {{')
            lines.append('  method: "POST",')
            lines.append('  headers: { "Content-Type": "application/json" },')
            lines.append(f"  body: JSON.stringify({body_hint}),")
            lines.append("});")
            lines.append("const result = await res.json();")
            lines.append("```\n")

            if response_mode == "onReceived":
                lines.append("Response shape: `{ status, executionId, message }` — workflow runs in background.")
            else:
                lines.append("Response shape: `{ status, executionId, data: [...items] }` — access items via `result.data`.")
        else:
            lines.append("```typescript")
            lines.append(f'const res = await fetch("{api_path}", {{')
            lines.append('  method: "POST",')
            lines.append('  headers: { "Content-Type": "application/json" },')
            lines.append("  body: JSON.stringify({ input_data: { /* your params */ } }),")
            lines.append("});")
            lines.append("const result = await res.json();")
            lines.append("```\n")
            lines.append(f'Response shape: `{{ data: {{ "{last_name}": <output>, ... }}, status: "success" }}`')

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

    # ── File Resolution ───────────────────────────────────────────────

    async def _resolve_current_files(
        self, request: AppBuilderChatRequest
    ) -> tuple[list[dict[str, str]], str | None]:
        """Resolve the current set of files for an app.

        Returns (files, warning). Warning is set if an app_id was provided
        but no files could be loaded — so the caller can inform the user.
        """

        # Get files from the current version
        if request.app_id and request.current_version_id:
            try:
                files = await self._app_service.get_version_files(
                    request.app_id, request.current_version_id
                )
                if files:
                    return [{"path": f["path"], "content": f["content"], "parsed_index": f.get("parsed_index")} for f in files], None
            except Exception:
                logger.warning(
                    "Failed to resolve files for app %s version %s",
                    request.app_id, request.current_version_id, exc_info=True,
                )

        # No version specified — try loading from app detail
        if request.app_id:
            try:
                app_detail = await self._app_service.get_app(request.app_id)
                if app_detail and app_detail.files:
                    return [{"path": f.path, "content": f.content, "parsed_index": f.parsed_index} for f in app_detail.files], None
            except Exception:
                logger.warning("Failed to resolve files for app %s", request.app_id, exc_info=True)

        # If an app_id was given but we couldn't load anything, warn
        warning = None
        if request.app_id:
            warning = "I couldn't find the existing files for this app, so I'll start fresh."
            logger.warning("App builder: no files found for app %s, starting from scratch", request.app_id)

        return [], warning
