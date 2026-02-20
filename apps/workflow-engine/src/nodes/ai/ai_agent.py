"""AI Agent node - agentic loop with tool calling."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, TYPE_CHECKING

logger = logging.getLogger(__name__)

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

# Scratchpad (working memory) tool names
_SCRATCHPAD_TOOL_NAMES = frozenset({"memory_store", "memory_recall"})

# Max retries for structured output parsing/validation
MAX_OUTPUT_RETRIES = 2

# Plan / reflect extraction patterns
_PLAN_PATTERN = re.compile(r"<plan>(.*?)</plan>", re.DOTALL)
_REFLECT_PATTERN = re.compile(r"<reflect>(.*?)</reflect>", re.DOTALL)

# Maps streaming event type strings to ExecutionEventType values
EVENT_TYPE_MAP: dict[str, str] = {
    "agent_thinking": ExecutionEventType.AGENT_THINKING,
    "agent_plan": ExecutionEventType.AGENT_PLAN,
    "agent_reflect": ExecutionEventType.AGENT_REFLECT,
    "agent_tool_call": ExecutionEventType.AGENT_TOOL_CALL,
    "agent_tool_result": ExecutionEventType.AGENT_TOOL_RESULT,
    "agent_spawn": ExecutionEventType.AGENT_SPAWN,
    "agent_child_complete": ExecutionEventType.AGENT_CHILD_COMPLETE,
    "agent_output_validation": ExecutionEventType.AGENT_OUTPUT_VALIDATION,
}


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
    # Working memory scratchpad
    scratchpad: dict[str, Any] = field(default_factory=dict)
    parent_scratchpad: dict[str, Any] | None = None
    # Rich context snippets passed from parent
    context_snippets: list[dict[str, Any]] = field(default_factory=list)


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
            NodeProperty(
                display_name="Enable Planning",
                name="enablePlanning",
                type="boolean",
                default=True,
                description="Inject a reasoning protocol that makes the agent plan before acting and reflect before answering.",
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
        enable_planning = self.get_parameter(node_definition, "enablePlanning", True)

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

        # Always create AgentContext (scratchpad needs it); populate spawn
        # fields only when sub-agents are enabled.
        _excluded = _SPAWN_TOOL_NAMES | _SCRATCHPAD_TOOL_NAMES
        agent_context = AgentContext(
            agent_depth=0,
            max_agent_depth=max_agent_depth,
            parent_model=model,
            parent_temperature=temperature,
            parent_system_prompt=system_prompt,
            inheritable_tools=[t for t in tools if t["name"] not in _excluded],
            inheritable_tool_executors={
                k: v for k, v in tool_executors.items() if k not in _excluded
            },
            allow_recursive_spawn=allow_recursive_spawn,
        )
        # Always inject scratchpad tools
        tools = tools + self._build_scratchpad_tools()
        if enable_sub_agents:
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
                enable_planning=enable_planning,
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
    # Scratchpad (working memory) tools
    # ------------------------------------------------------------------

    def _build_scratchpad_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions for memory_store and memory_recall."""
        return [
            {
                "name": "memory_store",
                "description": (
                    "Store a value in your working memory scratchpad. Use this to save "
                    "intermediate findings, partial results, or anything you need to "
                    "remember across iterations."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Key to store the value under",
                        },
                        "value": {
                            "description": "Value to store (any JSON-serializable type)",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
            {
                "name": "memory_recall",
                "description": (
                    "Recall values from your working memory scratchpad. Call with a key "
                    "to get a specific value, or without a key to get all stored values. "
                    "If you are a sub-agent, this also returns read-only context from the "
                    "parent agent's scratchpad."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Key to recall. Omit to get all stored values.",
                        },
                    },
                },
            },
        ]

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
                        "context_snippets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["label", "content"],
                            },
                            "description": "Structured context sections to inject into the sub-agent's task prompt.",
                        },
                        "expected_output": {
                            "type": "object",
                            "description": "JSON schema for expected structured output. If set, the child's response will be parsed as JSON and returned in a 'data' field.",
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
                                    "context_snippets": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {"type": "string"},
                                                "content": {"type": "string"},
                                            },
                                        },
                                    },
                                    "expected_output": {"type": "object"},
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

        # Always create child agent context (scratchpad needs it).
        # Spawn tools only added if recursive spawning is allowed and depth permits.
        can_spawn = (
            agent_context.allow_recursive_spawn
            and (agent_context.agent_depth + 1) < agent_context.max_agent_depth
        )
        child_agent_context = AgentContext(
            agent_depth=agent_context.agent_depth + 1,
            max_agent_depth=agent_context.max_agent_depth,
            parent_model=child_model,
            parent_temperature=child_temperature,
            parent_system_prompt=child_system_prompt,
            inheritable_tools=child_tools,
            inheritable_tool_executors=child_executors,
            allow_recursive_spawn=agent_context.allow_recursive_spawn,
            parent_scratchpad=dict(agent_context.scratchpad),  # snapshot
        )
        # Always give child scratchpad tools
        child_tools = child_tools + self._build_scratchpad_tools()
        if can_spawn:
            child_tools = child_tools + self._build_spawn_tools()

        # Rich context: inject context_snippets into task
        context_snippets = args.get("context_snippets", [])
        if context_snippets:
            snippet_parts = ["## Context from parent agent:"]
            for snippet in context_snippets:
                label = snippet.get("label", "Context")
                content = snippet.get("content", "")
                snippet_parts.append(f"### {label}\n{content}")
            task = f"{task}\n\n" + "\n\n".join(snippet_parts)

        expected_output = args.get("expected_output")

        # Emit spawn event
        self._emit_event(context, node_name, ExecutionEventType.AGENT_SPAWN, {
            "task": task[:200],
            "model": child_model,
            "depth": agent_context.agent_depth + 1,
        })

        try:
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
        except Exception as e:
            return {"error": f"Sub-agent failed: {e}"}

        # Build enriched return: response, iterations, evidence, optional data
        child_return: dict[str, Any] = {
            "response": result.get("response", ""),
            "iterations": result.get("iterations", 0),
            "evidence": dict(child_agent_context.scratchpad) or None,
        }

        # Parse structured data if expected_output was set
        if expected_output:
            try:
                child_return["data"] = json.loads(result.get("response", ""))
                child_return["parse_error"] = None
            except (json.JSONDecodeError, TypeError) as e:
                child_return["data"] = None
                child_return["parse_error"] = str(e)

        # Emit child complete event
        self._emit_event(context, node_name, ExecutionEventType.AGENT_CHILD_COMPLETE, {
            "depth": agent_context.agent_depth + 1,
            "iterations": child_return["iterations"],
            "has_evidence": bool(child_agent_context.scratchpad),
        })

        return child_return

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

    @staticmethod
    def _build_planning_prompt() -> str:
        """Return the reasoning protocol appended to the system prompt."""
        return (
            "\n\n## Reasoning Protocol\n"
            "Follow this protocol for every task:\n\n"
            "PLAN phase: Start your first response with a <plan>...</plan> block listing "
            "the steps you intend to take.\n\n"
            "ACT phase: Before each tool call, briefly state which plan step you are executing.\n\n"
            "REFLECT phase: Before giving your final answer, include a <reflect>...</reflect> "
            "block with:\n"
            "  - Steps completed and evidence gathered\n"
            "  - Remaining gaps or uncertainties\n"
            "  - Confidence level: low / medium / high\n\n"
            "If your confidence is not high after reflection, continue working. "
            "Only give your final answer when confidence is high."
        )

    @staticmethod
    def _extract_plan_blocks(text: str) -> tuple[str | None, str | None]:
        """Extract <plan> and <reflect> blocks from LLM response text."""
        plan_match = _PLAN_PATTERN.search(text)
        reflect_match = _REFLECT_PATTERN.search(text)
        return (
            plan_match.group(1).strip() if plan_match else None,
            reflect_match.group(1).strip() if reflect_match else None,
        )

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
        enable_planning: bool = True,
    ) -> dict[str, Any]:
        """Thin wrapper: consumes the streaming generator and dispatches events."""
        result: dict[str, Any] = {}
        async for event in self._run_agent_loop_stream(
            model=model,
            system_prompt=system_prompt,
            task=task,
            tools=tools,
            tool_executors=tool_executors,
            max_iterations=max_iterations,
            temperature=temperature,
            context=context,
            node_name=node_name,
            output_schema=output_schema,
            chat_history=chat_history,
            max_context_tokens=max_context_tokens,
            agent_context=agent_context,
            enable_planning=enable_planning,
        ):
            etype = event.get("type", "")
            if etype in EVENT_TYPE_MAP:
                self._emit_event(context, node_name, EVENT_TYPE_MAP[etype], event)
            elif etype == "result":
                result = event["data"]
        return result

    async def _run_agent_loop_stream(
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
        enable_planning: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async generator that yields event dicts for the agent loop.

        Yields events like:
          {"type": "iteration_start", "iteration": N}
          {"type": "agent_thinking", "content": ..., "iteration": N}
          {"type": "agent_plan", "plan": ..., "iteration": N}
          {"type": "agent_reflect", "reflection": ..., "iteration": N}
          {"type": "agent_tool_call", "tool": ..., "arguments": ..., "id": ..., "iteration": N}
          {"type": "agent_tool_result", "tool": ..., "result": ..., "id": ..., "is_error": ..., "iteration": N}
          {"type": "agent_output_validation", "status": ..., ...}
          {"type": "agent_response", "content": ...}
          {"type": "result", "data": <final result dict>}  (always last)
        """
        from ...engine.llm_provider import call_llm

        # Append reasoning protocol to system prompt when planning is enabled
        effective_system_prompt = system_prompt
        if enable_planning:
            effective_system_prompt = (system_prompt or "") + self._build_planning_prompt()

        # Build messages: system → history → current user message
        messages: list[dict[str, Any]] = []
        if effective_system_prompt:
            messages.append({"role": "system", "content": effective_system_prompt})

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

        # Schema lookup for tool argument validation
        tool_schema_map = {t["name"]: t.get("input_schema", {}) for t in tools}

        tool_calls_list: list[dict[str, Any]] = []
        iterations = 0
        consecutive_failures = 0
        _total_input_tokens = 0
        _total_output_tokens = 0
        _total_llm_time_ms = 0.0

        while iterations < max_iterations:
            iterations += 1
            yield {"type": "iteration_start", "iteration": iterations}

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

            # Gemini sometimes returns MALFORMED_FUNCTION_CALL — retry the turn
            if response.malformed_tool_call and not response.tool_calls:
                logger.warning("Malformed tool call on iteration %d, retrying", iterations)
                messages.append({"role": "assistant", "content": response.text or ""})
                messages.append({
                    "role": "user",
                    "content": "Your previous function call was malformed. Please try again with valid function call syntax.",
                })
                continue

            if response.tool_calls:
                # Yield thinking event if the LLM included text alongside tool calls
                if response.text:
                    yield {
                        "type": "agent_thinking",
                        "content": response.text,
                        "iteration": iterations,
                    }
                    # Extract plan/reflect blocks from thinking text
                    if enable_planning:
                        plan_text, reflect_text = self._extract_plan_blocks(response.text)
                        if plan_text:
                            yield {"type": "agent_plan", "plan": plan_text, "iteration": iterations}
                        if reflect_text:
                            yield {"type": "agent_reflect", "reflection": reflect_text, "iteration": iterations}

                # Append assistant message with tool calls
                messages.append(response.get_assistant_message())

                # Execute all tools in parallel
                async def _run_one_tool(tc):
                    # Validate arguments before execution
                    schema = tool_schema_map.get(tc.name, {})
                    if schema:
                        arg_errors = self._validate_tool_args(tc.args or {}, schema)
                        if arg_errors:
                            tool_result = {"error": f"Invalid arguments: {'; '.join(arg_errors)}"}
                            return tc, tool_result, json.dumps(tool_result), True

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

                    yield {
                        "type": "agent_tool_call",
                        "tool": tc.name,
                        "arguments": tc.args,
                        "id": tc.id,
                        "iteration": iterations,
                    }

                    if is_error:
                        iteration_had_failure = True

                    yield {
                        "type": "agent_tool_result",
                        "tool": tc.name,
                        "result": tool_result,
                        "id": tc.id,
                        "iteration": iterations,
                        "is_error": is_error,
                    }

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
                    yield {"type": "result", "data": {
                        "response": f"Agent stopped: tools failed {MAX_CONSECUTIVE_TOOL_FAILURES} consecutive iterations",
                        "toolCalls": tool_calls_list,
                        "iterations": iterations,
                        "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                    }}
                    return

                continue
            else:
                # No tool calls — final text response
                final_response = response.text or ""

                # Extract plan/reflect from final response
                if enable_planning and final_response:
                    plan_text, reflect_text = self._extract_plan_blocks(final_response)
                    if plan_text:
                        yield {"type": "agent_plan", "plan": plan_text, "iteration": iterations}
                    if reflect_text:
                        yield {"type": "agent_reflect", "reflection": reflect_text, "iteration": iterations}

                yield {"type": "agent_response", "content": final_response}

                # If structured output requested, do call(s) with response_format
                # and no tools to get clean JSON output, with retry on failure
                if response_format:
                    messages.append({"role": "assistant", "content": final_response})
                    messages.append({
                        "role": "user",
                        "content": "Now format your answer as JSON matching the required schema.",
                    })

                    best_parsed = None
                    best_errors: list[str] = []

                    for retry in range(MAX_OUTPUT_RETRIES + 1):
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

                        # Step 1: Try to parse JSON
                        try:
                            parsed = json.loads(struct_text)
                        except json.JSONDecodeError as e:
                            yield {
                                "type": "agent_output_validation",
                                "status": "parse_error",
                                "error": str(e),
                                "retry": retry,
                            }
                            if retry < MAX_OUTPUT_RETRIES:
                                messages.append({"role": "assistant", "content": struct_text})
                                messages.append({
                                    "role": "user",
                                    "content": f"JSON parse error: {e}. Please fix and return valid JSON matching the schema.",
                                })
                                continue
                            break

                        # Step 2: Validate against schema
                        validation_errors = self._validate_against_schema(parsed, output_schema or {})
                        best_parsed = parsed

                        if validation_errors:
                            best_errors = validation_errors
                            yield {
                                "type": "agent_output_validation",
                                "status": "validation_error",
                                "errors": validation_errors,
                                "retry": retry,
                            }
                            if retry < MAX_OUTPUT_RETRIES:
                                messages.append({"role": "assistant", "content": struct_text})
                                messages.append({
                                    "role": "user",
                                    "content": f"Schema validation errors: {'; '.join(validation_errors)}. Please fix and return valid JSON.",
                                })
                                continue

                        # Success (or final retry with best effort)
                        if not validation_errors:
                            yield {
                                "type": "agent_output_validation",
                                "status": "success",
                                "retry": retry,
                            }
                        break

                    if best_parsed is not None:
                        result_dict: dict[str, Any] = {
                            "response": final_response,
                            "structured": best_parsed,
                            "toolCalls": tool_calls_list,
                            "iterations": iterations,
                            "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                        }
                        if best_errors:
                            result_dict["validation_errors"] = best_errors
                        yield {"type": "result", "data": result_dict}
                        return

                yield {"type": "result", "data": {
                    "response": final_response,
                    "toolCalls": tool_calls_list,
                    "iterations": iterations,
                    "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                }}
                return

        yield {"type": "result", "data": {
            "response": "Agent reached maximum iterations",
            "toolCalls": tool_calls_list,
            "iterations": iterations,
            "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
        }}

    @staticmethod
    def _validate_tool_args(
        args: dict[str, Any], input_schema: dict[str, Any]
    ) -> list[str]:
        """Validate tool arguments against input_schema. Returns list of error strings."""
        errors: list[str] = []
        if not input_schema:
            return errors

        required = input_schema.get("required", [])
        properties = input_schema.get("properties", {})

        for req in required:
            if req not in args:
                errors.append(f"Missing required field: '{req}'")

        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        for key, value in args.items():
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type and expected_type in type_map:
                    py_type = type_map[expected_type]
                    if not isinstance(value, py_type):
                        # Allow int for number type
                        if expected_type == "number" and isinstance(value, int):
                            continue
                        errors.append(
                            f"Field '{key}': expected {expected_type}, got {type(value).__name__}"
                        )

        return errors

    @staticmethod
    def _validate_against_schema(
        data: Any, schema: dict[str, Any]
    ) -> list[str]:
        """Basic validation of data against a JSON schema. Returns list of errors."""
        errors: list[str] = []
        if not schema:
            return errors

        expected_type = schema.get("type")
        type_map = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
        }

        if expected_type and expected_type in type_map:
            py_type = type_map[expected_type]
            if not isinstance(data, py_type):
                errors.append(f"Expected top-level type '{expected_type}', got {type(data).__name__}")
                return errors

        if isinstance(data, dict):
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for req in required:
                if req not in data:
                    errors.append(f"Missing required field: '{req}'")
            for key, value in data.items():
                if key in properties:
                    prop_type = properties[key].get("type")
                    if prop_type and prop_type in type_map:
                        py_type = type_map[prop_type]
                        if not isinstance(value, py_type):
                            if prop_type == "number" and isinstance(value, int):
                                continue
                            errors.append(
                                f"Field '{key}': expected {prop_type}, got {type(value).__name__}"
                            )

        return errors

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
        # Scratchpad (working memory) tools
        if name == "memory_store" and agent_context is not None:
            key = input_data.get("key", "")
            value = input_data.get("value")
            if not key:
                return {"error": "Key is required for memory_store."}
            agent_context.scratchpad[key] = value
            return {"stored": key, "keys": list(agent_context.scratchpad.keys())}

        if name == "memory_recall" and agent_context is not None:
            key = input_data.get("key")
            result: dict[str, Any] = {}
            if key:
                if key in agent_context.scratchpad:
                    result["value"] = agent_context.scratchpad[key]
                else:
                    result["value"] = None
                    result["error"] = f"Key '{key}' not found in scratchpad"
            else:
                result["scratchpad"] = dict(agent_context.scratchpad)
            if agent_context.parent_scratchpad is not None:
                result["parent_scratchpad"] = agent_context.parent_scratchpad
            return result

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
