"""Test-only fake LLM provider (UI-DESIGN §10.3).

Hooked at the very top of ``llm_provider._call_llm_once``, so the real retry loop, the app-builder
agent loop and the generate repair loop all run unchanged above it.

Activation (``install()``, called first thing in the app lifespan). Every value is read from
``os.environ`` only. If ``WORKFLOW_LLM_PROVIDER`` is not ``"fake"`` nothing happens; otherwise ALL
of these must hold or startup aborts with ``RuntimeError("fake LLM refused: <reason>")``:

1. ``WORKFLOW_LLM_PROVIDER == "fake"``
2. ``WORKFLOW_TEST_MODE == "1"``
3. ``WORKFLOW_DB_NAME`` starts with ``workflows_test_``
4. ``WORKFLOW_BIND_HOST`` in {127.0.0.1, ::1, localhost} (advisory: the loopback middleware on
   ``/api/_test/*`` is the enforcement)
5. ``WORKFLOW_FAKE_LLM_TOKEN`` is at least 32 characters
6. ``WORKFLOW_DISABLE_DOTENV == "1"``

Script format::

    {"rules": [{"id", "when": {...}, "times": null, "respond": {...}}], "default": {"raise": "unmatched"}}

``when`` keys (all present keys must match): ``tool``, ``last_role``, ``last_user_matches``,
``system_matches``, ``last_tool_result_matches``, ``any_user_matches``, ``model_matches`` (regexes use
``re.search``) and ``nth`` (1-based count of calls that matched this rule's other ``when`` keys).
``respond``: ``{"text"}`` | ``{"tool_calls": [{"name", "args"}]}`` | ``{"raise": "retryable"|"fatal"|"unmatched", "status"?}``.
Strings: ``@file:<path>`` (relative to the script file) loads a file; ``${ENV}`` is interpolated in
every string, including loaded file contents. An unknown variable is a load-time error.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MIN_TOKEN_LEN = 32
CALL_LOG_SIZE = 500
WHEN_KEYS = {"tool", "last_role", "last_user_matches", "system_matches", "last_tool_result_matches",
             "any_user_matches", "model_matches", "nth"}
RESPOND_RAISES = {"retryable", "fatal", "unmatched"}
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# The installed fake, or None (production).
ACTIVE: "FakeLLM | None" = None


class FakeLLMError(Exception):
    """Raised by ``{"raise": "retryable"|"fatal"}``; ``status_code`` drives ``_is_retryable``."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeLLMUnmatched(Exception):
    """No rule matched (``{"raise": "unmatched"}``, the default)."""


class ScriptError(ValueError):
    """The script is invalid (bad shape, bad regex, unknown ${VAR}, unreadable @file:)."""


# ── script loading ───────────────────────────────────────────────────────────


def _interpolate(s: str, where: str) -> str:
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise ScriptError(f"unknown variable ${{{name}}} in {where}")
        return os.environ[name]
    return _ENV_RE.sub(sub, s)


def _resolve_strings(value: Any, base_dir: Path | None, where: str = "script") -> Any:
    if isinstance(value, str):
        if value.startswith("@file:"):
            rel = value[len("@file:"):]
            p = Path(rel)
            if not p.is_absolute():
                if base_dir is None:
                    raise ScriptError(f"@file:{rel} needs a script file location (relative path)")
                p = base_dir / p
            try:
                text = p.read_text()
            except OSError as e:
                raise ScriptError(f"cannot read @file:{rel}: {e}") from None
            return _interpolate(text, f"@file:{rel}")
        return _interpolate(value, where)
    if isinstance(value, list):
        return [_resolve_strings(v, base_dir, where) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_strings(v, base_dir, where) for k, v in value.items()}
    return value


def _check_respond(resp: Any, where: str) -> None:
    if not isinstance(resp, dict):
        raise ScriptError(f"{where}: respond must be an object")
    kinds = [k for k in ("text", "tool_calls", "raise") if k in resp]
    if len(kinds) != 1:
        raise ScriptError(f"{where}: respond needs exactly one of text, tool_calls, raise")
    if "raise" in resp and resp["raise"] not in RESPOND_RAISES:
        raise ScriptError(f"{where}: raise must be one of {sorted(RESPOND_RAISES)}")
    delay = resp.get("delay_ms")
    if delay is not None and (isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 60000):
        raise ScriptError(f"{where}: delay_ms must be a number between 0 and 60000")
    if "tool_calls" in resp:
        tcs = resp["tool_calls"]
        if not isinstance(tcs, list) or not tcs or not all(isinstance(t, dict) and isinstance(t.get("name"), str)
                                                          for t in tcs):
            raise ScriptError(f"{where}: tool_calls must be a non-empty list of {{name, args}}")


def parse_script(raw: Any, base_dir: Path | None = None) -> dict[str, Any]:
    """Validate a script and resolve ``@file:`` / ``${ENV}``. Raises ScriptError."""
    if not isinstance(raw, dict) or not isinstance(raw.get("rules", []), list):
        raise ScriptError("script must be an object with a 'rules' list")
    unknown_top = set(raw) - {"rules", "default"}
    if unknown_top:
        raise ScriptError(f"unknown script keys: {sorted(unknown_top)}")
    script = _resolve_strings(copy.deepcopy(raw), base_dir)
    rules = []
    for i, r in enumerate(script.get("rules") or []):
        where = f"rule {r.get('id', i) if isinstance(r, dict) else i}"
        if not isinstance(r, dict):
            raise ScriptError(f"{where}: must be an object")
        when = r.get("when") or {}
        if not isinstance(when, dict) or set(when) - WHEN_KEYS:
            raise ScriptError(f"{where}: unknown when keys {sorted(set(when) - WHEN_KEYS) if isinstance(when, dict) else when}")
        compiled = {}
        for k, v in when.items():
            if k.endswith("_matches"):
                try:
                    compiled[k] = re.compile(v)
                except (re.error, TypeError) as e:
                    raise ScriptError(f"{where}: bad regex for {k}: {e}") from None
        if "nth" in when and not (isinstance(when["nth"], int) and when["nth"] >= 1):
            raise ScriptError(f"{where}: nth must be an integer >= 1")
        if "last_role" in when and when["last_role"] not in ("user", "assistant", "tool", "system"):
            raise ScriptError(f"{where}: last_role must be user, assistant or tool")
        times = r.get("times")
        if times is not None and not (isinstance(times, int) and times >= 0):
            raise ScriptError(f"{where}: times must be null or an integer >= 0")
        _check_respond(r.get("respond"), where)
        rules.append({"id": r.get("id") or f"rule{i + 1}", "when": when, "re": compiled, "times": times,
                      "respond": r["respond"]})
    default = script.get("default") or {"raise": "unmatched"}
    _check_respond(default, "default")
    return {"rules": rules, "default": default}


def load_script_file(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise ScriptError(f"cannot load script {path}: {e}") from None
    return parse_script(raw, p.resolve().parent)


# ── message helpers ──────────────────────────────────────────────────────────


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content, default=str)


def _last(messages: list[dict], role: str) -> dict | None:
    for m in reversed(messages):
        if m.get("role") == role:
            return m
    return None


def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "gemini"
    from .llm_provider import LLAMA_MODELS
    if model in LLAMA_MODELS:
        return "llama"
    return "openai"


# ── the fake ─────────────────────────────────────────────────────────────────


class FakeLLM:
    def __init__(self, script: dict[str, Any] | None = None, base_dir: Path | None = None) -> None:
        self._lock = threading.Lock()
        self.base_dir = base_dir
        self.script = script or {"rules": [], "default": {"raise": "unmatched"}}
        self.calls: deque[dict[str, Any]] = deque(maxlen=CALL_LOG_SIZE)
        self.unavailable = False
        self._seq = 0
        self._matched: dict[int, int] = {}
        self._fired: dict[int, int] = {}

    def set_script(self, script: dict[str, Any]) -> int:
        with self._lock:
            self.script = script
            self._matched, self._fired = {}, {}
        return len(script["rules"])

    def clear_calls(self) -> None:
        with self._lock:
            self.calls.clear()

    def _base_match(self, rule: dict, model: str, messages: list[dict], tool_names: set[str]) -> bool:
        w, rx = rule["when"], rule["re"]
        if "tool" in w and w["tool"] not in tool_names:
            return False
        if "last_role" in w and (not messages or messages[-1].get("role") != w["last_role"]):
            return False
        checks = {
            "last_user_matches": lambda: [_text((_last(messages, "user") or {}).get("content"))] if _last(messages, "user") else [],
            "system_matches": lambda: [_text((_last(messages, "system") or {}).get("content"))] if _last(messages, "system") else [],
            "last_tool_result_matches": lambda: [_text((_last(messages, "tool") or {}).get("content"))] if _last(messages, "tool") else [],
            "any_user_matches": lambda: [_text(m.get("content")) for m in messages if m.get("role") == "user"],
            "model_matches": lambda: [model],
        }
        for k, get in checks.items():
            if k in rx and not any(rx[k].search(t) for t in get()):
                return False
        return True

    async def call(self, model: str, messages: list[dict], temperature: float = 0.2,
                   tools: list | None = None, **kwargs: Any) -> Any:
        from .llm_provider import LLMResponse, ToolCall, _normalize_tools

        normalised = _normalize_tools(tools or [], _provider_for(model)) if tools else []
        tool_names = {t["name"] for t in normalised}
        with self._lock:
            self._seq += 1
            seq = self._seq
            chosen: dict | None = None
            for idx, rule in enumerate(self.script["rules"]):
                if not self._base_match(rule, model, messages, tool_names):
                    continue
                self._matched[idx] = self._matched.get(idx, 0) + 1
                if chosen is not None:
                    continue
                nth = rule["when"].get("nth")
                if nth is not None and self._matched[idx] != nth:
                    continue
                if rule["times"] is not None and self._fired.get(idx, 0) >= rule["times"]:
                    continue
                self._fired[idx] = self._fired.get(idx, 0) + 1
                chosen = rule
            respond = chosen["respond"] if chosen else self.script["default"]
            entry: dict[str, Any] = {
                "seq": seq, "ts": time.time(), "model": model, "rule_id": chosen["id"] if chosen else None,
                "messages": json.loads(json.dumps(messages, default=str)),
                "tools": json.loads(json.dumps(normalised, default=str)), "temperature": temperature,
            }
            if "raise" in respond:
                entry["raised"] = respond["raise"]
            self.calls.append(entry)

        if respond.get("delay_ms"):  # demo pacing: the answer arrives after a pause, like a real model's
            await asyncio.sleep(float(respond["delay_ms"]) / 1000.0)
        kind = respond.get("raise")
        if kind == "retryable":
            raise FakeLLMError(f"fake LLM: retryable error (rule {entry['rule_id']})", int(respond.get("status") or 529))
        if kind == "fatal":
            raise FakeLLMError(f"fake LLM: fatal error (rule {entry['rule_id']})", int(respond.get("status") or 400))
        if kind == "unmatched":
            raise FakeLLMUnmatched(f"fake LLM: no rule matched call {seq}")
        if "tool_calls" in respond:
            return LLMResponse(text=respond.get("text"), tool_calls=[
                ToolCall(id=f"fake_{seq}_{i}", name=tc["name"], args=copy.deepcopy(tc.get("args") or {}))
                for i, tc in enumerate(respond["tool_calls"])])
        return LLMResponse(text=respond["text"])


# ── activation ───────────────────────────────────────────────────────────────


def _refuse(reason: str) -> None:
    raise RuntimeError(f"fake LLM refused: {reason}")


def check_activation() -> bool:
    """False when the fake is not requested; True when every condition holds; raises otherwise."""
    env = os.environ
    if env.get("WORKFLOW_LLM_PROVIDER") != "fake":
        return False
    if env.get("WORKFLOW_TEST_MODE") != "1":
        _refuse("WORKFLOW_TEST_MODE must be 1")
    if not (env.get("WORKFLOW_DB_NAME") or "").startswith("workflows_test_"):
        _refuse(f"WORKFLOW_DB_NAME must start with workflows_test_ (got {env.get('WORKFLOW_DB_NAME')!r})")
    if env.get("WORKFLOW_BIND_HOST") not in LOOPBACK_HOSTS:
        _refuse(f"WORKFLOW_BIND_HOST must be loopback (got {env.get('WORKFLOW_BIND_HOST')!r})")
    if len(env.get("WORKFLOW_FAKE_LLM_TOKEN") or "") < MIN_TOKEN_LEN:
        _refuse(f"WORKFLOW_FAKE_LLM_TOKEN must be at least {MIN_TOKEN_LEN} characters")
    if env.get("WORKFLOW_DISABLE_DOTENV") != "1":
        _refuse("WORKFLOW_DISABLE_DOTENV must be 1")
    return True


def install() -> "FakeLLM | None":
    """Activate the fake when requested (all conditions) — else do nothing. Idempotent per process."""
    global ACTIVE
    if not check_activation():
        return None
    from . import llm_provider

    script_path = os.environ.get("WORKFLOW_FAKE_LLM_SCRIPT")
    try:
        script = load_script_file(script_path) if script_path else parse_script({"rules": []})
    except ScriptError as e:
        _refuse(f"script: {e}")
    base = Path(script_path).resolve().parent if script_path else None
    ACTIVE = FakeLLM(script, base_dir=base)
    llm_provider._RETRY_BASE_DELAY = 0  # E8: retries without waiting
    return ACTIVE


def uninstall() -> None:
    """Tests only."""
    global ACTIVE
    from . import llm_provider

    ACTIVE = None
    llm_provider._RETRY_BASE_DELAY = 1.0


def token() -> str:
    return os.environ.get("WORKFLOW_FAKE_LLM_TOKEN") or ""
