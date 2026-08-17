"""A 500 must carry the same CORS headers a 4xx does.

Without ``Access-Control-Allow-Origin`` on a 500, a browser never hands the
response to JS at all: fetch rejects with ``blocked by CORS policy`` /
``net::ERR_FAILED`` and the client sees a bare network failure. It cannot read
the status, so it cannot read the ``code`` either — which silently defeats the
whole "errors are problem+json, branch on ``code``" contract at the one moment
a caller most needs it. A 4xx behaves correctly, so the failure mode is
invisible unless the two are asserted side by side, which is what this file
does: the *same* route, the *same* request, differing only in which exception
the service raises.

The mechanism is the standard FastAPI/Starlette stack ordering.
``add_exception_handler(Exception, ...)`` does not install a normal handler:
Starlette pops it and gives it to ``ServerErrorMiddleware``, which sits
*outside* every user middleware — CORSMiddleware included. The 500 it writes is
therefore emitted above the CORS layer and never passes back through it. Only
an exception caught *below* CORS produces a response CORS can decorate.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from httpx import ASGITransport, AsyncClient

from app.api.errors import ProblemException
from app.features.discovery import repo as discovery_repo
from app.main import app

from conftest import auth, upload_inline

ORIGIN = "http://localhost:5174"
ROWS = [{"region": "EU", "amount": 100.5}, {"region": "US", "amount": 50.5}]

# One route, hit identically every time; only the exception raised underneath
# it changes. Facets needs no fixture data, so nothing but the error path is
# under test.
FACETS = "/api/v1/datasets/facets"


def _acao(response) -> str | None:
    return response.headers.get("access-control-allow-origin")


@pytest_asyncio.fixture
async def tolerant_client():
    """A client that reports the app's 500 instead of re-raising it.

    ``ServerErrorMiddleware`` writes its response and then re-raises so the
    server can log/crash-report; httpx's default ``raise_app_exceptions=True``
    would surface that instead of the response, hiding the headers this file is
    about. Turning it off lets the test read what a browser would receive.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def boom(monkeypatch):
    """Make the facets route fail with *exc* on the next request."""

    def _install(exc: Exception):
        async def _raise(*_a, **_kw):
            raise exc

        monkeypatch.setattr(discovery_repo, "facets", _raise)

    return _install


async def test_a_500_carries_the_same_cors_headers_as_a_400(
        tolerant_client, admin_id, boom):
    """The control and the regression, on one route, in one test."""
    headers = {**auth(admin_id), "Origin": ORIGIN}

    # ---- control: a handled 4xx. This has always worked. ----
    boom(ProblemException(400, "deliberate 400", code="deliberate-failure"))
    control = await tolerant_client.get(FACETS, headers=headers)
    assert control.status_code == 400, control.text
    assert _acao(control) in (ORIGIN, "*"), "4xx control lost its CORS headers"

    # ---- regression: an unhandled exception. ----
    boom(RuntimeError("deliberate 500"))
    err = await tolerant_client.get(FACETS, headers=headers)
    assert err.status_code == 500, err.text
    assert _acao(err) in (ORIGIN, "*"), (
        "a 500 without Access-Control-Allow-Origin is an unreadable network "
        "error in the browser, so the client can never see status or code"
    )

    # Identical treatment, not merely "present on both".
    assert _acao(err) == _acao(control)
    assert (err.headers.get("access-control-expose-headers")
            == control.headers.get("access-control-expose-headers"))


async def test_a_500_body_is_problem_json_with_a_code_and_no_internals(
        tolerant_client, admin_id, boom):
    """The envelope a client branches on must survive the 500 path too."""
    boom(RuntimeError("secret internal detail"))

    r = await tolerant_client.get(FACETS, headers={**auth(admin_id), "Origin": ORIGIN})

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 500
    assert body["code"] == "internal_server_error"
    assert body["title"] == "Internal Server Error"
    assert body["instance"] == FACETS
    # No traceback, no exception text, no module paths.
    assert "secret internal detail" not in r.text
    assert "RuntimeError" not in r.text
    assert "Traceback" not in r.text


async def test_a_500_still_carries_the_request_id_a_bug_report_quotes(
        tolerant_client, admin_id, boom):
    """X-Request-Id is exposed precisely so a UI can quote a *failed* request.

    It is set by RequestIDMiddleware on the way out, so it only survives if the
    500 is turned into a response beneath that middleware rather than above it.
    """
    boom(RuntimeError("deliberate 500"))

    r = await tolerant_client.get(FACETS, headers={**auth(admin_id), "Origin": ORIGIN})

    assert r.status_code == 500
    assert r.headers.get("x-request-id")
    exposed = {h.strip().lower() for h in
               r.headers.get("access-control-expose-headers", "").split(",") if h.strip()}
    assert "x-request-id" in exposed


async def test_an_unhandled_exception_does_not_escape_the_app(client, admin_id, boom):
    """Handled, not merely papered over.

    The default client re-raises whatever the ASGI app lets escape. If this
    returns a response at all, the exception was caught below CORS — which is
    the only place a catch produces CORS headers.
    """
    boom(RuntimeError("deliberate 500"))

    r = await client.get(FACETS, headers={**auth(admin_id), "Origin": ORIGIN})

    assert r.status_code == 500
    assert _acao(r) in (ORIGIN, "*")


async def test_preflight_for_the_failing_route_still_succeeds(tolerant_client, boom):
    """A preflight is answered by CORSMiddleware and never reaches the route,
    so a broken route must not make the browser refuse to send the request."""
    boom(RuntimeError("deliberate 500"))

    pre = await tolerant_client.request("OPTIONS", FACETS, headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-user-id",
    })

    assert pre.status_code == 200, pre.text
    assert _acao(pre) in (ORIGIN, "*")
    assert "GET" in pre.headers["access-control-allow-methods"]


async def test_a_failing_download_route_keeps_cors_too(
        tolerant_client, admin_id, monkeypatch):
    """The download routes hand back a streamed response rather than a JSON
    body, so they are the ones most likely to bypass the normal response path.

    A failure *before* the first byte must still become a decorated 500. (Once
    bytes are on the wire the status line is already sent and nothing can add a
    header — that case is the client's read error, not a CORS question.)
    """
    from app.features.files import api as files_api

    ds = (await upload_inline(tolerant_client, admin_id, json.dumps(ROWS)))["dataset_id"]
    url = f"/api/v1/datasets/{ds}/download"
    headers = {**auth(admin_id), "Origin": ORIGIN}

    # Control: the same route, a handled 4xx (unknown format).
    control = await tolerant_client.get(url, params={"format": "nope"}, headers=headers)
    assert 400 <= control.status_code < 500, control.text
    assert _acao(control) in (ORIGIN, "*")

    async def _raise(*_a, **_kw):
        raise RuntimeError("deliberate 500")

    monkeypatch.setattr(files_api, "download_dataset", _raise)
    err = await tolerant_client.get(url, headers=headers)

    assert err.status_code == 500, err.text
    assert _acao(err) == _acao(control)
    assert err.json()["code"] == "internal_server_error"
