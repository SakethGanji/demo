"""Optional wording of Vela's computed findings by a language model, on top of the numbers, never instead of them.

Order: the `claude` CLI (`claude -p --model claude-sonnet-5-5 --output-format json`), then the Anthropic API when
ANTHROPIC_API_KEY is set, then the computed text unchanged. Each attempt has a 30 s timeout. A reworded text is
kept only if every number of the computed text appears in it; otherwise that finding keeps the computed text.

Environment: ENSEMBLE_VELA_LLM = auto (default) | off · ENSEMBLE_CLAUDE_BIN (default `claude`) ·
ENSEMBLE_VELA_MODEL (default claude-sonnet-5-5) · ENSEMBLE_VELA_TIMEOUT (seconds, default 30).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any

NUM = re.compile(r"(?<![\w.])[-−]?\d+(?:[.,]\d+)*%?")

PROMPT = """You are Vela, the analyst assistant in a bank's AutoML workbench. Below are findings computed from one
training run. Rewrite each finding's "text" for a risk or credit analyst: one or two plain sentences, direct, no
hedging, no greeting. Keep every number exactly as written (same digits, same decimals). Do not add numbers, causes
or advice that are not in the input. The patch is described separately; do not restate it.

Return only a JSON object mapping each finding id to its new text, nothing else.

Run: {run}

Findings:
{findings}
"""


def _numbers(text: str) -> list[str]:
    return [n.replace("−", "-").rstrip("%") for n in NUM.findall(text)]


def _keeps_numbers(orig: str, new: str) -> bool:
    have = set(_numbers(new))
    return all(n in have for n in _numbers(orig))


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("the reply is not a JSON object")
    return obj


def _via_cli(prompt: str, model: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    exe = os.environ.get("ENSEMBLE_CLAUDE_BIN", "claude")
    path = exe if os.path.sep in exe else shutil.which(exe)
    if not path:
        raise FileNotFoundError(f"`{exe}` is not on PATH")
    p = subprocess.run([path, "-p", "--model", model, "--output-format", "json"], input=prompt, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"claude exited {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
    outer = json.loads(p.stdout)
    if outer.get("is_error"):
        raise RuntimeError(str(outer.get("result"))[:300])
    meta = {"model": next(iter(outer.get("modelUsage") or {}), model), "cost_usd": outer.get("total_cost_usd")}
    return _parse_json_object(outer.get("result") or ""), meta


def _via_api(prompt: str, model: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    import httpx

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                   headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                   json={"model": model, "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    body = r.json()
    text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
    return _parse_json_object(text), {"model": body.get("model", model)}


def word(findings: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """{source: claude-cli | anthropic-api | computed, model, texts: {id: text}, kept: {id: bool}, attempts, seconds}."""
    computed = {f["id"]: f.get("computed_text") or f["text"] for f in findings}
    mode = os.environ.get("ENSEMBLE_VELA_LLM", "auto").lower()
    model = os.environ.get("ENSEMBLE_VELA_MODEL", "claude-sonnet-5-5")
    timeout = float(os.environ.get("ENSEMBLE_VELA_TIMEOUT", "30"))
    attempts: list[dict[str, Any]] = []
    t0 = time.time()
    if mode != "off" and findings:
        payload = json.dumps([{"id": f["id"], "title": f["title"], "text": computed[f["id"]]} for f in findings],
                             ensure_ascii=False, indent=1)
        prompt = PROMPT.format(run=json.dumps(run, ensure_ascii=False), findings=payload)
        for source, fn in (("claude-cli", _via_cli), ("anthropic-api", _via_api)):
            s0 = time.time()
            try:
                obj, meta = fn(prompt, model, timeout)
            except subprocess.TimeoutExpired:
                attempts.append({"source": source, "ok": False, "error": f"timed out after {timeout:.0f} s", "seconds": time.time() - s0})
                continue
            except Exception as e:  # noqa: BLE001
                attempts.append({"source": source, "ok": False, "error": f"{type(e).__name__}: {e}"[:300], "seconds": time.time() - s0})
                continue
            texts, kept = {}, {}
            for fid, orig in computed.items():
                new = obj.get(fid)
                ok = isinstance(new, str) and new.strip() and _keeps_numbers(orig, new)
                texts[fid] = new.strip() if ok else orig
                kept[fid] = bool(ok)
            attempts.append({"source": source, "ok": True, "seconds": time.time() - s0})
            return {"source": source, "model": meta.get("model"), "texts": texts, "kept": kept, "attempts": attempts,
                    "seconds": time.time() - t0, "cost_usd": meta.get("cost_usd")}
    return {"source": "computed", "model": None, "texts": computed, "kept": {k: False for k in computed}, "attempts": attempts,
            "seconds": time.time() - t0, "disabled": mode == "off"}
