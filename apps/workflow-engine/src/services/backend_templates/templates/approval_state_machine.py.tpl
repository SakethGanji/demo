"""An approval state machine with an audit trail. Table: items(title string*, status string*, decided_by string,
decided_at datetime, decision_note string), item_events(item_id integer*, from_status string, to_status string*, actor string,
note string)
Identity: `req.caller` is engine-verified (forged identity headers are stripped); `actor` is caller.name or caller.id, never the body.
To restrict who may decide, check caller.id against an approvers table."""
from datetime import datetime, timezone
from studio import api, HTTPError
import studio

TRANSITIONS = {"pending": {"approved", "rejected"}, "approved": {"revoked"}, "rejected": set(), "revoked": set()}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _item(req):
    try:
        row = studio.db.table("items").get(int(req.params["id"]))
    except (KeyError, ValueError):
        row = None
    if row is None:
        raise HTTPError(404, {"error": "not found"})
    return row


@api.post("/items/{id}/transition", auth="app_token")
def transition_item(req):
    """Move an item. Body {to, note?}. Returns {item, changed}; repeating the same move is a no-op (changed false);
    an illegal move is 409 and changes nothing."""
    body = req.json() or {}
    to = body.get("to")
    if to not in TRANSITIONS:
        raise HTTPError(400, {"error": "to must be one of " + ", ".join(sorted(TRANSITIONS)), "field": "to"})
    caller = req.caller or {}
    if not caller.get("id"):
        raise HTTPError(401, {"error": "sign in required"})
    actor = caller.get("name") or str(caller["id"])
    item = _item(req)
    if item["status"] == to:
        return {"item": item, "changed": False}
    if to not in TRANSITIONS.get(item["status"], set()):
        raise HTTPError(409, {"error": f"cannot move from {item['status']} to {to}"})
    updated = studio.db.table("items").update(item["id"], {"status": to, "decided_by": actor, "decided_at": _now(),
                                                            "decision_note": body.get("note")})
    studio.db.table("item_events").insert({"item_id": item["id"], "from_status": item["status"], "to_status": to,
                                           "actor": actor, "note": body.get("note")})
    return {"item": updated, "changed": True}


@api.get("/items/{id}/events")
def item_events(req):
    """Audit trail, oldest first. Returns {rows: [{from_status, to_status, actor, note, created_at}]}"""
    item = _item(req)
    rows = studio.db.table("item_events").all({"item_id": item["id"]})
    return {"rows": rows}


@api.get("/items/pending")
def pending_items(req):
    """The approval queue: items still pending, oldest first. Returns {rows, total}"""
    rows = studio.db.table("items").all({"status": "pending"})
    return {"rows": rows, "total": len(rows)}
