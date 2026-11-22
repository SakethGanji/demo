"""L07 rules (labeling.md §5.11), corrections as proposals (§5.11, review M12), and the F13/F14 migration (§9, L09).

- **Rules** (weak labels): rows of `label_prompts` with `model='rule'`; `template` = the edit compiler's filter AST
  ({column, cmp, value} or a list, ANDed) → a label value. Running one writes `source='rule'` events with
  `provenance.rule_id`. The analysis shows coverage, overlap, conflicts and gold accuracy per rule.
- **Proposals**: corrections to any column (target or feature) are `propose` events plus a `label_proposals` row; no
  version changes. Decide (four eyes, ≠ proposer): approve appends `edit` events for the cells and materialises them
  through the core (snapshot with the cells' columns → `labels.materialise`), so the version is made only by LB-A.
- **Migration**: F14 `annotations.csv` / `adjudications.json` and F13 `answers.json` → `migration` events with
  deterministic ids (re-running inserts nothing), actor always `legacy:<name>`, CSV row position → `__row_id` via the
  P10 row map (`lake.splitstore.row_ids`); `truth.json` and `probs.npz` are never imported; simulated annotators are
  marked `simulated`. The old routes keep reading the run files (byte-identical GETs) and the answer routes also
  append events to the run's layer (adapters in annotations_api / active_learning_api).
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import lbb_common as C


# -- rules ----------------------------------------------------------------------------------------------------------------

def create_rule(layer_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    col, val, flt = body.get("column"), body.get("value"), body.get("filter")
    if not col or val is None or not flt:
        raise HTTPException(400, "A rule is {name, column, value, filter: {column, cmp, value} | [...]}.")
    with C.db().connect() as con:
        lay = C.layer(con, layer_id)
        sch = C.schema(con, layer_id)
        classes = C.classes_of(sch["spec_obj"], col) if sch else None
        if classes and str(val) not in classes:
            raise HTTPException(400, f"'{val}' is not a class of {col}.")
        name = body.get("name") or f"rule:{col}={val}"
        spec = {"column": col, "value": str(val), "filter": flt}
        v = (C.one(con, "SELECT MAX(version) AS v FROM label_prompts WHERE layer_id = ? AND name = ?", (layer_id, name))["v"] or 0) + 1
        rid = C.new_id("lp_")
        con.execute("INSERT INTO label_prompts (id, layer_id, name, version, schema_version, model, effort, template, context_json,"
                    " few_shot, output_schema, prompt_sha256, created_at, created_by) VALUES (?, ?, ?, ?, ?, 'rule', 'none', ?, ?, '{}', '{}', ?, ?, ?)",
                    (rid, layer_id, name, v, sch["version"] if sch else 0, C.canon(flt), C.canon(spec), C.sha(spec), C.now(), by))
        C.audit(con, by, "label_rule_created", {"rule_id": rid, "layer_id": layer_id, **spec}, object_kind="label_layer", object_id=layer_id,
                team_id=lay.get("team_id"))
    return {"id": rid, "name": name, "version": v, **spec}


def _rule(con: Any, rule_id: str) -> dict[str, Any]:
    r = C.one(con, "SELECT * FROM label_prompts WHERE id = ? AND model = 'rule'", (rule_id,))
    if not r:
        raise HTTPException(404, f"No rule '{rule_id}'.")
    r["spec"] = C.loads(r["context_json"], {})
    return r


def _matches(version_id: str, table: str, flt: Any) -> list[int]:
    from .. import frames
    from ..lake import duck
    from ..lake import mask as M
    from ..lake import pages as PG

    m = M.for_version(version_id, table)
    m.cols = {}
    src = frames.scan(version_id, table, mask_for=m)
    try:
        where = PG.filter_where(src, flt)
    except Exception as e:  # noqa: BLE001 - the edit compiler's 400s
        raise HTTPException(400, {"code": "bad_filter", "message": str(getattr(e, "message", e))[:300]}) from None
    at = duck.interactive().query(f"SELECT {PG.q(PG.ROW_ID)} FROM {src.rel} WHERE {where} ORDER BY {PG.q(PG.ROW_ID)}",
                                  fetch="arrow", bind=src.bind)
    return [int(x) for x in at.column(0).to_pylist()]


def run_rule(rule_id: str, version_id: str, by: str) -> dict[str, Any]:
    with C.db().connect() as con:
        r = _rule(con, rule_id)
        lay = C.layer(con, r["layer_id"])
        C.check_version_of_layer(con, lay, version_id)
    ids = _matches(version_id, lay["table_name"], r["spec"]["filter"])
    evs = [{"row_id": i, "column": r["spec"]["column"], "kind": "label", "value": r["spec"]["value"], "source": "rule",
            "seen_version_id": version_id, "provenance": {"rule_id": rule_id, "rule_sha": r["prompt_sha256"]}} for i in ids]
    res = C.append(r["layer_id"], evs, by)
    return {"rule_id": rule_id, "version_id": version_id, "matched": len(ids), "written": len(res)}


def rule_analysis(layer_id: str) -> dict[str, Any]:
    with C.db().connect() as con:
        C.layer(con, layer_id)
        rules = {r["id"]: r for r in C.rows(con, "SELECT id, name, context_json FROM label_prompts WHERE layer_id = ? AND model = 'rule'", (layer_id,))}
        evs = C.rows(con, "SELECT row_id, column_name, value, provenance FROM label_events WHERE layer_id = ? AND source = 'rule' ORDER BY seq", (layer_id,))
        gold = {(int(g["row_id"]), g["column_name"]): g["value"] for g in C.rows(
            con, "SELECT g.row_id, g.column_name, g.value FROM label_gold g JOIN label_tasks t ON t.id = g.task_id WHERE t.layer_id = ?", (layer_id,))}
        n_rows = C.one(con, "SELECT COUNT(DISTINCT row_id) AS n FROM label_events WHERE layer_id = ?", (layer_id,))["n"]
    by_rule: dict[str, dict[tuple[int, str], str]] = defaultdict(dict)
    for e in evs:
        rid = (C.loads(e["provenance"], {}) or {}).get("rule_id")
        by_rule[rid][(int(e["row_id"]), e["column_name"])] = e["value"]
    keys = defaultdict(dict)
    for rid, m in by_rule.items():
        for k, v in m.items():
            keys[k][rid] = v
    out = []
    for rid, m in by_rule.items():
        overlap = sum(1 for k in m if len(keys[k]) > 1)
        conflict = sum(1 for k in m if len(set(keys[k].values())) > 1)
        g = [k for k in m if k in gold]
        out.append({"rule_id": rid, "name": (rules.get(rid) or {}).get("name"), "covered": len(m), "overlap": overlap,
                    "conflicts": conflict, "gold_n": len(g), "gold_accuracy": (sum(m[k] == gold[k] for k in g) / len(g)) if g else None})
    return {"layer_id": layer_id, "rules": out, "rows_with_any_event": n_rows,
            "conflicting_rows": sum(1 for v in keys.values() if len(set(v.values())) > 1)}


# -- proposals -------------------------------------------------------------------------------------------------------------

def propose(layer_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    cells = body.get("cells") or []
    vid = body.get("version_id")
    if not cells or not vid:
        raise HTTPException(400, "A proposal is {version_id, cells: [{row_id, column, value}], reason}.")
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "reason: why these cells should change.")
    with C.db().connect() as con:
        lay = C.layer(con, layer_id)
        C.check_version_of_layer(con, lay, vid)
        pid = C.new_id("lpr_")
        con.execute("INSERT INTO label_proposals (id, layer_id, origin, base_version_id, n_cells, reason, status, proposed_by, proposed_at, cells)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)", (pid, layer_id, body.get("origin", "edit"), vid, len(cells), reason, by,
                                                                       C.now(), C.canon(cells)))
    evs = [{"row_id": int(c["row_id"]), "column": c["column"], "kind": "propose", "value": c.get("value"), "source": "edit",
            "seen_version_id": vid, "refers_to": pid, "provenance": {"proposal_id": pid, "reason": reason[:300]}} for c in cells]
    res = C.append(layer_id, evs, by)
    with C.db().connect() as con:
        C.audit(con, by, "label_proposal_created", {"proposal_id": pid, "layer_id": layer_id, "n_cells": len(cells), "version_id": vid},
                object_kind="label_proposal", object_id=pid, team_id=lay.get("team_id"))
    return {"id": pid, "status": "pending", "n_cells": len(cells), "events": [r["id"] for r in res]}


def proposals(layer_id: str) -> list[dict[str, Any]]:
    with C.db().connect() as con:
        C.layer(con, layer_id)
        out = C.rows(con, "SELECT * FROM label_proposals WHERE layer_id = ? ORDER BY proposed_at DESC", (layer_id,))
    for p in out:
        p["cells"] = C.loads(p.get("cells"), [])
    return out


def decide(proposal_id: str, approve: bool, comment: str, by: str, app: Any) -> dict[str, Any]:
    from .. import labels as L
    from ..catalog import governance as G

    with C.db().connect() as con:
        p = C.one(con, "SELECT * FROM label_proposals WHERE id = ?", (proposal_id,))
        if not p:
            raise HTTPException(404, f"No proposal '{proposal_id}'.")
        if p["status"] != "pending":
            raise HTTPException(409, f"The proposal is {p['status']}.")
        if p["proposed_by"] == by and not G.poc_mode():
            raise HTTPException(409, {"code": "four_eyes", "message": "The proposer cannot decide their own proposal."})
        lay = C.layer(con, p["layer_id"])
        con.execute("UPDATE label_proposals SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    ("approved" if approve else "rejected", by, C.now(), comment or "", proposal_id))
        C.audit(con, by, "label_proposal_decided", {"proposal_id": proposal_id, "approve": approve}, object_kind="label_proposal",
                object_id=proposal_id, team_id=lay.get("team_id"))
    if not approve:
        return {"id": proposal_id, "status": "rejected"}
    cells = C.loads(p["cells"], [])
    evs = [{"row_id": int(c["row_id"]), "column": c["column"], "kind": "label", "value": c.get("value"), "source": "edit",
            "seen_version_id": p["base_version_id"], "refers_to": None,
            "provenance": {"proposal_id": proposal_id, "approved_by": by}} for c in cells]
    C.append(p["layer_id"], evs, p["proposed_by"])
    cols = sorted({c["column"] for c in cells})
    try:
        snap = L.create_snapshot(app.state.db, app.state.lake, p["layer_id"], {"columns": cols, "accept": {"edit": True}}, by)
        mat = L.materialise(app, snap["id"], p["base_version_id"], by, columns=cols, reason=f"proposal {proposal_id}: {p['reason']}"[:300])
    except L.LabelError as e:
        with C.db().connect() as con:
            con.execute("UPDATE label_proposals SET comment = ? WHERE id = ?", (f"{comment or ''} [materialise pending: {e.code}]".strip(), proposal_id))
        return {"id": proposal_id, "status": "approved", "materialised": False, "reason": e.payload()}
    with C.db().connect() as con:
        con.execute("UPDATE label_proposals SET status = 'applied', applied_version_id = ? WHERE id = ?", (mat.get("version_id"), proposal_id))
    return {"id": proposal_id, "status": "applied", "snapshot_id": snap["id"], "version_id": mat.get("version_id"), "materialise": mat}


# -- migration (L09) ---------------------------------------------------------------------------------------------------------

def _eid(*parts: Any) -> str:
    return "le_" + hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:24]


def ensure_run_layer(app: Any, rec: dict[str, Any], classes: list[str], by: str) -> dict[str, Any]:
    """The default layer of (root of the run's dataset, primary table, run team); created with schema v1 from the run's
    classes when missing."""
    from .. import labels as L

    label = rec["config"].get("label") or rec.get("label")
    with C.db().connect() as con:
        d = C.one(con, "SELECT COALESCE(root_id, id) AS root, primary_table, COALESCE(team_id, '') AS team FROM datasets WHERE id = ?",
                  (rec["dataset_id"],))
        if not d:
            raise HTTPException(404, f"Run {rec['id']}'s dataset is gone.")
        lay = C.one(con, "SELECT * FROM label_layers WHERE root_id = ? AND is_default = 1 ORDER BY created_at LIMIT 1", (d["root"],))
    if lay is None:
        try:
            lay = L.create_layer(C.db(), rec["dataset_id"], d["primary_table"], label, by, kind="labels", purpose="migrated F13/F14 labels")
        except L.LabelError as e:
            raise HTTPException(e.status, e.payload()) from e
    with C.db().connect() as con:
        sch = C.schema(con, lay["id"])
    if sch is None or label not in {c.get("column") for c in (sch["spec_obj"].get("columns") or [])}:
        try:
            L.add_schema(C.db(), lay["id"], {"columns": [{"column": label, "role": "target", "type": "class", "classes": classes}]}, by)
        except L.LabelError as e:
            raise HTTPException(e.status, e.payload()) from e
    return lay


def _row_ids(app: Any, rec: dict[str, Any]) -> Any:
    from ..lake import splitstore

    return splitstore.row_ids(C.db(), app.state.datasets, rec["dataset_id"])


def migrate_run(app: Any, run_id: str, by: str, dry_run: bool = False) -> dict[str, Any]:
    from ..engine import active_learning as al
    from ..engine import annotations as an

    rec = app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run '{run_id}'.")
    cfg = rec["config"]
    run_dir = Path(cfg["run_dir"])
    label = cfg.get("label") or rec.get("label")
    long = an.read_long(run_dir)
    adj = an.read_adjudications(run_dir)
    ans = al.read_answers(run_dir)
    al_ans = (ans or {}).get("answers") or {}
    out = {"run_id": run_id, "annotations": int(len(long)), "adjudications": len(adj), "al_answers": len(al_ans)}
    if long.empty and not adj and not al_ans:
        return {**out, "status": "nothing_to_migrate"}
    ids = _row_ids(app, rec)
    if ids is None:
        return {**out, "status": "unmapped", "message": "The run's version has no __row_id map; skipped, never guessed."}
    classes = [str(c) for c in (rec.get("result") or {}).get("classes") or []]
    simulated_names = {a["name"] for a in an.SAMPLE_ANNOTATORS} if an.read_truth(run_dir) is not None else set()
    csv_bytes = (an.ann_dir(run_dir) / "annotations.csv").read_bytes() if (an.ann_dir(run_dir) / "annotations.csv").exists() else b""
    fsha = hashlib.sha256(csv_bytes).hexdigest()
    evs: list[dict[str, Any]] = []
    unmapped = 0
    n = len(ids)
    for i, r in enumerate(long.to_dict("records")):
        pos = int(r["row"])
        if not 0 <= pos < n:
            unmapped += 1
            continue
        name = str(r["annotator"])
        src = str(r.get("source") or "")
        sim = (src == "sample" and name in simulated_names) or name.startswith("simulated")
        evs.append({"id": _eid(run_id, "annotations.csv", pos, name, r.get("at")), "row_id": int(ids[pos]), "column": label,
                    "kind": "label", "value": str(r["label"]), "source": "migration", "actor": f"legacy:{name}",
                    "seen_version_id": rec["dataset_id"],
                    "provenance": {"run_id": run_id, "file": "annotations.csv", "file_sha256": fsha, "row": pos, "line": i,
                                   "source_original": r.get("source"), "at": r.get("at"), "simulated": sim}})
    for pos_s, a in sorted(adj.items()):
        pos = int(pos_s)
        if not 0 <= pos < n:
            unmapped += 1
            continue
        who = str(a.get("by") or "unknown")
        evs.append({"id": _eid(run_id, "adjudications.json", pos, who, a.get("at")), "row_id": int(ids[pos]), "column": label,
                    "kind": "adjudicate", "value": str(a["label"]), "source": "migration", "actor": f"legacy:{who}",
                    "seen_version_id": rec["dataset_id"],
                    "provenance": {"run_id": run_id, "file": "adjudications.json", "row": pos, "at": a.get("at"),
                                   "simulated": who.startswith("simulated")}})
    for item_id, a in sorted(al_ans.items()):
        if a.get("label") is None or a.get("answer") == "skip":
            continue
        try:
            pos = int(str(item_id).rsplit("-", 1)[1])
        except (IndexError, ValueError):
            unmapped += 1
            continue
        if not 0 <= pos < n:
            unmapped += 1
            continue
        who = str(a.get("by") or "unknown")
        evs.append({"id": _eid(run_id, "answers.json", item_id, who, a.get("at")), "row_id": int(ids[pos]), "column": label,
                    "kind": "label", "value": str(a["label"]), "source": "migration", "actor": f"legacy:{who}",
                    "seen_version_id": rec["dataset_id"],
                    "provenance": {"run_id": run_id, "file": "active_learning/answers.json", "item": item_id, "answer": a.get("answer"),
                                   "at": a.get("at"), "simulated": who.startswith("simulated")}})
    sims = sum(1 for e in evs if e["provenance"].get("simulated"))
    if dry_run:
        return {**out, "status": "plan", "events": len(evs), "simulated": sims, "unmapped": unmapped}
    lay = ensure_run_layer(app, rec, classes, by)
    with C.db().connect() as con:
        task = C.one(con, "SELECT id FROM label_tasks WHERE layer_id = ? AND name = ?", (lay["id"], f"F14/F13 import {run_id}"))
        if task is None:
            sch = C.schema(con, lay["id"])
            tid = C.new_id("lt_")
            con.execute("INSERT INTO label_tasks (id, layer_id, name, task_type, schema_version, columns_json, context_json, state,"
                        " created_at, created_by, team_id) VALUES (?, ?, ?, 'class', ?, ?, '[]', 'closed', ?, ?, ?)",
                        (tid, lay["id"], f"F14/F13 import {run_id}", sch["version"], C.canon([label]), C.now(), by, lay.get("team_id")))
        else:
            tid = task["id"]
    for e in evs:
        e["task_id"] = tid
    res = C.append(lay["id"], evs, by)
    imported = sum(1 for r in res if not r.get("already"))
    summary = {**out, "status": "done", "layer_id": lay["id"], "task_id": tid, "events": len(evs), "imported": imported,
               "already": len(res) - imported, "simulated": sims, "unmapped": unmapped}
    with C.db().connect() as con:
        C.audit(con, by, "label_migration_run", summary, object_kind="label_layer", object_id=lay["id"], team_id=lay.get("team_id"))
    return summary


def dual_write(app: Any, rec: dict[str, Any], rows: list[dict[str, Any]], by: str) -> int:
    """Adapter (§9 step 9): an answer on an old run route also appends events to the run's layer, once the run was
    migrated (a task 'F14/F13 import <run>' exists). rows = [{row (CSV position), label, kind, annotator}]. Never fails
    the old route: errors are swallowed and reported as 0."""
    try:
        with C.db().connect() as con:
            t = C.one(con, "SELECT id, layer_id FROM label_tasks WHERE name = ?", (f"F14/F13 import {rec['id']}",))
        if not t or not rows:
            return 0
        ids = _row_ids(app, rec)
        if ids is None:
            return 0
        label = rec["config"].get("label") or rec.get("label")
        evs = [{"row_id": int(ids[int(r["row"])]), "column": label, "kind": r.get("kind", "label"), "value": r["label"],
                "source": "human", "task_id": t["id"], "seen_version_id": rec["dataset_id"],
                "provenance": {"run_id": rec["id"], "route": "adapter", "annotator_name": r.get("annotator")}}
               for r in rows if r.get("label") is not None and 0 <= int(r["row"]) < len(ids)]
        return len(C.append(t["layer_id"], evs, by)) if evs else 0
    except Exception:  # noqa: BLE001
        return 0


_ = Counter
