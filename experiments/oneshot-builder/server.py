"""Serve one generated app over HTTP. Written once; the same for every app.

    uv run --no-project --with fastapi --with uvicorn python server.py APP_DIR [--port 8100]

APP_DIR holds schema.json + actions.py (from build.py). Data is kept in
APP_DIR/app.db; sample rows are added only the first time.

Who's calling: X-User (email) and X-Role headers. Stand-in for the studio login.

  GET    /api/app                      tables, columns, queries, actions (+ inputs, roles)
  GET    /api/tables/{t}?col=v&q=&sort=-col&limit=&offset=   rows + total
  GET    /api/tables/{t}/{id}          row + its change history
  POST   /api/tables/{t}               create   } only roles in the table's "edit_roles";
  PATCH  /api/tables/{t}/{id}          edit     } "locked" columns change only through actions
  DELETE /api/tables/{t}/{id}          delete   }
  GET    /api/queries/{name}           saved query
  POST   /api/actions/{name}           run an action (JSON body = inputs)
  GET    /api/outbox                   emails the app has sent
"""
from __future__ import annotations

import argparse
import inspect
import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

import kit


def create_app(app_dir: Path) -> FastAPI:
    schema = json.loads((app_dir / "schema.json").read_text())
    actions, _tests = kit.load_actions((app_dir / "actions.py").read_text())
    db = kit.DB(schema, path=str(app_dir / "app.db"))
    if db.fresh:
        for t in schema["tables"]:
            for row in t.get("sample_rows", []):
                db.insert(t["name"], **row)
        db.con.commit()
    lock = threading.Lock()  # one writer at a time; each request is one transaction
    api = FastAPI(title=app_dir.name)
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def who(req: Request) -> kit.User:
        return kit.User(req.headers.get("x-user", "anonymous"), req.headers.get("x-role", "member"))

    def run(user, fn):
        """Run fn in one transaction: all its writes land, or none do."""
        with lock:
            db.user = user
            db.outbox = []
            try:
                out = fn()
                db.con.commit()
                return out
            except kit.ActionError as e:
                db.con.rollback()
                raise HTTPException(400, str(e))
            except HTTPException:
                db.con.rollback()
                raise
            except Exception as e:  # noqa: BLE001  a bug in generated code
                db.con.rollback()
                raise HTTPException(500, f"{type(e).__name__}: {e}")

    def table(name):
        if name not in db.schema:
            raise HTTPException(404, f'no table "{name}"')
        return db.schema[name]

    def can_edit(user, t):
        if user.role not in t.get("edit_roles", []):
            raise HTTPException(403, f'{user.role} can\'t edit {t["name"]} directly'
                                + (" (use the app's actions)" if actions else ""))

    def unlocked(t, values: dict):
        locked = [c["name"] for c in t["columns"] if c.get("locked") and c["name"] in values]
        if locked:
            raise HTTPException(400, f"{', '.join(locked)} can only be changed through the app's actions")
        return values

    def query_columns(name):
        with lock:
            cur = db.con.execute(db.queries[name], {"me": "", "today": db.today()})
            return [d[0] for d in cur.description]

    @api.get("/api/app")
    def describe():
        def inputs(fn):
            ps = list(inspect.signature(fn).parameters.values())[2:]  # skip db, user
            return [{"name": p.name, "required": p.default is inspect.Parameter.empty,
                     "type": getattr(p.annotation, "__name__", str(p.annotation))
                     if p.annotation is not inspect.Parameter.empty else "any"} for p in ps]
        return {
            "name": app_dir.name,
            "roles": schema.get("roles", []),
            "tables": [{"name": t["name"], "columns": t["columns"], "edit_roles": t.get("edit_roles", [])}
                       for t in schema["tables"]],
            "queries": [{"name": n, "columns": query_columns(n)} for n in db.queries],
            "actions": [{"name": n, "roles": fn._action["roles"], "description": (fn.__doc__ or "").strip(),
                         "inputs": inputs(fn)} for n, fn in actions.items()],
        }

    @api.get("/api/tables/{name}")
    def list_rows(name: str, request: Request, q: str = "", sort: str = "id", limit: int = 100, offset: int = 0):
        t = table(name)
        cols = {c["name"] for c in t["columns"]} | set(kit.AUTO)
        where, params = [], []
        for k, v in request.query_params.items():
            if k in cols:
                where.append(f'"{k}" = ?'); params.append(v)
        if q:
            texts = [c["name"] for c in t["columns"] if c["type"] in ("text", "person", "email", "choice")]
            if texts:
                where.append("(" + " OR ".join(f'"{c}" LIKE ?' for c in texts) + ")")
                params += [f"%{q}%"] * len(texts)
        col = sort.lstrip("-")
        if col not in cols:
            raise HTTPException(400, f'can\'t sort by "{col}"')
        sql_where = " WHERE " + " AND ".join(where) if where else ""
        with lock:
            total = db.con.execute(f'SELECT COUNT(*) FROM "{name}"{sql_where}', params).fetchone()[0]
            rows = db.con.execute(
                f'SELECT * FROM "{name}"{sql_where} ORDER BY "{col}" {"DESC" if sort.startswith("-") else "ASC"} '
                f"LIMIT ? OFFSET ?", [*params, min(limit, 1000), offset]).fetchall()
            return {"rows": [db._row(name, r) for r in rows], "total": total}

    @api.get("/api/tables/{name}/{id}")
    def get_row(name: str, id: int, request: Request):
        table(name)
        return run(who(request), lambda: {**db.get(name, id), "_history": db.history(name, id)})

    @api.post("/api/tables/{name}")
    async def create_row(name: str, request: Request):
        t, user, body = table(name), who(request), await request.json()
        can_edit(user, t)
        return run(user, lambda: db.insert(name, **unlocked(t, body)))

    @api.patch("/api/tables/{name}/{id}")
    async def edit_row(name: str, id: int, request: Request):
        t, user, body = table(name), who(request), await request.json()
        can_edit(user, t)
        return run(user, lambda: db.update(name, id, **unlocked(t, body)))

    @api.delete("/api/tables/{name}/{id}")
    def delete_row(name: str, id: int, request: Request):
        t, user = table(name), who(request)
        can_edit(user, t)
        return run(user, lambda: (db.delete(name, id), {"deleted": id})[1])

    @api.get("/api/queries/{name}")
    def saved_query(name: str, request: Request):
        if name not in db.queries:
            raise HTTPException(404, f'no query "{name}"')
        return run(who(request), lambda: db.query(name))

    @api.post("/api/actions/{name}")
    async def run_action(name: str, request: Request):
        if name not in actions:
            raise HTTPException(404, f'no action "{name}"')
        fn, user = actions[name], who(request)
        roles = fn._action["roles"]
        if roles and user.role not in roles:
            raise HTTPException(403, f"{user.role} can't run {name} (allowed: {', '.join(roles)})")
        body = await request.json() if (await request.body()) else {}
        try:
            inspect.signature(fn).bind(None, None, **body)
        except TypeError as e:
            raise HTTPException(400, f"bad inputs for {name}: {e}")
        result = run(user, lambda: fn(db, user, **body))
        return {"result": result}

    @api.get("/api/outbox")
    def outbox():
        with lock:
            return [dict(r) for r in db.con.execute("SELECT * FROM _outbox ORDER BY rowid DESC LIMIT 100")]

    # the page (APP_DIR/ui, built by ui/check.mjs); read per request so a rebuild needs no restart
    @api.get("/")
    def index():
        f = app_dir / "ui" / "index.html"
        if not f.exists():
            raise HTTPException(404, "no page built yet")
        return HTMLResponse(f.read_text(), headers={"cache-control": "no-store"})

    @api.get("/app.js")
    def bundle():
        return Response((app_dir / "ui" / "app.js").read_bytes(), media_type="text/javascript",
                        headers={"cache-control": "no-store"})

    return api


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir")
    ap.add_argument("--port", type=int, default=8100)
    a = ap.parse_args()
    uvicorn.run(create_app(Path(a.app_dir)), host="127.0.0.1", port=a.port)
