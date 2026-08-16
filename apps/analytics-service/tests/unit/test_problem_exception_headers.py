"""A ``ProblemException`` may carry response headers without 500ing.

``ProblemException.__init__`` collected every keyword but ``code`` into
``self.extra``, and ``extra`` is rendered as problem+json *body* fields. So
``headers=`` fell into it, and ``_http_exception_handler`` then called::

    problem_response(status, detail, path,
                     code=...,
                     headers=exc.headers,      # None — the base was never told
                     **exc.extra)              # {"headers": {...}} — again

which is ``TypeError: problem_response() got multiple values for keyword
argument 'headers'``. Inside an exception handler that is not a nice 500 either:
the client asked for a 401 and got an unhandled server error.

Nothing passes ``headers=`` to ``ProblemException`` today, so this was latent —
but it is squarely in the path of the outstanding SSO/JWT work on
``get_principal``, where a 401 with ``WWW-Authenticate`` is the obvious thing to
raise, and it is the kind of trap that costs an afternoon because the symptom
(a 500) points nowhere near the cause (a kwarg name collision).

Fixed by forwarding ``headers`` to the base class rather than by popping it in
the handler: that makes ``exc.headers`` correct for every reader — Starlette's
own ``ExceptionMiddleware`` included — instead of only for the one handler that
happens to unpack ``extra``.
"""

from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.api.errors import ProblemException, _http_exception_handler

AUTH = {"WWW-Authenticate": 'Bearer realm="analytics"'}


def _request(path: str = "/api/v1/datasets") -> Request:
    return Request({"type": "http", "method": "GET", "path": path,
                    "query_string": b"", "headers": []})


def test_headers_reach_the_base_class_not_the_body():
    exc = ProblemException(401, "Bearer token required",
                           code="token-required", headers=AUTH, realm="analytics")

    assert exc.headers == AUTH
    # `extra` is the problem+json body; a transport header does not belong in it.
    assert "headers" not in exc.extra
    assert exc.extra == {"realm": "analytics"}


async def test_the_handler_renders_it_instead_of_raising_typeerror():
    """The actual regression: this call used to raise, not return a response."""
    exc = ProblemException(401, "Bearer token required",
                           code="token-required", headers=AUTH, realm="analytics")

    response = await _http_exception_handler(_request(), exc)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="analytics"'
    assert response.headers["content-type"].startswith("application/problem+json")
    body = json.loads(bytes(response.body))
    assert body["code"] == "token-required"
    assert body["detail"] == "Bearer token required"
    assert body["realm"] == "analytics"
    # The header is NOT smuggled into the document.
    assert "headers" not in body


async def test_a_problem_exception_without_headers_is_unchanged():
    """The overwhelmingly common case keeps rendering exactly as before."""
    exc = ProblemException(400, "Dataset has 3 sheets — specify one",
                           code="sheet-selection-required",
                           sheets=["Revenue", "Expenses", "Headcount"])

    assert exc.headers is None
    response = await _http_exception_handler(_request(), exc)

    assert response.status_code == 400
    body = json.loads(bytes(response.body))
    assert body["code"] == "sheet-selection-required"
    assert body["sheets"] == ["Revenue", "Expenses", "Headcount"]


@pytest.mark.parametrize("status,expected_title", [
    (401, "Unauthorized"), (429, "Too Many Requests"), (503, "Service Unavailable"),
])
async def test_headers_compose_with_every_status_that_wants_them(status, expected_title):
    """401/429/503 are the three that carry a header by convention."""
    exc = ProblemException(status, "nope", headers={"Retry-After": "30"})

    response = await _http_exception_handler(_request(), exc)

    assert response.status_code == status
    assert response.headers["retry-after"] == "30"
    assert json.loads(bytes(response.body))["title"] == expected_title
