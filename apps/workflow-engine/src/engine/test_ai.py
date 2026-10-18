"""Model adapter for the AI test helpers (Stage 3B, CONTRACTS-STAGE3B §1).

``ask_json(task, system, user, *, model=None) -> dict`` asks the model for one JSON object through
``llm_provider.call_llm`` (so the test-only fake LLM hooks in), and parses the reply. Every system prompt
starts with a fixed first line (``TEST_AI:DRAFT_TESTS`` / ``TEST_AI:VARIATIONS``) so fake scripts can match it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Literal

from . import llm_provider

logger = logging.getLogger(__name__)

TEST_AI_MODEL = os.environ.get("WORKFLOW_TEST_AI_MODEL") or os.environ.get("WORKFLOW_MODULE_GEN_MODEL") or "gemini-3.5-flash"
SYSTEM_LINES = {"draft_tests": "TEST_AI:DRAFT_TESTS", "variations": "TEST_AI:VARIATIONS"}
_MAX_OUTPUT_TOKENS = 16000
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$")


class TestAIError(Exception):
    """The model could not be used. ``code``: ``not_configured`` | ``bad_json`` | ``provider_failed``."""

    __test__ = False  # not a pytest class

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def parse_json_object(text: str | None) -> dict[str, Any]:
    """The first JSON object in ``text``, tolerating ```json fences and prose around it. Raises ``TestAIError(bad_json)``."""
    raw = (text or "").strip()
    if not raw:
        raise TestAIError("bad_json", "The model sent an empty reply")
    raw = _FENCE.sub("", raw).strip()
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(raw[start:])
        except ValueError:
            start = raw.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = raw.find("{", start + 1)
    raise TestAIError("bad_json", "The model's reply was not a JSON object")


async def ask_json(
    task: Literal["draft_tests", "variations"], system: str, user: str, *, model: str | None = None,
) -> dict[str, Any]:
    """Ask ``model`` (default ``TEST_AI_MODEL``) one question and return its reply as a dict."""
    if task not in SYSTEM_LINES:
        raise ValueError(f"unknown test-ai task: {task}")
    if not system.startswith(SYSTEM_LINES[task] + "\n") and system != SYSTEM_LINES[task]:
        raise ValueError(f"the system prompt for '{task}' must start with {SYSTEM_LINES[task]}")
    model = model or TEST_AI_MODEL
    if not llm_provider.is_configured(model):
        raise TestAIError("not_configured", f"No credentials for model '{model}'")
    try:
        response = await llm_provider.call_llm(
            model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2, response_format={"type": "json_object"}, max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except Exception as e:  # provider down, quota, fake script unmatched ...
        logger.warning("test-ai call failed (%s): %s", task, type(e).__name__)
        raise TestAIError("provider_failed", f"The model call failed: {type(e).__name__}") from e
    if getattr(response, "output_truncated", False):
        raise TestAIError("bad_json", "The model's reply was cut off")
    return parse_json_object(response.text)
