"""The platform side of an app backend: tables, actions, tests.

The model never writes SQL schemas, routes or migrations. It writes:
  schema.json  - tables (columns, types, choices), sample rows, saved queries
  actions.py   - @action functions that use `db`, plus test_* functions
This module creates the tables, validates every write, records an audit
trail, enforces roles, and runs the tests each on a fresh copy of the data.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import traceback

TYPES = {"text": "TEXT", "number": "REAL", "money": "REAL", "integer": "INTEGER",
         "date": "TEXT", "datetime": "TEXT", "boolean": "INTEGER", "choice": "TEXT",
         "person": "TEXT", "email": "TEXT", "ref": "INTEGER"}
AUTO = ("id", "created_at", "created_by", "updated_at")


class ActionError(Exception):
    """Raise this to refuse an action with a message the user will see."""


class User:
    """People are identified by email. `name` is the part before the @."""

    def __init__(self, who: str, role: str):
        self.email = (who if "@" in who else f"{who}@bank.example").lower()
        self.name = self.email.split("@")[0]
        self.role = role


def action(roles=None):
    """Mark a function as an action. roles=None means anyone may run it."""
    def wrap(fn):
        fn._action = {"roles": roles}
        return fn
    return wrap


class DB:
    """What actions get. Every write is validated against schema.json."""

    def __init__(self, schema: dict, user: User | None = None, path: str = ":memory:"):
        self.schema = {t["name"]: t for t in schema["tables"]}
        for t in schema["tables"]:
            for c in t["columns"]:
                if c["type"] not in TYPES:
                    raise ActionError(f'{t["name"]}.{c["name"]}: unknown type "{c["type"]}"')
                if c["type"] == "ref" and c.get("table") not in self.schema:
                    raise ActionError(f'{t["name"]}.{c["name"]}: ref needs "table" naming one of {list(self.schema)}')
        self.queries = {q["name"]: q["sql"] for q in schema.get("queries", [])}
        self.user = user
        self.outbox: list[dict] = []
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        existing = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.fresh = not (existing & set(self.schema))  # True: nothing stored yet, so seed sample rows
        for t in schema["tables"]:
            if t["name"] in existing:  # migrate: add new columns (edits only ever add)
                have = {r[1] for r in self.con.execute(f'PRAGMA table_info("{t["name"]}")')}
                for c in t["columns"]:
                    if c["name"] not in have:
                        self.con.execute(f'ALTER TABLE "{t["name"]}" ADD COLUMN "{c["name"]}" {TYPES[c["type"]]}')
                        if "default" in c:
                            self.con.execute(f'UPDATE "{t["name"]}" SET "{c["name"]}"=?',
                                             (self._clean(t["name"], {c["name"]: c["default"]}, False)[c["name"]],))
                continue
            cols = ", ".join(f'"{c["name"]}" {TYPES[c["type"]]}' for c in t["columns"])
            self.con.execute(
                f'CREATE TABLE "{t["name"]}" (id INTEGER PRIMARY KEY, created_at TEXT, '
                f'created_by TEXT, updated_at TEXT{", " + cols if cols else ""})')
        self.con.execute("CREATE TABLE IF NOT EXISTS _audit (at TEXT, who TEXT, tbl TEXT, row_id INTEGER, change TEXT)")
        self.con.execute("CREATE TABLE IF NOT EXISTS _outbox (at TEXT, recipient TEXT, subject TEXT, body TEXT)")
        self.con.commit()

    # -- helpers ---------------------------------------------------------
    def _table(self, name):
        if name not in self.schema:
            raise ActionError(f'unknown table "{name}" (tables: {", ".join(self.schema)})')
        return self.schema[name]

    def _clean(self, table, values, full):
        t = self._table(table)
        cols = {c["name"]: c for c in t["columns"]}
        out = {}
        for k, v in values.items():
            if k in AUTO:
                continue
            if k not in cols:
                raise ActionError(f'unknown column "{table}.{k}" (columns: {", ".join(cols)})')
            c = cols[k]
            if v is None:
                out[k] = None
                continue
            ty = c["type"]
            try:
                if ty in ("number", "money"):
                    v = float(v)
                elif ty in ("integer", "ref"):
                    v = int(v)
                elif ty == "boolean":
                    v = int(bool(v))
                elif ty == "date":
                    v = (v if isinstance(v, _dt.date) else _dt.date.fromisoformat(str(v)[:10])).isoformat()
                elif ty == "datetime":
                    v = (v if isinstance(v, _dt.datetime) else _dt.datetime.fromisoformat(str(v))).isoformat()
                else:
                    v = str(v)
            except (TypeError, ValueError):
                raise ActionError(f'{table}.{k} must be a {ty}, got {v!r}')
            if ty == "choice" and v not in c.get("choices", []):
                raise ActionError(f'{table}.{k} must be one of {c.get("choices")}, got {v!r}')
            if ty in ("person", "email"):
                v = v.lower()
            if ty == "ref" and not self.con.execute(
                    f'SELECT 1 FROM "{c["table"]}" WHERE id=?', (v,)).fetchone():
                raise ActionError(f'{table}.{k}: {c["table"]} #{v} does not exist')
            out[k] = v
        if full:
            for c in t["columns"]:
                if "default" in c and out.get(c["name"]) is None:
                    out[c["name"]] = self._clean(table, {c["name"]: c["default"]}, False)[c["name"]]
                if c.get("required") and out.get(c["name"]) in (None, ""):
                    raise ActionError(f'{table}.{c["name"]} is required')
        return out

    def _audit(self, table, row_id, change):
        self.con.execute("INSERT INTO _audit VALUES (?,?,?,?,?)",
                         (self.now(), self.user.email if self.user else "system", table, row_id,
                          json.dumps(change, default=str)))

    # -- the API actions use ---------------------------------------------
    def now(self) -> str:
        return _dt.datetime.now().replace(microsecond=0).isoformat()

    def today(self) -> str:
        return _dt.date.today().isoformat()

    def insert(self, table: str, **values) -> dict:
        vals = self._clean(table, values, full=True)
        vals.update(created_at=self.now(), updated_at=self.now(),
                    created_by=self.user.email if self.user else "system")
        keys = ", ".join(f'"{k}"' for k in vals)
        cur = self.con.execute(f'INSERT INTO "{table}" ({keys}) VALUES ({", ".join("?" * len(vals))})',
                               list(vals.values()))
        self._audit(table, cur.lastrowid, {"insert": values})
        return self.get(table, cur.lastrowid)

    def _row(self, table, row) -> dict:
        d = dict(row)
        for c in self.schema[table]["columns"]:
            if c["type"] == "boolean" and d.get(c["name"]) is not None:
                d[c["name"]] = bool(d[c["name"]])
        return d

    def get(self, table: str, id: int) -> dict:
        self._table(table)
        row = self.con.execute(f'SELECT * FROM "{table}" WHERE id=?', (id,)).fetchone()
        if row is None:
            raise ActionError(f"{table} #{id} not found")
        return self._row(table, row)

    def update(self, table: str, id: int, **changes) -> dict:
        before = self.get(table, id)
        vals = self._clean(table, changes, full=False)
        vals["updated_at"] = self.now()
        sets = ", ".join(f'"{k}"=?' for k in vals)
        self.con.execute(f'UPDATE "{table}" SET {sets} WHERE id=?', [*vals.values(), id])
        self._audit(table, id, {k: [before.get(k), v] for k, v in changes.items()})
        return self.get(table, id)

    def delete(self, table: str, id: int) -> None:
        self.get(table, id)
        for other, t in self.schema.items():
            for c in t["columns"]:
                if c["type"] == "ref" and c["table"] == table and self.con.execute(
                        f'SELECT 1 FROM "{other}" WHERE "{c["name"]}"=?', (id,)).fetchone():
                    raise ActionError(f"can't delete {table} #{id}: {other} rows still point to it")
        self.con.execute(f'DELETE FROM "{table}" WHERE id=?', (id,))
        self._audit(table, id, {"delete": True})

    def find(self, table: str, **where) -> list[dict]:
        self._table(table)
        clause = " AND ".join(f'"{k}"=?' for k in where) or "1=1"
        return [self._row(table, r) for r in self.con.execute(
            f'SELECT * FROM "{table}" WHERE {clause} ORDER BY id', list(where.values()))]

    def query(self, name_or_sql: str, **params) -> list[dict]:
        """Run a saved query by name, or a read-only SELECT. Saved queries get
        :me (the current user's email) and :today automatically."""
        sql = self.queries.get(name_or_sql, name_or_sql)
        if not sql.lstrip().lower().startswith(("select", "with")):
            raise ActionError("query() is read-only: use insert/update/delete to change data")
        params = {"me": self.user.email if self.user else "", "today": self.today(), **params}
        return [dict(r) for r in self.con.execute(sql, params)]

    def history(self, table: str, id: int) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM _audit WHERE tbl=? AND row_id=? ORDER BY rowid", (table, id))]

    def notify(self, to: str, subject: str, body: str = "") -> None:
        """Send an email/notification. Recorded in the outbox (sent by the platform)."""
        if not to or "@" not in str(to):
            raise ActionError(f"can't notify {to!r}: not an email address")
        self.outbox.append({"to": to, "subject": subject, "body": body})
        self.con.execute("INSERT INTO _outbox VALUES (?,?,?,?)", (self.now(), to, subject, body))


# -- loading and running ---------------------------------------------------
ALLOWED_IMPORTS = ("datetime", "math", "decimal", "re", "json")


def load_actions(code: str) -> tuple[dict, dict]:
    for line in code.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            mod = s.split()[1].split(".")[0]
            if mod not in ALLOWED_IMPORTS + ("kit", "__future__"):
                raise ActionError(f"import of {mod!r} isn't allowed (allowed: {', '.join(ALLOWED_IMPORTS)})")
    ns: dict = {"action": action, "ActionError": ActionError, "__name__": "app_actions"}
    exec(compile(code, "actions.py", "exec"), ns)
    actions = {k: v for k, v in ns.items() if callable(v) and hasattr(v, "_action")}
    tests = {k: v for k, v in ns.items() if k.startswith("test_") and callable(v)}
    return actions, tests


class T:
    """What tests get: run actions as a role, check refusals, read data."""

    def __init__(self, schema, actions):
        self.actions = actions
        self.db = DB(schema)
        for t in schema["tables"]:
            for row in t.get("sample_rows", []):
                self.db.insert(t["name"], **row)
        self.db.outbox.clear()

    @property
    def outbox(self):
        return self.db.outbox

    def _call(self, name, as_role, as_user, inputs, /):
        if name not in self.actions:
            raise AssertionError(f'no action named "{name}" (actions: {", ".join(self.actions)})')
        fn = self.actions[name]
        roles = fn._action["roles"]
        self.db.user = User(as_user or f"{as_role}.user", as_role)
        if roles and as_role not in roles:
            raise ActionError(f"{as_role} can't run {name} (allowed: {', '.join(roles)})")
        return fn(self.db, self.db.user, **inputs)

    def run(self, name, /, as_role="member", as_user=None, **inputs):
        """Run an action; fails the test if the action refuses."""
        return self._call(name, as_role, as_user, inputs)

    def fails(self, name, /, as_role="member", as_user=None, **inputs) -> str:
        """Run an action that should be refused; returns the refusal message."""
        try:
            self._call(name, as_role, as_user, inputs)
        except ActionError as e:
            return str(e)
        raise AssertionError(f"expected {name} to be refused, but it succeeded")

    def get(self, table, id):
        return self.db.get(table, id)

    def find(self, table, **where):
        return self.db.find(table, **where)

    def query(self, name, **params):
        return self.db.query(name, **params)


def existing_data_fits(old: dict, new: dict) -> list[str]:
    """For edits: can the rows people already entered move to the new schema?
    Tables and columns may be added; removing, renaming or retyping them, or a
    new required column with no default, would break existing data."""
    problems = []
    new_t = {t["name"]: t for t in new["tables"]}
    for t in old["tables"]:
        if t["name"] not in new_t:
            problems.append(f'table "{t["name"]}" was removed')
            continue
        new_c = {c["name"]: c for c in new_t[t["name"]]["columns"]}
        for c in t["columns"]:
            if c["name"] not in new_c:
                problems.append(f'column "{t["name"]}.{c["name"]}" was removed or renamed')
            elif new_c[c["name"]]["type"] != c["type"]:
                problems.append(f'column "{t["name"]}.{c["name"]}" changed type')
    if problems:
        return problems
    try:  # move the old rows across, as a real migration would
        db = DB(new)
        for t in old["tables"]:
            for row in t.get("sample_rows", []):
                db.insert(t["name"], **row)
    except Exception as e:  # noqa: BLE001
        problems.append(f"existing rows don't fit the new schema: {e}")
    return problems


def check(schema: dict, code: str, old_schema: dict | None = None) -> list[dict]:
    """Run every layer check. Returns [{layer, name, ok, error}] in order."""
    results = []

    def add(layer, name, ok, error=""):
        results.append({"layer": layer, "name": name, "ok": ok, "error": error})

    if old_schema is not None:
        problems = existing_data_fits(old_schema, schema)
        add("data", "existing data still fits", not problems, "; ".join(problems))

    # 1. Data: tables build, sample rows are valid, saved queries run
    try:
        base = T(schema, {})
        base.db.user = User("checker", "member")
        add("data", f'tables: {", ".join(base.db.schema)}', True)
    except Exception as e:  # noqa: BLE001
        add("data", "tables and sample rows", False, f"{type(e).__name__}: {e}")
        return results
    for name in base.db.queries:
        try:
            n = len(base.query(name))
            add("data", f'query "{name}" ({n} rows on sample data)', True)
        except Exception as e:  # noqa: BLE001
            add("data", f'query "{name}"', False, f"{type(e).__name__}: {e}")

    # 2. Actions: code loads; each test runs on a fresh copy of the sample data
    try:
        actions, tests = load_actions(code)
    except Exception as e:  # noqa: BLE001
        add("actions", "actions.py loads", False, f"{type(e).__name__}: {e}")
        return results
    if not actions:
        add("actions", "actions.py defines actions", False, "no @action functions found")
    if not tests:
        add("actions", "actions.py defines tests", False, "no test_* functions found")
    for tname, fn in tests.items():
        label = (fn.__doc__ or tname).strip().splitlines()[0]
        try:
            fn(T(schema, actions))
            add("actions", label, True)
        except Exception as e:  # noqa: BLE001
            tb = traceback.extract_tb(e.__traceback__)
            where = next((f"line {f.lineno}: {f.line}" for f in reversed(tb) if f.filename == "actions.py"), "")
            add("actions", label, False, f"{type(e).__name__}: {e} ({where})")
    return results
