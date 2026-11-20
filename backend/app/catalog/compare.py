"""POST /api/compare {a, b}: two runs or registered versions. Paired Δ with a bootstrap CI when both were scored on the
same original rows (same dataset root, root-row mapping on both sides, ≥ 30 shared holdout rows, same label and
problem type); otherwise a labelled "different holdout, not paired" table of each side's own holdout score.

Only stored artefacts are read (holdout_predictions.csv, lineage.json, the result JSON), so it works on evicted runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import stats

MIN_ROWS = 30


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
    return pd.read_csv(p, dtype={"y_true": str, "y_pred": str})


def root_info(app, dataset_id: str) -> tuple[str, np.ndarray | None, str]:
    """(root dataset id, root row of every row or None, how). None: the rows can't be mapped to the root (a refresh or a
    monitoring window: new rows), so nothing on it is paired with another dataset."""
    ds = app.state.datasets
    rec = app.state.db.get_dataset(dataset_id) or {}
    with app.state.db.connect() as con:
        r = con.execute("SELECT parent_id, root_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    parent, root = (r["parent_id"], r["root_id"]) if r else (None, None)
    lin = ds.lineage(dataset_id) or {}
    rr = lin.get("root_rows")
    n = int(rec.get("rows") or 0)
    if rr and len(rr) == n:
        return root or lin.get("root") or dataset_id, np.asarray(rr, dtype=int), "root_rows"
    if not parent and not lin.get("parent"):
        return dataset_id, np.arange(n), "is the root"
    return dataset_id, None, "no root-row mapping (new rows)"


def _scorer(pt: str, a: pd.DataFrame, b: pd.DataFrame, pos: str | None):
    """(metric, fa, fb, valid) on aligned frames; higher is better (RMSE negated)."""
    if pt == "binary" and pos is not None and f"p_{pos}" in a and f"p_{pos}" in b:
        from ..engine.scorecard import auc

        ya = (a["y_true"].astype(str) == str(pos)).to_numpy().astype(int)
        yb = (b["y_true"].astype(str) == str(pos)).to_numpy().astype(int)
        pa, pb = a[f"p_{pos}"].to_numpy(float), b[f"p_{pos}"].to_numpy(float)
        return ("AUC", (lambda i: float(auc(ya[i], pa[i]))), (lambda i: float(auc(yb[i], pb[i]))),
                (lambda i: ya[i].min() != ya[i].max() and yb[i].min() != yb[i].max()))
    if pt in ("binary", "multiclass"):
        ea = (a["y_true"].astype(str) == a["y_pred"].astype(str)).to_numpy(float)
        eb = (b["y_true"].astype(str) == b["y_pred"].astype(str)).to_numpy(float)
        return "accuracy", (lambda i: float(ea[i].mean())), (lambda i: float(eb[i].mean())), None
    num = lambda f, c: pd.to_numeric(f[c], errors="coerce").to_numpy(float)  # noqa: E731
    sa = (num(a, "y_true") - num(a, "y_pred")) ** 2
    sb = (num(b, "y_true") - num(b, "y_pred")) ** 2
    return "RMSE", (lambda i: -float(np.sqrt(np.nanmean(sa[i])))), (lambda i: -float(np.sqrt(np.nanmean(sb[i])))), None


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


def compare(app, a_ref: str, b_ref: str, b: int = 1000) -> dict[str, Any]:
    xa, xb = resolve(app, a_ref), resolve(app, b_ref)
    ra, rb = xa["run"], xb["run"]
    for r in (ra, rb):
        if r["status"] != "succeeded":
            raise CompareError(f"Run {r['id']} is {r['status']}; compare reads finished runs.")
    A, B = _side(app, xa), _side(app, xb)
    out: dict[str, Any] = {"a": A, "b": B, "paired": False}

    def unpaired(reason: str) -> dict[str, Any]:
        same_metric = A["eval_metric"] == B["eval_metric"] and A["holdout_score"] is not None and B["holdout_score"] is not None
        return {**out, "paired": False, "label": "different holdout, not paired", "reason": reason,
                "table": [{"side": s, "id": X["id"], "label": X["label"], "dataset_id": X["dataset_id"], "metric": X["eval_metric"],
                           "holdout_score": X["holdout_score"], "holdout_rows": X["holdout_rows"]} for s, X in (("a", A), ("b", B))],
                "difference": (B["holdout_score"] - A["holdout_score"]) if same_metric else None,
                "difference_note": ("b − a on two different holdouts: no confidence interval, not evidence that one model is "
                                    "better" if same_metric else "the metrics differ")}

    if ra["label"] != rb["label"]:
        return unpaired(f"different targets ('{ra['label']}' vs '{rb['label']}')")
    if A["problem_type"] != B["problem_type"]:
        return unpaired(f"different problem types ({A['problem_type']} vs {B['problem_type']})")
    root_a, map_a, how_a = root_info(app, ra["dataset_id"])
    root_b, map_b, how_b = root_info(app, rb["dataset_id"])
    out["roots"] = {"a": {"root": root_a, "how": how_a}, "b": {"root": root_b, "how": how_b}}
    if root_a != root_b:
        return unpaired(f"different original datasets ({root_a} vs {root_b})")
    if map_a is None or map_b is None:
        return unpaired(f"{'a' if map_a is None else 'b'}'s dataset has {how_a if map_a is None else how_b}")
    hp_a, hp_b = _hp(ra), _hp(rb)
    if hp_a is None or hp_b is None:
        return unpaired("holdout predictions are missing on one side")
    hp_a = hp_a.assign(root=map_a[hp_a["row"].to_numpy()])
    hp_b = hp_b.assign(root=map_b[hp_b["row"].to_numpy()])
    hp_a, hp_b = hp_a[hp_a["root"] >= 0], hp_b[hp_b["root"] >= 0]  # synthetic rows (root -1-i) never pair
    rows = np.intersect1d(hp_a["root"].to_numpy(), hp_b["root"].to_numpy())
    if len(rows) < MIN_ROWS:
        return unpaired(f"only {len(rows)} holdout rows in common (≥ {MIN_ROWS} needed)")
    al = hp_a.drop_duplicates("root").set_index("root").loc[rows]
    bl = hp_b.drop_duplicates("root").set_index("root").loc[rows]
    labels_same = bool((al["y_true"].astype(str).to_numpy() == bl["y_true"].astype(str).to_numpy()).all())
    pos = (ra.get("result") or {}).get("positive_class")
    metric, fa, fb, valid = _scorer(A["problem_type"] or "", al, bl, None if pos is None else str(pos))
    bs = stats.paired_bootstrap(fa, fb, len(rows), b=b, valid=valid)
    nat = (lambda s: -s) if metric == "RMSE" else (lambda s: s)
    out.update(paired=True, label="paired on the same original holdout rows", metric=metric,
               score_a=nat(bs["a"]), score_b=nat(bs["b"]), delta=bs["delta"], ci=bs["ci"], level=bs["level"], B=bs["B"],
               rows=int(len(rows)), rows_a=int(len(hp_a)), rows_b=int(len(hp_b)), same_rows=bool(len(rows) == len(hp_a) == len(hp_b)),
               labels_same=labels_same, excludes_zero=bs["excludes_zero"],
               sign="Δ = b − a, better is positive" + (" (RMSE: Δ is the reduction)" if metric == "RMSE" else ""),
               verdict=("b better" if bs["ci"][0] is not None and bs["ci"][0] > 0 else "a better" if bs["ci"][1] is not None and bs["ci"][1] < 0
                        else "no difference beyond noise"),
               method=f"paired bootstrap, B = {bs['B']}, percentile {int(bs['level'] * 100)} % CI, rows resampled together")
    if not labels_same:
        out["warning"] = "Some shared rows have different labels in the two versions (relabelled): each side is scored on its own labels."
    return json.loads(json.dumps(out, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
