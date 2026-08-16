"""Every status this service returns has a title in the problem envelope.

``title`` is the human-readable half of RFC 7807 and the only part of the body
a generic error toast shows. A status missing from ``_STATUS_TITLES`` falls
through to the literal string "Error", so the two statuses the file transfer
layer raises outside the common set — 460 (TUS checksum mismatch) and 507
(out of disk) — rendered as ``{"title": "Error", "code": "error"}``: a body
that says nothing about a failure the caller can actually act on (retry the
chunk / free space).

The raise sites pass an explicit ``code``; this pins that the derived half of
the envelope is right too, and that an explicit code still wins over the
status-derived default.
"""

from __future__ import annotations

import json

from app.api.errors import problem_response


def _body(response) -> dict:
    return json.loads(bytes(response.body).decode())


def test_the_tus_checksum_mismatch_status_has_a_title_and_a_derived_code():
    body = _body(problem_response(460, "Checksum mismatch", "/api/v1/tus/abc"))
    assert body["title"] == "Checksum Mismatch"
    assert body["code"] == "checksum_mismatch"
    assert body["status"] == 460


def test_the_out_of_disk_status_has_a_title_and_a_derived_code():
    body = _body(problem_response(507, "No space", "/api/v1/tus/"))
    assert body["title"] == "Insufficient Storage"
    assert body["code"] == "insufficient_storage"


def test_an_explicit_code_still_overrides_the_status_derived_one():
    """The raise sites use hyphenated codes; the title map must not fight them."""
    body = _body(problem_response(460, "Checksum mismatch", "/api/v1/tus/abc",
                                  code="checksum-mismatch"))
    assert body["code"] == "checksum-mismatch"
    assert body["title"] == "Checksum Mismatch"


def test_an_unmapped_status_still_degrades_to_the_generic_title():
    """The fallback has to stay — a status nobody listed must not raise."""
    body = _body(problem_response(418, "I am a teapot", "/api/v1/x"))
    assert body["title"] == "Error"
    assert body["code"] == "error"
