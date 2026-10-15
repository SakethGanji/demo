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

import json
import logging
from typing import Any, AsyncGenerator

from ..engine.llm_provider import call_llm
from ..repositories.api_test_repository import ApiTestRepository
from .app_service import AppService
from ..db.models import ApiTestExecutionModel
from ..schemas.app_builder import AppBuilderChatRequest
from .schema_inference import summarize_response
from .tsx_parser import parse_tsx_file

logger = logging.getLogger(__name__)

_HEAVY_MODEL = "claude-sonnet-4-6"
_LIGHT_MODEL = "claude-sonnet-4-6"
MAX_AGENT_TURNS = 25
MAX_CONSECUTIVE_ERRORS = 3


# ── Helpers ───────────────────────────────────────────────────────────


def _sse(event: str, data: Any) -> dict[str, Any]:
    return {"event": event, "data": json.dumps(data)}


def _format_response_summary(summary: dict[str, Any] | None, lines: list[str]) -> None:
    """Append the response-summary section to `lines` for an api execution.

    Mirrors the kinds emitted by `summarize_response`. Tolerant of missing
    fields so a future schema bump doesn't break old rows.
    """
    if not summary:
        return
    kind = summary.get("kind")
    partial = " (body was truncated at capture)" if summary.get("partial") else ""

    if kind == "binary":
        lines.append("")
        lines.append(
            f"Binary response{partial} — render as a file download via "
            "`res.blob()` + `URL.createObjectURL()`."
        )
        if summary.get("filename"):
            lines.append(f"Suggested filename: `{summary['filename']}`")
        if summary.get("decode_error"):
            lines.append(f"_Note: {summary['decode_error']}_")
        return

    if kind == "json":
        schema = summary.get("schema", {})
        sample = summary.get("sample")
        lines.append("")
        lines.append(f"### Response Schema{partial}")
        lines.append(f"```json\n{json.dumps(schema, indent=2)}\n```")
        if sample is not None:
            lines.append("")
            lines.append("### Response Sample (truncated)")
            lines.append(f"```json\n{json.dumps(sample, indent=2)}\n```")
        if summary.get("note"):
            lines.append(f"_Note: {summary['note']}_")
        return

    if kind == "csv":
        delim = summary.get("delimiter", ",")
        label = "TSV" if delim == "\t" else "CSV"
        lines.append("")
        lines.append(f"### Response Template ({label}){partial}")
        if summary.get("header"):
            lines.append(f"- Header: `{summary['header']}`")
        lines.append(f"- Approx rows: {summary.get('row_count', 0)}")
        return

    if kind == "xml":
        lines.append("")
        lines.append(f"### Response Template (XML){partial}")
        if summary.get("root"):
            lines.append(f"- Root: `<{summary['root']}>`")
        tags = summary.get("tags") or []
        if tags:
            lines.append(f"- Tags seen: {', '.join(f'`<{t}>`' for t in tags)}")
        return

    if kind == "yaml":
        lines.append("")
        lines.append(f"### Response Template (YAML){partial}")
        keys = summary.get("top_keys") or []
        if keys:
            lines.append(f"- Top-level keys: {', '.join(f'`{k}`' for k in keys)}")
        else:
            lines.append("- (no top-level keys detected)")
        return

    if kind == "text":
        snippet = summary.get("snippet", "")
        if snippet:
            lines.append("")
            lines.append(f"```\n{snippet}\n```")
        if summary.get("decode_error"):
            lines.append(f"_Note: {summary['decode_error']}_")
        return

    # Unknown kind — emit nothing rather than raw JSON dump.


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
- Use `fetch()` for API calls. If an "Attached Endpoint" section is provided below, use that EXACT url, method, headers, and body shape — do not invent paths or parameters.
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

**Available browser APIs (free to use):**
`navigator.clipboard.{readText,writeText}` (works in click handlers), `requestFullscreen()`, `document.title`, `ReactDOM.createPortal`, `Worker`, `localStorage`/`sessionStorage`, `getUserMedia` (camera/mic), `geolocation`, `<a download>`, `Blob` + `URL.createObjectURL`. The sandbox grants these.

**Things that still don't work (use alternatives):**
- `window.history.pushState` / `popstate` → use `useState` for in-app routing (no real navigation in the iframe)
- `alert()`/`confirm()`/`prompt()` → React UI is better; native dialogs work but are ugly
- `EventSource` / `WebSocket` to external hosts → not proxied; use `fetch` polling instead
- `window.open(url)` to a different origin → may be blocked; prefer in-app navigation

**Fetch:** All `fetch()` calls (any absolute URL, including external hosts) are proxied through the parent and bypass CORS — call them as you normally would. Both JSON and binary responses are supported: use `response.json()` for JSON, `response.blob()` / `response.arrayBuffer()` for files. \
No streaming/ReadableStream. No `AbortController` signal. No `FormData` request bodies — read files with `FileReader`, send as JSON.

**File downloads:** When the response is a file (e.g. xlsx, pdf, image), use `const blob = await res.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = '<name>'; a.click(); URL.revokeObjectURL(url);` to trigger the download.

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


def build_spec_summary(slug: str, specs: list[dict[str, Any]], focus: set[str] | None = None) -> str:
    """System-prompt section describing the project's API (replaces the single attached endpoint)."""
    from .client_codegen import CLIENT_PATH
    lines = [
        "## Project API",
        f"This app belongs to a project with a typed API client at `{CLIENT_PATH}` (generated — never edit it).",
        "Import it from the entry file as `import { api } from './api/client'` (from `src/App.tsx`: `'../api/client'`).",
        "Call operations as `await api.<operation>(params?, body?, query?)`. Do not hand-write fetch() for these routes.",
        "If the UI needs an endpoint that is not listed, call the `declare_endpoint` tool instead of inventing one.",
        "",
    ]
    for s in sorted(specs, key=lambda x: x["name"]):
        if s.get("auth") == "api_key":
            continue
        mark = " ← focus" if focus and s["id"] in focus else ""
        stub = " (stub: returns 501 until implemented)" if (s.get("handler") or {}).get("type") == "stub" else ""
        lines.append(f"- `api.{s['name']}` — {s['method']} {s['path']}{stub}{mark}")
        if s.get("response_schema"):
            import json as _json
            lines.append(f"  response: `{_json.dumps(s['response_schema'])[:600]}`")
    return "\n".join(lines)


# ── Service Class ─────────────────────────────────────────────────────


class _LLMFailed(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class AppBuilderAIService:
    """Generates and modifies React TSX apps via a tool-based agent loop."""

    def __init__(
        self,
        api_test_repo: ApiTestRepository,
        app_service: AppService,
    ) -> None:
        self._api_test_repo = api_test_repo
        self._app_service = app_service

    # ── Public ────────────────────────────────────────────────────────

    async def stream_chat(
        self, request: AppBuilderChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream app builder response as SSE event dicts.

        Review C5: the stream always ends with ``done``; any failure first yields a
        ``message`` event ``{type:"error", code:"llm_failed"|"internal", message}``."""
        try:
            async for event in self._stream_chat(request):
                yield event
        except _LLMFailed as e:
            logger.warning("app builder: LLM call failed: %s", e.cause, exc_info=e.cause)
            yield _sse("message", {"type": "error", "code": "llm_failed",
                                   "message": f"The AI provider failed ({type(e.cause).__name__}): {str(e.cause)[:300]}"})
        except Exception as e:  # noqa: BLE001 - always end the stream cleanly
            logger.exception("app builder: stream failed")
            yield _sse("message", {"type": "error", "code": "internal",
                                   "message": f"The assistant stopped unexpectedly ({type(e).__name__})."})
        yield _sse("done", {"type": "done"})

    async def _stream_chat(
        self, request: AppBuilderChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        # 1. Resolve current files (multi-file aware)
        working_set, resolve_warning = await self._resolve_current_files(request)
        if resolve_warning:
            yield _sse("message", {"type": "text", "content": resolve_warning})

        # 2. Load attached API test executions for context
        api_executions: list[ApiTestExecutionModel] = []
        if request.api_execution_ids:
            api_executions = await self._api_test_repo.get_many(request.api_execution_ids)
            # Auto-add to the app's allow-list so the published page can replay
            # them. Only when an app_id is set — fresh apps with no row yet
            # will pick this up on the next chat after they save.
            if request.app_id:
                try:
                    await self._app_service.grant_api_executions(
                        request.app_id, [e.id for e in api_executions]
                    )
                except Exception:
                    logger.warning("grant_api_executions failed", exc_info=True)

        # 2b. Project API context (phase 6): spec summary, typed client in the working set,
        #     declare_endpoint tool for endpoints the UI needs.
        self._project = None
        self._spec_summary = None
        self._declared: list[dict[str, Any]] = []
        try:
            await self._load_project_api(request, working_set)
        except Exception:
            logger.warning("app builder: project API context failed", exc_info=True)

        # 3. Snapshot initial state to detect if agent modified anything
        initial_snapshot = {f["path"]: f["content"] for f in working_set}

        # 4. Run agent loop
        async for event in self._run_agent_loop(
            working_set, api_executions, request.message,
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

        # (review C6: stubs are created inside each declare_endpoint call, see _declare_endpoint_now)

    async def _load_project_api(self, request: AppBuilderChatRequest, working_set: list[dict]) -> None:
        from ..db.models import AppModel, ProjectModel
        from .client_codegen import CLIENT_PATH

        session = self._app_service._session
        project = None
        app = await session.get(AppModel, request.app_id) if request.app_id else None
        if request.project_id:
            from .project_service import resolve_project
            project = await resolve_project(session, request.project_id)
        elif app is not None:
            project = await session.get(ProjectModel, app.project_id)
        if project is None:
            return
        self._project = project
        from .api_route_service import ApiRouteService, route_spec
        specs = [route_spec(r) for r in await ApiRouteService(session).rows(project.id)]
        if not specs:
            return
        self._spec_summary = build_spec_summary(project.slug, specs, focus=set(request.route_ids or []))
        from ..routes.api_routes import client_source
        src, _ = await client_source(session, project, project.default_environment)
        working_set[:] = [f for f in working_set if f["path"] != CLIENT_PATH]
        working_set.append({"path": CLIENT_PATH, "content": src, "parsed_index": None})

    async def _declare_endpoint_now(self, args: dict, working_set: list[dict]) -> tuple[str, dict[str, Any]]:
        """Review C6: create (or find) the stub inside the tool call, report a per-stub result, and
        regenerate api/client.ts in the working set before the next model turn."""
        from .api_route_service import ApiRouteService, path_shape
        from .client_codegen import CLIENT_PATH
        from .project_service import ProjectError

        method, path = str(args.get("method") or "").upper(), args.get("path")
        info: dict[str, Any] = {"method": method, "path": path, "status": "error", "route": None, "error": None}
        if getattr(self, "_project", None) is None:
            info["error"] = {"code": "no_project", "message": "endpoints can only be declared for project apps"}
            return "Error: no project context — endpoints can only be declared for project apps.", info
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not isinstance(path, str) or not path.startswith("/"):
            info["error"] = {"code": "invalid_route", "message": "method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'"}
            return "Error: method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'.", info
        session = self._app_service._session
        svc = ApiRouteService(session)
        existing = next((r for r in await svc.rows(self._project.id)
                         if r.method == method and r.path_shape == path_shape(path)), None)
        if existing is not None:
            info.update(status="existing", route=await svc.to_dict(self._project, existing))
            text = f"{method} {path} already exists as operation '{existing.name}' — call api.{existing.name}(...)."
        else:
            payload = {k: args[k] for k in ("name", "description", "request_schema", "response_schema") if args.get(k)}
            try:
                res = await svc.create(self._project, {**payload, "method": method, "path": path, "handler": {"type": "stub"}},
                                       source="app_stub")
            except ProjectError as e:  # validation errors are raised before anything is written
                info["error"] = {"code": e.code, "message": e.message}
                return f"Error: could not declare {method} {path}: {e.code}: {e.message}", info
            info.update(status="created", route=res["route"])
            self._declared.append(res["route"])
            text = (f"Declared {method} {path}: created stub operation '{res['route']['name']}' (returns 501 until "
                    f"implemented). api/client.ts now has api.{res['route']['name']}(...).")
        try:  # the next turn sees the regenerated client
            from ..routes.api_routes import client_source
            src, _ = await client_source(session, self._project, self._project.default_environment)
            working_set[:] = [f for f in working_set if f["path"] != CLIENT_PATH]
            working_set.append({"path": CLIENT_PATH, "content": src, "parsed_index": None})
        except Exception:
            logger.warning("app builder: client regeneration failed", exc_info=True)
        return text, info

    # ── Agent Loop ────────────────────────────────────────────────────

    async def _run_agent_loop(
        self,
        working_set: list[dict[str, str]],
        api_executions: list[ApiTestExecutionModel],
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
        system_prompt = self._build_agent_system_prompt(api_executions)

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
            # Review C6: declare_endpoint is offered on every turn, including the first.
            tools = [t for t in self._build_agent_tools() if t["name"] in ("write_files", "declare_endpoint")]
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

            try:
                response = await call_llm(
                    model=current_model,
                    messages=messages,
                    tools=tools,
                    temperature=0.3,
                    max_tokens=16_000,
                )
            except Exception as e:  # review C5: surfaced as an `error` event, then `done`
                raise _LLMFailed(e) from e

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

                declared = None
                try:
                    if tc.name == "declare_endpoint":
                        result, declared = await self._declare_endpoint_now(tc.args, working_set)
                    else:
                        result = self._execute_agent_tool(tc.name, tc.args, working_set)
                    consecutive_errors = 0
                except Exception as e:
                    result = f"Error: {e}"
                    consecutive_errors += 1
                if declared is not None:
                    yield _sse("message", {"type": "endpoints_declared",
                                           "routes": [declared["route"]] if declared["status"] == "created" else [],
                                           "results": [declared]})

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
        tools = self._base_agent_tools()
        if getattr(self, "_project", None) is not None:
            tools.append({
                "name": "declare_endpoint",
                "description": "Declare an API endpoint this UI needs that does not exist yet; it becomes a stub route.",
                "parameters": {"type": "object", "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string", "description": "e.g. /orders/{id}"},
                    "name": {"type": "string", "description": "camelCase operation name"},
                    "description": {"type": "string"},
                    "response_schema": {"type": "object"},
                }, "required": ["method", "path"]},
            })
        return tools

    def _base_agent_tools(self) -> list[dict]:
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
        if name == "declare_endpoint":
            return self._tool_declare_endpoint(args, working_set)
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

    def _tool_declare_endpoint(self, args: dict, working_set: list[dict]) -> str:
        """Sync validation-only variant (kept for callers without a DB session); the agent loop uses
        _declare_endpoint_now, which creates the stub inside the call (review C6)."""
        if getattr(self, "_project", None) is None:
            return "Error: no project context — endpoints can only be declared for project apps."
        method, path = str(args.get("method") or "").upper(), args.get("path")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE") or not isinstance(path, str) or not path.startswith("/"):
            return "Error: method must be GET/POST/PUT/PATCH/DELETE and path must start with '/'."
        self._declared.append({**args, "method": method})
        return (f"Declared {method} {path} as a stub route (returns 501 until implemented). "
                "Call it through the generated client once it exists, or fetch('/api/p/<slug>" + path + "').")

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

    def _build_agent_system_prompt(self, api_executions: list[ApiTestExecutionModel]) -> str:
        parts = [_AGENT_SYSTEM_PROMPT]
        if getattr(self, "_spec_summary", None):
            parts.append(self._spec_summary)
        for ex in api_executions:
            parts.append(self._api_execution_context(ex))
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

    def _api_execution_context(self, ex: ApiTestExecutionModel) -> str:
        """Render a captured API test execution as system-prompt context.

        Reads the persisted `response_summary` if available; falls back to
        live computation for old rows that pre-date the column. Either way,
        no raw body bytes go into the prompt.
        """
        label = ex.name or f"{ex.method} {ex.url}"
        lines = [
            f'## Attached Endpoint: "{label}"',
            "",
            "Use the **exact** url, method, headers, and body shape below in your generated `fetch()` call.",
            "If the schema doesn't tell you what you need (enum values, auth, date format, etc.), ask the user before guessing.",
            "",
            "### Request",
            "```typescript",
            f'const res = await fetch("{ex.url}", {{',
            f'  method: "{ex.method}",',
        ]

        headers = ex.request_headers or {}
        if headers:
            header_pairs = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in headers.items())
            lines.append(f"  headers: {{ {header_pairs} }},")

        if ex.request_body_text is not None and ex.method.upper() not in ("GET", "HEAD"):
            lines.append(f"  body: {json.dumps(ex.request_body_text)},")

        lines.append("});")
        lines.append("```")

        # ── Response info ────────────────────────────────────────────
        lines.append("")
        lines.append("### Response")
        lines.append(f"- **Status:** {ex.response_status}")
        if ex.response_content_type:
            lines.append(f"- **Content-Type:** `{ex.response_content_type}`")
        lines.append(f"- **Size:** {ex.response_size} bytes")

        summary = ex.response_summary
        if summary is None and ex.response_body_b64:
            # Back-compat: old rows captured before response_summary existed.
            # Compute live (and cheaply — same logic, just not persisted).
            summary = summarize_response(
                response_body_b64=ex.response_body_b64,
                content_type=ex.response_content_type,
                response_truncated=ex.response_truncated,
                response_headers=ex.response_headers,
            )

        _format_response_summary(summary, lines)
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
