"""``studio.resource``: one declaration gives a table list/get/create/update/delete-or-archive and state transitions,
validated from the plan's column list, with the standard error shape, audit rows and business-rule hooks. Runs the
generated package against an in-memory DataTable transport."""

from __future__ import annotations

import pytest

from tests.unit.test_sdk_jobs_generation import _load_sdk


class _Tables:
    """An in-memory DataTable: the operations and filter grammar the SDK sends."""

    def __init__(self):
        self.rows: dict[str, list[dict]] = {}
        self.next_id = 0

    def _match(self, row, f):
        for k, v in (f or {}).items():
            if k == "$and":
                if not all(self._match(row, x) for x in v):
                    return False
            elif k == "$or":
                if not any(self._match(row, x) for x in v):
                    return False
            elif isinstance(v, dict) and v and all(str(x).startswith("$") for x in v):
                for op, x in v.items():
                    have = row.get(k)
                    ok = {"$in": lambda: have in x, "$gt": lambda: have is not None and have > x,
                          "$contains": lambda: str(x).lower() in str(have or "").lower(),
                          "$exists": lambda: (k in row) == bool(x), "$eq": lambda: have == x}[op]()
                    if not ok:
                        return False
            elif row.get(k) != v:
                return False
        return True

    def __call__(self, node, p, items):
        assert node == "DataTable"
        rows = self.rows.setdefault(p["table"], [])
        op = p["operation"]
        if op == "insert":
            self.next_id += 1
            row = {"id": self.next_id, "created_at": f"t{self.next_id:04d}", "updated_at": None, **p["data"]}
            rows.append(row)
            return {"ok": True, "items": [dict(row)]}
        if op in ("get", "update", "delete"):
            row = next((r for r in rows if r["id"] == p["rowId"]), None)
            if row is None:
                return {"ok": False, "error": {"code": "row_not_found", "message": "no row"}}
            if op == "update":
                row.update(p["data"])
            if op == "delete":
                rows.remove(row)
                return {"ok": True, "items": []}
            return {"ok": True, "items": [dict(row)]}
        if op == "list":
            out = [dict(r) for r in rows if self._match(r, p.get("filter"))]
            out.sort(key=lambda r: r["id"])
            return {"ok": True, "items": out[: p.get("limit", 50)]}
        raise AssertionError(op)


COLUMNS = [{"name": "title", "type": "string", "required": True},
           {"name": "amount", "type": "number", "required": True},
           {"name": "code", "type": "string", "unique": True},
           {"name": "category", "type": "string", "enum": ["travel", "meals"]},
           {"name": "status", "type": "string", "required": True, "default": "draft",
            "enum": ["draft", "submitted", "approved", "rejected", "paid"]},
           {"name": "owner_id", "type": "integer", "ref": "people.id", "index": True},
           {"name": "approved_at", "type": "datetime"}, {"name": "approved_by", "type": "string"},
           {"name": "created_by", "type": "string"}, {"name": "updated_by", "type": "string"},
           {"name": "archived_at", "type": "datetime"}]
STATES = {"field": "status", "initial": "draft", "final": ["paid", "rejected"],
          "transitions": [{"from": "draft", "to": "submitted"}, {"from": "submitted", "to": "approved"},
                          {"from": "submitted", "to": "rejected"}, {"from": "approved", "to": "paid"}]}


@pytest.fixture
def env():
    studio = _load_sdk()
    db = _Tables()
    tok = studio._transport.use(db)
    t = studio.db.table
    res = studio.resource(t("expenses"), columns=COLUMNS, states=STATES, refs={"owner_id": t("people")},
                          archive="archived_at", audit=t("audit_log"), audit_columns=["event", "entity", "entity_id", "actor"],
                          search=["title"])
    db.rows["people"] = [{"id": 900, "name": "Ann"}]
    try:
        yield studio, db, res
    finally:
        studio._transport._current.reset(tok)


def _req(studio, body=None, params=None, query=None, who="u1"):
    return studio.api.Request({"method": "POST", "body": body, "params": params or {}, "query": query or {},
                               "caller": {"kind": "user", "id": who, "name": who}})


def _err(studio, fn, *a):
    with pytest.raises(studio.HTTPError) as e:
        fn(*a)
    return e.value.status, e.value.body


def test_create_validates_from_the_columns_and_sets_the_system_parts(env):
    studio, db, res = env
    r = res.create(_req(studio, {"title": "Taxi", "amount": 12.5, "owner_id": 900, "id": 77, "created_at": "x"}))
    assert r.status == 201
    row = r.body
    assert row["status"] == "draft" and row["created_by"] == "u1" and row["id"] != 77     # system fields dropped
    assert db.rows["audit_log"][-1] == {"id": db.rows["audit_log"][-1]["id"], "created_at": db.rows["audit_log"][-1]["created_at"],
                                        "updated_at": None, "event": "expenses.created", "entity": "expenses",
                                        "entity_id": row["id"], "actor": "u1"}
    assert _err(studio, res.create, _req(studio, ["x"]))[1]["error"] == "validation_error"
    assert _err(studio, res.create, _req(studio, {"amount": 1})) == (400, {"error": "validation_error",
                                                                          "message": "title is required", "field": "title"})
    assert _err(studio, res.create, _req(studio, {"title": "a", "amount": "1"}))[1]["field"] == "amount"
    assert _err(studio, res.create, _req(studio, {"title": "a", "amount": 1, "category": "golf"}))[1]["field"] == "category"
    assert _err(studio, res.create, _req(studio, {"title": "a", "amount": 1, "mood": "x"}))[1]["message"] == "unknown field 'mood'"
    assert _err(studio, res.create, _req(studio, {"title": "a", "amount": 1, "owner_id": 5}))[1]["field"] == "owner_id"
    assert _err(studio, res.create, _req(studio, {"title": "a", "amount": 1, "status": "paid"}))[1]["field"] == "status"
    res.create(_req(studio, {"title": "a", "amount": 1, "code": "C1"}))
    assert _err(studio, res.create, _req(studio, {"title": "b", "amount": 2, "code": "C1"}))[:1] == (409,)


def test_list_filters_sorts_pages_and_hides_archived(env):
    studio, _, res = env
    for i, cat in enumerate(["travel", "meals", "travel", "meals", "travel"]):
        res.create(_req(studio, {"title": f"e{i}", "amount": i, "category": cat}))
    page = res.list(_req(studio, query={"category": "travel", "limit": "2", "sort": "amount"}))
    assert set(page) == {"rows", "total", "limit", "offset"} and page["total"] == 3 and page["limit"] == 2
    assert [r["amount"] for r in page["rows"]] == [0, 2]
    assert res.list(_req(studio, query={"category": "travel,meals"}))["total"] == 5
    assert res.list(_req(studio, query={"q": "E3"}))["total"] == 1
    assert res.list(_req(studio, query={"limit": "99999"}))["limit"] == 1000
    assert _err(studio, res.list, _req(studio, query={"category": "golf"}))[1]["error"] == "validation_error"
    assert _err(studio, res.list, _req(studio, query={"sort": "nope"}))[1]["field"] == "sort"
    first = res.list(_req(studio))["rows"][0]
    assert res.delete(_req(studio, params={"id": str(first["id"])}))["archived_at"]
    assert res.delete(_req(studio, params={"id": str(first["id"])}))["id"] == first["id"]       # a repeat is a no-op
    assert res.list(_req(studio))["total"] == 4
    assert res.list(_req(studio, query={"include_archived": "true"}))["total"] == 5


def test_get_and_update_and_missing_rows(env):
    studio, _, res = env
    row = res.create(_req(studio, {"title": "Taxi", "amount": 1})).body
    assert res.get(_req(studio, params={"id": str(row["id"])}))["title"] == "Taxi"
    assert _err(studio, res.get, _req(studio, params={"id": "999"})) == (404, {"error": "not_found", "message": "expenses 999 not found"})
    assert _err(studio, res.get, _req(studio, params={"id": "junk"}))[0] == 404
    up = res.update(_req(studio, {"amount": 3}, params={"id": str(row["id"])}, who="u2"))
    assert up["amount"] == 3 and up["updated_by"] == "u2"
    assert _err(studio, res.update, _req(studio, {"title": None}, params={"id": str(row["id"])}))[1]["field"] == "title"
    moved = res.update(_req(studio, {"status": "submitted"}, params={"id": str(row["id"])}))   # a state change is a transition
    assert moved["status"] == "submitted"
    assert _err(studio, res.update, _req(studio, {"status": "paid"}, params={"id": str(row["id"])}))[1]["error"] == "illegal_transition"


def test_transitions_follow_the_state_machine(env):
    studio, db, res = env
    row = res.create(_req(studio, {"title": "Taxi", "amount": 1})).body
    p = {"id": str(row["id"])}
    assert _err(studio, res.transition, _req(studio, params=p), "approved")[:1] == (409,)
    assert res.transition(_req(studio, params=p), "submitted")["status"] == "submitted"
    assert res.transition(_req(studio, params=p), "submitted")["status"] == "submitted"         # repeat = no-op
    done = res.transition(_req(studio, params=p, who="boss"), "approved")
    assert done["approved_by"] == "boss" and done["approved_at"]
    assert db.rows["audit_log"][-1]["event"] == "expenses.approved"
    res.transition(_req(studio, params=p), "paid")
    status, body = _err(studio, res.transition, _req(studio, params=p), "approved")
    assert status == 409 and body["error"] == "illegal_transition" and body["field"] == "status"


def test_hooks_carry_the_business_rules(env):
    studio, _, res = env
    seen = []

    @res.validate
    def no_big(data, req, row):
        if (data.get("amount") or 0) > 1000:
            raise studio.resources.error(400, "validation_error", "too much", "amount")

    @res.before_create
    def tag(data, req):
        return {**data, "code": f"X-{data['title']}"}

    @res.before_transition
    def not_own(row, to, req):
        if to == "approved" and row["created_by"] == req.caller["id"]:
            raise studio.resources.error(403, "forbidden", "no self-approval")
        return {"amount": row["amount"]}

    @res.after_transition
    def note(row, frm, req):
        seen.append((frm, row["status"]))

    assert _err(studio, res.create, _req(studio, {"title": "Big", "amount": 5000}))[1]["message"] == "too much"
    row = res.create(_req(studio, {"title": "T", "amount": 1})).body
    assert row["code"] == "X-T"
    p = {"id": str(row["id"])}
    res.transition(_req(studio, params=p), "submitted")
    assert _err(studio, res.transition, _req(studio, params=p), "approved")[0] == 403
    res.transition(_req(studio, params=p, who="boss"), "approved")
    assert seen == [("draft", "submitted"), ("submitted", "approved")]


def test_hard_delete_and_idempotent_create():
    studio = _load_sdk()
    db = _Tables()
    tok = studio._transport.use(db)
    try:
        res = studio.resource(studio.db.table("tags"), columns=[{"name": "key", "type": "string", "required": True}],
                              idempotent_key="key")
        a = res.create(_req(studio, {"key": "k1"}))
        b = res.create(_req(studio, {"key": "k1"}))
        assert a.status == 201 and b.status == 200 and b.body["id"] == a.body["id"]
        assert res.delete(_req(studio, params={"id": str(a.body["id"])})) == {"id": a.body["id"], "deleted": True}
        assert _err(studio, res.get, _req(studio, params={"id": str(a.body["id"])}))[0] == 404
    finally:
        studio._transport._current.reset(tok)


def test_the_package_and_reference_export_resource():
    from src.sdk.generate import generate_python_sdk, reference

    files = generate_python_sdk()["files"]
    assert "studio/resources.py" in files and "from .resources import Resource, resource\n" in files["studio/__init__.py"]
    names = {e["name"] for e in reference()["entries"]}
    assert {"studio.resource", "studio.resource(...).list", "studio.resource(...).transition"} <= names


def test_a_transition_stores_and_requires_body_fields_and_default_filters_follow_the_columns(env):
    studio, _, res = env
    row = res.create(_req(studio, {"title": "Taxi", "amount": 1, "category": "travel"})).body
    p = {"id": str(row["id"])}
    res.transition(_req(studio, params=p), "submitted")
    got = res.transition(_req(studio, {"approved_by": "ann"}, params=p), "approved", fields=["approved_by"], required=["approved_by"])
    assert got["status"] == "approved" and got["approved_by"] == "ann"
    row2 = res.create(_req(studio, {"title": "Lunch", "amount": 2})).body
    res.transition(_req(studio, params={"id": str(row2["id"])}), "submitted")
    err = _err(studio, res.transition, _req(studio, {}, params={"id": str(row2["id"])}), "approved",
               None, ["approved_by"], ["approved_by"])
    assert err[1]["field"] == "approved_by" and err[0] == 400
    assert _err(studio, res.transition, _req(studio, {"nope": 1}, params={"id": str(row2["id"])}), "approved", None,
                ["approved_by"], ["approved_by"])[0] == 400
    # enum / ref / state / unique columns filter without being listed
    assert {"category", "status", "owner_id", "code"} <= set(res.filters)
    assert res.list(_req(studio, query={"status": "approved"}))["total"] == 1
