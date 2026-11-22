"""Label layers, schemas and the append-only event log (labeling plan §3, §5.4). The Python contract for LB-B / LB-C:

    create_layer(db, root_id, table, name, by, team_id=None, kind='labels', purpose='') -> layer
    add_schema(db, layer_id, spec, by, guidelines='', class_map=None) -> schema row
    append_events(db, layer_id, events, by, *, version_id=None, source='human', task_id=None) -> {appended, seqs, events}
        events: [{row_id, column, value, kind='label', item_key='', source?, confidence?, refers_to?, assignment_id?,
                  provenance?, is_null?}]   (≤ 500 per call; hashes computed here from `version_id`'s rows)
    events(db, layer_id, after_seq=0, limit=500, row_id=None) -> [event]
    row_history(db, layer_id, row_id) -> {events, resolved}
"""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from ..catalog.schema import DEFAULT_TEAM, audit, now
from . import registry as REG
from . import rowhash as RH
from . import rows as ROWS
from .values import LabelError, canon_json, canon_label

MAX_BATCH = 500
EVENT_KINDS = ("label", "confirm", "reject", "adjudicate", "retract", "propose", "flag")


def _id(prefix: str) -> str:
    return prefix + secrets.token_hex(8)


def layer(con: Any, layer_id: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM label_layers WHERE id = ?", (layer_id,)).fetchone()
    if not r:
        raise LabelError(404, "layer_not_found", f"No label layer {layer_id}.")
    d = dict(r)
    d.pop("salt", None)
    return d


def _layer_full(con: Any, layer_id: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM label_layers WHERE id = ?", (layer_id,)).fetchone()
    if not r:
        raise LabelError(404, "layer_not_found", f"No label layer {layer_id}.")
    return dict(r)


def current_schema(con: Any, layer_id: str) -> dict[str, Any] | None:
    r = con.execute("SELECT * FROM label_schemas WHERE layer_id = ? ORDER BY version DESC LIMIT 1", (layer_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["spec"] = json.loads(d["spec"])
    d["class_map"] = json.loads(d["class_map"] or "{}")
    return d


def schema_columns(sch: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {c["column"]: c for c in ((sch or {}).get("spec") or {}).get("columns") or []}


def root_of(con: Any, dataset_id: str) -> tuple[str, str]:
    """(root id, team) of any version id of a dataset."""
    r = con.execute("SELECT COALESCE(d.root_id, d.id), COALESCE(d.team_id, p.team_id) FROM datasets d"
                    " LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", (dataset_id,)).fetchone()
    if not r:
        raise LabelError(404, "dataset_not_found", f"No dataset {dataset_id}.")
    return r[0], r[1] or DEFAULT_TEAM


def default_version(con: Any, root: str) -> str:
    r = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
    if r and r[0]:
        return r[0]
    r = con.execute("SELECT id FROM datasets WHERE (root_id = ? OR id = ?) AND state <> 'deleted' ORDER BY created_at DESC LIMIT 1",
                    (root, root)).fetchone()
    if not r:
        raise LabelError(404, "dataset_not_found", f"No version of {root}.")
    return r[0]


def version_root(con: Any, version_id: str) -> str:
    return ROWS.version_info(con, version_id)["root"]


def layers(db: Any, dataset_id: str) -> list[dict[str, Any]]:
    with db.connect() as con:
        root, _ = root_of(con, dataset_id)
        out = []
        for r in con.execute("SELECT * FROM label_layers WHERE root_id = ? ORDER BY created_at", (root,)):
            d = dict(r)
            d.pop("salt", None)
            d["n_events"] = d["next_seq"] - 1
            sch = current_schema(con, d["id"])
            d["schema_version"] = sch["version"] if sch else None
            out.append(d)
        return out


def create_layer(db: Any, dataset_id: str, table: str | None, name: str, by: str, *, team_id: str | None = None,
                 kind: str = "labels", purpose: str = "", spec: dict[str, Any] | None = None) -> dict[str, Any]:
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise LabelError(400, "bad_name", "A layer needs a name (≤ 120 characters).")
    if kind not in ("labels", "corrections", "outcomes"):
        raise LabelError(400, "bad_kind", "kind is labels, corrections or outcomes.")
    with db.connect() as con:
        root, team = root_of(con, dataset_id)
        if not con.execute("SELECT 1 FROM dataset_roots WHERE id = ?", (root,)).fetchone():
            from ..catalog.schema import ensure_root

            ensure_root(con, root)
        v = default_version(con, root)
        table = table or ROWS.version_info(con, v)["primary_table"] or "data"
        if not con.execute("SELECT 1 FROM version_tables WHERE version_id = ? AND table_name = ?", (v, table)).fetchone():
            raise LabelError(400, "table_unknown", f"{v} has no table {table!r}.")
        team = team_id or team
        if con.execute("SELECT 1 FROM label_layers WHERE root_id = ? AND table_name = ? AND team_id = ? AND name = ?",
                       (root, table, team, name)).fetchone():
            raise LabelError(409, "layer_exists", f"A layer named {name!r} already exists on this table.")
        first = not con.execute("SELECT 1 FROM label_layers WHERE root_id = ? AND table_name = ? AND team_id = ? AND is_default = 1",
                                (root, table, team)).fetchone()
        lid = _id("ll_")
        con.execute("INSERT INTO label_layers (id, root_id, table_name, team_id, name, purpose, kind, salt, next_seq, is_default,"
                    " state, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'active', ?, ?)",
                    (lid, root, table, team, name, purpose or "", kind, secrets.token_hex(32), int(first), now(), by))
        audit(con, by, "label_layer_created", {"layer_id": lid, "root_id": root, "table": table, "name": name},
              object_kind="label_layer", object_id=lid)
    if spec:
        add_schema(db, lid, spec, by)
    with db.connect() as con:
        return layer(con, lid)


def patch_layer(db: Any, layer_id: str, b: dict[str, Any], by: str) -> dict[str, Any]:
    with db.connect() as con:
        cur = layer(con, layer_id)
        sets: dict[str, Any] = {}
        if "state" in b:
            if b["state"] not in ("active", "frozen", "archived"):
                raise LabelError(400, "bad_state", "state is active, frozen or archived.")
            sets["state"] = b["state"]
        if "name" in b:
            n = str(b["name"] or "").strip()
            if not n or len(n) > 120:
                raise LabelError(400, "bad_name", "A layer needs a name (≤ 120 characters).")
            sets["name"] = n
        if "purpose" in b:
            sets["purpose"] = str(b["purpose"] or "")[:2000]
        if sets:
            con.execute(f"UPDATE label_layers SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), layer_id))
            audit(con, by, "label_layer_changed", {"layer_id": layer_id, "from": {k: cur[k] for k in sets}, "to": sets},
                  object_kind="label_layer", object_id=layer_id)
        return layer(con, layer_id)


def _check_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict) or not isinstance(spec.get("columns"), list) or not spec["columns"]:
        raise LabelError(400, "bad_schema", "spec is {columns: [{column, role, type, classes?…}]}.")
    seen = set()
    for c in spec["columns"]:
        if not isinstance(c, dict) or not isinstance(c.get("column"), str) or not c["column"]:
            raise LabelError(400, "bad_schema", "Every schema column has a name (column).")
        if c["column"] in seen or c["column"] == "__row_id":
            raise LabelError(400, "bad_schema", f"Column {c['column']!r} is listed twice or reserved.")
        seen.add(c["column"])
        c.setdefault("role", "target")
        c.setdefault("type", "class")
        if c["role"] not in ("target", "feature"):
            raise LabelError(400, "bad_schema", f"{c['column']}: role is target or feature.")
        if c["type"] not in ("class", "number", "text", "date"):
            raise LabelError(400, "bad_schema", f"{c['column']}: type is class, number, text or date.")
        if c["type"] == "class":
            cl = c.get("classes")
            if not isinstance(cl, list) or not cl or len({str(x) for x in cl}) != len(cl):
                raise LabelError(400, "bad_schema", f"{c['column']}: a class column lists its classes (distinct).")
            c["classes"] = [str(x) for x in cl]
    return {"format": "ensemble.labelschema/1", **spec}


def add_schema(db: Any, layer_id: str, spec: Any, by: str, *, guidelines: str = "", class_map: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = _check_spec(spec)
    with db.connect() as con:
        lay = layer(con, layer_id)
        cur = current_schema(con, layer_id)
        ver = (cur["version"] + 1) if cur else 1
        st = canon_json(spec)
        sid = _id("lsc_")
        con.execute("INSERT INTO label_schemas (id, layer_id, version, spec, spec_sha256, guidelines, guidelines_sha256,"
                    " migrates_from, class_map, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, layer_id, ver, st, hashlib.sha256(st.encode()).hexdigest(), guidelines or "",
                     hashlib.sha256((guidelines or "").encode()).hexdigest(), cur["version"] if cur else None,
                     canon_json(class_map or {}), now(), by))
        audit(con, by, "label_schema_added", {"layer_id": layer_id, "version": ver, "root_id": lay["root_id"]},
              object_kind="label_layer", object_id=layer_id)
        return current_schema(con, layer_id)


def _principal_id(by: str) -> str:
    try:
        from ..authz.context import current

        ctx = current()
        if ctx is not None and ctx.principal is not None:
            return ctx.principal.id
    except Exception:  # noqa: BLE001
        pass
    return by


def append_events(db: Any, layer_id: str, events: list[dict[str, Any]], by: str, *, version_id: str | None = None,
                  source: str = "human", task_id: str | None = None, allowed_sources: set[str] | None = None,
                  ctx_columns: list[str] | None = None) -> dict[str, Any]:
    """Validate, hash (against `version_id`, default the root's default version) and append ≤ 500 events with
    consecutive seqs in one transaction (§5.4). Hashes are computed BEFORE the seq transaction."""
    if not isinstance(events, list) or not events:
        raise LabelError(400, "no_events", "events is a non-empty list.")
    if len(events) > MAX_BATCH:
        raise LabelError(400, "too_many_events", f"At most {MAX_BATCH} events per call.")
    actor = _principal_id(by)
    with db.connect() as con:
        lay = _layer_full(con, layer_id)
        if lay["state"] != "active":
            raise LabelError(409, "layer_not_active", f"The layer is {lay['state']}: no new events.")
        vid = version_id or default_version(con, lay["root_id"])
        vi = ROWS.version_info(con, vid)
        if vi["root"] != lay["root_id"]:
            raise LabelError(400, "version_other_root", f"{vid} is not a version of this layer's dataset.")
        srcs = REG.sources(con)
        sch = current_schema(con, layer_id)
    cols_spec = schema_columns(sch)
    table_cols = ROWS.column_names(vid, lay["table_name"])
    norm: list[dict[str, Any]] = []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            raise LabelError(400, "bad_event", f"events[{i}] is an object.")
        col = e.get("column")
        if not isinstance(col, str) or not col:
            raise LabelError(400, "bad_event", f"events[{i}]: column is required.")
        if cols_spec and col not in cols_spec:
            raise LabelError(400, "column_not_in_schema", f"events[{i}]: {col!r} is not a column of the layer's schema.",
                             columns=sorted(cols_spec))
        if col not in table_cols and (cols_spec.get(col) or {}).get("role") == "feature":
            raise LabelError(400, "column_unknown", f"events[{i}]: {col!r} is not a column of {vid}.")
        try:
            rid = int(e.get("row_id"))
        except (TypeError, ValueError):
            raise LabelError(400, "bad_event", f"events[{i}]: row_id is an integer (__row_id).") from None
        kind = e.get("kind") or "label"
        if kind not in EVENT_KINDS:
            raise LabelError(400, "bad_event", f"events[{i}]: kind is one of {', '.join(EVENT_KINDS)}.")
        src = e.get("source") or source
        if src not in srcs:
            raise LabelError(400, "unknown_source", f"events[{i}]: source {src!r} is not registered.", sources=sorted(srcs))
        if allowed_sources is not None and src not in allowed_sources:
            raise LabelError(403, "source_not_allowed", f"events[{i}]: you may append {', '.join(sorted(allowed_sources))} events only.")
        conf = e.get("confidence")
        if conf is not None:
            try:
                cf = float(conf)
            except (TypeError, ValueError):
                cf = float("nan")
            if not (0.0 <= cf <= 1.0):
                raise LabelError(400, "bad_confidence", f"events[{i}]: confidence is between 0 and 1.")
            conf = format(cf, ".6f")
        if kind in ("retract", "reject", "flag"):
            value, is_null = None, 0
        elif e.get("is_null") or ("value" in e and e["value"] is None):
            value, is_null = canon_label(None, cols_spec.get(col), column=col)
        else:
            value, is_null = canon_label(e.get("value"), cols_spec.get(col), column=col)
        if kind in ("confirm", "reject", "retract", "adjudicate") and e.get("refers_to") is not None \
                and not isinstance(e.get("refers_to"), str):
            raise LabelError(400, "bad_event", f"events[{i}]: refers_to is an event id.")
        prov = e.get("provenance") or {}
        if not isinstance(prov, dict):
            raise LabelError(400, "bad_event", f"events[{i}]: provenance is an object.")
        item_key = str(e.get("item_key") or "")
        norm.append({"id": str(e.get("id") or _id("le_")), "row_id": rid, "column_name": col, "item_key": item_key, "kind": kind,
                     "value": value, "is_null": is_null, "source": src, "task_id": e.get("task_id") or task_id,
                     "assignment_id": e.get("assignment_id"), "refers_to": e.get("refers_to"),
                     "confidence": conf, "provenance": canon_json(prov)})
    # hashes from the seen version's rows (absent row: 400, a label names a row that exists)
    rows = ROWS.read(vid, lay["table_name"], [n["row_id"] for n in norm])
    missing = sorted({n["row_id"] for n in norm if n["row_id"] not in rows})
    if missing:
        raise LabelError(400, "row_not_found", f"{len(missing)} row id(s) are not in {vid}: {missing[:5]}.", row_ids=missing[:50])
    for n in norm:
        h = RH.hashes(lay["salt"], rows[n["row_id"]], n["column_name"], ctx_columns)
        n["provenance"] = canon_json({**json.loads(n["provenance"]), "_ctx": h.pop("_ctx")})
        n.update(h)
    schema_version = sch["version"] if sch else 0
    at = now()
    with db.connect() as con:
        cur = con.execute("UPDATE label_layers SET next_seq = next_seq + ? WHERE id = ? AND state = 'active' RETURNING next_seq",
                          (len(norm), layer_id)).fetchone()
        if not cur:
            raise LabelError(409, "layer_not_active", "The layer is not active.")
        first = int(cur[0]) - len(norm)
        out = []
        for k, n in enumerate(norm):
            seq = first + k
            con.execute(
                "INSERT INTO label_events (fmt, id, layer_id, seq, row_id, column_name, item_key, kind, value, is_null, source, actor,"
                " task_id, assignment_id, refers_to, schema_version, confidence, seen_version_id, row_hash_ctx, row_hash_all,"
                " ctx_cols_sha256, base_value_hash, provenance, created_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (n["id"], layer_id, seq, n["row_id"], n["column_name"], n["item_key"], n["kind"], n["value"], n["is_null"],
                 n["source"], actor, n["task_id"], n["assignment_id"], n["refers_to"], schema_version, n["confidence"], vid,
                 n["row_hash_ctx"], n["row_hash_all"], n["ctx_cols_sha256"], n["base_value_hash"], n["provenance"], at))
            out.append({"id": n["id"], "seq": seq, "row_id": n["row_id"], "column": n["column_name"], "kind": n["kind"],
                        "value": n["value"], "source": n["source"]})
        audit(con, by, "label_events_appended", {"layer_id": layer_id, "n": len(norm), "first_seq": first,
                                                 "last_seq": first + len(norm) - 1, "version_id": vid,
                                                 "sources": sorted({n["source"] for n in norm})},
              object_kind="label_layer", object_id=layer_id)
    return {"layer_id": layer_id, "appended": len(out), "seen_version_id": vid, "first_seq": first,
            "last_seq": first + len(out) - 1, "events": out}


def _event_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["column"] = d.pop("column_name")
    d["provenance"] = json.loads(d["provenance"] or "{}")
    d["is_null"] = bool(d["is_null"])
    return d


def events(db: Any, layer_id: str, *, after_seq: int = 0, limit: int = 500, row_id: int | None = None) -> dict[str, Any]:
    limit = max(1, min(int(limit), 5000))
    with db.connect() as con:
        layer(con, layer_id)
        q = "SELECT * FROM label_events WHERE layer_id = ? AND seq > ?"
        a: list[Any] = [layer_id, int(after_seq)]
        if row_id is not None:
            q += " AND row_id = ?"
            a.append(int(row_id))
        rs = [_event_out(r) for r in con.execute(q + " ORDER BY seq LIMIT ?", (*a, limit))]
    return {"events": rs, "next_after_seq": rs[-1]["seq"] if len(rs) == limit else None}
