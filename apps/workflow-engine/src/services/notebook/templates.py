"""Notebook templates (BUILD-PLAN §1.2): ``templates/*.json``, each ``{slug, title, description, icon, keeps_as, cells}``.

A template cell is ``{kind, title?, spec, guard?, children?}``. Strings may hold placeholders (``__CONNECTION__``,
``__BASE_URL__``) that :func:`instantiate` fills at create time; an unfilled placeholder is left as written, so the
cell shows it and fails clearly when run (for example ``connection_not_found``).
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
#: gallery order; any other template file follows alphabetically
ORDER = ("blank", "stuck-wires", "daily-data-quality", "portal-smoke")


class TemplateNotFound(KeyError):
    pass


@lru_cache(maxsize=1)
def _all() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        t = json.loads(p.read_text())
        out[t["slug"]] = t
    return dict(sorted(out.items(), key=lambda kv: (ORDER.index(kv[0]) if kv[0] in ORDER else len(ORDER), kv[0])))


def list_templates() -> list[dict[str, Any]]:
    """Gallery rows: ``[{slug, title, description, icon, keeps_as, cells:[{kind, title}]}]``."""
    return [{"slug": t["slug"], "title": t["title"], "description": t["description"], "icon": t.get("icon"),
             "keeps_as": t.get("keeps_as"), "cells": [{"kind": c["kind"], "title": c.get("title")} for c in t["cells"]]}
            for t in _all().values()]


def get_template(slug: str) -> dict[str, Any]:
    t = _all().get(slug)
    if t is None:
        raise TemplateNotFound(slug)
    return copy.deepcopy(t)


def _fill(value: Any, subs: dict[str, str]) -> Any:
    if isinstance(value, str):
        for k, v in subs.items():
            value = value.replace(k, v)
        return value
    if isinstance(value, list):
        return [_fill(v, subs) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, subs) for k, v in value.items()}
    return value


def instantiate(slug: str, *, connection: str | None = None, base_url: str | None = None,
                **extra: str) -> list[dict[str, Any]]:
    """The template's cells with placeholders filled: ``[{kind, title, spec, guard, children}]`` in order.

    ``connection`` fills ``__CONNECTION__``, ``base_url`` fills ``__BASE_URL__`` (no trailing slash); ``extra`` maps
    ``NAME`` to a value for ``__NAME__``. Raises :class:`TemplateNotFound`."""
    subs: dict[str, str] = {}
    if connection:
        subs["__CONNECTION__"] = connection
    if base_url:
        subs["__BASE_URL__"] = base_url.rstrip("/")
    for k, v in extra.items():
        if v is not None:
            subs[f"__{k.upper()}__"] = str(v)
    cells = []
    for c in get_template(slug)["cells"]:
        cell = {"kind": c["kind"], "title": c.get("title"), "spec": _fill(c.get("spec") or {}, subs),
                "guard": _fill(c.get("guard"), subs) if c.get("guard") else None,
                "children": [{"kind": ch["kind"], "title": ch.get("title"), "spec": _fill(ch.get("spec") or {}, subs),
                              "guard": _fill(ch.get("guard"), subs) if ch.get("guard") else None}
                             for ch in c.get("children") or []]}
        cells.append(cell)
    return cells


__all__ = ["TemplateNotFound", "get_template", "instantiate", "list_templates"]
