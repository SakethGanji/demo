"""The typed interface client is deterministic and follows the contracts (DESIGN-STAGE5-UIS section 6.3)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.engine.node_registry import register_all_nodes
from src.services.interface_client_codegen import form_data_type, generate_interface_client, pascal
from src.services.workflow_checker import StaticLookups, check_workflow

register_all_nodes()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SNAPSHOT = FIXTURES / "interface_client" / "refunds.client.ts"


def load(name: str) -> tuple[dict, dict]:
    d = json.loads((FIXTURES / "workflows" / name).read_text())
    return {"nodes": d["nodes"], "connections": d["connections"], "interface": d["interface"]}, d["interface"]


def refunds() -> str:
    definition, iface = load("refund_triage_ui.json")
    shapes = check_workflow(definition, lookups=StaticLookups())["shapes_by_node"]
    return generate_interface_client(definition, iface, "default", "wf_1", "proj_default", shapes)


def test_output_matches_the_snapshot():
    out = refunds()
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(out)
    assert out == SNAPSHOT.read_text()


def test_output_is_deterministic():
    assert refunds() == refunds()


def test_types_come_from_the_contracts():
    out = refunds()
    assert 'export interface RefundsInput { order_id: string; amount: number; reason: "not_received" | "damaged" | "wrong_item" | "other"; email: string }' in out
    assert "export interface ManagerReviewDecision { comment?: string; form_data: { note?: string } }" in out
    assert 'export type TaskNode = "Manager review"' in out
    for verb in ("startRun(", "getRun(", "watchRun(", "listTasks(", "getTask<", "approveTask<", "rejectTask<"):
        assert verb in out
    assert '"/submit"' in out and "Idempotency-Key" in out and "/decide" in out


def test_no_secret_or_internal_names_leak_into_the_client():
    out = refunds()
    for forbidden in ("Classify", "Issue refund", "REFUND_CLASSIFY", "__REFUND_API_URL__", "password", "secret"):
        assert forbidden not in out


def test_a_chat_interface_has_no_run_verbs():
    definition, iface = load("chat_support_ui.json")
    out = generate_interface_client(definition, iface, "default")
    assert "startRun(" not in out and "listTasks(" in out and "export type TaskNode = string" in out


def test_pascal_names():
    assert pascal("refund-triage") == "RefundTriage" and pascal("Manager review") == "ManagerReview"
    assert pascal("2fa") == "N2fa" and pascal("") == "Workflow" and pascal("---") == "Workflow"


def test_form_data_types():
    assert form_data_type([]) == "Record<string, never>" and form_data_type(None) == "Record<string, never>"
    assert form_data_type([{"name": "n", "type": "number", "required": True}, {"name": "ok", "type": "checkbox"},
                           {"name": "kind", "type": "select", "options": [{"value": "a"}, "b"]}, {"name": "has space", "type": "text"}]) == \
        '{ n: number; ok?: boolean; kind?: "a" | "b"; "has space"?: string }'


def test_a_field_named_like_any_is_not_corrupted():
    definition, iface = load("refund_triage_ui.json")
    definition["nodes"][0]["parameters"]["input_schema"]["properties"]["company"] = {"type": "string"}
    assert "company?: string" in generate_interface_client(definition, iface, "default")
