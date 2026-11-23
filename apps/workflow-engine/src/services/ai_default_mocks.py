"""Stage 6 default mocks (DESIGN-STAGE6-AI-BUILDER §6.5): ``test_defaults.mocks`` for the effectful nodes of an AI-built workflow.

A test run never calls an outside system: an effectful node without a mock fails with ``TEST_UNMOCKED_EFFECT``. The spec's tests mock the
steps they care about; these defaults cover the rest so a test of one branch does not fail on a step of another.

Pure and deterministic. ``fill`` NEVER overrides a mock that is already in ``test_defaults.mocks`` (and a test's own mocks win at run time
anyway), so spec mocks are never touched.

``approval_items`` is the Python twin of the studio's ``lib/approvalMocks.ts::sampleApprovalItems``; both are pinned to the shared fixture
``tests/fixtures/ai_builder/approval_items.json`` by a parity test.

========================= ==========================================================================================
Node                      Default mock
========================= ==========================================================================================
HttpRequest + api_ref     the catalog's first 2xx mock option (documented example, else ``example_from_shape``)
HttpRequest, no api_ref   ``{http: {status: 200, body: {}}}`` + warning
LLMChat / AIAgent         ``{items: [example_from_shape(outputSchema)]}``; without a schema ``{response, text}``
Approval                  decision-only ``{port: "approved", approval: {decision, ...}}`` (the run adds the real incoming item)
Postgres/MongoDB/Neo4j/   ``{items: []}`` + warning
DataTable
SendEmail                 ``{items: [{"sent": true}]}``
ExecuteWorkflow           one item shaped like the child's output when the child is known (``subworkflow_outputs``, Stage 8
                          section 6.2 / 9.6); otherwise ``{items: [{}]}`` + warning
any other effectful type  ``{items: [{}]}`` + warning
========================= ==========================================================================================
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from ..engine.effects import effect_of
from .mock_gen import example_from_shape, first_success, mock_options, subworkflow_mock

APPROVAL_PORTS: tuple[str, ...] = ("approved", "rejected", "timed_out")

DB_TYPES = ("Postgres", "MongoDB", "Neo4j", "DataTable")


def approval_items(port: str = "approved") -> list[dict[str, Any]]:
    """The item an Approval emits on resume, as the studio's Tests panel pre-fills it (``sampleApprovalItems``).
    An unknown port is treated as ``approved``."""
    port = port if port in APPROVAL_PORTS else "approved"
    return [{"approval": {"decision": port, "comment": "", "form_data": {}, "decided_by": "system:timeout" if port == "timed_out" else "tester"}}]


def approval_mock(port: str = "approved") -> dict[str, Any]:
    """Decision-only: test mode emits each REAL incoming item plus this ``approval`` on ``port`` (a fixed ``items`` list would drop the
    fields the later steps read)."""
    port = port if port in APPROVAL_PORTS else "approved"
    return {"port": port, "approval": approval_items(port)[0]["approval"]}


def _schema_of(params: Mapping[str, Any]) -> dict[str, Any] | None:
    for key in ("outputSchema", "output_schema"):
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                raw = json.loads(raw)
            except ValueError:
                continue
        if isinstance(raw, dict) and raw:
            return raw
    return None


def _operation(params: Mapping[str, Any], operations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None) -> Mapping[str, Any] | None:
    ref = params.get("api_ref")
    if not isinstance(ref, Mapping):
        return None
    return ((operations or {}).get(str(ref.get("spec_id"))) or {}).get(str(ref.get("operation_id")))


def default_mock_for(node: Mapping[str, Any], operations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
                     subworkflow_outputs: Mapping[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """``(mock, warning)`` for one node dict. ``mock`` is None for a node with no outside effect. ``operations`` is
    ``project_operations(...)``: ``{spec_id: {operation_id: Operation}}``. ``subworkflow_outputs`` is ``{child workflow id: output Shape}``
    (``workflow_io.output``) for the sub-workflows the project can call."""
    ntype = str(node.get("type"))
    params = node.get("parameters") if isinstance(node.get("parameters"), Mapping) else {}
    name = node.get("name")
    if effect_of(ntype) == "none":
        return None, None
    if ntype == "HttpRequest":
        op = _operation(params, operations)
        if op is not None:
            opts = mock_options(dict(op))
            ok = next((o for o in opts if o["category"] == "ext_success"), None)
            if ok is not None:
                return copy.deepcopy(ok["mock"]), None
            first = first_success(dict(op))
            status = first[0] if first else 200
            return {"http": {"status": status, "body": {}}}, f"'{name}': the API operation documents no example reply; its default mock is an empty body"
        if isinstance(params.get("api_ref"), Mapping):
            return {"http": {"status": 200, "body": {}}}, f"'{name}': its API operation was not found in the catalog; default mock is an empty 200"
        return {"http": {"status": 200, "body": {}}}, f"'{name}' calls a URL, not a catalog API; its default mock is an empty 200 reply"
    if ntype in ("LLMChat", "AIAgent"):
        schema = _schema_of(params)
        if schema is not None:
            example = example_from_shape(_as_shape(schema))
            if ntype == "AIAgent":  # the agent's item is {response, structured: <schema>}
                return {"items": [{"response": "example response", "structured": example}]}, None
            return {"items": [example]}, None
        return {"items": [{"response": "example response", "text": "example response"}]}, None
    if ntype == "Approval":
        return approval_mock("approved"), None
    if ntype in DB_TYPES:
        return {"items": []}, f"'{name}' ({ntype}): default mock returns no rows; add a test mock for the rows your rules need"
    if ntype == "SendEmail":
        return {"items": [{"sent": True}]}, None
    if ntype == "ExecuteWorkflow":
        child = params.get("workflowId")
        if isinstance(child, str) and child in (subworkflow_outputs or {}):
            return subworkflow_mock(subworkflow_outputs[child], str(name))
        return {"items": [{}]}, f"'{name}' runs a sub-workflow; its default mock is one empty item"
    return {"items": [{}]}, f"'{name}' ({ntype}) talks to the outside; its default mock is one empty item"


def _as_shape(schema: dict[str, Any]) -> dict[str, Any]:
    from ..engine import shapes as sh

    return sh.normalize(schema, "declared")


def fill(defn: Mapping[str, Any], operations: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
         subworkflow_outputs: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """A copy of ``defn`` whose ``test_defaults.mocks`` has a mock for every enabled effectful node that lacks one, and the
    warnings (``[{node, message}]``) for guesses the reviewer should see. Existing default mocks are never changed."""
    out = copy.deepcopy(dict(defn))
    defaults = out.get("test_defaults")
    defaults = dict(defaults) if isinstance(defaults, Mapping) else {}
    mocks = dict(defaults.get("mocks") or {})
    warnings: list[dict[str, str]] = []
    for node in out.get("nodes") or []:
        if not isinstance(node, Mapping) or not node.get("name") or node.get("disabled"):
            continue
        name = str(node["name"])
        if name in mocks:
            continue
        mock, warning = default_mock_for(node, operations, subworkflow_outputs)
        if mock is None:
            continue
        mocks[name] = mock
        if warning:
            warnings.append({"node": name, "message": warning})
    defaults["mocks"] = mocks
    out["test_defaults"] = defaults
    return out, warnings
