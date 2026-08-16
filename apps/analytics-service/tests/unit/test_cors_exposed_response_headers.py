"""A browser must be allowed to READ the headers our responses depend on.

CORS strips every response header from JS except the seven safelisted ones
unless the server names it in ``Access-Control-Expose-Headers``. The app named
exactly one header (``X-Request-Id``), which is the one header no feature
depends on, while every header a client has to *act* on was invisible:

  - ``Content-Disposition`` carries the filename on all five download
    responses. A cross-origin fetch/blob download that cannot read it saves the
    file as the URL's last path segment — "download" or "1" — instead of
    "orders_v3.csv". Every downloaded file in the UI lands with a wrong name,
    and re-downloading a second version silently overwrites the first.
  - ``Location`` and ``Upload-Offset`` are how the TUS protocol works: the
    client reads Location from the 201 to know where to PATCH, and Upload-Offset
    to know where to resume. Hidden, resumable upload does not degrade — it
    cannot start.

Same-origin callers (curl, the test suite's ASGI client, a dev proxy) see these
headers regardless, so this breaks only in a real browser against a deployed
origin — the one environment no test covers. Hence this test, which asserts on
the exposed list rather than on the headers themselves.

It also pins middleware ordering: CORS is installed after `install_middleware`
so it runs outermost, which is what keeps the headers on a short-circuited
*error* response. If someone moves that call, a cross-origin client stops being
able to read the problem+json body of a 404 at all.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

ORIGIN = "https://console.example.com"


def _exposed(response) -> set[str]:
    raw = response.headers.get("access-control-expose-headers", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


@pytest.fixture
async def cross_origin_client():
    """No DB: the routes exercised here 404 in the router, before any handler."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_download_filename_header_is_readable_cross_origin(cross_origin_client):
    r = await cross_origin_client.get("/api/v1/no-such-route",
                                      headers={"Origin": ORIGIN})

    assert "content-disposition" in _exposed(r)


async def test_resumable_upload_headers_are_readable_cross_origin(cross_origin_client):
    r = await cross_origin_client.get("/api/v1/no-such-route",
                                      headers={"Origin": ORIGIN})

    exposed = _exposed(r)
    # Without these two a TUS client cannot find the upload URL or resume it.
    assert {"location", "upload-offset"} <= exposed
    assert "content-length" in exposed, "progress reporting on large downloads"


async def test_request_id_stays_readable_cross_origin(cross_origin_client):
    """The one header that was already exposed must not be lost in the widening."""
    r = await cross_origin_client.get("/api/v1/no-such-route",
                                      headers={"Origin": ORIGIN})

    assert "x-request-id" in _exposed(r)
    assert r.headers.get("x-request-id")


async def test_cors_headers_survive_a_short_circuited_error_response(cross_origin_client):
    """CORS runs outermost, so an error a browser needs to read still carries it."""
    r = await cross_origin_client.get("/api/v1/no-such-route",
                                      headers={"Origin": ORIGIN})

    assert r.status_code == 404
    assert r.headers.get("access-control-allow-origin") in ("*", ORIGIN)
    assert "content-disposition" in _exposed(r)
