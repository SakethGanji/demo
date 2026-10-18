"""R11: the ``interface`` key survives every dump / load path of a workflow definition (DESIGN-STAGE5-UIS section 14)."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.db.models import WorkflowModel
from src.engine.types import Workflow
from src.repositories.workflow_repository import WorkflowRepository, definition_to_workflow, workflow_to_definition
from src.schemas.workflow import WorkflowCreateRequest, WorkflowUpdateRequest
from src.services.workflow_service import WorkflowService

pytestmark = pytest.mark.anyio
FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage_ui.json").read_text())
INTERFACE = FIXTURE["interface"]


class FakeSession:
    def __init__(self, row):
        self.row = row

    async def get(self, model, key):
        return self.row if key == self.row.id else None

    async def commit(self):
        pass

    async def refresh(self, row):
        pass


def make_row(definition) -> WorkflowModel:
    now = datetime.now()
    return WorkflowModel(id="wf_1", name="Refund triage (UI)", draft_definition=definition, created_at=now, updated_at=now, active=False)


def base_definition(with_interface=True) -> dict:
    d = {"nodes": [{"name": "Refund request", "type": "FormTrigger", "parameters": {}}], "connections": [], "settings": {}}
    if with_interface:
        d["interface"] = copy.deepcopy(INTERFACE)
    return d


def test_definition_dump_and_load_keep_the_interface():
    wf = definition_to_workflow(base_definition(), "n", "wf_1")
    assert wf.interface == INTERFACE
    assert workflow_to_definition(wf)["interface"] == INTERFACE
    assert "interface" not in workflow_to_definition(definition_to_workflow(base_definition(False), "n", "wf_1"))


def test_the_dump_is_a_copy():
    wf = definition_to_workflow(base_definition(), "n", "wf_1")
    out = workflow_to_definition(wf)
    out["interface"]["slug"] = "changed"
    assert wf.interface["slug"] == "refunds"


def test_empty_interface_is_not_dumped():
    assert "interface" not in workflow_to_definition(Workflow(name="n", nodes=[], connections=[], interface={}))


async def test_a_save_without_an_interface_never_drops_the_stored_one():
    row = make_row(base_definition())
    repo = WorkflowRepository(FakeSession(row))
    plain = Workflow(name="Refund triage (UI)", nodes=[], connections=[], id="wf_1")      # e.g. a tests-only save
    stored = await repo.update("wf_1", plain)
    assert row.draft_definition["interface"] == INTERFACE and stored.workflow.interface == INTERFACE


async def test_set_interface_replaces_and_removes_it_without_touching_the_rest():
    row = make_row(base_definition())
    repo = WorkflowRepository(FakeSession(row))
    changed = {**INTERFACE, "title": "New title"}
    await repo.set_interface("wf_1", changed)
    assert row.draft_definition["interface"]["title"] == "New title" and row.draft_definition["nodes"][0]["name"] == "Refund request"
    await repo.set_interface("wf_1", None)
    assert "interface" not in row.draft_definition and row.draft_definition["nodes"]
    assert await repo.set_interface("nope", None) is None


async def test_set_interface_writes_a_new_dict_so_the_jsonb_change_is_detected():
    row = make_row(base_definition(False))
    before = row.draft_definition
    await WorkflowRepository(FakeSession(row)).set_interface("wf_1", INTERFACE)
    assert row.draft_definition is not before and "interface" not in before


async def test_update_workflow_carries_the_interface_through_a_full_save():
    row = make_row(base_definition())
    repo = WorkflowRepository(FakeSession(row))
    service = WorkflowService.__new__(WorkflowService)
    service._workflow_repo = repo
    service._node_service = SimpleNamespace(compute_node_io=lambda t, p: {"inputs": [], "inputCount": 0, "outputs": [], "outputCount": 0,
                                                                          "inputStrategy": None, "outputStrategy": None, "group": []},
                                            _node_registry=SimpleNamespace(get_node_type_info=lambda t: None))
    service._node_registry = None
    req = WorkflowUpdateRequest(name="Renamed")
    detail = await service.update_workflow("wf_1", req)
    assert row.draft_definition["interface"] == INTERFACE
    assert detail.definition["interface"] == INTERFACE


def test_the_api_definition_includes_the_interface_only_when_there_is_one():
    service = WorkflowService.__new__(WorkflowService)
    service._node_service = SimpleNamespace(compute_node_io=lambda t, p: {"inputs": [], "inputCount": 0, "outputs": [], "outputCount": 0,
                                                                          "inputStrategy": None, "outputStrategy": None, "group": []},
                                            _node_registry=SimpleNamespace(get_node_type_info=lambda t: None))
    assert service._workflow_to_dict(Workflow(name="n", nodes=[], connections=[], interface=INTERFACE))["interface"] == INTERFACE
    assert "interface" not in service._workflow_to_dict(Workflow(name="n", nodes=[], connections=[]))


def test_create_request_still_builds_a_workflow_without_an_interface():
    req = WorkflowCreateRequest(name="n", nodes=[{"name": "a", "type": "Start", "parameters": {}}])
    assert req.to_workflow().interface is None
