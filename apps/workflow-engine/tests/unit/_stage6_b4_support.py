"""Shared helpers of the Stage 6 B4 tests: install a hand-written fake LLM script in this process, build a project, drive the
builder service the way the routes do. Not a test module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.engine import fake_llm

SCRIPTS = Path(__file__).resolve().parents[1] / "fixtures" / "fake_llm" / "ai_builder"
PROJECT = "proj_default"


def install_script(name_or_rules: str | list[dict[str, Any]]) -> fake_llm.FakeLLM:
    """Install the fake LLM in this process with a script from ``fixtures/fake_llm/ai_builder`` (or explicit rules)."""
    if isinstance(name_or_rules, str):
        script = fake_llm.load_script_file(str(SCRIPTS / f"{name_or_rules}.json"))
    else:
        script = fake_llm.parse_script({"rules": name_or_rules})
    fake = fake_llm.FakeLLM(script)
    fake_llm.ACTIVE = fake
    return fake


def uninstall() -> None:
    fake_llm.ACTIVE = None


def load_rules(name: str) -> list[dict[str, Any]]:
    return json.loads((SCRIPTS / f"{name}.json").read_text())["rules"]


def prompts_seen(fake: fake_llm.FakeLLM) -> list[str]:
    """Every system + user message the fake received, as one string per call (what the model would have seen)."""
    out = []
    for c in fake.calls:
        out.append("\n".join(str(m.get("content")) for m in c["messages"]))
    return out


def system_lines(fake: fake_llm.FakeLLM) -> list[str]:
    out = []
    for c in fake.calls:
        sys_msg = next((m["content"] for m in c["messages"] if m.get("role") == "system"), "")
        out.append(sys_msg.split("\n", 1)[0])
    return out
