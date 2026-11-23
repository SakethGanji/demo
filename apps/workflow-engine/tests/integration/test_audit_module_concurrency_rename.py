"""AUDIT-BACKEND-FIXES #5 — module saves are serialised (no lost update) and a rename that would break
PythonStep nodes / other modules' imports is refused with the references listed."""

from __future__ import annotations

import threading

import pytest

from .conftest import uniq
from .journeys._helpers import new_project

pytestmark = pytest.mark.integration

V1 = "def calc(items):\n    return [{'v': 1}]\n"


def _mod(api, P, name, src=V1):
    r = api.post(f"/projects/{P}/modules", {"name": name, "source": src})
    assert r.status_code == 201, r.text
    return r.json()["module"]


def test_concurrent_saves_with_the_same_base_cannot_both_win(api):
    P, _ = new_project(api, "b4")
    m = _mod(api, P, "shared")
    results: list[int] = []
    barrier = threading.Barrier(6)

    def save(i: int) -> None:
        import httpx

        with httpx.Client(base_url=api.base_url, timeout=60) as c:
            barrier.wait()
            r = c.put(f"/api/projects/{P}/modules/{m['id']}",
                      json={"source": f"def calc(items):\n    return [{{'v': {i + 10}}}]\n", "base_sha": m["source_sha"]})
            results.append(r.status_code)

    ts = [threading.Thread(target=save, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 409, 409, 409, 409, 409], results
    final = api.get(f"/projects/{P}/modules/{m['id']}").json()
    assert final["source_sha"] != m["source_sha"]


def test_rename_is_refused_while_a_workflow_step_or_module_import_references_the_name(api):
    P, _ = new_project(api, "b4r")
    m = _mod(api, P, "steps")
    other = _mod(api, P, "user_mod", "import steps\n\ndef f():\n    return steps.calc([])\n")
    wf = {"name": uniq("w"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Step", "type": "PythonStep", "parameters": {"module": "steps", "function": "calc"}, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "Step", "source_output": "main", "target_input": "main"}]}
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    for dry in ("?dry_run=true", ""):
        r = api.put(f"/projects/{P}/modules/{m['id']}{dry}", {"source": V1, "name": "renamed"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "module_referenced", (dry, r.text)
        refs = r.json()["detail"]["references"]
        assert {"kind": "workflow", "id": W, "node": "Step"}.items() <= next(x for x in refs if x["kind"] == "workflow").items()
        assert any(x["kind"] == "module" and x["name"] == "user_mod" for x in refs)
    assert api.get(f"/projects/{P}/modules/{m['id']}").json()["name"] == "steps"
    # the importing module can be renamed (nothing references it) and the unreferenced one too
    assert api.put(f"/projects/{P}/modules/{other['id']}", {"source": other["source"] if "source" in other else "x = 1\n",
                                                           "name": "user_mod2"}).status_code == 200
    free = _mod(api, P, "free")
    assert api.put(f"/projects/{P}/modules/{free['id']}", {"source": V1, "name": "free2"}).json()["module"]["name"] == "free2"
    # without a rename, saving a referenced module is unaffected
    assert api.put(f"/projects/{P}/modules/{m['id']}", {"source": V1, "name": "steps"}).status_code == 200
