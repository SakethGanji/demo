"""Tier 0 — generic coverage across all 27 SDK_TYPES (all 28 registered nodes minus
the deliberately-excluded AIAgent). Unlike test_workflow_sdk_contract.py, which hand-
picks realistic values per node for readability, these tests synthesize values FROM
the live registry's own property list — same principle the SDK itself uses: nothing
here is a hand-copied schema that can drift out of sync with the real nodes.

Run: venv/bin/python -m pytest tests/engine/test_workflow_sdk_all_types.py -v
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from src.engine.workflow_sdk import (  # noqa: E402
    EXCLUDED_TYPES,
    SDK_TYPES,
    _is_effectively_required,
    execute_workflow_script,
)
from src.engine.node_registry import node_registry, register_all_nodes  # noqa: E402

# Registration must happen at IMPORT time, not just in a fixture — the
# parametrize() call below runs at module-collection time, before any fixture
# executes, and needs the registry populated to compute its type list.
register_all_nodes()


@pytest.fixture(autouse=True, scope="module")
def _registered():
    register_all_nodes()


def _dummy_value(prop) -> object:
    """A plausible, type-correct value for a NodeProperty, derived from its own
    declared type/options — not a hand-picked value that could go stale."""
    if prop.options:
        return prop.options[0].value
    if prop.type == "number":
        return 1
    if prop.type == "boolean":
        return True
    if prop.type == "json":
        return "{}"
    if prop.type == "collection":
        return []
    return "test-value"  # string and anything else


def _all_registered_types() -> list[str]:
    register_all_nodes()
    return node_registry.list()


# ---------------------------------------------------------------------------
# Registry-level sanity: SDK_TYPES is exactly "everything except AIAgent"
# ---------------------------------------------------------------------------

def test_sdk_types_is_all_registered_types_minus_excluded():
    registered = set(_all_registered_types())
    assert set(SDK_TYPES) | EXCLUDED_TYPES == registered, (
        "SDK_TYPES + EXCLUDED_TYPES has drifted from node_registry — a node was "
        "added or removed and this file (or workflow_sdk.py's SDK_TYPES) wasn't updated"
    )
    assert set(SDK_TYPES) & EXCLUDED_TYPES == set()


def test_aiagent_is_excluded_and_unreachable_from_a_script():
    assert "AIAgent" not in SDK_TYPES
    assert node_registry.has("AIAgent")  # it's a real node, just not exposed here
    result = execute_workflow_script('n = AIAgent(model="x")\n')
    assert result.error is not None
    assert "not defined" in result.error  # plain NameError — the name was never injected


# ---------------------------------------------------------------------------
# Every SDK type constructs cleanly when given exactly its own required params,
# synthesized from the live registry — and fails correctly when one is omitted.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_name", SDK_TYPES)
def test_type_constructs_with_only_its_required_params(type_name):
    instance = node_registry.get(type_name)
    props = instance.node_description.properties if instance.node_description else []
    required = [p for p in props if _is_effectively_required(p)]
    kwargs = ", ".join(f'{p.name}={_dummy_value(p)!r}' for p in required)
    script = f'n = {type_name}({kwargs})\n'
    result = execute_workflow_script(script)
    assert result.error is None, f"{type_name} failed with only its required params: {result.error}\nscript: {script}"
    assert len(result.workflow.nodes) == 1
    assert result.workflow.nodes[0].type == type_name


@pytest.mark.parametrize("type_name", SDK_TYPES)
def test_type_rejects_unknown_parameter(type_name):
    script = f'n = {type_name}(__totally_made_up_param__="x")\n'
    result = execute_workflow_script(script)
    assert result.error is not None
    assert "no parameter '__totally_made_up_param__'" in result.error


def _required_props(type_name: str):
    desc = node_registry.get(type_name).node_description
    return [p for p in (desc.properties if desc else []) if _is_effectively_required(p)]


_TYPES_WITH_REQUIRED_PARAMS = [t for t in SDK_TYPES if _required_props(t)]


@pytest.mark.parametrize("type_name", _TYPES_WITH_REQUIRED_PARAMS)
def test_type_with_required_params_rejects_missing_them(type_name):
    result = execute_workflow_script(f'n = {type_name}()\n')
    assert result.error is not None
    assert "missing required parameter" in result.error


# ---------------------------------------------------------------------------
# Merge — genuinely different wiring shape (multiple inputs), the fan-in case
# the original 8-type slice never exercised.
# ---------------------------------------------------------------------------

def test_merge_accepts_multiple_independent_sources():
    script = (
        'start = Start()\n'
        'a = HttpRequest(method="GET", url="https://example.com/a")\n'
        'b = HttpRequest(method="GET", url="https://example.com/b")\n'
        'c = HttpRequest(method="GET", url="https://example.com/c")\n'
        'merge = Merge()\n'
        'start >> a\n'
        'start >> b\n'
        'start >> c\n'
        'for source in (a, b, c):\n'
        '    source >> merge\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error
    into_merge = [c for c in result.workflow.connections if c.target_node == "Merge"]
    assert len(into_merge) == 3
    assert {c.source_node for c in into_merge} == {"HttpRequest", "HttpRequest 2", "HttpRequest 3"}


# ---------------------------------------------------------------------------
# ChatInput is a valid trigger (inputs=[]), not just Cron/Start/Webhook/etc.
# ---------------------------------------------------------------------------

def test_chatinput_counts_as_a_trigger_for_validate():
    # LLMChat needs no args here: `model` and `userMessage` are both `required=True`
    # in the schema but ship non-empty defaults, so the SDK's own mapping rule
    # (SDK-DESIGN.md §5) treats them as optional — confirmed by construction
    # succeeding with zero kwargs, not asserted.
    script = (
        'entry = ChatInput()\n'
        'reply = LLMChat()\n'
        'entry >> reply\n'
        'validate()\n'
    )
    result = execute_workflow_script(script)
    assert result.error is None, result.error


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
