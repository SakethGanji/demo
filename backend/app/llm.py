"""One caller for every optional language-model call (Autopilot decisions, the decision log's wording, Vela's answers).

Order: the `claude` CLI (`claude -p --model claude-sonnet-5-5 --output-format json`), then the Anthropic API when
ANTHROPIC_API_KEY is set, then nothing: the caller gets `None` and uses its computed fallback. Reuses Vela's tested
`_via_cli` / `_via_api` (app/vela/wording.py). Default timeout 60 s per attempt.

Environment: ENSEMBLE_VELA_LLM = auto (default) | off · ENSEMBLE_CLAUDE_BIN · ENSEMBLE_VELA_MODEL ·
ENSEMBLE_LLM_TIMEOUT (seconds, default 60).
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from .vela.wording import _via_api, _via_cli

SOURCES = {"claude-cli": "Claude CLI", "anthropic-api": "Anthropic API", "computed": "computed (no model)"}


def enabled() -> bool:
    return os.environ.get("ENSEMBLE_VELA_LLM", "auto").lower() != "off"


def ask_json(prompt: str, timeout: float | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(the reply as a JSON object or None, meta: {source, model, attempts, seconds, cost_usd})."""
    model = os.environ.get("ENSEMBLE_VELA_MODEL", "claude-sonnet-5-5")
    timeout = float(timeout or os.environ.get("ENSEMBLE_LLM_TIMEOUT", "60"))
    attempts: list[dict[str, Any]] = []
    t0 = time.time()
    if enabled():
        for source, fn in (("claude-cli", _via_cli), ("anthropic-api", _via_api)):
            s0 = time.time()
            try:
                obj, meta = fn(prompt, model, timeout)
            except subprocess.TimeoutExpired:
                attempts.append({"source": source, "ok": False, "error": f"timed out after {timeout:.0f} s", "seconds": round(time.time() - s0, 2)})
                continue
            except Exception as e:  # noqa: BLE001
                attempts.append({"source": source, "ok": False, "error": f"{type(e).__name__}: {e}"[:300], "seconds": round(time.time() - s0, 2)})
                continue
            attempts.append({"source": source, "ok": True, "seconds": round(time.time() - s0, 2)})
            return obj, {"source": source, "model": meta.get("model"), "attempts": attempts,
                         "seconds": round(time.time() - t0, 2), "cost_usd": meta.get("cost_usd")}
    return None, {"source": "computed", "model": None, "attempts": attempts, "seconds": round(time.time() - t0, 2),
                  "disabled": not enabled()}
