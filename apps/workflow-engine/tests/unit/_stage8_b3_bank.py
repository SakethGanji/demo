"""Shared builders for the Stage 8 B3 tests: node/connection helpers and a few realistic bank workflows."""

from __future__ import annotations

from typing import Any

from src.engine.node_registry import register_all_nodes
from src.services.workflow_checker import ChildWorkflow, StaticLookups

register_all_nodes()

PROJECT = "proj_bank"


def N(name: str, type_: str = "Set", params: dict | None = None, **kw: Any) -> dict:
    return {"name": name, "type": type_, "parameters": params or {}, **kw}


def C(a: str, b: str, out: str = "main", inp: str = "main") -> dict:
    return {"source_node": a, "target_node": b, "source_output": out, "target_input": inp}


def setf(name: str, /, **fields: str) -> dict:
    """A Set node that adds ``fields`` (name -> value or expression)."""
    return N(name, "Set", {"fields": [{"name": k, "value": v} for k, v in fields.items()]})


def wf(nodes: list[dict], conns: list[dict], **extra: Any) -> dict:
    return {"nodes": nodes, "connections": conns, **extra}


def trigger(name: str = "Inputs", *fields: tuple, source: str | None = "fields") -> dict:
    """ExecuteWorkflowTrigger with ``fields`` = (name, type, required) tuples."""
    params: dict[str, Any] = {}
    if source:
        params["inputSource"] = source
    if source == "fields":
        params["fields"] = [{"name": n, "type": t, "required": r} for n, t, r in fields]
    return N(name, "ExecuteWorkflowTrigger", params)


def call(name: str, wid: str, version: Any = "latest", **params: Any) -> dict:
    return N(name, "ExecuteWorkflow", {"workflowId": wid, "version": version, **params})


def start(schema: dict | None = None, name: str = "Start") -> dict:
    return N(name, "Start", {"input_schema": schema or {"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}}}})


# ── realistic bank workflows ─────────────────────────────────────────────────────────────────


def eligibility_child() -> dict:
    """'Refund eligibility': (order_id, amount, segment?) -> + eligible, category, limit."""
    return wf(
        [
            trigger("Inputs", ("order_id", "string", True), ("amount", "number", True), ("segment", "string", False)),
            setf("Classify", category="standard", limit="500"),
        ],
        [C("Inputs", "Classify")],
    )


def notify_child() -> dict:
    """'Notify customer': (customer_id, channel, template) -> + sent, message_id."""
    return wf(
        [
            trigger("Inputs", ("customer_id", "string", True), ("channel", "string", True), ("template", "string", False)),
            setf("Send", sent="true", message_id="m-1"),
        ],
        [C("Inputs", "Send")],
    )


def lookups_with(children: dict[str, dict], *, name: str = "wf_parent", project: str = PROJECT, **extra: Any) -> StaticLookups:
    """Lookups where each child ``{id: definition}`` is published as version 1 (and is also the draft)."""
    kids = {
        cid: ChildWorkflow(cid, f"Child {cid}", project, draft=d, versions={1: d}, published_version=1)
        for cid, d in children.items()
    }
    return StaticLookups(children=kids, current_workflow_id=name, current_project_id=project, **extra)


def refund_parent(version: Any = "latest", inputs: dict | None = None, **call_params: Any) -> dict:
    """Refund request -> Refund eligibility (sub-workflow) -> branch on the child's output."""
    params = {"inputMode": "map", "inputs": inputs if inputs is not None else {"order_id": "{{ $json.order_id }}", "amount": "{{ $json.amount }}"}}
    params.update(call_params)
    return wf(
        [
            start({"type": "object", "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}, "customer_id": {"type": "string"}}}),
            call("Eligibility", "wf_elig", version, **params),
            setf("Decide", verdict="{{ $json.category }}"),
        ],
        [C("Start", "Eligibility"), C("Eligibility", "Decide")],
    )
