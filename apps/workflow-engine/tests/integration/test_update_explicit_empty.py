"""PUT /workflows/{id}: an explicit empty list replaces, only an absent field keeps (glass editor parity T34.22).

Settings merge by key, so the editor saving ``settings.sticky_notes`` keeps the workflow's other settings.
"""

from __future__ import annotations

import pytest

from .conftest import start_set_workflow

pytestmark = pytest.mark.integration


def _def(api, wid):
    return api.get(f"/workflows/{wid}").json()["definition"]


def test_explicit_empty_nodes_removes_every_step(api):
    wf = start_set_workflow()
    wid = api.post("/workflows", wf).json()["id"]
    assert len(_def(api, wid)["nodes"]) == 2

    # absent fields: unchanged
    r = api.put(f"/workflows/{wid}", {"name": wf["name"] + "-x"})
    assert r.status_code == 200, r.text
    d = _def(api, wid)
    assert [n["name"] for n in d["nodes"]] == ["Start", "Set"]
    assert len(d["connections"]) == 1

    # explicit []: every step and wire removed
    r = api.put(f"/workflows/{wid}", {"name": wf["name"], "nodes": [], "connections": []})
    assert r.status_code == 200, r.text
    assert r.json()["definition"]["nodes"] == []
    d = _def(api, wid)
    assert d["nodes"] == [] and d["connections"] == []


def test_explicit_empty_connections_removes_the_wires(api):
    wf = start_set_workflow()
    wid = api.post("/workflows", wf).json()["id"]
    r = api.put(f"/workflows/{wid}", {**wf, "connections": []})
    assert r.status_code == 200, r.text
    d = _def(api, wid)
    assert len(d["nodes"]) == 2 and d["connections"] == []


def test_settings_merge_by_key(api):
    wf = {**start_set_workflow(), "settings": {"max_execution_time": 30}}
    wid = api.post("/workflows", wf).json()["id"]
    notes = [{"id": "sticky-1", "position": {"x": 1, "y": 2}, "content": "hi", "color": "yellow"}]
    r = api.put(f"/workflows/{wid}", {**wf, "settings": {"sticky_notes": notes}})
    assert r.status_code == 200, r.text
    s = _def(api, wid)["settings"]
    assert s["max_execution_time"] == 30
    assert s["sticky_notes"] == notes
    # the notes never run: the workflow still runs its steps
    run = api.post(f"/workflows/{wid}/run").json()
    assert run["status"] == "success"
    # replacing the key with [] clears the notes
    api.put(f"/workflows/{wid}", {**wf, "settings": {"sticky_notes": []}})
    assert _def(api, wid)["settings"]["sticky_notes"] == []
