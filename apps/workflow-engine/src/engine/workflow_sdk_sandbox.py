"""Subprocess isolation for SDK script execution (HANDOFF-SDK-COMPLETION.md §1).

``execute_workflow_script`` runs untrusted, model-written Python through an
in-process ``exec`` with a curated ``__builtins__`` dict. That whitelist is a
lint, not a boundary — attribute-chain escapes work — which is fine for our own
tests but not for scripts submitted by a running agent. This module is the
boundary: the script executes in a short-lived child process with

  - a hard wall-clock timeout (killed, not joined),
  - a scrubbed environment (no API keys, no DB URLs — the app's secrets are
    simply absent from the child, so even a full sandbox escape reads nothing),
  - a capped, JSON-only stdout channel back to the parent.

Isolation is a *policy of the caller*, not of the SDK: the agent-facing tool
always goes through here; tests and ``test_run`` keep calling
``execute_workflow_script`` in-process, which is also the core this child
process wraps — so the fast suite and the sandbox can never drift apart.

Wire format (child stdout, one JSON object):
  {ok, error, partial, problems, workflow: {name, nodes, connections},
   node_meta, results}
``ok`` is true only when the script raised nothing AND post-hoc validation
found no problems — a script that never calls validate() gets validated here
anyway, so "forgot to validate" cannot slip a broken graph through.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .types import Connection, NodeDefinition, Workflow

# Parent of ``src`` — the import root the child needs on PYTHONPATH.
_APP_ROOT = str(Path(__file__).resolve().parents[2])

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_OUTPUT_BYTES = 2_000_000  # a graph payload is KBs; MBs means something hostile


def _error_payload(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "partial": True,
        "problems": [],
        "workflow": {"name": "sdk_script", "nodes": [], "connections": []},
        "node_meta": {},
        "results": [],
    }


def result_to_payload(result: Any, problems: list[str]) -> dict[str, Any]:
    """Serialize an ExecutionResult (+ post-hoc validation problems) to JSON-safe."""
    wf = result.workflow
    return {
        "ok": result.error is None and not problems,
        "error": result.error,
        "partial": result.partial,
        "problems": problems,
        "workflow": {
            "name": wf.name if wf else "sdk_script",
            "nodes": [
                {"name": n.name, "type": n.type, "parameters": n.parameters}
                for n in (wf.nodes if wf else [])
            ],
            "connections": [
                {
                    "source_node": c.source_node,
                    "target_node": c.target_node,
                    "source_output": c.source_output,
                    "target_input": c.target_input,
                }
                for c in (wf.connections if wf else [])
            ],
        },
        "node_meta": result.node_meta,
        "results": result.results,
    }


def workflow_from_payload(payload: dict[str, Any]) -> Workflow:
    """Rebuild the engine dataclasses from a child payload (for persistence)."""
    wf = payload["workflow"]
    return Workflow(
        name=wf["name"],
        nodes=[
            NodeDefinition(
                name=n["name"], type=n["type"], parameters=n.get("parameters") or {}
            )
            for n in wf["nodes"]
        ],
        connections=[
            Connection(
                source_node=c["source_node"],
                target_node=c["target_node"],
                source_output=c.get("source_output", "main"),
                target_input=c.get("target_input", "main"),
            )
            for c in wf["connections"]
        ],
    )


async def execute_script_isolated(
    script: str,
    workflow_name: str = "sdk_script",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Run one SDK script in a fresh child process; always returns a payload dict."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.engine.workflow_sdk_sandbox",
        cwd=_APP_ROOT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            # Deliberately NOT os.environ: no API keys, no DB URLs, no .env
            # spillover. The child only needs to import the app's code.
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONPATH": _APP_ROOT,
        },
    )
    request = json.dumps({"script": script, "workflow_name": workflow_name}).encode()
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(request), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return _error_payload(
            f"script execution timed out after {timeout:.0f}s and was killed — "
            f"scripts must terminate (no unbounded while-loops or busy waits)"
        )

    if len(stdout) > MAX_OUTPUT_BYTES:
        return _error_payload(
            f"script produced {len(stdout)} bytes of output "
            f"(cap {MAX_OUTPUT_BYTES}) — rejected"
        )
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-500:]
        return _error_payload(
            f"sandbox process exited with code {proc.returncode}: {tail}"
        )
    try:
        return json.loads(stdout.decode())
    except (ValueError, UnicodeDecodeError):
        return _error_payload("sandbox returned non-JSON output — rejected")


def _child_main() -> int:
    """Child entrypoint: JSON request on stdin, JSON payload on stdout."""
    try:
        request = json.loads(sys.stdin.read())
        script = request["script"]
        workflow_name = request.get("workflow_name") or "sdk_script"
    except Exception as exc:  # noqa: BLE001
        json.dump(_error_payload(f"bad sandbox request: {exc}"), sys.stdout)
        return 0

    from .workflow_sdk import _Draft, _validate_draft, execute_workflow_script

    result = execute_workflow_script(script, workflow_name=workflow_name)
    problems: list[str] = []
    if result.error is None and result.workflow is not None:
        # The script may never have called validate(); a broken graph must not
        # slip through on that omission.
        problems = _validate_draft(
            _Draft(
                nodes=list(result.workflow.nodes),
                connections=list(result.workflow.connections),
            )
        )
    json.dump(result_to_payload(result, problems), sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(_child_main())
