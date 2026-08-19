"""SDK-completion guarantees (HANDOFF-SDK-COMPLETION.md steps 3–4):

- SDK_TYPES is DERIVED from the registry, so the "add a node once" drill must
  pass with zero workflow_sdk.py edits: register a throwaway type and it shows
  up in constructors, signature_reference() and describe() immediately.
- The two misuses that caused 5/5 failures in the 2026-08-19 Sonnet eval now
  raise teaching errors: wiring the constructor itself, and calling a trigger
  constructor twice (once bare "to configure").

Run: venv/bin/python -m pytest tests/engine/test_workflow_sdk_completion.py -v
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.engine.node_registry import node_registry, register_all_nodes  # noqa: E402
from src.engine.workflow_sdk import (  # noqa: E402
    EXCLUDED_TYPES,
    SDK_TYPES,
    execute_workflow_script,
    signature_reference,
)
from src.nodes.base import (  # noqa: E402
    BaseNode,
    NodeInputDefinition,
    NodeOutputDefinition,
    NodeProperty,
    NodeTypeDescription,
)


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def test_sdk_types_is_derived_and_deterministic():
    register_all_nodes()
    assert set(SDK_TYPES) == set(node_registry.list()) - EXCLUDED_TYPES
    # Deterministic ordering — the reference is agent-facing prompt text.
    from src.engine import workflow_sdk

    assert workflow_sdk.SDK_TYPES == workflow_sdk.SDK_TYPES
    assert "AIAgent" not in SDK_TYPES


def test_signature_reference_has_group_headers():
    ref = signature_reference()
    for header in ("# trigger", "# flow", "# transform"):
        assert header in ref, f"missing group header {header!r}"
    # The orphan-constructor rule (the eval's dominant failure) is stated.
    assert "adds a node to the workflow immediately" in ref


# ---------------------------------------------------------------------------
# The add-a-node drill: register a throwaway type, everything follows
# ---------------------------------------------------------------------------


class _DrillProbeNode(BaseNode):
    node_description = NodeTypeDescription(
        name="DrillProbe",
        display_name="Drill Probe",
        description="Throwaway node for the add-a-node drill",
        group=["transform"],
        inputs=[NodeInputDefinition(name="main", display_name="Input")],
        outputs=[NodeOutputDefinition(name="main", display_name="Output")],
        properties=[
            NodeProperty(
                display_name="Target", name="target", type="string", default=""
            ),
        ],
    )

    @property
    def type(self) -> str:
        return "DrillProbe"

    @property
    def description(self) -> str:
        return "Throwaway node for the add-a-node drill"

    async def execute(self, context, node_definition, input_data):
        raise NotImplementedError


def test_add_a_node_drill_zero_sdk_edits():
    # `from workflow_sdk import SDK_TYPES` is a point-in-time snapshot; the
    # live, derived view is the module attribute — use that for the drill.
    from src.engine import workflow_sdk

    register_all_nodes()
    assert "DrillProbe" not in workflow_sdk.SDK_TYPES
    node_registry.register(_DrillProbeNode)
    try:
        # Constructor list
        assert "DrillProbe" in workflow_sdk.SDK_TYPES
        # Prompt reference
        assert "DrillProbe(" in signature_reference()
        # Constructor + describe() inside a real script execution
        result = execute_workflow_script(
            "start = Start()\n"
            'probe = DrillProbe(target="x")\n'
            "start >> probe\n"
            'info = describe("DrillProbe")\n'
            "results.append(info)\n"
            "validate()\n"
        )
        assert result.error is None, result.error
        assert [n.type for n in result.workflow.nodes] == ["Start", "DrillProbe"]
        assert result.results[0]["type"] == "DrillProbe"
        assert any(p["name"] == "target" for p in result.results[0]["properties"])
    finally:
        # The registry has no public unregister; scrub directly so the
        # throwaway type can't leak into other tests in this process.
        node_registry._nodes.pop("DrillProbe", None)
        node_registry._instances.pop("DrillProbe", None)
    assert "DrillProbe" not in workflow_sdk.SDK_TYPES


# ---------------------------------------------------------------------------
# Teaching errors for the eval's observed misuses
# ---------------------------------------------------------------------------


def test_wiring_the_constructor_itself_raises_teaching_error():
    result = execute_workflow_script("s = Start()\nCron >> s\n")
    assert result.error is not None
    assert "CONSTRUCTOR" in result.error and "assign" in result.error.lower()
    assert "line 2" in result.error


def test_wiring_into_the_constructor_raises_teaching_error():
    result = execute_workflow_script("s = Start()\ns >> Cron\n")
    assert result.error is not None
    assert "CONSTRUCTOR" in result.error


def test_port_access_on_constructor_raises_teaching_error():
    result = execute_workflow_script("s = Start()\nIf.true >> s\n")
    assert result.error is not None
    assert "CONSTRUCTOR" in result.error


def test_import_raises_teaching_error():
    # Run 2 of the Sonnet eval: the only failures left were reflex
    # `import datetime` lines. The error must say what to do, not just
    # "__import__ not found".
    result = execute_workflow_script("import datetime\ns = Start()\n")
    assert result.error is not None
    assert "imports are disabled" in result.error
    assert "Delete the import statement" in result.error


def test_duplicate_trigger_validate_names_the_culprit():
    result = execute_workflow_script(
        'Cron(mode="cron", cronExpression="0 6 * * *")\n'
        'c = Cron(mode="cron", cronExpression="0 6 * * *")\n'
        'stop = StopAndError(errorType="error", message="x")\n'
        "c >> stop\n"
        "validate()\n"
    )
    assert result.error is not None
    assert "2 trigger nodes" in result.error
    assert "Cron(...) was called more than once" in result.error
    assert "exactly once" in result.error
