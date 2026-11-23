"""The compact plan the planner writes, and the adapter that expands it to the full plan body every consumer reads.

The model writes one terse entry per table, route, screen and rule; code derives the rest (enum values of a state field,
initial / final states, ``owns``, ``reads``, ``depends_on``, build order, route table bindings, request / response fields
from the table, list pagination, error and idempotency defaults). :func:`expand` accepts the compact form, the full form,
or a mix item by item, and is idempotent, so a hand-written full plan passes through unchanged.

Compact grammar (all strings are plain text, split on a few fixed separators):

column      ``name:type [req] [uniq] [idx] [=default] [enum:a|b|c] [ref:table[.col]]``
table       ``{name, owner, cols: [column], sm?, keep?, desc?}`` (audit and soft delete on; ``keep`` = ``days:90`` / ``last_n:500``)
sm          ``field initial=a final=x,y; a>b POST /t/{id}/go @who; b>c`` (transition: from>to [METHOD /path] [@who])
route       ``METHOD /path | purpose | in: a!,b:integer | out: id,name | err: 404 not_found, 409 conflict | w: t1 | r: t2
            | idem: repeat_is_noop | int: key | page`` (``!`` marks a required field; every part after the path is optional)
route_groups ``[{module, routes: [route]}]``
module      ``{key, kind, purpose, helpers: ["name(signature) purpose"]}``
screen      ``Name | purpose | GET /a, POST /b``
acceptance  ``text :: route:GET /a, screen:Name``
"""

from __future__ import annotations

import re
from typing import Any

from . import project_plan as pp

_SM_TRANSITION = re.compile(r"^\s*([\w*]+)\s*>\s*([\w*]+)(?:\s+((?:GET|POST|PUT|PATCH|DELETE)\s+\S+))?(?:\s+@\s*(.+?))?\s*$", re.I)
_TYPE_ALIASES = {"str": "string", "text": "string", "int": "integer", "float": "number", "bool": "boolean", "date": "datetime",
                 "timestamp": "datetime", "object": "json", "dict": "json", "list": "json"}


def _typ(t: str | None) -> str:
    t = (t or "string").strip().lower()
    return _TYPE_ALIASES.get(t, t)


# ── tables ───────────────────────────────────────────────────────────────────


def parse_column(s: Any) -> dict[str, Any] | None:
    """One column from ``name:type req uniq idx =default enum:a|b ref:t.c`` (a dict is a full column and passes through)."""
    if isinstance(s, dict):
        return s
    parts = str(s or "").split()
    if not parts:
        return None
    name, _, typ = parts[0].partition(":")
    col: dict[str, Any] = {"name": name.strip(), "type": _typ(typ)}
    for tok in parts[1:]:
        low = tok.lower()
        if low in ("req", "required", "!"):
            col["required"] = True
        elif low in ("uniq", "unique", "^"):
            col["unique"] = True
        elif low in ("idx", "index", "#"):
            col["index"] = True
        elif tok.startswith("="):
            col["default"] = _default(tok[1:], col["type"])
        elif low.startswith("enum:"):
            col["enum"] = [x for x in tok[5:].split("|") if x]
        elif low.startswith("ref:"):
            col["ref"] = tok[4:]
    return col if col["name"] else None


def _default(raw: str, typ: str) -> Any:
    try:
        if typ == "integer":
            return int(raw)
        if typ == "number":
            return float(raw)
    except ValueError:
        return raw
    if typ == "boolean":
        return raw.lower() in ("true", "1", "yes")
    return raw


def parse_state_machine(s: Any) -> dict[str, Any] | None:
    """``status initial=new final=done,rejected; new>review POST /t/{id}/submit @member; review>done`` -> a state machine."""
    if isinstance(s, dict):
        return s
    chunks = [c for c in str(s or "").split(";") if c.strip()]
    if not chunks:
        return None
    head = chunks[0].split()
    sm: dict[str, Any] = {"field": head[0], "transitions": []}
    for tok in head[1:]:
        k, _, v = tok.partition("=")
        if k == "initial":
            sm["initial"] = v
        elif k == "final":
            sm["final"] = [x for x in v.split(",") if x]
    for c in chunks[1:]:
        m = _SM_TRANSITION.match(c)
        if m:
            sm["transitions"].append({"from": m.group(1), "to": m.group(2), **({"route": m.group(3)} if m.group(3) else {}),
                                      **({"who": m.group(4)} if m.group(4) else {})})
    return sm


def _states(sm: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for x in ([sm.get("initial")] if sm.get("initial") else []) + [s for t in sm.get("transitions") or []
                                                                    for s in (t.get("from"), t.get("to"))] + list(sm.get("final") or []):
        if x and x != "*" and x not in seen:
            seen.append(x)
    return seen


def expand_table(t: dict[str, Any]) -> dict[str, Any]:
    if "cols" not in t and "sm" not in t and "keep" not in t:
        return t
    cols = [c for c in (parse_column(x) for x in t.get("cols") or t.get("columns") or []) if c]
    out: dict[str, Any] = {"name": t.get("name"), "owner": t.get("owner"), "columns": cols, "audit": t.get("audit", True),
                           "soft_delete": t.get("soft_delete", True)}
    for k in ("writers", "description", "business_key", "growth_note"):
        if t.get(k):
            out[k] = t[k]
    if t.get("desc"):
        out["description"] = t["desc"]
    out["indexes"] = [[c["name"]] for c in cols if c.get("index")] + list(t.get("indexes") or [])
    if t.get("keep"):
        policy, _, val = str(t["keep"]).partition(":")
        out["retention"] = {"policy": policy.strip(), "value": int(val) if val.strip().isdigit() else None}
    sm = parse_state_machine(t.get("sm") or t.get("state_machine"))
    if sm:
        states = _states(sm)
        if not sm.get("initial") and sm["transitions"]:
            sm["initial"] = sm["transitions"][0]["from"]
        if "final" not in sm:
            froms = {x["from"] for x in sm["transitions"]}
            sm["final"] = [s for s in states if s not in froms and s != sm.get("initial")]
        col = next((c for c in cols if c["name"] == sm["field"]), None)
        if col is None:
            cols.append({"name": sm["field"], "type": "string", "required": True, "enum": states, "index": True})
        elif not col.get("enum"):
            col["enum"] = states
        out["state_machine"] = sm
    return out


# ── routes ───────────────────────────────────────────────────────────────────


def _field_list(s: str) -> list[dict[str, Any]]:
    """``a!, b:integer, c?, status[open,closed]`` (``!`` may sit before or after the type; a bracketed list is an enum and its
    commas do not split fields)."""
    out = []
    for part in re.split(r",(?![^\[{(]*[\]})])", s):
        part = part.strip()
        if not part:
            continue
        part = re.sub(r"\s*[\[{(][^\]})]*[\]})]?", "", part)       # drop an enum/hint list: status[open,closed]
        name, _, typ = part.partition(":")
        req = "!" in name or typ.strip().endswith("!")
        name = re.sub(r"[^A-Za-z0-9_.]", "", name)
        if not name:
            continue
        typ = typ.strip().rstrip("!?").strip()
        out.append({"name": name, "type": _typ(typ if re.fullmatch(r"[A-Za-z_ ]*", typ) else ""),
                    **({"required": True} if req else {})})
    return out


def _table_fields(t: dict[str, Any], *, request: bool) -> list[dict[str, Any]]:
    cols = [{"name": c["name"], "type": c.get("type", "string"), **({"required": True} if c.get("required") and request else {})}
            for c in t.get("columns") or [] if request is False or c["name"] not in ("created_by", "updated_by", "archived_at")]
    return cols if request else [{"name": "id", "type": "string"}] + cols


def parse_route(s: str, module: str) -> dict[str, Any] | None:
    parts = [p.strip() for p in str(s).split("|")]
    key = pp.route_key(parts[0])
    if not key or not pp._ROUTE_RE.match(key):
        return None
    r: dict[str, Any] = {"key": key, "module": module, "_compact": True}
    for p in parts[1:]:
        head, sep, rest = p.partition(":")
        h = head.strip().lower()
        if sep and h in ("in", "out", "err", "w", "r", "idem", "int", "auth"):
            rest = rest.strip()
            if h == "in":
                r["request"] = {"fields": _field_list(rest)}
            elif h == "out":
                r["response"] = {"fields": _field_list(rest)}
            elif h == "err":
                r["errors"] = [{"status": int(x.split()[0]), "code": " ".join(x.split()[1:]) or "error"}
                               for x in rest.split(",") if x.strip().split()[:1] and x.strip().split()[0].isdigit()]
            elif h in ("w", "r"):
                r.setdefault("tables", {})["writes" if h == "w" else "reads"] = [x.strip() for x in rest.split(",") if x.strip()]
            elif h == "idem":
                r["idempotency"] = rest
            elif h == "int":
                r["integrations"] = [x.strip() for x in rest.split(",") if x.strip()]
            elif h == "auth":
                r["auth"] = rest
        elif p.lower() == "page":
            r["pagination"] = True
        elif p.lower() == "nopage":
            r["pagination"] = False
        elif p and "purpose" not in r:
            r["purpose"] = p
    return r


def _segments(path: str) -> list[str]:
    return [x for x in path.split("/") if x and not x.startswith("{")]


def derive_route(r: dict[str, Any], tables: dict[str, dict[str, Any]], owned: set[str]) -> dict[str, Any]:
    """Fill what code can know: the table a route is about (path segment == table name, or its plural), its request and
    response fields, list pagination, 404 / 422 errors and the idempotency of state-changing routes."""
    m = pp._ROUTE_RE.match(r["key"])
    method, path = m.group(1).upper(), m.group(2)
    tbl = r.get("tables") or {}
    if not tbl.get("reads") and not tbl.get("writes"):
        for seg in _segments(path):
            hit = next((n for n in tables if seg in (n, n + "s", n.rstrip("s"), n.replace("_", "-"), n.replace("_", "-") + "s")), None)
            if hit:
                tbl = {"reads": [hit]} if method == "GET" else {"writes": [hit]}
                break
        r["tables"] = tbl
    bound = (tbl.get("reads") or tbl.get("writes") or [None])[0]
    t = tables.get(bound) if bound else None
    if t and "request" not in r and method in ("POST", "PUT", "PATCH"):
        r["request"] = {"fields": _table_fields(t, request=True)}
    if t and "response" not in r and method in ("GET", "POST", "PUT", "PATCH"):
        r["response"] = {"fields": _table_fields(t, request=False)}
    if "pagination" not in r and pp._is_list_route(r["key"]):
        r["pagination"] = True
    if "errors" not in r:
        errs = []
        if "{" in path:
            errs.append({"status": 404, "code": "not_found"})
        if method in pp.STATE_CHANGING:
            errs.append({"status": 422, "code": "invalid_request"})
        r["errors"] = errs
    if "idempotency" not in r and method in pp.STATE_CHANGING:
        r["idempotency"] = "repeat_is_noop" if method in ("PUT", "DELETE") else "none"
    return r


# ── apps, modules, requirements ──────────────────────────────────────────────


def parse_screen(s: Any) -> dict[str, Any] | None:
    if isinstance(s, dict):
        return s
    parts = [p.strip() for p in str(s or "").split("|")]
    if not parts or not parts[0]:
        return None
    routes = [pp.route_key(x) for x in parts[2].split(",")] if len(parts) > 2 else []
    return {"name": parts[0], "purpose": parts[1] if len(parts) > 1 else "", "routes": [x for x in routes if x]}


def parse_helper(s: Any) -> dict[str, Any] | None:
    if isinstance(s, dict):
        return s
    m = re.match(r"^\s*([\w.]+)\s*(\([^)]*\)(?:\s*->\s*\S+)?)\s*(.*)$", str(s or ""))
    return {"name": m.group(1), "signature": m.group(1) + m.group(2), "purpose": m.group(3).strip()} if m else None


def parse_acceptance(a: Any, n: int) -> dict[str, Any] | None:
    if isinstance(a, dict):
        return a
    text, _, covers = str(a or "").partition("::")
    if not text.strip():
        return None
    return {"id": f"ac{n + 1}", "text": text.strip(), "covers": [c.strip() for c in covers.split(",") if c.strip()]}


def expand(raw: Any) -> dict[str, Any]:
    """The full (not yet normalized) plan body for a compact, full or mixed one. Idempotent; never raises on odd input."""
    b = dict(raw) if isinstance(raw, dict) else {}
    data = dict(b.get("data")) if isinstance(b.get("data"), dict) else {}
    tables = [expand_table(t) for t in (b.pop("tables", None) or data.get("tables") or []) if isinstance(t, dict)]
    data["tables"] = tables
    b["data"] = data
    by_table = {t.get("name"): t for t in tables if t.get("name")}

    reqs = dict(b.get("requirements")) if isinstance(b.get("requirements"), dict) else {}
    for k in ("assumptions", "limitations"):
        if isinstance(b.get(k), list):
            reqs[k] = b.pop(k)
    acc = b.pop("acceptance", None) if isinstance(b.get("acceptance"), list) else reqs.get("acceptance")
    if acc is not None:
        reqs["acceptance"] = [x for x in (parse_acceptance(a, n) for n, a in enumerate(acc)) if x]
    b["requirements"] = reqs

    modules = []
    for m in b.get("modules") or []:
        if not isinstance(m, dict):
            continue
        m = dict(m)
        if isinstance(m.get("helpers"), list):
            m["helpers"] = [h for h in (parse_helper(x) for x in m["helpers"]) if h]
        modules.append(m)
    b["modules"] = modules
    owner_of = {t.get("name"): t.get("owner") for t in tables}

    routes: list[dict[str, Any]] = []
    groups = b.pop("route_groups", None) or []
    for g in groups:
        if isinstance(g, dict):
            routes += [r for r in (parse_route(s, str(g.get("module") or "")) for s in g.get("routes") or []) if r]
    routes += [dict(r) for r in b.get("routes") or [] if isinstance(r, dict)]
    owned_by_module: dict[str, set[str]] = {}
    for t, o in owner_of.items():
        owned_by_module.setdefault(o, set()).add(t)
    b["routes"] = [derive_route(r, by_table, owned_by_module.get(r.get("module"), set())) if r.pop("_compact", False) else r
                   for r in routes]

    keys = {m.get("key") or m.get("name") for m in modules}
    for m in modules if groups else []:    # reads / depends_on from what the module's routes touch (compact routes only)
        mine = [r for r in b["routes"] if r.get("module") == (m.get("key") or m.get("name"))]
        reads = [t for r in mine for t in (r.get("tables") or {}).get("reads", []) + (r.get("tables") or {}).get("writes", [])
                 if t in by_table and owner_of.get(t) != (m.get("key") or m.get("name"))]
        m["reads"] = list(dict.fromkeys(list(m.get("reads") or []) + reads))
        dep = [owner_of[t] for t in m["reads"] if owner_of.get(t) in keys]
        m["depends_on"] = list(dict.fromkeys(list(m.get("depends_on") or []) + dep))

    apps = []
    for a in b.get("apps") or []:
        if isinstance(a, dict):
            a = dict(a)
            a["screens"] = [s for s in (parse_screen(x) for x in a.get("screens") or []) if s]
            apps.append(a)
    b["apps"] = apps

    if not b.get("build_order") and modules:
        order = _build_order(modules, apps)
        b["build_order"] = order
    return b


def _build_order(modules: list[dict[str, Any]], apps: list[dict[str, Any]]) -> list[str]:
    keys = [str(m.get("key") or m.get("name")) for m in modules]
    shared = [str(m.get("key") or m.get("name")) for m in modules if m.get("kind") == "shared"]
    deps = {str(m.get("key") or m.get("name")): set(m.get("depends_on") or []) & set(keys) - {str(m.get("key") or m.get("name"))}
            for m in modules}
    order: list[str] = list(shared)
    left = [k for k in keys if k not in order]
    while left:
        ready = [k for k in left if deps[k] <= set(order)]
        pick = ready[0] if ready else left[0]      # a cycle is left for validation to report
        order.append(pick)
        left.remove(pick)
    return order + [f"app:{a.get('key') or a.get('name')}" for a in apps]
