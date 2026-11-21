"""POST /api/compare {a, b}: two runs or registered versions. Paired Δ with a bootstrap CI when both were scored on the
same original rows (same dataset root, root-row mapping on both sides, ≥ 30 shared holdout rows, same label and
problem type); otherwise a labelled "different holdout, not paired" table of each side's own holdout score.

P09 (§4.3, DLA4 #3): pairing is the intersection of the two runs' holdout `__row_id` sets, reported as `overlap`
(n_common / the larger holdout). "paired on the same holdout rows" only when the sets are equal; "paired on n of N rows
(holdouts differ)" otherwise; below 50 % overlap no paired Δ at all, only the labelled unpaired table.

Only stored artefacts are read (holdout_predictions.csv, lineage.json, the result JSON), so it works on evicted runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames, stats

MIN_ROWS = 30
#: P09 (§4.3): below this share of the larger holdout in common, no paired Δ is reported (only a labelled unpaired table)
MIN_OVERLAP = 0.5


class CompareError(ValueError):
    pass


def resolve(app, ref: str) -> dict[str, Any]:
    """A run id or a registered version id → {kind, id, run}."""
    run = app.state.runs.get(ref)
    if run:
        return {"kind": "run", "id": ref, "run": run, "label": run["name"]}
    v = app.state.registry.get_version(ref)
    if v:
        run = app.state.runs.get(v["run_id"])
        if not run:
            raise CompareError(f"Version {ref}'s source run {v['run_id']} is gone.")
        return {"kind": "version", "id": ref, "run": run, "label": f"{v['model_name']} v{v['version']}",
                "note": "scored by its source run's holdout predictions"}
    raise CompareError(f"'{ref}' is neither a run nor a registered version.")


def _hp(run: dict[str, Any]) -> pd.DataFrame | None:
    p = Path(run["config"].get("run_dir") or "") / "holdout_predictions.csv"
    if not p.exists():
        return None
    return frames.read_artifact(p, dtype={"y_true": str, "y_pred": str})


def root_info(app, dataset_id: str, positional: bool = False) -> tuple[str, np.ndarray | None, str]:
    """(root dataset id, root row of every row by read position, or None, how). None: the rows can't be mapped to the
    root (a refresh or a monitoring window: new rows), so nothing on it is paired with another dataset.
    P09: a Parquet version's rows carry their identity (`__row_id`, shared by every version of the root); only a
    CSV-era version (until P10 migrates it) is mapped through its lineage file.
    P10: `positional` = the run's holdout rows are read positions (a CSV-era run). On a migrated version they map
    through the table's ids in data.csv order (= the CSV-era row map), reported as before."""
    from ..lake import migrate as MG

    ds = app.state.datasets
    rec = app.state.db.get_dataset(dataset_id) or {}
    with app.state.db.connect() as con:
        r = con.execute("SELECT parent_id, root_id, storage FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    parent, root = (r["parent_id"], r["root_id"]) if r else (None, None)
    lin = ds.lineage(dataset_id) or {}
    if r and r["storage"] == "parquet":
        from ..lake import splitstore as SS

        leg = MG.legacy_mapping(app.state.db, dataset_id) if positional else None
        if leg is None:
            return root or dataset_id, SS.table_row_ids(dataset_id), "__row_id"
        ids, how, is_root = leg
        if ids is not None:
            return (dataset_id if is_root else root or lin.get("root") or dataset_id), ids.astype(int), how
    else:
        n = int(rec.get("rows") or 0)
        rr = MG.legacy_row_map(lin, n)
        if rr is not None:
            return root or lin.get("root") or dataset_id, rr.astype(int), MG.HOW_ROW_MAP
        if not parent and not lin.get("parent"):
            return dataset_id, np.arange(n), MG.HOW_ROOT
    with app.state.db.connect() as con:
        op = (con.execute("SELECT op FROM datasets WHERE id = ?", (dataset_id,)).fetchone() or [None])[0]
    why = "refresh" if op in ("sql", "refresh") or lin.get("source") == "sql" else (op or "new rows")
    return root or lin.get("root") or dataset_id, None, f"no root-row mapping ({why})"


def _ids(hp: pd.DataFrame, mapping: np.ndarray, how: str) -> np.ndarray:
    r = hp["row"].to_numpy().astype("int64")
    if how == "__row_id":
        return r
    out = np.full(len(r), -1, dtype="int64")
    ok = (r >= 0) & (r < len(mapping))
    out[ok] = mapping[r[ok]]
    return out


def _read_modes(app, a: str, b: str) -> tuple[str, str] | None:
    with app.state.db.connect() as con:
        m = {r[0]: r[1] for r in con.execute("SELECT run_id, read_mode FROM run_inputs WHERE role = 'train' AND run_id IN (?, ?)",
                                             (a, b))}
    return (m[a], m[b]) if a in m and b in m else None


#: AutoGluon eval metric → (label, higher is better). Scored from holdout_predictions.csv with numpy (fast enough to
#: bootstrap); anything else falls back to AUC (binary), accuracy (classification) or RMSE (regression), said in `metric_note`.
METRICS = {"roc_auc": ("AUC", True), "accuracy": ("accuracy", True), "balanced_accuracy": ("balanced accuracy", True),
           "f1": ("F1", True), "f1_macro": ("macro F1", True), "f1_weighted": ("weighted F1", True), "precision": ("precision", True),
           "recall": ("recall", True), "average_precision": ("average precision", True), "log_loss": ("log loss", False),
           "root_mean_squared_error": ("RMSE", False), "rmse": ("RMSE", False), "mean_squared_error": ("MSE", False),
           "mean_absolute_error": ("MAE", False), "mae": ("MAE", False), "r2": ("R²", True)}


def _fns(pt: str, f: pd.DataFrame, pos: str | None, metric: str):
    """score(idx) for one side, higher is better (losses negated); None when this metric can't be scored here."""
    if pt in ("binary", "multiclass"):
        yt, yp = f["y_true"].astype(str).to_numpy(), f["y_pred"].astype(str).to_numpy()
        hit = (yt == yp).astype(float)
        classes = sorted(set(yt) | set(yp))
        if metric == "accuracy":
            return lambda i: float(hit[i].mean())
        if metric == "balanced_accuracy":
            return lambda i: float(np.mean([hit[i][yt[i] == c].mean() for c in np.unique(yt[i])]))
        if metric in ("f1", "precision", "recall") and pt == "binary" and pos is not None:
            t, p_ = (yt == pos), (yp == pos)

            def bin_(i, m=metric):
                tp = float((t[i] & p_[i]).sum())
                fp, fn = float((~t[i] & p_[i]).sum()), float((t[i] & ~p_[i]).sum())
                if m == "precision":
                    return tp / (tp + fp) if tp + fp else 0.0
                if m == "recall":
                    return tp / (tp + fn) if tp + fn else 0.0
                return tp / (tp + 0.5 * (fp + fn)) if tp + fp + fn else 0.0
            return bin_
        if metric in ("f1_macro", "f1_weighted"):
            def mf1(i, w=metric == "f1_weighted"):
                out, ws = [], []
                for c in classes:
                    t, p_ = yt[i] == c, yp[i] == c
                    tp, fp, fn = float((t & p_).sum()), float((~t & p_).sum()), float((t & ~p_).sum())
                    if tp + fp + fn == 0:
                        continue
                    out.append(tp / (tp + 0.5 * (fp + fn)))
                    ws.append(float(t.sum()))
                return float(np.average(out, weights=ws) if w and sum(ws) else np.mean(out)) if out else 0.0
            return mf1
        if metric in ("roc_auc", "average_precision") and pt == "binary" and pos is not None and f"p_{pos}" in f:
            y, p_ = (yt == pos).astype(int), f[f"p_{pos}"].to_numpy(float)
            if metric == "roc_auc":
                from ..engine.scorecard import auc

                return lambda i: float(auc(y[i], p_[i]))
            from sklearn.metrics import average_precision_score

            return lambda i: float(average_precision_score(y[i], p_[i]))
        if metric == "log_loss" and all(f"p_{c}" in f for c in set(yt)):
            pt_true = np.clip(np.array([f[f"p_{c}"].to_numpy(float)[k] for k, c in enumerate(yt)]), 1e-15, 1)
            return lambda i: -float(-np.log(pt_true[i]).mean())
        return None
    num = lambda c: pd.to_numeric(f[c], errors="coerce").to_numpy(float)  # noqa: E731
    yt, yp = num("y_true"), num("y_pred")
    e = yt - yp
    if metric in ("root_mean_squared_error", "rmse"):
        return lambda i: -float(np.sqrt(np.nanmean(e[i] ** 2)))
    if metric == "mean_squared_error":
        return lambda i: -float(np.nanmean(e[i] ** 2))
    if metric in ("mean_absolute_error", "mae"):
        return lambda i: -float(np.nanmean(np.abs(e[i])))
    if metric == "r2":
        return lambda i: float(1 - np.nansum(e[i] ** 2) / max(np.nansum((yt[i] - np.nanmean(yt[i])) ** 2), 1e-300))
    return None


def _scorer(pt: str, a: pd.DataFrame, b: pd.DataFrame, pos: str | None, eval_metric: str | None):
    """(metric key, label, higher_is_better, fa, fb, valid, note): the run's own eval metric when it can be scored from the
    stored predictions, else AUC / accuracy / RMSE."""
    fallback = "roc_auc" if pt == "binary" and pos is not None and f"p_{pos}" in a and f"p_{pos}" in b else (
        "accuracy" if pt in ("binary", "multiclass") else "root_mean_squared_error")
    note = None
    m = eval_metric if eval_metric in METRICS else None
    fa = fb = None
    if m:
        fa, fb = _fns(pt, a, pos, m), _fns(pt, b, pos, m)
    if fa is None or fb is None:
        if eval_metric and eval_metric != fallback:
            note = f"the run's metric {eval_metric} can't be scored from the stored predictions here: {METRICS[fallback][0]} instead"
        m = fallback
        fa, fb = _fns(pt, a, pos, m), _fns(pt, b, pos, m)
    valid = None
    if pt == "binary" and pos is not None and m in ("roc_auc", "average_precision"):
        ya = (a["y_true"].astype(str) == pos).to_numpy()
        yb = (b["y_true"].astype(str) == pos).to_numpy()
        valid = lambda i: ya[i].min() != ya[i].max() and yb[i].min() != yb[i].max()  # noqa: E731
    return m, METRICS[m][0], METRICS[m][1], fa, fb, valid, note


def _side(app, x: dict[str, Any]) -> dict[str, Any]:
    run = x["run"]
    res = run.get("result") or {}
    em = res.get("eval_metric") or {}
    m = em.get("name") if isinstance(em, dict) else em
    with app.state.db.connect() as con:
        r = con.execute("SELECT predictor_state, root_id FROM runs JOIN datasets d ON d.id = runs.dataset_id WHERE runs.id = ?",
                        (run["id"],)).fetchone()
    return {"kind": x["kind"], "id": x["id"], "label": x["label"], "run_id": run["id"], "dataset_id": run["dataset_id"],
            "target": run["label"], "problem_type": res.get("problem_type"), "eval_metric": m,
            "holdout_score": ((res.get("holdout") or {}).get("metrics") or {}).get(m) if m else None,
            "holdout_rows": (res.get("data") or {}).get("holdout_rows"), "predictor_state": r["predictor_state"] if r else None,
            "root_id": r["root_id"] if r else None, **({"note": x["note"]} if x.get("note") else {})}


#: the bootstrap's work is capped: B × shared rows ≤ this (B is lowered, and the response says so)
MAX_WORK = 20_000_000


def compare(app, a_ref: str, b_ref: str, b: int = 1000) -> dict[str, Any]:
    xa, xb = resolve(app, a_ref), resolve(app, b_ref)
    ra, rb = xa["run"], xb["run"]
    for r in (ra, rb):
        if r["status"] != "succeeded":
            raise CompareError(f"Run {r['id']} is {r['status']}; compare reads finished runs.")
    A, B = _side(app, xa), _side(app, xb)
    out: dict[str, Any] = {"a": A, "b": B, "paired": False}

    def unpaired(reason: str, comparable: bool = True) -> dict[str, Any]:
        # F-P2 #13: no Δ across different targets or problem types, nor across different metrics
        same_metric = comparable and A["eval_metric"] == B["eval_metric"] and A["holdout_score"] is not None and B["holdout_score"] is not None
        return {**out, "paired": False, "label": "different holdout, not paired", "reason": reason,
                "table": [{"side": s, "id": X["id"], "label": X["label"], "dataset_id": X["dataset_id"], "metric": X["eval_metric"],
                           "holdout_score": X["holdout_score"], "holdout_rows": X["holdout_rows"]} for s, X in (("a", A), ("b", B))],
                "difference": (B["holdout_score"] - A["holdout_score"]) if same_metric else None,
                "difference_note": ("b − a on two different holdouts: no confidence interval, not evidence that one model is "
                                    "better" if same_metric else "not comparable: " + ("the metrics differ" if comparable else reason))}

    if ra["label"] != rb["label"]:
        return unpaired(f"different targets ('{ra['label']}' vs '{rb['label']}')", comparable=False)
    if A["problem_type"] != B["problem_type"]:
        return unpaired(f"different problem types ({A['problem_type']} vs {B['problem_type']})", comparable=False)
    from ..lake.migrate import positional_run

    root_a, map_a, how_a = root_info(app, ra["dataset_id"], positional_run(app.state.db, ra["id"], ra["dataset_id"]))
    root_b, map_b, how_b = root_info(app, rb["dataset_id"], positional_run(app.state.db, rb["id"], rb["dataset_id"]))
    out["roots"] = {"a": {"root": root_a, "how": how_a}, "b": {"root": root_b, "how": how_b}}
    if root_a != root_b:
        return unpaired(f"different original datasets ({root_a} vs {root_b})")
    if map_a is None or map_b is None:
        return unpaired(f"{'a' if map_a is None else 'b'} has {how_a if map_a is None else how_b}")
    hp_a, hp_b = _hp(ra), _hp(rb)
    if hp_a is None or hp_b is None:
        return unpaired("holdout predictions are missing on one side")
    # P09: holdout_predictions.csv names a row by the fit's frame index: the __row_id on a Parquet version (already the
    # root identity), the read position on a CSV-era one (mapped through its root rows)
    hp_a = hp_a.assign(root=_ids(hp_a, map_a, how_a))
    hp_b = hp_b.assign(root=_ids(hp_b, map_b, how_b))
    hp_a, hp_b = hp_a[hp_a["root"] >= 0], hp_b[hp_b["root"] >= 0]  # synthetic rows (root -1-i) never pair
    ids_a, ids_b = np.unique(hp_a["root"].to_numpy()), np.unique(hp_b["root"].to_numpy())
    rows = np.intersect1d(ids_a, ids_b)
    n_big = max(len(ids_a), len(ids_b))
    share = len(rows) / n_big if n_big else 0.0
    same = len(rows) == len(ids_a) == len(ids_b)
    out["overlap"] = {"n_common": int(len(rows)), "n_a": int(len(ids_a)), "n_b": int(len(ids_b)),
                      "share": round(share, 4), "same_holdout": bool(same),
                      "text": ("the same holdout rows" if same else
                               f"paired on {len(rows):,} of {n_big:,} rows (holdouts differ)"),
                      "rule": "overlap = rows in both holdouts / the larger holdout (by __row_id); a paired Δ needs ≥ 50 %"}
    modes = _read_modes(app, ra["id"], rb["id"])
    if modes and modes[0] != modes[1]:
        out["read_modes"] = {"a": modes[0], "b": modes[1], "note": "read modes differ"}
    if len(rows) < MIN_ROWS:
        return unpaired(f"only {len(rows)} holdout rows in common (≥ {MIN_ROWS} needed)")
    if share < MIN_OVERLAP:
        res = unpaired(f"the holdouts differ: {len(rows):,} of {n_big:,} rows in common ({share:.0%}), below the "
                       f"{MIN_OVERLAP:.0%} a paired Δ needs")
        res["label"] = (f"paired on {len(rows):,} of {n_big:,} rows (holdouts differ) — {share:.0%} overlap is below "
                        f"{MIN_OVERLAP:.0%}, so no paired Δ: each run's own holdout score, unpaired")
        return res
    al = hp_a.drop_duplicates("root").set_index("root").loc[rows]
    bl = hp_b.drop_duplicates("root").set_index("root").loc[rows]
    labels_same = bool((al["y_true"].astype(str).to_numpy() == bl["y_true"].astype(str).to_numpy()).all())
    pos = (ra.get("result") or {}).get("positive_class")
    pos = None if pos is None else str(pos)
    pt = A["problem_type"] or ""
    key, metric, gib, fa, fb, valid, mnote = _scorer(pt, al, bl, pos, A["eval_metric"])
    b_asked = b
    b = max(1, min(b, MAX_WORK // max(len(rows), 1)))
    bs = stats.paired_bootstrap(fa, fb, len(rows), b=b, valid=valid)
    nat = (lambda s: s) if gib else (lambda s: -s)
    if key != "roc_auc" and pt == "binary" and pos is not None and f"p_{pos}" in al and f"p_{pos}" in bl:
        fa2, fb2 = _fns(pt, al, pos, "roc_auc"), _fns(pt, bl, pos, "roc_auc")
        ii = np.arange(len(rows))
        out["auc"] = {"a": fa2(ii), "b": fb2(ii), "delta": fb2(ii) - fa2(ii), "note": "alongside, no CI"}
    if mnote:
        out["metric_note"] = mnote
    if b < b_asked:
        out["B_note"] = f"B lowered from {b_asked:,} to {b:,} to cap the work ({len(rows):,} rows)"
    out.update(paired=True, label=("paired on the same holdout rows" if same else
                                   f"paired on {len(rows):,} of {n_big:,} rows (holdouts differ)"), metric=metric, metric_key=key,
               score_a=nat(bs["a"]), score_b=nat(bs["b"]), delta=bs["delta"], ci=bs["ci"], level=bs["level"], B=bs["B"],
               rows=int(len(rows)), rows_a=int(len(hp_a)), rows_b=int(len(hp_b)), same_rows=bool(len(rows) == len(hp_a) == len(hp_b)),
               labels_same=labels_same, excludes_zero=bs["excludes_zero"],
               partial=bool(len(rows) < max(len(hp_a), len(hp_b))),
               sign="Δ = b − a, better is positive" + ("" if gib else f" ({metric}: lower is better, so Δ is the reduction)"),
               verdict=("b better" if bs["ci"][0] is not None and bs["ci"][0] > 0 else "a better" if bs["ci"][1] is not None and bs["ci"][1] < 0
                        else "no difference beyond noise"),
               method=f"paired bootstrap, B = {bs['B']}, percentile {int(bs['level'] * 100)} % CI, rows resampled together")
    if not labels_same:
        out["warning"] = "Some shared rows have different labels in the two versions (relabelled): each side is scored on its own labels."
    return json.loads(json.dumps(out, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
