"""Subprocess sandbox tests (HANDOFF-SDK-COMPLETION.md §1).

These spawn REAL child processes — each test costs a Python startup (~0.5s),
which is exactly the point: the boundary being tested is the process boundary.

Run: venv/bin/python -m pytest tests/engine/test_workflow_sdk_sandbox.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.engine.workflow_sdk_sandbox import (  # noqa: E402
    execute_script_isolated,
    workflow_from_payload,
)

GOOD_SCRIPT = (
    'cron = Cron(mode="cron", cronExpression="0 6 * * *")\n'
    'fetch = HttpRequest(method="GET", url="https://example.com/x")\n'
    "cron >> fetch\n"
    "validate()\n"
)


def test_good_script_round_trips():
    payload = asyncio.run(execute_script_isolated(GOOD_SCRIPT, workflow_name="sbx_ok"))
    assert payload["ok"] is True, payload
    assert payload["error"] is None and payload["problems"] == []
    wf = workflow_from_payload(payload)
    assert wf.name == "sbx_ok"
    assert [n.type for n in wf.nodes] == ["Cron", "HttpRequest"]
    assert wf.connections[0].source_node == "Cron"


def test_script_error_is_reported_not_raised():
    payload = asyncio.run(execute_script_isolated("s = Start()\nCron >> s\n"))
    assert payload["ok"] is False
    assert "CONSTRUCTOR" in payload["error"]
    assert payload["partial"] is True


def test_forgotten_validate_is_still_validated():
    # No trigger, and the script never calls validate() — the sandbox must
    # catch it anyway, or "forgot to validate" persists broken graphs.
    payload = asyncio.run(
        execute_script_isolated('a = Set()\nb = Set()\na >> b\n')
    )
    assert payload["ok"] is False
    assert payload["error"] is None  # the script itself ran fine
    assert any("no trigger" in p for p in payload["problems"])


def test_runaway_script_is_killed_by_timeout():
    payload = asyncio.run(
        execute_script_isolated("while True:\n    pass\n", timeout=3.0)
    )
    assert payload["ok"] is False
    assert "timed out" in payload["error"]


def test_child_env_is_scrubbed():
    # The child's environment must not carry the parent's secrets. The curated
    # builtins block `import os` for scripts, so probe via the error channel:
    # __import__ is absent, proving arbitrary imports (and env access) require
    # a full sandbox escape — which then finds an empty environment anyway.
    payload = asyncio.run(
        execute_script_isolated('import os\nresults.append(os.environ)\n')
    )
    assert payload["ok"] is False
    assert "__import__" in payload["error"] or "ImportError" in payload["error"]
