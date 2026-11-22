"""Resolution (labeling plan §5.3): events → one decided value per (row, column, item_key), with status and method.

    resolve(db, layer_id, policy=None, through_seq=None, columns=None, row_ids=None) -> {through_seq, policy, rows: [...]}

A row: {row_id, column, item_key, value, is_null, status, method, n_votes, n_annotators, agreement, confidence, sources,
row_hash_ctx, row_hash_all, ctx_cols_sha256, ctx_cols, base_value_hash, seen_version_id, last_seq}.
status ∈ applied · disputed · prelabel_only · schema_dropped."""

from __future__ import annotations

import json
from typing import Any

from . import plugins  # noqa: F401 - registers the consensus plug-ins
from . import registry as REG
from .store import current_schema, schema_columns
from .values import LabelError, canon_json

DEFAULT_POLICY: dict[str, Any] = {
    "columns": None, "method": "majority", "min_votes": 1, "min_agreement": "0.66", "weights": "equal",
    "accept": {"import": True, "migration": True, "edit": True, "remap": True, "rule": False, "model": False,
               "cleanlab": False, "llm": False},
    "stale": "exclude", "stale_on": "ctx", "base_changed": "conflict", "exclude_simulated": True, "through_seq": "latest",
}


def normalise_policy(p: dict[str, Any] | None) -> dict[str, Any]:
    p = dict(p or {})
    bad = [k for k in p if k not in DEFAULT_POLICY and k not in ("reference_run",)]
    if bad:
        raise LabelError(400, "bad_policy", f"Unknown policy keys: {', '.join(bad)}.")
    out = {**DEFAULT_POLICY, **p}
    out["accept"] = {**DEFAULT_POLICY["accept"], **(p.get("accept") or {})}
    if out["method"] not in REG.CONSENSUS:
        raise LabelError(400, "method_unavailable", f"Consensus method {out['method']!r} is not available in this build.",
                         methods=sorted(REG.CONSENSUS))
    if out["stale"] not in ("exclude", "keep") or out["stale_on"] not in ("ctx", "all") \
            or out["base_changed"] not in ("conflict", "layer_wins"):
        raise LabelError(400, "bad_policy", "stale is exclude|keep, stale_on ctx|all, base_changed conflict|layer_wins.")
    out["min_agreement"] = str(out["min_agreement"])
    return out


def _decide_machine(cands: list[dict[str, Any]], srcs: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    for v in sorted(cands, key=lambda x: (srcs[x["source"]]["precedence"], -x["seq"])):
        acc = policy["accept"].get(v["source"])
        if isinstance(acc, dict):  # llm: {min_confidence, …}
            ok = v.get("confidence") is not None and float(v["confidence"]) >= float(acc.get("min_confidence") or 1)
        else:
            ok = bool(acc)
        if ok:
            return {"value": v["value"], "is_null": v["is_null"], "status": "applied", "method": f"accepted_{v['source']}",
                    "agreement": None, "deciding": v}
    return None


def resolve(db: Any, layer_id: str, policy: dict[str, Any] | None = None, *, through_seq: int | None = None,
            columns: list[str] | None = None, row_ids: list[int] | None = None) -> dict[str, Any]:
    pol = normalise_policy(policy)
    with db.connect() as con:
        lay = con.execute("SELECT next_seq FROM label_layers WHERE id = ?", (layer_id,)).fetchone()
        if not lay:
            raise LabelError(404, "layer_not_found", f"No label layer {layer_id}.")
        last = int(lay[0]) - 1
        ts = last if through_seq is None and pol["through_seq"] in (None, "latest") else int(through_seq or pol["through_seq"])
        ts = min(ts, last)
        srcs = REG.sources(con)
        sch = current_schema(con, layer_id)
        q = ("SELECT id, seq, row_id, column_name, item_key, kind, value, is_null, source, actor, refers_to, confidence,"
             " seen_version_id, row_hash_ctx, row_hash_all, ctx_cols_sha256, base_value_hash, provenance"
             " FROM label_events WHERE layer_id = ? AND seq <= ? AND purged_at IS NULL")
        a: list[Any] = [layer_id, ts]
        cols = columns or pol.get("columns")
        if cols:
            q += f" AND column_name IN ({','.join('?' for _ in cols)})"
            a += list(cols)
        if row_ids is not None:
            ids = [int(i) for i in row_ids] or [-1]
            q += f" AND row_id IN ({','.join('?' for _ in ids)})"
            a += ids
        evs = [dict(r) for r in con.execute(q + " ORDER BY seq", a)]
        by_id = {e["id"]: e for e in evs}
    spec_cols = schema_columns(sch)
    groups: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for e in evs:
        groups.setdefault((e["row_id"], e["column_name"], e["item_key"]), []).append(e)
    out = []
    for (rid, col, ik), es in sorted(groups.items()):
        votes: dict[tuple[str, str], dict[str, Any]] = {}
        adjud = None
        for e in es:
            prov = json.loads(e["provenance"] or "{}")
            if pol["exclude_simulated"] and prov.get("simulated"):
                continue
            k = (e["actor"], e["source"])
            if e["kind"] == "adjudicate":
                adjud = e
            elif e["kind"] == "label":
                votes[k] = e
            elif e["kind"] == "confirm":
                ref = by_id.get(e["refers_to"] or "")
                v = dict(e)
                if v["value"] is None and not v["is_null"] and ref is not None:
                    v["value"], v["is_null"] = ref["value"], ref["is_null"]
                votes[k] = v
            elif e["kind"] in ("retract", "reject"):
                votes.pop(k, None)
        vs = list(votes.values())
        human = [v for v in vs if not srcs[v["source"]]["machine"] and v["source"] not in ("import", "migration", "edit", "remap")]
        human_like = [v for v in vs if not srcs[v["source"]]["machine"] and v not in human
                      and pol["accept"].get(v["source"], True)]
        dec: dict[str, Any] | None = None
        if adjud is not None:
            dec = {"value": adjud["value"], "is_null": adjud["is_null"], "status": "applied", "method": "adjudicated",
                   "agreement": None, "deciding": adjud}
        elif human or human_like:
            pool = human or human_like
            r = REG.CONSENSUS[pol["method"]](pool, pol)
            if r is not None:
                dec = {**r, "deciding": max((v for v in pool if (v["value"], v["is_null"]) == (r["value"], r["is_null"])),
                                            key=lambda x: x["seq"], default=max(pool, key=lambda x: x["seq"]))}
        if dec is None:
            machine = [v for v in vs if srcs[v["source"]]["machine"]]
            dec = _decide_machine(machine, srcs, pol) if machine else None
            if dec is None and machine:
                m = max(machine, key=lambda x: x["seq"])
                dec = {"value": None, "is_null": 0, "status": "prelabel_only", "method": f"prelabel_{m['source']}",
                       "agreement": None, "deciding": m}
        if dec is None:
            continue  # every vote retracted
        c = spec_cols.get(col)
        if dec["status"] == "applied" and c and c.get("type") == "class" and not c.get("multi") and not dec["is_null"] \
                and dec["value"] not in c.get("classes", []):
            mapped = (sch.get("class_map") or {}).get(dec["value"])
            if mapped in c.get("classes", []):
                dec["value"] = mapped
            else:
                dec["status"] = "schema_dropped"
        d = dec["deciding"]
        prov = json.loads(d.get("provenance") or "{}")
        out.append({
            "row_id": rid, "column": col, "item_key": ik,
            "value": dec["value"] if dec["status"] == "applied" else None, "is_null": bool(dec["is_null"]),
            "status": dec["status"], "method": dec["method"],
            "n_votes": len(vs), "n_annotators": len({v["actor"] for v in vs}),
            "agreement": None if dec.get("agreement") is None else format(dec["agreement"], ".6f"),
            "confidence": d.get("confidence"), "sources": ",".join(sorted({v["source"] for v in vs} | {d["source"]})),
            "row_hash_ctx": d["row_hash_ctx"], "row_hash_all": d["row_hash_all"], "ctx_cols_sha256": d["ctx_cols_sha256"],
            "ctx_cols": canon_json(prov.get("_ctx")) if prov.get("_ctx") is not None else None,
            "base_value_hash": d["base_value_hash"], "seen_version_id": d["seen_version_id"], "last_seq": max(e["seq"] for e in es),
        })
    return {"layer_id": layer_id, "through_seq": ts, "policy": pol, "rows": out}
