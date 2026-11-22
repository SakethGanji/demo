"""P19 (C §1.6): the coverage gate. F-X4a: EVERY route, whatever its method, declares either `require(action, object)`
or the explicit `public(reason)` marker, decided route by route (the old row-returning-GET path regex let 130 GETs
through unchecked). A route with neither is `uncovered`: logged at startup; `enforce` refuses to start, and
`ENSEMBLE_AUTHZ_STRICT_COVERAGE=1` makes every mode refuse (`GET /api/authz/coverage` lists them in any mode)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi.routing import APIRoute

def _routes(app: Any) -> Iterator[tuple[APIRoute, str]]:
    try:  # FastAPI ≥ 0.160 keeps included routers nested
        from fastapi.routing import _iter_routes_with_context
        for orig, ctx in _iter_routes_with_context(app.routes):
            if isinstance(orig, APIRoute):
                yield orig, (ctx.path if ctx is not None else orig.path)
    except ImportError:
        for r in app.routes:
            if isinstance(r, APIRoute):
                yield r, r.path


def _marker(route: APIRoute) -> dict[str, Any] | None:
    for d in route.dependencies or []:
        m = getattr(d.dependency, "authz", None)
        if m:
            return m
    return None


def coverage(app: Any) -> dict[str, Any]:
    rows, uncovered = [], []
    for r, p in _routes(app):
        m = _marker(r)
        for method in sorted(r.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            must = True  # F-X4a: every route declares, GETs included
            e = {"method": method, "path": p, "endpoint": f"{r.endpoint.__module__.removeprefix('app.')}.{r.endpoint.__name__}",
                 "must": must}
            if m and m.get("public"):
                e["public"] = m["public"]
            elif m:
                e.update(action=m["action"], obj=m["objs"], also=m["also"] or None, when=m["when"])
            e["covered"] = bool(m)
            rows.append(e)
            if must and not m:
                uncovered.append(e)
    return {"n_routes": len(rows), "n_must": sum(e["must"] for e in rows), "n_covered": sum(e["covered"] for e in rows),
            "n_uncovered": len(uncovered), "uncovered": uncovered, "routes": rows,
            "rule": "every route (every method, GETs included) carries require(action, object) or public(reason)"}
