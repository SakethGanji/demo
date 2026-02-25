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
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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
class LLMUsage:
    """Token usage from an LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Standardized response from call_llm."""

    text: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional[LLMUsage] = None
    response_time_ms: Optional[float] = None
    malformed_tool_call: bool = False

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
# Schema normalization
# ---------------------------------------------------------------------------


def _normalize_schema_types(schema: Any, to_case: str = "lower") -> Any:
    """Recursively normalize 'type' fields in a JSON schema.

    Args:
        schema: A JSON schema dict (or nested part of one).
        to_case: "lower" for OpenAI/Anthropic ("object"), "upper" for Gemini ("OBJECT").
    """
    if isinstance(schema, dict):
        result = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, str):
                result[key] = value.upper() if to_case == "upper" else value.lower()
            else:
                result[key] = _normalize_schema_types(value, to_case)
        return result
    if isinstance(schema, list):
        return [_normalize_schema_types(item, to_case) for item in schema]
    return schema


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
        logger.warning("_find_tool_name called with no tool_call_id")
        return "unknown"
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("id") == tool_call_id:
                return tc.get("function", {}).get("name", "unknown")
    logger.warning("Could not find tool name for tool_call_id=%s", tool_call_id)
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
    from google.genai.types import (
        GenerateContentConfig, FunctionDeclaration, Tool,
        AutomaticFunctionCallingConfig,
        ToolConfig, FunctionCallingConfig, FunctionCallingConfigMode,
    )
    from google.api_core import exceptions as google_exceptions

    client = _get_gemini_client()
    contents, system_instruction = _convert_messages_to_gemini_content(messages)

    # Build tools — wrap in Tool(function_declarations=...)
    gemini_tools = None
    if tools:
        declarations = []
        for t in tools:
            schema = _tool_to_schema(t)
            params = _normalize_schema_types(
                schema["parameters"] or {"type": "OBJECT", "properties": {}},
                to_case="upper",
            )
            declarations.append(FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters=params,
            ))
        gemini_tools = [Tool(function_declarations=declarations)]

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "automatic_function_calling": AutomaticFunctionCallingConfig(disable=True),
    }
    if gemini_tools:
        config_kwargs["tools"] = gemini_tools
        # Map tool_choice kwarg to Gemini's function_calling_mode
        _tc = kwargs.get("tool_choice", "auto")
        _mode_map = {
            "auto": FunctionCallingConfigMode.AUTO,
            "required": FunctionCallingConfigMode.ANY,
            "none": FunctionCallingConfigMode.NONE,
        }
        fc_mode = _mode_map.get(_tc, FunctionCallingConfigMode.AUTO)
        config_kwargs["tool_config"] = ToolConfig(
            function_calling_config=FunctionCallingConfig(mode=fc_mode),
        )
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

    from time import perf_counter as _pc
    _t0 = _pc()
    try:
        response = await client.aio.models.generate_content(
            model=model, contents=contents, config=config,
        )
    except (google_exceptions.Unauthenticated, google_exceptions.PermissionDenied):
        fresh_client = _reset_gemini_client()
        response = await fresh_client.aio.models.generate_content(
            model=model, contents=contents, config=config,
        )
    _elapsed = round((_pc() - _t0) * 1000, 2)

    result = _parse_gemini_response(response)
    result.response_time_ms = _elapsed

    # Extract usage metadata
    usage_meta = getattr(response, "usage_metadata", None)
    if usage_meta:
        result.usage = LLMUsage(
            input_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
            total_tokens=getattr(usage_meta, "total_token_count", 0) or 0,
        )

    return result


def _parse_gemini_response(response: Any) -> LLMResponse:
    if not response.candidates:
        return LLMResponse(text="[Model returned no candidates]")

    candidate = response.candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    finish_name = getattr(finish, "name", None)

    # Gemini returns MALFORMED_FUNCTION_CALL when function call output is
    # garbled — signal to the agent loop so it can retry the turn.
    # UNEXPECTED_TOOL_CALL occurs with thinking models (2.5 Flash) when the
    # model attempts a tool call during its reasoning phase.
    if finish_name in ("MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL"):
        text = None
        # Still try to extract function calls — they may be valid
        parts = (candidate.content.parts if candidate.content else None) or []
        tool_calls = []
        text_parts = []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc:
                tool_calls.append(ToolCall(
                    id=str(uuid.uuid4()),
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                ))
            elif getattr(part, "text", None):
                text_parts.append(part.text)
        if tool_calls:
            logger.info("Recovered %d tool call(s) from %s response", len(tool_calls), finish_name)
            return LLMResponse(
                text="\n".join(text_parts) if text_parts else None,
                tool_calls=tool_calls,
            )
        # No recoverable tool calls — fall back to malformed signal
        try:
            text = response.text
        except (ValueError, AttributeError) as exc:
            logger.debug("Could not extract text from %s response: %s", finish_name, exc)
        logger.warning("Gemini returned %s (no recoverable tool calls)", finish_name)
        return LLMResponse(text=text or "", malformed_tool_call=True)

    parts = (candidate.content.parts if candidate.content else None) or []
    has_fc = any(getattr(p, "function_call", None) for p in parts)

    if has_fc:
        tool_calls = []
        text_parts = []
        for part in parts:
            fc = getattr(part, "function_call", None)
            if fc:
                tool_calls.append(ToolCall(
                    id=str(uuid.uuid4()),
                    name=fc.name,
                    args=dict(fc.args) if fc.args else {},
                ))
            elif getattr(part, "text", None):
                text_parts.append(part.text)
        return LLMResponse(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
        )

    text = None
    try:
        text = response.text
    except (ValueError, AttributeError) as exc:
        logger.debug("Could not extract text from Gemini response (finish_reason=%s): %s", finish_name, exc)

    if not text:
        text = "[Model returned an empty response]"
        if finish_name and finish_name != "STOP":
            text += f" (finish_reason={finish_name})"

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
            params = _normalize_schema_types(
                schema["parameters"] or {"type": "object", "properties": {}},
                to_case="lower",
            )
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
        completion_kwargs["parallel_tool_calls"] = True
    if kwargs.get("max_tokens"):
        completion_kwargs["max_tokens"] = kwargs["max_tokens"]
    if kwargs.get("user"):
        completion_kwargs["user"] = kwargs["user"]

    rf = kwargs.get("response_format")
    if rf and rf.get("type") == "json_object":
        if "schema" in rf:
            completion_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": rf.get("name", "response"),
                    "schema": rf["schema"],
                },
            }
        else:
            completion_kwargs["response_format"] = {"type": "json_object"}

    from time import perf_counter as _pc
    _t0 = _pc()
    completion = await client.chat.completions.create(**completion_kwargs)
    _elapsed = round((_pc() - _t0) * 1000, 2)

    resp = LLMResponse()
    resp.response_time_ms = _elapsed

    # Extract usage
    if completion.usage:
        resp.usage = LLMUsage(
            input_tokens=completion.usage.prompt_tokens or 0,
            output_tokens=completion.usage.completion_tokens or 0,
            total_tokens=completion.usage.total_tokens or 0,
        )

    choice = completion.choices[0] if completion.choices else None
    if not choice:
        return resp

    msg = choice.message
    resp.text = msg.content or None
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Malformed tool call arguments from OpenAI for %s: %s",
                    tc.function.name, tc.function.arguments[:200] if tc.function.arguments else "",
                )
                args = {}
                resp.malformed_tool_call = True
            resp.tool_calls.append(ToolCall(
                id=tc.id, name=tc.function.name, args=args,
            ))

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
            params = _normalize_schema_types(
                schema["parameters"] or {"type": "object", "properties": {}},
                to_case="lower",
            )
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

    from time import perf_counter as _pc
    _t0 = _pc()
    response = await client.messages.create(**call_kwargs)
    _elapsed = round((_pc() - _t0) * 1000, 2)

    resp = LLMResponse()
    resp.response_time_ms = _elapsed

    # Extract usage
    if response.usage:
        resp.usage = LLMUsage(
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            total_tokens=(response.usage.input_tokens or 0) + (response.usage.output_tokens or 0),
        )

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


_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    # OpenAI / Anthropic SDK errors
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (429, 500, 502, 503, 529):
        return True
    # google-genai wraps HTTP errors in google.api_core.exceptions
    cls_name = type(exc).__name__
    if cls_name in ("TooManyRequests", "ServiceUnavailable", "InternalServerError", "ResourceExhausted"):
        return True
    # Generic connection errors
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    return False


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
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await _call_llm_once(model, messages, temperature, tools=tools, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, _MAX_RETRIES, delay, exc,
            )
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, but keeps type checkers happy


async def _call_llm_once(
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    tools: Optional[list] = None,
    **kwargs: Any,
) -> LLMResponse:
    """Single attempt to call an LLM backend."""
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
