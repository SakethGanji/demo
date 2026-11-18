"""Optional wording of Vela's computed findings by a language model, on top of the numbers, never instead of them.

Through the F5 router (app/llm.py): the Anthropic SDK when ANTHROPIC_API_KEY is set, then the `claude` CLI, then the
computed text unchanged; asynchronous, cached by (prompt hash, facts hash), timeouts from the router's settings. The
number guard: a reworded text is kept only if every number of the computed text appears in it; otherwise that finding
keeps the computed text.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .. import llm

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


async def word_async(findings: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """{source: anthropic-sdk | claude-cli | computed, decider, model, texts: {id: text}, kept: {id: bool}, attempts, seconds,
    cached}."""
    computed = {f["id"]: f.get("computed_text") or f["text"] for f in findings}
    t0 = time.perf_counter()
    if not llm.enabled() or not findings:
        return {"source": "computed", "decider": llm.SOURCES["computed"], "model": None, "texts": computed,
                "kept": {k: False for k in computed}, "attempts": [], "seconds": time.perf_counter() - t0,
                "disabled": not llm.enabled()}
    items = [{"id": f["id"], "title": f["title"], "text": computed[f["id"]]} for f in findings]
    prompt = PROMPT.format(run=json.dumps(run, ensure_ascii=False), findings=json.dumps(items, ensure_ascii=False, indent=1))
    obj, meta = await llm.ask_json_async(prompt, facts={"run": run, "findings": items})
    texts, kept = {}, {}
    for fid, orig in computed.items():
        new = (obj or {}).get(fid)
        ok = isinstance(new, str) and new.strip() and _keeps_numbers(orig, new)
        texts[fid] = new.strip() if ok else orig
        kept[fid] = bool(ok)
    return {"source": meta["source"], "decider": meta["decider"], "model": meta.get("model"), "texts": texts, "kept": kept,
            "attempts": meta["attempts"], "seconds": meta["seconds"], "cached": meta.get("cached", False),
            "cost_usd": meta.get("cost_usd"), "disabled": False}


def word(findings: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """Blocking form of word_async (for code in its own thread and the tests)."""
    return asyncio.run(word_async(findings, run))
