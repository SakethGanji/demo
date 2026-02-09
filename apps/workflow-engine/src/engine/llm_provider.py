"""Unified LLM provider using direct SDKs (google-genai, openai, anthropic).

Public API:
    call_llm(model, messages, temperature, tools, **kwargs) -> LLMResponse

Routing:
  - gemini-*           -> google.genai  (Vertex AI or API key)
  - meta/llama-*       -> openai SDK    (company OpenAI-compatible proxy)
  - gpt-* / o1-* / o3  -> openai SDK    (direct OpenAI API)
  - claude-*           -> anthropic SDK  (direct Anthropic API)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Represents a tool call requested by the LLM."""

    id: str
    name: str
    args: Dict[str, Any]


@dataclass
class LLMResponse:
    """Standardized response from call_llm."""

    text: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)

    def get_assistant_message(self) -> Dict:
        """Return the model's response as an OpenAI-format message dict."""
        if self.tool_calls:
            return {
                "role": "assistant",
                "content": self.text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.args),
                        },
                    }
                    for tc in self.tool_calls
                ],
            }
        return {"role": "assistant", "content": self.text}


# ---------------------------------------------------------------------------
# Model & Configuration Registry
# ---------------------------------------------------------------------------

GEMINI_MODELS: set[str] = {
    "gemini-2.5-pro", "gemini-2.5-flash",
    "gemini-2.0-flash", "gemini-2.0-flash-001",
    "gemini-1.5-flash", "gemini-1.5-flash-latest",
    "gemini-1.5-pro", "gemini-1.5-pro-latest",
    "gemini-1.0-pro",
}

LLAMA_MODELS: dict[str, str] = {
    "meta/llama-4-maverick-17b-128e-instruct-maas": "us-east5",
    "meta/llama-4-scout-17b-16e-instruct-maas": "us-east5",
    "meta/llama-3.3-70b-instruct-maas": "us-central1",
    "meta/llama-3.1-405b-instruct-maas": "us-central1",
}

_DEFAULT_PROXY_TEMPLATE = (
    "https://r2d2-c3p0-icg-msst-genaihub-178909.apps.namicg39023u"
    ".ecs.dyn.nsroot.net/vertex/v1beta1/projects/{project}"
    "/locations/{region}/endpoints/openapi"
)


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------


def _get_env(key: str) -> str | None:
    """Read a config value, checking WORKFLOW_ prefix first, then raw.

    Falls back to pydantic-settings if available (loads from .env file).
    """
    val = os.environ.get(f"WORKFLOW_{key}") or os.environ.get(key)
    if val:
        return val
    # Fallback: try loading from pydantic-settings (reads .env)
    try:
        from ..core.config import settings
        attr = key.lower()  # e.g. GEMINI_API_KEY -> gemini_api_key
        return getattr(settings, attr, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lazy client singletons
# ---------------------------------------------------------------------------

_clients: dict[str, Any] = {}


def _get_gemini_client() -> Any:
    if "gemini" not in _clients:
        from google import genai

        api_key = _get_env("GEMINI_API_KEY")
        if api_key:
            _clients["gemini"] = genai.Client(api_key=api_key)
        else:
            project = os.environ.get(
                "GOOGLE_CLOUD_PROJECT",
                _get_env("LLM_PROXY_PROJECT") or "",
            )
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
            _clients["gemini"] = genai.Client(
                vertexai=True, project=project, location=location,
            )
    return _clients["gemini"]


def _reset_gemini_client() -> Any:
    _clients.pop("gemini", None)
    return _get_gemini_client()


def _get_openai_client() -> Any:
    if "openai" not in _clients:
        from openai import AsyncOpenAI

        _clients["openai"] = AsyncOpenAI(api_key=_get_env("OPENAI_API_KEY"))
    return _clients["openai"]


def _get_anthropic_client() -> Any:
    if "anthropic" not in _clients:
        from anthropic import AsyncAnthropic

        _clients["anthropic"] = AsyncAnthropic(api_key=_get_env("ANTHROPIC_API_KEY"))
    return _clients["anthropic"]


def _get_llama_client(model: str) -> Any:
    key = f"llama:{model}"
    if key not in _clients:
        from openai import AsyncOpenAI

        region = LLAMA_MODELS[model]
        base_url = _get_env("LLM_PROXY_BASE_URL") or _DEFAULT_PROXY_TEMPLATE.format(
            project=_get_env("LLM_PROXY_PROJECT") or "",
            region=region,
        )
        token = os.environ.get("COIN_TOKEN", "")
        _clients[key] = AsyncOpenAI(api_key=token, base_url=base_url)
    return _clients[key]


# ---------------------------------------------------------------------------
# Tool schema extraction (supports both callables and dicts)
# ---------------------------------------------------------------------------


def _tool_to_schema(tool: Any) -> dict[str, Any]:
    """Convert a tool (callable or dict) to {name, description, parameters}."""
    if isinstance(tool, dict):
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": (
                tool.get("parameters")
                or tool.get("input_schema")
                or {}
            ),
        }
    if callable(tool):
        return _function_to_schema(tool)
    raise TypeError(f"Tool must be a callable or dict, got {type(tool)}")


def _function_to_schema(func: Callable) -> dict[str, Any]:
    """Extract tool schema from a function's docstring and type hints."""
    try:
        from docstring_parser import parse as parse_docstring
    except ImportError:
        # Fallback when docstring_parser is not installed
        return {
            "name": func.__name__,
            "description": (func.__doc__ or "").strip().split("\n")[0],
            "parameters": {"type": "OBJECT", "properties": {}},
        }

    docstring = parse_docstring(func.__doc__ or "")
    type_map = {
        "str": "STRING", "int": "INTEGER",
        "float": "NUMBER", "bool": "BOOLEAN",
    }

    properties: dict[str, Any] = {}
    required: list[str] = []

    sig = inspect.signature(func)
    for param_name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    for param_info in docstring.params:
        param_name = param_info.arg_name
        param_type = "STRING"
        if param_name in func.__annotations__:
            type_name = (
                str(func.__annotations__[param_name])
                .split("[")[0].split(".")[0].split("|")[-1].lower()
            )
            param_type = type_map.get(type_name, "STRING")
        properties[param_name] = {
            "type": param_type,
            "description": param_info.description or "",
        }

    return {
        "name": func.__name__,
        "description": docstring.short_description or "",
        "parameters": {
            "type": "OBJECT",
            "properties": properties,
            "required": required,
        },
    }


# ---------------------------------------------------------------------------
# Message conversion: dict messages -> Gemini Content objects
# ---------------------------------------------------------------------------


def _convert_messages_to_gemini_content(
    messages: list[dict],
) -> tuple[list[Any], str | None]:
    from google.genai.types import Content, Part

    system_instruction: str | None = None
    contents: list[Content] = []

    for msg in messages:
        role = msg["role"]
        content = msg.get("content") or ""

        if role == "system":
            system_instruction = content
            continue

        if role == "user":
            contents.append(
                Content(role="user", parts=[Part(text=content)])
            )

        elif role == "assistant":
            if msg.get("tool_calls"):
                parts = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args = (
                            json.loads(args_raw)
                            if isinstance(args_raw, str)
                            else args_raw
                        )
                    except json.JSONDecodeError:
                        args = {}
                    parts.append(
                        Part.from_function_call(name=fn.get("name"), args=args)
                    )
                contents.append(Content(role="model", parts=parts))
            elif content:
                contents.append(
                    Content(role="model", parts=[Part(text=content)])
                )

        elif role == "tool":
            try:
                resp = json.loads(msg["content"]) if isinstance(msg.get("content"), str) else msg.get("content", {})
                if not isinstance(resp, dict):
                    resp = {"content": resp}
            except (json.JSONDecodeError, TypeError):
                resp = {"content": str(msg.get("content", ""))}

            name = msg.get("name") or _find_tool_name(
                messages, msg.get("tool_call_id"),
            )
            contents.append(Content(
                role="user",
                parts=[Part.from_function_response(name=name, response=resp)],
            ))

    return contents, system_instruction


def _find_tool_name(messages: list[dict], tool_call_id: str | None) -> str:
    """Look up the function name for a tool_call_id in the conversation."""
    if not tool_call_id:
        return "unknown"
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("id") == tool_call_id:
                return tc.get("function", {}).get("name", "unknown")
    return "unknown"


# ---------------------------------------------------------------------------
# Message conversion: dict messages -> Anthropic format
# ---------------------------------------------------------------------------


def _convert_messages_to_anthropic(
    messages: list[dict],
) -> tuple[list[dict], str | None]:
    system: str | None = None
    result: list[dict] = []

    for msg in messages:
        role = msg["role"]

        if role == "system":
            system = msg.get("content")
            continue

        if role == "assistant":
            if msg.get("tool_calls"):
                content: list[dict] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args_raw = fn.get("arguments", "{}")
                    try:
                        args = (
                            json.loads(args_raw)
                            if isinstance(args_raw, str)
                            else args_raw
                        )
                    except json.JSONDecodeError:
                        args = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": fn["name"],
                        "input": args,
                    })
                result.append({"role": "assistant", "content": content})
            else:
                result.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                })

        elif role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                }],
            })

        elif role == "user":
            result.append({"role": "user", "content": msg.get("content") or ""})

    return result, system


# ---------------------------------------------------------------------------
# Backend: Gemini (google.genai)
# ---------------------------------------------------------------------------


async def _call_gemini_vertex(
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    tools: Optional[list] = None,
    **kwargs: Any,
) -> LLMResponse:
    from google.genai.types import GenerateContentConfig, FunctionDeclaration, Tool
    from google.api_core import exceptions as google_exceptions

    client = _get_gemini_client()
    contents, system_instruction = _convert_messages_to_gemini_content(messages)

    # Build tools — wrap in Tool(function_declarations=...)
    gemini_tools = None
    if tools:
        declarations = []
        for t in tools:
            schema = _tool_to_schema(t)
            declarations.append(FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"] or {"type": "OBJECT", "properties": {}},
            ))
        gemini_tools = [Tool(function_declarations=declarations)]

    config_kwargs: dict[str, Any] = {"temperature": temperature}
    if gemini_tools:
        config_kwargs["tools"] = gemini_tools
    if system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if kwargs.get("max_tokens"):
        config_kwargs["max_output_tokens"] = kwargs["max_tokens"]

    rf = kwargs.get("response_format")
    if rf and rf.get("type") == "json_object":
        config_kwargs["response_mime_type"] = "application/json"
        if "schema" in rf:
            config_kwargs["response_schema"] = rf["schema"]

    config = GenerateContentConfig(**config_kwargs)

    def do_sync_call():
        return client.models.generate_content(
            model=model, contents=contents, config=config,
        )

    try:
        response = await asyncio.to_thread(do_sync_call)
    except (google_exceptions.Unauthenticated, google_exceptions.PermissionDenied):
        fresh_client = _reset_gemini_client()

        def do_retry():
            return fresh_client.models.generate_content(
                model=model, contents=contents, config=config,
            )

        response = await asyncio.to_thread(do_retry)

    return _parse_gemini_response(response)


def _parse_gemini_response(response: Any) -> LLMResponse:
    if not response.candidates:
        return LLMResponse(text="[Model returned no candidates]")

    candidate = response.candidates[0]
    parts = candidate.content.parts if candidate.content else []
    has_fc = any(getattr(p, "function_call", None) for p in parts)

    if has_fc:
        tool_calls = []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc:
                tool_calls.append(ToolCall(
                    id=str(uuid.uuid4()),
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                ))
        return LLMResponse(tool_calls=tool_calls)

    text = None
    try:
        text = response.text
    except (ValueError, AttributeError):
        pass

    if not text:
        finish = getattr(candidate, "finish_reason", None)
        text = "[Model returned an empty response]"
        if finish and getattr(finish, "name", None) != "STOP":
            text += f" Finish Reason: {finish.name}"

    return LLMResponse(text=text)


# ---------------------------------------------------------------------------
# Backend: OpenAI-compatible (OpenAI direct + Llama proxy)
# ---------------------------------------------------------------------------


async def _call_openai_compat(
    client: Any,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    tools: Optional[list] = None,
    **kwargs: Any,
) -> LLMResponse:
    api_tools = None
    if tools:
        api_tools = []
        for t in tools:
            schema = _tool_to_schema(t)
            params = schema["parameters"] or {"type": "object", "properties": {}}
            # Normalize type casing for OpenAI (OBJECT -> object)
            if isinstance(params.get("type"), str):
                params["type"] = params["type"].lower()
            api_tools.append({
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": params,
                },
            })

    completion_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if api_tools:
        completion_kwargs["tools"] = api_tools
        completion_kwargs["tool_choice"] = "auto"
    if kwargs.get("max_tokens"):
        completion_kwargs["max_tokens"] = kwargs["max_tokens"]
    if kwargs.get("user"):
        completion_kwargs["user"] = kwargs["user"]

    rf = kwargs.get("response_format")
    if rf and rf.get("type") == "json_object":
        completion_kwargs["response_format"] = {"type": "json_object"}

    completion = await client.chat.completions.create(**completion_kwargs)

    resp = LLMResponse()
    choice = completion.choices[0] if completion.choices else None
    if not choice:
        return resp

    msg = choice.message
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            resp.tool_calls.append(ToolCall(
                id=tc.id, name=tc.function.name, args=args,
            ))
    else:
        resp.text = msg.content

    return resp


# ---------------------------------------------------------------------------
# Backend: Anthropic
# ---------------------------------------------------------------------------


async def _call_anthropic(
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    tools: Optional[list] = None,
    **kwargs: Any,
) -> LLMResponse:
    client = _get_anthropic_client()
    api_messages, system_prompt = _convert_messages_to_anthropic(messages)

    api_tools = None
    if tools:
        api_tools = []
        for t in tools:
            schema = _tool_to_schema(t)
            params = schema["parameters"] or {"type": "object", "properties": {}}
            if isinstance(params.get("type"), str):
                params["type"] = params["type"].lower()
            api_tools.append({
                "name": schema["name"],
                "description": schema["description"],
                "input_schema": params,
            })

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "max_tokens": kwargs.get("max_tokens") or 4096,
    }
    if system_prompt:
        call_kwargs["system"] = system_prompt
    if api_tools:
        call_kwargs["tools"] = api_tools
    if temperature is not None:
        call_kwargs["temperature"] = temperature

    response = await client.messages.create(**call_kwargs)

    resp = LLMResponse()
    text_parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            resp.tool_calls.append(ToolCall(
                id=block.id,
                name=block.name,
                args=block.input if isinstance(block.input, dict) else {},
            ))
    if text_parts:
        resp.text = "\n".join(text_parts)

    return resp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_embedding(
    text: str,
    provider: str = "openai",
    model: str = "text-embedding-3-small",
) -> list[float]:
    """Get embedding vector for text using the configured provider.

    Args:
        text: Text to embed.
        provider: "openai" or "gemini".
        model: Embedding model ID (e.g. "text-embedding-3-small", "text-embedding-004").

    Returns:
        Embedding vector as list of floats.
    """
    if provider == "openai":
        client = _get_openai_client()
        response = await client.embeddings.create(model=model, input=text)
        return response.data[0].embedding

    if provider == "gemini":
        client = _get_gemini_client()

        def do_embed():
            return client.models.embed_content(model=model, contents=text)

        response = await asyncio.to_thread(do_embed)
        return response.embeddings[0].values

    raise ValueError(f"Unknown embedding provider: {provider}")


async def call_llm(
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    tools: Optional[list] = None,
    **kwargs: Any,
) -> LLMResponse:
    """Unified function to call an LLM with optional tool calling.

    Args:
        model: Model identifier (e.g. "gemini-2.0-flash", "gpt-4o").
        messages: Conversation as OpenAI-format dicts.
        temperature: Sampling temperature.
        tools: List of tools — each can be a Python callable (schema
               extracted from docstring) or a dict with name/description/parameters.
        **kwargs: Extra options forwarded to the backend:
            max_tokens (int), response_format (dict), user (str).

    Returns:
        LLMResponse with .text and/or .tool_calls populated.
    """
    if model in GEMINI_MODELS or model.startswith("gemini-"):
        return await _call_gemini_vertex(
            model, messages, temperature, tools=tools, **kwargs,
        )

    if model in LLAMA_MODELS:
        return await _call_openai_compat(
            _get_llama_client(model), model, messages, temperature,
            tools=tools, **kwargs,
        )

    if model.startswith("claude-"):
        return await _call_anthropic(
            model, messages, temperature, tools=tools, **kwargs,
        )

    # Default: OpenAI (gpt-*, o1-*, o3-*, etc.)
    return await _call_openai_compat(
        _get_openai_client(), model, messages, temperature,
        tools=tools, **kwargs,
    )
