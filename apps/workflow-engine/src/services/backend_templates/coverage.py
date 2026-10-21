"""Plan-versus-delivery: which routes the builder promised and which the saved source actually has.

A model that runs out of output budget tends to stop after the first area and report success. The plan it declared
(``planned_routes`` on emit/patch, plus routes the request or the agreed spec names as ``METHOD /path``) is the ledger;
whatever the source lacks is owed, and the generator keeps asking until it is delivered or the budget ends."""

from __future__ import annotations

import re
from typing import Any

_ROUTE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_\-./{}:<>*]*)")
_PARAM = re.compile(r"\{[^}/]*\}|<[^>/]*>|:[A-Za-z_]\w*")


def norm(method: str, path: str) -> str:
    p = _PARAM.sub("{}", path.split("?")[0].strip())
    p = re.sub(r"/{2,}", "/", p).rstrip("/") or "/"
    return f"{method.upper()} {p.lower()}"


def parse_route(text: Any) -> str | None:
    m = _ROUTE.search(str(text or ""))
    return norm(m.group(1), m.group(2)) if m and "*" not in m.group(2) else None


def routes_in_text(*texts: Any) -> dict[str, str]:
    """``{normalised key: as written}`` for every ``METHOD /path`` in the texts (wildcards are patterns, not routes)."""
    out: dict[str, str] = {}
    for t in texts:
        for m in _ROUTE.finditer(str(t or "")):
            if "*" in m.group(2):
                continue
            out.setdefault(norm(m.group(1), m.group(2)), f"{m.group(1)} {m.group(2).rstrip('.,;:')}")
    return out


def spec_routes(spec: dict[str, Any] | None, prompt: str, conversation: list[Any] | None = None) -> dict[str, str]:
    """Routes the request and the agreed spec name explicitly. A spec that splits the backend over several modules names
    routes of the other modules too, so only the request itself counts then."""
    texts: list[Any] = [prompt] + [getattr(t, "content", "") for t in (conversation or [])[-1:]]
    if spec and len([m for m in spec.get("module_plan") or [] if isinstance(m, dict)]) <= 1:
        texts += [str(c) for c in spec.get("acceptance_criteria") or []]
    return routes_in_text(*texts)


def planned(items: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items if isinstance(items, list) else []:
        key = parse_route(it)
        if key:
            m = _ROUTE.search(str(it))
            out.setdefault(key, f"{m.group(1)} {m.group(2)}")
    return out


def delivered(decls: list[dict[str, Any]]) -> set[str]:
    return {norm(d["method"], d["path"]) for d in decls}


def missing(ledger: dict[str, str], decls: list[dict[str, Any]], existing: set[str] | None = None) -> list[str]:
    have = delivered(decls) | (existing or set())
    return [label for key, label in ledger.items() if key not in have]


def plan_routes(spec: dict[str, Any] | None) -> dict[str, str]:
    """The ledger from an approved plan's module spec: its route contracts (``{key: "METHOD /path", ...}``) and nothing else."""
    out: dict[str, str] = {}
    for r in (spec or {}).get("routes") or []:
        key = r.get("key") if isinstance(r, dict) else r
        k = parse_route(key)
        if k:
            m = _ROUTE.search(str(key))
            out.setdefault(k, f"{m.group(1)} {m.group(2)}")
    return out
