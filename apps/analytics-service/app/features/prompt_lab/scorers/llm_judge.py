"""LLM-judge scorer — uses Anthropic Haiku to rate (prediction, expected) on a 0-1 scale.

Requires an `anthropic_client` kwarg (an `AsyncAnthropic` instance). Falls back
to 0.0 on parse failures or API errors.
"""

from __future__ import annotations

import re
from typing import Any

_JUDGE_MODEL = "claude-haiku-4-5"
_PROMPT = """You are an impartial grader. Compare the model's PREDICTION to the EXPECTED answer
and rate how well the prediction matches on a scale from 0.0 (totally wrong) to 1.0 (perfect match).

EXPECTED: {expected}
PREDICTION: {prediction}

Respond with ONLY a single decimal number between 0.0 and 1.0. No explanation."""

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


async def score(prediction: str, expected: Any, **kwargs) -> float:
    client = kwargs.get("anthropic_client")
    if client is None:
        return 0.0
    try:
        resp = await client.messages.create(
            model=_JUDGE_MODEL,
            max_tokens=8,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": _PROMPT.format(expected=str(expected), prediction=str(prediction)),
                }
            ],
        )
        text_parts = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
        raw = "".join(text_parts).strip()
        match = _NUM_RE.search(raw)
        if not match:
            return 0.0
        val = float(match.group(0))
        return max(0.0, min(1.0, val))
    except Exception:
        return 0.0
