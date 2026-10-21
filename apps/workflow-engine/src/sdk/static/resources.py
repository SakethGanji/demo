"""``studio.resource`` — one declaration gives a table its standard routes (list, get, create, update, delete or archive,
state transitions), validated from the column list, with one error shape and optional audit rows.

    import studio
    from studio import api

    expenses = studio.resource(studio.db.table("expenses"), columns=[...plan columns...],
                               states={"field": "status", "initial": "draft", "final": ["paid"],
                                       "transitions": [{"from": "draft", "to": "submitted"}]},
                               audit=studio.db.table("audit_log"))

    @api.get("/expenses", auth="app_token", name="listExpenses")
    def list_expenses(req):
        return expenses.list(req)

    @expenses.before_transition
    def only_managers_approve(row, to, req):
        ...

Routes stay ordinary ``@api`` functions (the engine discovers them statically), and each one calls a handler here.
Errors are ``HTTPError(status, {"error": code, "message": ..., "field"?: ...})`` with the codes ``not_found``,
``validation_error``, ``unique_violation`` and ``illegal_transition``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .api import HTTPError, Request, Response
from .db import Table

SYSTEM = ("id", "created_at", "updated_at")
HOOKS = ("validate", "before_create", "after_create", "before_update", "after_update", "before_transition",
         "after_transition", "before_delete", "after_delete")
MAX_LIMIT = 1000


def error(status: int, code: str, message: str, field: str | None = None) -> HTTPError:
    """The standard error: ``raise error(409, "illegal_transition", "...")``."""
    body = {"error": code, "message": message}
    if field:
        body["field"] = field
    return HTTPError(status, body)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _typed(col: dict, value: Any, *, from_query: bool = False) -> Any:
    """The value as the column's type, or ValueError. Query strings are converted; JSON bodies must already match."""
    t = col.get("type") or "string"
    if value is None:
        return None
    if t == "integer":
        if from_query and isinstance(value, str):
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("an integer")
    elif t == "number":
        if from_query and isinstance(value, str):
            value = float(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("a number")
    elif t == "boolean":
        if from_query and isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
            value = value.lower() in ("true", "1")
        if not isinstance(value, bool):
            raise ValueError("true or false")
    elif t == "datetime":
        if not isinstance(value, str):
            raise ValueError("an ISO date-time string")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif t == "string" and not isinstance(value, str):
        raise ValueError("a string")
    if col.get("enum") and value not in col["enum"]:
        raise ValueError("one of " + ", ".join(map(str, col["enum"])))
    return value


class Resource:
    def __init__(self, table: Table, *, columns: list[dict] = (), filters: list[str] | None = None,
                 sort: str = "-created_at", search: list[str] = (), states: dict | None = None,
                 refs: dict[str, Table] | None = None, archive: str | None = None, audit: Table | None = None,
                 audit_columns: list[str] | None = None, idempotent_key: str | None = None) -> None:
        self.table, self.name = table, table.name
        self.columns = {c["name"]: c for c in columns if c.get("name") not in SYSTEM}
        self.states = states or None
        self.refs = dict(refs or {})
        self.archive, self.audit_table, self.idempotent_key = archive, audit, idempotent_key
        self.audit_columns = audit_columns
        self.sort, self.search = sort, list(search)
        state = self.states["field"] if self.states else None
        self.filters = list(filters) if filters is not None else [
            n for n, c in self.columns.items()
            if c.get("enum") or c.get("ref") or c.get("index") or c.get("unique") or c.get("type") == "boolean"
            or n == state or n in self.refs]
        self.hooks: dict[str, list[Callable]] = {h: [] for h in HOOKS}

    # -- hooks: @res.before_create etc. -----------------------------------------------------------------------------
    def _hook(self, kind: str) -> Callable:
        def register(fn: Callable) -> Callable:
            self.hooks[kind].append(fn)
            return fn
        return register

    def __getattr__(self, name: str) -> Any:
        if name in HOOKS:
            return self._hook(name)
        raise AttributeError(name)

    def _run(self, kind: str, *args: Any) -> Any:
        out = None
        for fn in self.hooks[kind]:
            got = fn(*args)
            out = got if got is not None else out
        return out

    # -- helpers a custom route can use -----------------------------------------------------------------------------
    def row(self, req: Request, param: str = "id") -> dict:
        """The row the path names, or 404 not_found (a malformed id is also 404)."""
        raw = (req.params or {}).get(param)
        try:
            row_id = int(raw)
        except (TypeError, ValueError):
            raise error(404, "not_found", f"{self.name} {raw} not found") from None
        row = self.table.get(row_id)
        if row is None:
            raise error(404, "not_found", f"{self.name} {row_id} not found")
        return row

    def actor(self, req: Request) -> str | None:
        c = req.caller or {}
        return c.get("id") or c.get("name")

    def record(self, event: str, row: dict, req: Request, detail: Any = None) -> None:
        """An audit row (when the resource has an audit table): event, entity, entity_id, actor, detail."""
        if self.audit_table is None:
            return
        data = {"event": f"{self.name}.{event}", "entity": self.name, "entity_id": row.get("id"),
                "actor": self.actor(req), "detail": detail}
        if self.audit_columns is not None:
            data = {k: v for k, v in data.items() if k in self.audit_columns}
        self.audit_table.insert(data)

    # -- validation -------------------------------------------------------------------------------------------------
    def _body(self, req: Request) -> dict:
        body = req.json()
        if not isinstance(body, dict):
            raise error(400, "validation_error", "the body must be a JSON object")
        return body

    def check(self, data: dict, *, partial: bool, row: dict | None = None) -> dict:
        """Unknown fields, required, types, enums, refs and unique; returns the cleaned data (system fields dropped)."""
        out = {}
        for k, v in data.items():
            if k in SYSTEM:
                continue
            if k not in self.columns:
                raise error(400, "validation_error", f"unknown field '{k}'", k)
            try:
                out[k] = _typed(self.columns[k], v)
            except ValueError as e:
                raise error(400, "validation_error", f"{k} must be {e}", k) from None
        if not partial:
            for k, c in self.columns.items():
                if k not in out and c.get("default") is not None:
                    out[k] = c["default"]
            for k, c in self.columns.items():
                if c.get("required") and out.get(k) in (None, "") and k != (self.states or {}).get("field"):
                    raise error(400, "validation_error", f"{k} is required", k)
        elif any(self.columns[k].get("required") and v in (None, "") for k, v in out.items()):
            k = next(k for k, v in out.items() if self.columns[k].get("required") and v in (None, ""))
            raise error(400, "validation_error", f"{k} is required", k)
        for k, t in self.refs.items():
            if out.get(k) is not None and t.get(out[k]) is None:
                raise error(400, "validation_error", f"{k} {out[k]} does not exist", k)
        for k, c in self.columns.items():
            if c.get("unique") and out.get(k) is not None:
                hit = self.table.list({k: out[k]}, limit=1)
                if hit and (row is None or hit[0]["id"] != row["id"]):
                    raise error(409, "unique_violation", f"{k} '{out[k]}' is already used", k)
        return out

    # -- handlers ---------------------------------------------------------------------------------------------------
    def list(self, req: Request) -> dict:
        """One page: ``{rows, total, limit, offset}``. Query: exact filters on the filter columns (a comma list matches
        any), ``q`` (search), ``sort`` (``-col`` descending), ``limit`` (max 1000), ``offset``, ``include_archived``."""
        q = req.query or {}
        parts: list[dict] = []
        for k in self.filters:
            if q.get(k) in (None, ""):
                continue
            col = self.columns.get(k, {"type": "integer"} if k == "id" else {})
            try:
                values = [_typed(col, v.strip(), from_query=True) for v in str(q[k]).split(",")]
            except ValueError as e:
                raise error(400, "validation_error", f"{k} must be {e}", k) from None
            parts.append({k: values[0]} if len(values) == 1 else {k: {"$in": values}})
        if q.get("q") and self.search:
            parts.append({"$or": [{c: {"$contains": str(q["q"])}} for c in self.search]})
        if self.archive and str(q.get("include_archived", "")).lower() not in ("true", "1"):
            parts.append({"$or": [{self.archive: {"$exists": False}}, {self.archive: None}]})
        sort = q.get("sort") or self.sort
        if sort.lstrip("+-") not in set(self.columns) | set(SYSTEM):
            raise error(400, "validation_error", f"cannot sort by {sort}", "sort")
        try:
            limit = max(1, min(int(q.get("limit") or 50), MAX_LIMIT))
            offset = max(0, int(q.get("offset") or 0))
        except ValueError:
            raise error(400, "validation_error", "limit and offset must be integers") from None
        flt = parts[0] if len(parts) == 1 else ({"$and": parts} if parts else None)
        return self.table.page(flt, sort=sort, limit=limit, offset=offset)

    def get(self, req: Request) -> dict:
        return self.row(req)

    def create(self, req: Request) -> Response:
        """201 with the new row. With ``idempotent_key`` an identical repeat answers 200 with the existing row."""
        data = self._body(req)
        key = self.idempotent_key
        if key and data.get(key) is not None:
            hit = self.table.list({key: data[key]}, limit=1)
            if hit:
                same = all(hit[0].get(k) == v for k, v in data.items() if k not in SYSTEM)
                if same:
                    return Response(200, hit[0])
                raise error(409, "unique_violation", f"{key} '{data[key]}' is already used", key)
        if self.states:
            f, initial = self.states["field"], self.states.get("initial")
            if data.get(f) not in (None, initial):
                raise error(400, "validation_error", f"{f} is set by transitions", f)
        data = self.check(data, partial=False)
        if self.states and self.states.get("initial"):
            data[self.states["field"]] = self.states["initial"]
        who = self.actor(req)
        for c in ("created_by", "updated_by"):
            if c in self.columns:
                data[c] = who
        self._run("validate", data, req, None)
        data = self._run("before_create", data, req) or data
        row = self.table.insert(data)
        self.record("created", row, req)
        self._run("after_create", row, req)
        return Response(201, row)

    def update(self, req: Request) -> dict:
        """Change some fields (PUT or PATCH). A change of the state field goes through ``transition``."""
        row = self.row(req)
        changes = self.check(self._body(req), partial=True, row=row)
        f = (self.states or {}).get("field")
        to = changes.pop(f, None) if f else None
        if self.archive and row.get(self.archive):
            raise error(409, "illegal_transition", f"{self.name} {row['id']} is archived")
        if changes:
            if "updated_by" in self.columns:
                changes["updated_by"] = self.actor(req)
            self._run("validate", changes, req, row)
            changes = self._run("before_update", row, changes, req) or changes
            row = self.table.update(row["id"], changes)
            self.record("updated", row, req, {"fields": sorted(changes)})
            self._run("after_update", row, req)
        if to is not None and to != row.get(f):
            row = self.move(row, to, req)
        return row

    def transition(self, req: Request, to: str, changes: dict | None = None, fields: list[str] = (),
                   required: list[str] = ()) -> dict:
        """Move the row the path names to state ``to`` (a repeat is a no-op). ``fields``: body fields stored with the move
        (validated like an update); ``required``: those the body must carry."""
        row = self.row(req)
        data = dict(changes or {})
        if fields:
            body = req.json() if req.json() is not None else {}
            if not isinstance(body, dict):
                raise error(400, "validation_error", "the body must be a JSON object")
            for k in required:
                if body.get(k) in (None, ""):
                    raise error(400, "validation_error", f"{k} is required", k)
            data.update(self.check({k: v for k, v in body.items() if k in fields}, partial=True, row=row))
        return self.move(row, to, req, data)

    def move(self, row: dict, to: str, req: Request, changes: dict | None = None) -> dict:
        """The transition itself, for a loaded row: 409 illegal_transition unless the state machine allows it. Sets
        ``{to}_at`` / ``{to}_by`` when those columns exist, then writes an audit row."""
        sm = self.states or {}
        f = sm.get("field")
        if not f:
            raise error(409, "illegal_transition", f"{self.name} has no states")
        frm = row.get(f)
        if frm == to:
            return row
        allowed = any(t.get("to") == to and t.get("from") in (frm, "*", "any") for t in sm.get("transitions") or [])
        if frm in (sm.get("final") or []) or not allowed:
            raise error(409, "illegal_transition", f"{self.name} {row['id']} cannot go from {frm} to {to}", f)
        data = {f: to, **(changes or {})}
        extra = self._run("before_transition", row, to, req)
        if isinstance(extra, dict):
            data.update(extra)
        for col, value in ((f"{to}_at", _now()), (f"{to}_by", self.actor(req)), ("updated_by", self.actor(req))):
            if col in self.columns and col not in data:
                data[col] = value
        new = self.table.update(row["id"], data)
        self.record(to, new, req, {"from": frm, "to": to})
        self._run("after_transition", new, frm, req)
        return new

    def delete(self, req: Request) -> dict:
        """Archive the row when the resource has an archive column (a repeat is a no-op), else delete it."""
        row = self.row(req)
        if self.archive and row.get(self.archive):
            return row
        self._run("before_delete", row, req)
        if self.archive:
            row = self.table.update(row["id"], {self.archive: _now()})
            self.record("archived", row, req)
        else:
            self.table.delete(row["id"])
            self.record("deleted", row, req)
            row = {"id": row["id"], "deleted": True}
        self._run("after_delete", row, req)
        return row


def resource(table: Table, **options: Any) -> Resource:
    return Resource(table, **options)
