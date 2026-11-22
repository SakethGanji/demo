"""G4 route: the fair-lending check of a binary run without protected attributes.

    GET /api/runs/{run_id}/fair-lending?surname=&zip=&truth=&t=&favourable=

Zero-config: the surname and zip columns come from the G0 roles (surname, zip), the hidden true group (if the table
has one) from the "protected" role, the threshold from the run, and which outcome is favourable from the label's name
(a default/fraud/delinquency label: approved = ŷ ≠ the positive class). Every input can be overridden.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import path, require  # F-X4a

from .. import frames
from .. import intake
from ..engine import explain
from ..engine.store import now
from ..http import run_or_404
from . import bisg, tables

router = APIRouter(prefix="/api")
ADVERSE = re.compile(r"default|delinq|fraud|bad|charge_?off|loss|arrear|churn|declin|reject|deny|denied", re.I)


def _role_col(cols: list[dict[str, Any]], role: str) -> tuple[str | None, list[str]]:
    for c in cols:
        if c["role"] == role:
            return c["name"], c["evidence"]
    for c in cols:
        for a in c["also"]:
            if a["role"] == role:
                return c["name"], a["evidence"]
    return None, []


def analyse(rec: dict[str, Any], *, surname: str | None, zip_col: str | None, truth: str | None, t: float | None,
            favourable: str | None) -> dict[str, Any]:
    res = rec.get("result") or {}
    if res.get("problem_type") != "binary":
        raise ValueError(f"The fair-lending check needs a yes/no (binary) model; this run is {res.get('problem_type')}.")
    cfg = rec["config"]
    data = frames.read(frames.resolve(cfg))
    det = intake.detect(data)["columns"]
    found = {}
    for key, role, given in (("surname", "surname", surname), ("zip", "zip", zip_col), ("truth", "protected", truth)):
        col, ev = _role_col(det, role)
        if given == "":  # explicitly none (e.g. ?zip= : surname only)
            found[key] = None
        elif given:
            if given not in data.columns:
                raise ValueError(f"No column named {given!r} in the dataset.")
            found[key] = {"column": given, "source": "set by you", "evidence": ["set by you"]}
        elif col:
            found[key] = {"column": col, "source": "detected", "evidence": ev}
        else:
            found[key] = None
    text_cols = [c["name"] for c in det if c["type"] in ("category", "text", "id")]
    base = {"run_id": rec["id"], "label": rec["label"], "detected": found,
            "candidates": {"surname": text_cols, "zip": [c["name"] for c in det], "truth": text_cols}}
    if not found["surname"]:
        return {**base, "ready": False,
                "reason": "No column reads like a surname. BISG needs the applicant's surname (and ideally a zip code): pick the columns."}
    t_tab = tables.load()
    hd = explain.load_holdout(Path(cfg["run_dir"]))
    rows = hd["row"].to_numpy()
    sub = data.loc[rows].reset_index(drop=True)  # P11: `row` is the __row_id (the frame's index), not a position
    post = bisg.posterior(sub[found["surname"]["column"]], sub[found["zip"]["column"]] if found["zip"] else None, t_tab)
    P = post["P"]
    pos = res.get("positive_class")
    thr = t if t is not None else float((res.get("threshold") or {}).get("value", 0.5))
    y, p = explain.binary_arrays(hd, pos)
    yhat = (p > thr).astype(int)
    adverse = favourable == "negative" if favourable else bool(ADVERSE.search(str(rec["label"])))
    approved = (yhat == 0) if adverse else (yhat == 1)
    a = approved.astype(float)
    groups = t_tab.groups
    air = bisg.weighted_air(P, a, groups)
    truth_arr = None
    if found["truth"]:
        truth_arr = bisg.match_truth(sub[found["truth"]["column"]], groups, tables.LABELS)
        if truth_arr is None:
            found["truth"]["note"] = "its values are not the BISG groups, so it cannot validate the proxy"
    out: dict[str, Any] = {
        **base, "ready": True,
        "tables": {"source": t_tab.source, "label": t_tab.label, "surnames": int(len(t_tab.surname)), "zips": int(len(t_tab.zip))},
        "groups": groups, "group_labels": tables.LABELS, "reference": tables.REFERENCE,
        "n": int(len(sub)), "threshold": thr, "positive_class": str(pos),
        "favourable": {"adverse_label": adverse, "approved_when": f"ŷ ≠ {pos}" if adverse else f"ŷ = {pos}",
                       "approval_rate": float(a.mean())},
        "coverage": {"surname": float(post["surname_matched"].mean()), "zip": float(post["zip_matched"].mean()) if found["zip"] else None},
        "air": air,
        "metricframe": bisg.weighted_metricframe(P, y, yhat, groups),
        "proxy": bisg.proxy_quality(P, groups, truth_arr),
        "limits": {"min_eff_n": bisg.MIN_EFF_N, "min_separability": bisg.MIN_SEPARABILITY, "bootstrap": bisg.B},
    }
    if truth_arr is not None:
        tr = {r["group"]: r for r in bisg.hard_air(truth_arr, a, groups)}
        comp = []
        for r in air:
            g, h = r["group"], tr[r["group"]]
            if r["reference"]:
                continue
            comp.append({"group": g, "judged": r["judged"], "air": r["air"], "plain_air": r["plain_air"], "true_air": h["air"],
                         "true_air_ci": h["air_ci"], "n_true": h["n_true"],
                         "delta": None if r["air"] is None or h["air"] is None else r["air"] - h["air"],
                         "plain_delta": None if r["plain_air"] is None or h["air"] is None else r["plain_air"] - h["air"]})
        judged = [c for c in comp if c["judged"] and c["delta"] is not None]
        out["truth"] = {"column": found["truth"]["column"], "by_group": list(tr.values()), "comparison": comp,
                        "max_abs_delta": max((abs(c["delta"]) for c in judged), default=None),
                        "max_abs_plain_delta": max((abs(c["plain_delta"]) for c in judged if c["plain_delta"] is not None), default=None),
                        "within_005": all(abs(c["delta"]) <= 0.05 for c in judged) if judged else None}
    flagged = [r for r in air if r["flag"] == "below 0.80"]
    worst = min((r for r in air if r["judged"] and not r["reference"] and r["air"] is not None), key=lambda r: r["air"], default=None)
    out["headline"] = {
        "computed_at": now(), "tables": t_tab.source, "surname": found["surname"]["column"],
        "zip": found["zip"]["column"] if found["zip"] else None, "threshold": thr, "n": out["n"],
        "approval_rate": float(a.mean()), "lowest": {k: worst[k] for k in ("group", "air", "air_ci", "diff", "diff_ci")} if worst else None,
        "below_080": [r["group"] for r in flagged],
        "air": {r["group"]: r["air"] for r in air if r["judged"]},
        "proxy_accuracy": out["proxy"].get("accuracy"), "proxy_expected_accuracy": out["proxy"]["expected_accuracy"],
        "max_abs_delta_vs_truth": (out.get("truth") or {}).get("max_abs_delta"),
    }
    return out


@router.get("/runs/{run_id}/fair-lending", dependencies=[require("run.read", path("run_id", "run"))])
async def fair_lending(run_id: str, request: Request, surname: str | None = None, zip: str | None = None,  # noqa: A002
                       truth: str | None = None, t: float | None = Query(None, ge=0, le=1),
                       favourable: Literal["negative", "positive"] | None = None) -> dict[str, Any]:
    """BISG group probabilities from surname × zip, then adverse impact ratios and approval-rate differences with 95%
    bootstrap CIs per group, F4's fairness metrics on the probability-weighted groups, and the proxy's error (against
    the hidden true group when the table has one)."""
    rec = run_or_404(request, run_id)
    if rec["status"] != "succeeded":
        raise HTTPException(409, f"Run {run_id} is {rec['status']}; it needs to finish first.")
    try:
        out = await run_in_threadpool(analyse, rec, surname=surname, zip_col=zip, truth=truth, t=t, favourable=favourable)
    except (ValueError, explain.ExplainError) as e:
        raise HTTPException(400, str(e)) from e
    if out.get("ready"):
        request.app.state.runs.merge_result(run_id, fair_lending=out["headline"])
    return out
