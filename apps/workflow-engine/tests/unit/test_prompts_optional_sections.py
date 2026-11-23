"""Capability graph (CONTRACTS-CAPGRAPH.md 4.3): the new optional prompt sections change nothing when they are absent.

``tests/fixtures/ai_builder/prompt_render_snapshot.json`` holds sha256 hashes of every existing prompt rendered BEFORE the capability graph
existed (system + user message for each task, with and without the Stage 8 ``callable_subworkflows`` section), plus ``prompts_version()``.
They protect the replay cassettes and every fake_llm script: a prompt that moves by one byte makes them stale.

Regenerate ONLY with ``WF_PROMPT_SNAPSHOT_WRITE=1`` and only for a deliberate, reviewed prompt change.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.services import ai_builder_prompts as P

SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "ai_builder" / "prompt_render_snapshot.json"
EXAMPLE_SPEC = {"title": "T", "summary": "s", "trigger": {"kind": "webhook", "fields": []}, "rules": [{"id": "r1", "text": "x"}],
                "steps": [{"name": "Receive", "node_type": "Webhook", "purpose": "p", "rules": []}], "tests": [], "rule_tests": {}}
SUBS = [{"id": "wf_1", "name": "Assign owner", "inputs": [{"name": "category", "type": "string", "required": True}], "output_fields": ["owner"]}]


def _contexts() -> dict[str, dict]:
    base = {
        "spec": {"untrusted_request": "Refunds over 500 need a manager.", "mode": "new", "catalog": [{"type": "Webhook"}], "api_operations": [],
                 "existing_workflows": [], "variable_names": ["V"]},
        "spec_repair": {"spec": EXAMPLE_SPEC, "issues": [{"code": "SPEC004", "message": "m"}], "catalog": [{"type": "Webhook"}], "api_operations": []},
        "build": {"spec": EXAMPLE_SPEC, "catalog": [{"type": "Webhook"}], "api_operations": [], "variable_names": [], "current_workflow": None,
                  "untrusted_revision_request": ""},
        "repair": {"spec": EXAMPLE_SPEC, "catalog": [{"type": "Webhook"}], "api_operations": [], "current_workflow": {"nodes": []},
                   "op_errors": [], "findings": [{"code": "WF001"}], "round": 1, "rounds_left": 2, "untrusted_revision_request": ""},
        "node_fix": {"untrusted_error": "boom"},
        "node_code": {"untrusted_instruction": "sum the amounts"},
        "node_expr": {"untrusted_instruction": "the amount"},
        "node_explain": {"untrusted_question": "what does this do?"},
        "chat_route": {"untrusted_message": "add a step", "mode_hint": "auto", "has_workflow": False},
    }
    out: dict[str, dict] = {}
    for task, ctx in base.items():
        out[task] = ctx
        if task in P.OPTIONAL_TASKS and "callable_subworkflows" in P.OPTIONAL_SECTIONS:
            out[f"{task}+subs"] = {**ctx, "callable_subworkflows": SUBS}
    return out


def _render_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, ctx in _contexts().items():
        task = name.split("+")[0]
        system, user = P.render(task, ctx, max_chars=10**9)
        hashes[name] = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()
    for task in sorted(P.PROMPTS):
        hashes[f"hash:{task}"] = P.prompt_hash(task)
    hashes["prompts_version"] = P.prompts_version()
    return hashes


def test_write_snapshot_when_asked():
    if os.environ.get("WF_PROMPT_SNAPSHOT_WRITE") == "1":
        SNAPSHOT.write_text(json.dumps(_render_hashes(), indent=1, sort_keys=True) + "\n")
    assert SNAPSHOT.exists()


def test_every_existing_prompt_renders_byte_identical():
    want = json.loads(SNAPSHOT.read_text())
    got = _render_hashes()
    changed = sorted(k for k in want if got.get(k) != want[k])
    assert not changed, f"prompts changed: {changed}"
    assert "prompts_version" in got and got["prompts_version"] == want["prompts_version"], "prompts_version moved: every cassette is STALE"


def test_the_graph_sections_are_optional_and_only_render_when_present():
    ctx = _contexts()["spec"]
    system0, user0 = P.render("spec", ctx)
    assert "capability_context" not in user0 and "reference_workflow" not in user0
    assert "capability_context` is the organisation" not in system0
    # empty values are "absent" too (the same rule as callable_subworkflows)
    s1, u1 = P.render("spec", {**ctx, "capability_context": {}, "reference_workflow": None})
    assert (s1, u1) == (system0, user0)
    s2, u2 = P.render("spec", {**ctx, "capability_context": {"concepts": [{"key": "concept:approval"}], "slots": {}, "reuse": {}}})
    assert s2.startswith(system0.rstrip("\n")) and "capability_context` is the organisation" in s2
    assert '"capability_context":' in u2 and u2.startswith(u1[:-1])
    for task in ("spec", "spec_repair", "build", "repair"):
        assert "capability_context" in P.OPTIONAL_TASKS[task] and "reference_workflow" in P.OPTIONAL_TASKS[task]


def test_reference_workflow_rule_is_appended_after_the_capability_rule():
    ctx = {**_contexts()["build"], "capability_context": {"concepts": [{"key": "k"}]}, "reference_workflow": {"nodes": []}}
    system, user = P.render("build", ctx)
    assert system.index("capability_context` is the organisation") < system.index("reference_workflow` is a workflow that passed")
    assert user.index('"capability_context"') < user.index('"reference_workflow"')


def test_the_understand_prompt_is_outside_the_hashed_prompts():
    """prompts_version() hashes PROMPTS; the understand prompt lives in GRAPH_PROMPTS so no cassette goes STALE."""
    assert "understand" not in P.PROMPTS
    system, user = P.render_understand({"untrusted_request": "x", "concept_vocabulary": [], "roles": [], "slot_types": []})
    assert system.split("\n")[0] == "WF_AI:UNDERSTAND"
    assert json.loads(user)["untrusted_request"] == "x"
