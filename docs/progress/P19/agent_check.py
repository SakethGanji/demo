"""P19: the Vela agent's principal, in-process (the signing secret exists only inside the API process). Run from backend/
with ENSEMBLE_DATA_DIR=<chain.py's dir> ENSEMBLE_ADMINS=Ada ENSEMBLE_AUTHZ=enforce ENSEMBLE_AUTHZ_ALLOW_TYPED=1.
Usage: python agent_check.py <dataset id>"""
import asyncio, json, sys, time
import httpx
from fastapi.testclient import TestClient

if __name__ != "__main__":  # the heavy pool spawns processes that re-import this file
    raise SystemExit

from app.agent import tools as T
from app.main import app

ds = sys.argv[1]
body = lambda v, by: {"ops": [{"op": "set_cells", "cells": [{"row_id": 9, "column": "income", "value": v}]}], "reason": "agent check", "by": by}


async def main() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vela-agent") as client:
        for user, v in (("Ben", 71000), ("Ana", 72000)):
            ctx = T.Ctx(client=client, app=app, actor=f"Vela agent on behalf of {user}", deadline=time.monotonic() + 60)
            try:
                r = await T.call(ctx, "POST", f"/api/datasets/{ds}/edit", body=body(v, ctx.actor))
                print(f"## A agent on behalf of {user}: POST …/edit → created {r.get('id')}")
            except T.ToolError as e:
                print(f"## A agent on behalf of {user}: POST …/edit → ToolError {str(e)[:200]}")
        r = await client.post(f"/api/datasets/{ds}/edit", json={k: v for k, v in body(73000, "").items() if k != "by"},
                              headers={"x-ensemble-internal": "416e61.deadbeef"})
        print("## A forged internal header (Ana, wrong signature) →", r.status_code, json.dumps(r.json())[:160])
        r = await client.post("/api/authz/check", json={"action": "data.version", "object_kind": "dataset", "object_id": ds},
                              headers=T.principal_headers(T.Ctx(client=client, app=app, actor="Vela agent on behalf of Ana", deadline=0)))
        print("## A /authz/check as the agent of Ana →", {k: r.json()[k] for k in ("allow", "reason")}, r.json()["principal"])


with TestClient(app):  # runs the lifespan (state, DB) in this process
    with app.state.db.connect() as con:
        START = con.execute("SELECT MAX(id) FROM audit_log").fetchone()[0]
    asyncio.run(main())
    with app.state.db.connect() as con:
        for r in con.execute("SELECT id, actor, action, principal_id, auth, request_id FROM audit_log WHERE id > ? AND (auth = 'agent' OR "
                             "action = 'authz_denied') ORDER BY id", (START,)):
            print("   ", tuple(r))
