"""Deterministic first draft of a planned module, written with no model call.

``scaffold_module(plan, key)`` declares a ``studio.resource`` for every table the module owns or is a planned writer of, and
maps the plan's CRUD-shaped routes onto it as ordinary ``@api`` one-liners (so discovery, self-tests, coverage and the api
client see normal routes). What the platform cannot know is left as a clearly marked TODO: non-CRUD routes become handlers
that answer 501, shared helpers raise NotImplementedError, and business rules (who may move a row, extra error codes) are
listed. The generator saves the scaffold, then asks the model only for the TODO list, as patches."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from ..schema_migrations import SYSTEM_COLUMNS

KNOWN_CODES = {"not_found", "validation_error", "unique_violation", "illegal_transition"}
PAGE_FIELDS = {"rows", "total", "limit", "offset"}
ANYONE = {"", "any", "anyone", "everyone", "all", "team", "any user"}
ROUTE_AUTHS = ("none", "app_token", "api_key")
COLUMN_KEYS = ("name", "type", "required", "default", "unique", "enum", "ref", "index")
_PARAM = re.compile(r"^\{(\w+)\}$")


def _lit(v: Any, indent: int = 0) -> str:
    """A Python literal in the module's style (double quotes, True/False/None)."""
    if isinstance(v, bool) or v is None:
        return repr(v)
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(str(k))}: {_lit(x)}" for k, x in v.items()) + "}"
    items = [_lit(x) for x in v]
    one = "[" + ", ".join(items) + "]"
    if len(one) <= 100 or indent < 0:
        return one
    pad = " " * (indent + 4)
    return "[\n" + ",\n".join(pad + i for i in items) + ",\n" + " " * indent + "]"


def _ident(text: str) -> str:
    s = re.sub(r"\W+", "_", text).strip("_").lower()
    return ("x_" + s) if not s or s[0].isdigit() else s


def _singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith(("sses", "xes", "ches", "shes")):
        return name[:-2]
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def _split(key: str) -> tuple[str, list[str]]:
    method, _, path = key.partition(" ")
    return method.upper(), [p for p in path.strip().split("/") if p]


def _table_for(segs: list[str], resources: dict[str, Any], route: dict[str, Any]) -> str | None:
    if segs:
        first = _ident(segs[0])
        for cand in (first, first + "s", _singular(first)):
            if cand in resources:
                return cand
    touched = [t for t in route["tables"]["writes"] + route["tables"]["reads"] if t in resources]
    return touched[0] if len(set(touched)) == 1 else None


def _target(sm: dict[str, Any] | None, key: str, verb: str) -> tuple[list[dict[str, Any]], bool]:
    """The transitions a ``POST /x/{id}/verb`` route performs: those naming the route, else those whose target state reads
    like the verb (approve -> approved). Second: whether the route was named by the plan."""
    if not sm:
        return [], False
    norm = re.sub(r"\{\w+\}", "{}", key)
    named = [t for t in sm["transitions"] if re.sub(r"\{\w+\}", "{}", t.get("route") or "") == norm]
    if named:
        return named, True
    forms = {verb, verb + "d", verb + "ed", verb + "led", verb.rstrip("e") + "ed", verb.rstrip("y") + "ied"}
    return [t for t in sm["transitions"] if t["to"] in forms], False


class _Module:
    def __init__(self, plan: dict[str, Any], key: str) -> None:
        from .. import project_plan as pp

        self.p = pp.normalize(plan)
        self.key = key
        self.mod = next(m for m in self.p["modules"] if m["key"] == key)
        self.tables = {t["name"]: t for t in self.p["data"]["tables"]}
        self.routes = [r for r in self.p["routes"] if r["module"] == key]
        writes = {t for r in self.routes for t in r["tables"]["writes"]}
        audit = self._audit_table()
        self.audit = audit if audit and (audit in self.mod["owns"] or key in self.tables[audit]["writers"]) else None
        # the audit table is written through each resource's audit rows, so it is a resource only where it is owned
        self.resources = {n: self.tables[n] for n in self.tables
                          if n in self.mod["owns"] or (key in self.tables[n]["writers"] and n in writes and n != audit)}
        self.funcs: set[str] = set(self.resources)
        self.covered: list[str] = []
        self.todo: list[dict[str, Any]] = []
        self.blocks: list[str] = []

    def _audit_table(self) -> str | None:
        for n, t in self.tables.items():
            cols = {c["name"] for c in t["columns"]}
            if n in ("audit_log", "audit_logs", "audit", "audit_events") or {"event", "entity"} <= cols:
                return n
        return None

    def _fn(self, base: str) -> str:
        name = _ident(base)
        while name in self.funcs:
            name += "_route"
        self.funcs.add(name)
        return name

    # -- resources --------------------------------------------------------------------------------------------------
    def resource_block(self, name: str, t: dict[str, Any]) -> str:
        cols = [{k: c[k] for k in COLUMN_KEYS if k in c and c[k] not in (None, False)} for c in t["columns"]]
        opts = [f"columns={_lit(cols, 4)}"]
        refs = {}
        for c in t["columns"]:
            ref = str(c.get("ref") or "").split(".")[0]
            if ref in self.tables:
                refs[c["name"]] = f'studio.db.table({json.dumps(ref)})'
        if refs:
            opts.append("refs={" + ", ".join(f"{json.dumps(k)}: {v}" for k, v in refs.items()) + "}")
        if t.get("state_machine"):
            sm = t["state_machine"]
            moves = "".join(f"\n        {_lit({'from': x['from'], 'to': x['to']})}," for x in sm["transitions"])
            opts.append(f'states={{"field": {json.dumps(sm["field"])}, "initial": {_lit(sm["initial"])}, '
                        f'"final": {_lit(sm["final"])}, "transitions": [{moves}\n    ]}}')
        search = [c["name"] for c in t["columns"] if c["type"] == "string" and not c.get("enum") and not c.get("ref")
                  and not c["name"].endswith(("_id", "_by", "_key"))][:3]
        if search:
            opts.append(f"search={_lit(search)}")
        soft = (self.p["data"].get("conventions") or {}).get("soft_delete") or "archived_at"
        if t.get("soft_delete") and soft in {c["name"] for c in t["columns"]}:
            opts.append(f"archive={json.dumps(soft)}")
        if self.audit and name != self.audit:
            acols = [c["name"] for c in self.tables[self.audit]["columns"]]
            opts.append(f'audit=studio.db.table({json.dumps(self.audit)}), audit_columns={_lit(acols)}')
        for r in self.routes:
            m = re.match(r"natural_key:(\w+)$", r.get("idempotency") or "")
            if m and _split(r["key"])[0] == "POST" and m.group(1) in {c["name"] for c in t["columns"]} \
                    and _table_for(_split(r["key"])[1], self.resources, r) == name:
                opts.append(f"idempotent_key={json.dumps(m.group(1))}")
                break
        body = ",\n    ".join(opts)
        return f'{name} = studio.resource(\n    studio.db.table({json.dumps(name)}),\n    {body},\n)'

    # -- routes -----------------------------------------------------------------------------------------------------
    def _decorator(self, r: dict[str, Any]) -> str:
        method, _ = _split(r["key"])
        path = r["key"].partition(" ")[2].strip()
        kw = [json.dumps(path)]
        if r.get("auth") in ROUTE_AUTHS:
            kw.append(f"auth={json.dumps(r['auth'])}")
        if r.get("purpose"):
            kw.append(f"description={json.dumps(r['purpose'][:200])}")
        return f"@api.{method.lower()}({', '.join(kw)})"

    @staticmethod
    def _returns(r: dict[str, Any], default: str) -> str:
        names = [f["name"] for f in r["response"]["fields"]]
        return "{" + ", ".join(names) + "}" if names else default

    def crud(self, r: dict[str, Any]) -> tuple[str, str, str] | None:
        """(function name, body line, returns) when the route is plain CRUD on one resource, else None."""
        method, segs = _split(r["key"])
        name = _table_for(segs, self.resources, r)
        if name is None or r["integrations"]:
            return None
        t = self.resources[name]
        cols = {c["name"] for c in t["columns"]}
        req_f = {f["name"] for f in r["request"]["fields"]}
        resp_f = {f["name"] for f in r["response"]["fields"]}
        row_ok = resp_f <= cols | set(SYSTEM_COLUMNS)
        one = _singular(name)
        if any(_PARAM.match(s) for s in segs[:1]) or len(segs) > 3:
            return None
        if len(segs) == 1:
            if method == "GET" and resp_f <= PAGE_FIELDS | cols:
                return f"list_{name}", f"return {name}.list(req)", "{rows, total, limit, offset}"
            effect = any(x.get("effect") and re.sub(r"\{\w+\}", "{}", x.get("route") or "") == r["key"]
                         for x in (t.get("state_machine") or {}).get("transitions") or [])
            if method == "POST" and req_f <= cols and row_ok and not effect:
                return f"create_{one}", f"return {name}.create(req)", "the new row (201)"
            return None
        if not _PARAM.match(segs[1]):
            return None
        param = _PARAM.match(segs[1]).group(1)
        load = "" if param == "id" else f', "{param}"'
        if len(segs) == 2:
            if load:
                return None
            if method == "GET" and row_ok:
                return f"get_{one}", f"return {name}.get(req)", "the row"
            if method in ("PUT", "PATCH") and req_f <= cols and row_ok:
                return f"update_{one}", f"return {name}.update(req)", "the updated row"
            if method == "DELETE":
                verb = "archive" if "archive=" in self.resource_block(name, t) else "delete"
                return f"{verb}_{one}", f"return {name}.delete(req)", "the archived row" if verb == "archive" else "{id, deleted}"
            return None
        verb = _ident(segs[2])
        moves, _named = _target(t.get("state_machine"), r["key"], verb)
        targets = {x["to"] for x in moves}
        if method != "POST" or len(targets) != 1 or any(x.get("effect") for x in moves) or load or not row_ok \
                or not req_f <= cols:
            return None
        to = targets.pop()
        extra = ""
        if req_f:
            need = [f["name"] for f in r["request"]["fields"] if f.get("required")]
            extra = f", fields={_lit(sorted(req_f))}" + (f", required={_lit(need)}" if need else "")
        return f"{verb}_{one}", f"return {name}.transition(req, {json.dumps(to)}{extra})", "the row after the move"

    def rules(self, r: dict[str, Any], fn: str) -> None:
        """Business rules a mapped route still needs, as hooks."""
        method, segs = _split(r["key"])
        name = _table_for(segs, self.resources, r)
        t = self.resources.get(name or "", {})
        who = False
        for x in (t.get("state_machine") or {}).get("transitions") or []:
            if re.sub(r"\{\w+\}", "{}", x.get("route") or "") == re.sub(r"\{\w+\}", "{}", r["key"]) \
                    and (x.get("who") or "").strip().lower() not in ANYONE:
                who = True
                self.todo.append({"kind": "rule", "route": r["key"], "function": fn,
                                  "text": f"{r['key']}: only {x['who']} may move {name} {x['from']} -> {x['to']}; add a "
                                          f"@{name}.before_transition hook that raises error(403, \"forbidden\", ...)"})
        extra = [e for e in r["errors"] if e.get("code") and e["code"] not in KNOWN_CODES
                 and not (who and e.get("status") == 403)]     # the who-rule above answers the 403
        if extra:
            codes = ", ".join(f"{e['status']} {e['code']}" for e in extra)
            hook = "before_transition" if len(segs) == 3 else {"POST": "before_create", "DELETE": "before_delete"}.get(
                method, "validate" if method != "GET" else "")
            self.todo.append({"kind": "rule", "route": r["key"], "function": fn,
                              "text": f"{r['key']}: answer {codes} where the plan says"
                                      + (f" (a @{name}.{hook} hook)" if hook else " (inside the handler)")})

    def route_block(self, r: dict[str, Any]) -> str:
        got = self.crud(r)
        dec = self._decorator(r)
        if got is not None:
            fn, line, ret = got
            fn = self._fn(fn)
            self.covered.append(r["key"])
            self.rules(r, fn)
            doc = f"{(r['purpose'] or r['key'])[:1].upper()}{(r['purpose'] or r['key'])[1:]}. Returns {ret}."
            return f'{dec}\ndef {fn}(req):\n    """{_doc(doc)}"""\n    {line}\n'
        method, segs = _split(r["key"])
        words = [s for s in segs if not _PARAM.match(s)]
        fn = self._fn("_".join([{"GET": "get", "POST": "post", "PUT": "put", "PATCH": "patch", "DELETE": "delete"}[method]]
                               + words) if len(words) != 2 else f"{words[1]}_{_singular(words[0])}")
        notes = [r["purpose"] or r["key"]]
        if r["request"]["fields"]:
            notes.append("Body: " + ", ".join(f["name"] + ("*" if f.get("required") else "") for f in r["request"]["fields"]))
        notes.append("Returns " + self._returns(r, "JSON"))
        if r["errors"]:
            notes.append("Errors: " + ", ".join(f"{e['status']} {e['code']}" for e in r["errors"]))
        if r.get("idempotency"):
            notes.append(f"Idempotency: {r['idempotency']}")
        if r["integrations"]:
            notes.append("Uses: " + ", ".join(r["integrations"]))
        self.todo.append({"kind": "route", "route": r["key"], "function": fn, "text": f"{r['key']}: " + "; ".join(notes)})
        return (f'{dec}\ndef {fn}(req):\n    """TODO: {_doc(". ".join(notes))}."""\n'
                f'    raise HTTPError(501, {{"error": "not_built", "message": {json.dumps(r["key"] + " is not built yet")}}})\n')

    def helper_block(self, h: dict[str, Any]) -> str:
        sig = re.sub(r"\s*->.*$", "", h.get("signature") or "").strip()
        try:
            fn = ast.parse(f"def {sig}:\n    pass\n").body[0]
            assert isinstance(fn, ast.FunctionDef) and fn.name == h["name"]
            head = sig
        except Exception:  # noqa: BLE001 - a free-text signature still gets a callable stub
            head = f"{_ident(h['name'])}(*args, **kwargs)"
        self.funcs.add(h["name"])
        self.todo.append({"kind": "helper", "function": h["name"], "text": f"helper {h.get('signature') or h['name']}: "
                                                                           f"{h.get('purpose') or ''}".rstrip(": ")})
        return (f'def {head}:\n    """TODO: {_doc(h.get("purpose") or h["name"])}."""\n'
                f'    raise NotImplementedError({json.dumps(h["name"] + " is not built yet")})\n')

    def source(self) -> str:
        title = self.mod.get("purpose") or f"the {self.key} module"
        out = [f'"""{self.key}: {_doc(title)}.\n\nStandard routes are studio.resource handlers declared from the approved plan; '
               'business rules go in the\nresource hooks (@res.validate, @res.before_create, @res.before_transition, ...)."""',
               "", "import studio", "from studio import HTTPError, api", ""]
        for name, t in self.resources.items():
            out += ["", self.resource_block(name, t)]
        if self.mod["kind"] == "shared":
            for h in self.mod["helpers"]:
                out += ["", "", self.helper_block(h).rstrip("\n")]
        for r in self.routes:
            out += ["", "", self.route_block(r).rstrip("\n")]
        text = "\n".join(out).rstrip() + "\n"
        if "HTTPError" not in text.split("from studio import HTTPError, api", 1)[1]:
            text = text.replace("from studio import HTTPError, api", "from studio import api")
        return text


def _doc(text: str) -> str:
    return " ".join(str(text).replace('"""', "'").replace("\\", "/").split()).rstrip(".")


def scaffold_module(plan: dict[str, Any], key: str) -> dict[str, Any]:
    """``{source, covered: [route keys mapped with no model], todo: [{kind: route|helper|rule, route?, function, text}],
    routes: n planned}`` for plan module ``key``."""
    m = _Module(plan, key)
    src = m.source()
    return {"source": src, "covered": m.covered, "todo": m.todo, "routes": len(m.routes)}


def todo_message(todo: list[dict[str, Any]]) -> str:
    """What the model is asked for after the scaffold is saved: only the TODO list, as patches."""
    lines = ["The module above is a scaffold that is ALREADY SAVED: its studio.resource routes are complete and tested. "
             "Do not rewrite or re-emit it. With patch_module (several ops in one call), write ONLY these:"]
    for t in todo:
        where = f" [{t['function']}]" if t.get("function") else ""
        lines.append(f"- {t['text']}{where}")
    lines.append("Replace each TODO function with replace_function (keep its decorator); add business rules as resource hooks "
                 "with add_function; raise errors with `from studio.resources import error` -> error(status, code, message, field).")
    return "\n".join(lines)


_NOT_BUILT = re.compile(r"is not built yet|\"not_built\"")


def leftovers(source: str) -> list[str]:
    """The scaffold's TODO functions still in ``source`` (a 501 route stub or a NotImplementedError helper)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(source, n) or ""
            if _NOT_BUILT.search(seg):
                route = next((f"{d.func.attr.upper()} {d.args[0].value}" for d in n.decorator_list
                              if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args
                              and isinstance(d.args[0], ast.Constant)), None)
                out.append(route or n.name)
    return out
