"""CRUD + filters + pagination for one entity. Rename `items`/`item` and the columns; keep the shape.
Tables: items(title string*, kind string*, status string, tags json, owner string, notes string)"""
from studio import api, Response, HTTPError
import studio

KINDS = {"ui", "api"}
STATUSES = {"draft", "active", "retired"}
FIELDS = ("title", "kind", "status", "tags", "owner", "notes")


def _tbl():
    return studio.db.table("items")


def _clean(body, partial):
    """Validate a create (partial=False) or update (partial=True) body; returns only known columns."""
    if not isinstance(body, dict):
        raise HTTPError(400, {"error": "body must be an object"})
    out = {k: body[k] for k in FIELDS if k in body}
    if not partial:
        for f in ("title", "kind"):
            if not str(out.get(f) or "").strip():
                raise HTTPError(400, {"error": f"{f} is required", "field": f})
    if "kind" in out and out["kind"] not in KINDS:
        raise HTTPError(400, {"error": "kind must be one of " + ", ".join(sorted(KINDS)), "field": "kind"})
    if "status" in out and out["status"] not in STATUSES:
        raise HTTPError(400, {"error": "status must be one of " + ", ".join(sorted(STATUSES)), "field": "status"})
    if "tags" in out and not (isinstance(out["tags"], list) and all(isinstance(t, str) for t in out["tags"])):
        raise HTTPError(400, {"error": "tags must be a list of strings", "field": "tags"})
    return out


def _row_id(req):
    try:
        return int(req.params["id"])
    except (KeyError, ValueError):
        raise HTTPError(404, {"error": "not found"})


def _load(req):
    row = _tbl().get(_row_id(req))
    if row is None:
        raise HTTPError(404, {"error": "not found"})
    return row


@api.get("/items")
def list_items(req):
    """List items. Filters: kind, status, owner, tag, q; sort; limit/offset.
    Returns {rows: [{id, title, kind, status, tags, owner}], total, limit, offset}"""
    q = req.query
    flt = {k: q[k] for k in ("kind", "status", "owner") if q.get(k)}
    sort = q.get("sort") or "-updated_at"
    rows = _tbl().all(flt or None)          # filter in the table; tag and text search below, in Python
    if q.get("tag"):
        rows = [r for r in rows if q["tag"] in (r.get("tags") or [])]
    if q.get("q"):
        needle = q["q"].lower()
        rows = [r for r in rows if needle in (str(r.get("title") or "") + " " + str(r.get("notes") or "")).lower()]
    key, desc = sort.lstrip("+-"), sort.startswith("-")
    rows.sort(key=lambda r: (r.get(key) is None, str(r.get(key) or "")), reverse=desc)
    limit = max(1, min(int(q.get("limit") or 50), 500))
    offset = max(0, int(q.get("offset") or 0))
    return {"rows": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset}


@api.post("/items")
def create_item(req):
    """Create an item. Returns the created row (status 201)."""
    data = _clean(req.json(), partial=False)
    data.setdefault("status", "draft")
    data.setdefault("tags", [])
    return Response(201, _tbl().insert(data))


@api.get("/items/{id}")
def get_item(req):
    """One item. Returns the row; 404 when the id does not exist."""
    return _load(req)


@api.put("/items/{id}")
def update_item(req):
    """Partial update. Returns the updated row; 404 for an unknown id, 400 for an invalid value."""
    _load(req)
    return _tbl().update(_row_id(req), _clean(req.json(), partial=True))


@api.delete("/items/{id}")
def delete_item(req):
    """Delete an item unless something refers to it (409). Returns {deleted: id}."""
    row = _load(req)
    # if other tables refer to it: raise HTTPError(409, {"error": "in use"}) before deleting
    _tbl().delete(row["id"])
    return {"deleted": row["id"]}
