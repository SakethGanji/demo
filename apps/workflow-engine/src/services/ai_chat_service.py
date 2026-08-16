"""AI Chat service — LLM-powered workflow assistant with agentic workflow generation."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ..engine.llm_provider import call_llm

if TYPE_CHECKING:
    from .workflow_service import WorkflowService
from ..engine.node_registry import NodeRegistryClass
from ..schemas.ai_chat import AIChatRequest
from ..schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
    NodeDefinitionSchema,
    ConnectionSchema,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CHAT_MODEL = "gemini-2.0-flash"

_WORKFLOW_ID_RE = re.compile(r"^wf_\d+_[0-9a-f]+$")

_GENERATE_PATTERNS = [
    "create a workflow", "build a workflow", "generate a workflow",
    "make a workflow", "design a workflow", "build me a",
    "create a pipeline", "automate",
]

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AIChatService:
    """LLM-powered workflow assistant with agentic generation capabilities.

    Handles two modes:
      - **Chat** (default): single-turn LLM call for questions and explanations.
      - **Generate**: multi-turn agentic loop that discovers nodes, builds a
        workflow, validates, saves, test-executes, and fixes it autonomously.
    """

    # Chat settings
    _MAX_HISTORY_MESSAGES = 20
    _MAX_MESSAGE_CHARS = 4000

    # Generator settings
    GENERATOR_MODEL = "gemini-2.5-pro"
    MAX_ITERATIONS = 20
    MAX_CONSECUTIVE_ERRORS = 3
    MAX_CONTEXT_TOKENS = 120_000

    def __init__(
        self,
        node_registry: NodeRegistryClass,
        workflow_service: WorkflowService | None = None,
    ) -> None:
        self._registry = node_registry
        self._workflow_service = workflow_service
        self._node_catalog: str | None = None
        self._last_saved_workflow: dict[str, Any] | None = None
        self._generator_tools = self._build_generator_tools()

    # ==================================================================
    # Public entry point
    # ==================================================================

    async def stream_chat(
        self, request: AIChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream a chat response, yielding SSE event dicts.

        Decides between the agentic generator and basic chat based on
        ``mode_hint`` or keyword detection.
        """
        if request.mode_hint == "generate":
            use_generator = True
        elif request.mode_hint == "auto":
            use_generator = await self._classify_intent(request.message)
        else:
            use_generator = False

        if use_generator and self._workflow_service is not None:
            async for event in self._stream_generate(request):
                yield event
            return

        # Fall through to basic single-turn chat
        async for event in self._stream_basic_chat(request):
            yield event

    # ==================================================================
    # Intent classification
    # ==================================================================

    async def _classify_intent(self, message: str) -> bool:
        """Use a fast LLM call to classify whether the user wants to generate a workflow."""
        try:
            response = await call_llm(
                model=_CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a binary classifier. The user is interacting with a workflow automation platform. "
                            "Determine if the user's message is requesting the CREATION or MODIFICATION of a workflow/pipeline/automation, "
                            "or if they are just asking a question, requesting an explanation, or having a general conversation.\n\n"
                            "Respond with ONLY a single word:\n"
                            "- GENERATE — if the user wants to create, build, modify, or fix a workflow\n"
                            "- CHAT — for everything else (questions, explanations, greetings, general discussion)"
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                temperature=0,
                max_tokens=8,
            )
            return (response.text or "").strip().upper().startswith("GENERATE")
        except Exception:
            logger.warning("Intent classification failed, falling back to keyword matching")
            lower = message.lower()
            return any(p in lower for p in _GENERATE_PATTERNS)

    # ==================================================================
    # Basic chat (single-turn LLM)
    # ==================================================================

    async def _stream_basic_chat(
        self, request: AIChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Simple single-turn LLM chat for questions / explanations."""
        try:
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self._build_chat_system_prompt(request.workflow_context)},
            ]

            # Build proper multi-turn history
            for msg in request.conversation_history[-self._MAX_HISTORY_MESSAGES:]:
                if msg.role in ("user", "assistant") and msg.content:
                    messages.append({"role": msg.role, "content": msg.content[:self._MAX_MESSAGE_CHARS]})

            messages.append({"role": "user", "content": request.message})

            response = await call_llm(
                model=_CHAT_MODEL,
                messages=messages,
                temperature=0.4,
                max_tokens=4096,
            )

            text = response.text or ""
            if text:
                yield _sse("text", {"type": "text", "content": text})

        except Exception as exc:
            logger.exception("AI chat stream error")
            yield _sse("error", {"type": "error", "message": "An error occurred while processing your request."})

        yield _sse("done", {"type": "done"})

    def _build_node_catalog(self) -> str:
        if self._node_catalog is not None:
            return self._node_catalog

        infos = self._registry.get_node_info_full()

        groups: dict[str, list[str]] = {}
        for info in infos:
            group = (info.group or ["other"])[0]
            entry_parts = [f"- **{info.type}**: {info.description}"]

            if info.properties:
                param_strs = []
                for p in info.properties[:6]:
                    param_strs.append(f"{p['name']}({p['type']})")
                if param_strs:
                    entry_parts.append(f"  Params: {', '.join(param_strs)}")

            groups.setdefault(group, []).append("\n".join(entry_parts))

        lines: list[str] = []
        for group_name, entries in groups.items():
            lines.append(f"\n### {group_name.title()}")
            lines.extend(entries)

        self._node_catalog = "\n".join(lines)
        return self._node_catalog

    def _build_chat_system_prompt(self, workflow_context: Any | None) -> str:
        catalog = self._build_node_catalog()

        context_section = ""
        if workflow_context:
            summary = {
                "name": workflow_context.name,
                "nodes": [{"name": n.get("name"), "type": n.get("type")} for n in (workflow_context.nodes or [])],
                "connections": [f"{c.get('source_node')}->{c.get('target_node')}" for c in (workflow_context.connections or [])],
            }
            context_section = f"\n## Current Workflow State\n```json\n{json.dumps(summary, indent=2)}\n```\n"

        return (
            "You are an expert workflow automation assistant.\n\n"
            f"## Available Node Types\n{catalog}\n"
            f"{context_section}\n"
            "Help the user understand, build, and modify workflows. "
            "Explain what nodes do, suggest workflow designs, and answer questions "
            "about workflow automation."
        )

    # ==================================================================
    # Agentic workflow generation
    # ==================================================================

    async def _stream_generate(
        self, request: AIChatRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the agent loop and yield SSE event dicts in real-time."""
        system_prompt = self._build_generator_system_prompt(request.workflow_context)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

        # Inject conversation history
        for msg in request.conversation_history:
            role = msg.role
            content = msg.content
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": request.message})

        yield _sse("phase", {"type": "phase", "phase": "starting", "message": "Starting workflow generation..."})

        # Track the last successfully saved workflow so we can push it to the UI
        self._last_saved_workflow: dict[str, Any] | None = None

        try:
            async for event in self._run_agent_loop(messages, test_input=request.test_input):
                yield _sse(event.get("type", "info"), event)
        except Exception as exc:
            logger.exception("Workflow generator error")
            yield _sse("error", {"type": "error", "message": "An error occurred during workflow generation."})

        # Emit operations event so the frontend can load the workflow onto the canvas
        if self._last_saved_workflow is not None:
            wf = self._last_saved_workflow
            nodes = wf.get("nodes", [])
            connections = wf.get("connections", [])
            payload = {
                "mode": "full_workflow",
                "workflow": {
                    "name": wf.get("name", "Generated Workflow"),
                    "nodes": [
                        {"name": n.get("name"), "type": n.get("type"), "parameters": n.get("parameters", {})}
                        for n in nodes
                    ],
                    "connections": [
                        {
                            "source_node": c.get("source_node"),
                            "target_node": c.get("target_node"),
                            "source_output": c.get("source_output", "main"),
                            "target_input": c.get("target_input", "main"),
                        }
                        for c in connections
                    ],
                },
                "operations": None,
                "summary": f"Generated workflow with {len(nodes)} node(s) and {len(connections)} connection(s)",
            }
            yield _sse("operations", {"type": "operations", "payload": payload})

        yield _sse("done", {"type": "done"})

    # ------------------------------------------------------------------
    # Agent loop (mirrors AIAgentNode._run_agent_loop)
    # ------------------------------------------------------------------

    async def _run_agent_loop(
        self,
        messages: list[dict[str, Any]],
        *,
        test_input: dict[str, Any] | None = None,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_iterations: int | None = None,
        temperature: float | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Core while-loop: call_llm -> tool calls -> append results -> repeat.

        Yields event dicts for each step (thinking, tool_call, tool_result, text).
        """
        _model = model or self.GENERATOR_MODEL
        _tools = tools if tools is not None else self._generator_tools
        _max_iter = max_iterations if max_iterations is not None else self.MAX_ITERATIONS
        _temp = temperature if temperature is not None else 0.3

        iteration = 0
        consecutive_errors = 0

        while iteration < _max_iter:
            iteration += 1
            yield {"type": "thinking", "iteration": iteration}

            messages = self._trim_messages(messages, self.MAX_CONTEXT_TOKENS)

            response = await call_llm(
                model=_model,
                messages=messages,
                temperature=_temp,
                tools=_tools,
                max_tokens=8192,
            )

            if not response.tool_calls:
                text = response.text or ""
                if text:
                    yield {"type": "text", "content": text}
                return

            messages.append(response.get_assistant_message())

            if response.text:
                yield {"type": "thinking_text", "content": response.text, "iteration": iteration}

            for tc in response.tool_calls:
                yield {"type": "tool_call", "tool": tc.name, "args": tc.args}
                try:
                    result = await self._execute_tool(tc.name, tc.args, test_input=test_input)
                    consecutive_errors = 0
                except Exception as e:
                    logger.warning("Tool %s failed: %s", tc.name, e)
                    result = {"error": str(e)}
                    consecutive_errors += 1

                summary = self._summarize_result(tc.name, result)
                yield {"type": "tool_result", "tool": tc.name, "result": summary}

                result_str = json.dumps(result) if not isinstance(result, str) else result
                messages.append({
                    "role": "tool",
                    "content": result_str,
                    "tool_call_id": tc.id,
                    "name": tc.name,
                })

            if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                yield {"type": "text", "content": "Generation stopped: too many consecutive tool errors."}
                return

        yield {"type": "text", "content": "Generation stopped: reached maximum iterations."}

    # ------------------------------------------------------------------
    # Generator system prompt
    # ------------------------------------------------------------------

    def _build_generator_system_prompt(self, workflow_context: Any | None) -> str:
        context_section = ""
        if workflow_context:
            context_section = (
                "\n## Existing Workflow Context\n"
                f"Name: {workflow_context.name}\n"
                f"Nodes: {json.dumps(workflow_context.nodes, indent=2)}\n"
                f"Connections: {json.dumps(workflow_context.connections, indent=2)}\n"
            )

        return f"""You are an autonomous workflow generation agent for the `cmdstd` engine. Your sole purpose is to translate a user's natural language request into a fully functional workflow by strictly following a series of tool-based steps.

### **Core Mandate: The Workflow Creation Lifecycle**
You must execute the following steps in sequence. Do not deviate.

1.  **Discover**: Analyze the user's prompt to determine the required nodes. Use `get_node_catalog()` to see available nodes and `get_node_schemas()` to fetch the exact parameters for the nodes you choose. **Under no circumstances should you guess parameters or their structure.**
2.  **Design**: Construct the complete workflow as a JSON object. Adhere strictly to the format below and the schemas you retrieved. Ensure all node names are unique and lay them out logically (x-position +300 for each sequential node). Start node is always the first node.
3.  **Validate**: Before saving, you MUST call `validate_workflow()` on your generated JSON to check for structural errors.
4.  **Save**: Once validated, call `save_workflow()` to persist the workflow.
5.  **Test**: After saving, you MUST call `execute_workflow()` to confirm it runs without errors.
6.  **Fix**: If execution fails, analyze the error, call `update_workflow()` with your corrected JSON, and then loop back to the **Validate** step. You have a maximum of **3** fix attempts. If you cannot fix the workflow, report the final error and stop.

{context_section}
### **Workflow JSON Structure**
- **Node**: `{{"name": "...", "type": "...", "parameters": {{...}}, "position": {{"x": 0, "y": 0}}}}`
- **Connection**: `{{"source_node": "...", "target_node": "...", "source_output": "main", "target_input": "main"}}`

### **CRITICAL: Expression Syntax**
The workflow engine uses a **Python-based expression language**, NOT JavaScript. All expressions must be wrapped in `{{{{ }}}}`. Using JavaScript syntax will cause a hard failure.

**Supported:**
- Field access: `{{{{ $json.fieldName }}}}`, `{{{{ $json["field name"] }}}}`
- Cross-node reference: `{{{{ $node["Node Name"].json.field }}}}`
- Environment variables: `{{{{ $env.MY_VAR }}}}`
- Arithmetic & comparisons: `{{{{ $json.price * 1.1 }}}}`, `{{{{ $json.count > 0 }}}}`
- Built-in functions: `join(arr, sep)`, `length(x)`, `upper(s)`, `lower(s)`, `slice(x, start, end)`, `first(arr)`, `last(arr)`, `keys(obj)`, `values(obj)`, `now()`, `date_now()`, `json_stringify(x)`, `int(x)`, `str(x)`, `round(x, n)`

**FORBIDDEN (JavaScript-style — these will cause a hard failure):**
- `.map()`, `.filter()`, `.reduce()`, `.join()` (JS array methods)
- Arrow functions (`=>`)
- Template literals (backtick strings)
- List comprehensions
- `JSON.stringify()`, `Math.*`, `Date.*`

**Examples:**
- String concatenation: `{{{{ "Hello " + $json.name }}}}`
- Array helper: `{{{{ join($json.tags, ", ") }}}}`
- Conditional-style: `{{{{ $json.score > 50 and "pass" or "fail" }}}}`
- Nested access: `{{{{ $node["HTTP Request"].json.data.items }}}}`

### **Rules**
- Always validate before saving. Always test after saving.
- Use get_node_schema or get_node_schemas to understand parameters — do not guess.
- Do not re-fetch schemas you already retrieved earlier in the conversation.
- Keep workflows simple and focused on the user's request.

After successfully completing the entire lifecycle, provide a concise summary that includes:
- The `workflow_id`
- What the workflow does (1-2 sentences)
- The nodes used and how they connect
- Test execution result (pass/fail and key outputs)"""

    # ------------------------------------------------------------------
    # Generator tool definitions
    # ------------------------------------------------------------------

    def _build_generator_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_node_catalog",
                "description": "List all available node types with brief descriptions, grouped by category. Optionally filter by category.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Optional category filter (e.g. 'triggers', 'flow', 'data', 'integrations', 'ai', 'output')",
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "get_node_schema",
                "description": "Get the full property schema for a specific node type, including all parameters, types, defaults, and options.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_type": {
                            "type": "string",
                            "description": "The node type identifier (e.g. 'HttpRequest', 'AIAgent', 'CodeTool')",
                        }
                    },
                    "required": ["node_type"],
                },
            },
            {
                "name": "validate_workflow",
                "description": "Validate a workflow definition for structural correctness: node types exist, unique names, connections valid. Returns {valid: bool, errors: [...]}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow": {
                            "type": "object",
                            "description": "The workflow definition with 'name', 'nodes', 'connections', and optional 'description'",
                        }
                    },
                    "required": ["workflow"],
                },
            },
            {
                "name": "save_workflow",
                "description": "Save a validated workflow to the database. Returns {workflow_id, name}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow": {
                            "type": "object",
                            "description": "The workflow definition with 'name', 'nodes', 'connections', and optional 'description'",
                        }
                    },
                    "required": ["workflow"],
                },
            },
            {
                "name": "execute_workflow",
                "description": "Execute a saved workflow by ID with optional test input data. Returns {status, node_outputs, errors}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "The workflow ID returned by save_workflow",
                        },
                        "input_data": {
                            "type": "object",
                            "description": "Optional input data to pass to the workflow's Start node",
                        },
                    },
                    "required": ["workflow_id"],
                },
            },
            {
                "name": "update_workflow",
                "description": "Update an existing saved workflow (e.g. to fix errors after testing). Provide the workflow_id and the updated definition.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "The workflow ID to update",
                        },
                        "workflow": {
                            "type": "object",
                            "description": "The updated workflow definition with 'name', 'nodes', 'connections'",
                        },
                    },
                    "required": ["workflow_id", "workflow"],
                },
            },
            {
                "name": "delete_workflow",
                "description": "Delete a workflow by ID (for cleanup on failure).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "The workflow ID to delete",
                        }
                    },
                    "required": ["workflow_id"],
                },
            },
            {
                "name": "get_workflow",
                "description": "Retrieve a saved workflow by ID. Returns the workflow_id, name, description, and full definition (nodes + connections).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {
                            "type": "string",
                            "description": "The workflow ID to retrieve",
                        }
                    },
                    "required": ["workflow_id"],
                },
            },
            {
                "name": "get_node_schemas",
                "description": "Get full schemas for multiple node types in one call. More efficient than calling get_node_schema repeatedly.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of node type identifiers (e.g. ['HttpRequest', 'Set', 'AIAgent'])",
                        }
                    },
                    "required": ["node_types"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _execute_tool(
        self, tool_name: str, args: dict[str, Any], *, test_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        executors = {
            "get_node_catalog": self._tool_get_node_catalog,
            "get_node_schema": self._tool_get_node_schema,
            "validate_workflow": self._tool_validate_workflow,
            "save_workflow": self._tool_save_workflow,
            "execute_workflow": self._tool_execute_workflow,
            "update_workflow": self._tool_update_workflow,
            "delete_workflow": self._tool_delete_workflow,
            "get_workflow": self._tool_get_workflow,
            "get_node_schemas": self._tool_get_node_schemas,
        }
        executor = executors.get(tool_name)
        if executor is None:
            return {"error": f"Unknown tool: {tool_name}"}
        if tool_name == "execute_workflow":
            return await executor(args, test_input=test_input)
        return await executor(args)

    # ------------------------------------------------------------------
    # Tool executors
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_workflow_id(workflow_id: str) -> str | None:
        """Return an error message if the workflow_id looks malformed, else None."""
        if not workflow_id or not _WORKFLOW_ID_RE.match(workflow_id):
            return f"Invalid workflow_id format: '{workflow_id}'. Expected format: wf_<timestamp>_<hex>"
        return None

    @staticmethod
    def _parse_definition(
        workflow_def: dict[str, Any],
    ) -> tuple[list[NodeDefinitionSchema], list[ConnectionSchema]]:
        """Convert raw node/connection dicts to schema objects."""
        nodes = [
            NodeDefinitionSchema(
                name=n["name"],
                type=n["type"],
                parameters=n.get("parameters", {}),
                position=n.get("position"),
            )
            for n in workflow_def.get("nodes", [])
        ]
        connections = [
            ConnectionSchema(
                source_node=c["source_node"],
                target_node=c["target_node"],
                source_output=c.get("source_output", "main"),
                target_input=c.get("target_input", "main"),
            )
            for c in workflow_def.get("connections", [])
        ]
        return nodes, connections

    async def _tool_get_node_catalog(self, args: dict[str, Any]) -> dict[str, Any]:
        category_filter = args.get("category")
        infos = self._registry.get_node_info_full()

        groups: dict[str, list[dict[str, Any]]] = {}
        for info in infos:
            group = (info.group or ["other"])[0]
            if category_filter and group != category_filter.lower():
                continue

            entry: dict[str, Any] = {
                "type": info.type,
                "description": info.description,
                "display_name": info.display_name,
            }

            groups.setdefault(group, []).append(entry)

        return {"categories": groups}

    async def _tool_get_node_schema(self, args: dict[str, Any]) -> dict[str, Any]:
        node_type = args.get("node_type", "")
        info = self._registry.get_node_type_info(node_type)
        if info is None:
            return {"error": f"Unknown node type: {node_type}"}

        result: dict[str, Any] = {
            "type": info.type,
            "display_name": info.display_name,
            "description": info.description,
            "group": info.group,
            "properties": info.properties,
            "inputs": info.inputs,
            "outputs": info.outputs,
            "input_count": info.input_count,
            "output_count": info.output_count,
        }
        return result

    async def _tool_validate_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_def = args.get("workflow", {})
        errors: list[str] = []

        nodes = workflow_def.get("nodes", [])
        connections = workflow_def.get("connections", [])

        if not nodes:
            errors.append("Workflow must have at least one node.")
            return {"valid": False, "errors": errors}

        names = [n.get("name", "") for n in nodes]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            errors.append(f"Duplicate node names: {set(dupes)}")

        name_set = set(names)

        # Build node_type_map and name->type mapping for deep validation
        node_type_map: dict[str, Any] = {}  # node_type_str -> NodeTypeInfo
        node_name_to_type: dict[str, str] = {}
        for node in nodes:
            ntype = node.get("type", "")
            node_name_to_type[node.get("name", "")] = ntype
            if not self._registry.has(ntype):
                errors.append(f"Unknown node type: '{ntype}'")
            else:
                if ntype not in node_type_map:
                    info = self._registry.get_node_type_info(ntype)
                    if info:
                        node_type_map[ntype] = info

        for conn in connections:
            src = conn.get("source_node", "")
            tgt = conn.get("target_node", "")

            if src not in name_set:
                errors.append(f"Connection references unknown source node: '{src}'")
                continue
            if tgt not in name_set:
                errors.append(f"Connection references unknown target node: '{tgt}'")
                continue

            src_type = node_name_to_type.get(src, "")
            tgt_type = node_name_to_type.get(tgt, "")
            src_info = node_type_map.get(src_type)
            tgt_info = node_type_map.get(tgt_type)

            # Output port existence check
            source_output = conn.get("source_output", "main")
            if src_info and src_info.outputs is not None:
                output_names = {o["name"] for o in src_info.outputs}
                if source_output not in output_names:
                    errors.append(
                        f"Connection from '{src}': output port '{source_output}' does not exist "
                        f"on node type '{src_type}'. Available outputs: {sorted(output_names)}"
                    )

            # Input port existence check
            target_input = conn.get("target_input", "main")
            if tgt_info and tgt_info.inputs is not None:
                input_names = {i["name"] for i in tgt_info.inputs}
                if target_input not in input_names:
                    errors.append(
                        f"Connection to '{tgt}': input port '{target_input}' does not exist "
                        f"on node type '{tgt_type}'. Available inputs: {sorted(input_names)}"
                    )

        types = [n.get("type", "") for n in nodes]
        if "Start" not in types:
            errors.append("Workflow should have a Start node.")

        return {"valid": len(errors) == 0, "errors": errors}

    async def _tool_save_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_def = args.get("workflow", {})

        # Auto-validate before saving to catch LLM mistakes
        validation = await self._tool_validate_workflow(args)
        if not validation.get("valid"):
            return {"error": f"Validation failed: {validation['errors']}"}

        name = workflow_def.get("name", "Generated Workflow")
        description = workflow_def.get("description", "")
        nodes, connections = self._parse_definition(workflow_def)

        request = WorkflowCreateRequest(
            name=name,
            nodes=nodes,
            connections=connections,
            description=description,
        )

        result = await self._workflow_service.create_workflow(request)
        self._last_saved_workflow = workflow_def
        return {"workflow_id": result.id, "name": result.name}

    async def _tool_execute_workflow(
        self, args: dict[str, Any], *, test_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        workflow_id = args.get("workflow_id", "")
        if err := self._validate_workflow_id(workflow_id):
            return {"error": err}
        input_data = args.get("input_data") or test_input

        try:
            result = await self._workflow_service.run_workflow(workflow_id, input_data)

            node_outputs: dict[str, Any] = {}
            if result.data:
                for node_name, items in result.data.items():
                    output_str = json.dumps(items)
                    if len(output_str) > 4000:
                        node_outputs[node_name] = f"[output truncated, {len(output_str)} chars]"
                    else:
                        node_outputs[node_name] = items

            error_list = []
            if result.errors:
                error_list = [
                    {"node": e.node_name, "error": e.error}
                    for e in result.errors
                ]

            return {
                "status": result.status,
                "node_outputs": node_outputs,
                "errors": error_list,
            }
        except Exception as e:
            return {"status": "failed", "errors": [{"error": str(e)}]}

    async def _tool_update_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = args.get("workflow_id", "")
        if err := self._validate_workflow_id(workflow_id):
            return {"error": err}
        workflow_def = args.get("workflow", {})

        # Auto-validate before updating to catch LLM mistakes
        validation = await self._tool_validate_workflow({"workflow": workflow_def})
        if not validation.get("valid"):
            return {"error": f"Validation failed: {validation['errors']}"}

        nodes, connections = self._parse_definition(workflow_def)

        request = WorkflowUpdateRequest(
            name=workflow_def.get("name"),
            nodes=nodes or None,
            connections=connections or None,
            description=workflow_def.get("description"),
        )

        result = await self._workflow_service.update_workflow(workflow_id, request)
        self._last_saved_workflow = workflow_def
        return {"workflow_id": result.id, "name": result.name, "updated": True}

    async def _tool_delete_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = args.get("workflow_id", "")
        if err := self._validate_workflow_id(workflow_id):
            return {"error": err}
        await self._workflow_service.delete_workflow(workflow_id)
        return {"deleted": True, "workflow_id": workflow_id}

    async def _tool_get_workflow(self, args: dict[str, Any]) -> dict[str, Any]:
        workflow_id = args.get("workflow_id", "")
        if err := self._validate_workflow_id(workflow_id):
            return {"error": err}
        result = await self._workflow_service.get_workflow(workflow_id)
        return {
            "workflow_id": result.id,
            "name": result.name,
            "description": result.definition.get("description", ""),
            "definition": result.definition,
        }

    async def _tool_get_node_schemas(self, args: dict[str, Any]) -> dict[str, Any]:
        node_types = args.get("node_types", [])
        schemas = {}
        not_found = []
        for nt in node_types:
            result = await self._tool_get_node_schema({"node_type": nt})
            if "error" in result:
                not_found.append(nt)
            else:
                schemas[nt] = result
        return {"schemas": schemas, "not_found": not_found}

    # ------------------------------------------------------------------
    # Context trimming (same strategy as AIAgentNode, with pinned tools)
    # ------------------------------------------------------------------

    _PINNED_TOOLS = {"save_workflow", "execute_workflow", "get_workflow", "get_node_schemas"}

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        return sum(len(str(m)) for m in messages) // 4

    def _trim_messages(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> list[dict[str, Any]]:
        if self._estimate_tokens(messages) <= max_tokens:
            return messages

        protected_end = 0
        for i, m in enumerate(messages):
            protected_end = i + 1
            if m["role"] == "user":
                break

        prefix = messages[:protected_end]
        suffix = messages[protected_end:]

        while suffix and self._estimate_tokens(prefix + suffix) > max_tokens:
            candidate = suffix[0]

            # Check if this is a pinned tool result
            if candidate.get("role") == "tool" and candidate.get("name") in self._PINNED_TOOLS:
                # Move pinned tool result to protected prefix
                prefix.append(suffix.pop(0))
                continue

            # Check if this is an assistant message with pinned tool calls
            if candidate.get("tool_calls"):
                tc_names = {
                    tc.get("function", {}).get("name", "")
                    for tc in candidate.get("tool_calls", [])
                }
                if tc_names & self._PINNED_TOOLS:
                    # Keep the assistant message and its tool results in prefix
                    tc_ids = {tc["id"] for tc in candidate.get("tool_calls", [])}
                    prefix.append(suffix.pop(0))
                    while suffix and suffix[0].get("tool_call_id") in tc_ids:
                        prefix.append(suffix.pop(0))
                    continue

            # Drop the message (not pinned)
            dropped = suffix.pop(0)
            if dropped.get("tool_calls"):
                tc_ids = {tc["id"] for tc in dropped.get("tool_calls", [])}
                while suffix and suffix[0].get("tool_call_id") in tc_ids:
                    suffix.pop(0)

        return prefix + suffix

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_result(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        if "error" in result:
            return {"success": False, "error": result["error"]}

        if tool_name == "validate_workflow":
            return {"success": result.get("valid", False), "errors": result.get("errors", [])}
        elif tool_name == "save_workflow":
            return {"success": True, "workflow_id": result.get("workflow_id"), "name": result.get("name")}
        elif tool_name == "execute_workflow":
            summary: dict[str, Any] = {
                "success": result.get("status") == "success",
                "status": result.get("status"),
                "errors": result.get("errors", []),
            }
            # Include truncated node outputs so the agent can debug
            raw_outputs = result.get("node_outputs")
            if raw_outputs and isinstance(raw_outputs, dict):
                truncated: dict[str, Any] = {}
                for node_name, output in raw_outputs.items():
                    output_repr = json.dumps(output) if not isinstance(output, str) else output
                    if len(output_repr) > 500:
                        truncated[node_name] = output_repr[:500] + "…[truncated]"
                    else:
                        truncated[node_name] = output
                summary["node_outputs"] = truncated
            return summary
        elif tool_name == "delete_workflow":
            return {"success": True, "deleted": True}
        elif tool_name == "get_workflow":
            return {
                "success": True,
                "workflow_id": result.get("workflow_id"),
                "name": result.get("name"),
            }
        elif tool_name == "get_node_schemas":
            return {"success": True, "schema_count": len(result.get("schemas", {}))}
        else:
            return {"success": True}


def _sse(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build an SSE event dict matching the existing contract."""
    return {
        "event": event_type,
        "data": json.dumps(data),
    }
