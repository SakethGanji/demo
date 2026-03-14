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
)

if TYPE_CHECKING:
    from ...engine.types import (
        ExecutionContext,
        NodeData,
        NodeDefinition,
        NodeExecutionResult,
    )

# Max consecutive tool failures before the agent aborts the loop
MAX_CONSECUTIVE_TOOL_FAILURES = 5

# Rough chars-per-token fallback for non-OpenAI models
_CHARS_PER_TOKEN_FALLBACK = 3.5
_DEFAULT_MAX_CONTEXT_TOKENS = 120_000

# Cached tiktoken encoders
_tiktoken_encoders: dict[str, Any] = {}


def _get_tiktoken_encoder(model: str) -> Any | None:
    """Get a tiktoken encoder for OpenAI models, None for others."""
    try:
        import tiktoken
    except ImportError:
        return None
    if model in _tiktoken_encoders:
        return _tiktoken_encoders[model]
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # Default to cl100k_base for unknown OpenAI models
        enc = tiktoken.get_encoding("cl100k_base")
    _tiktoken_encoders[model] = enc
    return enc

# Sub-agent spawn tool names (excluded from inheritance to prevent infinite nesting)
_SPAWN_TOOL_NAMES = frozenset({"spawn_agent"})

# Scratchpad (working memory) tool names
_SCRATCHPAD_TOOL_NAMES = frozenset({"memory_store", "memory_recall"})

# Skill delegation tool names (excluded from inheritance)
_SKILL_TOOL_NAMES = frozenset({"delegate_to_skill"})

# Programmatic tool calling tool names
_PTC_TOOL_NAMES = frozenset({"run_script"})

# Default PTC script timeout (seconds)
_PTC_SCRIPT_TIMEOUT = 60

# Max retries for structured output parsing/validation
MAX_OUTPUT_RETRIES = 2

# Max retries for malformed tool calls
MAX_MALFORMED_RETRIES = 3

# Default timeout for individual tool execution (seconds)
_DEFAULT_TOOL_TIMEOUT = 120

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
    "agent_response": ExecutionEventType.AGENT_RESPONSE,
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
    enable_scratchpad: bool = False
    # Working memory scratchpad (per-agent, isolated)
    scratchpad: dict[str, Any] = field(default_factory=dict)
    parent_scratchpad: dict[str, Any] | None = None
    # Shared memory store — single dict reference shared across the entire agent
    # tree. Any agent can read/write. Data never enters LLM context until an
    # agent explicitly calls memory_recall('shared:<key>').
    shared_store: dict[str, Any] = field(default_factory=dict)
    # Rich context snippets passed from parent
    context_snippets: list[dict[str, Any]] = field(default_factory=list)
    # Read-only context store for parent→child data passing (lazy retrieval)
    context_store: dict[str, Any] = field(default_factory=dict)
    # Skill delegation: skill configs and parent-level tools for scoped sub-agents
    skill_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    skill_all_tools: list[dict[str, Any]] = field(default_factory=list)
    skill_all_executors: dict[str, Any] = field(default_factory=dict)


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
                default="You are a helpful AI assistant with access to tools. When multiple tool calls are independent, call them all in the same turn to minimize round-trips.",
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
                default=30,
                description="Maximum number of LLM round-trips (each tool-call batch counts as one).",
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
                display_name="Max Output Tokens",
                name="maxOutputTokens",
                type="number",
                default=4096,
                description="Maximum tokens in each LLM response. Increase for models that support longer output (e.g. 8192 for Claude/GPT-4o).",
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
                default=False,
                description="Inject a reasoning protocol that makes the agent plan before acting and reflect before answering.",
            ),
            NodeProperty(
                display_name="Enable Scratchpad",
                name="enableScratchpad",
                type="boolean",
                default=False,
                description="Give the agent working memory tools (memory_store / memory_recall) for saving intermediate findings across iterations.",
            ),
            NodeProperty(
                display_name="Enable Scripting",
                name="enablePtc",
                type="boolean",
                default=False,
                description="Give the agent a run_script tool for programmatic tool calling — batch multiple tool calls in a Python script with loops, conditionals, and error handling.",
            ),
            NodeProperty(
                display_name="Skill Profiles",
                name="skillProfiles",
                type="collection",
                default=[],
                type_options={"multipleValues": True},
                description="Pre-defined sub-agent profiles. The agent picks a skill by name and the system wires up the right prompt and scoped tools automatically.",
                properties=[
                    NodeProperty(
                        display_name="Skill Name",
                        name="name",
                        type="string",
                        default="",
                        description="Unique identifier the LLM uses to invoke this skill",
                    ),
                    NodeProperty(
                        display_name="Description",
                        name="description",
                        type="string",
                        default="",
                        description="What this skill does — shown to the LLM for intent routing",
                    ),
                    NodeProperty(
                        display_name="System Prompt",
                        name="systemPrompt",
                        type="string",
                        default="",
                        description="Instructions for the sub-agent handling this skill",
                        type_options={"rows": 3},
                    ),
                    NodeProperty(
                        display_name="Tool Names",
                        name="toolNames",
                        type="string",
                        default="",
                        description="Comma-separated tool names this skill can access from the parent's tools",
                    ),
                    NodeProperty(
                        display_name="Output Schema (JSON)",
                        name="outputSchema",
                        type="json",
                        default="",
                        description="Optional JSON schema for structured sub-agent output",
                    ),
                ],
            ),
            # --- Memory Configuration ---
            NodeProperty(
                display_name="Memory Type",
                name="memoryType",
                type="options",
                default="none",
                description="Chat memory strategy for multi-turn conversations.",
                options=[
                    NodePropertyOption(name="None", value="none"),
                    NodePropertyOption(name="Simple (In-Memory)", value="simple"),
                    NodePropertyOption(name="Buffer Memory", value="buffer"),
                    NodePropertyOption(name="Token Buffer", value="tokenBuffer"),
                    NodePropertyOption(name="Conversation Window", value="conversationWindow"),
                    NodePropertyOption(name="Summary Memory", value="summary"),
                    NodePropertyOption(name="Summary Buffer", value="summaryBuffer"),
                    NodePropertyOption(name="Progressive Summary", value="progressiveSummary"),
                    NodePropertyOption(name="SQLite (Persistent)", value="sqlite"),
                    NodePropertyOption(name="Vector Memory", value="vector"),
                    NodePropertyOption(name="Entity Memory", value="entity"),
                    NodePropertyOption(name="Knowledge Graph", value="knowledgeGraph"),
                ],
            ),
            NodeProperty(
                display_name="Session ID",
                name="memorySessionId",
                type="string",
                default="default",
                description="Unique session identifier for chat history. Supports expressions.",
                display_options={"hide": {"memoryType": ["none"]}},
            ),
            NodeProperty(
                display_name="Max Messages",
                name="memoryMaxMessages",
                type="number",
                default=20,
                description="Maximum messages to keep in history.",
                display_options={"show": {"memoryType": ["simple", "buffer", "sqlite", "conversationWindow"]}},
            ),
            NodeProperty(
                display_name="Max Tokens",
                name="memoryMaxTokens",
                type="number",
                default=4000,
                description="Maximum tokens for history window.",
                display_options={"show": {"memoryType": ["tokenBuffer"]}},
            ),
            NodeProperty(
                display_name="Max Turns",
                name="memoryMaxTurns",
                type="number",
                default=10,
                description="Maximum conversation turns (1 turn = user + assistant pair).",
                display_options={"show": {"memoryType": ["conversationWindow"]}},
            ),
            NodeProperty(
                display_name="Summary Model",
                name="memorySummaryModel",
                type="options",
                default="gemini-2.0-flash",
                description="Model used for summarization.",
                options=[
                    NodePropertyOption(name="Gemini 2.0 Flash", value="gemini-2.0-flash"),
                    NodePropertyOption(name="Gemini 1.5 Flash", value="gemini-1.5-flash"),
                    NodePropertyOption(name="GPT-4o Mini", value="gpt-4o-mini"),
                ],
                display_options={"show": {"memoryType": ["summary", "summaryBuffer", "progressiveSummary", "entity", "knowledgeGraph"]}},
            ),
            NodeProperty(
                display_name="Recent Messages",
                name="memoryRecentMessages",
                type="number",
                default=5,
                description="Number of recent messages to keep alongside summary.",
                display_options={"show": {"memoryType": ["summary", "summaryBuffer", "entity"]}},
            ),
            NodeProperty(
                display_name="Summary Threshold",
                name="memorySummaryThreshold",
                type="number",
                default=15,
                description="Number of messages before triggering summarization.",
                display_options={"show": {"memoryType": ["summary"]}},
            ),
            NodeProperty(
                display_name="Update Frequency",
                name="memoryUpdateFrequency",
                type="number",
                default=2,
                description="Update summary every N messages.",
                display_options={"show": {"memoryType": ["progressiveSummary"]}},
            ),
            NodeProperty(
                display_name="Top K Results",
                name="memoryTopK",
                type="number",
                default=5,
                description="Number of semantically similar messages to retrieve.",
                display_options={"show": {"memoryType": ["vector"]}},
            ),
            NodeProperty(
                display_name="Embedding Provider",
                name="memoryEmbeddingProvider",
                type="options",
                default="openai",
                options=[
                    NodePropertyOption(name="OpenAI", value="openai"),
                    NodePropertyOption(name="Gemini", value="gemini"),
                ],
                display_options={"show": {"memoryType": ["vector"]}},
            ),
            NodeProperty(
                display_name="Neo4j Connection String",
                name="memoryNeo4jUri",
                type="string",
                default="bolt://localhost:7687",
                display_options={"show": {"memoryType": ["knowledgeGraph"]}},
            ),
            NodeProperty(
                display_name="Neo4j Username",
                name="memoryNeo4jUsername",
                type="string",
                default="neo4j",
                display_options={"show": {"memoryType": ["knowledgeGraph"]}},
            ),
            NodeProperty(
                display_name="Neo4j Password",
                name="memoryNeo4jPassword",
                type="string",
                default="",
                display_options={"show": {"memoryType": ["knowledgeGraph"]}},
            ),
            # --- Built-in Tools ---
            NodeProperty(
                display_name="Built-in Tools",
                name="builtinTools",
                type="multiOptions",
                default=[],
                description="Select built-in tools to give the agent.",
                options=[
                    NodePropertyOption(name="Calculator", value="calculator"),
                    NodePropertyOption(name="Current Time", value="currentTime"),
                    NodePropertyOption(name="Random Number", value="randomNumber"),
                    NodePropertyOption(name="Text Utils", value="text"),
                    NodePropertyOption(name="HTTP Request", value="httpRequest"),
                    NodePropertyOption(name="Run Code", value="code"),
                    NodePropertyOption(name="Run Workflow", value="workflow"),
                    NodePropertyOption(name="Neo4j Query", value="neo4jQuery"),
                    NodePropertyOption(name="Data Profile", value="dataProfile"),
                    NodePropertyOption(name="Data Aggregate", value="dataAggregate"),
                    NodePropertyOption(name="Data Sample", value="dataSample"),
                    NodePropertyOption(name="Data Report", value="dataReport"),
                ],
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
    ) -> NodeExecutionResult:
        from ...engine.types import NodeData
        from ...engine.expression_engine import ExpressionEngine, expression_engine
        from .inline_config import resolve_memory, resolve_tools

        # Get parameters with defaults
        model = self.get_parameter(node_definition, "model", "gemini-2.0-flash")
        system_prompt_template = self.get_parameter(node_definition, "systemPrompt", "")
        task_template = self.get_parameter(node_definition, "task", "")
        tools_config = self.get_parameter(node_definition, "tools", [])
        max_iterations = self.get_parameter(node_definition, "maxIterations", 30)
        temperature = self.get_parameter(node_definition, "temperature", 0.7)
        max_context_tokens = self.get_parameter(node_definition, "maxContextTokens", _DEFAULT_MAX_CONTEXT_TOKENS)
        max_output_tokens = self.get_parameter(node_definition, "maxOutputTokens", 4096)
        output_schema_raw = self.get_parameter(node_definition, "outputSchema", "")
        enable_sub_agents = self.get_parameter(node_definition, "enableSubAgents", False)
        max_agent_depth = self.get_parameter(node_definition, "maxAgentDepth", 3)
        allow_recursive_spawn = self.get_parameter(node_definition, "allowRecursiveSpawn", True)
        enable_planning = self.get_parameter(node_definition, "enablePlanning", False)
        enable_scratchpad = self.get_parameter(node_definition, "enableScratchpad", False)
        enable_ptc = self.get_parameter(node_definition, "enablePtc", False)
        skill_profiles = self.get_parameter(node_definition, "skillProfiles", [])

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

        # Resolve inline memory configuration
        memory_type = self.get_parameter(node_definition, "memoryType", "none")
        memory_config = None
        if memory_type and memory_type != "none":
            memory_params = {
                "sessionId": self.get_parameter(node_definition, "memorySessionId", "default"),
                "maxMessages": self.get_parameter(node_definition, "memoryMaxMessages", 20),
                "maxTokens": self.get_parameter(node_definition, "memoryMaxTokens", 4000),
                "maxTurns": self.get_parameter(node_definition, "memoryMaxTurns", 10),
                "summaryModel": self.get_parameter(node_definition, "memorySummaryModel", "gemini-2.0-flash"),
                "recentMessages": self.get_parameter(node_definition, "memoryRecentMessages", 5),
                "summaryThreshold": self.get_parameter(node_definition, "memorySummaryThreshold", 15),
                "updateFrequency": self.get_parameter(node_definition, "memoryUpdateFrequency", 2),
                "topK": self.get_parameter(node_definition, "memoryTopK", 5),
                "embeddingProvider": self.get_parameter(node_definition, "memoryEmbeddingProvider", "openai"),
                "connectionString": self.get_parameter(node_definition, "memoryNeo4jUri", "bolt://localhost:7687"),
                "username": self.get_parameter(node_definition, "memoryNeo4jUsername", "neo4j"),
                "password": self.get_parameter(node_definition, "memoryNeo4jPassword", ""),
            }
            memory_config = resolve_memory(memory_type, memory_params)

        if not task_template:
            raise ValueError("Task is required")

        # Build tools from inline built-in tool selections
        builtin_tool_names = self.get_parameter(node_definition, "builtinTools", [])
        tools, tool_executors = resolve_tools(builtin_tool_names)

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

        # Add tools from the manual tools parameter config
        if tools_config:
            tools = tools + self._build_tools(tools_config)

        # Create AgentContext; populate spawn/scratchpad/skill fields based on config.
        _excluded = _SPAWN_TOOL_NAMES | _SCRATCHPAD_TOOL_NAMES | _SKILL_TOOL_NAMES | _PTC_TOOL_NAMES
        agent_context = AgentContext(
            agent_depth=0,
            max_agent_depth=max_agent_depth,
            parent_model=model,
            parent_temperature=temperature,
            parent_system_prompt=system_prompt_template,
            inheritable_tools=[t for t in tools if t["name"] not in _excluded],
            inheritable_tool_executors={
                k: v for k, v in tool_executors.items() if k not in _excluded
            },
            allow_recursive_spawn=allow_recursive_spawn,
            enable_scratchpad=enable_scratchpad,
        )
        # Inject scratchpad tools only when enabled
        if enable_scratchpad:
            tools = tools + self._build_scratchpad_tools()
        if enable_sub_agents:
            tools = tools + self._build_spawn_tools()
        if enable_ptc and tools:
            # Give the agent a run_script tool listing available tool names
            tool_names = [t["name"] for t in tools]
            tools = tools + self._build_ptc_tools(tool_names)

        # Build skill delegation from inline parameters
        skill_configs = self._build_skills_from_parameters(skill_profiles)
        if skill_configs:
            available_tool_names = {t["name"] for t in tools}
            tools = tools + [self._build_delegate_to_skill_tool(skill_configs, available_tool_names)]
            agent_context.skill_configs = skill_configs
            agent_context.skill_all_tools = [t for t in tools if t["name"] != "delegate_to_skill"]
            agent_context.skill_all_executors = dict(tool_executors)
            # Inject full skill descriptions into system prompt template (sent once)
            system_prompt_template = (system_prompt_template or "") + self._build_skill_descriptions_for_system_prompt(skill_configs)

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

        effective_input = input_data if input_data else [NodeData(json={})]
        for idx, item in enumerate(effective_input):
            # Resolve $json expressions against current item's data
            expr_context = ExpressionEngine.create_context(
                effective_input,
                context.node_states,
                context.execution_id,
                idx,
            )
            task = expression_engine.resolve(task_template, expr_context)
            system_prompt = expression_engine.resolve(system_prompt_template, expr_context)

            # Build task with input data context (exclude internal keys)
            # Only append raw data if the task template didn't already reference $json
            full_task = task
            if "$json" not in str(task_template):
                user_data = {k: v for k, v in item.json.items() if not k.startswith("_")}
                if user_data:
                    try:
                        context_str = json.dumps(user_data, indent=2, default=str)
                    except (TypeError, ValueError):
                        context_str = ""
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
                max_output_tokens=max_output_tokens,
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

    # ------------------------------------------------------------------
    # Skill delegation
    # ------------------------------------------------------------------

    def _build_skills_from_parameters(
        self, profiles: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Build skill name -> config lookup from inline skillProfiles parameter."""
        skills: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            name = (profile.get("name") or "").strip()
            if not name:
                continue

            tool_names_raw = profile.get("toolNames", "")
            tool_names = [t.strip() for t in tool_names_raw.split(",") if t.strip()]

            output_schema = None
            output_schema_raw = profile.get("outputSchema", "")
            if output_schema_raw:
                if isinstance(output_schema_raw, str):
                    try:
                        output_schema = json.loads(output_schema_raw)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Skill profile '%s': invalid outputSchema JSON, ignoring", name)
                elif isinstance(output_schema_raw, dict):
                    output_schema = output_schema_raw

            skills[name] = {
                "skill_name": name,
                "description": profile.get("description", ""),
                "system_prompt": profile.get("systemPrompt", ""),
                "tool_names": tool_names,
                "output_schema": output_schema,
            }
        return skills

    def _build_delegate_to_skill_tool(
        self,
        skill_configs: dict[str, dict[str, Any]],
        available_tool_names: set[str],
    ) -> dict[str, Any]:
        """Build the delegate_to_skill tool definition from resolved skills.

        Keeps only skill names in the tool description to reduce per-iteration
        token cost. Full descriptions should go in the system prompt.
        """
        for name, config in skill_configs.items():
            tool_names = config.get("tool_names", [])
            missing = [t for t in tool_names if t not in available_tool_names]
            if missing:
                logger.warning("Skill '%s' references tools not on parent: %s", name, missing)

        skill_names = list(skill_configs.keys())

        return {
            "name": "delegate_to_skill",
            "description": (
                "Delegate a task to a specialized skill. Each skill has its own tools "
                "and instructions. Call multiple times in the same turn for parallel execution.\n\n"
                f"Available skills: {', '.join(skill_names)}"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": skill_names,
                        "description": "Name of the skill to delegate to",
                    },
                    "task": {
                        "type": "string",
                        "description": "The specific task for this skill's sub-agent",
                    },
                    "context": {
                        "type": "object",
                        "description": "Optional context data to pass to the skill (e.g. customer_id, session data)",
                    },
                },
                "required": ["skill", "task"],
            },
        }

    @staticmethod
    def _build_skill_descriptions_for_system_prompt(
        skill_configs: dict[str, dict[str, Any]],
    ) -> str:
        """Build full skill descriptions for inclusion in the system prompt."""
        if not skill_configs:
            return ""
        parts = ["\n## Available Skills"]
        for name, config in skill_configs.items():
            desc = config.get("description", "")
            tool_names = config.get("tool_names", [])
            parts.append(f"- **{name}**: {desc}")
            if tool_names:
                parts.append(f"  Tools: {', '.join(tool_names)}")
        return "\n".join(parts)

    async def _handle_delegate_to_skill(
        self,
        args: dict[str, Any],
        skill_configs: dict[str, dict[str, Any]],
        all_tools: list[dict[str, Any]],
        all_executors: dict[str, Any],
        agent_context: AgentContext,
        context: ExecutionContext,
        node_name: str,
        max_context_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> dict[str, Any]:
        """Handle delegate_to_skill by spawning a scoped sub-agent."""
        skill_name = args.get("skill", "")
        task = args.get("task", "")
        skill_context = args.get("context", {})

        if skill_name not in skill_configs:
            return {"error": f"Unknown skill: '{skill_name}'. Available: {list(skill_configs.keys())}"}
        if not task:
            return {"error": "Task is required for delegate_to_skill."}

        config = skill_configs[skill_name]

        child_depth = agent_context.agent_depth + 1
        _sd = "  " * agent_context.agent_depth
        print(f"\n{_sd}  SKILL: {skill_name} (depth {child_depth}/{agent_context.max_agent_depth})")
        print(f"{_sd}    task: {task[:150]}{'...' if len(task) > 150 else ''}")
        print(f"{_sd}    tools: {config.get('tool_names', [])}")

        if child_depth > agent_context.max_agent_depth:
            return {"error": f"Max agent depth ({agent_context.max_agent_depth}) reached."}

        # Filter parent tools to only those referenced by skill
        requested_tool_names = set(config.get("tool_names", []))
        child_tools = [t for t in all_tools if t["name"] in requested_tool_names]
        child_executors = {k: v for k, v in all_executors.items() if k in requested_tool_names}

        # Build context_store for lazy retrieval instead of inlining context
        child_context_store: dict[str, Any] = {}
        if skill_context:
            child_context_store["skill_context"] = skill_context
            task = f"{task}\n\nContext data available via memory_recall('skill_context')."

        # Inject scratchpad tools if parent has them enabled
        if agent_context.enable_scratchpad:
            child_tools = child_tools + self._build_scratchpad_tools()

        child_agent_context = AgentContext(
            agent_depth=child_depth,
            max_agent_depth=agent_context.max_agent_depth,
            parent_model=agent_context.parent_model,
            parent_temperature=agent_context.parent_temperature,
            parent_system_prompt=config.get("system_prompt", ""),
            inheritable_tools=child_tools,
            inheritable_tool_executors=child_executors,
            allow_recursive_spawn=False,
            enable_scratchpad=agent_context.enable_scratchpad,
            parent_scratchpad=dict(agent_context.scratchpad),
            shared_store=agent_context.shared_store,  # same reference — shared across tree
            context_store=child_context_store,
        )

        self._emit_event(context, node_name, ExecutionEventType.AGENT_SPAWN, {
            "task": task[:200],
            "skill": skill_name,
            "depth": child_depth,
            "tools": list(requested_tool_names),
        })

        child_node_name = f"{node_name}/skill:{skill_name}"

        try:
            result = await self._run_agent_loop(
                model=agent_context.parent_model,
                system_prompt=config.get("system_prompt", ""),
                task=task,
                tools=child_tools,
                tool_executors=child_executors,
                max_iterations=5,
                temperature=agent_context.parent_temperature,
                context=context,
                node_name=child_node_name,
                output_schema=config.get("output_schema"),
                max_context_tokens=max_context_tokens,
                agent_context=child_agent_context,
            )
        except Exception as e:
            return {"error": f"Skill '{skill_name}' failed: {e}"}

        skill_return: dict[str, Any] = {
            "skill": skill_name,
            "response": result.get("response", ""),
            "iterations": result.get("iterations", 0),
        }

        if config.get("output_schema"):
            try:
                skill_return["data"] = json.loads(result.get("response", ""))
            except (json.JSONDecodeError, TypeError) as e:
                skill_return["data"] = None
                skill_return["parse_error"] = str(e)

        self._emit_event(context, node_name, ExecutionEventType.AGENT_CHILD_COMPLETE, {
            "skill": skill_name,
            "depth": child_depth,
            "iterations": skill_return["iterations"],
        })

        return skill_return

    def _build_tools(self, tools_config: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build tools array from inline parameter config."""
        from ...engine.tool_schema import validate_tool_definition

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

            tool_def = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": params,
            }

            # Build-time validation
            warnings = validate_tool_definition(tool_def)
            for w in warnings:
                logger.warning("[tool-validation] %s", w)

            tools.append(tool_def)

        return tools

    # ------------------------------------------------------------------
    # Scratchpad (working memory) tools
    # ------------------------------------------------------------------

    def _build_scratchpad_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions for memory_store and memory_recall."""
        tools = [
            {
                "name": "memory_store",
                "description": (
                    "Store a value in memory. Use this to save intermediate findings, "
                    "partial results, or anything you need to remember across iterations.\n\n"
                    "Prefix key with 'shared:' to write to the shared store visible to ALL "
                    "agents in the tree (parent, siblings, children). Example: "
                    "memory_store(key='shared:customer_data', value=...). "
                    "Without the prefix, the value is private to this agent."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Key to store the value under",
                        },
                        "value": {
                            "type": "string",
                            "description": "Value to store (any JSON-serializable type)",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
            {
                "name": "memory_recall",
                "description": (
                    "Recall values from memory. Call with a key to get a specific value, "
                    "or without a key to list all stored values.\n\n"
                    "Prefix key with 'shared:' to read from the shared store visible to ALL "
                    "agents. Example: memory_recall(key='shared:customer_data'). "
                    "Without the prefix, checks: private scratchpad → context store → "
                    "shared store (fallback)."
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
        from ...engine.tool_schema import validate_tool_definition
        for t in tools:
            for w in validate_tool_definition(t):
                logger.warning("[tool-validation] %s", w)
        return tools

    # ------------------------------------------------------------------
    # Sub-agent spawn tools
    # ------------------------------------------------------------------

    def _build_spawn_tools(self) -> list[dict[str, Any]]:
        """Return the spawn_agent tool definition.

        Schema is intentionally flat — just task + name. The handler accepts
        extra args (model, system_prompt, temperature, max_iterations) that
        models may include even though they're not in the schema.

        For parallel execution, the model calls spawn_agent multiple times in
        one turn and the runtime executes them concurrently via asyncio.gather.
        """
        return [
            {
                "name": "spawn_agent",
                "description": (
                    "Spawn a sub-agent to perform a specific task. The sub-agent "
                    "runs independently, executes the task, and returns a result. "
                    "Call this tool multiple times in the same turn to run "
                    "sub-agents concurrently. Include relevant context data "
                    "directly in the task string."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task for the sub-agent. Include any context data inline.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Label for this sub-agent (for identifying results).",
                        },
                    },
                    "required": ["task"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Programmatic Tool Calling (PTC)
    # ------------------------------------------------------------------

    def _build_ptc_tools(
        self, tool_names: list[str],
    ) -> list[dict[str, Any]]:
        """Return the run_script tool definition for programmatic tool calling.

        Allows the agent to write a Python script that calls multiple tools
        in a single turn with loops, conditionals, and error handling — more
        robust than relying on the LLM to produce perfect structured output.
        """
        tool_list = ", ".join(f'"{n}"' for n in tool_names)
        return [
            {
                "name": "run_script",
                "description": (
                    "Execute a Python script that can call multiple tools programmatically. "
                    "Use this when you need to: batch multiple tool calls with loops, "
                    "apply conditional logic based on tool results, or handle errors in code.\n\n"
                    "Your script has access to:\n"
                    "  - `call_tool(name: str, args: dict) -> dict` — call any available tool\n"
                    "  - `results` — a list that collects return values; append your outputs\n"
                    "  - Standard Python (json, re, math, itertools, collections)\n\n"
                    f"Available tools: [{tool_list}]\n\n"
                    "Example:\n"
                    "```python\n"
                    "# Fetch 3 items and process them\n"
                    "ids = ['a', 'b', 'c']\n"
                    "for item_id in ids:\n"
                    "    result = call_tool('get_item', {'id': item_id})\n"
                    "    if 'error' not in result:\n"
                    "        call_tool('process_item', {'data': result})\n"
                    "        results.append({'id': item_id, 'status': 'ok'})\n"
                    "    else:\n"
                    "        results.append({'id': item_id, 'status': 'failed'})\n"
                    "```"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": "Python script to execute. Use call_tool(name, args) to invoke tools.",
                        },
                    },
                    "required": ["script"],
                },
            },
        ]

    async def _execute_ptc_script(
        self,
        script: str,
        tool_executors: dict[str, Any],
        tool_schema_map: dict[str, Any],
        context: ExecutionContext,
        agent_context: AgentContext | None,
        node_name: str,
        max_context_tokens: int,
    ) -> dict[str, Any]:
        """Execute a PTC script in a sandboxed environment with tool access.

        The script gets a `call_tool(name, args)` function that synchronously
        invokes tools and returns their results. The script runs in a thread
        with a restricted global namespace.
        """
        import textwrap

        # Collect tool call logs for the agent
        tool_call_log: list[dict[str, Any]] = []
        results_collector: list[Any] = []
        _sd = "  " * (agent_context.agent_depth if agent_context else 0)

        # Create synchronous bridge to async tool execution
        loop = asyncio.get_running_loop()

        def call_tool_sync(name: str, args: dict | None = None) -> Any:
            """Synchronous wrapper for tool execution (called from script thread)."""
            args = args or {}

            # Schema-based coercion
            schema = tool_schema_map.get(name, {})
            if schema:
                args = self._coerce_args(args, schema)
                arg_errors = self._validate_tool_args(args, schema)
                if arg_errors:
                    error = {"error": f"Invalid arguments: {'; '.join(arg_errors)}"}
                    tool_call_log.append({"tool": name, "args": args, "result": error, "is_error": True})
                    return error

            # Run the async executor from the sync thread
            future = asyncio.run_coroutine_threadsafe(
                self._execute_tool(
                    name, args, tool_executors, context,
                    agent_context=agent_context,
                    node_name=f"{node_name}/ptc",
                    max_context_tokens=max_context_tokens,
                ),
                loop,
            )
            try:
                result = future.result(timeout=_DEFAULT_TOOL_TIMEOUT)
            except TimeoutError:
                result = {"error": f"Tool '{name}' timed out"}
            except Exception as e:
                result = {"error": f"Tool '{name}' failed: {e}"}

            is_error = isinstance(result, dict) and "error" in result
            tool_call_log.append({"tool": name, "args": args, "result": result, "is_error": is_error})

            _res_preview = str(result)[:200]
            print(f"{_sd}    PTC call_tool({name}) => {_res_preview}")

            return result

        # Build restricted namespace
        import collections
        import itertools
        import math

        safe_globals = {
            "__builtins__": {
                # Safe builtins only
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "map": map, "filter": filter, "sorted": sorted,
                "reversed": reversed, "list": list, "dict": dict, "set": set,
                "tuple": tuple, "str": str, "int": int, "float": float,
                "bool": bool, "type": type, "isinstance": isinstance,
                "print": lambda *a, **kw: None,  # suppress prints
                "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
                "any": any, "all": all, "hasattr": hasattr, "getattr": getattr,
                "KeyError": KeyError, "ValueError": ValueError,
                "TypeError": TypeError, "IndexError": IndexError,
                "Exception": Exception, "StopIteration": StopIteration,
                "True": True, "False": False, "None": None,
            },
            "call_tool": call_tool_sync,
            "results": results_collector,
            "json": json,
            "re": re,
            "math": math,
            "itertools": itertools,
            "collections": collections,
        }

        # Clean the script (strip markdown fences if present)
        clean_script = script.strip()
        if clean_script.startswith("```"):
            # Remove opening fence (with optional language tag)
            first_newline = clean_script.index("\n")
            clean_script = clean_script[first_newline + 1:]
        if clean_script.endswith("```"):
            clean_script = clean_script[:-3]
        clean_script = textwrap.dedent(clean_script).strip()

        print(f"{_sd}  PTC SCRIPT ({len(clean_script)} chars, {clean_script.count(chr(10))+1} lines)")

        # Execute in thread with timeout
        def run_in_thread():
            exec(compile(clean_script, "<ptc_script>", "exec"), safe_globals)

        try:
            await asyncio.wait_for(
                asyncio.to_thread(run_in_thread),
                timeout=_PTC_SCRIPT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return {
                "error": f"Script timed out after {_PTC_SCRIPT_TIMEOUT}s",
                "tool_calls": tool_call_log,
                "partial_results": results_collector,
            }
        except Exception as e:
            # Extract the relevant error from the script
            import traceback
            tb_lines = traceback.format_exception(type(e), e, e.__traceback__)
            # Filter to only show the ptc_script frames
            script_lines = [l for l in tb_lines if "<ptc_script>" in l or not l.startswith("  File")]
            error_msg = "".join(script_lines[-3:]).strip() if script_lines else str(e)
            return {
                "error": f"Script error: {error_msg}",
                "tool_calls": tool_call_log,
                "partial_results": results_collector,
            }

        return {
            "results": results_collector,
            "tool_calls": tool_call_log,
            "tool_calls_count": len(tool_call_log),
        }

    def _resolve_child_tools(
        self,
        requested_tool_names: list[str] | None,
        agent_context: AgentContext,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Resolve which tools a child agent inherits from the parent."""
        parent_tools = agent_context.inheritable_tools
        parent_executors = agent_context.inheritable_tool_executors

        if requested_tool_names is None:
            logger.debug(
                "[sub-agent] inheriting ALL parent tools: [%s]",
                ", ".join(t["name"] for t in parent_tools),
            )
            return list(parent_tools), dict(parent_executors)

        requested = set(requested_tool_names)
        child_tools = [t for t in parent_tools if t["name"] in requested]
        child_executors = {k: v for k, v in parent_executors.items() if k in requested}
        missing = requested - {t["name"] for t in child_tools}
        if missing:
            logger.warning(
                "[sub-agent] requested tools not found in parent: %s", missing,
            )
        logger.debug(
            "[sub-agent] resolved %d/%d requested tools: [%s]",
            len(child_tools), len(requested),
            ", ".join(t["name"] for t in child_tools),
        )
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
        child_depth = agent_context.agent_depth + 1
        _sd = "  " * agent_context.agent_depth
        _agent_name = args.get("name") or f"sub-agent[{child_depth}]"
        print(f"\n{_sd}  SPAWN: {_agent_name} (depth {child_depth}/{agent_context.max_agent_depth})")
        print(f"{_sd}    task: {(args.get('task') or '')[:150]}{'...' if len(args.get('task', '')) > 150 else ''}")
        logger.info(
            "[sub-agent] spawn_agent requested | node=%s depth=%d/%d task=%s",
            node_name, child_depth, agent_context.max_agent_depth,
            (args.get("task") or "")[:120],
        )

        if agent_context.agent_depth >= agent_context.max_agent_depth:
            logger.warning(
                "[sub-agent] BLOCKED: max depth %d reached | node=%s",
                agent_context.max_agent_depth, node_name,
            )
            return {
                "error": (
                    f"Maximum sub-agent depth ({agent_context.max_agent_depth}) reached. "
                    "Cannot spawn further sub-agents."
                ),
            }

        task = args.get("task", "")
        if not task:
            logger.warning("[sub-agent] BLOCKED: empty task | node=%s", node_name)
            return {"error": "Task is required for spawn_agent."}

        child_model = args.get("model") or agent_context.parent_model
        child_system_prompt = args.get("system_prompt") or agent_context.parent_system_prompt
        child_max_iterations = args.get("max_iterations", 5)
        child_temperature = args.get("temperature", agent_context.parent_temperature)

        child_tools, child_executors = self._resolve_child_tools(
            args.get("tools"), agent_context
        )
        logger.info(
            "[sub-agent] config | model=%s temp=%.2f max_iter=%d tools=[%s] scratchpad=%s",
            child_model, child_temperature, child_max_iterations,
            ", ".join(t["name"] for t in child_tools),
            agent_context.enable_scratchpad,
        )

        # Create child agent context; spawn/scratchpad tools gated by config.
        can_spawn = (
            agent_context.allow_recursive_spawn
            and child_depth < agent_context.max_agent_depth
        )
        logger.debug(
            "[sub-agent] can_spawn=%s (recursive=%s, depth %d < max %d)",
            can_spawn, agent_context.allow_recursive_spawn,
            child_depth, agent_context.max_agent_depth,
        )
        child_agent_context = AgentContext(
            agent_depth=child_depth,
            max_agent_depth=agent_context.max_agent_depth,
            parent_model=child_model,
            parent_temperature=child_temperature,
            parent_system_prompt=child_system_prompt,
            inheritable_tools=child_tools,
            inheritable_tool_executors=child_executors,
            allow_recursive_spawn=agent_context.allow_recursive_spawn,
            enable_scratchpad=agent_context.enable_scratchpad,
            parent_scratchpad=dict(agent_context.scratchpad),  # snapshot
            shared_store=agent_context.shared_store,  # same reference — shared across tree
        )
        # Give child scratchpad tools only if parent has them enabled
        if agent_context.enable_scratchpad:
            child_tools = child_tools + self._build_scratchpad_tools()
            logger.debug("[sub-agent] injected scratchpad tools for child (parent keys: %s)",
                         list(agent_context.scratchpad.keys()))
        if can_spawn:
            child_tools = child_tools + self._build_spawn_tools()
            logger.debug("[sub-agent] injected spawn tools for child")

        # Rich context: store context_snippets in context_store for lazy retrieval
        context_snippets = args.get("context_snippets", [])
        if context_snippets:
            ctx_keys = []
            for i, snippet in enumerate(context_snippets):
                ctx_key = f"ctx_{i}"
                if isinstance(snippet, dict):
                    child_agent_context.context_store[ctx_key] = snippet.get("content", "")
                    label = snippet.get("label", f"Context {i}")
                    ctx_keys.append(f"- memory_recall('{ctx_key}'): {label}")
                elif isinstance(snippet, str):
                    child_agent_context.context_store[ctx_key] = snippet
                    ctx_keys.append(f"- memory_recall('{ctx_key}')")
            task = (
                f"{task}\n\nContext available via memory_recall:\n"
                + "\n".join(ctx_keys)
            )
            logger.debug("[sub-agent] stored %d context snippets in context_store", len(context_snippets))

        expected_output = args.get("expected_output")
        # Normalize: model may send expected_output as a JSON string instead of dict
        if isinstance(expected_output, str):
            try:
                expected_output = json.loads(expected_output)
            except (json.JSONDecodeError, TypeError):
                expected_output = None  # unparseable, ignore
        if expected_output:
            logger.debug("[sub-agent] expected_output schema set: %s", list(expected_output.get("properties", {}).keys()) if isinstance(expected_output, dict) else "non-dict")

        # Emit spawn event
        self._emit_event(context, node_name, ExecutionEventType.AGENT_SPAWN, {
            "task": task[:200],
            "model": child_model,
            "depth": child_depth,
        })

        agent_label = args.get("name") or f"sub-agent[{child_depth}]"
        child_node_name = f"{node_name}/{agent_label}"
        logger.info("[sub-agent] STARTING child loop | name=%s", child_node_name)
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
                node_name=child_node_name,
                max_context_tokens=max_context_tokens,
                agent_context=child_agent_context,
            )
        except Exception as e:
            logger.error(
                "[sub-agent] FAILED | name=%s error=%s", child_node_name, e,
                exc_info=True,
            )
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
                logger.debug("[sub-agent] parsed structured output successfully")
            except (json.JSONDecodeError, TypeError) as e:
                child_return["data"] = None
                child_return["parse_error"] = str(e)
                logger.warning("[sub-agent] structured output parse failed: %s", e)

        _resp_preview = child_return.get("response", "")[:200]
        print(f"{_sd}  CHILD DONE: {child_node_name}")
        print(f"{_sd}    iterations={child_return['iterations']}  scratchpad={list(child_agent_context.scratchpad.keys())}")
        print(f"{_sd}    response: {_resp_preview}{'...' if len(child_return.get('response', '')) > 200 else ''}")
        logger.info(
            "[sub-agent] COMPLETED | name=%s iterations=%d response_len=%d scratchpad_keys=%s",
            child_node_name, child_return["iterations"],
            len(child_return.get("response", "")),
            list(child_agent_context.scratchpad.keys()),
        )

        # Emit child complete event
        self._emit_event(context, node_name, ExecutionEventType.AGENT_CHILD_COMPLETE, {
            "depth": child_depth,
            "iterations": child_return["iterations"],
            "has_evidence": bool(child_agent_context.scratchpad),
        })

        return child_return

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
    def _estimate_tokens(messages: list[dict[str, Any]], model: str = "") -> int:
        """Estimate token count for a list of messages.

        Uses tiktoken for OpenAI models (accurate). For other providers,
        uses a calibrated chars-per-token ratio:
          - Gemini: ~3.2 chars/token (slightly more aggressive tokenizer)
          - Claude: ~3.5 chars/token
          - Llama:  ~3.5 chars/token
        JSON overhead (keys, braces, quotes) is counted in chars.
        """
        if model.startswith(("gpt-", "o1-", "o3", "o4")):
            enc = _get_tiktoken_encoder(model)
            if enc is not None:
                total = 0
                for m in messages:
                    # Per-message overhead: ~4 tokens for role/name/etc
                    total += 4
                    content = m.get("content", "")
                    if isinstance(content, str):
                        total += len(enc.encode(content))
                    # Tool calls in assistant messages
                    for tc in m.get("tool_calls", []):
                        total += len(enc.encode(json.dumps(tc, default=str)))
                return total

        # Provider-specific chars-per-token ratios
        if model.startswith("gemini"):
            cpt = 3.2
        else:
            cpt = _CHARS_PER_TOKEN_FALLBACK

        total_chars = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            # Count tool call args
            for tc in m.get("tool_calls", []):
                total_chars += len(json.dumps(tc, default=str))
            # Per-message overhead: role key, braces ~20 chars
            total_chars += 20
        return int(total_chars / cpt)

    @staticmethod
    def _estimate_tools_tokens(tools: list[dict[str, Any]] | None, model: str = "") -> int:
        """Estimate tokens consumed by tool definitions sent alongside messages.

        Tool schemas are serialized to JSON and sent with every LLM call,
        so they eat into the available context budget.
        """
        if not tools:
            return 0
        tools_json = json.dumps(tools, default=str)
        if model.startswith(("gpt-", "o1-", "o3", "o4")):
            enc = _get_tiktoken_encoder(model)
            if enc is not None:
                return len(enc.encode(tools_json))
        cpt = 3.2 if model.startswith("gemini") else _CHARS_PER_TOKEN_FALLBACK
        return int(len(tools_json) / cpt)

    @staticmethod
    def _compact_messages(
        messages: list[dict[str, Any]], max_tokens: int, model: str = ""
    ) -> list[dict[str, Any]]:
        """Compact conversation history to fit within the token budget.

        Two-pass approach:
        1. First pass: Summarize old tool results (older than last 3 iterations)
           into one-line placeholders to preserve conversation flow.
        2. Second pass: Drop oldest compressed groups if still over budget.

        Never drops: system message, first user message, last 2 iterations.
        """
        total = AIAgentNode._estimate_tokens(messages, model)
        if total <= max_tokens:
            return messages

        # Identify protected prefix: system prompt + first user message
        protected_end = 0
        for i, m in enumerate(messages):
            protected_end = i + 1
            if m["role"] == "user":
                break

        prefix = messages[:protected_end]
        middle = list(messages[protected_end:])

        # Count iteration boundaries (assistant messages with tool_calls)
        iteration_starts = [
            i for i, m in enumerate(middle)
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]

        # Protect last 2 iterations
        protect_from = len(middle)
        if len(iteration_starts) >= 2:
            protect_from = iteration_starts[-2]
        elif len(iteration_starts) >= 1:
            protect_from = iteration_starts[-1]

        # First pass: summarize old tool result messages
        for i in range(protect_from):
            m = middle[i]
            if m.get("role") == "tool" and m.get("content"):
                content = m["content"]
                tool_name = m.get("name", "unknown")
                # Summarize: detect type and create brief
                brief = ""
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                    if isinstance(parsed, list):
                        brief = f"array[{len(parsed)} items]"
                    elif isinstance(parsed, dict):
                        if "error" in parsed:
                            brief = f"error: {str(parsed['error'])[:80]}"
                        else:
                            brief = f"object with keys: {list(parsed.keys())[:5]}"
                    else:
                        brief = f"{type(parsed).__name__}: {str(parsed)[:60]}"
                except (json.JSONDecodeError, TypeError):
                    brief = f"text[{len(content)} chars]"
                middle[i] = dict(m, content=f"[{tool_name} returned {brief}]")

        result = prefix + middle
        current_tokens = AIAgentNode._estimate_tokens(result, model)

        # Second pass: drop oldest groups if still over budget
        if current_tokens > max_tokens:
            droppable = result[protected_end:]
            kept_suffix = []
            # Rebuild from the end, keeping what fits
            for m in reversed(droppable):
                test = prefix + [m] + kept_suffix
                test_tokens = AIAgentNode._estimate_tokens(test, model)
                if test_tokens <= max_tokens:
                    kept_suffix.insert(0, m)
                else:
                    # Drop orphan tool results if we drop their assistant
                    break
            result = prefix + kept_suffix

        return result

    @staticmethod
    def _build_planning_prompt() -> str:
        """Return the reasoning protocol appended to the system prompt."""
        return (
            "\n\n## Reasoning Protocol\n"
            "Follow this protocol for every task:\n\n"
            "PLAN phase: In your FIRST response, include a <plan>...</plan> block listing "
            "the steps you intend to take, then IMMEDIATELY make your first tool call(s) "
            "in the same response. Do NOT send a plan-only message with no tool calls.\n\n"
            "ACT phase: Call multiple independent tools in the same turn when possible.\n\n"
            "REFLECT phase: Before giving your final answer, include a <reflect>...</reflect> "
            "block with:\n"
            "  - Steps completed and evidence gathered\n"
            "  - Remaining gaps or uncertainties\n"
            "  - Confidence level: low / medium / high\n\n"
            "Present your best answer with any stated uncertainties. "
            "Do not fabricate information — if you lack evidence, say so."
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
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Thin wrapper: consumes the streaming generator and dispatches events."""
        depth = agent_context.agent_depth if agent_context else 0
        _d = "  " * depth
        print(f"\n{_d}{'='*60}")
        print(f"{_d}AGENT START | {node_name} (depth={depth})")
        print(f"{_d}  model={model}  max_iter={max_iterations}  tools={len(tools)}  temp={temperature}")
        print(f"{_d}  task: {task[:150]}{'...' if len(task) > 150 else ''}")
        print(f"{_d}{'='*60}")
        logger.info(
            "[agent-loop] START | node=%s depth=%d model=%s max_iter=%d tools=%d",
            node_name, depth, model, max_iterations, len(tools),
        )
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
            max_output_tokens=max_output_tokens,
        ):
            etype = event.get("type", "")
            if etype in EVENT_TYPE_MAP:
                self._emit_event(context, node_name, EVENT_TYPE_MAP[etype], event)
            elif etype == "result":
                result = event["data"]
        _resp_preview = result.get("response", "")[:200]
        _structured = result.get("structured")
        print(f"\n{_d}{'='*60}")
        print(f"{_d}AGENT END | {node_name} (depth={depth})")
        print(f"{_d}  iterations={result.get('iterations', 0)}  response_len={len(result.get('response', ''))}")
        if _structured:
            print(f"{_d}  structured keys: {list(_structured.keys())}")
        else:
            print(f"{_d}  response: {_resp_preview}{'...' if len(result.get('response', '')) > 200 else ''}")
        print(f"{_d}{'='*60}")
        logger.info(
            "[agent-loop] END | node=%s depth=%d iterations=%d response_len=%d",
            node_name, depth, result.get("iterations", 0),
            len(result.get("response", "")),
        )
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
        max_output_tokens: int = 4096,
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
        # Per-tool failure history: {tool_name: [(args_snapshot, error), ...]}
        failure_tracker: dict[str, list[tuple[str, str]]] = {}
        malformed_retries = 0
        base_temperature = temperature
        _total_input_tokens = 0
        _total_output_tokens = 0
        _total_llm_time_ms = 0.0
        _warned_80 = False
        _warned_90 = False

        _ld = "  " * (agent_context.agent_depth if agent_context else 0)
        while iterations < max_iterations:
            iterations += 1
            print(f"{_ld}  --- iteration {iterations}/{max_iterations} ({node_name}) ---")
            yield {"type": "iteration_start", "iteration": iterations}

            # Trim context if it's grown too large
            # Reserve budget for tool schemas (sent alongside messages every call)
            tools_budget = self._estimate_tools_tokens(tools, model)
            messages = self._compact_messages(messages, max_context_tokens - tools_budget, model)

            # B5: Budget awareness — inject warnings once as iterations run out
            budget_used = iterations / max_iterations
            is_last_iteration = iterations == max_iterations
            if budget_used >= 0.9 and not is_last_iteration and not _warned_90:
                _warned_90 = True
                remaining = max_iterations - iterations
                messages.append({
                    "role": "user",
                    "content": f"FINAL WARNING: Only {remaining} iteration(s) remaining. Provide your answer now.",
                })
            elif budget_used >= 0.8 and not _warned_80:
                _warned_80 = True
                remaining = max_iterations - iterations
                messages.append({
                    "role": "user",
                    "content": f"Note: {remaining} iterations remaining out of {max_iterations}. Start wrapping up or batch your remaining tool calls.",
                })

            # B7: Lower temperature after consecutive failures or malformed retries
            temp_penalty = 0.2 * consecutive_failures + 0.15 * malformed_retries
            effective_temperature = max(
                0.1, base_temperature - temp_penalty,
            ) if (consecutive_failures > 0 or malformed_retries > 0) else base_temperature

            # On last iteration, call LLM without tools to guarantee a text response
            # Dynamic tool filtering: after warmup iterations, only send tools
            # that the model has used or that are "meta" tools (scratchpad, spawn, etc.)
            iter_tools: list[dict[str, Any]] | None = None
            if tools and not is_last_iteration:
                _ALWAYS_INCLUDE = _SCRATCHPAD_TOOL_NAMES | _SPAWN_TOOL_NAMES | _SKILL_TOOL_NAMES | _PTC_TOOL_NAMES
                _WARMUP_ITERATIONS = 3
                if iterations > _WARMUP_ITERATIONS and tool_calls_list:
                    # Collect tools actually used so far
                    _used_tools = {tc_entry["tool"] for tc_entry in tool_calls_list}
                    # Always include meta tools + used tools
                    _relevant = _used_tools | _ALWAYS_INCLUDE
                    # Filter: keep used tools + meta tools + any tool not yet tried
                    # (so the model can still discover new tools, but seen-and-unused
                    # tools don't clutter context)
                    _all_tool_names = {t["name"] for t in tools}
                    # "Seen" = mentioned in assistant text or tool results
                    _mentioned_in_context = set()
                    for msg in messages[-6:]:  # check recent messages
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            for tn in _all_tool_names:
                                if tn in content:
                                    _mentioned_in_context.add(tn)
                    _relevant |= _mentioned_in_context
                    filtered = [t for t in tools if t["name"] in _relevant]
                    # Only filter if it actually reduces the list meaningfully
                    if len(filtered) >= 2 and len(filtered) < len(tools):
                        iter_tools = filtered
                        logger.debug(
                            "Dynamic tool filter: %d/%d tools (used=%s)",
                            len(filtered), len(tools), _used_tools,
                        )
                    else:
                        iter_tools = tools
                else:
                    iter_tools = tools

            # When no tools and structured output requested, pass response_format
            # directly so the model generates JSON natively in one shot.
            iter_kwargs: dict[str, Any] = {}
            if iter_tools is None and response_format:
                iter_kwargs["response_format"] = response_format

            response = await call_llm(
                model=model,
                messages=messages,
                temperature=effective_temperature,
                tools=iter_tools,
                max_tokens=max_output_tokens,
                **iter_kwargs,
            )

            # Accumulate token usage
            if response.usage:
                _total_input_tokens += response.usage.input_tokens
                _total_output_tokens += response.usage.output_tokens
            if response.response_time_ms:
                _total_llm_time_ms += response.response_time_ms

            # Gemini sometimes returns MALFORMED_FUNCTION_CALL — retry with a cap
            if response.malformed_tool_call and not response.tool_calls:
                malformed_retries += 1
                logger.warning("Malformed tool call on iteration %d (retry %d/%d)", iterations, malformed_retries, MAX_MALFORMED_RETRIES)
                if malformed_retries > MAX_MALFORMED_RETRIES:
                    logger.error("Exceeded malformed tool call retry cap, stopping agent")
                    yield {"type": "result", "data": {
                        "response": response.text or "Agent stopped: repeated malformed tool calls",
                        "toolCalls": tool_calls_list,
                        "iterations": iterations,
                        "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                    }}
                    return

                messages.append({"role": "assistant", "content": response.text or ""})
                # Detailed malformed feedback with schema hints and examples
                feedback = self._build_malformed_feedback(
                    response.text, tools, malformed_retries,
                )
                messages.append({"role": "user", "content": feedback})
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
                            print(f"{_ld}  PLAN: {plan_text[:200]}{'...' if len(plan_text) > 200 else ''}")
                            yield {"type": "agent_plan", "plan": plan_text, "iteration": iterations}
                        if reflect_text:
                            print(f"{_ld}  REFLECT: {reflect_text[:200]}{'...' if len(reflect_text) > 200 else ''}")
                            yield {"type": "agent_reflect", "reflection": reflect_text, "iteration": iterations}

                # Append assistant message with tool calls
                messages.append(response.get_assistant_message())

                # Execute all tools in parallel (with result caching)
                results = await asyncio.gather(
                    *[
                        self._run_one_tool(
                            tc, tool_schema_map, tool_executors, context,
                            agent_context, node_name, max_context_tokens,
                        )
                        for tc in response.tool_calls
                    ]
                )

                # Process results in order (messages must stay ordered)
                iteration_had_failure = False
                for tc, tool_result, result_str, is_error in results:
                    # Print tool call
                    _args_preview = json.dumps(tc.args, default=str)
                    if len(_args_preview) > 200:
                        _args_preview = _args_preview[:200] + "..."
                    print(f"{_ld}  TOOL CALL: {tc.name}({_args_preview})")
                    # Print tool result
                    _res_preview = str(tool_result)
                    if len(_res_preview) > 300:
                        _res_preview = _res_preview[:300] + "..."
                    _err_marker = " ERROR" if is_error else ""
                    print(f"{_ld}  TOOL RESULT{_err_marker}: {_res_preview}")

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

                # B3: Smart failure tracking
                if iteration_had_failure:
                    consecutive_failures += 1
                    # Track per-tool failures
                    for tc, tool_result, result_str, is_error in results:
                        if is_error:
                            args_key = json.dumps(tc.args, sort_keys=True, default=str)
                            err_msg = tool_result.get("error", "") if isinstance(tool_result, dict) else str(tool_result)
                            failure_tracker.setdefault(tc.name, []).append((args_key, err_msg))

                            tool_failures = failure_tracker[tc.name]
                            # Detect same-tool-same-args repeated failure
                            same_args_count = sum(1 for a, _ in tool_failures if a == args_key)
                            if same_args_count >= 2:
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        f"Tool '{tc.name}' has failed {same_args_count} times with the same arguments. "
                                        f"Do NOT retry with the same inputs. Either use different arguments or skip this tool."
                                    ),
                                })
                            # Detect same-tool-different-args repeated failures
                            elif len(tool_failures) >= 4:
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        f"Tool '{tc.name}' has failed {len(tool_failures)} times with different arguments. "
                                        f"Consider an alternative approach that doesn't rely on this tool."
                                    ),
                                })
                else:
                    consecutive_failures = 0

                # B1: Structured error feedback
                if iteration_had_failure:
                    error_details = []
                    for tc, tool_result, result_str, is_error in results:
                        if is_error:
                            schema = tool_schema_map.get(tc.name, {})
                            required = schema.get("required", [])
                            err_msg = tool_result.get("error", "") if isinstance(tool_result, dict) else str(tool_result)
                            error_details.append(
                                f"- {tc.name}(args={json.dumps(tc.args, default=str)}): {err_msg}"
                                + (f"\n  Required params: {required}" if required else "")
                            )
                    if error_details:
                        messages.append({
                            "role": "user",
                            "content": (
                                "The following tool calls failed this iteration:\n"
                                + "\n".join(error_details)
                                + "\n\nCheck that you are using values returned by "
                                "previous tool calls, not fabricating IDs."
                            ),
                        })

                # B3: Circuit breaker — graceful: make one final LLM call without tools
                if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                    logger.warning(
                        "Circuit breaker hit after %d consecutive failures, forcing text response",
                        consecutive_failures,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"Tools have failed {consecutive_failures} consecutive iterations. "
                            "Stop calling tools and provide the best answer you can with the information gathered so far."
                        ),
                    })
                    fallback = await call_llm(
                        model=model,
                        messages=messages,
                        temperature=base_temperature,
                        tools=None,
                        max_tokens=max_output_tokens,
                    )
                    if fallback.usage:
                        _total_input_tokens += fallback.usage.input_tokens
                        _total_output_tokens += fallback.usage.output_tokens
                    if fallback.response_time_ms:
                        _total_llm_time_ms += fallback.response_time_ms
                    yield {"type": "result", "data": {
                        "response": fallback.text or f"Agent stopped: tools failed {MAX_CONSECUTIVE_TOOL_FAILURES} consecutive iterations",
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
                        print(f"{_ld}  FINAL PLAN: {plan_text[:200]}{'...' if len(plan_text) > 200 else ''}")
                        yield {"type": "agent_plan", "plan": plan_text, "iteration": iterations}
                    if reflect_text:
                        print(f"{_ld}  FINAL REFLECT: {reflect_text[:200]}{'...' if len(reflect_text) > 200 else ''}")
                        yield {"type": "agent_reflect", "reflection": reflect_text, "iteration": iterations}

                print(f"{_ld}  FINAL RESPONSE ({len(final_response)} chars): {final_response[:300]}{'...' if len(final_response) > 300 else ''}")
                yield {"type": "agent_response", "content": final_response}

                # Structured output: response_format was already passed on the
                # call above when iter_tools was None, so try parsing directly.
                # Fall back to a retry loop only if the initial parse/validation fails.
                if response_format:
                    best_parsed = None
                    best_errors: list[str] = []

                    # First, try to parse the response we already have
                    try:
                        parsed = json.loads(final_response)
                        validation_errors = self._validate_against_schema(parsed, output_schema or {})
                        if not validation_errors:
                            print(f"{_ld}  STRUCTURED OUTPUT: validated OK — keys={list(parsed.keys())}")
                            yield {"type": "agent_output_validation", "status": "success", "retry": 0}
                            yield {"type": "result", "data": {
                                "response": final_response,
                                "structured": parsed,
                                "toolCalls": tool_calls_list,
                                "iterations": iterations,
                                "_usage": {"inputTokens": _total_input_tokens, "outputTokens": _total_output_tokens, "llmResponseTimeMs": _total_llm_time_ms},
                            }}
                            return
                        best_parsed = parsed
                        best_errors = validation_errors
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # Retry loop: ask the model to fix/reformat as JSON
                    messages.append({"role": "assistant", "content": final_response})
                    messages.append({
                        "role": "user",
                        "content": "Now format your answer as JSON matching the required schema.",
                    })

                    for retry in range(MAX_OUTPUT_RETRIES):
                        struct_response = await call_llm(
                            model=model,
                            messages=messages,
                            temperature=0.0,
                            tools=None,
                            max_tokens=max_output_tokens,
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
                        except json.JSONDecodeError as e:
                            yield {"type": "agent_output_validation", "status": "parse_error", "error": str(e), "retry": retry + 1}
                            if retry < MAX_OUTPUT_RETRIES - 1:
                                messages.append({"role": "assistant", "content": struct_text})
                                messages.append({"role": "user", "content": f"JSON parse error: {e}. Please fix and return valid JSON matching the schema."})
                                continue
                            break

                        validation_errors = self._validate_against_schema(parsed, output_schema or {})
                        best_parsed = parsed

                        if validation_errors:
                            best_errors = validation_errors
                            yield {"type": "agent_output_validation", "status": "validation_error", "errors": validation_errors, "retry": retry + 1}
                            if retry < MAX_OUTPUT_RETRIES - 1:
                                messages.append({"role": "assistant", "content": struct_text})
                                messages.append({"role": "user", "content": f"Schema validation errors: {'; '.join(validation_errors)}. Please fix and return valid JSON."})
                                continue
                        else:
                            yield {"type": "agent_output_validation", "status": "success", "retry": retry + 1}
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
    def _compress_tool_result(
        result_str: str, tool_name: str = "", max_chars: int = 8000,
    ) -> str:
        """Compress a tool result, preserving structure over raw data.

        - JSON arrays: keep first 3 items + count + schema of first item
        - JSON objects: keep all keys, truncate long string values
        - Non-JSON: head+tail fallback
        """
        if len(result_str) <= max_chars:
            return result_str

        # Try structured compression for JSON
        try:
            data = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            # Non-JSON fallback: head + tail
            keep = max_chars // 2
            truncated = len(result_str) - max_chars
            return (
                result_str[:keep]
                + f"\n\n[... truncated {truncated} chars ...]\n\n"
                + result_str[-keep:]
            )

        if isinstance(data, list) and len(data) > 3:
            # Keep first 3 items + summary
            sample = data[:3]
            schema_hint = {}
            if data and isinstance(data[0], dict):
                schema_hint = {k: type(v).__name__ for k, v in data[0].items()}
            compressed = {
                "_compressed": True,
                "total_items": len(data),
                "sample": sample,
                "item_schema": schema_hint or None,
            }
            out = json.dumps(compressed, default=str)
            if len(out) <= max_chars:
                return out

        if isinstance(data, dict):
            # Truncate long string values
            compressed_dict = {}
            for k, v in data.items():
                if isinstance(v, str) and len(v) > 500:
                    compressed_dict[k] = v[:500] + f"...[{len(v)} chars]"
                else:
                    compressed_dict[k] = v
            out = json.dumps(compressed_dict, default=str)
            if len(out) <= max_chars:
                return out

        # Final fallback: head + tail
        keep = max_chars // 2
        truncated = len(result_str) - max_chars
        return (
            result_str[:keep]
            + f"\n\n[... truncated {truncated} chars ...]\n\n"
            + result_str[-keep:]
        )

    @staticmethod
    def _coerce_args(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        """Coerce tool arguments to match schema types where unambiguous.

        Handles: string→int, string→bool, string→object, value→array.
        """
        if not schema or not args:
            return args

        properties = schema.get("properties", {})
        coerced = dict(args)

        for key, value in coerced.items():
            if key not in properties:
                continue
            expected = (properties[key].get("type") or "").lower()
            if not expected:
                continue

            if expected == "integer" and isinstance(value, str):
                try:
                    coerced[key] = int(value)
                    logger.debug("Coerced '%s' from str to int", key)
                except (ValueError, TypeError):
                    pass

            elif expected == "number" and isinstance(value, str):
                try:
                    coerced[key] = float(value)
                    logger.debug("Coerced '%s' from str to number", key)
                except (ValueError, TypeError):
                    pass

            elif expected == "boolean" and isinstance(value, str):
                lower = value.lower().strip()
                if lower in ("true", "1", "yes"):
                    coerced[key] = True
                    logger.debug("Coerced '%s' from str to bool", key)
                elif lower in ("false", "0", "no"):
                    coerced[key] = False
                    logger.debug("Coerced '%s' from str to bool", key)

            elif expected == "object" and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        coerced[key] = parsed
                        logger.debug("Coerced '%s' from str to object", key)
                except (json.JSONDecodeError, TypeError):
                    pass

            elif expected == "array" and not isinstance(value, list):
                coerced[key] = [value]
                logger.debug("Coerced '%s' from %s to array", key, type(value).__name__)

        return coerced

    @staticmethod
    def _fuzzy_match_tool_name(
        name: str, available: set[str],
    ) -> str | None:
        """Try to recover a tool name via case-insensitive or edit-distance match."""
        if not name or not available:
            return None

        # Case-insensitive match
        name_lower = name.lower()
        for candidate in available:
            if candidate.lower() == name_lower:
                logger.info("Fuzzy tool name match: '%s' -> '%s' (case)", name, candidate)
                return candidate

        # Edit distance ≤ 2 (simple Levenshtein)
        def _edit_dist(a: str, b: str) -> int:
            if abs(len(a) - len(b)) > 2:
                return 3  # early exit
            m, n = len(a), len(b)
            dp = list(range(n + 1))
            for i in range(1, m + 1):
                prev, dp[0] = dp[0], i
                for j in range(1, n + 1):
                    temp = dp[j]
                    dp[j] = min(
                        dp[j] + 1, dp[j - 1] + 1,
                        prev + (0 if a[i - 1] == b[j - 1] else 1),
                    )
                    prev = temp
            return dp[n]

        close_matches = [
            c for c in available if _edit_dist(name.lower(), c.lower()) <= 2
        ]
        if len(close_matches) == 1:
            logger.info("Fuzzy tool name match: '%s' -> '%s' (edit dist)", name, close_matches[0])
            return close_matches[0]

        return None

    @staticmethod
    def _build_malformed_feedback(
        response_text: str | None,
        tools: list[dict[str, Any]],
        malformed_retries: int,
    ) -> str:
        """Build detailed feedback for malformed tool call retries."""
        parts = ["Your previous function call was malformed."]

        # Show the model its broken output (truncated)
        if response_text:
            preview = response_text[:500]
            if len(response_text) > 500:
                preview += "..."
            parts.append(f"\nYour output was:\n```\n{preview}\n```")

        # Show available tools with schemas
        tool_hints = []
        best_match_schema = None
        for t in tools:
            schema = t.get("input_schema") or t.get("parameters") or {}
            required = schema.get("required", [])
            props = schema.get("properties", {})
            prop_details = []
            for pname, pdef in props.items():
                ptype = pdef.get("type", "any")
                marker = " (required)" if pname in required else ""
                prop_details.append(f"{pname}: {ptype}{marker}")
            tool_hints.append(
                f"  - {t['name']}({', '.join(prop_details)})"
            )
            # Fuzzy match: pick tool most likely intended
            if response_text and t["name"].lower() in (response_text or "").lower():
                best_match_schema = (t["name"], schema)

        parts.append("\nAvailable tools:\n" + "\n".join(tool_hints))

        # On 2nd+ retry, show example
        if malformed_retries >= 2 and best_match_schema:
            name, schema = best_match_schema
            example_args = {}
            for pname, pdef in schema.get("properties", {}).items():
                ptype = pdef.get("type", "string")
                if ptype == "string":
                    example_args[pname] = f"<{pname}>"
                elif ptype in ("integer", "number"):
                    example_args[pname] = 0
                elif ptype == "boolean":
                    example_args[pname] = True
                elif ptype == "object":
                    example_args[pname] = {}
                elif ptype == "array":
                    example_args[pname] = []
            parts.append(
                f"\nExample correct call for {name}:\n"
                f"```json\n{json.dumps(example_args, indent=2)}\n```"
            )

        parts.append(
            "\nDo not wrap arguments in markdown code fences. "
            "Call one of the tools above with correct parameter names and types."
        )

        return "\n".join(parts)

    @staticmethod
    def _check_empty_ids(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
        """Check if any ID-like string parameter was passed as empty string.

        Returns an error message if found, None otherwise.
        """
        properties = schema.get("properties", {})
        for param_name, param_def in properties.items():
            if param_name not in args:
                continue
            param_type = (param_def.get("type") or "").lower()
            if param_type != "string":
                continue
            desc = (param_def.get("description") or "").lower()
            name_lower = param_name.lower()
            is_id_like = any(
                kw in name_lower or kw in desc
                for kw in ("id", "identifier", "uuid", "_id")
            )
            if is_id_like and args[param_name] == "":
                return (
                    f"Field '{param_name}' appears to be an identifier but was "
                    f"passed as empty string. Use a value returned by a previous "
                    f"tool call, not a placeholder."
                )
        return None

    @staticmethod
    def _validate_against_schema(
        data: Any, schema: dict[str, Any], path: str = ""
    ) -> list[str]:
        """Validate data against a JSON schema. Supports nested objects, arrays, enums."""
        errors: list[str] = []
        if not schema:
            return errors

        type_map = {
            "object": dict,
            "array": list,
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
        }

        prefix = f"'{path}': " if path else ""

        # Type check
        expected_type = schema.get("type")
        if expected_type and expected_type in type_map:
            py_type = type_map[expected_type]
            if not isinstance(data, py_type):
                # Allow int for number type
                if expected_type == "number" and isinstance(data, int):
                    pass
                else:
                    errors.append(f"{prefix}expected {expected_type}, got {type(data).__name__}")
                    return errors

        # Enum check
        if "enum" in schema and data not in schema["enum"]:
            errors.append(f"{prefix}value {data!r} not in enum {schema['enum']}")

        # Object validation
        if isinstance(data, dict):
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            for req in required:
                if req not in data:
                    errors.append(f"{prefix}missing required field '{req}'")
            for key, value in data.items():
                if key in properties:
                    child_path = f"{path}.{key}" if path else key
                    errors.extend(
                        AIAgentNode._validate_against_schema(value, properties[key], child_path)
                    )

        # Array validation
        if isinstance(data, list):
            items_schema = schema.get("items")
            if items_schema:
                for i, item in enumerate(data):
                    child_path = f"{path}[{i}]" if path else f"[{i}]"
                    errors.extend(
                        AIAgentNode._validate_against_schema(item, items_schema, child_path)
                    )
            min_items = schema.get("minItems")
            max_items = schema.get("maxItems")
            if min_items is not None and len(data) < min_items:
                errors.append(f"{prefix}array has {len(data)} items, minimum is {min_items}")
            if max_items is not None and len(data) > max_items:
                errors.append(f"{prefix}array has {len(data)} items, maximum is {max_items}")

        return errors

    async def _run_one_tool(
        self,
        tc: Any,
        tool_schema_map: dict[str, Any],
        tool_executors: dict[str, Any],
        context: ExecutionContext,
        agent_context: AgentContext | None,
        node_name: str,
        max_context_tokens: int,
    ) -> tuple[Any, Any, str, bool]:
        """Validate and execute a single tool call, returning (tc, result, result_str, is_error)."""
        # Programmatic Tool Calling: run_script is handled here because it
        # needs access to tool_schema_map and tool_executors
        if tc.name == "run_script":
            script = (tc.args or {}).get("script", "")
            if not script:
                tool_result = {"error": "Script is required for run_script."}
                return tc, tool_result, json.dumps(tool_result), True
            tool_result = await self._execute_ptc_script(
                script, tool_executors, tool_schema_map, context,
                agent_context, node_name, max_context_tokens,
            )
            result_str = json.dumps(tool_result, default=str)
            result_str = self._compress_tool_result(result_str, "run_script")
            is_error = isinstance(tool_result, dict) and "error" in tool_result
            return tc, tool_result, result_str, is_error

        # Guard: if raw_args is set, repair failed — skip execution
        if getattr(tc, "raw_args", None) is not None:
            tool_result = {
                "error": (
                    f"Could not parse arguments for '{tc.name}'. "
                    f"Raw args: {tc.raw_args[:500]}"
                ),
            }
            return tc, tool_result, json.dumps(tool_result), True

        # Fuzzy tool name recovery
        tool_name = tc.name
        schema = tool_schema_map.get(tool_name)
        if schema is None:
            recovered = self._fuzzy_match_tool_name(
                tool_name, set(tool_schema_map.keys()),
            )
            if recovered:
                tc.name = recovered
                tool_name = recovered
                schema = tool_schema_map.get(tool_name, {})
            else:
                schema = {}

        # Coerce arguments to match schema types
        if schema:
            tc.args = self._coerce_args(tc.args or {}, schema)

            empty_id_err = self._check_empty_ids(tc.args, schema)
            if empty_id_err:
                tool_result = {"error": empty_id_err}
                return tc, tool_result, json.dumps(tool_result), True

            arg_errors = self._validate_tool_args(tc.args, schema)
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
        result_str = self._compress_tool_result(result_str, tool_name)
        is_error = isinstance(tool_result, dict) and "error" in tool_result
        return tc, tool_result, result_str, is_error

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
        _sd = "  " * (agent_context.agent_depth if agent_context else 0)
        if name == "memory_store" and agent_context is not None:
            key = input_data.get("key", "")
            value = input_data.get("value")
            if not key:
                logger.warning("[scratchpad] memory_store called with empty key | node=%s", node_name)
                return {"error": "Key is required for memory_store."}

            # shared:<key> writes to the shared store (visible to all agents in tree)
            if key.startswith("shared:"):
                shared_key = key[7:]  # strip "shared:" prefix
                if not shared_key:
                    return {"error": "Shared key name cannot be empty (use 'shared:<name>')."}
                agent_context.shared_store[shared_key] = value
                _val_preview = str(value)[:200] + ("..." if len(str(value)) > 200 else "")
                print(f"{_sd}  SHARED STORE: [{shared_key}] = {_val_preview}")
                logger.debug("[shared_store] stored key=%s | depth=%d total_keys=%d",
                             shared_key, agent_context.agent_depth, len(agent_context.shared_store))
                return {"stored": f"shared:{shared_key}", "shared_keys": list(agent_context.shared_store.keys())}

            agent_context.scratchpad[key] = value
            _val_preview = str(value)[:200] + ("..." if len(str(value)) > 200 else "")
            print(f"{_sd}  SCRATCHPAD STORE: [{key}] = {_val_preview}")
            logger.debug("[scratchpad] stored key=%s | depth=%d total_keys=%d",
                         key, agent_context.agent_depth, len(agent_context.scratchpad))
            return {"stored": key, "keys": list(agent_context.scratchpad.keys())}

        if name == "memory_recall" and agent_context is not None:
            key = input_data.get("key")
            result: dict[str, Any] = {}
            if key:
                # shared:<key> reads from the shared store
                if key.startswith("shared:"):
                    shared_key = key[7:]
                    if not shared_key:
                        # List all shared keys
                        result["shared_keys"] = list(agent_context.shared_store.keys())
                        print(f"{_sd}  SHARED RECALL ALL: keys={result['shared_keys']}")
                    elif shared_key in agent_context.shared_store:
                        result["value"] = agent_context.shared_store[shared_key]
                        _val_preview = str(result["value"])[:200] + ("..." if len(str(result["value"])) > 200 else "")
                        print(f"{_sd}  SHARED RECALL: [{shared_key}] => {_val_preview}")
                        logger.debug("[shared_store] recalled key=%s | depth=%d", shared_key, agent_context.agent_depth)
                    else:
                        result["value"] = None
                        result["error"] = f"Key '{shared_key}' not found in shared store"
                        result["shared_keys"] = list(agent_context.shared_store.keys())
                        print(f"{_sd}  SHARED RECALL: [{shared_key}] => NOT FOUND (available: {result['shared_keys']})")
                    return result

                # Check scratchpad first, then context_store, then shared_store
                if key in agent_context.scratchpad:
                    result["value"] = agent_context.scratchpad[key]
                    _val_preview = str(result["value"])[:200] + ("..." if len(str(result["value"])) > 200 else "")
                    print(f"{_sd}  SCRATCHPAD RECALL: [{key}] => {_val_preview}")
                    logger.debug("[scratchpad] recalled key=%s | depth=%d", key, agent_context.agent_depth)
                elif key in agent_context.context_store:
                    result["value"] = agent_context.context_store[key]
                    _val_preview = str(result["value"])[:200] + ("..." if len(str(result["value"])) > 200 else "")
                    print(f"{_sd}  CONTEXT_STORE RECALL: [{key}] => {_val_preview}")
                    logger.debug("[context_store] recalled key=%s | depth=%d", key, agent_context.agent_depth)
                elif key in agent_context.shared_store:
                    result["value"] = agent_context.shared_store[key]
                    _val_preview = str(result["value"])[:200] + ("..." if len(str(result["value"])) > 200 else "")
                    print(f"{_sd}  SHARED RECALL (implicit): [{key}] => {_val_preview}")
                    logger.debug("[shared_store] recalled key=%s (implicit) | depth=%d", key, agent_context.agent_depth)
                else:
                    result["value"] = None
                    all_keys = (
                        list(agent_context.scratchpad.keys())
                        + list(agent_context.context_store.keys())
                        + [f"shared:{k}" for k in agent_context.shared_store.keys()]
                    )
                    result["error"] = f"Key '{key}' not found"
                    result["available_keys"] = all_keys
                    print(f"{_sd}  RECALL: [{key}] => NOT FOUND (available: {all_keys})")
                    logger.debug("[memory] key=%s NOT FOUND | depth=%d available=%s",
                                 key, agent_context.agent_depth, all_keys)
            else:
                result["scratchpad"] = dict(agent_context.scratchpad)
                if agent_context.shared_store:
                    result["shared"] = dict(agent_context.shared_store)
                print(f"{_sd}  RECALL ALL: scratchpad={list(agent_context.scratchpad.keys())} shared={list(agent_context.shared_store.keys())}")
                logger.debug("[memory] recalled all | depth=%d scratchpad=%s shared=%s",
                             agent_context.agent_depth,
                             list(agent_context.scratchpad.keys()),
                             list(agent_context.shared_store.keys()))
            if agent_context.parent_scratchpad is not None:
                result["parent_scratchpad"] = agent_context.parent_scratchpad
                print(f"{_sd}  SCRATCHPAD (parent): keys={list(agent_context.parent_scratchpad.keys())}")
                logger.debug("[scratchpad] included parent scratchpad keys=%s",
                             list(agent_context.parent_scratchpad.keys()))
            return result

        # Skill delegation tool
        if name == "delegate_to_skill" and agent_context is not None and agent_context.skill_configs:
            return await self._handle_delegate_to_skill(
                input_data,
                agent_context.skill_configs,
                agent_context.skill_all_tools,
                agent_context.skill_all_executors,
                agent_context, context, node_name, max_context_tokens,
            )

        # Sub-agent spawn tool
        if name == "spawn_agent" and agent_context is not None:
            return await self._handle_spawn_agent(
                input_data, agent_context, context, node_name, max_context_tokens,
            )

        # Custom executor (from inline tool config)
        if name in tool_executors:
            executor = tool_executors[name]
            try:
                if inspect.iscoroutinefunction(executor):
                    coro = executor(input_data, context)
                else:
                    # Run sync executor in a thread to avoid blocking
                    coro = asyncio.to_thread(executor, input_data)
                return await asyncio.wait_for(coro, timeout=_DEFAULT_TOOL_TIMEOUT)
            except asyncio.TimeoutError:
                return {"error": f"Tool '{name}' timed out after {_DEFAULT_TOOL_TIMEOUT}s"}
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"Unknown tool: '{name}'"}
