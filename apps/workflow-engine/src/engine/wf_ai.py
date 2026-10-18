"""The only path to the model for Stage 6 (DESIGN-STAGE6-AI-BUILDER §6.8, §6.9, §6.12, §11.5).

``ask(task, system, user, schema, *, meter) -> dict`` does, in order: system-line guard, policy + configured check,
call budget / deadline / prompt-size limits, cache lookup (spec and node_explain only), one call through a swappable
``ModelAdapter`` (both adapters go through ``llm_provider.call_llm``, so the fake LLM hook, retries and routing stay in
one place), one ``WF_AI:JSON_FIX`` retry when the reply is not usable JSON, metering, and the cassette recorder.

Errors are ``AIError(code, message)``; codes: ``not_configured``, ``model_not_allowed``, ``call_budget``, ``deadline``,
``prompt_too_large``, ``timeout``, ``provider_failed``, ``bad_json``, ``record_aborted``. The caller maps them to HTTP
(``bad_json`` / ``provider_failed`` / ``timeout`` / ``call_budget`` -> 502 ``ai_failed``).
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from . import ai_policy, llm_provider
from .llm_provider import LLMUsage
from .test_ai import TestAIError, parse_json_object

logger = logging.getLogger(__name__)

SYSTEM_LINES: dict[str, str] = {
    "spec": "WF_AI:SPEC",
    "spec_repair": "WF_AI:SPEC_REPAIR",
    "build": "WF_AI:BUILD",
    "repair": "WF_AI:REPAIR",
    "node_fix": "WF_AI:NODE_FIX",
    "node_code": "WF_AI:NODE_CODE",
    "node_expr": "WF_AI:NODE_EXPR",
    "node_explain": "WF_AI:NODE_EXPLAIN",
    "chat_route": "WF_AI:CHAT_ROUTE",
    "json_fix": "WF_AI:JSON_FIX",
}
CACHEABLE_TASKS = frozenset({"spec", "node_explain"})
CACHE_SIZE = 256
JSON_FIX_HEAD_CHARS = 4000

JSON_FIX_SCHEMA_NOTE = (
    "The previous reply could not be used. The user message is JSON: `error` says what was wrong, `reply_head` is the first "
    "4000 characters of the previous reply, `schema` is the JSON schema the answer must follow. Answer again with ONLY one JSON "
    "object that follows `schema`, keeping the content of the previous reply wherever it was valid. No prose, no markdown fences."
)


class AIError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── config (§6.8) ───────────────────────────────────────────────────────────────────────────


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AIConfig:
    model: str
    adapter: str
    max_repair_rounds: int
    max_calls: int
    call_timeout_s: float
    build_deadline_s: float
    max_output_tokens: int
    max_prompt_chars: int
    max_ops: int
    max_nodes: int
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None
    thinking_level: str
    temperature: float
    store_prompts: bool
    record_dir: str | None

    def budgets(self) -> dict[str, Any]:
        return {"max_repair_rounds": self.max_repair_rounds, "max_calls": self.max_calls, "call_timeout_s": self.call_timeout_s,
                "build_deadline_s": self.build_deadline_s, "max_output_tokens": self.max_output_tokens,
                "max_prompt_chars": self.max_prompt_chars, "max_ops": self.max_ops, "max_nodes": self.max_nodes}


def config() -> AIConfig:
    """Read the env on every call, so tests and the lead can change it without a restart."""
    e = os.environ
    return AIConfig(
        model=(e.get("WF_AI_MODEL") or e.get("WORKFLOW_MODULE_GEN_MODEL") or "gemini-3.5-flash").strip(),
        adapter=(e.get("WF_AI_ADAPTER") or "json_schema").strip(),
        max_repair_rounds=_int("WF_AI_MAX_REPAIR_ROUNDS", 3),
        max_calls=_int("WF_AI_MAX_CALLS", 8),
        call_timeout_s=_float("WF_AI_CALL_TIMEOUT_S", 60.0) or 60.0,
        build_deadline_s=_float("WF_AI_BUILD_DEADLINE_S", 150.0) or 150.0,
        max_output_tokens=_int("WF_AI_MAX_OUTPUT_TOKENS", 16000),
        max_prompt_chars=_int("WF_AI_MAX_PROMPT_CHARS", 160000),
        max_ops=_int("WF_AI_MAX_OPS", 60),
        max_nodes=_int("WF_AI_MAX_NODES", 40),
        price_in_per_mtok=_float("WF_AI_PRICE_IN_PER_MTOK", None),
        price_out_per_mtok=_float("WF_AI_PRICE_OUT_PER_MTOK", None),
        thinking_level=(e.get("WF_AI_THINKING_LEVEL") or "low").strip(),
        temperature=_float("WF_AI_TEMPERATURE", 0.2) if _float("WF_AI_TEMPERATURE", 0.2) is not None else 0.2,
        store_prompts=e.get("WF_AI_STORE_PROMPTS", "0").strip().lower() in ("1", "true", "yes"),
        record_dir=(e.get("WF_AI_RECORD_DIR") or "").strip() or None,
    )


# ── meter ───────────────────────────────────────────────────────────────────────────────────


@dataclass
class Meter:
    """Counts one proposal's model use. ``events`` gets one ``llm_call`` payload (§2.2) per call, cached hits included."""

    max_calls: int | None = None            # default: config().max_calls
    deadline_s: float | None = None         # whole-build wall budget; None = no deadline
    case: str | None = None                 # recorder file name (default: WF_AI_RECORD_CASE or "uncased")
    secret_values: set[str] = field(default_factory=set)   # known secrets: the recorder aborts if a reply holds one
    on_event: Callable[[dict[str, Any]], None] | None = None
    calls: int = 0                          # counted calls (cache hits excluded)
    cached_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    llm_ms: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    prompts_version: str | None = None      # recorder; default: ai_builder_prompts.prompts_version()

    def budget(self) -> int:
        return self.max_calls if self.max_calls is not None else config().max_calls

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def remaining_s(self) -> float | None:
        return None if self.deadline_s is None else self.deadline_s - (time.monotonic() - self.started_at)

    def add_usage(self, usage: LLMUsage | None) -> tuple[int, int]:
        i = int(getattr(usage, "input_tokens", 0) or 0)
        o = int(getattr(usage, "output_tokens", 0) or 0)
        self.input_tokens += i
        self.output_tokens += o
        return i, o

    def cost_usd(self) -> float | None:
        """None until the owner sets both prices (no invented prices)."""
        c = config()
        if c.price_in_per_mtok is None or c.price_out_per_mtok is None:
            return None
        return round(self.input_tokens / 1e6 * c.price_in_per_mtok + self.output_tokens / 1e6 * c.price_out_per_mtok, 5)

    def usage(self) -> dict[str, Any]:
        return {"llm_calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": self.cost_usd(), "duration_ms": self.elapsed_ms()}

    def emit(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)
        if self.on_event is not None:
            try:
                self.on_event(payload)
            except Exception:  # noqa: BLE001 - a broken listener must not break a build
                logger.warning("meter listener failed", exc_info=True)


# ── adapters (§6.12) ────────────────────────────────────────────────────────────────────────


@dataclass
class AIReply:
    text: str
    usage: LLMUsage | None
    model: str
    provider: str
    finish: str  # stop | length | tool


class ModelAdapter(Protocol):
    name: str

    async def ask_json(self, system: str, user: str, schema: dict, *, max_tokens: int, timeout_s: float) -> AIReply: ...


class _AdapterBase:
    name = ""

    def __init__(self, model: str, temperature: float = 0.2, thinking_level: str | None = "low") -> None:
        self.model, self.temperature, self.thinking_level = model, temperature, thinking_level

    def _messages(self, system: str, user: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _extra(self) -> dict[str, Any]:
        return {"thinking_level": self.thinking_level} if self.thinking_level and self.model.startswith("gemini") else {}


class JsonSchemaAdapter(_AdapterBase):
    """Constrained JSON: ``response_format={"type":"json_object","schema":...}`` (Gemini response_mime_type + response_schema)."""

    name = "json_schema"

    async def ask_json(self, system: str, user: str, schema: dict, *, max_tokens: int, timeout_s: float) -> AIReply:
        resp = await asyncio.wait_for(llm_provider.call_llm(
            self.model, self._messages(system, user), temperature=self.temperature,
            response_format={"type": "json_object", "schema": schema}, max_tokens=max_tokens, **self._extra()), timeout=timeout_s)
        truncated = bool(getattr(resp, "output_truncated", False))
        return AIReply(text=resp.text or "", usage=resp.usage, model=self.model, provider=ai_policy.provider_of(self.model),
                       finish="length" if truncated else "stop")


class FunctionCallAdapter(_AdapterBase):
    """Fallback: one ``emit_result`` tool, forced (``tool_choice="required"``); its arguments are returned as the JSON text."""

    name = "function_call"
    TOOL_NAME = "emit_result"

    async def ask_json(self, system: str, user: str, schema: dict, *, max_tokens: int, timeout_s: float) -> AIReply:
        tool = {"name": self.TOOL_NAME, "description": "Emit the complete result as the arguments of this call.", "parameters": schema}
        resp = await asyncio.wait_for(llm_provider.call_llm(
            self.model, self._messages(system, user), temperature=self.temperature, tools=[tool], tool_choice="required",
            max_tokens=max_tokens, **self._extra()), timeout=timeout_s)
        truncated = bool(getattr(resp, "output_truncated", False)) or bool(getattr(resp, "malformed_tool_call", False))
        call = next((tc for tc in (resp.tool_calls or []) if tc.name == self.TOOL_NAME), None)
        if call is not None and isinstance(call.args, dict):
            text, finish = json.dumps(call.args), "tool"
        else:
            text, finish = resp.text or "", "length" if truncated else "stop"  # some providers answer in text anyway
        return AIReply(text=text, usage=resp.usage, model=self.model, provider=ai_policy.provider_of(self.model),
                       finish="length" if truncated and call is None else finish)


ADAPTERS: dict[str, type[_AdapterBase]] = {"json_schema": JsonSchemaAdapter, "function_call": FunctionCallAdapter}


def make_adapter(model: str, cfg: AIConfig | None = None) -> ModelAdapter:
    cfg = cfg or config()
    cls = ADAPTERS.get(cfg.adapter)
    if cls is None:
        raise AIError("provider_failed", f"unknown WF_AI_ADAPTER '{cfg.adapter}' (known: {', '.join(ADAPTERS)})")
    return cls(model, cfg.temperature, cfg.thinking_level)


# ── cache (in-process LRU, spec + node_explain only) ────────────────────────────────────────

_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(key: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return copy.deepcopy(_CACHE[key])
    return None


def _cache_put(key: str, value: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = copy.deepcopy(value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


# ── helpers ─────────────────────────────────────────────────────────────────────────────────


def prompt_sha256(model: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{model}\n{system}\n{user}".encode("utf-8", errors="surrogatepass")).hexdigest()


def check_system_line(task: str, system: str) -> None:
    line = SYSTEM_LINES.get(task)
    if line is None:
        raise ValueError(f"unknown wf_ai task: {task}")
    if system != line and not system.startswith(line + "\n"):
        raise ValueError(f"the system prompt for '{task}' must start with {line}")


def _missing_required(obj: dict[str, Any], schema: dict) -> list[str]:
    return [k for k in (schema.get("required") or []) if k not in obj]


def _parse(reply: AIReply, schema: dict) -> dict[str, Any]:
    """The reply as a dict that has the schema's required top-level keys. Raises ``TestAIError(bad_json)``."""
    if reply.finish == "length":
        raise TestAIError("bad_json", "The model's reply was cut off")
    obj = parse_json_object(reply.text)
    missing = _missing_required(obj, schema)
    if missing:
        raise TestAIError("bad_json", f"The reply is missing required keys: {', '.join(missing)}")
    return obj


# ── recorder (§11.5) ────────────────────────────────────────────────────────────────────────


def _record_case(meter: Meter) -> str:
    raw = meter.case or os.environ.get("WF_AI_RECORD_CASE") or "uncased"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)[:80] or "uncased"


def _prompts_version(meter: Meter) -> str:
    if meter.prompts_version:
        return meter.prompts_version
    try:
        from ..services.ai_builder_prompts import prompts_version

        return prompts_version()
    except Exception:  # noqa: BLE001
        return "unknown"


def record_call(meter: Meter, cfg: AIConfig, *, task: str, model: str, sha: str, reply_text: str, usage: tuple[int, int]) -> None:
    """Append one cassette entry to ``<WF_AI_RECORD_DIR>/<case>.json``. Only the reply and hashes are stored, never prompts.

    The reply is scanned first (known secrets, canaries, key shapes, PEM, and cards / IBANs / SSNs; emails and phones are
    NOT a trigger because model replies legitimately carry example ones). A hit raises ``AIError("record_aborted")`` and
    writes nothing.
    """
    if not cfg.record_dir:
        return
    reason = ai_policy.find_secret(reply_text, meter.secret_values)
    if reason is None and ai_policy.has_high_confidence_pii(reply_text):
        reason = "a card number, IBAN or SSN"
    if reason is not None:
        raise AIError("record_aborted", f"Recording aborted: the model reply for {SYSTEM_LINES[task]} holds {reason}")
    case = _record_case(meter)
    path = Path(cfg.record_dir) / f"{case}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cassette = json.loads(path.read_text())
        if not isinstance(cassette, dict) or not isinstance(cassette.get("calls"), list):
            raise ValueError("not a cassette")
    except (OSError, ValueError):
        cassette = {"case": case, "model": model, "prompts_version": _prompts_version(meter), "calls": []}
    cassette["calls"].append({"n": len(cassette["calls"]), "system_line": SYSTEM_LINES[task], "prompt_sha256": sha,
                              "reply_text": reply_text, "usage": {"in": usage[0], "out": usage[1]}})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cassette, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


# ── ask ─────────────────────────────────────────────────────────────────────────────────────


async def _one_call(task: str, system: str, user: str, schema: dict, *, meter: Meter, cfg: AIConfig, model: str,
                    max_tokens: int, adapter: ModelAdapter) -> tuple[AIReply, str, dict[str, Any]]:
    """Budget checks, one adapter call, metering and the llm_call event. Returns ``(reply, sha, event)``; the caller emits the event once its outcome is known."""
    if meter.calls >= meter.budget():
        raise AIError("call_budget", f"The call budget ({meter.budget()}) is used up")
    remaining = meter.remaining_s()
    if remaining is not None and remaining <= 0:
        raise AIError("deadline", "The build deadline has passed")
    size = len(system) + len(user)
    if size > cfg.max_prompt_chars:
        raise AIError("prompt_too_large", f"The prompt is {size} characters; the limit is {cfg.max_prompt_chars}")
    timeout = cfg.call_timeout_s if remaining is None else max(0.1, min(cfg.call_timeout_s, remaining))
    sha = prompt_sha256(model, system, user)
    event: dict[str, Any] = {"task": task, "system_line": SYSTEM_LINES[task], "prompt_sha256": sha, "model": model,
                             "input_tokens": 0, "output_tokens": 0, "ms": 0, "cached": False, "outcome": "ok"}
    if cfg.store_prompts:
        event["system"], event["user"] = system, user  # already scrubbed by the caller
    t0 = time.monotonic()
    meter.calls += 1
    try:
        reply = await asyncio.wait_for(adapter.ask_json(system, user, schema, max_tokens=max_tokens, timeout_s=timeout), timeout=cfg.call_timeout_s)
    except asyncio.TimeoutError:
        event.update(ms=int((time.monotonic() - t0) * 1000), outcome="timeout")
        meter.emit(event)
        raise AIError("timeout", f"The model did not answer within {timeout:.0f}s") from None
    except AIError:
        raise
    except Exception as e:  # noqa: BLE001 - provider down, quota, fake script unmatched ...
        logger.warning("wf_ai call failed (%s): %s", task, type(e).__name__)
        event.update(ms=int((time.monotonic() - t0) * 1000), outcome="provider_failed")
        meter.emit(event)
        raise AIError("provider_failed", f"The model call failed: {type(e).__name__}") from e
    ms = int((time.monotonic() - t0) * 1000)
    meter.llm_ms += ms
    i, o = meter.add_usage(reply.usage)
    event.update(ms=ms, input_tokens=i, output_tokens=o)
    return reply, sha, event


async def ask(task: str, system: str, user: str, schema: dict, *, meter: Meter, model: str | None = None,
              max_tokens: int | None = None, adapter: ModelAdapter | None = None) -> dict[str, Any]:
    """Ask the model one question and return its reply as a dict that follows ``schema``'s required keys."""
    check_system_line(task, system)
    if task == "json_fix":
        raise ValueError("json_fix is made by ask() itself")
    cfg = config()
    model = model or cfg.model
    ok, reason = ai_policy.allowed(model)
    if not ok:
        raise AIError("model_not_allowed", reason)
    if not llm_provider.is_configured(model):
        raise AIError("not_configured", f"No credentials for model '{model}'")

    key = prompt_sha256(model, system, user)
    if task in CACHEABLE_TASKS:
        hit = _cache_get(key)
        if hit is not None:
            meter.cached_hits += 1
            meter.emit({"task": task, "system_line": SYSTEM_LINES[task], "prompt_sha256": key, "model": model,
                        "input_tokens": 0, "output_tokens": 0, "ms": 0, "cached": True, "outcome": "ok"})
            return hit

    max_tokens = max_tokens or cfg.max_output_tokens
    adapter = adapter or make_adapter(model, cfg)
    reply, sha, event = await _one_call(task, system, user, schema, meter=meter, cfg=cfg, model=model, max_tokens=max_tokens, adapter=adapter)
    try:
        record_call(meter, cfg, task=task, model=model, sha=sha, reply_text=reply.text, usage=(event["input_tokens"], event["output_tokens"]))
        try:
            result = _parse(reply, schema)
        except TestAIError as first:
            event["outcome"] = "bad_json"
            meter.emit(event)
            event = None
            fix_system = SYSTEM_LINES["json_fix"] + "\n" + JSON_FIX_SCHEMA_NOTE
            fix_user = json.dumps({"error": first.message, "reply_head": (reply.text or "")[:JSON_FIX_HEAD_CHARS], "schema": schema},
                                  ensure_ascii=False)
            reply2, sha2, event2 = await _one_call("json_fix", fix_system, fix_user, schema, meter=meter, cfg=cfg, model=model,
                                                   max_tokens=max_tokens, adapter=adapter)
            try:
                record_call(meter, cfg, task="json_fix", model=model, sha=sha2, reply_text=reply2.text,
                            usage=(event2["input_tokens"], event2["output_tokens"]))
                try:
                    result = _parse(reply2, schema)
                except TestAIError as second:
                    event2["outcome"] = "bad_json"
                    raise AIError("bad_json", second.message) from None
            finally:
                meter.emit(event2)
    finally:
        if event is not None:
            meter.emit(event)
    if task in CACHEABLE_TASKS:
        _cache_put(key, result)
    return result
