"""Endpoint coverage: every route the studio uses or that phases 1–9 added must be exercised.

Runs last (file name) and only when the whole integration suite was collected — a filtered
run (-k / -m / single file) can't judge coverage and is skipped. Requests are recorded
client-side by StudioClient (httpx event hook), including SSE streams and raw calls.
"""

from __future__ import annotations

import re

import pytest

from .conftest import StudioClient

pytestmark = pytest.mark.integration

# Engine routes that neither the studio nor the new contract uses (BACKEND-DESIGN §1, "kept for
# external callers"). Listed explicitly so a new endpoint can never silently go untested.
NOT_REQUIRED = {
    ("GET", "/execution-stream/{workflow_id}"): "legacy GET stream; studio uses POST",
    ("DELETE", "/api/executions/{execution_id}"): "not used by the studio",
    ("POST", "/api/executions/{execution_id}/cancel"): "not used by the studio",
    ("POST", "/api/executions/{execution_id}/retry"): "not used by the studio",
    ("DELETE", "/api/executions"): "not used by the studio (destructive: clears all executions)",
    ("GET", "/api/files/validate"): "not used by the studio",
    ("PUT", "/api/credentials/{credential_id}"): "credentials are not used by the studio",
    ("DELETE", "/api/credentials/{credential_id}"): "credentials are not used by the studio",
    ("GET", "/api/folders/{folder_id}"): "folders are not used by the studio",
    ("PUT", "/api/folders/{folder_id}"): "folders are not used by the studio",
    ("DELETE", "/api/folders/{folder_id}"): "folders are not used by the studio",
    ("GET", "/api/workflows/{workflow_id}/versions/{version_id}"): "exercised indirectly; not used by the studio",
}


def _routes():
    from src.main import app  # noqa: WPS433 — import only to enumerate routes (no DB I/O)
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((m, path))
    return out


def _regex(path: str) -> re.Pattern:
    pat = re.sub(r"\{[^}:]+:path\}", ".+", path)
    pat = re.sub(r"\{[^}]+\}", "[^/]+", pat)
    return re.compile("^" + pat + "$")


def test_every_used_or_new_endpoint_is_exercised(request):
    import pathlib
    session = request.session
    here = pathlib.Path(__file__).parent
    expected = {str(p) for p in here.rglob("test_*.py")}
    collected = {str(item.path) for item in session.items}
    if session.config.option.keyword or session.config.option.markexpr or not expected <= collected:
        pytest.skip("coverage is only judged on a full integration run")
    hits = StudioClient.hits
    missing = []
    for method, path in _routes():
        if path.startswith(("/docs", "/redoc", "/openapi.json")) or (method, path) in NOT_REQUIRED:
            continue
        rx = _regex(path)
        if not any(m == method and rx.match(p) for m, p in hits):
            missing.append(f"{method} {path}")
    assert not missing, "endpoints never exercised by a journey/characterization test:\n  " + "\n  ".join(missing)
