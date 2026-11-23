"""Cassettes for the Stage 6 AI builder (DESIGN-STAGE6-AI-BUILDER §11.5).

A cassette is what ``engine/wf_ai.record_call`` writes while ``WF_AI_RECORD_DIR`` is set (replies and hashes only, never prompts)::

    {"case": "refund_over_500", "model": "gemini-3.5-flash", "prompts_version": "<16 hex>",
     "calls": [{"n": 0, "system_line": "WF_AI:SPEC", "prompt_sha256": "..", "reply_text": "{...}", "usage": {"in": 1, "out": 2}}],
     "verdict": {...}}          # added by the harness after scoring (write_verdict)

``to_fake_script(cassette)`` turns it into a ``fake_llm`` script: one rule per (system_line, nth), where ``nth`` is the 1-based
count of calls with that system line (fake_llm counts the calls that matched the rule's other ``when`` keys). The regex is anchored, so
``WF_AI:SPEC`` does not also match ``WF_AI:SPEC_REPAIR``. Replay installs the script through ``PUT /api/_test/fake-llm/script``.

How recordings get their file name: ``wf_ai`` names the file after ``Meter.case`` or the env ``WF_AI_RECORD_CASE``, and the builder
sets ``Meter.case`` to the project id; ``adopt_recording`` renames ``<project_id>.json`` to ``<case>.json`` after a case has run.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

CASSETTE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "ai_builder" / "cassettes"
SYSTEM_LINES = ("WF_AI:SPEC", "WF_AI:SPEC_REPAIR", "WF_AI:BUILD", "WF_AI:REPAIR", "WF_AI:NODE_FIX", "WF_AI:NODE_CODE", "WF_AI:NODE_EXPR",
                "WF_AI:NODE_EXPLAIN", "WF_AI:CHAT_ROUTE", "WF_AI:JSON_FIX")
TOKEN_HEADER = "X-Test-Token"


class CassetteError(ValueError):
    """The cassette is malformed or cannot be turned into a fake script."""


# ── files ───────────────────────────────────────────────────────────────────────────────────


def load_cassette(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise CassetteError(f"cannot read cassette {p}: {e}") from None
    validate_cassette(doc)
    return doc


def list_cassettes(directory: str | Path | None = None) -> list[Path]:
    d = Path(directory) if directory else CASSETTE_DIR
    return sorted(d.glob("*.json")) if d.is_dir() else []


def save_cassette(path: str | Path, cassette: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cassette, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


def validate_cassette(doc: Any) -> None:
    if not isinstance(doc, dict) or not isinstance(doc.get("case"), str) or not isinstance(doc.get("calls"), list):
        raise CassetteError("a cassette is an object with a 'case' string and a 'calls' list")
    for i, c in enumerate(doc["calls"]):
        if not isinstance(c, dict) or c.get("system_line") not in SYSTEM_LINES or not isinstance(c.get("reply_text"), str):
            raise CassetteError(f"call {i}: needs a known system_line and a reply_text string")


def write_verdict(path: str | Path, verdict: dict[str, Any]) -> dict[str, Any]:
    """Store the scored outcome of the recorded run (what replay must reproduce) in the cassette file."""
    cassette = load_cassette(path)
    cassette["verdict"] = verdict
    save_cassette(path, cassette)
    return cassette


def adopt_recording(record_dir: str | Path, recorded_as: str, case: str, verdict: dict[str, Any] | None = None) -> Path:
    """Rename ``<recorded_as>.json`` (the project id the builder used) to ``<case>.json``, set its ``case``, optionally add the verdict."""
    src = Path(record_dir) / f"{recorded_as}.json"
    cassette = load_cassette(src)
    cassette["case"] = case
    if verdict is not None:
        cassette["verdict"] = verdict
    dst = Path(record_dir) / f"{case}.json"
    save_cassette(dst, cassette)
    if src != dst:
        src.unlink()
    return dst


# ── replay ──────────────────────────────────────────────────────────────────────────────────


def to_fake_script(cassette: dict[str, Any]) -> dict[str, Any]:
    """The fake_llm script that replays ``cassette``. An unrecorded extra call raises ``unmatched`` (the default), which fails the replay loudly."""
    validate_cassette(cassette)
    counts: dict[str, int] = {}
    rules = []
    for call in cassette["calls"]:
        line, text = call["system_line"], call["reply_text"]
        if "${" in text or text.startswith("@file:"):
            raise CassetteError(f"call {call.get('n')}: the reply holds '${{' or starts with '@file:', which fake_llm would interpolate")
        counts[line] = counts.get(line, 0) + 1
        rules.append({"id": f"{cassette['case']}:{line}:{counts[line]}",
                      "when": {"system_matches": "^" + re.escape(line) + r"(?:\n|$)", "nth": counts[line]},
                      "respond": {"text": text}})
    return {"rules": rules, "default": {"raise": "unmatched"}}


def expected_calls(cassette: dict[str, Any]) -> dict[str, int]:
    """Recorded call count per system line (the replay test compares it with ``GET /api/_test/fake-llm/calls``)."""
    out: dict[str, int] = {}
    for c in cassette["calls"]:
        out[c["system_line"]] = out.get(c["system_line"], 0) + 1
    return out


def prompts_stale(cassette: dict[str, Any]) -> bool:
    """True when the cassette was recorded with other prompts than the current ones (STALE; with WF_AI_REPLAY_STRICT=1 the replay test fails)."""
    from src.services.ai_builder_prompts import prompts_version

    return cassette.get("prompts_version") != prompts_version()


def replay_strict() -> bool:
    return os.environ.get("WF_AI_REPLAY_STRICT", "0").strip().lower() in ("1", "true", "yes")


def install_script(client: Any, token: str, cassette_or_script: dict[str, Any]) -> int:
    """PUT the script to a running fake-LLM engine. ``client`` is the integration ``StudioClient`` (``.raw``). Returns the rule count."""
    script = cassette_or_script if "rules" in cassette_or_script else to_fake_script(cassette_or_script)
    r = client.raw("PUT", "/api/_test/fake-llm/script", headers={TOKEN_HEADER: token}, json=script)
    if r.status_code != 200:
        raise CassetteError(f"the engine refused the script: {r.status_code} {r.text[:200]}")
    return int(r.json()["rules"])


def clear_calls(client: Any, token: str) -> None:
    client.raw("DELETE", "/api/_test/fake-llm/calls", headers={TOKEN_HEADER: token})


def fetch_calls(client: Any, token: str) -> list[dict[str, Any]]:
    """The fake LLM's call log (prompts included: use only in tests)."""
    r = client.raw("GET", "/api/_test/fake-llm/calls", headers={TOKEN_HEADER: token})
    return list(r.json()) if r.status_code == 200 else []


def count_by_system_line(calls: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for call in calls:
        sys_msg = next((m.get("content", "") for m in call.get("messages", []) if m.get("role") == "system"), "")
        line = str(sys_msg).split("\n", 1)[0]
        out[line] = out.get(line, 0) + 1
    return out


# ── recording (live tier) ───────────────────────────────────────────────────────────────────


def record_env(record_dir: str | Path) -> dict[str, str]:
    """Env for the engine process (``extra_env``) that turns the recorder on."""
    return {"WF_AI_RECORD_DIR": str(record_dir)}


def scan_cassette(cassette: dict[str, Any], secret_values: set[str] | None = None) -> list[str]:
    """Reasons a cassette must not be committed (secrets, canaries, cards, IBANs, SSNs in a reply). Empty list = clean."""
    from src.engine import ai_policy

    problems = []
    for c in cassette["calls"]:
        reason = ai_policy.find_secret(c["reply_text"], secret_values)
        if reason is None and ai_policy.has_high_confidence_pii(c["reply_text"]):
            reason = "a card number, IBAN or SSN"
        if reason:
            problems.append(f"call {c.get('n')} ({c['system_line']}): {reason}")
    return problems
