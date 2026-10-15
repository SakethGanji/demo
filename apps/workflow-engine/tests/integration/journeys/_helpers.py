"""Shared helpers for UI-INTEGRATION.md §6 journeys (J1–J17)."""

from __future__ import annotations

import json
import re
import shutil
from urllib.parse import quote

import pytest

from ..conftest import start_set_workflow, uniq

needs_esbuild = pytest.mark.skipif(shutil.which("esbuild") is None, reason="[esbuild] precondition: esbuild on PATH")
needs_bwrap = pytest.mark.skipif(shutil.which("bwrap") is None, reason="[bwrap] precondition: bubblewrap installed")


def SET_WF(expr: str, name: str | None = None) -> dict:
    """The journeys' SET_WF(expr): Start → Set{answer: expr}, keepOnlySet."""
    return start_set_workflow(name=name, expr=expr, field="answer")


START = {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}}


def chain(name: str, node: dict) -> dict:
    """Start → node."""
    return {"name": name, "nodes": [START, node],
            "connections": [{"source_node": "Start", "target_node": node["name"], "source_output": "main", "target_input": "main"}],
            "settings": {}}


def new_project(api, prefix: str = "j", **extra) -> tuple[str, str]:
    slug = f"{prefix}-{uniq('s')}"
    r = api.post("/projects", {"name": f"{prefix} {uniq()}", "slug": slug, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"], slug


def app_token(api, slug: str) -> str:
    page = api.raw("GET", f"/a/{slug}").text
    return json.loads(re.search(r"window.__APP_TOKEN=(\"[^\"]+\")", page).group(1))


def save_app(api, aid: str, src: str, path: str = "App.tsx") -> dict:
    r = api.put(f"/apps/{aid}", {"source_code": src, "files": [{"path": path, "content": src}],
                                 "create_version": True, "version_trigger": "manual"})
    assert r.status_code == 200, r.text
    return r.json()


def qfilter(obj) -> str:
    return quote(json.dumps(obj))


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


SIMPLE_APP = "export default function App(){return <div>hi</div>}"


def pending(phase: int):
    """Strict xfail until the named phase lands (removed when it does)."""
    return pytest.mark.xfail(reason=f"needs phase {phase}", strict=True)
