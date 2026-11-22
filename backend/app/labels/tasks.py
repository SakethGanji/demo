"""L07 (labeling.md §5.8, §5.9): labeling tasks on a layer. A task has an ontology version (the layer's schema) and
guidelines, members (annotators, reviewers; guests are annotators with no team role, scoped to this task only), a
gold set in three disjoint pools (qa / few_shot / calibration), queues filled by strategies (random, active =
uncertainty from a run's OOF p̂ as F13, cleanlab = label issues on that p̂, disputed, gold, llm_low_conf, manual),
leases (`next`), answers (each one a `human` event through the core), gold grading with auto-pause, review and
adjudication (the reviewer never adjudicates an item they voted on), and quality (gold accuracy with Wilson bounds,
Cohen's κ per pair, Fleiss' κ, anchoring)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import HTTPException

from . import lbb_common as C

STRATEGIES = ("random", "active", "cleanlab", "disputed", "gold", "llm_low_conf", "manual")


# -- tasks --------------------------------------------------------------------------------------------------------------

def get_task(con: Any, task_id: str) -> dict[str, Any]:
    t = C.one(con, "SELECT * FROM label_tasks WHERE id = ?", (task_id,))
    if not t:
        raise HTTPException(404, f"No labeling task '{task_id}'.")
    t["columns"] = C.loads(t["columns_json"], [])
    t["context"] = C.loads(t["context_json"], [])
    return t


def public_task(t: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k not in ("columns_json", "context_json")}
    out["unmasked_columns"] = C.loads(t.get("unmasked_columns"), [])
    return out


def create_task(layer_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    if isinstance(body.get("schema"), dict) and body["schema"].get("spec"):  # the UI may send the ontology with the task
        from .. import labels as L

        try:
            L.add_schema(C.db(), layer_id, body["schema"]["spec"], by, guidelines=body["schema"].get("guidelines") or "")
        except L.LabelError as e:
            raise HTTPException(e.status, e.payload()) from e
    with C.db().connect() as con:
        lay = C.layer(con, layer_id)
        sch = C.schema(con, layer_id, body.get("schema_version"))
        if sch is None:
            raise HTTPException(409, {"code": "no_schema", "message": "The layer has no schema version yet: POST …/schemas first."})
        cols = C.as_list(body.get("columns"), "columns")
        if not cols or not all(isinstance(c, str) for c in cols):
            raise HTTPException(400, "columns: the columns this task labels (at least one).")
        for c in cols:
            if body.get("task_type", "class") == "class" and not C.classes_of(sch["spec_obj"], c):
                raise HTTPException(400, f"Column '{c}' has no classes in schema v{sch['version']}.")
        tid = C.new_id("lt_")
        con.execute(
            "INSERT INTO label_tasks (id, layer_id, name, task_type, consensus, schema_version, columns_json, context_json,"
            " votes_per_row, gold_share, blind_share, show_prelabel, review, min_gold_accuracy, lease_seconds, state,"
            " created_at, created_by, min_gold_items, guidelines, team_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
            (tid, layer_id, body.get("name") or "task", body.get("task_type", "class"), body.get("consensus", "majority"),
             sch["version"], C.canon(cols), C.canon(body.get("context") or []), C.as_int(body.get("votes_per_row"), "votes_per_row", default=1, lo=1, hi=50),
             C.as_share(body.get("gold_share"), "gold_share", "0.05"), C.as_share(body.get("blind_share"), "blind_share", "0.10"), 1 if body.get("show_prelabel", True) else 0,
             body.get("review", "disputed"), C.as_share(body.get("min_gold_accuracy"), "min_gold_accuracy", "0.80"),
             C.as_int(body.get("lease_seconds"), "lease_seconds", default=900, lo=10, hi=7 * 86400),
             C.now(), by, C.as_int(body.get("min_gold_items"), "min_gold_items", default=20, lo=0, hi=100_000), body.get("guidelines") or sch.get("guidelines") or "",
             lay.get("team_id")))
        C.audit(con, by, "label_task_created", {"task_id": tid, "layer_id": layer_id, "root_id": lay["root_id"],
                                                 "columns": cols, "schema_version": sch["version"]},
                object_kind="label_task", object_id=tid, team_id=lay.get("team_id"))
        return public_task(get_task(con, tid))


def patch_task(task_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    allowed = {"state", "name", "min_gold_accuracy", "min_gold_items", "gold_share", "blind_share", "show_prelabel",
               "guidelines", "review", "votes_per_row"}
    with C.db().connect() as con:
        get_task(con, task_id)
        for k, v in body.items():
            if k in allowed and v is not None:
                con.execute(f"UPDATE label_tasks SET {k} = ? WHERE id = ?", (str(v) if k.endswith(("share", "accuracy")) else v, task_id))
        C.audit(con, by, "label_task_updated", {"task_id": task_id, "patch": {k: v for k, v in body.items() if k in allowed}},
                object_kind="label_task", object_id=task_id)
        return public_task(get_task(con, task_id))


# -- members ------------------------------------------------------------------------------------------------------------

def add_member(task_id: str, who: str, role: str, by: str) -> dict[str, Any]:
    if role not in ("annotator", "reviewer"):
        raise HTTPException(400, "role is annotator or reviewer.")
    pid = C.resolve_principal(who)
    with C.db().connect() as con:
        t = get_task(con, task_id)
        guest = 1 if C.team_role(con, pid, t.get("team_id")) is None else 0
        con.execute("INSERT INTO label_task_members (task_id, principal_id, role, added_by, added_at, guest) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (task_id, principal_id, role) DO NOTHING", (task_id, pid, role, by, C.now(), guest))
        C.audit(con, by, "label_task_member_added", {"task_id": task_id, "principal_id": pid, "role": role, "guest": bool(guest)},
                object_kind="label_task", object_id=task_id)
    return {"task_id": task_id, "principal_id": pid, "role": role, "guest": bool(guest)}


def members(con: Any, task_id: str) -> list[dict[str, Any]]:
    return C.rows(con, "SELECT m.*, p.display_name FROM label_task_members m LEFT JOIN principals p ON p.id = m.principal_id"
                       " WHERE m.task_id = ? ORDER BY m.role, m.added_at", (task_id,))


def membership(con: Any, task_id: str, pid: str, role: str) -> dict[str, Any] | None:
    return C.one(con, "SELECT * FROM label_task_members WHERE task_id = ? AND principal_id = ? AND role = ?", (task_id, pid, role))


def require_member(con: Any, task_id: str, pid: str, role: str) -> dict[str, Any]:
    m = membership(con, task_id, pid, role)
    if m is None:
        raise HTTPException(403, {"code": "not_a_member", "message": f"You are not a {role} of task {task_id}.", "role": role})
    return m


def resume_member(task_id: str, who: str, by: str) -> dict[str, Any]:
    pid = C.resolve_principal(who)
    with C.db().connect() as con:
        get_task(con, task_id)
        if membership(con, task_id, by, "reviewer") is None and not _is_manager(con, task_id, by):
            raise HTTPException(403, {"code": "not_a_reviewer", "message": "Only a reviewer or a task manager resumes an annotator."})
        n = con.execute("UPDATE label_task_members SET paused_at = NULL, paused_reason = NULL WHERE task_id = ? AND principal_id = ?"
                        " AND role = 'annotator'", (task_id, pid)).rowcount
        C.audit(con, by, "label_annotator_resumed", {"task_id": task_id, "principal_id": pid}, object_kind="label_task", object_id=task_id)
    return {"task_id": task_id, "principal_id": pid, "resumed": bool(n)}


def _is_manager(con: Any, task_id: str, pid: str) -> bool:
    t = get_task(con, task_id)
    return C.team_role(con, pid, t.get("team_id")) in ("maintainer", "owner", "admin")


# -- gold ---------------------------------------------------------------------------------------------------------------

def set_gold(task_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    """{pool, column?, items:[{row_id, value}]} or {pool, column?, from_version: v, n, seed, exclude_pools?}: the second
    copies the current cell values of n random rows (a demo / audit convenience). Pools are disjoint per row and column
    (the primary key): a row in `few_shot` can never be in `calibration` or `qa`."""
    first = (body.get("items") or [{}])[0] if isinstance(body.get("items"), list) else {}
    pool = body.get("pool") or first.get("pool") or "qa"
    body.setdefault("column", first.get("column"))
    if pool not in ("qa", "few_shot", "calibration"):
        raise HTTPException(400, "pool is qa, few_shot or calibration.")
    with C.db().connect() as con:
        t = get_task(con, task_id)
        lay = C.layer(con, t["layer_id"])
        col = body.get("column") or t["columns"][0]
        items = list(C.as_list(body.get("items"), "items", of_dicts=True))
        if body.get("from_version"):
            v = body["from_version"]
            C.check_version_of_layer(con, lay, v)
            taken = {r["row_id"] for r in C.rows(con, "SELECT row_id FROM label_gold WHERE task_id = ? AND column_name = ?", (task_id, col))}
            allr = C.read_rows(v, lay["table_name"], None, [col])
            seed = str(body.get("seed", 0))
            cand = sorted((r for r in allr if r["__row_id"] not in taken and r.get(col) is not None),
                          key=lambda r: C.split(f"{seed}:{pool}:{r['__row_id']}"))
            items = [{"row_id": int(r["__row_id"]), "value": str(r[col])} for r in cand[: C.as_int(body.get("n"), "n", default=20, lo=1, hi=100_000)]]
        sch = C.schema(con, t["layer_id"], t["schema_version"])
        classes = C.classes_of(sch["spec_obj"], col) if sch else None
        added = clash = 0
        for i, it in enumerate(items):
            it["row_id"] = C.as_row_id(it.get("row_id"), f"items[{i}].row_id")
            val = None if it.get("value") is None else str(it["value"])
            if classes and val is not None and val not in classes:
                raise HTTPException(400, f"Gold value '{val}' is not a class of {col}.")
            had = C.one(con, "SELECT pool FROM label_gold WHERE task_id = ? AND row_id = ? AND column_name = ?", (task_id, int(it["row_id"]), col))
            if had and had["pool"] != pool:
                clash += 1
                continue
            con.execute("INSERT INTO label_gold (task_id, row_id, column_name, value, pool, set_by, set_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT (task_id, row_id, column_name) DO UPDATE SET value = excluded.value",
                        (task_id, int(it["row_id"]), col, val, pool, by, C.now()))
            added += 1
        C.audit(con, by, "label_gold_set", {"task_id": task_id, "pool": pool, "column": col, "n": added, "refused_other_pool": clash},
                object_kind="label_task", object_id=task_id)
        counts = {r["pool"]: r["n"] for r in C.rows(con, "SELECT pool, COUNT(*) AS n FROM label_gold WHERE task_id = ? GROUP BY pool", (task_id,))}
    return {"task_id": task_id, "pool": pool, "column": col, "added": added, "refused_other_pool": clash, "pools": counts}


def gold_summary(task_id: str) -> dict[str, Any]:
    with C.db().connect() as con:
        get_task(con, task_id)
        counts = {r["pool"]: r["n"] for r in C.rows(con, "SELECT pool, COUNT(*) AS n FROM label_gold WHERE task_id = ? GROUP BY pool", (task_id,))}
        gold = C.rows(con, "SELECT row_id, column_name, value, pool, set_by, set_at FROM label_gold WHERE task_id = ? ORDER BY pool, row_id", (task_id,))
    return {"task_id": task_id, "pools": counts, "gold": gold}


def gold_rows(con: Any, task_id: str, pool: str, column: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT row_id, column_name, value FROM label_gold WHERE task_id = ? AND pool = ?" + (" AND column_name = ?" if column else "")
    return C.rows(con, q + " ORDER BY row_id", (task_id, pool, column) if column else (task_id, pool))


# -- fills --------------------------------------------------------------------------------------------------------------

def fill(task_id: str, body: dict[str, Any], by: str) -> dict[str, Any]:
    strategy = body.get("strategy", "random")
    if strategy not in STRATEGIES:
        raise HTTPException(400, {"code": "bad_strategy", "message": f"strategy is one of {', '.join(STRATEGIES)}.", "strategies": list(STRATEGIES)})
    n = C.as_int(body.get("n"), "n", default=50, lo=1, hi=10_000, clamp=True)
    with C.db().connect() as con:
        t = get_task(con, task_id)
        lay = C.layer(con, t["layer_id"])
        version_id = body.get("version_id")
        if not version_id:
            raise HTTPException(400, "version_id: the version rows are served from.")
        C.check_version_of_layer(con, lay, version_id)
        col = t["columns"][0]
        already = {r["row_id"] for r in C.rows(con, "SELECT DISTINCT row_id FROM label_assignments WHERE task_id = ?"
                                                    " AND status IN ('open','leased') AND is_gold = 0", (task_id,))}
        gold_all = {r["row_id"] for r in C.rows(con, "SELECT row_id FROM label_gold WHERE task_id = ?", (task_id,))}
        holdout = _holdout_ids(con, body.get("run_id"))
        prelabels: dict[int, str] = {}
        picked: list[tuple[int, str]] = []  # (row_id, priority)
        if strategy == "random":
            ids = [r["__row_id"] for r in C.read_rows(version_id, lay["table_name"], None, [])]
            seed = str(body.get("seed", task_id))
            ids = [i for i in ids if i not in already and i not in gold_all and i not in holdout]
            ids.sort(key=lambda i: C.split(f"{seed}:{i}"))
            picked = [(int(i), "0") for i in ids[:n]]
        elif strategy == "manual":
            ids = [C.as_row_id(x, f"row_ids[{i}]") for i, x in enumerate(C.as_list(body.get("row_ids"), "row_ids"))]
            picked = [(i, "0") for i in ids if i not in already][:n]
        elif strategy == "gold":
            picked = [(int(r["row_id"]), "1") for r in gold_rows(con, task_id, "qa", col)][:n]
        elif strategy == "disputed":
            picked = [(rid, "2") for rid in disputed_rows(con, t)[:n]]
        elif strategy == "llm_low_conf":
            for r in _llm_prelabels(con, t, below=body.get("below")):
                if r["row_id"] not in already and len(picked) < n:
                    picked.append((int(r["row_id"]), "0"))
                    prelabels[int(r["row_id"])] = r["id"]
        elif strategy in ("active", "cleanlab"):
            picked, sugg = _from_run(con, t, lay, version_id, body.get("run_id"), strategy, n, already | gold_all | holdout)
            if strategy == "cleanlab" and sugg:
                evs = [{"row_id": rid, "column": col, "kind": "label", "value": lab, "source": "cleanlab", "task_id": task_id,
                        "seen_version_id": version_id, "provenance": {"run_id": body.get("run_id"), "fill": "cleanlab"}}
                       for rid, lab in sugg.items()]
                for ev, res in zip(evs, C.append(t["layer_id"], evs, by)):
                    prelabels[ev["row_id"]] = res["id"]
        fill_id = C.new_id("lf_")
        n_items = 0
        votes = max(1, int(t["votes_per_row"]))
        blind_share = float(t["blind_share"])
        for rid, prio in picked:
            is_gold = 1 if strategy == "gold" else 0
            is_blind = 1 if (not is_gold and C.split(f"blind:{task_id}:{rid}") < blind_share) else 0
            for _ in range(votes):
                con.execute("INSERT INTO label_assignments (id, task_id, row_id, version_id, item_key, reason, priority, is_gold, is_blind,"
                            " assignee, status, created_at, fill_id, column_name, prelabel_event)"
                            " VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, NULL, 'open', ?, ?, ?, ?)",
                            (C.new_id("la_"), task_id, rid, version_id, strategy, prio, is_gold, is_blind, C.now(), fill_id, col,
                             prelabels.get(rid)))
                n_items += 1
        # gold sprinkled at gold_share into a non-gold fill
        n_gold = 0
        gs = float(t["gold_share"])
        if strategy not in ("gold",) and gs > 0 and picked:
            g = gold_rows(con, task_id, "qa", col)
            want = min(len(g), int(math.ceil(len(picked) * gs / max(1e-9, 1 - gs))))
            for r in sorted(g, key=lambda r: C.split(f"g:{fill_id}:{r['row_id']}"))[:want]:
                for _ in range(votes):
                    con.execute("INSERT INTO label_assignments (id, task_id, row_id, version_id, item_key, reason, priority, is_gold, is_blind,"
                                " assignee, status, created_at, fill_id, column_name) VALUES (?, ?, ?, ?, '', 'gold', '1', 1, 0, NULL, 'open', ?, ?, ?)",
                                (C.new_id("la_"), task_id, int(r["row_id"]), version_id, C.now(), fill_id, col))
                    n_gold += 1
        C.audit(con, by, "label_task_filled", {"task_id": task_id, "strategy": strategy, "rows": len(picked), "items": n_items,
                                                "gold_items": n_gold, "fill_id": fill_id, "version_id": version_id,
                                                "run_id": body.get("run_id")}, object_kind="label_task", object_id=task_id)
    return {"task_id": task_id, "fill_id": fill_id, "strategy": strategy, "rows": len(picked), "items": n_items,
            "gold_items": n_gold, "excluded_holdout": len(holdout)}


def _holdout_ids(con: Any, run_id: str | None) -> set[int]:
    if not run_id:
        return set()
    try:
        ids, pos_hold = _run_row_map(con, run_id)
        return {int(ids[i]) for i in pos_hold}
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        return set()


def _run_row_map(con: Any, run_id: str) -> tuple[np.ndarray, np.ndarray]:
    """The run's version's `__row_id` by read position (P10 row map) and the holdout positions."""
    from ..engine import annotations as an
    from ..lake import splitstore

    rec = _run(con, run_id)
    from ..main import app as _app  # noqa: F401  (the API process' stores)

    ids = splitstore.row_ids(C.db(), _app.state.datasets, rec["dataset_id"])
    if ids is None:
        raise HTTPException(409, {"code": "unmapped", "message": f"Run {run_id}'s rows have no __row_id map."})
    _, hold = an.split_rows(rec["config"])
    return np.asarray(ids), np.asarray(hold)


def _run(con: Any, run_id: str) -> dict[str, Any]:
    from ..main import app as _app

    rec = _app.state.runs.get(run_id)
    if not rec:
        raise HTTPException(404, f"No run '{run_id}'.")
    return rec


def _from_run(con: Any, t: dict[str, Any], lay: dict[str, Any], version_id: str, run_id: str | None, strategy: str, n: int,
              exclude: set[int]) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """`active`: least-confident rows by the run's out-of-fold p̂ (F13's uncertainty, training rows only); `cleanlab`:
    cleanlab's label issues on that p̂ with its suggested label as the pre-label."""
    if not run_id:
        raise HTTPException(400, f"strategy {strategy} needs run_id (a finished classification run on this root).")
    from pathlib import Path

    from ..engine import annotations as an

    rec = _run(con, run_id)
    run_dir = Path(rec["config"]["run_dir"])
    got = an.read_probs(run_dir)
    if got is None:
        raise HTTPException(409, {"code": "no_oof", "message": "The run has no out-of-fold p̂ yet (open its Annotators panel "
                                                                "or the F13 queue once to compute it)."})
    meta, P = got
    rows_pos = np.asarray(meta.get("rows") or range(len(P)))
    classes = [str(c) for c in meta.get("classes") or (rec.get("result") or {}).get("classes") or []]
    ids, hold = _run_row_map(con, run_id)
    hold = set(hold.tolist())
    keep = [i for i, p in enumerate(rows_pos) if int(p) not in hold and int(ids[int(p)]) not in exclude]
    if strategy == "active":
        Pm = P[keep]
        srt = np.sort(Pm, axis=1)
        margin = srt[:, -1] - (srt[:, -2] if Pm.shape[1] > 1 else 0)
        order = np.argsort(margin)[:n]
        return [(int(ids[int(rows_pos[keep[j]])]), f"{1 - float(margin[j]):.4f}") for j in order], {}
    # cleanlab
    from cleanlab.filter import find_label_issues

    data_rows = C.read_rows(version_id, lay["table_name"], [int(ids[int(rows_pos[i])]) for i in keep], [t["columns"][0]])
    given = {r["__row_id"]: str(r[t["columns"][0]]) for r in data_rows}
    sel = [i for i in keep if int(ids[int(rows_pos[i])]) in given and given[int(ids[int(rows_pos[i])])] in classes]
    y = np.array([classes.index(given[int(ids[int(rows_pos[i])])]) for i in sel])
    issues = find_label_issues(y, P[sel], return_indices_ranked_by="self_confidence")[:n]
    picked = [(int(ids[int(rows_pos[sel[j]])]), "1") for j in issues]
    sugg = {int(ids[int(rows_pos[sel[j]])]): classes[int(np.argmax(P[sel[j]]))] for j in issues}
    return picked, sugg


def _llm_prelabels(con: Any, t: dict[str, Any], below: Any = None) -> list[dict[str, Any]]:
    """Latest LLM event per row on the task's column that is not auto-accepted (or below `below`)."""
    col = t["columns"][0]
    evs = C.rows(con, "SELECT id, row_id, value, confidence, provenance, seq FROM label_events WHERE layer_id = ? AND column_name = ?"
                      " AND source = 'llm' AND kind = 'label' ORDER BY seq", (t["layer_id"], col))
    last: dict[int, dict[str, Any]] = {}
    for e in evs:
        last[int(e["row_id"])] = e
    out = []
    for rid, e in sorted(last.items()):
        prov = C.loads(e["provenance"], {}) or {}
        conf = float(e["confidence"] or 0)
        if below is not None:
            if conf < float(below):
                out.append(e)
        elif not prov.get("auto_accept"):
            out.append(e)
    return out


# -- next / answers -----------------------------------------------------------------------------------------------------

def _expire(con: Any, task_id: str) -> None:
    con.execute("UPDATE label_assignments SET status = 'open', assignee = NULL, leased_until = NULL"
                " WHERE task_id = ? AND status = 'leased' AND leased_until < ?", (task_id, C.now()))


def next_items(task_id: str, pid: str, n: int) -> dict[str, Any]:
    n = C.as_int(n, "n", default=10, lo=1, hi=100, clamp=True)
    with C.db().connect() as con:
        t = get_task(con, task_id)
        if t["state"] != "open":
            raise HTTPException(409, {"code": "task_not_open", "message": f"Task is {t['state']}."})
        m = require_member(con, task_id, pid, "annotator")
        if m.get("paused_at"):
            raise HTTPException(409, {"code": "annotator_paused", "message": m.get("paused_reason") or "Paused on this task.",
                                      "paused_at": m["paused_at"]})
        _expire(con, task_id)
        mine = C.rows(con, "SELECT * FROM label_assignments WHERE task_id = ? AND assignee = ? AND status = 'leased' ORDER BY priority DESC, row_id",
                      (task_id, pid))
        voted = {r["row_id"] for r in C.rows(con, "SELECT DISTINCT row_id FROM label_assignments WHERE task_id = ? AND assignee = ?", (task_id, pid))}
        until = (datetime.now(UTC) + timedelta(seconds=int(t["lease_seconds"]))).isoformat(timespec="seconds")
        if len(mine) < n:
            cand = C.rows(con, "SELECT id, row_id FROM label_assignments WHERE task_id = ? AND status = 'open'"
                               " ORDER BY is_gold DESC, priority DESC, row_id LIMIT ?", (task_id, (n - len(mine)) * 8 + 50))
            # gold first only up to its share: interleave so gold is not obvious
            cand.sort(key=lambda r: C.split(f"o:{pid}:{r['id']}"))
            for c in cand:
                if len(mine) >= n:
                    break
                if c["row_id"] in voted:
                    continue
                try:
                    ok = con.execute("UPDATE label_assignments SET status = 'leased', assignee = ?, leased_until = ?"
                                     " WHERE id = ? AND status = 'open'", (pid, until, c["id"])).rowcount
                except Exception:  # noqa: BLE001 - ux_la_once: this caller already has this row
                    ok = 0
                if ok:
                    voted.add(c["row_id"])
                    mine.append(C.one(con, "SELECT * FROM label_assignments WHERE id = ?", (c["id"],)))
        lay = C.layer(con, t["layer_id"])
        unmasked = C.loads(t.get("unmasked_columns"), []) or []
    items = _render(t, lay, mine, unmasked)
    return {"task_id": task_id, "items": items, "n": len(items), "guidelines": t.get("guidelines") or "",
            "schema_version": t["schema_version"], "columns": t["columns"]}


def _render(t: dict[str, Any], lay: dict[str, Any], asg: list[dict[str, Any]], unmasked: list[str]) -> list[dict[str, Any]]:
    by_version: dict[str, list[int]] = defaultdict(list)
    for a in asg:
        by_version[a["version_id"]].append(int(a["row_id"]))
    ctx_cols = list(t["context"])
    data: dict[tuple[str, int], dict[str, Any]] = {}
    for v, ids in by_version.items():
        masked = C.read_rows(v, lay["table_name"], ids, ctx_cols)
        clear = C.read_rows(v, lay["table_name"], ids, unmasked, mask=_plain_mask(v, lay["table_name"])) if unmasked else []
        cl = {r["__row_id"]: r for r in clear}
        for r in masked:
            rr = dict(r)
            for c in unmasked:
                if c in ctx_cols and r["__row_id"] in cl:
                    rr[c] = cl[r["__row_id"]].get(c)
            data[(v, int(r["__row_id"]))] = rr
    pre = {}
    ids = [a["prelabel_event"] for a in asg if a.get("prelabel_event")]
    if ids:
        with C.db().connect() as con:
            for e in C.rows(con, f"SELECT id, value, source, confidence FROM label_events WHERE layer_id = ? AND id IN ({', '.join('?' * len(ids))})",
                            [t["layer_id"], *ids]):
                pre[e["id"]] = e
    out = []
    for a in asg:
        row = data.get((a["version_id"], int(a["row_id"])), {})
        show = bool(t["show_prelabel"]) and not a["is_gold"] and not a["is_blind"]
        p = pre.get(a.get("prelabel_event")) if show else None
        out.append({"assignment_id": a["id"], "row_id": int(a["row_id"]), "column": a.get("column_name") or t["columns"][0],
                    "context": {k: v for k, v in row.items() if k != "__row_id"}, "leased_until": a["leased_until"],
                    "prelabel": ({"value": p["value"], "source": p["source"], "confidence": p["confidence"], "event_id": p["id"]}
                                 if p else None)})
    return out


def _plain_mask(version_id: str, table: str | None) -> Any:
    from ..lake import mask as M

    m = M.for_version(version_id, table)
    m.cols = {}
    return m


def answer(task_id: str, pid: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    with C.db().connect() as con:
        t = get_task(con, task_id)
        m = require_member(con, task_id, pid, "annotator")
        if m.get("paused_at"):
            raise HTTPException(409, {"code": "annotator_paused", "message": m.get("paused_reason") or "Paused on this task."})
        sch = C.schema(con, t["layer_id"], t["schema_version"])
        evs, asg_done, skipped, gold_graded = [], [], 0, []
        for it in items:
            a = C.one(con, "SELECT * FROM label_assignments WHERE id = ? AND task_id = ?", (it.get("assignment_id"), task_id))
            if not a:
                raise HTTPException(404, f"No assignment '{it.get('assignment_id')}' in this task.")
            if a["assignee"] != pid or a["status"] != "leased":
                raise HTTPException(409, {"code": "not_your_lease", "message": f"Assignment {a['id']} is not leased to you (status {a['status']})."})
            col = a.get("column_name") or t["columns"][0]
            if it.get("skip"):
                con.execute("UPDATE label_assignments SET status = 'skipped', done_at = ? WHERE id = ?", (C.now(), a["id"]))
                skipped += 1
                continue
            kind = "flag" if it.get("flag") else "label"
            val = it.get("value")
            classes = C.classes_of(sch["spec_obj"], col) if sch else None
            if kind == "label" and classes and str(val) not in classes:
                raise HTTPException(400, {"code": "bad_value", "message": f"'{val}' is not a class of {col} (schema v{t['schema_version']}).",
                                          "classes": classes})
            refers = None
            if kind == "label" and a.get("prelabel_event") and it.get("confirm"):
                kind, refers = "confirm", a["prelabel_event"]
            prov = {"ms_spent": C.as_int(it.get("ms"), "ms", default=0, lo=0, clamp=True), "blind": bool(a["is_blind"] or a["is_gold"] or not t["show_prelabel"]),
                    "gold": bool(a["is_gold"]), "shown_prelabel": bool(a.get("prelabel_event") and t["show_prelabel"]
                                                                       and not a["is_blind"] and not a["is_gold"])}
            if it.get("note"):
                prov["note"] = str(it["note"])[:500]
            evs.append({"row_id": int(a["row_id"]), "column": col, "kind": kind, "value": None if val is None else str(val),
                        "source": "human", "task_id": task_id, "assignment_id": a["id"], "refers_to": refers,
                        "seen_version_id": a["version_id"], "schema_version": t["schema_version"], "provenance": prov})
            asg_done.append(a)
    res = C.append(t["layer_id"], evs, pid) if evs else []
    paused = None
    with C.db().connect() as con:
        for a in asg_done:
            con.execute("UPDATE label_assignments SET status = 'done', done_at = ? WHERE id = ?", (C.now(), a["id"]))
        for a, ev in zip(asg_done, evs):
            if a["is_gold"] and ev["kind"] in ("label", "confirm"):
                g = C.one(con, "SELECT value FROM label_gold WHERE task_id = ? AND row_id = ? AND column_name = ?", (task_id, a["row_id"], ev["column"]))
                if g:
                    gold_graded.append({"row_id": int(a["row_id"]), "correct": str(g["value"]) == str(ev["value"])})
        if gold_graded:
            acc = gold_accuracy(con, t, pid)
            if acc["n"] >= int(t.get("min_gold_items") or 20) and acc["accuracy"] < float(t["min_gold_accuracy"]):
                why = (f"Gold accuracy {acc['accuracy']:.0%} over {acc['n']} gold items is below the task's "
                       f"{float(t['min_gold_accuracy']):.0%}: paused until a reviewer resumes you.")
                n = con.execute("UPDATE label_task_members SET paused_at = ?, paused_reason = ? WHERE task_id = ? AND principal_id = ?"
                                " AND role = 'annotator' AND paused_at IS NULL", (C.now(), why, task_id, pid)).rowcount
                if n:
                    con.execute("UPDATE label_assignments SET status = 'open', assignee = NULL, leased_until = NULL"
                                " WHERE task_id = ? AND assignee = ? AND status = 'leased'", (task_id, pid))
                    C.audit(con, pid, "label_annotator_paused", {"task_id": task_id, "principal_id": pid, **acc},
                            object_kind="label_task", object_id=task_id)
                paused = {"paused": True, "reason": why, **acc}
    return {"task_id": task_id, "written": len(res), "accepted": len(res), "events": res, "skipped": skipped,
            "gold": {"graded": len(gold_graded), "n": len(gold_graded), "correct": sum(g["correct"] for g in gold_graded)},
            "paused": bool(paused), "paused_reason": paused["reason"] if paused else None, "pause": paused}


def gold_accuracy(con: Any, t: dict[str, Any], pid: str) -> dict[str, Any]:
    from ..stats import wilson

    g = {(int(r["row_id"]), r["column_name"]): r["value"] for r in C.rows(con, "SELECT row_id, column_name, value FROM label_gold WHERE task_id = ? AND pool = 'qa'", (t["id"],))}
    evs = C.rows(con, "SELECT e.row_id, e.column_name, e.value, e.kind, e.seq FROM label_events e JOIN label_assignments a ON a.id = e.assignment_id"
                      " WHERE e.task_id = ? AND e.actor = ? AND a.is_gold = 1 AND e.kind IN ('label','confirm') ORDER BY e.seq", (t["id"], pid))
    last = {}
    for e in evs:
        last[(int(e["row_id"]), e["column_name"])] = e["value"]
    n = sum(1 for k in last if k in g)
    k = sum(1 for kk, v in last.items() if kk in g and str(g[kk]) == str(v))
    lo_hi = wilson(k, n, empty=[None, None])
    return {"n": n, "correct": k, "accuracy": (k / n) if n else 1.0, "wilson_lo": lo_hi[0], "wilson_hi": lo_hi[1]}


# -- votes, disputes, review ---------------------------------------------------------------------------------------------

def votes(con: Any, t: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    """Effective human votes per (row, column) on this task's columns: per actor the latest label/confirm (retract
    removes it), plus the latest adjudication. Machine pre-labels are not votes."""
    cols = t["columns"]
    evs = C.rows(con, f"SELECT id, row_id, column_name, kind, value, actor, source, refers_to, task_id, assignment_id, provenance, seq"
                      f" FROM label_events WHERE layer_id = ? AND column_name IN ({', '.join('?' * len(cols))})"
                      f" AND source IN ('human','migration','import') AND (purged_at IS NULL) ORDER BY seq",
                 [t["layer_id"], *cols])
    gold_asg = {r["id"] for r in C.rows(con, "SELECT id FROM label_assignments WHERE task_id = ? AND is_gold = 1", (t["id"],))}
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for e in evs:
        if e["assignment_id"] in gold_asg:
            continue
        k = (int(e["row_id"]), e["column_name"])
        d = out.setdefault(k, {"votes": {}, "adjudicated": None, "events": []})
        if e["kind"] in ("label", "confirm"):
            d["votes"][e["actor"]] = {"value": e["value"], "event_id": e["id"], "blind": (C.loads(e["provenance"], {}) or {}).get("blind")}
        elif e["kind"] == "retract":
            d["votes"].pop(e["actor"], None)
        elif e["kind"] == "adjudicate":
            d["adjudicated"] = {"value": e["value"], "by": e["actor"], "event_id": e["id"]}
    return out


def disputed_rows(con: Any, t: dict[str, Any], min_agreement: float = 0.66) -> list[int]:
    out = []
    for (rid, _c), d in votes(con, t).items():
        vs = [v["value"] for v in d["votes"].values()]
        if len(vs) >= 2 and not d["adjudicated"]:
            top = Counter(vs).most_common(1)[0][1]
            if top / len(vs) < min_agreement:
                out.append(rid)
    return sorted(set(out))


def review(task_id: str, kind: str, pid: str) -> dict[str, Any]:
    with C.db().connect() as con:
        t = get_task(con, task_id)
        require_member(con, task_id, pid, "reviewer")
        items = []
        if kind == "disputed":
            for (rid, col), d in sorted(votes(con, t).items()):
                vs = [v["value"] for v in d["votes"].values()]
                if len(vs) >= 2 and not d["adjudicated"] and Counter(vs).most_common(1)[0][1] / len(vs) < 0.66:
                    items.append({"row_id": rid, "column": col, "votes": [{"actor": a, "value": v["value"]} for a, v in d["votes"].items()],
                                  "you_voted": pid in d["votes"]})
        elif kind == "gold_fail":
            g = {(int(r["row_id"]), r["column_name"]): r["value"] for r in gold_rows(con, task_id, "qa")}
            for e in C.rows(con, "SELECT e.row_id, e.column_name, e.value, e.actor FROM label_events e JOIN label_assignments a ON a.id = e.assignment_id"
                                 " WHERE e.task_id = ? AND a.is_gold = 1 ORDER BY e.seq", (task_id,)):
                k = (int(e["row_id"]), e["column_name"])
                if k in g and str(g[k]) != str(e["value"]):
                    items.append({"row_id": k[0], "column": k[1], "actor": e["actor"], "answer": e["value"], "gold": g[k]})
        elif kind == "flagged":
            for e in C.rows(con, "SELECT row_id, column_name, actor, provenance FROM label_events WHERE task_id = ? AND kind = 'flag' ORDER BY seq", (task_id,)):
                items.append({"row_id": int(e["row_id"]), "column": e["column_name"], "actor": e["actor"],
                              "note": (C.loads(e["provenance"], {}) or {}).get("note")})
        else:
            raise HTTPException(400, "kind is disputed, gold_fail or flagged.")
        paused = [m for m in members(con, task_id) if m.get("paused_at")]
    return {"task_id": task_id, "kind": kind, "items": items, "n": len(items),
            "paused_annotators": [{"principal_id": m["principal_id"], "display_name": m.get("display_name"),
                                   "reason": m["paused_reason"]} for m in paused]}


def adjudicate(task_id: str, pid: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """The reviewer's final value for an item. Item-level four eyes: refused (409) when the reviewer has a vote on it
    (labeling.md §6.1, `item_four_eyes`); POC mode relaxes it like every other decide path."""
    with C.db().connect() as con:
        t = get_task(con, task_id)
        require_member(con, task_id, pid, "reviewer")
        sch = C.schema(con, t["layer_id"], t["schema_version"])
        vs = votes(con, t)
        poc = _poc()
        evs = []
        for it in items:
            col = it.get("column") or t["columns"][0]
            k = (C.as_row_id(it.get("row_id"), "row_id"), col)
            if k not in vs or not vs[k]["votes"]:
                raise HTTPException(404, f"Row {k[0]} has no votes on {col} in this task.")
            if pid in vs[k]["votes"] and not poc:
                raise HTTPException(409, {"code": "four_eyes", "message": f"You voted on row {k[0]}: another reviewer adjudicates it.",
                                          "row_id": k[0]})
            classes = C.classes_of(sch["spec_obj"], col) if sch else None
            if classes and str(it.get("value")) not in classes:
                raise HTTPException(400, f"'{it.get('value')}' is not a class of {col}.")
            ver = C.one(con, "SELECT version_id FROM label_assignments WHERE task_id = ? AND row_id = ? ORDER BY created_at DESC LIMIT 1",
                        (task_id, k[0]))
            evs.append({"row_id": k[0], "column": col, "kind": "adjudicate", "value": str(it["value"]), "source": "human",
                        "task_id": task_id, "seen_version_id": ver["version_id"] if ver else None, "schema_version": t["schema_version"],
                        "refers_to": vs[k]["votes"][next(iter(vs[k]["votes"]))]["event_id"],
                        "provenance": {"comment": str(it.get("comment") or "")[:500]}})
    res = C.append(t["layer_id"], evs, pid)
    return {"task_id": task_id, "adjudicated": len(res), "events": res}


def _poc() -> bool:
    try:
        from ..catalog import governance as G

        return bool(G.poc_mode())
    except Exception:  # noqa: BLE001
        return False


# -- quality ------------------------------------------------------------------------------------------------------------

def quality(task_id: str) -> dict[str, Any]:
    from ..engine import annotations as an
    from ..stats import wilson

    with C.db().connect() as con:
        t = get_task(con, task_id)
        sch = C.schema(con, t["layer_id"], t["schema_version"])
        col = t["columns"][0]
        classes = (C.classes_of(sch["spec_obj"], col) if sch else None) or []
        vs = votes(con, t)
        ms = defaultdict(list)
        for e in C.rows(con, "SELECT actor, provenance FROM label_events WHERE task_id = ? AND source = 'human' AND kind IN ('label','confirm')", (task_id,)):
            ms[e["actor"]].append(int((C.loads(e["provenance"], {}) or {}).get("ms_spent") or 0))
        names = {m["principal_id"]: m.get("display_name") for m in members(con, task_id)}
        mems = members(con, task_id)
        per = {}
        annotators = sorted({a for d in vs.values() for a in d["votes"]} | {m["principal_id"] for m in mems if m["role"] == "annotator"})
        for a in annotators:
            ga = gold_accuracy(con, t, a)
            items = [(k, d) for k, d in vs.items() if a in d["votes"]]
            agree = [Counter(v["value"] for v in d["votes"].values()).most_common(1)[0][0] == d["votes"][a]["value"]
                     for _k, d in items if len(d["votes"]) >= 2]
            med = float(np.median(ms[a])) if ms.get(a) else None
            paused = next((m for m in mems if m["principal_id"] == a and m["role"] == "annotator" and m.get("paused_at")), None)
            per[a] = {"principal_id": a, "display_name": names.get(a), "items": len(items), "median_ms": med,
                      "items_per_hour": (3_600_000 / med) if med else None, "gold": ga,
                      "agreement_with_majority": (sum(agree) / len(agree)) if agree else None, "paused": bool(paused)}
        idx = {c: i for i, c in enumerate(classes)}
        pairs = []
        for i, a in enumerate(annotators):
            for b in annotators[i + 1:]:
                sh = [(d["votes"][a]["value"], d["votes"][b]["value"]) for d in vs.values() if a in d["votes"] and b in d["votes"]]
                sh = [(x, y) for x, y in sh if x in idx and y in idx]
                if not sh:
                    continue
                ka = an.cohen_kappa(np.array([idx[x] for x, _ in sh]), np.array([idx[y] for _, y in sh]), max(1, len(classes)))
                pairs.append({"a": a, "b": b, "n_shared": len(sh), "kappa": None if math.isnan(ka) else round(ka, 4),
                              "agreement": sum(x == y for x, y in sh) / len(sh), "reliable": len(sh) >= 30})
        full = [d for d in vs.values() if len(d["votes"]) >= max(2, int(t["votes_per_row"]))]
        fk = None
        if full and classes:
            counts = np.zeros((len(full), len(classes)))
            for r, d in enumerate(full):
                for v in d["votes"].values():
                    if v["value"] in idx:
                        counts[r, idx[v["value"]]] += 1
            counts = counts[counts.sum(1) >= 2]
            if len(counts):
                f = an.fleiss_kappa(counts)
                fk = None if math.isnan(f) else round(f, 4)
        # anchoring: agreement with the shown pre-label vs agreement of blind answers with the same pre-label source
        anch = _anchoring(con, t)
        backlog = C.one(con, "SELECT COUNT(*) AS n FROM label_assignments WHERE task_id = ? AND status IN ('open','leased')", (task_id,))["n"]
        done = C.one(con, "SELECT COUNT(*) AS n FROM label_assignments WHERE task_id = ? AND status = 'done'", (task_id,))["n"]
        n_disp = len(disputed_rows(con, t))
        n_adj = sum(1 for d in vs.values() if d["adjudicated"])
    for a in per.values():  # flat aliases for the UI (LB-C labels.ts Quality)
        g = a["gold"]
        a.update(per_hour=a["items_per_hour"], gold_n=g["n"], gold_correct=g["correct"], gold_accuracy=g["accuracy"] if g["n"] else None,
                 gold_ci=[g["wilson_lo"], g["wilson_hi"]] if g["n"] else None,
                 kappa=next((p["kappa"] for p in pairs if a["principal_id"] in (p["a"], p["b"])), None),
                 paused_reason=next((m.get("paused_reason") for m in mems if m["principal_id"] == a["principal_id"] and m["role"] == "annotator"), None))
    return {"task_id": task_id, "annotators": list(per.values()), "cohen_kappa": pairs, "fleiss_kappa": fk,
            "pairwise": [{"a": p["a"], "b": p["b"], "kappa": p["kappa"], "n": p["n_shared"]} for p in pairs],
            "kappa": pairs[0]["kappa"] if len(pairs) == 1 else fk, "n_items": len(vs),
            "fleiss_rows": len(full), "anchoring": anch, "backlog": backlog, "done": done, "disputed": n_disp,
            "adjudicated": n_adj, "classes": classes}


def _anchoring(con: Any, t: dict[str, Any]) -> dict[str, Any]:
    from ..engine import annotations as an

    rows = C.rows(con, "SELECT e.value, e.provenance, p.value AS pre FROM label_events e JOIN label_assignments a ON a.id = e.assignment_id"
                       " JOIN label_events p ON p.layer_id = e.layer_id AND p.id = a.prelabel_event WHERE e.task_id = ?"
                       " AND e.kind IN ('label','confirm')", (t["id"],))
    shown = [r for r in rows if (C.loads(r["provenance"], {}) or {}).get("shown_prelabel")]
    blind = [r for r in rows if not (C.loads(r["provenance"], {}) or {}).get("shown_prelabel")]
    a_s = sum(r["value"] == r["pre"] for r in shown)
    a_b = sum(r["value"] == r["pre"] for r in blind)
    gap = (a_s / len(shown) - a_b / len(blind)) if shown and blind else None
    p = an.mcnemar_exact(a_s, a_b) if shown and blind else None
    return {"shown": len(shown), "shown_agree": a_s, "blind": len(blind), "blind_agree": a_b,
            "gap": gap, "p_value": p, "flagged": bool(gap is not None and gap > 0.10 and p is not None and p < 0.05)}
