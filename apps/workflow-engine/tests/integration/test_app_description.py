"""An app's description is stored (create/update accept it) and returned by the detail endpoints (found by the
Create-UI E2E, T20: the studio sends it, the engine kept it, no client could read it back)."""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import new_project

pytestmark = pytest.mark.integration


def test_description_round_trips_on_create_get_and_update(api):
    P, _ = new_project(api, "desc")
    r = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}, "description": "A refunds dashboard"})
    assert r.status_code == 201 and r.json()["description"] == "A refunds dashboard", r.text
    A = r.json()["id"]
    assert api.get(f"/apps/{A}").json()["description"] == "A refunds dashboard"
    assert api.put(f"/apps/{A}", {"description": "new text"}).json()["description"] == "new text"
    assert api.get(f"/apps/{A}").json()["description"] == "new text"
    # the 1000-character cap is enforced, and an app without one reports null
    assert api.put(f"/apps/{A}", {"description": "x" * 1001}).status_code == 422
    B = api.post(f"/projects/{P}/apps", {"name": uniq("b"), "definition": {}}).json()["id"]
    assert api.get(f"/apps/{B}").json()["description"] is None
