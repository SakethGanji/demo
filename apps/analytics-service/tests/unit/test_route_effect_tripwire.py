"""Every mutating route declares whether it actually changes anything.

This is a TRIPWIRE, not a behaviour test — the same shape as
``test_route_authorization_tripwires.py``. It asserts a property of the
codebase, derived from the live route table, so a NEW endpoint has to confront
the rule rather than slip past it.

The rule exists because ``GET /datasets/{id}/usage`` derived ``writes`` from
the HTTP method, and the studio's ordinary row read is
``POST .../sheets/{s}/query`` — a POST only because a QuerySpec does not fit in
a query string. Opening a dataset in the UI reported ``writes: 1`` against a
dataset nobody had touched, and the Library panel published that number.

Fixing the eight read-shaped POSTs that existed would have fixed the
instances. What makes the CLASS not come back is this test: the classification
lives in one table (``app/shared/request_effects.py``), and the table must
match the route table exactly, in both directions. A ninth read-shaped POST
cannot be added without someone answering "does it persist anything?", because
until they do, this fails.

The answer is about persistence, not naming: ``/sql`` and ``/pivot`` sound like
reads and register artifacts; ``/charts/{id}/render`` sounds like a run and
deliberately persists nothing. If this fires on a route you just added, read
the handler and follow it into the service — the verdict is whether a row is
INSERTed or a blob is written AND registered, not whether the endpoint feels
read-only.
"""

from __future__ import annotations

from app.main import app
from app.shared.request_effects import READ, READ_ACTIONS, ROUTE_EFFECTS, effect_of


def _mutating_routes() -> set[tuple[str, str]]:
    """(method, declared path) for every route the audit middleware records.

    GET is excluded on purpose: the middleware audits only mutating methods
    plus data egress (``/download`` and sample GETs), and egress is counted by
    path, not by effect.
    """
    out = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS", "GET"}):
            out.add((method, route.path))
    return out


def test_every_mutating_route_declares_its_effect() -> None:
    """No route may be classified by accident."""
    missing = _mutating_routes() - set(ROUTE_EFFECTS)
    assert not missing, (
        "These routes have no declared effect, so `usage` and `timeline` would "
        "fall back to calling them writes:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(missing)) +
        "\n\nRead the handler and add an entry to app/shared/request_effects.py. "
        "READ means it persists NOTHING: no row, no registered artifact. If it "
        "writes a parquet and registers it (as /sql and /pivot do), it is a WRITE "
        "even though it reads like a query."
    )


def test_no_declared_effect_outlives_its_route() -> None:
    """The other direction: a stale entry silently classifies nothing.

    Without this, a renamed route leaves its old entry behind looking like the
    rule is still applied — and the renamed route falls through to WRITE.
    """
    stale = set(ROUTE_EFFECTS) - _mutating_routes()
    assert not stale, (
        "These entries name routes that no longer exist:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in sorted(stale)) +
        "\n\nA route was renamed or removed; update app/shared/request_effects.py."
    )


def test_the_read_shaped_routes_are_the_ones_we_think_they_are() -> None:
    """Pin the READ set itself, so it cannot be widened quietly.

    The failure this guards against is the opposite of the original bug and
    worse: marking a route READ hides real mutations from the usage counters
    AND from the dataset timeline, which is a history view. An addition here
    should be a deliberate, reviewed line.
    """
    reads = {f"{m} {p}" for (m, p), effect in ROUTE_EFFECTS.items() if effect == READ}
    assert reads == {
        # Row reads: a POST because the QuerySpec is a body.
        "POST /api/v1/datasets/{dataset_id}/versions/{version_number}/query",
        "POST /api/v1/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/query",
        "POST /api/v1/datasets/{dataset_id}/views/{view_id}/run",
        # Computed answers that persist nothing.
        "POST /api/v1/datasets/{dataset_id}/charts/{chart_id}/render",
        "POST /api/v1/datasets/{dataset_id}/transformations/compile",
        "POST /api/v1/datasets/{dataset_id}/transformations/{definition_id}/preview",
        "POST /api/v1/joins/preview",
        "POST /api/v1/profile",
    }, (
        "The read-shaped set changed. If you added one, confirm in the handler "
        "that it INSERTs no row and registers no artifact — a mutation marked "
        "READ disappears from the dataset's history entirely."
    )
    # What the read-models actually pass to SQL is derived from the same table.
    assert set(READ_ACTIONS) == reads


def test_an_unknown_route_is_reported_as_a_write() -> None:
    """The default during the window where the two tables disagree.

    Over-reporting a write is recoverable — someone sees an event they did not
    expect. Under-reporting one means a change to a dataset left no history,
    which is the failure this whole surface exists to prevent.
    """
    assert effect_of("POST", "/api/v1/datasets/{dataset_id}/not-a-real-route") == "write"
    # ...and the lookup is method-sensitive: PUT and DELETE on one path can
    # legitimately differ, so a match on path alone would be wrong.
    assert effect_of("post", "/api/v1/profile") == READ
    assert effect_of("DELETE", "/api/v1/profile") == "write"
