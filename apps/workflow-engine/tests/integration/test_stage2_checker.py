"""Stage 2 checker over HTTP: POST /workflows/{id}/check and the publish block (CONTRACTS-STAGE2 §3)."""

from __future__ import annotations

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration


def wf(bad: bool) -> dict:
    field = "{{ $json.nope }}" if bad else "{{ $json.mode }}"
    return {"name": uniq("s2chk"), "settings": {},
            "nodes": [{"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
                      {"name": "A", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "o", "value": field}]},
                       "position": {"x": 0, "y": 0}}],
            "connections": [{"source_node": "Start", "target_node": "A", "source_output": "main", "target_input": "main"}]}


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_check_returns_findings_for_the_stored_draft_and_never_saves(api):
    wid = make(api, wf(bad=True))
    r = api.post(f"/workflows/{wid}/check", {})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["code"] for f in body["findings"]] == ["WF002"]
    assert body["findings"][0]["node"] == "A" and set(body) == {"findings", "shapes_by_node", "coverage"}
    # a posted definition is checked instead of the draft, and the draft stays as it was
    ok = api.post(f"/workflows/{wid}/check", {"definition": {k: wf(bad=False)[k] for k in ("nodes", "connections")}}).json()
    assert ok["findings"] == []
    assert [f["code"] for f in api.post(f"/workflows/{wid}/check", {}).json()["findings"]] == ["WF002"]


def test_check_unknown_workflow_is_404(api):
    assert api.post("/workflows/wf_does_not_exist/check", {}).status_code == 404


def test_publish_is_blocked_by_error_findings_and_allowed_without(api):
    wid = make(api, wf(bad=True))
    r = api.post(f"/workflows/{wid}/publish", {"message": "x"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"] == "Workflow has errors" and [f["code"] for f in detail["findings"]] == ["WF002"]
    assert api.put(f"/workflows/{wid}", wf(bad=False)).status_code == 200
    assert api.post(f"/workflows/{wid}/publish", {"message": "x"}).status_code == 200
