"""F17 · Model Doctor: one fixed decision procedure that names the factor most likely limiting a run.

It reads what earlier checks already computed and measures only what is cheap:

  measured here (a worker job, cached by the holdout predictions' SHA)
    fit gap      the run's metric on its training rows vs its holdout (`predictor.evaluate`), z = gap / SE_boot
    confidence   on the holdout, the error rate of the 20 % most confident predictions (the "error floor") with a
                 Wilson interval, next to the error the model itself expects there, mean(1 − max p̂)
    cleanlab     `cleanlab.filter.find_label_issues(prune_by_noise_rate)` on the holdout's out-of-sample p̂: the share
                 of labels confident learning flags
    slices       for each listed weak slice (F16), the loss of predicting the slice's own base rate (a constant):
                 the floor a hard-by-nature region cannot go below
  read from earlier sessions (never started here; offered when missing)
    Vela         leakage, val-vs-holdout optimism, imbalance, time budget (app/vela/findings.py)
    F6           random vs out-of-time holdout score of the same config (split-comparison)
    F12          learning_curve/1 verdict          F13  active_learning/1 verdict      F14  annotations/1
    F15          shift/1 (adversarial AUC)         F16  slices/1                       F3   latency profile

Every check ends in one status: finding (with a severity), clear, no-evidence (e.g. a "noisy" verdict), missing (the
job has not run: an offer to run it) or n/a. Severity is a fixed rule on the evidence numbers (see RULES). The
primary factor is the finding with the highest severity; ties go to the earlier factor in PRECEDENCE (a factor that
invalidates the measurement comes before one that limits the model). The rest are secondary, in the same order.

Reconciliations (each written into the check's `reason`):
* label noise vs hard-by-nature: cleanlab flags overlap as noise too, so a noise finding needs the error floor of the
  most confident predictions to be high **and** the model to expect part of it (not overconfident), plus cleanlab's
  rate ≥ 5 %. An error floor the model does not expect at all is overconfidence: it goes to overfit.
* overfit vs label noise: a train–holdout gap alone appears whenever labels are noisy (flips get memorised), so a
  classification overfit finding needs the gap (z ≥ 3) **and** overconfidence on the holdout.
* saturated learning curve: if the model's holdout predictions never change (one class or constant), "saturated" is
  about the model, not the data: reported as underfit.
* shift: F15 sees covariate shift only; F6's random vs out-of-time gap sees any shift. Either can raise the finding.
* weak slices: a slice whose loss is no worse than predicting its own base rate is hard by nature (minor); a slice
  where the model does worse than that constant is fixable (major).
* learning curve / active learning "noisy" or "no-clear-difference": no evidence, never a finding.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SCHEMA = "doctor/1"
PROCEDURE = "doctor-procedure/1"
SEV = {"critical": 3, "major": 2, "minor": 1}
PRECEDENCE = ["leakage", "shift", "label_noise", "overfit", "data_limited", "underfit", "latency", "weak_slice",
              "budget", "imbalance", "weak_class"]
TITLES = {"leakage": "Leakage", "shift": "Shift between training and deployment", "label_noise": "Label noise",
          "overfit": "Overfitting", "data_limited": "Data-limited", "underfit": "Underfitting (the model is too simple)",
          "latency": "Latency budget", "weak_slice": "Weak slice", "budget": "Time budget", "imbalance": "Imbalanced label",
          "weak_class": "Weak class"}
# The fixed thresholds, also returned with every diagnosis so the page can show them.
RULES = {
    "leakage": "critical: one feature alone predicts the label (AUC / accuracy ≥ 0.97, R² ≥ 0.97) with ≥ 50 % of the importance; "
               "major: an identifier used as a feature (Vela's leakage check)",
    "shift": "critical: out-of-time optimism z ≥ 3 and Δ ≥ 0.02, or adversarial verdict strong; major: z ≥ 2, or verdict moderate; "
             "minor: verdict slight, or a batch PSI > 0.25",
    "label_noise": "classification: error floor (top-20 % confidence) Wilson lower bound ≥ 0.06, not overconfident, cleanlab rate ≥ 0.05 → "
                   "major (critical if the lower bound ≥ 0.12); or the annotators' consensus changes ≥ 5 % of labels (F14) → major",
    "overfit": "z = (train − holdout)/SE ≥ 3 and, for classification, overconfident (floor − expected ≥ 0.05 and expected < floor/3) → "
               "major (critical if z ≥ 8); Vela's validation-vs-holdout optimism alone → minor",
    "data_limited": "learning curve verdict data-limited (CI of the gain per doubling above ε) → major; "
                    "+ active learning beats random → the labelling queue is the prescription",
    "underfit": "learning curve saturated while the holdout predictions are constant → major",
    "latency": "served one-row p95 of the best model > budget → major (critical if > 2 × budget)",
    "weak_slice": "a weak slice (F16, BH q ≤ α) whose loss is above its own base-rate constant's (CI lower bound) → major; otherwise minor",
    "budget": "Vela: the fit used ≥ 95 % of τ or skipped models → minor (major if models were skipped)",
    "imbalance": "Vela: minority share < 20 % with an uncalibrated threshold → minor",
    "weak_class": "Vela: a class's recall ≥ 0.05 below the macro mean → minor",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


# --------------------------------------------------------------------------- the measurements (worker)


def _confidence(hp: pd.DataFrame, top: float = 0.2) -> dict[str, Any] | None:
    pcols = [c for c in hp.columns if c.startswith("p_")]
    if not pcols or len(hp) < 50:
        return None
    classes = [c[2:] for c in pcols]
    P = hp[pcols].to_numpy(dtype=float)
    y = hp["y_true"].astype(str).to_numpy()
    ok = np.isin(y, classes)
    P, y = P[ok], y[ok]
    conf = P.max(1)
    arg = np.asarray(classes)[P.argmax(1)]
    q = float(np.quantile(conf, 1 - top))
    sel = conf >= q
    n_top = int(sel.sum())
    k = int((arg[sel] != y[sel]).sum())
    floor = k / max(1, n_top)
    expected = float((1 - conf[sel]).mean())
    lo, hi = wilson(k, n_top)
    overconfident = bool(floor - expected >= 0.05 and expected < floor / 3)
    out = {"top_share": top, "conf_cut": q, "n_top": n_top, "errors_top": k, "floor": floor, "floor_ci": [lo, hi],
           "expected": expected, "overconfident": overconfident, "n": int(len(y)),
           "error_all": float((arg != y).mean()), "pred_unique": int(len(np.unique(hp["y_pred"].astype(str)))),
           "p_sd": float(P.std(axis=0).max())}
    try:
        from cleanlab.filter import find_label_issues

        idx = {c: i for i, c in enumerate(classes)}
        yi = np.array([idx[v] for v in y])
        if len(np.unique(yi)) == len(classes):
            iss = find_label_issues(yi, P, filter_by="prune_by_noise_rate", n_jobs=1)
            out["cleanlab"] = {"rate": float(iss.mean()), "n": int(iss.sum()), "of": int(len(iss)),
                               "method": "cleanlab.filter.find_label_issues(filter_by='prune_by_noise_rate') on the holdout's p̂"}
    except Exception as e:  # noqa: BLE001
        out["cleanlab"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return out


def _fit_gap(cfg: dict[str, Any], rec: dict[str, Any], hp: pd.DataFrame | None) -> dict[str, Any]:
    """The run's metric on its training rows and on its holdout, both through `predictor.evaluate` (the same
    threshold and sign), and the holdout's bootstrap SE (Vela's)."""
    from ..engine.pool import load_predictor
    from .findings import bootstrap_se

    p = load_predictor(cfg["predictor_path"])
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    df = pd.read_csv(cfg["csv_path"])
    label = cfg["label"]
    fold = np.asarray(sp["fold"])
    drop = [c for c in (cfg.get("drop_columns") or []) if c in df.columns and c != label]
    tr = df.loc[fold >= 0].drop(columns=drop)
    tr = tr[tr[label].notna()]
    ho = df.loc[np.asarray(sp["holdout"], dtype=int)].drop(columns=drop)
    ho = ho[ho[label].notna()]
    name = p.eval_metric.name
    s_tr = float(p.evaluate(tr, silent=True, auxiliary_metrics=False)[name])
    s_ho = float(p.evaluate(ho, silent=True, auxiliary_metrics=False)[name])
    se = bootstrap_se(rec, hp) if hp is not None and len(hp) >= 30 else None
    gap = s_tr - s_ho  # internal sign: higher is better, so a positive gap means training looks better
    return {"metric": name, "greater_is_better": bool(p.eval_metric.greater_is_better), "train": s_tr, "holdout": s_ho,
            "gap": gap, "se": se, "z": (gap / se) if se else None, "n_train": int(len(tr)), "n_holdout": int(len(ho)),
            "method": "predictor.evaluate on the training rows (folds ≥ 0) and on the holdout; scores in AutoGluon's "
                      "higher-is-better sign; SE = holdout bootstrap (B = 200)"}


def _slice_floors(cfg: dict[str, Any], hp: pd.DataFrame, problem_type: str) -> list[dict[str, Any]]:
    """Per listed weak slice: its base rate and the loss of predicting that base rate as a constant (log loss:
    H(π_S) per row as −log of the slice's own class shares; 0/1 error: 1 − max share; absolute error: |y − median|)."""
    p = Path(cfg["run_dir"]) / "slices.json"
    if not p.exists():
        return []
    res = json.loads(p.read_text())
    loss = (res.get("loss") or {}).get("name")
    by_row = hp.set_index("row")
    out = []
    for s in (res.get("slices") or [])[:5]:
        rows = [r for r in (s.get("rows") or []) if r in by_row.index]
        if len(rows) < 10:
            continue
        y = by_row.loc[rows, "y_true"]
        item: dict[str, Any] = {"id": s["id"], "label": s["label"], "n": len(rows), "mean": s["mean"], "ci": s["ci"],
                                "delta": s["delta"], "q": s["q"], "loss": loss}
        if problem_type in ("binary", "multiclass"):
            shares = y.astype(str).value_counts(normalize=True)
            item["base_rate"] = {str(k): float(v) for k, v in shares.items()}
            if loss == "log_loss":
                yy = y.astype(str).map(shares).to_numpy(dtype=float)
                item["constant_loss"] = float(-np.log(np.clip(yy, 1e-15, 1)).mean())
            elif loss in ("error", "zero_one"):
                item["constant_loss"] = float(1 - shares.max())
        elif loss == "absolute_error":
            yv = pd.to_numeric(y, errors="coerce")
            item["constant_loss"] = float((yv - yv.median()).abs().mean())
        if "constant_loss" in item:
            item["lower_mean"] = _lower_mean(s)
            item["fixable"] = bool(item["lower_mean"] > item["constant_loss"])
        out.append(item)
    return out


def _lower_mean(s: dict[str, Any]) -> float:
    """Lower bound of ℓ̄_S: ℓ̄_S minus the half-width of Δ's interval (an upper bound on ℓ̄_S's own half-width)."""
    half = (s["ci"][1] - s["ci"][0]) / 2
    return float(s["mean"] - half)


def measure(cfg: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """(Runs in a worker.) Everything the doctor measures itself. Each part fails alone."""
    from .findings import holdout_frame

    res = rec.get("result") or {}
    pt = res.get("problem_type")
    hp = holdout_frame(rec)
    out: dict[str, Any] = {"errors": []}
    try:
        out["fit"] = _fit_gap(cfg, rec, hp)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"fit gap: {type(e).__name__}: {e}"[:300])
    if hp is not None and pt in ("binary", "multiclass"):
        try:
            out["confidence"] = _confidence(hp)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"confidence: {type(e).__name__}: {e}"[:300])
    if hp is not None:
        try:
            out["slice_floors"] = _slice_floors(cfg, hp, pt)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"slices: {type(e).__name__}: {e}"[:300])
    return out


def twin_se(rec: dict[str, Any]) -> float | None:
    from .findings import bootstrap_se, holdout_frame

    hp = holdout_frame(rec)
    return bootstrap_se(rec, hp) if hp is not None and len(hp) >= 30 else None


def cache_key(rec: dict[str, Any]) -> str:
    run_dir = Path(rec["config"]["run_dir"])
    parts = []
    for f in ("holdout_predictions.csv", "slices.json"):
        p = run_dir / f
        parts.append(hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else "-")
    return PROCEDURE + ":" + ":".join(parts)


# --------------------------------------------------------------------------- the decision procedure


def _check(cid: str, status: str, reason: str, evidence: dict[str, Any] | None = None, severity: str | None = None,
           prescription: dict[str, Any] | None = None, offer: dict[str, Any] | None = None, formula: str | None = None,
           source: str | None = None) -> dict[str, Any]:
    return {"id": cid, "title": TITLES.get(cid, cid), "status": status, "severity": severity if status == "finding" else None,
            "reason": reason, "evidence": evidence or {}, "prescription": prescription, "offer": offer, "formula": formula,
            "source": source, "rule": RULES.get(cid)}


def _vela(findings: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    return next((f for f in findings if f.get("kind") == kind), None)


def _patch_rx(f: dict[str, Any], why: str | None = None) -> dict[str, Any]:
    return {"kind": "gym_patch", "title": f["patch"]["title"], "patch": f["patch"], "why": why or f.get("text")}


def diagnose(ctx: dict[str, Any]) -> dict[str, Any]:
    """The fixed procedure. `ctx` holds plain data only (see api.py), so the result depends on nothing else."""
    run = ctx["run"]
    rid = run["id"]
    res = run.get("result") or {}
    pt = res.get("problem_type")
    clf = pt in ("binary", "multiclass")
    vf = ctx.get("vela") or []
    m = ctx.get("measured") or {}
    fit = m.get("fit") or {}
    conf = m.get("confidence")
    checks: list[dict[str, Any]] = []

    # 1. leakage (Vela)
    lk = _vela(vf, "leakage")
    if lk:
        sus = lk["numbers"]["suspects"]
        sev = "critical" if any(s["reason"] == "near-perfect alone" for s in sus) else "major"
        checks.append(_check("leakage", "finding", lk["text"], {"suspects": sus}, sev, _patch_rx(lk), formula=lk.get("formula"), source="vela"))
    else:
        checks.append(_check("leakage", "clear", "No feature predicts the label alone (single-feature tree on the holdout below "
                             "0.97 for the top-3 by importance) and no identifier is used as a feature.", source="vela"))

    # 2. shift (F6 out-of-time gap + F15 adversarial AUC + Vela drift)
    sc = ctx.get("split_comparison") or {}
    sh = res.get("shift") or None
    oot = next((r for r in sc.get("runs") or [] if r.get("strategy") in ("time", "time-group") and r.get("optimism") is not None
                and (r.get("same_config") or r.get("this_run"))), None)
    ev: dict[str, Any] = {}
    sev_s: list[str] = []
    why: list[str] = []
    if oot:
        rand = next((r for r in sc["runs"] if r["run_id"] == sc.get("random_run_id")), None)
        d = float(oot["optimism"])
        se = ctx.get("oot_se")
        z = d / se if se else None
        ev["out_of_time"] = {"random_run": sc.get("random_run_id"), "random_score": rand and rand["score"], "oot_run": oot["run_id"],
                             "oot_score": oot["score"], "strategy": oot["strategy"], "delta": d, "se": se, "z": z, "metric": sc.get("metric")}
        if z is not None and z >= 3 and d >= 0.02:
            sev_s.append("critical")
        elif z is not None and z >= 2:
            sev_s.append("major")
        why.append(f"Random split {rand['score']:.4f} vs {oot['strategy']} split {oot['score']:.4f} ({sc.get('metric')}): Δ = {d:+.4f}"
                   + (f", z = Δ/√(SE₁² + SE₂²) = {z:.1f}" if z is not None else "") + " (F6, any shift)." if rand else "")
    if sh and sh.get("status") == "succeeded" and sh.get("auc") is not None:
        ev["adversarial"] = {k: sh.get(k) for k in ("key", "source", "auc", "ci", "verdict", "top_features", "n_reference", "n_new")}
        ev["adversarial"]["permutation_p"] = (sh.get("null") or {}).get("permutation_p")
        v = sh.get("verdict")
        if v == "strong":
            sev_s.append("critical")
        elif v == "moderate":
            sev_s.append("major")
        elif v == "slight":
            sev_s.append("minor")
        top = next((f["feature"] for f in sh.get("top_features") or [] if f.get("significant")), None)
        why.append(f"Adversarial AUC {sh['auc']:.3f} [{sh['ci'][0]:.3f}, {sh['ci'][1]:.3f}] on {sh.get('source')}: {v}"
                   + (f", {top} shifts most" if top else "") + " (F15, covariate shift only).")
    drift = _vela(vf, "drift")
    if drift:
        ev["batch_psi"] = {"feature": drift["numbers"]["feature"], "psi": drift["numbers"]["psi"]}
        sev_s.append("minor")
        why.append(drift["text"])
    has_dates = bool((ctx.get("split_options") or {}).get("date_columns"))
    offer_twin = None
    if not oot and has_dates and (run["config"].get("split") or {}).get("strategy", "random-stratified") == "random-stratified":
        col = ctx["split_options"]["date_columns"][0]["column"]
        offer_twin = {"kind": "split_twin", "title": "Train the Out-Of-Time Twin", "endpoint": f"/api/runs/{rid}/split-twin",
                      "body": {"split_spec": {"strategy": "time", "time_column": col}},
                      "why": f"This random-split run has a date column ({col}); an out-of-time twin measures what time costs."}
    offer_shift = None
    if not sh:
        offer_shift = {"kind": "run_check", "check": "shift", "title": "Run The Shift Check", "endpoint": f"/api/runs/{rid}/shift",
                       "body": {"source": "time"} if has_dates else {"source": "holdout"},
                       "why": "An adversarial classifier (F15) tells training rows from newer ones; about 30–70 s."}
    if sev_s:
        sev = max(sev_s, key=lambda s: SEV[s])
        rx = None
        if offer_twin:
            rx = offer_twin
        elif (run["config"].get("split") or {}).get("strategy", "random-stratified") == "random-stratified" and has_dates:
            col = ctx["split_options"]["date_columns"][0]["column"]
            rx = {"kind": "split_twin", "title": "Validate Out Of Time", "endpoint": f"/api/runs/{rid}/split-twin",
                  "body": {"split_spec": {"strategy": "time", "time_column": col}},
                  "why": "Choose and report the model on a holdout that looks like the future."}
        shift_actions = ctx.get("shift_actions") or []
        w = next((a for a in shift_actions if a.get("kind") == "weights-version"), None)
        dr = next((a for a in shift_actions if a.get("kind") == "gym-patch"), None)
        if oot and w:  # already validated out of time: weight training towards the new period
            rx = {"kind": "shift_weights", "title": "Importance-Weight Training", "endpoint": w["endpoint"], "why": w["why"]}
        elif not rx and dr:
            rx = {"kind": "gym_patch", "title": dr["title"], "patch": dr["patch"], "why": dr["why"]}
        elif not rx and w:
            rx = {"kind": "shift_weights", "title": "Importance-Weight Training", "endpoint": w["endpoint"], "why": w["why"]}
        checks.append(_check("shift", "finding", " ".join(x for x in why if x), ev, sev, rx, offer=offer_shift or None,
                             formula="z = (s_random − s_oot) / √(SE²_random + SE²_oot);  AUC_adv = P(ŝ(x_new) > ŝ(x_ref))", source="F6 + F15"))
    elif oot or sh:
        checks.append(_check("shift", "clear", " ".join(x for x in why if x) or "No shift detected.", ev, offer=offer_twin or offer_shift, source="F6 + F15"))
    else:
        checks.append(_check("shift", "missing", "Neither an out-of-time twin (F6) nor a shift check (F15) exists for this run.",
                             ev, offer=offer_twin or offer_shift, source="F6 + F15"))

    # 3. label noise (confidence floor + cleanlab; F14 annotations)
    ann = (res.get("annotations") or {}).get("summary") if isinstance(res.get("annotations"), dict) else None
    ann_rate = None
    if ann and ann.get("n_rows"):
        ch = (ann.get("consensus") or {}).get("changed_vs_given")
        if ch is not None:
            ann_rate = ch / ann["n_rows"]
    if not clf:
        checks.append(_check("label_noise", "n/a", f"A {pt} run: the error-floor and cleanlab checks need class probabilities."))
    elif not conf:
        checks.append(_check("label_noise", "n/a", "No holdout probabilities to check."))
    else:
        cl = conf.get("cleanlab") or {}
        rate = cl.get("rate")
        e = {"floor": conf["floor"], "floor_ci": conf["floor_ci"], "expected_top": conf["expected"], "n_top": conf["n_top"],
             "conf_cut": conf["conf_cut"], "overconfident": conf["overconfident"], "cleanlab_rate": rate,
             "cleanlab_n": cl.get("n"), "annotator_change_rate": ann_rate}
        lo = conf["floor_ci"][0]
        base = (f"Among the {conf['n_top']} most confident holdout predictions (max p̂ ≥ {conf['conf_cut']:.3f}) the error is "
                f"{conf['floor']:.3f} [{conf['floor_ci'][0]:.3f}, {conf['floor_ci'][1]:.3f}]; the model expects {conf['expected']:.3f} there. "
                + (f"cleanlab flags {rate:.1%} of holdout labels. " if rate is not None else ""))
        if lo >= 0.06 and not conf["overconfident"] and (rate or 0) >= 0.05:
            sev = "critical" if lo >= 0.12 else "major"
            checks.append(_check("label_noise", "finding", base + "Even where the model is most sure it disagrees with the label this often, "
                                 "and it has learnt to expect it: an error floor, the signature of noisy labels (or of an outcome that is random "
                                 "given these features: reviewing the flagged labels tells the two apart).", e, sev,
                                 {"kind": "inbox", "title": "Review Flagged Labels", "run_id": rid,
                                  "endpoint": f"/api/runs/{rid}/annotations", "why": "Review the labels cleanlab flags (the Ground-Truth Inbox "
                                  "style queue in Explain → Annotators, or an Autopilot audit) and save corrected labels as a dataset version."},
                                 formula="floor = #{ŷᵢ ≠ yᵢ : p̂ᵢ ≥ q₀.₈} / #{p̂ᵢ ≥ q₀.₈},  expected = mean(1 − p̂ᵢ) over the same rows",
                                 source="confidence floor + cleanlab"))
        elif ann_rate is not None and ann_rate >= 0.05:
            checks.append(_check("label_noise", "finding", f"The annotators' consensus (F14) changes {ann_rate:.1%} of the given labels.", e, "major",
                                 {"kind": "inbox", "title": "Save The Consensus Labels", "run_id": rid,
                                  "endpoint": f"/api/runs/{rid}/annotations/version", "why": "Use the CROWDLAB consensus as the labels."},
                                 source="F14"))
        else:
            r = base
            if conf["overconfident"] and lo >= 0.06:
                r += "The model does not expect these errors (overconfident): attributed to overfitting, not to the labels."
            elif (rate or 0) >= 0.05:
                r += "cleanlab's rate is high but the confident predictions are right: the disagreements sit where classes overlap (hard by nature)."
            else:
                r += "No error floor."
            checks.append(_check("label_noise", "clear", r, e, source="confidence floor + cleanlab"))

    # 4. overfit (train–holdout gap + overconfidence; Vela's val-vs-holdout optimism)
    of = _vela(vf, "overfit")
    if fit.get("z") is not None:
        z = fit["z"]
        e = {"metric": fit["metric"], "train": fit["train"], "holdout": fit["holdout"], "gap": fit["gap"], "se": fit["se"], "z": z,
             "overconfident": conf["overconfident"] if conf else None, "vela_val_gap": of and of["numbers"]["gap"]}
        txt = (f"{fit['metric']} on the training rows {fit['train']:.4f} vs the holdout {fit['holdout']:.4f}: gap {fit['gap']:.4f}, "
               f"z = {z:.1f} (SE {fit['se']:.4f}).")
        cond = z >= 3 and (not clf or (conf or {}).get("overconfident"))
        patch = _overfit_patch(ctx, None)
        if cond:
            sev = "critical" if z >= 8 else "major"
            checks.append(_check("overfit", "finding", txt + (" The model is overconfident on the holdout: its most confident "
                                 f"predictions err {conf['floor']:.3f} where it expects {conf['expected']:.3f}." if clf else ""), e, sev, patch,
                                 formula="z = (s_train − s_holdout) / SE_boot", source="fit gap"))
        elif of:
            checks.append(_check("overfit", "finding", of["text"] + " " + txt, e, "minor", _patch_rx(of), formula=of.get("formula"), source="vela"))
        else:
            checks.append(_check("overfit", "clear", txt + (" A gap with calibrated holdout confidence is what noisy or overlapping labels "
                                 "leave, not overfitting." if z >= 3 else ""), e, formula="z = (s_train − s_holdout) / SE_boot", source="fit gap"))
    elif of:
        checks.append(_check("overfit", "finding", of["text"], {"vela_val_gap": of["numbers"]["gap"]}, "minor", _patch_rx(of), source="vela"))
    else:
        checks.append(_check("overfit", "n/a", "The training score could not be computed for this run.", source="fit gap"))

    # 5. data-limited / underfit (F12 + F13)
    lc = res.get("learning_curve") or {}
    v = lc.get("verdict") if isinstance(lc, dict) else None
    al = ((res.get("active_learning") or {}).get("summary") or {}).get("verdict") if isinstance(res.get("active_learning"), dict) else None
    degenerate = bool(conf and (conf["pred_unique"] <= 1 or conf["p_sd"] < 1e-6))
    offer_lc = {"kind": "run_check", "check": "learning_curve", "title": "Run The Learning Curve", "endpoint": f"/api/runs/{rid}/learning-curve",
                "body": {}, "why": "9 refits of the run's own call on 10 / 25 / 50 % of its training rows (F12)."}
    if v and v.get("label"):
        sl = v.get("slope_per_doubling") or {}
        e = {"verdict": v["label"], "slope": sl.get("value"), "ci": sl.get("ci"), "min_gain": (v.get("evidence") or {}).get("min_gain"),
             "x2": (v.get("extrapolation") or {}).get("x2"), "active_learning": al and {k: al.get(k) for k in ("label", "gap", "ci", "wins", "n_seeds")}}
        head = (f"Learning curve: gain per doubling Δ₂ = {sl.get('value', 0):+.4f} [{sl['ci'][0]:+.4f}, {sl['ci'][1]:+.4f}] vs ε = "
                f"{e['min_gain']:.4f}: {v['label']}." if sl.get("ci") and e["min_gain"] is not None else f"Learning curve: {v['label']}.")
        if v["label"] == "data-limited":
            rx = {"kind": "labelling_queue", "title": "Label More Rows (Active Learning)", "run_id": rid, "endpoint": f"/api/runs/{rid}/active-learning",
                  "why": "More labelled rows are the lever; the queue picks the most informative ones."} if clf else \
                 {"kind": "info", "title": "Collect More Rows", "why": "More labelled rows of this kind are the lever."}
            if al and al.get("label") == "active-better":
                head += f" Active learning beats random at equal budget (d̄ {al['gap']:+.4f})."
            checks.append(_check("data_limited", "finding", head + (f" At 2N the score is projected {e['x2']['score']:.4f}." if e.get("x2") else ""),
                                 e, "major", rx, formula="Δ₂ = b(1 − 2^(−c)),  u(n) = A − b·(n/N)^(−c)", source="F12 + F13"))
            checks.append(_check("underfit", "clear", "The curve is still rising: not a capacity limit.", source="F12"))
        elif v["label"] == "saturated" and degenerate:
            checks.append(_check("data_limited", "clear", head + " But the model's predictions never change, so this says nothing about the data.", e, source="F12"))
            body = ctx.get("base") or {}
            from .findings import make_patch

            nxt = {"medium_quality": "good_quality", "good_quality": "high_quality"}.get(body.get("presets") or "medium_quality", "high_quality")
            checks.append(_check("underfit", "finding", f"Saturated with constant predictions ({conf['pred_unique']} distinct prediction(s) on the holdout): "
                                 "the model is too simple to use the features.", e, "major",
                                 {"kind": "gym_patch", "title": f"Escalate To {nxt}", "why": "A stronger preset fits more expressive models.",
                                  "patch": make_patch(body, "underfit", f"Escalate to {nxt}", [("presets", nxt, "stronger models"),
                                                                                                 ("fit_kwargs.hyperparameters", None, "the preset's own model zoo")])},
                                 source="F12 + holdout predictions"))
        elif v["label"] == "saturated":
            checks.append(_check("data_limited", "clear", head + " More rows of the same kind are not expected to help.", e, source="F12"))
            checks.append(_check("underfit", "clear", "The predictions vary; saturation is a data limit, not a capacity one.", source="F12"))
        else:
            checks.append(_check("data_limited", "no-evidence", head + " The interval contains ε: no evidence either way (more seeds narrow it).",
                                 e, offer={**offer_lc, "title": "Rerun With More Seeds", "body": {"seeds": 5}}, source="F12"))
            checks.append(_check("underfit", "n/a" if not degenerate else "clear", "Read with the learning curve.", source="F12"))
    else:
        checks.append(_check("data_limited", "missing" if run["config"].get("kind", "tabular") in (None, "tabular") else "n/a",
                             "No learning curve yet.", offer=offer_lc, source="F12"))
        if degenerate:
            checks.append(_check("underfit", "finding", f"The holdout predictions are constant ({conf['pred_unique']} distinct).",
                                 {"pred_unique": conf["pred_unique"]}, "major", None, offer=offer_lc, source="holdout predictions"))
        else:
            checks.append(_check("underfit", "clear", "The predictions vary across the holdout.", source="holdout predictions"))

    # 6. latency (F3)
    budget = ctx.get("latency_budget_ms")
    lat = ctx.get("latency")
    if not budget:
        checks.append(_check("latency", "n/a", "No latency budget: pass latency_ms (or set infer_limit on the run).", source="F3"))
    elif not lat:
        checks.append(_check("latency", "missing", f"Budget {budget:g} ms per row; the served latency has not been measured.",
                             {"budget_ms": budget}, offer={"kind": "run_check", "check": "latency", "title": "Measure Latency",
                                                           "endpoint": f"/api/runs/{rid}/latency", "method": "GET",
                                                           "why": "One-row p95 of every model as served (F3), in a worker."}, source="F3"))
    else:
        rows = lat.get("models") or []
        best = next((r for r in rows if r.get("best")), None)
        if not best:
            checks.append(_check("latency", "n/a", "The latency profile has no best model.", source="F3"))
        else:
            p95 = best["served_ms_p95"]
            gib = lat.get("natural_greater_is_better", True)
            ok = [r for r in rows if r["served_ms_p95"] <= budget and r.get("score_rank") is not None]
            pick = max(ok, key=lambda r: r["score_rank"] if gib else -r["score_rank"]) if ok else None
            e = {"budget_ms": budget, "served_p95_ms": p95, "model": best["model"], "n_models": len(rows),
                 "fastest": min(rows, key=lambda r: r["served_ms_p95"])["model"],
                 "best_within_budget": pick and {"model": pick["model"], "served_p95_ms": pick["served_ms_p95"],
                                                 "score_val": pick["score_val_natural"], "score_test": pick.get("score_test_natural"),
                                                 "best_score_test": best.get("score_test_natural")}}
            if p95 > budget:
                sev = "critical" if p95 > 2 * budget else "major"
                if pick:
                    rx = {"kind": "register", "title": f"Serve {pick['model']}", "endpoint": f"/api/runs/{rid}/register",
                          "body": {"serve_model": pick["model"], "prune_pipeline": True, "clone_for_deployment": True},
                          "why": f"{pick['model']} is served at p95 {pick['served_ms_p95']:.1f} ms ≤ {budget:g} ms "
                                 f"(holdout {pick.get('score_test_natural') or 0:.4f} vs {best.get('score_test_natural') or 0:.4f})."}
                else:
                    from .findings import make_patch

                    body = ctx.get("base") or {}
                    rx = {"kind": "gym_patch", "title": "Refit For A One-Row Budget", "why": "No model meets the budget: AutoGluon's infer_limit prunes the ensemble.",
                          "patch": make_patch(body, "latency", "Refit for the latency budget", [
                              ("fit_kwargs.refit_full", True, "one model per family"), ("fit_kwargs.set_best_to_refit_full", True, "serve the refit"),
                              ("fit_kwargs.infer_limit", round(budget / 1000 * 0.6, 5), "60 % of the budget, seconds per row"),
                              ("fit_kwargs.infer_limit_batch_size", 1, "one row per call")])}
                checks.append(_check("latency", "finding", f"{best['model']} is served at p95 {p95:.1f} ms per row against a budget of {budget:g} ms"
                                     + (f"; {pick['model']} fits at {pick['served_ms_p95']:.1f} ms." if pick else "; no model of the run fits."),
                                     e, sev, rx, formula="τ₉₅ = Q₀.₉₅{T₁…Tₙ}, one row per call, as served", source="F3"))
            else:
                checks.append(_check("latency", "clear", f"{best['model']} is served at p95 {p95:.1f} ms ≤ {budget:g} ms.", e, source="F3"))

    # 7. weak slices (F16, Δ read next to the slice's base rate)
    sl = res.get("slices")
    floors = m.get("slice_floors") or []
    if not sl:
        checks.append(_check("weak_slice", "missing", "No weak-slice search yet.", offer={"kind": "run_check", "check": "slices",
                             "title": "Search Weak Slices", "endpoint": f"/api/runs/{rid}/slices", "body": {},
                             "why": "Every feature value and pair tested against the rest (F16); about a second."}, source="F16"))
    elif not sl.get("n_weak"):
        checks.append(_check("weak_slice", "clear", f"No weak slice among {sl.get('n_tested')} tested (BH q ≤ {sl.get('alpha')}).", {"n_tested": sl.get("n_tested")}, source="F16"))
    else:
        fx = [f for f in floors if f.get("fixable")]
        top = fx[0] if fx else (floors[0] if floors else None)
        t0 = sl["top"][0]
        e = {"n_weak": sl["n_weak"], "n_tested": sl["n_tested"], "top": t0, "floors": floors}
        if top and "constant_loss" in top:
            txt = (f"{top['label']}: ℓ̄_S {top['mean']:.3f} (Δ {top['delta']:+.3f} vs the rest, q {top['q']:.1e}); predicting the slice's own "
                   f"base rate would give {top['constant_loss']:.3f}. " + ("The model does worse than that constant here: fixable."
                                                                          if top.get("fixable") else "The model is no worse than that constant: hard by nature."))
        else:
            txt = f"{t0['label']}: Δ {t0['delta']:+.3f} [{t0['ci'][0]:+.3f}, {t0['ci'][1]:+.3f}], q {t0['q']:.1e}."
        target = top or t0
        rx = {"kind": "labelling_queue", "title": "Label More Rows In This Slice", "run_id": rid, "slice_id": target["id"],
              "endpoint": f"/api/runs/{rid}/active-learning", "body": {"slice_id": target["id"]},
              "why": "The F13 queue, pre-filtered to the slice."} if clf else \
             {"kind": "open_slice", "title": "Open The Slice", "run_id": rid, "slice_id": target["id"], "why": "Rows, SHAP inside vs outside."}
        checks.append(_check("weak_slice", "finding", txt, e, "major" if fx else "minor", rx,
                             formula="Δ = ℓ̄_S − ℓ̄_C;  fixable ⇔ lower(ℓ̄_S) > ℓ(base rate of S)", source="F16"))

    # 8–10. Vela's remaining checks (budget, imbalance, weak class)
    for kind, sev_of in (("budget", lambda f: "major" if f["numbers"].get("skipped") else "minor"),
                         ("imbalance", lambda f: "minor"), ("weak_class", lambda f: "minor")):
        f = _vela(vf, kind)
        if f:
            checks.append(_check(kind, "finding", f["text"], f["numbers"], sev_of(f), _patch_rx(f), formula=f.get("formula"), source="vela"))
        else:
            checks.append(_check(kind, "clear", "Vela's check did not fire.", source="vela"))

    found = [c for c in checks if c["status"] == "finding"]
    found.sort(key=lambda c: (-SEV[c["severity"]], PRECEDENCE.index(c["id"]) if c["id"] in PRECEDENCE else 99))
    primary = found[0] if found else None
    offers = [c["offer"] for c in checks if c.get("offer") and c["status"] in ("missing", "no-evidence", "clear")]
    if primary:
        headline = f"{primary['title']} ({primary['severity']}): {primary['reason']}"
    else:
        headline = "No limiting factor found by the checks that ran." + (f" {len(offers)} check(s) can still be run." if offers else "")
    return {"schema": SCHEMA, "procedure": PROCEDURE, "run_id": rid, "computed_at": now(), "problem_type": pt,
            "primary": primary, "secondary": found[1:], "checks": checks, "offers": offers, "headline": headline,
            "precedence": PRECEDENCE, "severity_order": ["critical", "major", "minor"], "rules": RULES,
            "inputs": {"latency_budget_ms": budget, "measured_errors": m.get("errors") or [], "vela_errors": ctx.get("vela_errors") or []}}


def _overfit_patch(ctx: dict[str, Any], of: dict[str, Any] | None) -> dict[str, Any]:
    if of:
        return _patch_rx(of)
    from .findings import _with_params, _zoo, make_patch

    base = ctx.get("base") or {}
    run = ctx["run"]
    hpz = _zoo((base.get("fit_kwargs") or {}).get("hyperparameters")
               or (((run.get("result") or {}).get("resolved") or {}).get("fit_kwargs") or {}).get("hyperparameters"))
    reg = {"GBM": {"min_data_in_leaf": 40, "lambda_l2": 1.0}, "CAT": {"l2_leaf_reg": 10.0}, "XGB": {"reg_lambda": 10.0, "min_child_weight": 4.0},
           "RF": {"min_samples_leaf": 5}, "XT": {"min_samples_leaf": 5}, "NN_TORCH": {"weight_decay": 1e-4, "dropout_prob": 0.3},
           "KNN": {"n_neighbors": 25}}
    for fam in [f for f in reg if f in hpz]:
        v = hpz[fam]
        cfgs = v if isinstance(v, list) else [v]
        for c in cfgs:  # an explicit capacity setting (no early stop, deep leaves) is replaced by the regularised one
            if isinstance(c, dict):
                for k in ("ag.early_stop", "min_data_in_leaf", "num_leaves", "learning_rate", "num_boost_round", "n_neighbors",
                          "min_samples_leaf", "max_depth"):
                    c.pop(k, None)
        hpz = _with_params(hpz, fam, reg[fam])
    fams = [f for f in reg if f in hpz]
    patch = make_patch(base, "overfit", "Regularise " + ", ".join(fams) if fams else "Bag 5 folds",
                       [("fit_kwargs.hyperparameters", hpz, "stronger regularisation: " + "; ".join(
                           f"{f}: " + ", ".join(f"{k}={v}" for k, v in reg[f].items()) for f in fams))] if fams else
                       [("fit_kwargs.num_bag_folds", 5, "choose models on out-of-fold predictions")])
    return {"kind": "gym_patch", "title": patch["title"], "patch": patch, "why": "Constrain capacity so the model stops memorising."}
