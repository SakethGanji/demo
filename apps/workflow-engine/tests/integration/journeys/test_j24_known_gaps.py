"""Journey J24: engine gaps found while writing the J20-J23 suites. Each is a strict xfail that flips to a
hard failure (remove the marker) the moment the gap is closed. See also test_j20 ``test_09``."""

from __future__ import annotations

import pytest

from ..conftest import http_workflow
from ._helpers import new_project

pytestmark = pytest.mark.integration


@pytest.mark.xfail(strict=True, reason="GAP: settings.egress_allowlist is enforced only for broker calls (python modules / studio.nodes); "
                                       "a workflow HttpRequest node in the same project may call any host")
def test_project_egress_allowlist_also_applies_to_workflow_http_nodes(api, upstream):
    P, _ = new_project(api, "gap-egress")
    assert api.put(f"/projects/{P}", {"settings": {"egress_allowlist": ["only.example.com"]}}).status_code == 200
    W = api.post(f"/projects/{P}/workflows", http_workflow(f"{upstream}/echo")).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["status"] != "success", "workflow HttpRequest reached a host outside the project's egress allowlist"


@pytest.mark.xfail(strict=True, reason="GAP: a 'number' project secret accepts any string (no numeric validation on create/update)")
def test_number_secret_must_be_numeric(api):
    P, _ = new_project(api, "gap-num")
    r = api.post(f"/projects/{P}/secrets", {"key": "LIMIT", "value": "not-a-number", "type": "number", "environment": "dev"})
    assert r.status_code == 422
