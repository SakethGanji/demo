"""Characterization: app builder, publish, public pages, API tester (BACKEND-DESIGN §1)."""

from __future__ import annotations

import base64
import json
import re
import shutil

import pytest

from .conftest import uniq

pytestmark = pytest.mark.integration

needs_esbuild = pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")

APP_TSX = """/** Test app. */
/** Root component. */
export default function App() {
  return <div className="p-4">Hello</div>
}
"""


def _new_app(api, name: str | None = None) -> dict:
    # useAppActions.ts:101
    r = api.post("/apps", {"name": name or uniq("app"), "definition": {}})
    assert r.status_code == 201, r.text
    return r.json()


def _save(api, aid: str, src: str = APP_TSX) -> dict:
    # AppBuilderShell.tsx:137
    r = api.put(f"/apps/{aid}", {
        "source_code": src,
        "files": [{"path": "App.tsx", "content": src}],
        "create_version": True,
        "version_trigger": "manual",
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_app_crud_and_versions(api):
    app = _new_app(api)
    aid = app["id"]
    assert set(app) >= {"id", "name", "definition", "active", "workflow_ids", "api_execution_ids", "files",
                        "current_version", "slug", "access", "access_password_set", "embed_enabled"}
    assert app["access"] == "private" and app["active"] is False
    assert any(a["id"] == aid for a in api.get("/apps").json())

    # rename (AppBuilderShell.tsx:85)
    assert api.put(f"/apps/{aid}", {"name": "renamed"}).json()["name"] == "renamed"
    saved = _save(api, aid)
    assert saved["current_version"]["version_number"] == 1
    assert saved["files"][0]["path"] == "App.tsx"
    _save(api, aid, APP_TSX.replace("Hello", "Hello 2"))

    vers = api.get(f"/apps/{aid}/versions").json()
    assert [v["version_number"] for v in vers] == [1, 2]  # ascending
    v1 = next(v for v in vers if v["version_number"] == 1)
    assert set(v1) >= {"id", "version_number", "trigger", "label", "created_at"}
    # VersionHistory.tsx:63
    r = api.patch(f"/apps/{aid}/versions/{v1['id']}", {"label": "first"})
    assert r.status_code == 200 and r.json()["label"] == "first"
    detail = api.get(f"/apps/{aid}/versions/{v1['id']}").json()
    assert "Hello</div>" in detail["source_code"]
    files = api.get(f"/apps/{aid}/versions/{v1['id']}/files").json()
    assert files[0]["path"] == "App.tsx"
    # AppBuilderShell.tsx:160
    r = api.post(f"/apps/{aid}/versions/{v1['id']}/revert")
    assert r.status_code == 200, r.text
    # api_execution_ids (AppBuilderChatPanel.tsx:173)
    assert api.put(f"/apps/{aid}", {"api_execution_ids": []}).json()["api_execution_ids"] == []

    assert api.delete(f"/apps/{aid}").json() == {"success": True}
    assert api.get(f"/apps/{aid}").status_code == 404


def test_app_missing(api):
    assert api.get("/apps/app_nope").status_code == 404
    assert api.put("/apps/app_nope", {"name": "x"}).status_code == 404
    assert api.delete("/apps/app_nope").status_code == 404


@needs_esbuild
def test_publish_public_page_and_unpublish(api):
    app = _new_app(api)
    aid = app["id"]
    _save(api, aid)
    slug = uniq("slug")
    # PublishDialog.tsx:44
    r = api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"})
    assert r.status_code == 200, r.text
    pub = r.json()
    assert pub["slug"] == slug and pub["active"] is True and pub["public_url"].endswith(f"/a/{slug}")
    page = api.raw("GET", f"/a/{slug}")
    assert page.status_code == 200
    assert "window.__APP_TOKEN=" in page.text
    assert "frame-ancestors" in page.headers["content-security-policy"]
    assert '<meta http-equiv="Content-Security-Policy"' not in page.text
    assert '<script src="https://cdn.tailwindcss.com"' not in page.text
    js = re.search(rf"/a/{slug}/_assets/([0-9a-f]+)\.js", page.text).group(1)
    asset = api.raw("GET", f"/a/{slug}/_assets/{js}.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    css = api.raw("GET", f"/a/{slug}/_assets/{js}.css")
    assert css.status_code == 200 and ".p-4{" in css.text
    assert api.get(f"/apps/{aid}").json()["published_version"] is not None

    # PublishDialog.tsx:81
    assert api.post(f"/apps/{aid}/unpublish").status_code == 200
    assert api.raw("GET", f"/a/{slug}").status_code == 404


@needs_esbuild
def test_publish_compiles_utilities_without_executing_app_tailwind_config(api):
    aid = _new_app(api)["id"]
    r = api.put(f"/apps/{aid}", {
        "source_code": APP_TSX,
        "files": [
            {"path": "App.tsx", "content": APP_TSX},
            {"path": "tailwind.config.js", "content": 'throw new Error("App config must never execute on the host")'},
        ],
        "create_version": True,
    })
    assert r.status_code == 200, r.text
    slug = uniq("css")
    r = api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"})
    assert r.status_code == 200, r.text
    page = api.raw("GET", f"/a/{slug}")
    asset_path = re.search(r'href="([^"]+\.css)"', page.text).group(1)
    css = api.raw("GET", asset_path)
    assert css.status_code == 200 and ".p-4{" in css.text


@needs_esbuild
def test_password_app_unlock(api):
    aid = _new_app(api)["id"]
    _save(api, aid)
    slug = uniq("pw")
    assert api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "password", "access_password": "hunter22"}).status_code == 200
    assert api.raw("GET", f"/a/{slug}").status_code == 401
    bad = api.raw("POST", f"/a/{slug}/_unlock", data={"password": "nope"})
    assert bad.status_code == 401
    ok = api.raw("POST", f"/a/{slug}/_unlock", data={"password": "hunter22"}, follow_redirects=False)
    assert ok.status_code == 303 and "app_unlock" in ok.headers.get("set-cookie", "")


def test_private_app_is_404(api):
    assert api.raw("GET", f"/a/{uniq('nope')}").status_code == 404


def test_api_tester_flow_and_replay(api, upstream):
    # ApiTesterPanel.tsx:433
    r = api.post("/api-tester/execute", {"name": "echo", "method": "POST", "url": f"{upstream}/things",
                                         "headers": {"X-A": "1"}, "body": json.dumps({"q": 1})})
    assert r.status_code == 201, r.text
    ex = r.json()
    assert ex["response_status"] == 200
    echoed = json.loads(base64.b64decode(ex["response_body_b64"]))
    assert echoed["body"] == {"q": 1} and echoed["headers"]["x-a"] == "1"
    eid = ex["id"]
    assert any(e["id"] == eid for e in api.get("/api-tester/executions").json())
    assert api.get(f"/api-tester/executions/{eid}").json()["url"] == f"{upstream}/things"
    assert api.patch(f"/api-tester/executions/{eid}", {"name": "renamed"}).json()["name"] == "renamed"

    if shutil.which("esbuild"):
        aid = _new_app(api)["id"]
        _save(api, aid)
        api.put(f"/apps/{aid}", {"api_execution_ids": [eid]})
        slug = uniq("rp")
        api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"})
        page = api.raw("GET", f"/a/{slug}").text
        token = json.loads(re.search(r"window.__APP_TOKEN=(\"[^\"]+\")", page).group(1))
        assert f"{upstream}/things" in page  # api map
        rr = api.raw("POST", f"/a/{slug}/api-tester/replay/{eid}", json={"method": "POST", "body": json.dumps({"q": 2})},
                     headers={"Authorization": f"Bearer {token}"})
        assert rr.status_code == 200 and rr.json()["body"] == {"q": 2}
        assert api.raw("POST", f"/a/{slug}/api-tester/replay/{eid}", json={}).status_code == 401

    assert api.delete(f"/api-tester/executions/{eid}").status_code == 200
    assert api.get(f"/api-tester/executions/{eid}").status_code == 404


@needs_esbuild
def test_public_workflow_proxy_auth(api):
    """Auth + allowlist on /a/{slug}/api/workflow/{id}/run (public_apps.py:373)."""
    aid = _new_app(api)["id"]
    _save(api, aid)
    slug = uniq("wfp")
    api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"})
    page = api.raw("GET", f"/a/{slug}").text
    token = json.loads(re.search(r"window.__APP_TOKEN=(\"[^\"]+\")", page).group(1))
    assert api.raw("POST", f"/a/{slug}/api/workflow/wf_x/run", json={}).status_code == 401
    r = api.raw("POST", f"/a/{slug}/api/workflow/wf_x/run", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_folders_and_credentials_still_served(api):
    f = api.post("/folders", {"name": uniq("f"), "team_id": "default"})
    assert f.status_code == 201, f.text
    assert any(x["id"] == f.json()["id"] for x in api.get("/folders").json())
    c = api.post("/credentials", {"name": uniq("c"), "type": "generic", "data": {"k": "v"}, "team_id": "default"})
    assert c.status_code == 201, c.text
    assert api.get(f"/credentials/{c.json()['id']}").status_code == 200
