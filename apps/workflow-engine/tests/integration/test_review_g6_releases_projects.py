"""UI-DESIGN-REVIEW.md fixes, group 6 — releases and project settings.

D10 release outcomes (draft envs have no active release, diff filters unchanged items, unversioned
apps are versioned or fail app_calls_resolve), D12 project dry run validates like the save,
C17 summary counts modules, C19 client.ts / openapi.json unknown environment, B7 rename preview.
"""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import SET_WF, new_project, save_app

pytestmark = pytest.mark.integration


def _route_wf(api, P, path="/a"):
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ 1 }}")).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": path, "handler": {"type": "workflow", "workflow_id": W}})
    return W


def test_d10_draft_environment_snapshots_are_never_active(api):
    P, S = new_project(api, "d10")
    _route_wf(api, P)
    r = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert r.status_code == 201, r.text
    d1 = r.json()
    assert (d1["is_active"], d1["status"], d1["serving"]) == (False, "snapshot", False)
    d2 = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    assert d2["number"] == 2 and d2["status"] == "snapshot"
    dev = api.get(f"/projects/{P}/releases?environment=dev").json()
    assert not any(x["is_active"] for x in dev) and {x["status"] for x in dev} == {"snapshot"}
    s = api.get(f"/projects/{P}/summary").json()
    assert s["active_releases"]["dev"] is None
    assert s["latest_releases"]["dev"]["release_id"] == d2["id"] and s["latest_releases"]["dev"]["number"] == 2
    # rollback is a release-environment operation
    r = api.post(f"/projects/{P}/environments/dev/rollback", {})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_a_release_environment"
    # promoting a snapshot activates it in the release env, which then serves it
    p1 = api.post(f"/projects/{P}/releases/{d2['id']}/promote", {"to_environment": "prod"}).json()
    assert (p1["is_active"], p1["status"], p1["serving"]) == (True, "active", True)
    assert api.get(f"/projects/{P}/summary").json()["active_releases"]["prod"]["release_id"] == p1["id"]


def test_d10_diff_filters_unchanged_workflows_and_breaking_check_uses_previous_snapshot(api):
    P, S = new_project(api, "d10d")
    W = _route_wf(api, P)
    W2 = _route_wf(api, P, "/b")
    rel1 = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    p1 = api.post(f"/projects/{P}/releases/{rel1['id']}/promote", {"to_environment": "prod"}).json()
    wf = api.get(f"/workflows/{W}").json()
    nodes = wf["definition"]["nodes"]
    nodes[1]["parameters"]["fields"][0]["value"] = "{{ 2 }}"
    assert api.put(f"/workflows/{W}", {"name": wf["name"], "nodes": nodes, "connections": wf["definition"]["connections"]}).status_code == 200
    rel2 = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    d = api.get(f"/projects/{P}/releases/{rel2['id']}/diff?against={p1['id']}").json()
    assert [x["workflow_id"] for x in d["workflows"]] == [W]      # W2 unchanged → filtered out
    pre = api.post(f"/projects/{P}/releases/{rel2['id']}/promote?dry_run=true", {"to_environment": "prod"}).json()
    assert [x["workflow_id"] for x in pre["diff"]["workflows"]] == [W] and W2
    # breaking_vs_active on a dev snapshot compares with the previous dev snapshot
    rid = next(r["id"] for r in api.get(f"/projects/{P}/routes").json() if r["path"] == "/b")
    api.delete(f"/projects/{P}/routes/{rid}")
    rel3 = api.post(f"/projects/{P}/releases", {"environment": "dev"}).json()
    br = next(c for c in rel3["checks"] if c["id"] == "breaking_vs_active")
    assert br["status"] == "warn" and br["details"]["against"] == rel2["id"]
    assert any(c["kind"] == "route_removed" for c in br["details"]["changes"])


def test_d10_unversioned_apps_are_versioned_into_the_release_or_fail_the_check(api):
    P, S = new_project(api, "d10a")
    _route_wf(api, P)
    src = f"export default function App(){{ fetch('/api/p/{S}/a'); return <div/> }}"
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    assert api.put(f"/apps/{A}", {"source_code": src}).status_code == 200      # draft only, no version
    assert api.get(f"/apps/{A}/versions").json() == []
    rel = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert rel.status_code == 201, rel.text
    snap = api.get(f"/projects/{P}/releases/{rel.json()['id']}").json()["snapshot"]
    pin = next(a for a in snap["apps"] if a["app_id"] == A)
    versions = api.get(f"/apps/{A}/versions").json()
    assert len(versions) == 1 and pin["version_id"] == versions[0]["id"]
    assert versions[0]["trigger"] == "release"
    # an app with no source at all cannot be released: the check fails and names the app
    B = api.post(f"/projects/{P}/apps", {"name": uniq("empty"), "definition": {}}).json()["id"]
    r = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "checks_failed"
    chk = next(c for c in r.json()["detail"]["checks"] if c["id"] == "app_calls_resolve")
    assert chk["status"] == "fail" and chk["details"]["unversioned"] == [B]
    assert "Save the app once before releasing" in chk["message"]
    # dry run never creates versions
    C = api.post(f"/projects/{P}/apps", {"name": uniq("c"), "definition": {}}).json()["id"]
    api.put(f"/apps/{C}", {"source_code": src})
    api.delete(f"/apps/{B}")
    pre = api.post(f"/projects/{P}/releases?dry_run=true", {"environment": "dev"}).json()
    assert any(a["app_id"] == C and a["version_id"] == -1 for a in pre["snapshot_apps"])
    assert api.get(f"/apps/{C}/versions").json() == []


def test_d12_project_dry_run_validates_like_the_real_update(api):
    P, S = new_project(api, "d12")
    api.post(f"/projects/{P}/secrets", {"key": "K", "value": "v", "environment": "prod", "type": "secret"})
    cases = [
        ({"environments": [{"name": "dev", "kind": "draft"}]}, 409, "environment_in_use"),
        ({"default_environment": "nope"}, 422, "unknown_environment"),
        ({"environments": [{"name": "Bad Name"}]}, 422, "invalid_environment"),
        ({"name": "   "}, 422, "invalid_name"),
        ({"settings": {"egress_allowlist": "x"}}, 422, "invalid_settings"),
        ({"slug": "BAD SLUG"}, 422, "invalid_slug"),
    ]
    for body, status, code in cases:
        real = api.put(f"/projects/{P}", body)
        dry = api.put(f"/projects/{P}?dry_run=true", body)
        assert (real.status_code, real.json()["detail"]["code"]) == (status, code), (body, real.text)
        assert (dry.status_code, dry.json()["detail"]["code"]) == (status, code), (body, dry.text)
    ok = api.put(f"/projects/{P}?dry_run=true", {"environments": [{"name": "dev", "kind": "draft"}, {"name": "prod", "kind": "release"},
                                                                    {"name": "stage", "kind": "release"}]})
    assert ok.status_code == 200 and [e["name"] for e in ok.json()["project"]["environments"]] == ["dev", "prod", "stage"]
    assert [e["name"] for e in api.get(f"/projects/{P}").json()["environments"]] == ["dev", "prod"]   # nothing saved


def test_b7_rename_preview_lists_published_and_referencing_apps(api):
    P, S = new_project(api, "b7")
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    save_app(api, A, f"export default function App(){{ fetch('/api/p/{S}/a'); return <div/> }}")
    new = f"{S}-renamed"
    r = api.put(f"/projects/{P}?dry_run=true", {"slug": new})
    assert r.status_code == 200 and [a["app_id"] for a in r.json()["affected_apps"]] == [A]
    assert r.json()["affected_apps"][0]["references_slug"] is True
    assert api.get(f"/projects/{P}").json()["slug"] == S


def test_c17_summary_counts_modules(api):
    P, _ = new_project(api, "c17")
    assert api.get(f"/projects/{P}/summary").json()["modules"] == 0
    api.post(f"/projects/{P}/modules", {"name": "a", "source": "def f(items):\n    return items\n"})
    s = api.get(f"/projects/{P}/summary").json()
    assert s["modules"] == 1 and set(s["counts"]) == {"apps", "workflows", "routes", "tables"}


def test_c19_client_and_openapi_reject_unknown_environment(api):
    P, _ = new_project(api, "c19")
    for path in (f"/projects/{P}/client.ts?environment=nope", f"/projects/{P}/openapi.json?environment=nope"):
        r = api.get(path)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_environment", path
    assert api.get(f"/projects/{P}/client.ts?environment=prod").status_code == 200
    assert api.get(f"/projects/{P}/openapi.json").status_code == 200
    # the studio's "default" keeps mapping to the project default (G23-style)
    assert api.get(f"/projects/{P}/client.ts?environment=default").status_code == 200
