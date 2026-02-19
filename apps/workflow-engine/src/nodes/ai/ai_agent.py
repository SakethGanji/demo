"""AI Agent node - agentic loop with tool calling."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from ..base import (
    BaseNode,
    NodeTypeDescription,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodePropertyOption,
)
from ...engine.types import (
    ExecutionEvent,
    ExecutionEventType,
    SubnodeSlotDefinition,
)

if TYPE_CHECKING:
    from ...engine.types import (
        ExecutionContext,
        NodeData,
        NodeDefinition,
        NodeExecutionResult,
        SubnodeContext,
    )

# Max consecutive tool failures before the agent aborts the loop
MAX_CONSECUTIVE_TOOL_FAILURES = 3

# Rough chars-per-token estimate for context window management
_CHARS_PER_TOKEN = 4
_DEFAULT_MAX_CONTEXT_TOKENS = 120_000

# Sub-agent spawn tool names (excluded from inheritance to prevent infinite nesting)
_SPAWN_TOOL_NAMES = frozenset({"spawn_agent", "spawn_agents_parallel"})


# ---------------------------------------------------------------------------
# Agent context for recursive sub-agent spawning
# ---------------------------------------------------------------------------


@dataclass
class AgentContext:
    """Tracks depth and carries inheritable config through recursive agent calls."""

    agent_depth: int = 0
    max_agent_depth: int = 3
    parent_model: str = "gemini-2.0-flash"
    parent_temperature: float = 0.7
    parent_system_prompt: str = ""
    inheritable_tools: list[dict[str, Any]] = field(default_factory=list)
    inheritable_tool_executors: dict[str, Any] = field(default_factory=dict)
    allow_recursive_spawn: bool = True


class AIAgentNode(BaseNode):
    """AI Agent node - agentic loop with tool calling capabilities."""

    node_description = NodeTypeDescription(
        name="AIAgent",
        display_name="AI Agent",
        description="Autonomous agent with tool calling capabilities",
        icon="fa:brain",
        group=["ai"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[
            NodeOutputDefinition(
                name="main",
                display_name="Result",
                schema={
                    "type": "object",
                    "properties": {
                        "response": {"type": "string", "description": "Final agent response"},
                        "toolCalls": {"type": "array", "description": "Tools called during execution"},
                        "iterations": {"type": "number", "description": "Number of agent iterations"},
                    },
                },
            )
        ],
        properties=[
            NodeProperty(
                display_name="Model",
                name="model",
                type="options",
                default="gemini-2.0-flash",
                options=[
                    NodePropertyOption(name="Gemini 2.0 Flash", value="gemini-2.0-flash"),
                    NodePropertyOption(name="Gemini 1.5 Flash", value="gemini-1.5-flash"),
                    NodePropertyOption(name="Gemini 1.5 Pro", value="gemini-1.5-pro"),
                    NodePropertyOption(name="GPT-4o", value="gpt-4o"),
                    NodePropertyOption(name="GPT-4o Mini", value="gpt-4o-mini"),
                    NodePropertyOption(name="Claude Sonnet", value="claude-sonnet-4-20250514"),
                ],
            ),
            NodeProperty(
                display_name="System Prompt",
                name="systemPrompt",
                type="string",
                default="You are a helpful AI assistant with access to tools.",
                type_options={"rows": 3},
            ),
            NodeProperty(
                display_name="Task",
                name="task",
                type="string",
                default="",
                required=True,
                description="Task for the agent. Supports expressions.",
                type_options={"rows": 5},
            ),
            NodeProperty(
                display_name="Tools",
                name="tools",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                properties=[
                    NodeProperty(
                        display_name="Tool Name",
                        name="name",
                        type="string",
                        default="",
                    ),
                    NodeProperty(
                        display_name="Description",
                        name="description",
                        type="string",
                        default="",
                    ),
                    NodeProperty(
                        display_name="Parameters (JSON Schema)",
                        name="parameters",
                        type="json",
                        default="{}",
                    ),
                ],
            ),
            NodeProperty(
                display_name="Max Iterations",
                name="maxIterations",
                type="number",
                default=10,
                description="Maximum number of agent iterations",
            ),
            NodeProperty(
                display_name="Temperature",
                name="temperature",
                type="number",
                default=0.7,
            ),
            NodeProperty(
                display_name="Max Context Tokens",
                name="maxContextTokens",
                type="number",
                default=120000,
                description="Approximate token budget for conversation history. Older messages are trimmed when exceeded.",
            ),
            NodeProperty(
                display_name="Output Schema (JSON)",
                name="outputSchema",
                type="json",
                default="",
                description="Optional JSON schema to force structured output. When set, the agent's final response will be valid JSON matching this schema.",
                type_options={"rows": 5},
            ),
            NodeProperty(
                display_name="Enable Sub-Agents",
                name="enableSubAgents",
                type="boolean",
                default=False,
                description="Allow the agent to spawn sub-agents dynamically at runtime.",
            ),
            NodeProperty(
                display_name="Max Agent Depth",
                name="maxAgentDepth",
                type="number",
                default=3,
                description="Maximum depth of nested sub-agent spawning.",
                display_options={"show": {"enableSubAgents": [True]}},
            ),
            NodeProperty(
                display_name="Allow Recursive Spawning",
                name="allowRecursiveSpawn",
                type="boolean",
                default=True,
                description="Whether sub-agents can themselves spawn sub-agents.",
                display_options={"show": {"enableSubAgents": [True]}},
            ),
        ],
        subnode_slots=[
            SubnodeSlotDefinition(
                name="chatModel",
                display_name="Model",
                slot_type="model",
                required=False,
                multiple=False,
            ),
            SubnodeSlotDefinition(
                name="memory",
                display_name="Memory",
                slot_type="memory",
                required=False,
                multiple=False,
            ),
            SubnodeSlotDefinition(
                name="tools",
                display_name="Tools",
                slot_type="tool",
                required=False,
                multiple=True,
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "AIAgent"

    @property
    def description(self) -> str:
        return "Autonomous agent with tool calling capabilities"

    async def execute(
        self,
        context: ExecutionContext,
        node_definition: NodeDefinition,
        input_data: list[NodeData],
        subnode_context: SubnodeContext | None = None,
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData

        # Get parameters with defaults
        model = self.get_parameter(node_definition, "model", "gemini-2.0-flash")
        system_prompt = self.get_parameter(node_definition, "systemPrompt", "")
        task = self.get_parameter(node_definition, "task", "")
        tools_config = self.get_parameter(node_definition, "tools", [])
        max_iterations = self.get_parameter(node_definition, "maxIterations", 10)
        temperature = self.get_parameter(node_definition, "temperature", 0.7)
        max_context_tokens = self.get_parameter(node_definition, "maxContextTokens", _DEFAULT_MAX_CONTEXT_TOKENS)
        output_schema_raw = self.get_parameter(node_definition, "outputSchema", "")
        enable_sub_agents = self.get_parameter(node_definition, "enableSubAgents", False)
        max_agent_depth = self.get_parameter(node_definition, "maxAgentDepth", 3)
        allow_recursive_spawn = self.get_parameter(node_definition, "allowRecursiveSpawn", True)

        # Parse output schema if provided
        output_schema: dict[str, Any] | None = None
        if output_schema_raw:
            if isinstance(output_schema_raw, str):
                try:
                    output_schema = json.loads(output_schema_raw)
                except json.JSONDecodeError:
                    pass
            elif isinstance(output_schema_raw, dict):
                output_schema = output_schema_raw

        # Override with model subnode config if connected
        model_config = self._get_model_config(subnode_context)
        if model_config:
            model = model_config.get("model", model)
            temperature = model_config.get("temperature", temperature)

        # Get memory functions if connected
        memory_config = self._get_memory_config(subnode_context)

        if not task:
            raise ValueError("Task is required")

        # Build tools from connected subnodes first, then from config
        tools, tool_executors = self._build_tools_from_subnodes(subnode_context)

        # Merge runtime tools from input data (dynamic tool binding)
        for item in (input_data or []):
            if isinstance(item.json.get("_tools"), list):
                for rt in item.json["_tools"]:
                    if not rt.get("name"):
                        continue
                    tools.append({
                        "name": rt["name"],
                        "description": rt.get("description", ""),
                        "input_schema": rt.get("input_schema", {}),
                    })
                    if "execute" in rt:
                        tool_executors[rt["name"]] = rt["execute"]

        # Add tools from parameter config if no subnodes / runtime tools connected
        if not tools:
            tools = self._build_tools(tools_config)

        # Build agent context for sub-agent spawning
        agent_context: AgentContext | None = None
        if enable_sub_agents:
            agent_context = AgentContext(
                agent_depth=0,
                max_agent_depth=max_agent_depth,
                parent_model=model,
                parent_temperature=temperature,
                parent_system_prompt=system_prompt,
                inheritable_tools=[t for t in tools if t["name"] not in _SPAWN_TOOL_NAMES],
                inheritable_tool_executors={
                    k: v for k, v in tool_executors.items() if k not in _SPAWN_TOOL_NAMES
                },
                allow_recursive_spawn=allow_recursive_spawn,
            )
            tools = tools + self._build_spawn_tools()

        results: list[NodeData] = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_llm_time_ms = 0.0
        total_iterations = 0
        total_tool_calls = 0

        # Get chat history as structured messages if memory is connected
        chat_history: list[dict[str, str]] = []
        if memory_config and "getHistory" in memory_config:
            chat_history = await asyncio.to_thread(memory_config["getHistory"])

        for item in input_data if input_data else [NodeData(json={})]:
            # Build task with input data context
            full_task = task
            context_str = json.dumps(item.json, indent=2) if item.json else ""
            if context_str:
                full_task = f"{full_task}\n\nInput data:\n{context_str}"

            result = await self._run_agent_loop(
                model=model,
                system_prompt=system_prompt,
                task=full_task,
                tools=tools,
                tool_executors=tool_executors,
                max_iterations=max_iterations,
                temperature=temperature,
                context=context,
                node_name=node_definition.name,
                output_schema=output_schema,
                chat_history=chat_history,
                max_context_tokens=max_context_tokens,
                agent_context=agent_context,
            )

            # Accumulate agent metrics from result
            total_iterations += result.get("iterations", 0)
            total_tool_calls += len(result.get("toolCalls", []))
            if "_usage" in result:
                usage = result.pop("_usage")
                total_input_tokens += usage.get("inputTokens", 0)
                total_output_tokens += usage.get("outputTokens", 0)
                total_llm_time_ms += usage.get("llmResponseTimeMs", 0)

            # Save to memory if connected
            if memory_config and "addMessage" in memory_config:
                await asyncio.to_thread(memory_config["addMessage"], "user", task)
                if result.get("response"):
                    await asyncio.to_thread(memory_config["addMessage"], "assistant", result["response"])

            results.append(NodeData(json=result))

        metadata: dict[str, Any] = {
            "model": model,
            "agentIterations": total_iterations,
            "toolCallCount": total_tool_calls,
        }
        if total_input_tokens or total_output_tokens:
            metadata.update({
                "inputTokens": total_input_tokens,
                "outputTokens": total_output_tokens,
                "totalTokens": total_input_tokens + total_output_tokens,
                "llmResponseTimeMs": round(total_llm_time_ms, 2),
            })

        return self.output(results, metadata=metadata)

    def _get_model_config(self, subnode_context: SubnodeContext | None) -> dict[str, Any] | None:
        """Get model configuration from connected model subnode."""
        if not subnode_context or not subnode_context.models:
            return None
        return subnode_context.models[0].config

    def _get_memory_config(self, subnode_context: SubnodeContext | None) -> dict[str, Any] | None:
        """Get memory configuration from connected memory subnode."""
        if not subnode_context or not subnode_context.memory:
            return None
        return subnode_context.memory[0].config

    def _build_tools_from_subnodes(
        self, subnode_context: SubnodeContext | None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Build tools from connected tool subnodes."""
        tools = []
        tool_executors: dict[str, Any] = {}

        if not subnode_context or not subnode_context.tools:
            return tools, tool_executors

        for resolved_tool in subnode_context.tools:
            config = resolved_tool.config
            if not config.get("name"):
                continue

            tools.append({
                "name": config["name"],
                "description": config.get("description", ""),
                "input_schema": config.get("input_schema", {}),
            })

            if "execute" in config:
                tool_executors[config["name"]] = config["execute"]

        return tools, tool_executors

    def _build_tools(self, tools_config: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build tools array from inline parameter config."""
        tools = []

        for tool in tools_config:
            if not tool.get("name"):
                continue

            params = tool.get("parameters", {})
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except json.JSONDecodeError:
                    params = {}

            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": params,
            })

        return tools

    # ------------------------------------------------------------------
    # Sub-agent spawn tools
    # ------------------------------------------------------------------

    def _build_spawn_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions for spawn_agent and spawn_agents_parallel."""
        return [
            {
                "name": "spawn_agent",
                "description": (
                    "Spawn a sub-agent to perform a specific task. The sub-agent runs "
                    "independently with its own system prompt and model, executes the task, "
                    "and returns a result. Use this to delegate focused subtasks."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task for the sub-agent to perform",
                        },
                        "system_prompt": {
                            "type": "string",
                            "description": "System prompt for the sub-agent. Defaults to parent's if omitted.",
                        },
                        "model": {
                            "type": "string",
                            "description": "Model to use (e.g. 'gemini-2.0-flash', 'gpt-4o'). Defaults to parent's if omitted.",
                        },
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Names of tools from the parent to give this sub-agent. Omit to inherit all.",
                        },
                        "max_iterations": {
                            "type": "integer",
                            "description": "Max tool-calling iterations for the sub-agent. Default 5.",
                        },
                        "temperature": {
                            "type": "number",
                            "description": "Temperature for the sub-agent. Defaults to parent's.",
                        },
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "spawn_agents_parallel",
                "description": (
                    "Spawn multiple sub-agents in parallel. Each agent spec defines a "
                    "separate agent with its own task, model, and tools. All run concurrently "
                    "and results are returned as an array. Use this when subtasks are independent."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agents": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Label for this agent (for identifying results)",
                                    },
                                    "task": {"type": "string"},
                                    "system_prompt": {"type": "string"},
                                    "model": {"type": "string"},
                                    "tools": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "max_iterations": {"type": "integer"},
                                    "temperature": {"type": "number"},
                                },
                                "required": ["task"],
                            },
                            "description": "Array of agent specifications to run in parallel",
                        },
                    },
                    "required": ["agents"],
                },
            },
        ]

    def _resolve_child_tools(
        self,
        requested_tool_names: list[str] | None,
        agent_context: AgentContext,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Resolve which tools a child agent inherits from the parent."""
        parent_tools = agent_context.inheritable_tools
        parent_executors = agent_context.inheritable_tool_executors

        if requested_tool_names is None:
            return list(parent_tools), dict(parent_executors)

        requested = set(requested_tool_names)
        child_tools = [t for t in parent_tools if t["name"] in requested]
        child_executors = {k: v for k, v in parent_executors.items() if k in requested}
        return child_tools, child_executors

    async def _handle_spawn_agent(
        self,
        args: dict[str, Any],
        agent_context: AgentContext,
        context: ExecutionContext,
        node_name: str,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> dict[str, Any]:
        """Handle a spawn_agent tool call by running a child agent loop."""
        if agent_context.agent_depth >= agent_context.max_agent_depth:
            return {
                "error": (
                    f"Maximum sub-agent depth ({agent_context.max_agent_depth}) reached. "
                    "Cannot spawn further sub-agents."
                ),
            }

        task = args.get("task", "")
        if not task:
            return {"error": "Task is required for spawn_agent."}

        child_model = args.get("model") or agent_context.parent_model
        child_system_prompt = args.get("system_prompt") or agent_context.parent_system_prompt
        child_max_iterations = args.get("max_iterations", 5)
        child_temperature = args.get("temperature", agent_context.parent_temperature)

        child_tools, child_executors = self._resolve_child_tools(
            args.get("tools"), agent_context
        )

        # Build child agent context (if recursive spawning is allowed and depth permits)
        child_agent_context: AgentContext | None = None
        can_spawn = (
            agent_context.allow_recursive_spawn
            and (agent_context.agent_depth + 1) < agent_context.max_agent_depth
        )
        if can_spawn:
            child_agent_context = AgentContext(
                agent_depth=agent_context.agent_depth + 1,
                max_agent_depth=agent_context.max_agent_depth,
                parent_model=child_model,
                parent_temperature=child_temperature,
                parent_system_prompt=child_system_prompt,
                inheritable_tools=child_tools,
                inheritable_tool_executors=child_executors,
                allow_recursive_spawn=agent_context.allow_recursive_spawn,
            )
            child_tools = child_tools + self._build_spawn_tools()

        result = await self._run_agent_loop(
            model=child_model,
            system_prompt=child_system_prompt,
            task=task,
            tools=child_tools,
            tool_executors=child_executors,
            max_iterations=child_max_iterations,
            temperature=child_temperature,
            context=context,
            node_name=f"{node_name}/sub-agent[{agent_context.agent_depth + 1}]",
            max_context_tokens=max_context_tokens,
            agent_context=child_agent_context,
        )

        # Only return the summary to the parent — keep raw toolCalls out of
        # the parent's context window so sub-agents provide true isolation.
        return {
            "response": result.get("response", ""),
            "iterations": result.get("iterations", 0),
        }

    async def _handle_spawn_agents_parallel(
        self,
        args: dict[str, Any],
        agent_context: AgentContext,
        context: ExecutionContext,
        node_name: str,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> dict[str, Any]:
        """Handle spawn_agents_parallel by running child agents concurrently."""
        agent_specs = args.get("agents", [])
        if not agent_specs:
            return {"error": "At least one agent spec is required."}

        async def _run_one(spec: dict[str, Any]) -> dict[str, Any]:
            result = await self._handle_spawn_agent(
                spec, agent_context, context, node_name, max_context_tokens,
            )
            label = spec.get("name") or spec.get("task", "")[:40]
            return {"name": label, **result}

        results = await asyncio.gather(
            *[_run_one(spec) for spec in agent_specs],
            return_exceptions=True,
        )

        output: list[dict[str, Any]] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                label = agent_specs[i].get("name", f"agent_{i}")
                output.append({"name": label, "error": str(r)})
            else:
                output.append(r)

        return {"agents": output}

    def _emit_event(
        self,
        context: ExecutionContext,
        node_name: str,
        event_type: ExecutionEventType,
        data: dict[str, Any],
    ) -> None:
        """Emit an agent event if an event callback is available."""
        if not context.on_event:
            return
        from ...engine.types import NodeData

        context.on_event(ExecutionEvent(
            type=event_type,
            execution_id=context.execution_id,
            timestamp=datetime.now(),
            node_name=node_name,
            data=[NodeData(json=data)],
        ))

    @staticmethod
    def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
        """Rough token estimate based on character count."""
        total_chars = sum(
            len(json.dumps(m, default=str)) for m in messages
        )
        return total_chars // _CHARS_PER_TOKEN

    @staticmethod
    def _trim_messages(
        messages: list[dict[str, Any]], max_tokens: int
    ) -> list[dict[str, Any]]:
        """Trim conversation history to fit within the token budget.

        Preserves:
          - The system prompt (index 0 if role == system)
          - The original user task (first user message)
          - The most recent messages

        Drops the oldest middle messages until under budget.
        """
        if AIAgentNode._estimate_tokens(messages) <= max_tokens:
            return messages

        # Identify protected prefix: system prompt + first user message
        protected_end = 0
        for i, m in enumerate(messages):
            protected_end = i + 1
            if m["role"] == "user":
                break

        prefix = messages[:protected_end]
        suffix = messages[protected_end:]

        # Drop oldest messages from suffix until within budget
        while suffix and AIAgentNode._estimate_tokens(prefix + suffix) > max_tokens:
            # Drop in chunks: remove the oldest assistant+tool group together
            # to avoid orphaned tool results without their assistant message
            dropped = suffix.pop(0)
            # If we dropped an assistant message with tool_calls, also drop
            # the following tool result messages that reference it
            if dropped.get("tool_calls"):
                tc_ids = {tc["id"] for tc in dropped.get("tool_calls", [])}
                while suffix and suffix[0].get("tool_call_id") in tc_ids:
                    suffix.pop(0)

        return prefix + suffix

    async def _run_agent_loop(
        self,
        model: str,
        system_prompt: str,
        task: str,
        tools: list[dict[str, Any]],
        tool_executors: dict[str, Any],
        max_iterations: int,
        temperature: float,
        context: ExecutionContext,
        node_name: str,
        output_schema: dict[str, Any] | None = None,
        chat_history: list[dict[str, str]] | None = None,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
        agent_context: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Run an agentic tool-calling loop via call_llm."""
        from ...engine.llm_provider import call_llm

        # Build messages: system → history → current user message
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Inject chat history as proper user/assistant message turns
        if chat_history:
            for msg in chat_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        # Current turn
        messages.append({"role": "user", "content": task})

        # Structured output format — applied only on the final (no-tools) call
        response_format: dict[str, Any] | None = None
        if output_schema:
            response_format = {"type": "json_object", "schema": output_schema}

        tool_calls_list: list[dict[str, Any]] = []
        iterations = 0
        consecutive_failures = 0
        _total_input_tokens = 0
        _total_output_tokens = 0
        _total_llm_time_ms = 0.0

        while iterations < max_iterations:
            iterations += 1

            # Trim context if it's grown too large
            messages = self._trim_messages(messages, max_context_tokens)

            # Don't mix response_format with tools — Gemini doesn't support it
            response = await call_llm(
                model=model,
                messages=messages,
                temperature=temperature,
                tools=tools if tools else None,
                max_tokens=4096,
            )

            # Accumulate token usage
            if response.usage:
                _total_input_tokens += response.usage.input_tokens
                _total_output_tokens += response.usage.output_tokens
            if response.response_time_ms:
                _total_llm_time_ms += response.response_time_ms

            if response.tool_calls:
                # Emit thinking event if the LLM included text alongside tool calls
                if response.text:
                    self._emit_event(context, node_name, ExecutionEventType.AGENT_THINKING, {
                        "content": response.text,
                        "iteration": iterations,
                    })

                # Append assistant message with tool calls
                messages.append(response.get_assistant_message())

                # Execute all tools in parallel
                async def _run_one_tool(tc):
                    tool_result = await self._execute_tool(
                        tc.name, tc.args, tool_executors, context,
                        agent_context=agent_context,
                        node_name=node_name,
                        max_context_tokens=max_context_tokens,
                    )
                    result_str = (
                        json.dumps(tool_result)
                        if not isinstance(tool_result, str)
                        else tool_result
                    )
                    is_error = isinstance(tool_result, dict) and "error" in tool_result
                    return tc, tool_result, result_str, is_error

                results = await asyncio.gather(
                    *[_run_one_tool(tc) for tc in response.tool_calls]
                )

                # Process results in order (messages must stay ordered)
                iteration_had_failure = False
                for tc, tool_result, result_str, is_error in results:
                    tool_calls_list.append({
                        "tool": tc.name,
                        "input": tc.args,
                        "output": tool_result,
                        "id": tc.id,
                        "is_error": is_error,
                    })

                    self._emit_event(context, node_name, ExecutionEventType.AGENT_TOOL_CALL, {
                        "tool": tc.name,
                        "arguments": tc.args,
                        "id": tc.id,
                        "iteration": iterations,
                    })

                    if is_error:
                        iteration_had_failure = True

                    self._emit_event(context, node_name, ExecutionEventType.AGENT_TOOL_RESULT, {
                        "tool": tc.name,
                        "result": tool_result,
                        "id": tc.id,
                        "iteration": iterations,
                        "is_error": is_error,
                    })

                    messages.append({
                        "role": "tool",
                        "content": result_str,
                        "tool_call_id": tc.id,
                        "name": tc.name,
                    })

                # Circuit breaker: abort if tools keep failing
                if iteration_had_failure:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                    return {
                        "response": f"Agent stopped: tools failed {MAX_CONSECUTIVE_TOOL_FAILURES} consecutive iterations",
                        "toolCalls": tool_calls_list,
                        "iterations": iterations,
                        "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                    }

                continue
            else:
                # No tool calls — final text response
                final_response = response.text or ""

                # If structured output requested, do one more call with response_format
                # and no tools to get clean JSON output
                if response_format:
                    # Ask the LLM to structure its answer
                    messages.append({"role": "assistant", "content": final_response})
                    messages.append({
                        "role": "user",
                        "content": "Now format your answer as JSON matching the required schema.",
                    })
                    struct_response = await call_llm(
                        model=model,
                        messages=messages,
                        temperature=0.0,
                        tools=None,
                        max_tokens=4096,
                        response_format=response_format,
                    )
                    if struct_response.usage:
                        _total_input_tokens += struct_response.usage.input_tokens
                        _total_output_tokens += struct_response.usage.output_tokens
                    if struct_response.response_time_ms:
                        _total_llm_time_ms += struct_response.response_time_ms

                    struct_text = struct_response.text or ""
                    try:
                        parsed = json.loads(struct_text)
                        return {
                            "response": final_response,
                            "structured": parsed,
                            "toolCalls": tool_calls_list,
                            "iterations": iterations,
                            "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                        }
                    except json.JSONDecodeError:
                        pass

                return {
                    "response": final_response,
                    "toolCalls": tool_calls_list,
                    "iterations": iterations,
                    "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                }

        return {
            "response": "Agent reached maximum iterations",
            "toolCalls": tool_calls_list,
            "iterations": iterations,
            "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
        }

    async def _execute_tool(
        self,
        name: str,
        input_data: dict[str, Any],
        tool_executors: dict[str, Any],
        context: ExecutionContext,
        agent_context: AgentContext | None = None,
        node_name: str = "",
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> Any:
        """Execute a tool using spawn handler, custom executor, or error fallback.

        Supports both sync and async executors. Sync executors are run via
        asyncio.to_thread to avoid blocking the event loop.
        """
        # Sub-agent spawn tools
        if name == "spawn_agent" and agent_context is not None:
            return await self._handle_spawn_agent(
                input_data, agent_context, context, node_name, max_context_tokens,
            )
        if name == "spawn_agents_parallel" and agent_context is not None:
            return await self._handle_spawn_agents_parallel(
                input_data, agent_context, context, node_name, max_context_tokens,
            )

        # Custom executor (from connected subnodes)
        if name in tool_executors:
            executor = tool_executors[name]
            try:
                if inspect.iscoroutinefunction(executor):
                    return await executor(input_data, context)
                else:
                    # Run sync executor in a thread to avoid blocking
                    return await asyncio.to_thread(executor, input_data)
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"Unknown tool: '{name}'"}
