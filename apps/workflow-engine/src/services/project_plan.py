"""The ProjectPlan: one agreed, versioned, approved plan per project that every Studio builder reads instead of asking.

A plan body (``v: 1``) holds the requirements (answers, assumptions, limitations, acceptance criteria), environments,
secrets, integrations, the data model (tables with owners, refs, enums, indexes, state machines, retention), the modules
and their route contracts, the apps and their screens, non-functional rules and the build order. Everything is keyed, so
diffs and conformance work by key.

Pure layer (no database): :func:`normalize`, :func:`validate_plan` (issues PLAN001-PLAN014), :func:`diff_plans` and the
consumer helpers :func:`module_spec`, :func:`app_brief`, :func:`answered_topics`, :func:`route_contract`,
:func:`table_plan`, :func:`build_order`, :func:`module_for`.

Store (``project_plans`` rows): :func:`create_draft`, :func:`update_draft`, :func:`approve`, :func:`revise`,
:func:`list_versions`, :func:`get_version`, :func:`load_approved`, :func:`bind_app` and :func:`mirror_to_knowledge`.
An approved version is never edited (the one exception is :func:`bind_app`, which records which app implements a plan
app); revising creates a new draft whose ``base_version`` is the approved one, and approving it supersedes the old one.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .backend_templates.coverage import norm as route_norm
from .schema_migrations import SYSTEM_COLUMNS, column as norm_column, diff_table, plan_migrations

PLAN_V = 1
COLUMN_TYPES = ("string", "number", "integer", "boolean", "datetime", "json")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
STATE_CHANGING = ("POST", "PUT", "PATCH", "DELETE")
MAX_MODULE_ROUTES = 12
RETENTION_POLICIES = ("keep", "days", "last_n")
_ROUTE_RE = re.compile(r"^\s*([A-Za-z]+)\s+(/\S*)\s*$")
_PLAN_TOKEN = re.compile(r"\[plan:([A-Za-z0-9_]+)\]")


# ── normalization ────────────────────────────────────────────────────────────


def _s(v: Any, limit: int = 2000) -> str:
    return str(v).strip()[:limit] if v is not None else ""


def _strs(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        s = _s(x, 500)
        if s and s not in out:
            out.append(s)
    return out


def _dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def hostname(value: Any) -> str:
    """A host as the broker matches it: hostname only (no scheme, port, path), lower case."""
    s = _s(value, 300).lower()
    if not s:
        return ""
    if "://" not in s:
        s = "//" + s
    try:
        h = urlsplit(s).hostname or ""
    except ValueError:
        h = ""
    if not h and s.startswith("//*."):     # a wildcard host keeps its star
        h = s[2:].split("/")[0].split(":")[0]
    return h


def is_placeholder_url(url: str) -> bool:
    """A base URL on a reserved name (RFC 2606/6761: ``.example``, ``.invalid``) cannot be a real system: the environment is a
    placeholder that is named (guards, refused actions) but never called."""
    h = hostname(url)
    return bool(h) and (h.endswith((".example", ".invalid")) or h in ("example", "invalid"))


def route_key(r: Any) -> str:
    """``METHOD /path`` as written (method upper-cased), from a ``key`` or ``method`` + ``path``."""
    if isinstance(r, str):
        raw = r
    elif isinstance(r, dict):
        raw = _s(r.get("key")) or f"{_s(r.get('method'))} {_s(r.get('path'))}"
    else:
        return ""
    m = _ROUTE_RE.match(raw)
    return f"{m.group(1).upper()} {m.group(2)}" if m else raw.strip()


def _rnorm(key: str) -> str:
    m = _ROUTE_RE.match(key or "")
    return route_norm(m.group(1), m.group(2)) if m else (key or "").strip().upper()


def _fields(v: Any) -> list[dict[str, Any]]:
    out = []
    for f in _dicts(v):
        if not _s(f.get("name")):
            continue
        d = {"name": _s(f.get("name"), 100), "type": f.get("type") if f.get("type") in COLUMN_TYPES else "string"}
        if f.get("required"):
            d["required"] = True
        if f.get("description"):
            d["description"] = _s(f.get("description"), 300)
        out.append(d)
    return out


def _table(t: dict[str, Any], conventions: dict[str, Any]) -> dict[str, Any]:
    cols = [norm_column(c) for c in _dicts(t.get("columns")) if _s(c.get("name"))
            and _s(c.get("name")) not in SYSTEM_COLUMNS]
    for c in cols:
        if c["type"] not in COLUMN_TYPES:
            c["type"] = "string"
    names = {c["name"] for c in cols}
    audit, soft = bool(t.get("audit")), bool(t.get("soft_delete"))
    if audit:   # the conventions make audit and soft delete concrete columns, so builders and the gate see them
        for a in _strs(conventions.get("audit_columns")) or ["created_by", "updated_by"]:
            if a not in names:
                cols.append({"name": a, "type": "string", "required": False})
                names.add(a)
    if soft:
        sd = _s(conventions.get("soft_delete")) or "archived_at"
        if sd not in names:
            cols.append({"name": sd, "type": "datetime", "required": False})
    ret = t.get("retention") if isinstance(t.get("retention"), dict) else {}
    out: dict[str, Any] = {
        "name": _s(t.get("name"), 63), "owner": _s(t.get("owner"), 63) or None, "writers": _strs(t.get("writers")),
        "description": _s(t.get("description"), 500), "columns": cols,
        "indexes": [_strs(i) for i in (t.get("indexes") or []) if _strs(i)] if isinstance(t.get("indexes"), list) else [],
        "business_key": _strs(t.get("business_key")), "audit": audit, "soft_delete": soft,
        "retention": {"policy": ret.get("policy") if ret.get("policy") in RETENTION_POLICIES else "keep",
                      "value": ret.get("value") if isinstance(ret.get("value"), (int, float)) and not isinstance(ret.get("value"), bool) else None},
        "growth_note": _s(t.get("growth_note"), 500),
    }
    sm = t.get("state_machine")
    if isinstance(sm, dict) and _s(sm.get("field")):
        out["state_machine"] = {
            "field": _s(sm.get("field"), 63), "initial": _s(sm.get("initial"), 63) or None, "final": _strs(sm.get("final")),
            "transitions": [{"from": _s(x.get("from"), 63), "to": _s(x.get("to"), 63),
                             **({"route": route_key(x["route"])} if _s(x.get("route")) else {}),
                             **({"who": _s(x.get("who"), 200)} if _s(x.get("who")) else {}),
                             **({"effect": _s(x.get("effect"), 300)} if _s(x.get("effect")) else {})}
                            for x in _dicts(sm.get("transitions"))]}
    else:
        out["state_machine"] = None
    return out


def _route(r: dict[str, Any]) -> dict[str, Any]:
    req = r.get("request") if isinstance(r.get("request"), dict) else {}
    resp = r.get("response") if isinstance(r.get("response"), dict) else {}
    tables = r.get("tables") if isinstance(r.get("tables"), dict) else {}
    pag = r.get("pagination")
    return {"key": route_key(r), "module": _s(r.get("module"), 63) or None, "purpose": _s(r.get("purpose"), 500),
            "auth": _s(r.get("auth"), 40) or None,
            "request": {"fields": _fields(req.get("fields"))}, "response": {"fields": _fields(resp.get("fields"))},
            "errors": [{"status": int(e["status"]) if str(e.get("status", "")).isdigit() else None, "code": _s(e.get("code"), 80)}
                       for e in _dicts(r.get("errors"))],
            "idempotency": _s(r.get("idempotency"), 200) or None,
            "pagination": bool(pag) if isinstance(pag, bool) else None,
            "integrations": _strs(r.get("integrations")),
            "tables": {"reads": _strs(tables.get("reads")), "writes": _strs(tables.get("writes"))}}


def normalize(body: Any) -> dict[str, Any]:
    """A complete v1 body: every section present, items keyed and typed, hosts reduced to hostnames, system columns
    removed (the table manages them), the audit / soft-delete conventions applied as columns, and module ``owns`` kept
    in step with table ``owner``. Unknown keys are dropped; non-dict items are skipped. Idempotent."""
    b = body if isinstance(body, dict) else {}
    reqs = b.get("requirements") if isinstance(b.get("requirements"), dict) else {}
    data = b.get("data") if isinstance(b.get("data"), dict) else {}
    conventions = data.get("conventions") if isinstance(data.get("conventions"), dict) else {}
    tables = [_table(t, conventions) for t in _dicts(data.get("tables")) if _s(t.get("name"))]

    modules: list[dict[str, Any]] = []
    for m in _dicts(b.get("modules")):
        key = _s(m.get("key") or m.get("name"), 63)
        if not key:
            continue
        modules.append({"key": key, "kind": "shared" if m.get("kind") == "shared" else "routes",
                        "purpose": _s(m.get("purpose"), 500), "depends_on": _strs(m.get("depends_on")),
                        "owns": _strs(m.get("owns")), "reads": _strs(m.get("reads")),
                        "integrations": _strs(m.get("integrations")),
                        "helpers": [{"name": _s(h.get("name"), 80), "signature": _s(h.get("signature"), 300),
                                     "purpose": _s(h.get("purpose"), 300)} for h in _dicts(m.get("helpers")) if _s(h.get("name"))]})
    by_key = {m["key"]: m for m in modules}
    for t in tables:   # owner <-> owns: whichever side names it, both say it
        if t["owner"] is None:
            claim = [m["key"] for m in modules if t["name"] in m["owns"]]
            if len(claim) == 1:
                t["owner"] = claim[0]
        if t["owner"] in by_key and t["name"] not in by_key[t["owner"]]["owns"]:
            by_key[t["owner"]]["owns"].append(t["name"])

    apps = []
    for a in _dicts(b.get("apps")):
        key = _s(a.get("key") or a.get("name"), 63)
        if not key:
            continue
        apps.append({"key": key, "name": _s(a.get("name"), 120) or key, "app_id": _s(a.get("app_id"), 80) or None,
                     "users": _s(a.get("users"), 500),
                     "screens": [{"name": _s(s.get("name"), 120), "purpose": _s(s.get("purpose"), 500),
                                  "routes": [route_key(x) for x in (s.get("routes") or []) if route_key(x)]
                                  if isinstance(s.get("routes"), list) else [],
                                  "states": {str(k).lower(): _s(v, 500) for k, v in (s.get("states") or {}).items() if _s(v)}
                                  if isinstance(s.get("states"), dict) else {},
                                  "journeys": _strs(s.get("journeys"))}
                                 for s in _dicts(a.get("screens")) if _s(s.get("name"))]})

    integrations = []
    for i in _dicts(b.get("integrations")):
        key = _s(i.get("key") or i.get("kind"), 63)
        if not key:
            continue
        integrations.append({"key": key, "kind": _s(i.get("kind"), 40) or "http", "purpose": _s(i.get("purpose"), 500),
                             "job_types": _strs(i.get("job_types")),
                             "contract": i.get("contract") if isinstance(i.get("contract"), dict) else None,
                             "secrets": _strs(i.get("secrets")),
                             "hosts": [h for h in (hostname(x) for x in _strs(i.get("hosts"))) if h],
                             "smoke": i.get("smoke") if isinstance(i.get("smoke"), dict) else None})

    return {
        "v": PLAN_V, "title": _s(b.get("title"), 200), "summary": _s(b.get("summary"), 2000),
        "requirements": {
            "answers": [{"id": _s(a.get("id"), 80), "topic": _s(a.get("topic"), 40) or "other",
                         "question": _s(a.get("question"), 1000), "answer": _s(a.get("answer"), 2000),
                         "round": int(a["round"]) if isinstance(a.get("round"), int) else 1,
                         "source": a.get("source") if a.get("source") in ("model", "check", "domain", "plan", "checklist") else "model"}
                        for a in _dicts(reqs.get("answers")) if _s(a.get("id"))],
            **({"request": _s(reqs.get("request"), 12000)} if _s(reqs.get("request"), 12000) else {}),   # the first message, as typed
            "assumptions": _strs(reqs.get("assumptions")), "limitations": _strs(reqs.get("limitations")),
            "acceptance": [{"id": _s(a.get("id"), 40) or f"ac{n + 1}", "text": _s(a.get("text"), 500),
                            "covers": _strs(a.get("covers"))}
                           for n, a in enumerate(_dicts(reqs.get("acceptance"))) if _s(a.get("text"))],
        },
        "roles_note": _s(b.get("roles_note"), 2000),
        "environments": [{"name": _s(e.get("name"), 63), "base_url": _s(e.get("base_url"), 500),
                          "live": bool(e.get("live")), "refused_actions": _strs(e.get("refused_actions")),
                          "placeholder": bool(e.get("placeholder")) or is_placeholder_url(_s(e.get("base_url"), 500))}
                         for e in _dicts(b.get("environments")) if _s(e.get("name"))],
        "secrets": [{"name": _s(x.get("name"), 100), "purpose": _s(x.get("purpose"), 300),
                     "environments": _strs(x.get("environments")),
                     "allowed_hosts": [h for h in (hostname(v) for v in _strs(x.get("allowed_hosts"))) if h]}
                    for x in _dicts(b.get("secrets")) if _s(x.get("name"))],
        "integrations": integrations,
        "data": {"conventions": conventions, "tables": tables},
        "modules": modules,
        "routes": [_route(r) for r in _dicts(b.get("routes")) if route_key(r)],
        "apps": apps,
        "nfr": b.get("nfr") if isinstance(b.get("nfr"), dict) else {},
        "build_order": _strs(b.get("build_order")),
        "migrations": _dicts(b.get("migrations")),
    }


# ── validation ───────────────────────────────────────────────────────────────


def _issue(code: str, path: str, message: str, severity: str = "error", needs_input: bool = False,
           **extra: Any) -> dict[str, Any]:
    return {"code": code, "path": path, "message": message, "severity": severity, "needs_input": needs_input, **extra}


def has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(i.get("severity") == "error" for i in issues)


_SECTIONS = {"requirements": dict, "environments": list, "secrets": list, "integrations": list, "data": dict,
             "modules": list, "routes": list, "apps": list, "nfr": dict, "build_order": list}


def _shape_issues(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k, typ in _SECTIONS.items():
        if k in raw and raw[k] is not None and not isinstance(raw[k], typ):
            out.append(_issue("PLAN001", k, f"{k} must be a {'list' if typ is list else 'object'}"))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    if "tables" in data and not isinstance(data["tables"], list):
        out.append(_issue("PLAN001", "data.tables", "data.tables must be a list"))
    return out


def _dupes(items: list[str]) -> list[str]:
    seen, d = set(), []
    for x in items:
        if x in seen and x not in d:
            d.append(x)
        seen.add(x)
    return d


def _is_list_route(key: str) -> bool:
    m = _ROUTE_RE.match(key)
    return bool(m and m.group(1).upper() == "GET" and not m.group(2).rstrip("/").endswith("}"))


def _owner_map(p: dict[str, Any]) -> dict[str, str | None]:
    return {t["name"]: t["owner"] for t in p["data"]["tables"]}


def _module_graph(p: dict[str, Any]) -> dict[str, set[str]]:
    """``{module: modules that must be built before it}`` from ``depends_on`` and the owners of the tables it reads."""
    keys = {m["key"] for m in p["modules"]}
    owners = _owner_map(p)
    g: dict[str, set[str]] = {}
    for m in p["modules"]:
        deps = {d for d in m["depends_on"] if d in keys and d != m["key"]}
        deps |= {o for t in m["reads"] if (o := owners.get(t)) and o in keys and o != m["key"]}
        g[m["key"]] = deps
    return g


def _topo(p: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Kahn's order (stable by module order) and the modules left in a cycle."""
    g = _module_graph(p)
    order: list[str] = []
    left = [m["key"] for m in p["modules"]]
    while True:
        ready = [k for k in left if g[k] <= set(order)]
        if not ready:
            break
        order.append(ready[0])
        left.remove(ready[0])
    return order, left


# the smoke run's ``until`` grammar (builder_gates._until): "field in [a, b]" or "field == x" / "field != x"
_SMOKE_UNTIL = re.compile(r"\s*[\w.~|]+\s+in\s+\[.*\]\s*|\s*[\w.~|]+\s*(==|!=)\s*.+")


def _smoke_problem(smoke: dict[str, Any], routes: dict[str, Any], fact_routes: set[str]) -> str | None:
    """Why ``builder_gates.smoke_run`` could not run this smoke, or None. It starts with ``start`` ('METHOD /path' of a planned
    or existing route), optionally polls ``poll`` (same form, ``{id}`` filled from the start response) until ``until``."""
    start = smoke.get("start")
    m = _ROUTE_RE.match(start) if isinstance(start, str) else None
    if not m:
        return f"smoke.start is {_s(repr(start), 120)}, not 'METHOD /path' of a route"
    if (n := route_norm(m.group(1), m.group(2))) not in routes and n not in fact_routes:
        return f"smoke.start {start!r} is not a planned route"
    poll = smoke.get("poll")
    if poll not in (None, ""):
        pm = _ROUTE_RE.match(poll) if isinstance(poll, str) else None
        if not pm:
            return f"smoke.poll is {_s(repr(poll), 120)}, not 'METHOD /path/{{id}}' of a route"
        if (n := route_norm(pm.group(1), pm.group(2))) not in routes and n not in fact_routes:
            return f"smoke.poll {poll!r} is not a planned route"
        until = smoke.get("until")
        if not isinstance(until, str) or not _SMOKE_UNTIL.fullmatch(until):
            return f"smoke.until is {until!r}; it must read 'field in [a, b]' or 'field == value'"
    return None


def validate_plan(body: Any, facts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Coded issues ``{code, path, message, severity, needs_input}``. ``facts`` is the project as it stands (the
    ``gather_facts`` shape: ``tables`` with ``columns`` and ``row_count``, ``routes`` with ``method``/``path``); refs and screen
    routes may point at what exists, and a required column without a default is a problem only on a table with rows.
    ``needs_input`` marks issues only the team can settle (secret names, hosts, URLs, the live environment, defaults,
    retention numbers): they become questions. Any ``error`` blocks approval."""
    if not isinstance(body, dict):
        return [_issue("PLAN001", "", "the plan must be a JSON object")]
    out = _shape_issues(body)
    p = normalize(body)
    facts = facts or {}
    fact_tables = {str(t.get("name")): t for t in facts.get("tables") or [] if isinstance(t, dict)}
    fact_routes = {route_norm(str(r.get("method") or ""), str(r.get("path") or "")) for r in facts.get("routes") or []
                   if isinstance(r, dict)}
    # external (read-only) tables of the project's database connections, addressed as "<connection>.<table>"
    ext_tables = {f"{c.get('key')}.{t.get('name')}": t for c in facts.get("connections") or [] if isinstance(c, dict)
                  for t in c.get("tables") or [] if isinstance(t, dict)}
    tables = {t["name"]: t for t in p["data"]["tables"]}
    modules = {m["key"]: m for m in p["modules"]}
    routes = {_rnorm(r["key"]): r for r in p["routes"]}
    secrets = {s["name"]: s for s in p["secrets"]}

    # PLAN001: duplicate keys
    for label, keys in (("data.tables", [t["name"] for t in p["data"]["tables"]]), ("modules", [m["key"] for m in p["modules"]]),
                        ("routes", [_rnorm(r["key"]) for r in p["routes"]]), ("apps", [a["key"] for a in p["apps"]]),
                        ("secrets", [s["name"] for s in p["secrets"]]), ("environments", [e["name"] for e in p["environments"]]),
                        ("integrations", [i["key"] for i in p["integrations"]]),
                        ("requirements.acceptance", [a["id"] for a in p["requirements"]["acceptance"]])):
        for d in _dupes(keys):
            out.append(_issue("PLAN001", f"{label}.{d}", f"{d} appears more than once in {label}"))
    for t in p["data"]["tables"]:
        for d in _dupes([c["name"] for c in t["columns"]]):
            out.append(_issue("PLAN001", f"data.tables.{t['name']}.columns.{d}", f"column {d} appears twice in {t['name']}"))
    for a in p["apps"]:
        for d in _dupes([s["name"] for s in a["screens"]]):
            out.append(_issue("PLAN001", f"apps.{a['key']}.screens.{d}", f"screen {d} appears twice in {a['key']}"))
    for r in body.get("routes") or [] if isinstance(body.get("routes"), list) else []:
        if isinstance(r, dict) and not _ROUTE_RE.match(route_key(r)):
            out.append(_issue("PLAN001", f"routes.{route_key(r)}", f"route {route_key(r)!r} is not 'METHOD /path'"))

    for t in p["data"]["tables"]:
        tp = f"data.tables.{t['name']}"
        # PLAN002: refs
        for c in t["columns"]:
            ref = c.get("ref")
            if not ref:
                continue
            rt, _, rc = ref.partition(".")
            rc = rc or "id"
            ext = ext_tables.get(ref.rpartition(".")[0]) or ext_tables.get(ref)
            if ext is not None:
                ok = ref in ext_tables or ref.rpartition(".")[2] in {str(x.get("name")) for x in ext.get("columns") or []}
            elif rt in tables:
                ok = rc in SYSTEM_COLUMNS or rc in {x["name"] for x in tables[rt]["columns"]}
            elif rt in fact_tables:
                ok = rc in SYSTEM_COLUMNS or rc in {str(x.get("name")) for x in fact_tables[rt].get("columns") or []}
            else:
                ok = False
            if not ok:
                out.append(_issue("PLAN002", f"{tp}.columns.{c['name']}",
                                  f"{t['name']}.{c['name']} points at {ref}, which is neither in the plan nor in the project"))
        # PLAN003: one owner
        claims = [m["key"] for m in p["modules"] if t["name"] in m["owns"]]
        if not t["owner"] or t["owner"] not in modules:
            out.append(_issue("PLAN003", f"{tp}.owner",
                              f"table {t['name']} needs one owner module from the plan" +
                              (f" (it names {t['owner']}, which is not planned)" if t["owner"] else "")))
        elif len(set(claims)) > 1:
            out.append(_issue("PLAN003", f"{tp}.owner", f"table {t['name']} is owned by more than one module: "
                              f"{', '.join(sorted(set(claims)))}"))
        for w in t["writers"]:
            if w not in modules:
                out.append(_issue("PLAN003", f"{tp}.writers", f"writer {w} of {t['name']} is not a planned module"))
        # PLAN004: state machine
        sm = t.get("state_machine")
        if sm:
            col = next((c for c in t["columns"] if c["name"] == sm["field"]), None)
            states = set(col.get("enum") or []) if col else set()
            if col is None or not states:
                out.append(_issue("PLAN004", f"{tp}.state_machine", f"the state field {sm['field']} must be a column of "
                                  f"{t['name']} with an enum of its states"))
            else:
                used = ([sm["initial"]] if sm["initial"] else []) + sm["final"] + [
                    s for x in sm["transitions"] for s in (x["from"], x["to"]) if s != "*"]
                bad = sorted({s for s in used if s not in states})
                if bad:
                    out.append(_issue("PLAN004", f"{tp}.state_machine", f"{t['name']}.{sm['field']} uses undeclared "
                                      f"state(s) {', '.join(bad)}; declared: {', '.join(col['enum'])}"))
            for x in sm["transitions"]:
                if x.get("route") and _rnorm(x["route"]) not in routes:
                    out.append(_issue("PLAN004", f"{tp}.state_machine", f"transition {x['from']}->{x['to']} names route "
                                      f"{x['route']}, which is not planned"))
        # PLAN010: enums, defaults on tables with rows, retention numbers
        fact = fact_tables.get(t["name"])
        have = {str(c.get("name")) for c in (fact or {}).get("columns") or []}
        for c in t["columns"]:
            if "enum" in c and not c["enum"]:
                out.append(_issue("PLAN010", f"{tp}.columns.{c['name']}", f"enum column {t['name']}.{c['name']} lists no values"))
            if (fact and int(fact.get("row_count") or 0) > 0 and c["required"] and "default" not in c
                    and c["name"] not in have):
                out.append(_issue("PLAN010", f"{tp}.columns.{c['name']}",
                                  f"{t['name']} already has rows, so the new required column {c['name']} needs a default",
                                  needs_input=True, table=t["name"], column=c["name"]))
        if t["retention"]["policy"] in ("days", "last_n") and not t["retention"]["value"]:
            out.append(_issue("PLAN010", f"{tp}.retention", f"retention of {t['name']} is '{t['retention']['policy']}' "
                              "but has no number", needs_input=True, table=t["name"], policy=t["retention"]["policy"]))

    owners = _owner_map(p)
    per_module: dict[str, int] = {}
    for r in p["routes"]:
        rp = f"routes.{r['key']}"
        # PLAN003: route module
        if not r["module"] or r["module"] not in modules:
            out.append(_issue("PLAN003", f"{rp}.module", f"route {r['key']} belongs to no planned module"
                              + (f" ({r['module']})" if r["module"] else "")))
        else:
            per_module[r["module"]] = per_module.get(r["module"], 0) + 1
            for w in r["tables"]["writes"]:
                o = owners.get(w)
                if o and o != r["module"] and r["module"] not in tables[w]["writers"]:
                    out.append(_issue("PLAN003", f"{rp}.tables", f"{r['key']} writes {w}, which {o} owns; add "
                                      f"{r['module']} to {w}.writers or move the route"))
        # PLAN009: list pagination; state-changing errors and idempotency
        m = _ROUTE_RE.match(r["key"])
        if _is_list_route(r["key"]) and r["pagination"] is False:
            out.append(_issue("PLAN009", rp, f"{r['key']} lists records but pagination is off", "warning"))
        if m and m.group(1).upper() in STATE_CHANGING and (not r["errors"] or not r["idempotency"]):
            miss = [x for x, v in (("errors", r["errors"]), ("idempotency", r["idempotency"])) if not v]
            out.append(_issue("PLAN009", rp, f"{r['key']} changes state but declares no {' or '.join(miss)}", "warning"))
    # PLAN012
    for k, n in per_module.items():
        if n > MAX_MODULE_ROUTES:
            out.append(_issue("PLAN012", f"modules.{k}", f"module {k} has {n} routes; split it (at most {MAX_MODULE_ROUTES})",
                              "warning"))

    # PLAN005: screen routes
    for a in p["apps"]:
        for s in a["screens"]:
            for rk in s["routes"]:
                n = _rnorm(rk)
                if n not in routes and n not in fact_routes:
                    out.append(_issue("PLAN005", f"apps.{a['key']}.screens.{s['name']}",
                                      f"screen {s['name']} calls {rk}, which is neither planned nor a project route"))

    # PLAN006 / PLAN013: integrations
    for i in p["integrations"]:
        ip = f"integrations.{i['key']}"
        for name in i["secrets"]:
            if name not in secrets:
                out.append(_issue("PLAN006", ip, f"integration {i['key']} uses secret {name}, which is not in secrets",
                                  needs_input=True, secret=name, integration=i["key"]))
        if i["kind"] == "jobs" and (not i["contract"] or not i["smoke"]):
            miss = [x for x in ("contract", "smoke") if not i[x]]
            out.append(_issue("PLAN013", ip, f"Jobs integration {i['key']} has no {' or '.join(miss)}", "warning"))
        if i["kind"] == "jobs" and i["smoke"] and (why := _smoke_problem(i["smoke"], routes, fact_routes)):
            out.append(_issue("PLAN013", f"{ip}.smoke", f"Jobs integration {i['key']}: {why}. The smoke run calls this "
                              "project's own routes: start 'POST /runs', poll 'GET /runs/{id}', until 'status in [passed,failed]'",
                              "warning"))

    # PLAN007: secret hosts (raw values checked, so the normalization can be explained)
    raw_secrets = {_s(x.get("name"), 100): x for x in _dicts(body.get("secrets"))}
    for s in p["secrets"]:
        sp = f"secrets.{s['name']}"
        if not s["allowed_hosts"]:
            out.append(_issue("PLAN007", sp, f"secret {s['name']} has no allowed_hosts: the broker would refuse to send it",
                              needs_input=True, secret=s["name"]))
            continue
        for v in _strs((raw_secrets.get(s["name"]) or {}).get("allowed_hosts")):
            if hostname(v) != v:
                out.append(_issue("PLAN007", sp, f"allowed host {v!r} is stored as {hostname(v)!r}: hosts are hostnames "
                                  "only, with no scheme or port", "warning"))

    # PLAN008: environments
    conn_keys = {str(c.get("key")) for c in facts.get("connections") or [] if isinstance(c, dict)}
    calls_url = [i for i in p["integrations"] if i["key"] not in conn_keys and (i["kind"] == "http" or i["hosts"] or i["smoke"])]
    for e in p["environments"]:
        if not e["base_url"] and not e["placeholder"]:        # a placeholder is never called, so it needs no URL
            if calls_url:
                out.append(_issue("PLAN008", f"environments.{e['name']}", f"environment {e['name']} has no base_url",
                                  needs_input=True, environment=e["name"]))
            else:                                             # nothing calls it: studio-hosted apps need no URL
                out.append(_issue("PLAN008", f"environments.{e['name']}", f"environment {e['name']} has no base_url",
                                  "warning"))
    if any(e["refused_actions"] for e in p["environments"]) and not any(e["live"] for e in p["environments"]):
        out.append(_issue("PLAN008", "environments", "refused actions are declared but no environment is marked live",
                          needs_input=True))

    # PLAN011: build order
    order = p["build_order"]
    app_keys = {a["key"] for a in p["apps"]}
    topo, cycle = _topo(p)
    if cycle:
        out.append(_issue("PLAN011", "modules", f"modules depend on each other in a cycle: {', '.join(cycle)}"))
    for x in order:
        if x not in modules and not (x.startswith("app:") and x[4:] in app_keys):
            out.append(_issue("PLAN011", "build_order", f"build_order names {x}, which is not a planned module or app:<key>"))
    missing = [k for k in modules if k not in order]
    if missing:
        out.append(_issue("PLAN011", "build_order", f"build_order leaves out {', '.join(missing)}"))
    pos = {x: n for n, x in enumerate(order)}
    g = _module_graph(p)
    for k, deps in g.items():
        late = sorted(d for d in deps if k in pos and d in pos and pos[d] > pos[k])
        if late:
            out.append(_issue("PLAN011", "build_order", f"{k} comes before {', '.join(late)}, which it needs"))

    # PLAN014: helpers outside the shared module
    for m in p["modules"]:
        if m["kind"] != "shared" and m["helpers"]:
            out.append(_issue("PLAN014", f"modules.{m['key']}", f"helpers {', '.join(h['name'] for h in m['helpers'])} are "
                              f"planned in {m['key']}, which is not a shared module; move them to the shared module", "warning"))
    return out


# ── diffs ────────────────────────────────────────────────────────────────────


def _ch(kind: str, key: str, change: str, breaking: bool = False, **detail: Any) -> dict[str, Any]:
    return {"kind": kind, "key": key, "change": change, "breaking": breaking, **({"detail": detail} if detail else {})}


def diff_plans(prev: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """``{changes: [{kind, key, change, breaking, detail?}], breaking, affected: {modules, apps, tables}}``. Breaking:
    removing a route, table, column or enum value, a column type change, or a route moving to another module."""
    a = normalize(prev) if prev is not None else normalize({})
    b = normalize(new)
    changes: list[dict[str, Any]] = []
    at, bt = {t["name"]: t for t in a["data"]["tables"]}, {t["name"]: t for t in b["data"]["tables"]}
    changed_tables: set[str] = set()
    for n, t in bt.items():
        if n not in at:
            changes.append(_ch("table", n, "added"))
            changed_tables.add(n)
            continue
        if t == at[n]:
            continue
        changed_tables.add(n)
        d = diff_table(at[n]["columns"], t, 0, current_meta={"indexes": at[n]["indexes"], "description": at[n]["description"]})
        for r in d["refused"]:
            changes.append(_ch("column", f"{n}.{r.get('column') or r.get('from') or ''}", r["op"], True, reason=r["reason"]))
        for o in d["ops"]:
            col = o.get("column")
            changes.append(_ch("column", f"{n}.{col['name'] if isinstance(col, dict) else (col or '')}", o["op"]))
        rest = {k: v for k, v in t.items() if k not in ("columns", "indexes", "description")}
        if rest != {k: v for k, v in at[n].items() if k not in ("columns", "indexes", "description")}:
            changes.append(_ch("table", n, "changed"))
    for n in at:
        if n not in bt:
            changes.append(_ch("table", n, "removed", True))
            changed_tables.add(n)

    ar, br = {_rnorm(r["key"]): r for r in a["routes"]}, {_rnorm(r["key"]): r for r in b["routes"]}
    changed_routes: set[str] = set()
    touched_modules: set[str] = set()
    for k, r in br.items():
        if k not in ar:
            changes.append(_ch("route", r["key"], "added"))
        elif r["module"] != ar[k]["module"]:
            changes.append(_ch("route", r["key"], "moved", True, **{"from": ar[k]["module"], "to": r["module"]}))
            touched_modules.add(ar[k]["module"] or "")
        elif r != ar[k]:
            changes.append(_ch("route", r["key"], "changed"))
        else:
            continue
        changed_routes.add(k)
        touched_modules.add(r["module"] or "")
    for k, r in ar.items():
        if k not in br:
            changes.append(_ch("route", r["key"], "removed", True))
            changed_routes.add(k)
            touched_modules.add(r["module"] or "")

    am, bm = {m["key"]: m for m in a["modules"]}, {m["key"]: m for m in b["modules"]}
    for k, m in bm.items():
        if k not in am:
            changes.append(_ch("module", k, "added"))
            touched_modules.add(k)
        elif m != am[k]:
            changes.append(_ch("module", k, "changed"))
            touched_modules.add(k)
    for k in am:
        if k not in bm:
            changes.append(_ch("module", k, "removed"))
    for m in b["modules"]:
        if (set(m["owns"]) | set(m["reads"])) & changed_tables:
            touched_modules.add(m["key"])
    for r in b["routes"]:
        if (set(r["tables"]["reads"]) | set(r["tables"]["writes"])) & changed_tables:
            touched_modules.add(r["module"] or "")

    aa, ba = {x["key"]: x for x in a["apps"]}, {x["key"]: x for x in b["apps"]}
    touched_apps: set[str] = set()
    for k, x in ba.items():
        strip = lambda app: {kk: vv for kk, vv in app.items() if kk != "app_id"}  # noqa: E731
        if k not in aa:
            changes.append(_ch("app", k, "added"))
            touched_apps.add(k)
        elif strip(x) != strip(aa[k]):
            changes.append(_ch("app", k, "changed"))
            touched_apps.add(k)
        elif x["app_id"] != aa[k]["app_id"]:
            changes.append(_ch("app", k, "bound"))
        if any(_rnorm(rk) in changed_routes for s in x["screens"] for rk in s["routes"]):
            touched_apps.add(k)
    for k in aa:
        if k not in ba:
            changes.append(_ch("app", k, "removed"))

    for sect in ("integrations", "secrets", "environments", "nfr", "requirements", "roles_note", "build_order"):
        if a[sect] != b[sect]:
            changes.append(_ch(sect, sect, "changed"))
            if sect in ("integrations", "secrets", "environments"):
                touched_modules |= {m["key"] for m in b["modules"] if m["integrations"]}

    mod_order = [m["key"] for m in b["modules"]] + [k for k in am if k not in bm]
    return {"changes": changes, "breaking": any(c["breaking"] for c in changes),
            "affected": {"modules": [k for k in mod_order if k in touched_modules],
                         "apps": [k for k in ba if k in touched_apps], "tables": sorted(changed_tables)}}


# ── consumer helpers (pure; each takes a plan body) ─────────────────────────


def table_plan(plan: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((t for t in normalize(plan)["data"]["tables"] if t["name"] == name), None)


def route_contract(plan: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The planned route matching ``METHOD /path`` (parameter names and case do not matter)."""
    want = _rnorm(route_key(key))
    return next((r for r in normalize(plan)["routes"] if _rnorm(r["key"]) == want), None)


def answered_topics(plan: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in normalize(plan)["requirements"]["answers"]:
        if a["answer"]:
            out[a["topic"]] = f"{out[a['topic']]}; {a['answer']}" if a["topic"] in out and a["answer"] not in out[a["topic"]] \
                else out.get(a["topic"], a["answer"])
    return out


def build_order(plan: dict[str, Any]) -> list[str]:
    """The plan's build order when it is a valid topological order of its modules, otherwise one computed from
    ``depends_on`` and table reads; ``app:<key>`` entries come last."""
    p = normalize(plan)
    apps = [f"app:{a['key']}" for a in p["apps"]]
    order = p["build_order"]
    keys = [m["key"] for m in p["modules"]]
    if order and not [i for i in validate_plan(p) if i["code"] == "PLAN011"]:
        return order + [x for x in apps if x not in order]
    topo, cycle = _topo(p)
    return topo + cycle + [k for k in keys if k not in topo and k not in cycle] + apps


def module_for(plan: dict[str, Any], name_or_prompt: str | None) -> str | None:
    """The plan module a name or a prompt means: an exact key, a ``[plan:<key>]`` token, or a case-insensitive key."""
    if not name_or_prompt:
        return None
    keys = [m["key"] for m in normalize(plan)["modules"]]
    s = name_or_prompt.strip()
    if s in keys:
        return s
    m = _PLAN_TOKEN.search(s)
    if m and m.group(1) in keys:
        return m.group(1)
    return next((k for k in keys if k.lower() == s.lower()), None)


def _covers(ac: dict[str, Any], tags: set[str]) -> bool:
    return not ac["covers"] or any(c in tags for c in ac["covers"])


def module_spec(plan: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A ``spec``-shaped dict for module ``key`` that ``module_generator.spec_message`` accepts: owned tables as entities
    (read tables marked ``read_only``), state machines as workflows, the integrations its routes use, the acceptance
    criteria covering it, every plan answer, its route contracts, the shared module's helpers, limitations and roles."""
    p = normalize(plan)
    mod = next((m for m in p["modules"] if m["key"] == key), None)
    if mod is None:
        return None
    tables = {t["name"]: t for t in p["data"]["tables"]}
    routes = [r for r in p["routes"] if r["module"] == key]
    owned = [n for n in mod["owns"] if n in tables]
    read = [n for n in dict.fromkeys(mod["reads"] + [t for r in routes for t in r["tables"]["reads"] + r["tables"]["writes"]])
            if n in tables and n not in owned]
    entities = [{"name": n, "owner": tables[n]["owner"], "fields": tables[n]["columns"],
                 **({"read_only": True} if tables[n]["owner"] != key and key not in tables[n]["writers"] else
                    {"writer": True} if tables[n]["owner"] != key else {}),
                 **{k: tables[n][k] for k in ("description", "indexes", "business_key", "retention", "audit", "soft_delete")
                    if tables[n].get(k)}}
                for n in owned + read]
    workflows = []
    for n in owned:
        sm = tables[n].get("state_machine")
        if sm:
            col = next((c for c in tables[n]["columns"] if c["name"] == sm["field"]), {})
            workflows.append({"entity": n, "field": sm["field"], "states": col.get("enum") or [], "initial": sm["initial"],
                              "final": sm["final"], "transitions": sm["transitions"]})
    used = set(mod["integrations"]) | {i for r in routes for i in r["integrations"]}
    external = [{"name": i["key"], "use": i["purpose"] or i["kind"], "kind": i["kind"], "secrets": i["secrets"],
                 "hosts": i["hosts"], **({"job_types": i["job_types"]} if i["job_types"] else {}),
                 **({"contract": i["contract"]} if i["contract"] else {})}
                for i in p["integrations"] if i["key"] in used]
    tags = {f"module:{key}"} | {f"route:{r['key']}" for r in routes} | {f"table:{n}" for n in owned}
    tags |= {f"route:{_rnorm(r['key'])}" for r in routes}
    acceptance = [a["text"] for a in p["requirements"]["acceptance"] if _covers(a, tags)
                  or any(c.startswith("route:") and _rnorm(c[6:]) in {_rnorm(r["key"]) for r in routes} for c in a["covers"])]
    shared = next((m for m in p["modules"] if m["kind"] == "shared"), None)
    secrets = [s for s in p["secrets"] if any(s["name"] in e["secrets"] for e in external)]
    return {
        "summary": p["summary"] or p["title"], "module": key, "purpose": mod["purpose"],
        "entities": entities, "workflows": workflows,
        "roles": [{"name": "team", "can": [p["roles_note"]]}] if p["roles_note"] else [],
        "roles_note": p["roles_note"], "external_systems": external, "secrets": secrets,
        "acceptance_criteria": acceptance, "limitations": p["requirements"]["limitations"],
        "assumptions": p["requirements"]["assumptions"],
        "answered": [{"id": a["id"], "topic": a["topic"], "question": a["question"], "answer": a["answer"]}
                     for a in p["requirements"]["answers"]],
        "routes": routes, "depends_on": mod["depends_on"],
        "helpers": (shared or {}).get("helpers") or [], "shared_module": shared["key"] if shared else None,
        "nfr": p["nfr"], "conventions": p["data"]["conventions"],
        "environments": p["environments"],
    }


def _find_app(p: dict[str, Any], app_key: str) -> dict[str, Any] | None:
    return (next((a for a in p["apps"] if a["key"] == app_key), None)
            or next((a for a in p["apps"] if a["app_id"] and a["app_id"] == app_key), None)
            or next((a for a in p["apps"] if a["name"].lower() == (app_key or "").lower()), None))


def app_brief(plan: dict[str, Any], app_key: str) -> dict[str, Any] | None:
    """The plan app as a ``record_brief`` argument dict: users, screens, journeys, routes, states, acceptance_criteria,
    assumptions. ``app_key`` may be the plan key, the bound app id or the app's name."""
    p = normalize(plan)
    app = _find_app(p, app_key)
    if app is None:
        return None
    contracts = {_rnorm(r["key"]): r for r in p["routes"]}
    routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in app["screens"]:
        for rk in s["routes"]:
            n = _rnorm(rk)
            if n in seen:
                continue
            seen.add(n)
            m = _ROUTE_RE.match(rk)
            if m:
                routes.append({"method": m.group(1).upper(), "path": m.group(2),
                               "purpose": (contracts.get(n) or {}).get("purpose") or s["purpose"]})
    states: dict[str, str] = {}
    for s in app["screens"]:
        for k, v in s["states"].items():
            states[k] = f"{states[k]} | {s['name']}: {v}" if k in states else f"{s['name']}: {v}"
    for k, default in (("empty", "say there is nothing yet and how to add the first item"), ("loading", "skeleton rows"),
                       ("error", "a banner with the error message and a retry")):
        states.setdefault(k, default)
    tags = {f"app:{app['key']}"} | {f"screen:{s['name']}" for s in app["screens"]}
    journeys = [j for s in app["screens"] for j in s["journeys"]] or [f"{s['name']}: {s['purpose']}" for s in app["screens"]]
    return {"users": app["users"] or p["roles_note"] or "the project team",
            "screens": [{"name": s["name"], "purpose": s["purpose"]} for s in app["screens"]],
            "journeys": journeys, "routes": routes, "states": states,
            "acceptance_criteria": [a["text"] for a in p["requirements"]["acceptance"]
                                    if _covers(a, tags) or any(c.startswith("route:") and _rnorm(c[6:]) in seen for c in a["covers"])],
            "assumptions": p["requirements"]["assumptions"]}


def with_app_bound(plan: dict[str, Any], app_key: str, app_id: str) -> dict[str, Any]:
    """A copy of ``plan`` with plan app ``app_key`` bound to the studio app ``app_id`` (metadata; not a breaking change)."""
    out = copy.deepcopy(plan)
    for a in out.get("apps") or []:
        if isinstance(a, dict) and (a.get("key") or a.get("name")) == app_key:
            a["app_id"] = app_id
    return out


# ── store ────────────────────────────────────────────────────────────────────


def _err(status: int, code: str, message: str, **extra: Any) -> Exception:
    from .project_service import ProjectError

    return ProjectError(status, code, message, **extra)


def phase_of(row: Any) -> str:
    if row.status != "draft":
        return row.status
    return "questions" if row.questions else "review"


def plan_to_dict(row: Any, issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {"id": row.id, "project_id": row.project_id, "version": row.version, "status": row.status, "phase": phase_of(row),
           "base_version": row.base_version, "body": row.body, "questions": row.questions or [], "round": row.round,
           "created_by": row.created_by, "approved_by": row.approved_by,
           "approved_at": row.approved_at.isoformat() if row.approved_at else None,
           "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}
    if issues is not None:
        out["issues"] = issues
    return out


def version_summary(row: Any) -> dict[str, Any]:
    d = plan_to_dict(row)
    del d["body"]
    d["title"] = (row.body or {}).get("title") or ""
    return d


async def project_facts(session: Any, project_id: str) -> dict[str, Any]:
    """What exists in the project now (``gather_facts`` shape): tables with columns and row counts, routes, secrets."""
    from ..db.models import ProjectModel
    from .module_generator import gather_facts

    project = await session.get(ProjectModel, project_id)
    if project is None:
        raise _err(404, "project_not_found", "Project not found")
    return await gather_facts(session, project)


async def _rows(session: Any, project_id: str) -> list[Any]:
    from sqlalchemy import select

    from ..db.plan_models import ProjectPlanModel

    return list((await session.execute(select(ProjectPlanModel).where(ProjectPlanModel.project_id == project_id)
                                       .order_by(ProjectPlanModel.version.desc()))).scalars().all())


async def _row(session: Any, project_id: str, version: int, *, lock: bool = False) -> Any:
    from sqlalchemy import select

    from ..db.plan_models import ProjectPlanModel

    q = select(ProjectPlanModel).where(ProjectPlanModel.project_id == project_id, ProjectPlanModel.version == version)
    if lock:
        q = q.with_for_update()
    row = (await session.execute(q)).scalars().first()
    if row is None:
        raise _err(404, "plan_not_found", f"plan version {version} not found")
    return row


async def _approved_row(session: Any, project_id: str) -> Any:
    from sqlalchemy import select

    from ..db.plan_models import ProjectPlanModel

    return (await session.execute(select(ProjectPlanModel).where(ProjectPlanModel.project_id == project_id,
                                                                 ProjectPlanModel.status == "approved"))).scalars().first()


async def list_versions(session: Any, project_id: str) -> list[dict[str, Any]]:
    return [version_summary(r) for r in await _rows(session, project_id)]


async def get_version(session: Any, project_id: str, version: int) -> Any:
    return await _row(session, project_id, version)


async def latest_draft(session: Any, project_id: str) -> Any:
    return next((r for r in await _rows(session, project_id) if r.status == "draft"), None)


async def load_approved(session: Any, project_id: str) -> dict[str, Any] | None:
    """``{version, body}`` of the project's approved plan, or ``None`` when nothing is approved. What builders read."""
    row = await _approved_row(session, project_id)
    return {"version": row.version, "body": row.body} if row is not None else None


async def create_draft(session: Any, project_id: str, body: Any, *, questions: list[dict[str, Any]] | None = None,
                       round_no: int = 0, base_version: int | None = None, actor: str | None = None,
                       commit: bool = True) -> Any:
    """A new draft version (``max(version) + 1``) holding the normalized ``body``."""
    from sqlalchemy.exc import IntegrityError

    from ..db.plan_models import ProjectPlanModel
    from ..utils.ids import generate_id

    rows = await _rows(session, project_id)
    version = (rows[0].version + 1) if rows else 1
    now = datetime.now()
    row = ProjectPlanModel(id=generate_id("plan"), project_id=project_id, version=version, status="draft",
                           base_version=base_version, body=normalize(body), questions=questions or [], round=round_no,
                           created_by=actor, created_at=now, updated_at=now)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise _err(409, "plan_conflict", "another plan version was created at the same time; reload") from e
    if commit:
        await session.commit()
    return row


async def update_draft(session: Any, project_id: str, version: int, *, body: Any = None,
                       questions: list[dict[str, Any]] | None = None, round_no: int | None = None,
                       base_updated_at: str | None = None, commit: bool = True) -> Any:
    """Save edits to a draft. ``base_updated_at`` (the ``updated_at`` the editor started from) must still be current, or
    409 ``plan_conflict``. ``migrations`` are derived on approval and never taken from an edit."""
    row = await _row(session, project_id, version, lock=True)
    if row.status != "draft":
        raise _err(409, "plan_not_draft", f"plan v{version} is {row.status}; an approved plan is changed by revising it")
    if base_updated_at is not None and base_updated_at != row.updated_at.isoformat():
        raise _err(409, "plan_conflict", "the plan changed since you opened it; reload", current_updated_at=row.updated_at.isoformat())
    if body is not None:
        nb = normalize(body)
        nb["migrations"] = (row.body or {}).get("migrations") or []
        row.body = nb
    if questions is not None:
        row.questions = questions
    if round_no is not None:
        row.round = round_no
    row.updated_at = datetime.now()
    session.add(row)
    if commit:
        await session.commit()
        await session.refresh(row)
    return row


async def approve(session: Any, project_id: str, version: int, *, accept_breaking: bool = False, actor: str | None = None,
                  facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Approve draft ``version``: refused while it has open questions (409), any error issue (422 ``plan_invalid``) or a
    breaking change against the approved plan without ``accept_breaking`` (409 ``breaking_change``). Writes the derived
    ``migrations``, supersedes the previous approved version and mirrors the plan into project knowledge."""
    row = await _row(session, project_id, version, lock=True)
    if row.status != "draft":
        raise _err(409, "plan_not_draft", f"plan v{version} is {row.status}")
    if row.questions:
        raise _err(409, "plan_questions_open", "answer the open questions first", questions=row.questions)
    if facts is None:
        facts = await project_facts(session, project_id)
    issues = validate_plan(row.body, facts)
    if has_errors(issues):
        raise _err(422, "plan_invalid", "the plan has errors; fix them before approving",
                   issues=[i for i in issues if i["severity"] == "error"])
    prev = await _approved_row(session, project_id)
    diff = diff_plans(prev.body if prev else None, row.body)
    if prev is not None and diff["breaking"] and not accept_breaking:
        raise _err(409, "breaking_change", "this version removes or changes what builders already rely on; confirm with "
                   "accept_breaking", diff=diff)
    fact_tables = {str(t.get("name")): t for t in facts.get("tables") or [] if isinstance(t, dict)}
    if prev is not None:
        base: dict[str, Any] = prev.body
    else:   # first approval: migrate from what the project already has
        base = {"data": {"tables": [{"name": n, "columns": t.get("columns") or [], "description": t.get("description")}
                                    for n, t in fact_tables.items()]}}
    counts = {n: int(t.get("row_count") or 0) for n, t in fact_tables.items()}
    steps = [{"version": row.version, **m} for m in plan_migrations(base, row.body, row_counts=counts)
             if m["table"] in {t["name"] for t in normalize(row.body)["data"]["tables"]}]
    body = dict(row.body)
    body["migrations"] = list((prev.body or {}).get("migrations") or [] if prev else []) + steps
    now = datetime.now()
    if prev is not None:
        prev.status, prev.updated_at = "superseded", now
        session.add(prev)
        await session.flush()   # the partial unique index allows one approved row per project
    row.body, row.status, row.approved_by, row.approved_at, row.updated_at = body, "approved", actor, now, now
    session.add(row)
    await session.flush()
    knowledge = await mirror_to_knowledge(session, project_id, row.version, body, commit=False)
    await _record_files(session, project_id, prev, row.version, body)
    await session.commit()
    await session.refresh(row)
    return {**plan_to_dict(row, issues), "diff": diff, "migrations": steps, "knowledge": knowledge}


async def _record_files(session: Any, project_id: str, prev: Any, version: int, body: dict[str, Any]) -> None:
    """PLAN.md and plan.json in the project file change log (never fails the approval)."""
    from . import project_files as pf

    try:
        old_md = pf.plan_markdown(prev.body, prev.version) if prev is not None else None
        old_json = pf.plan_json(prev.body) if prev is not None else None
        changes = [{"path": "PLAN.md", "before": old_md, "after": pf.plan_markdown(body, version), "summary": f"approved plan v{version}"},
                   {"path": "plan.json", "before": old_json, "after": pf.plan_json(body), "summary": f"approved plan v{version}"}]
    except Exception:  # noqa: BLE001
        return
    await pf.record_changes(session, project_id, changes, actor="plan")


async def revise(session: Any, project_id: str, body: Any = None, *, actor: str | None = None) -> dict[str, Any]:
    """A new draft from the approved plan (or ``body``, an edited copy of it), ``base_version`` = the approved version,
    with its diff and the modules / apps / tables it affects."""
    prev = await _approved_row(session, project_id)
    if prev is None:
        raise _err(409, "plan_not_approved", "nothing is approved yet; draft and approve a plan first")
    nb = normalize(body if body is not None else copy.deepcopy(prev.body))
    nb["migrations"] = (prev.body or {}).get("migrations") or []
    row = await create_draft(session, project_id, nb, base_version=prev.version, actor=actor)
    diff = diff_plans(prev.body, row.body)
    return {**plan_to_dict(row), "diff": diff, "affected": diff["affected"]}


async def bind_app(session: Any, project_id: str, app_key: str, app_id: str, *, commit: bool = True) -> bool:
    """Record that studio app ``app_id`` implements plan app ``app_key`` (matched by key or name) in the approved plan.
    Metadata only, so the approved row is updated in place. False when there is no approved plan or no such app."""
    row = await _approved_row(session, project_id)
    if row is None:
        return False
    app = _find_app(normalize(row.body), app_key)
    if app is None:
        return False
    if app["app_id"] == app_id:
        return True
    row.body = with_app_bound(row.body, app["key"], app_id)
    row.updated_at = datetime.now()
    session.add(row)
    if commit:
        await session.commit()
    return True


async def mirror_to_knowledge(session: Any, project_id: str, version: int, body: dict[str, Any], *,
                              commit: bool = True) -> dict[str, int]:
    """Write the approved plan where the builders' retrieval already looks: ``spec`` key ``plan``, one ``clarify_answer``
    per answer (key ``plan:<qid>``), one ``table_schema`` per planned table and one ``requirement`` per acceptance criterion
    (key ``plan:ac:<id>``). Answer and criterion rows of an earlier plan that this one no longer has are removed."""
    from . import project_knowledge as pk

    p = normalize(body)
    ref = f"plan:v{version}"
    n = {"spec": 0, "clarify_answer": 0, "table_schema": 0, "requirement": 0}
    await pk.upsert(session, project_id, "spec", "plan", title=f"Project plan v{version}: {p['title']}".strip(": "),
                    body={"version": version, "title": p["title"], "summary": p["summary"], "roles_note": p["roles_note"],
                          "modules": [{"key": m["key"], "kind": m["kind"], "owns": m["owns"]} for m in p["modules"]],
                          "routes": [f"{r['key']} ({r['module']})" for r in p["routes"]],
                          "apps": [{"key": a["key"], "screens": [s["name"] for s in a["screens"]]} for a in p["apps"]],
                          "build_order": build_order(p), "limitations": p["requirements"]["limitations"],
                          "assumptions": p["requirements"]["assumptions"]},
                    status="approved", source="design", source_ref=ref, commit=False)
    n["spec"] = 1
    keep_ans: set[str] = set()
    for a in p["requirements"]["answers"]:
        key = f"plan:{a['id']}"
        keep_ans.add(key)
        await pk.upsert(session, project_id, "clarify_answer", key, title=a["question"][:200],
                        body={"question": a["question"], "answer": a["answer"], "topic": a["topic"], "plan_version": version},
                        status="approved", source="human", source_ref=ref, commit=False)
        n["clarify_answer"] += 1
    for t in p["data"]["tables"]:
        await pk.upsert(session, project_id, "table_schema", t["name"], title=f"table {t['name']} (planned)",
                        body={k: t[k] for k in ("name", "owner", "writers", "description", "columns", "indexes", "business_key",
                                                "retention", "state_machine") if t.get(k) not in (None, [], "")},
                        status="approved", source="design", source_ref=ref, commit=False)
        n["table_schema"] += 1
    keep_ac: set[str] = set()
    for a in p["requirements"]["acceptance"]:
        key = f"plan:ac:{a['id']}"
        keep_ac.add(key)
        await pk.upsert(session, project_id, "requirement", key, title=a["text"][:200],
                        body={"text": a["text"], "covers": a["covers"], "plan_version": version},
                        status="approved", source="design", source_ref=ref, commit=False)
        n["requirement"] += 1
    for kind, keep in (("clarify_answer", keep_ans), ("requirement", keep_ac)):
        for r in await pk.list_kind(session, project_id, kind):
            if r["key"].startswith("plan:") and r["key"] not in keep:
                await pk.delete(session, project_id, kind, r["key"], commit=False)
    if commit:
        await session.commit()
    return n
