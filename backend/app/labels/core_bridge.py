"""LB-B → LB-A: the one place that calls the label core (`app.labels.append_events`, contract da3293f). Events are
grouped by the version their labeller saw (the core hashes each call against one version) and sent in batches of
≤ 500. Event ids given by the caller (migration) are kept; ids already in the layer are skipped (idempotent).
An event may name its own `actor` (a job writing `legacy:<name>` events); outside a request the core takes it as given."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import lbb_common as C


def append(layer_id: str, events: list[dict[str, Any]], actor: str) -> list[dict[str, Any]]:
    from .. import labels as L

    if not events:
        return []
    db = C.db()
    given = [e["id"] for e in events if e.get("id")]
    have: set[str] = set()
    if given:
        with db.connect() as con:
            for i in range(0, len(given), 500):
                part = given[i:i + 500]
                have |= {r["id"] for r in C.rows(con, f"SELECT id FROM label_events WHERE layer_id = ? AND id IN ({', '.join('?' * len(part))})",
                                                 [layer_id, *part])}
    out_by_idx: dict[int, dict[str, Any]] = {}
    groups: dict[tuple[str | None, str], list[int]] = {}
    for i, e in enumerate(events):
        if e.get("id") and e["id"] in have:
            out_by_idx[i] = {"id": e["id"], "seq": None, "already": True}
            continue
        ctx = tuple(e["ctx_columns"]) if isinstance(e.get("ctx_columns"), (list, tuple)) else None
        groups.setdefault((e.get("seen_version_id"), e.get("actor") or actor, ctx), []).append(i)
    for (vid, who, ctx), idx in groups.items():
        for j in range(0, len(idx), 500):
            part = idx[j:j + 500]
            evs = [{k: v for k, v in events[i].items() if k not in ("seen_version_id", "schema_version", "created_at", "actor", "ctx_columns")} for i in part]
            try:
                if who != actor:  # an explicit actor (legacy:<name>): outside the request context, so the core keeps it
                    import contextvars

                    res = contextvars.Context().run(L.append_events, db, layer_id, evs, who, version_id=vid,
                                                     ctx_columns=list(ctx) if ctx is not None else None)
                else:
                    res = L.append_events(db, layer_id, evs, who, version_id=vid, ctx_columns=list(ctx) if ctx is not None else None)
            except L.LabelError as e:
                raise HTTPException(e.status, e.payload()) from e
            for i, r in zip(part, res["events"]):
                out_by_idx[i] = r
    return [out_by_idx[i] for i in range(len(events))]
