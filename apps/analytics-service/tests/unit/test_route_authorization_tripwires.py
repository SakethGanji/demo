"""Structural guards on authorization: every route resolves a caller, and every
row-returning read resolves masking.

These are TRIPWIRES, not behaviour tests. They assert a property of the codebase
rather than of a request, and they are deliberately derived from the live route
table and the source, so a NEW endpoint has to confront the rule rather than
slip past it. Same shape as ``tests/test_control_plane.py``, which enumerates
every JSONB column instead of listing known-bad ones.

They exist because both rules were broken repeatedly, not once:

* **Four TUS routes had no authentication at all.** ``HEAD``/``PATCH``/
  ``DELETE``/``status`` after upload creation took no principal and never called
  ``ensure_dataset_permission``, so any authenticated user could drive another
  team's upload — and ``DELETE`` unconditionally marked the version failed, so
  it could demote a *ready* version belonging to someone else.

* **Masking was enforced on about three read surfaces and absent from about
  ten.** ``/sql`` returned unmasked sensitive columns *and persisted them* as a
  fetchable artifact; the column explorer, ``/duplicates`` and ``/missing``
  returned raw values; chart render called ``run_view`` without a principal so a
  view-backed chart bypassed the masking the identical view applied elsewhere.

Ten separate patches would have fixed those ten endpoints. Neither of these
rules was ever *disagreed* with — it just was not applied uniformly, and nothing
noticed. A tripwire is what turns "we fixed the instances" into "the class
cannot come back", which is the only version of this worth the maintenance.

If one of these fires on code you just wrote, the presumption is that your code
is wrong. If it genuinely is not, add it to the allow-list **with a comment
saying why** — an unexplained entry defeats the whole file.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from app.features.auth.deps import Principal
from app.main import app

APP_DIR = pathlib.Path(__file__).resolve().parents[2] / "app"

# Framework/documentation routes and the health probe. None of these read tenant
# data, and /health is deliberately unauthenticated so a load balancer can call
# it without credentials.
INFRASTRUCTURE_PATHS = {
    "/health", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
}

# The MCP JSON-RPC surface is mounted as an ASGI app, not declared as FastAPI
# routes, so it has no `Depends(get_principal)` signature for this test to see.
# It is NOT unauthenticated. `app/features/mcp/identity.py` runs the service's
# own `get_principal` per HTTP request AND re-binds the principal per inbound
# JSON-RPC message, precisely because the streamable-HTTP session manager can
# run a later caller's work inside the task that opened the session — binding
# once at the ASGI layer would serve every caller as the session's creator.
# That is a stronger guarantee than a route dependency, not a weaker one.
MCP_MOUNT_PATHS = {"/api/v1/mcp"}


def _api_routes():
    """(method, path, endpoint) for every declared API route."""
    out = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        endpoint = getattr(route, "endpoint", None)
        if not methods or endpoint is None:
            continue
        if route.path in INFRASTRUCTURE_PATHS or route.path in MCP_MOUNT_PATHS:
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            out.append((method, route.path, endpoint))
    return out


def test_every_api_route_resolves_a_caller() -> None:
    """No route may skip identity — that is how four TUS routes went unguarded.

    A route without a ``Principal`` parameter cannot have checked who is calling,
    so it cannot have applied team scoping, RBAC, or the 404-hides-existence
    rule. The TUS routes proved this is not hypothetical: they were reachable by
    any authenticated user for any team's upload.
    """
    unguarded = []
    for method, path, endpoint in _api_routes():
        try:
            signature = inspect.signature(endpoint)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        has_principal = any(
            parameter.annotation is Principal
            or "Principal" in str(parameter.annotation)
            for parameter in signature.parameters.values()
        )
        if not has_principal:
            unguarded.append(f"{method} {path}")

    assert not unguarded, (
        "These routes take no Principal, so they cannot be enforcing "
        "authorization:\n  " + "\n  ".join(sorted(unguarded)) +
        "\n\nAdd `principal: Principal = Depends(get_principal)` and the "
        "appropriate ensure_dataset_permission call. If the route genuinely "
        "needs no caller, add it to INFRASTRUCTURE_PATHS with a comment."
    )


def test_the_mcp_mount_is_the_only_route_without_a_route_level_principal() -> None:
    """Pin the one exemption, so a second one cannot be added silently.

    Without this, the allow-list above is a place to quietly hide a new
    unauthenticated endpoint: append a path, and the first test goes green.
    """
    declared = {route.path for route in app.routes if getattr(route, "methods", None)}
    assert MCP_MOUNT_PATHS <= declared, (
        "The MCP mount path changed; update MCP_MOUNT_PATHS and re-read "
        "app/features/mcp/identity.py to confirm identity is still per-message."
    )
    assert INFRASTRUCTURE_PATHS | MCP_MOUNT_PATHS == {
        "/health", "/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect",
        "/api/v1/mcp",
    }, (
        "Someone widened the authorization exemption list. Every entry must be "
        "a framework route, the health probe, or the MCP mount — nothing that "
        "reads tenant data."
    )


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

# Modules that return dataset cell values to a caller and therefore must resolve
# masking. Keyed to the source file so the check is about the code, not a live
# request: a masking bug is a *missing call*, which no single response reveals.
ROW_RETURNING_MODULES = [
    "features/explorer/service.py",
    "features/explorer/data_quality.py",
    "features/data_accelerator/services/rowdiff.py",
]

MASKING_CALLS = {"resolve_masking", "ensure_raw_access", "mask_rows", "guard_masked_query"}


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@pytest.mark.parametrize("relative_path", ROW_RETURNING_MODULES)
def test_a_module_that_returns_dataset_rows_resolves_masking(relative_path: str) -> None:
    """Every row-returning module must reference the masking helpers.

    Coarse on purpose. It cannot prove masking is applied *correctly* — the
    behavioural tests in tests/test_pii_masking.py and
    tests/test_explorer_masking_surfaces.py do that. What it catches is the
    failure that actually happened ten times: a module that returns rows and
    never mentions masking at all, which no reviewer noticed because the absence
    of a call is invisible in a diff that adds an endpoint.
    """
    source_file = APP_DIR / relative_path
    assert source_file.exists(), f"{relative_path} moved; update this tripwire"
    called = _called_names(ast.parse(source_file.read_text()))
    assert called & MASKING_CALLS, (
        f"{relative_path} returns dataset rows but calls none of "
        f"{sorted(MASKING_CALLS)}. Every other row-returning surface resolves "
        "masking; an endpoint that does not is a silent data leak — the caller "
        "gets values they should not see and nothing in the response says so."
    )


def test_the_masking_exemption_is_evaluated_within_one_team() -> None:
    """``may_see_raw`` must take a team, and must not walk memberships itself.

    The bug this pins: the predicate used to OR ``DATASET_READ_SENSITIVE`` over
    *every* membership, so admin of any one team unmasked PII in every team the
    caller could read. The signature is the tripwire — a team-less predicate
    cannot be team-scoped, whatever its body does.
    """
    from app.shared import masking

    parameters = list(inspect.signature(masking.may_see_raw).parameters)
    assert len(parameters) >= 2, (
        "may_see_raw takes no team argument, so it cannot be scoping the "
        "exemption to the dataset's team. See app/features/auth/permissions.py: "
        "'A user's authority is evaluated *within a team*.'"
    )
    assert not hasattr(masking, "_has_permission"), (
        "_has_permission was the membership-walking helper that caused the "
        "cross-tenant leak. Principal.can(team_id, permission) replaces it and "
        "already handles the superuser bypass."
    )
