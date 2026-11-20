"""D3 · Typed experiments: one change to a finished run's own body, trained as a child run with lineage
(`parent_run_id`, `source: doctor-experiment`, `patch.experiment`), on the same dataset and so the same holdout rows.
The result is Δ = score(child) − score(parent) on the shared holdout rows with a paired bootstrap (B = 1,000).

Kinds (D4's review loop adds its hypotheses through the same route):
  ablate   {columns: [...]}  retrain without the columns (leakage ablation)
  budget   {}                twice the time budget (≤ 4 h)
  preset   {}                the next preset (medium → good → high → best)

Score: binary AUC (a cost run: expected cost per 1,000 at each run's served threshold, lower is better), multiclass
accuracy, regression RMSE (lower is better). The sign of Δ is "better is positive" in every case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

KINDS = ("ablate", "budget", "preset")
NEXT_PRESET = {"medium_quality": "good_quality", "good_quality": "high_quality", "high_quality": "best_quality",
               "best_quality": "best_quality"}


def body_for(rec: dict[str, Any], spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The child's POST /api/runs body and its name. Raises ValueError with a message for the user."""
    from ..gym.api import run_body

    kind = spec.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)} (got {kind!r}); experiments are typed, never free-form.")
    b = run_body(rec)
    feats = set((rec.get("result") or {}).get("features") or [])
    if kind == "ablate":
        cols = spec.get("columns")
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) for c in cols):
            raise ValueError("ablate needs columns: a non-empty list of column names.")
        unknown = [c for c in cols if c not in feats]
        if unknown:
            raise ValueError(f"not features of this run: {', '.join(unknown)}.")
        if len(set(cols)) >= len(feats):
            raise ValueError("an ablation must leave at least one feature.")
        b["drop_columns"] = list(dict.fromkeys(list(b.get("drop_columns") or []) + cols))
        what = f"without {', '.join(cols)}"
    elif kind == "budget":
        tau = float(b.get("time_limit") or ((rec.get("result") or {}).get("fit_seconds") or 120))
        b["time_limit"] = float(min(4 * 3600, round(tau * 2)))
        what = f"budget {tau:.0f} s → {b['time_limit']:.0f} s"
    else:
        cur = b.get("presets") or "medium_quality"
        cur = cur[0] if isinstance(cur, list) else cur
        nxt = NEXT_PRESET.get(cur, "good_quality")
        if nxt == cur:
            raise ValueError(f"the run already uses {cur}.")
        b["presets"] = nxt
        what = f"preset {cur} → {nxt}"
    b["parent_run_id"] = rec["id"]
    b["source"] = "doctor-experiment"
    b["patch"] = {"id": f"experiment:{kind}", "title": what, "experiment": {"kind": kind, **({"columns": spec["columns"]} if kind == "ablate" else {})},
                  "changes": []}
    b["name"] = f"{rec['name']} · {what}"
    return b, what


def _scores(rec: dict[str, Any]) -> tuple[pd.DataFrame | None, str]:
    p = Path(rec["config"]["run_dir"]) / "holdout_predictions.csv"
    if not p.exists():
        return None, ""
    hp = pd.read_csv(p, dtype={"y_true": str, "y_pred": str})
    return hp, (rec.get("result") or {}).get("problem_type") or ""


def _metric(pt: str, cost: dict[str, Any] | None):
    """(label, row-level scorer on a frame → higher-is-better float, natural(score))."""
    from ..engine.scorecard import auc

    if pt == "binary" and cost and cost.get("kind") == "binary":
        return "cost / 1,000", None, (lambda s: -s)
    if pt == "binary":
        return "AUC", lambda y, p: float(auc(y, p)), (lambda s: s)
    if pt == "multiclass":
        return "accuracy", None, (lambda s: s)
    return "RMSE", None, (lambda s: -s)


def delta(parent: dict[str, Any], child: dict[str, Any], b: int = 1000) -> dict[str, Any] | None:
    """Δ = score(child) − score(parent), better is positive, paired over the shared holdout rows."""
    from ensemble_models.cost import binary_row_costs

    hp_a, pt = _scores(parent)
    hp_b, _ = _scores(child)
    if hp_a is None or hp_b is None:
        return None
    rows = np.intersect1d(hp_a["row"].to_numpy(), hp_b["row"].to_numpy())
    if len(rows) < 30:
        return None
    a = hp_a.set_index("row").loc[rows]
    c = hp_b.set_index("row").loc[rows]
    res = parent.get("result") or {}
    cost = parent["config"].get("cost")
    label, auc_fn, nat = _metric(pt, cost)
    pos = str(res.get("positive_class"))
    n = len(rows)
    if pt == "binary":
        y = (a["y_true"].astype(str) == pos).to_numpy().astype(int)
        if cost and cost.get("kind") == "binary":
            ta = float(((res.get("threshold") or {}).get("value", 0.5)))
            tb = float((((child.get("result") or {}).get("threshold") or {}).get("value", 0.5)))
            ra = 1000 * binary_row_costs(y, a[f"p_{pos}"].to_numpy() > ta, cost["c_fp"], cost["c_fn"], cost.get("g_tp") or 0)
            rb = 1000 * binary_row_costs(y, c[f"p_{pos}"].to_numpy() > tb, cost["c_fp"], cost["c_fn"], cost.get("g_tp") or 0)
            fa = lambda i: -float(ra[i].mean())  # noqa: E731
            fb = lambda i: -float(rb[i].mean())  # noqa: E731
        else:
            pa, pb = a[f"p_{pos}"].to_numpy(), c[f"p_{pos}"].to_numpy()
            fa = lambda i: auc_fn(y[i], pa[i])  # noqa: E731
            fb = lambda i: auc_fn(y[i], pb[i])  # noqa: E731
    elif pt == "multiclass":
        ea = (a["y_true"].astype(str) == a["y_pred"].astype(str)).to_numpy().astype(float)
        eb = (c["y_true"].astype(str) == c["y_pred"].astype(str)).to_numpy().astype(float)
        y = ea
        fa = lambda i: float(ea[i].mean())  # noqa: E731
        fb = lambda i: float(eb[i].mean())  # noqa: E731
    else:
        yt = pd.to_numeric(a["y_true"], errors="coerce").to_numpy()
        sa = (yt - pd.to_numeric(a["y_pred"], errors="coerce").to_numpy()) ** 2
        sb = (yt - pd.to_numeric(c["y_pred"], errors="coerce").to_numpy()) ** 2
        y = sa
        fa = lambda i: -float(np.sqrt(sa[i].mean()))  # noqa: E731
        fb = lambda i: -float(np.sqrt(sb[i].mean()))  # noqa: E731
    allr = np.arange(n)
    s_a, s_b = fa(allr), fb(allr)
    rng = np.random.default_rng(0)
    ds = []
    for _ in range(b):
        i = rng.integers(0, n, n)
        if pt == "binary" and not cost and y[i].min() == y[i].max():
            continue
        ds.append(fb(i) - fa(i))
    ds = np.asarray(ds)
    return {"metric": label, "parent": nat(s_a), "child": nat(s_b), "delta": float(s_b - s_a),
            "ci": [float(np.quantile(ds, 0.025)), float(np.quantile(ds, 0.975))], "rows": int(n), "B": int(len(ds)),
            "same_rows": bool(len(rows) == len(hp_a) == len(hp_b)),
            "sign": "better is positive" + (" (a cost or an error: Δ is the reduction)" if label in ("cost / 1,000", "RMSE") else "")}


def list_for(store, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """The run's experiments (newest first), each with its status and, once it succeeded, Δ."""
    out = []
    for r in store.list():
        cfg = r.get("config") or {}
        if cfg.get("parent_run_id") != rec["id"] or cfg.get("source") != "doctor-experiment":
            continue
        ex = ((cfg.get("patch") or {}).get("experiment")) or {}
        full = store.get(r["id"]) if r.get("status") == "succeeded" else r
        item = {"run_id": r["id"], "name": r.get("name"), "status": r.get("status"), "kind": ex.get("kind"),
                "columns": ex.get("columns"), "title": (cfg.get("patch") or {}).get("title"), "created_at": r.get("created_at")}
        if r.get("status") == "succeeded":
            try:
                item["result"] = delta(rec, full)
            except Exception as e:  # noqa: BLE001
                item["error"] = f"{type(e).__name__}: {e}"[:200]
        out.append(item)
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def jsonable(o: Any) -> Any:
    return json.loads(json.dumps(o, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
