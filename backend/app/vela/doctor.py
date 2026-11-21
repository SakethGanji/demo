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
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import frames
from ..engine.store import now
from ..stats import wilson

SCHEMA = "doctor/1"
PROCEDURE = "doctor-procedure/1"
SEV = {"critical": 3, "major": 2, "minor": 1}
# D3: re-tuned so the primary is the most actionable finding: what invalidates the score first, then a decision the
# bank must confirm (the cost ratio), then what limits the model; "at the data's limit" ranks below every real problem.
PRECEDENCE = ["leakage", "shift", "label_noise", "overfit", "cost_tradeoff", "data_limited", "underfit", "latency",
              "weak_slice", "budget", "ceiling", "imbalance", "weak_class"]
TITLES = {"leakage": "Leakage", "shift": "Shift between training and deployment", "label_noise": "Label noise",
          "overfit": "Overfitting", "data_limited": "Data-limited", "underfit": "Underfitting (the model is too simple)",
          "latency": "Latency budget", "weak_slice": "Weak slice", "budget": "Time budget", "imbalance": "Imbalanced label",
          "weak_class": "Weak class", "cost_tradeoff": "Cost trade-off", "ceiling": "At the data's limit"}
# The fixed thresholds, also returned with every diagnosis so the page can show them.
RULES = {
    "leakage": "critical: one feature holds ≥ 50 % of the permutation importance and ≥ 3 × the next AND the Data Doctor or Smart "
               "Intake names it a leak suspect by its name or values (filled after the outcome; strength alone is not a hint) and nobody answered it is "
               "known at decision time; or one feature alone has AUC ≥ 0.97 (D1 single_auc; accuracy / R² ≥ 0.97 for other "
               "tasks); major: an identifier used as a feature. Prescription: an ablation, the same body without the column(s), "
               "same holdout, as a child run; Δ with a paired-bootstrap CI",
    "cost_tradeoff": "the run optimises a business cost (binary c_FP, c_FN): always a finding (major) that replaces weak class and "
                     "imbalance; decline rate, catch rate and cost per 1,000 at the served threshold and at the 3:1, 5:1 and 10:1 "
                     "Bayes thresholds t = 1/(1 + r). Prescription: confirm the ratio",
    "ceiling": "minor, all four: the learning curve is flat (F12 saturated, or the quick model's next doubling has a 95 % upper "
               "bound < ε), the top models agree (the ladder's single GBM within the interval of the best), the train–holdout "
               "gap is within noise (no overfit finding), and no fixable weak slice; ranks below every real problem",
    "shift": "prescription: importance weights when their ESS ≥ 20 % of the rows, else drop the top shifting column, else an out-of-time twin; "
             "critical: out-of-time optimism z ≥ 3 and Δ ≥ 0.02, or adversarial verdict strong; major: z ≥ 2, or verdict moderate; "
             "minor: verdict slight, or a batch PSI > 0.25; F18 monitoring of a registered version: a performance alert on the latest "
             "labelled batch → critical, a drift alert (PSI / adversarial AUC / score PSI) on the latest batch → major, and the "
             "prescription is then the retrain proposal",
    "label_noise": "classification: error floor (top-20 % confidence) Wilson lower bound ≥ 0.06, not overconfident, cleanlab rate ≥ 0.05 → "
                   "major (critical if the lower bound ≥ 0.12); or the annotators' consensus changes ≥ 5 % of labels (F14) → major; "
                   "or retraining without cleanlab's flagged training rows gains beyond noise (95 % CI above 0): the full-model clean_labels "
                   "experiment → major; the quick model only → no evidence (dropping hard rows lifts a quick model on clean labels too)",
    "overfit": "z = (train − holdout)/SE ≥ 3 and, for classification, overconfident (floor − expected ≥ 0.05 and expected < floor/3) → "
               "major (critical if z ≥ 8); Vela's validation-vs-holdout optimism alone → minor",
    "data_limited": "learning curve verdict data-limited (CI of the gain per doubling above ε) → major; "
                    "+ active learning beats random → the labelling queue is the prescription",
    "underfit": "learning curve saturated while the holdout predictions are constant → major",
    "latency": "served one-row p95 of the best model > budget → major (critical if > 2 × budget)",
    "weak_slice": "a weak slice (F16, BH q ≤ α) whose loss is above its own base-rate constant's (CI lower bound) → major; otherwise minor",
    "budget": "Vela: the fit used ≥ 95 % of τ or skipped models → minor (major if models were skipped and the fit ran > 10 % over τ)",
    "imbalance": "Vela: minority share < 20 % with an uncalibrated threshold → minor (n/a on a cost run: the cost trade-off replaces it)",
    "weak_class": "Vela: a class's recall ≥ 0.05 below the macro mean → minor (n/a on a cost run: the cost trade-off replaces it)",
}


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
    lo, hi = wilson(k, n_top, 1.96, empty=(0.0, 1.0))
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
    df = frames.read(frames.resolve(cfg))
    label = cfg["label"]
    fold = np.asarray(sp["fold"])
    drop = [c for c in (cfg.get("drop_columns") or []) if c in df.columns and c != label]
    tr = df.loc[fold >= 0].drop(columns=drop)  # a boolean mask over read positions
    tr = tr[tr[label].notna()]
    # P11: holdout entries are read positions; a Parquet frame's index is __row_id (with gaps after an edit), so .iloc
    ho = df.iloc[np.asarray(sp["holdout"], dtype=int)].drop(columns=drop)
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

    # 1. leakage (D3: dominance + a D1 / intake hint, or single-column AUC ≥ 0.97; Vela's identifiers and near-perfect trees)
    checks.append(_leakage(ctx, vf, rid))

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
    # F18: production monitoring. A drift alert on the latest batch (PSI / adversarial AUC / score PSI) is covariate shift
    # seen in production (major); a performance alert on the latest labelled batch is the model failing on new data
    # (critical: p(y | x) may have changed, which no covariate check sees). Either prescribes the retrain proposal.
    mon_rx = None
    mons = ctx.get("monitoring") or []
    for mv in mons:
        cov = [a for a in mv.get("active") or [] if a["kind"] in ("covariate", "score") and not a.get("explained_by")]
        perf = [a for a in mv.get("active") or [] if a["kind"] == "performance" and not a.get("explained_by")]
        if not (cov or perf):
            continue
        ev.setdefault("monitoring", []).append({"version_id": mv["version_id"], "label": mv["label"], "stage": mv["stage"],
                                                "alerts": [{k: a.get(k) for k in ("signal", "kind", "value", "threshold", "text", "period", "features", "lag_days")}
                                                           for a in cov + perf],
                                                "first_covariate": mv.get("first_covariate"), "first_performance": mv.get("first_performance")})
        if perf:
            sev_s.append("critical")
            why.append(f"Monitoring of {mv['label']} ({mv['stage']}): {perf[0]['text']} on {perf[0].get('period')}'s labelled rows"
                       " (F18; performance on new data, so p(y | x) may have changed too).")
        if cov:
            sev_s.append("major")
            why.append(f"Monitoring of {mv['label']}: {cov[0]['text']} on the {cov[0].get('period')} batch (F18, covariate drift in production).")
        if mon_rx is None:
            mon_rx = {"kind": "retrain", "title": "Retrain On Recent Data", "endpoint": mv["retrain_endpoint"],
                      "version_id": mv["version_id"],
                      "why": f"{mv['label']} has drifted in production: the retrain proposal trains the champion's config on the "
                             "latest labelled window; it launches on a click, and the new model goes through review."}
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
        w_ok = bool(w and (w.get("ess_share") or 0) >= 0.2)  # weights with an ESS under 20 % of the rows rest on a few rows
        if oot and w_ok:  # the out-of-time gap is already measured: weight training towards the new period
            rx = {"kind": "shift_weights", "title": "Importance-Weight Training", "endpoint": w["endpoint"], "why": w["why"]}
        elif (oot or not rx) and dr:
            rx = {"kind": "gym_patch", "title": dr["title"], "patch": dr["patch"], "why": dr["why"] + (
                f" (Importance weights would keep an effective {w.get('ess_share', 0):.1%} of the rows, too few.)" if w and not w_ok else "")}
        elif not rx and w:
            rx = {"kind": "shift_weights", "title": "Importance-Weight Training", "endpoint": w["endpoint"], "why": w["why"]}
        if mon_rx:  # drift seen in production: retraining on the recent window answers it more directly than a split twin
            rx = mon_rx
        checks.append(_check("shift", "finding", " ".join(x for x in why if x), ev, sev, rx, offer=offer_shift or None,
                             formula="z = (s_random − s_oot) / √(SE²_random + SE²_oot);  AUC_adv = P(ŝ(x_new) > ŝ(x_ref))", source="F6 + F15" + (" + F18" if mons else "")))
    elif oot or sh or mons:
        if mons and not why:
            why.append("Monitoring: " + "; ".join(f"{m['label']} {m['batches']} batches to {m['latest']}, no active alert" for m in mons) + " (F18).")
        checks.append(_check("shift", "clear", " ".join(x for x in why if x) or "No shift detected.", ev, offer=offer_twin or offer_shift, source="F6 + F15 + F18"))
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
        elif (lab := _label_gain(ctx, rid)) is not None and lab["fires"]:
            # D5: noise in the training labels only (the holdout's confident predictions are right, so the floor stays
            # low) shows as a measured gain from dropping cleanlab's flagged training rows; the full-ensemble
            # experiment, once it ran, decides over the quick model.
            e.update(clean=lab["evidence"])
            # F-A1: a quick model refit without the hardest rows gains on clean tables too (telco: +0.011 AUC with no
            # planted noise), so the quick model alone is a hint (minor); the full-model experiment beyond noise is major.
            quick = lab["source"] == "quick model"
            if quick:  # A2 M3: a quick model refit without its hardest rows gains on clean tables too: a hint, never a finding
                txt_ = base + lab["text"].replace(": the noise sits in the training labels. The clean-labels experiment measures it with the full model.",
                                                  ". Dropping the hardest rows lifts a quick model on clean labels too, so this is no "
                                                  "evidence of label noise by itself; the clean-labels experiment measures it with the full model.")
                checks.append(_check("label_noise", "no-evidence", txt_, e, offer=lab["rx"],
                                     formula="Δ = score(fit without the flagged training rows) − score(fit with them), same holdout, paired bootstrap",
                                     source="cleanlab on the training rows + quick model"))
            else:
                checks.append(_check("label_noise", "finding", lab["text"], e, "major", lab["rx"],
                                     formula="Δ = score(fit without the flagged training rows) − score(fit with them), same holdout, paired bootstrap",
                                     source="cleanlab on the training rows + " + lab["source"]))
        else:
            r = base
            if (lab := _label_gain(ctx, rid)) is not None:
                r += lab["text"] + " "
            if conf["overconfident"] and lo >= 0.06:
                r += "The model does not expect these errors (overconfident): attributed to overfitting, not to the labels."
            elif (rate or 0) >= 0.05:
                r += ("cleanlab's rate is high but the confident predictions are right: the flagged rows sit where the model is unsure "
                      "(classes that overlap, or flipped labels it cannot tell from them).")
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
        patch = _overfit_patch(ctx)
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
    g = ctx.get("gaps") or {}
    cost_run = bool(g.get("cost"))
    # A2 m2: a fit a few seconds over its budget is not a major problem: major only when models were skipped and the fit
    # ran more than 10 % over τ
    for kind, sev_of in (("budget", lambda f: "major" if f["numbers"].get("skipped") and (f["numbers"].get("used") or 0) > 1.10 else "minor"),
                         ("imbalance", lambda f: "minor"), ("weak_class", lambda f: "minor")):
        f = _vela(vf, kind)
        if cost_run and kind in ("imbalance", "weak_class"):
            checks.append(_check(kind, "n/a", "A cost run: the decision threshold comes from the costs, so a class's recall or the "
                                 "minority share is read in the cost trade-off" + (f" (Vela: {f['text']})" if f else "") + ".", source="vela"))
            continue
        if f:
            checks.append(_check(kind, "finding", f["text"], f["numbers"], sev_of(f), _patch_rx(f), formula=f.get("formula"), source="vela"))
        else:
            checks.append(_check(kind, "clear", "Vela's check did not fire.", source="vela"))

    # 11. cost trade-off (D3) and 12. at the data's limit (D3)
    checks.append(_cost_tradeoff(g, rid))
    improve = _improve(ctx, checks)
    checks.append(_ceiling(ctx, checks, improve))
    missing = _missing(ctx, checks)

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
            "missing_info": missing, "improve": improve, "errors_view": _errors_view(g),
            "inputs": {"latency_budget_ms": budget, "measured_errors": m.get("errors") or [], "vela_errors": ctx.get("vela_errors") or []}}


def next_steps(rec: dict[str, Any], intake: dict[str, Any] | None, version: dict[str, Any] | None) -> list[dict[str, Any]]:
    """G11: what else applies to this run, beyond what limits it (never a finding, never the primary): segmented models,
    reject inference, fair lending and recourse, from Smart Intake's roles and suggestions for the run's table. Each item
    has a reason and the in-app link that opens it pre-filled."""
    from urllib.parse import urlencode

    res = rec.get("result") or {}
    binary = res.get("problem_type") == "binary"
    ds, label, rid = rec["dataset_id"], rec.get("label"), rec["id"]
    sug = {x["id"]: x for x in (intake or {}).get("suggestions") or []}
    roles = (intake or {}).get("roles") or {}
    out: list[dict[str, Any]] = []
    seg = sug.get("segmented")
    if seg and (seg["prefill"].get("label") in (None, label)):
        p = seg["prefill"]
        out.append({"id": "segmented", "title": "Try one model per segment", "why": seg["reason"],
                    "link": "segmented?" + urlencode({"dataset": ds, "label": p.get("label") or label or "", "segment": p.get("segment") or ""})})
    ri = sug.get("reject_inference")
    if binary and ri:
        p = ri["prefill"]
        out.append({"id": "reject_inference", "title": "Check reject inference", "why": ri["reason"],
                    "link": "reject-inference?" + urlencode({"dataset": ds, "label": p.get("label") or label or "", "decision": p.get("decision") or ""})})
    if binary and (roles.get("surname") or sug.get("fair_lending")):
        sur = (roles.get("surname") or [None])[0]
        zp = (roles.get("zip") or [None])[0]
        out.append({"id": "fair_lending", "title": "Run the fair-lending check",
                    "why": f"'{sur}'{f' and {zp!r}' if zp else ''} give each applicant's estimated group (BISG); the adverse impact ratio of "
                           "this model's decisions by group, against the four-fifths rule." if sur else "The table has the inputs for BISG proxy groups.",
                    "link": f"models?run={rid}&tab=Explain&sub=Fairness"})
    if binary:
        out.append({"id": "recourse", "title": "See what would change a decline",
                    "why": "For a declined applicant: the reason codes and the smallest realistic changes that flip the decision, "
                           "re-scored by the served model." + ("" if version else " It reads a registered version: register this run first."),
                    "link": f"registry?version={version['id']}&tab=Try%20It" if version else f"registry?register={rid}"})
    return out


def _overfit_patch(ctx: dict[str, Any]) -> dict[str, Any]:
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


# --------------------------------------------------------------------------- D3 · Model Doctor 2


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%" if abs(x) >= 0.1 else f"{100 * x:.1f}%"


ERROR_METRICS = ("RMSE", "MAE", "root_mean_squared_error", "mean_absolute_error", "log loss", "cost / 1,000")


def _sv(v: float, metric: str | None = None, ref: float | None = None, sign: bool = True) -> str:
    """A measured number at a sensible precision: money-scale errors (RMSE in dollars) to whole units with separators,
    small scores to 4 decimals."""
    big = abs(ref if ref is not None else v) >= 100
    body = f"{v:+,.0f}" if big else f"{v:+.4f}"
    return body if sign else body.lstrip("+")


def _better(metric: str | None) -> str:
    return " (lower error is better; the sign shows the improvement)" if metric in ERROR_METRICS else ""


def _cis(ci: list[float] | None, ref: float | None = None) -> str:
    return f"[{_sv(ci[0], ref=ref)}, {_sv(ci[1], ref=ref)}]" if ci else ""


def _ci(ci: list[float] | None, d: int = 4) -> str:
    return f"[{ci[0]:+.{d}f}, {ci[1]:+.{d}f}]" if ci else ""


def _exp(ctx: dict[str, Any], kind: str, columns: list[str] | None = None) -> dict[str, Any] | None:
    for e in ctx.get("experiments") or []:
        if e.get("kind") == kind and (columns is None or sorted(e.get("columns") or []) == sorted(columns)):
            return e
    return None


# A2 M5: the contradiction rule fires when the model's error is below this share of the look-alike (nearest-neighbour)
# estimate's lower 95 % bound. The 1-NN estimate is biased upward in many dimensions: on clean benchmark tables the
# model reached 0.61 × (credit_clean, A2) of it, so 0.6 fired on clean data; 0.4 leaves a margin under every clean
# table measured (F-A2.md lists them).
R_STAR_RATIO = 0.4
# A2 C1: importance concentrated in one or two features with no decision-time answer is an open question, LLM or not
CONC_TOP1, CONC_TOP2, CONC_SECOND = 0.45, 0.75, 0.25
# A2 m1: a one-column drop "stands out" (the pattern of a column recorded after the outcome) when it is ≥ 0.02 on the
# metric's scale and ≥ 2 × the next largest one-column drop measured; smaller drops say "the score uses it": confirm timing
BIG_DROP, DROP_RATIO = 0.02, 2.0


TIMING_TOP_K, TIMING_MIN_SHARE = 3, 0.05


def dominant_unanswered(top: list[dict[str, Any]], known: set[str] | list[str] = ()) -> list[str]:
    """The A2 C1 concentration rule: one feature ≥ CONC_TOP1 of the importance, or two ≥ CONC_TOP2 together (the second
    ≥ CONC_SECOND), with no decision-time answer."""
    conc: list[str] = []
    if top and top[0]["share"] >= CONC_TOP1:
        conc.append(top[0]["feature"])
    if len(top) > 1 and top[0]["share"] + top[1]["share"] >= CONC_TOP2 and top[1]["share"] >= CONC_SECOND:
        conc += [t["feature"] for t in top[:2] if t["feature"] not in conc]
    return [f for f in conc if f not in set(known)]


def timing_questions(top: list[dict[str, Any]], known: set[str] | list[str] = ()) -> list[str]:
    """A3 N1: the open timing questions of a run: its top TIMING_TOP_K features by permutation importance (each
    ≥ TIMING_MIN_SHARE) that nobody has answered "known at decision time", whatever their share. Shared with the
    label check's reference model (missing.py), which leaves them out."""
    kn = set(known)
    return [t["feature"] for t in (top or [])[:TIMING_TOP_K] if (t.get("share") or 0) >= TIMING_MIN_SHARE and t["feature"] not in kn]


def _ablations(ctx: dict[str, Any], cols: list[str]) -> list[dict[str, Any]]:
    """Every finished one-column ablation of the run (any column), largest drop first."""
    out = []
    for e in ctx.get("experiments") or []:
        r = e.get("result") or {}
        if e.get("kind") == "ablate" and e.get("status") == "succeeded" and len(e.get("columns") or []) == 1 and r.get("delta") is not None:
            out.append({"column": e["columns"][0], "run_id": e.get("run_id"), "metric": r.get("metric"), "with": r.get("parent"),
                        "without": r.get("child"), "delta": r["delta"], "ci": r.get("ci"), "rows": r.get("rows")})
    out.sort(key=lambda a: a["delta"])
    return out


def drop_class(a: dict[str, Any], others: list[dict[str, Any]]) -> str:
    """'stands out' | 'uses' | 'barely': how much the score leans on one column, against the other one-column drops."""
    if not a.get("ci") or a["ci"][1] >= 0:
        return "barely"
    d = -a["delta"]
    nxt = max([-o["delta"] for o in others if o["column"] != a["column"] and o["delta"] < 0] or [0.0])
    big = d >= (BIG_DROP if abs(a.get("with") or 0) < 2 else BIG_DROP * abs(a.get("with") or 1))
    return "stands out" if big and d >= DROP_RATIO * nxt else "uses"


def _leakage(ctx: dict[str, Any], vf: list[dict[str, Any]], rid: str) -> dict[str, Any]:
    g = ctx.get("gaps") or {}
    lk = g.get("leak") or {}
    known = set(ctx.get("known") or [])
    # A hint is what the name or the values say (Smart Intake's leak rules), or a Data Doctor suspect nobody has answered
    # yet: until someone confirms it is known at decision time, a very strong column is an open leak question.
    d1 = {s["column"]: {**s, "open": True} for s in g.get("d1_leak") or [] if s["column"] not in known}
    sus: list[dict[str, Any]] = []
    f0 = lk.get("feature")
    shares = {t["feature"]: t["share"] for t in lk.get("top") or []}
    sauc = lk.get("single_auc") or {}
    if f0:
        dominant = lk["share"] >= 0.5 and (lk.get("ratio") is None or lk["ratio"] >= 3)
        if dominant and f0 in d1:
            sus.append({"feature": f0, "reason": "dominant + hint", "share": lk["share"], "ratio": lk.get("ratio"),
                        "hint": "; ".join(d1[f0].get("reasons") or []) or "Data Doctor leakage suspect",
                        "single_auc": sauc.get(f0) or d1[f0].get("auc")})
    for f, a in sauc.items():
        if a >= 0.97 and f not in known and not any(s["feature"] == f for s in sus):
            sus.append({"feature": f, "reason": "near-perfect alone", "single_auc": a, "share": shares.get(f)})
    # an unanswered Data Doctor suspect among the model's top features: the score may rest on it
    for f, s_ in d1.items():
        if any(x["feature"] == f for x in sus) or f not in shares or shares[f] < 0.10:
            continue
        sus.append({"feature": f, "reason": "open suspect", "share": shares[f], "single_auc": s_.get("auc"),
                    "hint": "; ".join(s_.get("reasons") or []), "basis": s_.get("basis"), "confidence": s_.get("confidence")})
    v = _vela(vf, "leakage")
    ids_ = {s["feature"] for s in (v["numbers"]["suspects"] if v else []) if s["reason"] == "identifier"}
    for s in (v["numbers"]["suspects"] if v else []):
        if s["feature"] in known or any(x["feature"] == s["feature"] for x in sus):
            continue
        if s["reason"] in ("identifier", "near-perfect alone"):
            sus.append({**s})
    # A2 C1: the score rests on one or two columns nobody has said are known at decision time. Without a language
    # model nothing else would ask, so the Doctor asks: an open question (a strong real signal looks the same), tested by
    # a one-column run without each.
    top = lk.get("top") or []
    dom = dominant_unanswered(top, known)
    # A3 N1: a moderate leak need not dominate (visit_index at 34 %, telco_planted's at 40 % and 25 %), so offline the
    # model's top features with no decision-time answer are each an open timing question, whatever their share (minor
    # alone: a strong real signal looks the same); the review ablates them one column at a time.
    for f in timing_questions(top, known):
        if f in ids_ or any(x["feature"] == f for x in sus):
            continue
        sus.append({"feature": f, "reason": "concentrated", "share": shares.get(f), "single_auc": sauc.get(f),
                    "hint": "no decision-time answer", "basis": "strength only", "dominant": f in dom})
    # the model errs less than the table's own look-alike estimate allows: a leak or train/holdout overlap
    tw = g.get("twins") or {}
    contra = None
    if tw.get("lower_ci") and tw.get("model_error") is not None and tw["lower_ci"][0] > 0:
        if tw["model_error"] < R_STAR_RATIO * tw["lower_ci"][0]:
            contra = {"model_error": tw["model_error"], "lower": tw["lower"], "lower_ci": tw["lower_ci"],
                      "ratio": tw["model_error"] / tw["lower"], "threshold": R_STAR_RATIO}
    ov = g.get("overlap") or {}
    sc_ = ov.get("score") or {}
    lift_ = (sc_["seen"]["auc"] - sc_["new"]["auc"]) if sc_.get("seen") and sc_.get("new") else None
    # A2 C1: overlap of ≥ 1 % of holdout rows is a finding; major when ≥ 5 % or the seen rows score better beyond noise
    ov_hit = (ov.get("seen_share") or 0) >= 0.01 and (ov.get("seen_rows") or 0) >= 10
    ov_major = ov_hit and ((ov.get("seen_share") or 0) >= 0.05 or (lift_ is not None and lift_ > max(0.02, sc_["new"].get("pm") or 0)))
    open_left = [c for c in d1 if not any(x["feature"] == c for x in sus)]
    if not sus and not contra and not ov_hit:
        why = "No feature dominates the permutation importance with a leak hint, none alone has AUC ≥ 0.97, no open Data Doctor suspect is among the top features, and no identifier is used."
        if f0:
            why = (f"The top feature '{f0}' holds {_pct(lk['share'])} of the permutation importance"
                   + (f" ({lk['ratio']:.1f}× the next)" if lk.get("ratio") else "")
                   + ("; it is a Data Doctor suspect but below the dominance rule" if f0 in d1 else
                      "; answered known at decision time" if f0 in known else "; no Data Doctor leak hint, and the importance is spread")
                   + (f"; AUC alone {sauc[f0]:.3f}" if sauc.get(f0) is not None else "")
                   + ". No identifier is used as a feature.")
        if open_left:
            why += (f" Still open: {', '.join(open_left)} {'is a' if len(open_left) == 1 else 'are'} Data Doctor suspect"
                    f"{'' if len(open_left) == 1 else 's'} nobody has answered (each under 10 % of the importance here).")
        if ov:
            why += (f" Train–holdout overlap: {_pct(ov.get('seen_share') or 0)} of holdout rows share an entity or a (near-)identical "
                    "row with training (under the 1 % rule).")
        if known & {s for s in (x["column"] for x in g.get("d1_leak") or [])}:
            why += f" Answered known at decision time: {', '.join(sorted(known))}."
        return _check("leakage", "clear", why, {"top": lk.get("top"), "single_auc": lk.get("single_auc"), "open_suspects": open_left,
                                                "overlap": ov or None},
                      source="D3 dominance + D1 + Vela + overlap")
    for s in sus:  # the F17 field names too (single_score / single_measure), for older readers
        if s.get("single_auc") is not None and s.get("single_score") is None:
            s.update(single_score=s["single_auc"], single_measure="AUC")
    leaks = [s["feature"] for s in sus if s["reason"] in ("dominant + hint", "near-perfect alone")]
    opens = [s["feature"] for s in sus if s["reason"] == "open suspect"]
    concs = [s["feature"] for s in sus if s["reason"] == "concentrated"]
    ids = [s["feature"] for s in sus if s["reason"] == "identifier"]
    abl = _ablations(ctx, [])
    by_col = {a["column"]: a for a in abl}
    for s in sus:
        a = by_col.get(s["feature"])
        if a:
            s["ablation"] = {**a, "class": drop_class(a, abl)}
    stands = [s["feature"] for s in sus if (s.get("ablation") or {}).get("class") == "stands out"]
    named_open = [s["feature"] for s in sus if s["reason"] == "open suspect" and s.get("basis") != "strength only"]
    doms = [s["feature"] for s in sus if s["reason"] == "concentrated" and s.get("dominant")]
    if leaks or (contra and (opens or doms)):
        sev = "critical"
    elif ids or contra or ov_major or named_open or stands:
        sev = "major"
    else:  # open questions resting on strength alone (a strong real signal looks the same), or a small overlap
        sev = "minor"
    parts: list[str] = []
    cq = [s0 for s0 in sus if s0["reason"] == "concentrated"]
    for s0 in [x for x in sus if x["reason"] != "concentrated"][:4] + cq[:1]:
        if s0["reason"] == "dominant + hint":
            parts.append(f"'{s0['feature']}' carries {_pct(s0['share'])} of the permutation importance"
                         + (f", {s0['ratio']:.1f}× the next feature" if s0.get("ratio") else "")
                         + f", and the Data Doctor flags it ({s0['hint']}). A column this dominant that is filled in after the outcome "
                           "makes the score look better than any decision made without it.")
        elif s0["reason"] == "near-perfect alone":
            parts.append(f"'{s0['feature']}' alone separates the label with "
                         + (f"AUC {s0['single_auc']:.3f}" if s0.get("single_auc") is not None else f"{s0.get('single_measure', 'score')} {s0.get('single_score', 0):.3f}")
                         + ": a column this predictive is usually recorded after the outcome.")
        elif s0["reason"] == "open suspect":
            parts.append(f"'{s0['feature']}' is a Data Doctor leakage suspect nobody has answered yet ({s0.get('hint') or 'strong alone'}) "
                         f"and carries {_pct(s0['share'])} of the model's importance. Until someone confirms it is known when the "
                         "decision is made, this score may include a leak.")
        elif s0["reason"] == "concentrated":  # A3 N1: one sentence for all the top features' timing questions
            many = len(cq) > 1
            parts.append(", ".join(f"'{x['feature']}' ({_pct(x['share'] or 0)}"
                                   + (f", alone AUC {x['single_auc']:.3f}" if x.get("single_auc") is not None else "") + ")" for x in cq)
                         + f" {'carry' if many else 'carries'} the most of the model's importance and nobody has said "
                         + ("they are" if many else "it is") + " known when the decision is made. Open timing question"
                         + ("s" if many else "") + ": a strong real signal and a column recorded after the outcome look the same "
                           "here; only the timing settles it (a one-column run without each measures how much the score leans on it).")
        else:
            parts.append(f"'{s0['feature']}' looks like an identifier ({_pct(s0.get('unique_share') or 0)} distinct values) and is used as a "
                         "feature; IDs memorise rows and do not generalise.")
    if contra:
        cand = ", ".join(f"'{t['feature']}' ({_pct(t['share'])})" for t in top[:2]) if top else ""
        parts.append(f"The model errs on {contra['model_error']:.3f} of holdout rows, under {R_STAR_RATIO:g} × the look-alike estimate of "
                     f"the error these columns cannot avoid ({contra['lower']:.3f}, 95 % CI [{contra['lower_ci'][0]:.3f}, "
                     f"{contra['lower_ci'][1]:.3f}]; approximate). Only information the look-alikes do not share does that: a leak, an "
                     "identifier, or rows shared between training and holdout"
                     + (f". The first leak candidates are the columns the model leans on most: {cand}" if cand else "") + ".")
    if ov_hit:
        e0 = (ov.get("entities") or [None])[0]
        sc = ov.get("score") or {}
        parts.append(f"{ov['seen_rows']:,} holdout rows ({_pct(ov['seen_share'])}) are not new to the model: "
                     + (f"their {e0['column']} also occurs in training ({e0['holdout_rows']:,} rows)" if e0 else "")
                     + (", " if e0 and (ov['exact']['holdout_rows'] or ov['near']['holdout_rows']) else "")
                     + (f"{ov['exact']['holdout_rows']:,} identical and {ov['near']['holdout_rows']:,} identical but for one column to a training row"
                        if (ov['exact']['holdout_rows'] or ov['near']['holdout_rows']) else "")
                     + (f". Holdout AUC {sc['all']['auc']:.3f} on all rows vs {sc['new']['auc']:.3f} ± {sc['new']['pm']:.3f} on the new rows only"
                        if sc.get("all") and sc.get("new") else "")
                     + (". A split that keeps each entity on one side gives the honest score." if e0 else
                        ". Removing the near-duplicates before the split gives the honest score."))
    txt = " ".join(parts)
    ev: dict[str, Any] = {"suspects": sus, "top": lk.get("top"), "below_bound": contra, "overlap": ov if ov_hit else None,
                          "open_suspects": opens + open_left, "concentrated": concs,
                          "ablations": [s["ablation"] for s in sus if s.get("ablation")]}
    cols = leaks + [c for c in opens + concs if c not in leaks] or ids
    rx: dict[str, Any] | None = None
    if cols:
        measured = [s for s in sus if s["feature"] in cols and s.get("ablation")]
        for s0 in measured:
            a = s0["ablation"]
            cl = a["class"]
            txt += (f" Run without {s0['feature']} ({a['run_id']}, same body and holdout): {a['metric']} {a['with']:.4f} → "
                    f"{a['without']:.4f}, Δ {a['delta']:+.4f} {_ci(a['ci'])}: "
                    + ("the score leans on it far more than on any other column tested, the pattern of a column recorded after "
                       "the outcome. Confirm its timing; if it is not known at decision time, the run without it is the honest score."
                       if cl == "stands out" else
                       "the score uses it, as it would any strong real signal. Confirm its timing." if cl == "uses" else
                       "the score barely depends on it."))
        if measured:
            m0 = measured[0]["ablation"]
            ev["ablation"] = {"run_id": m0["run_id"], "columns": [measured[0]["feature"]], "metric": m0["metric"], "with": m0["with"],
                              "without": m0["without"], "delta": m0["delta"], "ci": m0["ci"], "rows": m0["rows"]}
        todo = [c for c in cols if c not in by_col]
        running = [e for e in ctx.get("experiments") or [] if e.get("kind") == "ablate" and e.get("status") in ("queued", "running")
                   and set(e.get("columns") or []) & set(cols)]
        if todo and running:
            ab = running[0]
            ev.setdefault("ablation", {"run_id": ab["run_id"], "status": ab["status"], "columns": ab.get("columns") or cols})
            txt += f" Run without {', '.join(ab.get('columns') or [])} ({ab['run_id']}) is {ab['status']}: diagnose again when it finishes."
            rx = {"kind": "open_run", "title": "Watch The Run Without The Column", "run_id": ab["run_id"], "why": "Retraining without the suspect column."}
        elif todo:
            c1 = todo[:1]
            rx = {"kind": "experiment", "title": f"Retrain Without {c1[0]} (Ablation)", "endpoint": f"/api/runs/{rid}/experiments",
                  "body": {"kind": "ablate", "columns": c1},
                  "why": "A child run with the same body, holdout and config, minus one column: the score with and without, and Δ "
                         "with a paired-bootstrap CI. A large drop means the score leans on it; whether that is a leak only the "
                         "decision-time answer settles."}
        else:
            ac = [s["feature"] for s in measured if s["ablation"]["class"] != "barely"] or [s["feature"] for s in measured]
            rx = {"kind": "confirm_decision_time", "title": f"Confirm When {', '.join(ac)} {'Is' if len(ac) == 1 else 'Are'} Known",
                  "dataset_id": ctx["run"].get("dataset_id"), "columns": ac, "run_id": measured[0]["ablation"]["run_id"],
                  "question": f"Is {' / '.join(ac)} known at the moment the decision is made?",
                  "why": "Only the bank knows whether the column exists at decision time. Not known: the run without it is the honest "
                         "score. Known: keep it."}
    elif ov_hit:
        e0 = (ov.get("entities") or [None])[0]
        near_ = (ov.get("near") or {}).get("holdout_rows") or 0
        rx = ({"kind": "experiment", "title": f"Retrain With {e0['column']} Kept On One Side (Group Split)",
               "endpoint": f"/api/runs/{rid}/experiments", "body": {"kind": "group_split", "column": e0["column"]},
               "why": "The same body under a split that keeps every entity in training or in the holdout, never both."} if e0 else
              {"kind": "experiment", "title": "Drop Near-Duplicates And Retrain" if near_ else "Drop Duplicate Rows And Retrain",
               "endpoint": f"/api/runs/{rid}/experiments",
               "body": {"kind": "apply_data_fix", "transforms": [{"op": "drop_near_duplicates" if near_ else "drop_duplicates"}]},
               "why": "Rows identical (or identical but for one column) on both sides of the split make the holdout score "
                      "optimistic: the run without them, against this one, measures by how much (unpaired)."})
    else:
        rx = {"kind": "info", "title": "Look For The Column That Gives It Away",
              "why": "The error is below the look-alike estimate: check the top features for columns filled in after the "
                     "outcome, and the split for shared entities."}
    only_timing = bool(cq) and all(x["reason"] == "concentrated" for x in sus) and not contra and not ov_major
    only_overlap = ov_hit and not sus and not contra
    if only_overlap and sev == "minor":  # F-A2: a small overlap that does not move the score is not a leak question
        ev["overlap_only"] = True
        c_ = _check("leakage", "finding", txt, ev, sev, rx, source="overlap")
        c_["title"] = "Train–holdout overlap"
        return c_
    if only_timing and sev == "minor":  # A3 N1: on a clean table these are questions, not a leak finding
        ev["timing_only"] = True
        c_ = _check("leakage", "finding", txt, ev, sev, rx, source="top features without a decision-time answer")
        c_["title"] = "Decision-time questions"
        return c_
    return _check("leakage", "finding", txt, ev, sev, rx,
                  formula="s_j = I_j / Σ_k max(I_k, 0) ≥ 0.5,  s_j ≥ 3·s_(2);  AUC_j ≥ 0.97;  open D1 suspect with s_j ≥ 0.10;  "
                          f"unanswered top-{TIMING_TOP_K} feature (s_j ≥ {TIMING_MIN_SHARE}): an open timing question;  "
                          f"err < {R_STAR_RATIO}·lower(R*);  seen holdout rows ≥ 1 %;  Δ = s(without) − s(with)",
                  source="D3 dominance + D1 hint + concentration + R* estimate + overlap + ablation")


def critical_questions(d: dict[str, Any]) -> list[dict[str, Any]]:
    """The open critical leak questions of a computed diagnosis, [{column | None, question}] (stored on the run's
    `doctor` summary so promotion can read them without recomputing)."""
    lk = next((c for c in d.get("checks") or [] if c["id"] == "leakage"), None)
    if not lk or lk.get("status") != "finding":
        return []
    sev = lk.get("severity")
    ev = lk.get("evidence") or {}

    def gates(s: dict[str, Any]) -> bool:
        # A3 N2: not only a critical leak: an unanswered suspect the Data Doctor names high (confidence ≥ 0.85), or one
        # the Model Doctor rates major or worse, that the model actually uses (≥ GATE_SHARE of the importance). A top
        # feature's timing question alone (strength only) gates when its one-column run stands out, or it dominates in a
        # major finding.
        if s.get("reason") == "identifier":
            return False
        if sev == "critical":
            return True
        if (s.get("share") or 0) < GATE_SHARE:
            return False
        if s.get("reason") in ("dominant + hint", "near-perfect alone"):
            return True
        if s.get("reason") == "open suspect":
            return (s.get("confidence") or 0) >= 0.85 or (sev == "major" and s.get("basis") != "strength only")
        if s.get("reason") == "concentrated":
            return (s.get("ablation") or {}).get("class") == "stands out" or (sev == "major" and bool(s.get("dominant")))
        return False

    out = [{"column": s["feature"], "severity": sev,
            "question": f"Is '{s['feature']}' known at the moment the decision is made? The Doctor flags it ({s['reason']}"
                        + (f", {_pct(s['share'])} of the importance" if s.get("share") else "")
                        + f"; leakage {sev}); if it is recorded after the outcome the score is a leak."}
           for s in ev.get("suspects") or [] if gates(s)]
    if not out and sev == "critical":
        out.append({"column": None, "question": lk["reason"][:300]})
    return out


GATE_SHARE = 0.10


def open_critical(run_id: str, runs: Any = None, datasets: Any = None) -> list[str]:
    """A2 M2 (A3 N2: and the open major leak questions the model leans on, see critical_questions): the run's open
    leak questions that gate promotion, as sentences; [] when none. Read from the Doctor's last diagnosis
    of the run (computed after every fit and on every Doctor view), minus columns answered "known at decision time"
    since. A run whose Doctor never ran returns []. For promotion gates (Production, champion): block while this is
    non-empty unless an explicit override with a reason is given. `runs` / `datasets`: the app's RunStore and
    DatasetStore (app.state.runs / app.state.datasets); built from the settings when omitted."""
    if runs is None or datasets is None:
        from ..config import load_settings
        from ..datasets import DatasetStore
        from ..db import Database
        from ..engine.store import RunStore

        st = load_settings()
        db = Database(st.db_path)
        runs = runs or RunStore(db)
        datasets = datasets or DatasetStore(st, db)
    rec = runs.get(run_id) or {}
    qs = ((rec.get("result") or {}).get("doctor") or {}).get("open_critical") or []
    if not qs:
        return []
    known: set[str] = set()
    try:
        from ..data_doctor_api import answers

        known = {c for c, v in (answers(datasets, rec["dataset_id"]).get("known") or {}).items() if v is True}
    except Exception:  # noqa: BLE001
        pass
    return [q["question"] for q in qs if not q.get("column") or q["column"] not in known]


def _label_gain(ctx: dict[str, Any], rid: str) -> dict[str, Any] | None:
    """D5: the measured value of cleaner training labels. The full-ensemble clean_labels experiment when it has run,
    else the quick model refit without cleanlab's flagged training rows. Fires when the Δ's 95 % CI is above 0."""
    e = _exp(ctx, "clean_labels")
    r = (e or {}).get("result")
    if r and r.get("delta") is not None:
        fires = r["ci"][0] > 0  # Δ is "better is positive" in every metric (experiments.delta)
        return {"fires": fires, "source": f"experiment {e['run_id']}",
                "evidence": {"run_id": e["run_id"], "metric": r["metric"], "with": r["parent"], "without": r["child"], "delta": r["delta"], "ci": r["ci"]},
                "text": (f"The full model retrained without cleanlab's flagged training rows ({e['run_id']}): {r['metric']} {r['parent']:.4f} → "
                         f"{r['child']:.4f}, Δ {r['delta']:+.4f} {_ci(r['ci'])} on the same holdout: "
                         + ("noisy training labels hold the model back." if fires else
                            "within noise, so the flagged labels are not what limits the model." if r["ci"][1] >= 0 else
                            "worse: the flagged rows carry real signal.")),
                "rx": {"kind": "open_run", "title": "Open The Run On Cleaned Labels", "run_id": e["run_id"],
                       "why": "The same body trained without the flagged training rows; review those labels at their source too."}}
    cl = (ctx.get("gaps") or {}).get("clean_labels") or {}
    if cl.get("delta") is None or not cl.get("ci"):
        return None
    fires = cl["ci"][0] > 0
    return {"fires": fires, "source": "quick model",
            "evidence": {"flagged": cl.get("flagged"), "share": cl.get("share"), "metric": cl.get("metric"), "delta": cl["delta"], "ci": cl["ci"]},
            "text": (f"A quick model refit without cleanlab's {cl['flagged']:,} flagged training rows ({_pct(cl['share'])}) gains "
                     f"{cl['delta']:+.4f} {_ci(cl['ci'])} {cl.get('metric') or ''} on the untouched holdout"
                     + (": the noise sits in the training labels. The clean-labels experiment measures it with the full model."
                        if fires else ", within noise.")),
            "rx": {"kind": "experiment", "title": "Retrain On Cleaned Labels", "endpoint": f"/api/runs/{rid}/experiments",
                   "body": {"kind": "clean_labels"},
                   "why": "The run's own body without cleanlab's flagged training rows (a dataset version; the holdout untouched); Δ on the same holdout rows."}}


def _cost_tradeoff(g: dict[str, Any], rid: str) -> dict[str, Any]:
    c = g.get("cost")
    if not c:
        return _check("cost_tradeoff", "n/a", "Not a cost run: the run optimises a standard metric.", source="F7")
    rows = c["rows"]
    s = rows[0]
    cf = c["configured"]
    ratio = cf["c_fn"] / cf["c_fp"] if cf["c_fp"] else None
    alt = "; ".join(f"at {r['name']} (t = {r['t']:.3f}) {_pct(r['decline_rate'])} declined, {_pct(r['catch_rate'])} caught, "
                    f"{r['cost_per_1000']:,.0f} per 1,000" for r in rows[1:])
    txt = (f"This run minimises a business cost (c_FN {cf['c_fn']:g} : c_FP {cf['c_fp']:g}"
           + (f", a {ratio:.3g}:1 ratio" if ratio else "") + f"). At the served threshold t = {s['t']:.3f} it declines "
           f"{_pct(s['decline_rate'])} of applicants and catches {_pct(s['catch_rate'])} of '{c['positive_class']}', at "
           f"{s['cost_per_1000']:,.0f} per 1,000 decisions. Under the same costs, {alt}. The ratio sets the operating point more "
           "than any model change: confirm it with the business.")
    return _check("cost_tradeoff", "finding", txt, {"rows": rows, "configured": cf, "prevalence": c["prevalence"], "n": c["n"]},
                  "major", {"kind": "open_view", "view": "Threshold", "title": "Confirm The Cost Ratio",
                            "why": "Explain → Threshold shows the cost curve: set the ratio the business confirms and keep its threshold."},
                  formula="t*(r) = 1 / (1 + r),  decline = #{p̂ > t}/n,  catch = TP/(TP + FN),  C = 1000·(c_FP·FP + c_FN·FN − g_TP·TP)/n",
                  source="F7 + D3")


def _more_rows(ctx: dict[str, Any]) -> dict[str, Any] | None:
    g = ctx.get("gaps") or {}
    res = (ctx["run"].get("result") or {})
    lc = res.get("learning_curve") or {}
    v = lc.get("verdict") if isinstance(lc, dict) else None
    qc = g.get("quick_curve")
    x2 = ((v or {}).get("extrapolation") or {}).get("x2") if v else None
    if x2 and x2.get("gain") is not None and x2.get("ci"):
        nat_now = x2["score"] - x2["gain"]
        return {"delta": float(x2["gain"]), "ci": [float(x2["ci"][0] - nat_now), float(x2["ci"][1] - nat_now)],
                "metric": v.get("metric"), "source": f"F12 learning curve ({v.get('label')}): projected at 2N = {x2['n']:,} rows"}
    if qc:
        return {"delta": qc["next_doubling"], "ci": qc["ci"], "metric": qc["metric"],
                "source": f"early-stopped LightGBM on nested training subsets ({', '.join(f'{r:,}' for r in qc['rows'])} rows); the next doubling projected"}
    return None


def _improve(ctx: dict[str, Any], checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How to improve: one ranked list of levers, each with a measured Δ (or an offer to measure it)."""
    run = ctx["run"]
    rid = run["id"]
    g = ctx.get("gaps") or {}
    pt = (run.get("result") or {}).get("problem_type")
    clf = pt in ("binary", "multiclass")
    by = {c["id"]: c for c in checks}
    out: list[dict[str, Any]] = []
    lk = by.get("leakage")
    if lk and lk["status"] == "finding":
        ab = lk["evidence"].get("ablation") or {}
        cols = [s["feature"] for s in lk["evidence"]["suspects"] if s["reason"] != "identifier"] or [s["feature"] for s in lk["evidence"]["suspects"]]
        if not cols:  # train/holdout overlap or an error below the bound, no column named: the prescription is the lever
            out.append({"id": "drop_leak", "title": lk["prescription"]["title"] if lk.get("prescription") else "Remove the overlap",
                        "must": True, "delta": None, "ci": None, "metric": None,
                        "source": "not measured yet: " + ((lk.get("prescription") or {}).get("why") or ""), "action": lk.get("prescription")})
            cols = None
    if lk and lk["status"] == "finding" and cols:
        out.append({"id": "drop_leak", "title": f"Settle the leak question ({', '.join(cols)})", "must": True,
                    "delta": ab.get("delta"), "ci": ab.get("ci"), "metric": ab.get("metric"),
                    "source": f"ablation {ab['run_id']}: same body and holdout without the column(s)" if ab.get("delta") is not None else
                              ("ablation running" if ab.get("run_id") else "not measured yet: run the ablation"),
                    "note": ("If the column is not known at decision time, the run without it is the honest score; a fall is evidence the "
                             "score leans on it.") if ab.get("delta") is not None else None,
                    "action": lk["prescription"]})
    mr = _more_rows(ctx)
    lc_missing = not ((run.get("result") or {}).get("learning_curve") or {}).get("verdict")
    out.append({"id": "more_rows", "title": "Collect more labelled rows", "delta": mr and mr["delta"], "ci": mr and mr["ci"],
                "metric": mr and mr["metric"], "source": mr["source"] if mr else "not measured",
                "action": ({"kind": "run_check", "check": "learning_curve", "title": "Run The Learning Curve", "endpoint": f"/api/runs/{rid}/learning-curve",
                            "body": {}, "why": "The run's own fit on 10 / 25 / 50 % of its rows (F12): the measured value of more rows."}
                           if lc_missing else
                           {"kind": "labelling_queue", "title": "Label More Rows", "run_id": rid, "endpoint": f"/api/runs/{rid}/active-learning",
                            "why": "The labelling queue picks the most informative rows."} if clf else
                           {"kind": "info", "title": "Collect more rows of the same kind"})})
    cl = g.get("clean_labels")
    if cl:
        um = cl.get("measurable") is False
        out.append({"id": "clean_labels", "title": f"Clean the labels ({cl['flagged']:,} training rows flagged, {_pct(cl['share'])})",
                    "delta": None if um else cl.get("delta"), "ci": None if um else cl.get("ci"), "metric": cl.get("metric"),
                    "unmeasurable": um, "suggested": um,
                    "source": ("not measurable on this holdout: " + cl["unmeasurable_reason"]) if um else
                              "quick model refit without cleanlab's flagged training rows; holdout untouched" if cl.get("delta") is not None
                              else "too few flagged rows to measure",
                    "action": {"kind": "inbox", "title": "Review Flagged Labels", "run_id": rid,
                               "why": "The labelling queue and annotators (Doctor → Labels): fix the labels at the source."}})
    fl = ctx.get("feature_lab")
    ds = run["dataset_id"]
    if fl:
        out.append({"id": "new_features", "title": f"New features (Feature Lab: {fl['kept']} kept of {fl['tried']} tried)",
                    "delta": fl.get("delta"), "ci": fl.get("ci"), "metric": fl.get("metric"), "source": fl.get("source"),
                    "action": {"kind": "link", "title": "Open Feature Lab", "link": f"build?dataset={ds}&lab=1",
                               "why": "Build → Advanced · Feature Lab: typed features, scored on the training folds only."}})
    else:
        out.append({"id": "new_features", "title": "New features", "delta": None, "ci": None, "metric": None,
                    "source": "not measured: Feature Lab has not run on this dataset",
                    "action": {"kind": "link", "title": "Search Features", "link": f"build?dataset={ds}&lab=1",
                               "why": "Build → Advanced · Feature Lab: ratios, differences, aggregates, date parts; kept only on a clear gain."}})
    for kind, title in (("budget", "More time budget (2×)"), ("preset", "A stronger preset")):
        e = _exp(ctx, kind)
        r = (e or {}).get("result")
        fires = kind == "budget" and (by.get("budget") or {}).get("status") == "finding"
        out.append({"id": kind, "title": title, "delta": r and r["delta"], "ci": r and r["ci"], "metric": r and r["metric"],
                    "source": (f"experiment {e['run_id']}: {e.get('title')}" if r else f"experiment {e['run_id']} {e['status']}" if e else
                               "not measured" + (": the time-budget check fired" if fires else "")),
                    "action": ({"kind": "open_run", "title": "Open The Experiment", "run_id": e["run_id"]} if e else
                               {"kind": "experiment", "title": f"Run The {'Budget' if kind == 'budget' else 'Preset'} Experiment",
                                "endpoint": f"/api/runs/{rid}/experiments", "body": {"kind": kind},
                                "why": "A child run with only this change; Δ on the same holdout rows."}),
                    "suggested": fires})

    out.sort(key=_rank_key)
    for i, x in enumerate(out):
        x["rank"] = i + 1
        x["measured"] = x.get("delta") is not None
    return out


def _rank_key(x: dict[str, Any]) -> tuple[float, float]:
    """A must-do first; then measured gains > 0 (largest first); then what is not measured yet (an offer suggested by a
    finding first); then what was measured and does not help."""
    if x.get("must"):
        return (0, 0.0)
    d = x.get("delta")
    if d is not None and d > 0:
        return (1, -float(d))
    if d is None:
        return (2.0 if x.get("suggested") else 2.5, 0.0)
    return (3, -float(d))


def _ceiling(ctx: dict[str, Any], checks: list[dict[str, Any]], improve: list[dict[str, Any]]) -> dict[str, Any]:
    run = ctx["run"]
    res = run.get("result") or {}
    g = ctx.get("gaps") or {}
    by = {c["id"]: c for c in checks}
    conds: dict[str, Any] = {}
    missing: list[str] = []
    # flat learning curve
    v = ((res.get("learning_curve") or {}).get("verdict") or {}) if isinstance(res.get("learning_curve"), dict) else {}
    qc = g.get("quick_curve")
    if v.get("label") in ("saturated", "data-limited"):
        conds["flat"] = {"ok": v["label"] == "saturated", "source": "F12", "verdict": v["label"]}
    elif qc:
        conds["flat"] = {"ok": bool(qc["flat"]), "source": "quick curve", "next_doubling": qc["next_doubling"], "ci": qc["ci"], "eps": qc["eps"]}
    else:
        missing.append("learning curve")
    # top models agree
    lad = res.get("ladder") or {}
    single = next((r for r in lad.get("rungs") or [] if r.get("rung") == 2), None)
    if single and single.get("gap"):
        best_r = next((r for r in lad.get("rungs") or [] if r.get("rung") == lad.get("best_rung")), None)
        conds["agree"] = {"ok": bool(single.get("within")), "single": single.get("name"), "gap": single["gap"]["delta"],
                          "ci": single["gap"]["ci"], "metric": lad.get("metric_label"), "best_rung": lad.get("best_rung"),
                          "best_name": (best_r or {}).get("name"), "recommended": (lad.get("recommended") or {}).get("name")}
    else:
        missing.append("model ladder")
    of = by.get("overfit") or {}
    if of.get("status") in ("finding", "clear"):
        z_ = (of.get("evidence") or {}).get("z")
        conds["gap_noise"] = {"ok": of["status"] != "finding" and (z_ is None or z_ < 3), "z": z_}
    else:
        missing.append("train–holdout gap")
    ws = by.get("weak_slice") or {}
    conds["not_concentrated"] = {"ok": not (ws.get("status") == "finding" and ws.get("severity") == "major"),
                                 "weak_slice": ws.get("status")}
    lk = by.get("leakage") or {}
    if lk.get("status") == "finding" and not (lk.get("evidence") or {}).get("overlap_only"):
        return _check("ceiling", "clear", ("Decision-time questions are open on the top features" if (lk.get("evidence") or {}).get("timing_only")
                                           else "A leak question is open") + ": the data's limit is read only after "
                      "it is answered (and on the ablation run if the column is not known at decision time).", {"conditions": conds}, source="D3")
    opens = (lk.get("evidence") or {}).get("open_suspects") or []
    if opens:
        return _check("ceiling", "clear", f"Not judged: {', '.join(opens)} {'is a' if len(opens) == 1 else 'are'} Data Doctor leakage "
                      "suspect" + ("" if len(opens) == 1 else "s") + " nobody has answered. Confirm when "
                      + ("it is" if len(opens) == 1 else "they are") + " known at decision time first.", {"conditions": conds}, source="D3")
    if missing:
        return _check("ceiling", "missing" if "learning curve" in missing else "n/a", "Not enough to say: " + ", ".join(missing) + " missing.",
                      {"conditions": conds}, offer={"kind": "run_check", "check": "learning_curve", "title": "Run The Learning Curve",
                                                   "endpoint": f"/api/runs/{run['id']}/learning-curve", "body": {},
                                                   "why": "9 refits on 10 / 25 / 50 % of the training rows (F12)."}, source="D3")
    parts = []
    f = conds["flat"]
    parts.append("the learning curve is flat (F12 saturated)" if f["source"] == "F12" and f["ok"] else
                 "the learning curve still rises (F12 data-limited)" if f["source"] == "F12" else
                 f"the quick model's next doubling is {_sv(f['next_doubling'], ref=f['eps'] * 200)} {_cis(f['ci'], ref=f['eps'] * 200)} " + ("< " if f["ok"] else "vs ") + f"ε = {f['eps']:.4g}"
                 + (" (upper bound < 2ε)" if f["ok"] else ""))
    a = conds["agree"]
    # A2: the same words as the ladder (its best rung by name, and the rung it recommends)
    parts.append(f"{a['single']} is the best rung of the ladder" if a.get("best_rung") == 2 else
                 f"the ladder's best rung is {a.get('best_name') or 'the stack'}; {a['single']} is {abs(a['gap']):.4f} {a['metric']} "
                 f"{'below' if a['gap'] < 0 else 'above'} it {_ci(a['ci'])}" + (" (within noise)" if a["ok"] else " (outside the interval)")
                 + (f"; the ladder recommends {a['recommended']}" if a.get("recommended") and a.get("recommended") != a.get("best_name") else ""))
    gz = conds["gap_noise"]
    parts.append("the train–holdout gap is within noise" + (f" (z = {gz['z']:.1f})" if gz.get("z") is not None else "") if gz["ok"] else
                 "the train–holdout gap is beyond noise" + (f" (z = {gz['z']:.1f})" if gz.get("z") is not None else ""))
    parts.append("no fixable weak slice" if conds["not_concentrated"]["ok"] else "a fixable weak slice exists")
    allok = all(c["ok"] for c in conds.values())
    txt = "; ".join(parts) + "."
    if not allok:
        return _check("ceiling", "clear", "Not at the data's limit yet: " + txt, {"conditions": conds}, source="D3")
    top = next((x for x in improve if x.get("measured") and (x.get("delta") or 0) > 0), None)
    rx = ({**top["action"], "why": f"The best measured lever: {top['title']} ({_sv(top['delta'])} {top.get('metric') or ''})."}
          if top and top.get("action") else
          {"kind": "info", "title": "Tuning will not help: more rows, new features or a cleaner label would"})
    tw = (ctx.get("gaps") or {}).get("twins")
    extra = ((f" Look-alike rows (nearest neighbours) put the error these columns cannot avoid at roughly {tw['lower']:.3f} to {tw['r_nn']:.3f} "
              f"(the model errs on {tw['model_error']:.3f}; an approximate estimate)." if tw["model_error"] >= tw["lower"] else
              f" The model errs on {tw['model_error']:.3f}, below the look-alike estimate ({tw['lower']:.3f}): on this table that "
              "estimate is not a bound (it is approximate and runs high with many columns).") if tw else "")
    return _check("ceiling", "finding", "At the data's limit: " + txt + " Tuning will not help; more rows, new features or a cleaner "
                  "label would. What's missing ranks the gaps." + extra, {"conditions": conds}, "minor", rx,
                  formula="flat ∧ single ≈ stack ∧ z < 3 ∧ ¬fixable slice", source="F12 / quick curve + D2 ladder + fit gap + F16")


def _gain(o: dict[str, Any] | None, source: str, simulated: bool = False) -> dict[str, Any] | None:
    if not o or o.get("delta") is None:
        return None
    return {"delta": float(o["delta"]), "ci": o.get("ci"), "metric": o.get("metric"), "source": source, "simulated": simulated}


def _excess_gain(o: dict[str, Any] | None, source: str) -> dict[str, Any] | None:
    """An oracle's gain beyond a random segment of the same size (older caches without the baseline: the raw gain)."""
    if not o or o.get("delta") is None:
        return None
    if o.get("excess") is None:
        return _gain(o, source, True)
    if o.get("beyond_noise") is False:  # worth no more than a random group of the same size: not a gain of this gap
        return None
    return {"delta": float(o["excess"]["delta"]), "ci": o["excess"]["ci"], "metric": o.get("metric"), "simulated": True,
            "source": f"{source}: {_sv(o['delta'])}, minus {_sv(max(o['random']['delta'], 0), ref=o['delta'])} that a random segment of the same size gets",
            "raw": o["delta"], "random": o["random"]["delta"], "beyond_noise": o.get("beyond_noise")}


def _missing(ctx: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    """What's missing: the information gaps, ranked by measured potential gain, each a concrete data request."""
    g = ctx.get("gaps") or {}
    run = ctx["run"]
    pt = (run.get("result") or {}).get("problem_type")
    orc = {o["id"]: o for o in (g.get("oracle") or {}).get("segments") or []}
    gaps: list[dict[str, Any]] = []
    served = g.get("served") or {}
    tw = g.get("twins")
    lk = next((c for c in checks if c["id"] == "leakage"), None)
    lk_open = bool(lk and lk["status"] == "finding" and not (lk.get("evidence") or {}).get("overlap_only"))
    lev = (lk or {}).get("evidence") or {}
    # A2 C1: columns with an open leak question are never the subject of a data request ("collect a finer X")
    unanswered = ({x["feature"] for x in lev.get("suspects") or [] if x.get("feature")} | set(lev.get("open_suspects") or [])
                  | {x["column"] for x in g.get("d1_leak") or [] if x["column"] not in set(ctx.get("known") or [])})
    lab_q = any(c["id"] == "label_noise" and c["status"] in ("finding", "no-evidence") for c in checks)
    if tw:
        ex = tw.get("exact_conflicts") or {}
        f = (f"{_pct(tw['errors_in_mixed'])} of the model's errors are rows whose {tw['k']} closest training rows (on the columns that "
             f"matter, weighted by importance) had mixed outcomes: look-alike customers who ended differently. How often look-alikes "
             f"disagree ({tw['r_nn']:.3f}, 95 % CI [{tw['r_nn_ci'][0]:.3f}, {tw['r_nn_ci'][1]:.3f}]) puts the error no model can avoid "
             f"with these columns between about {tw['lower']:.3f} and {tw['r_nn']:.3f}, against a model error of "
             f"{tw['model_error']:.3f}" + (
                 ": while a leak question is open (see Leakage) this is not read as the data's limit: the columns the estimate "
                 "uses include the suspects." if lk_open else
                 f": roughly {_pct(tw['explained'] or 0)} or more of the error is beyond what these columns can separate (an approximate, "
                 "finite-sample estimate)." if (tw.get("explained") or 0) < 0.95 else
                 ": the model errs less than this estimate, so on this table it is not a lower bound (the look-alike estimate is "
                 "approximate and runs high with many columns), or the model reads something the neighbours cannot see (a leak, an "
                 "ID, rows shared with training): see Leakage."))
        if tw.get("where"):
            w = tw["where"]
            f += f" These look-alikes concentrate in {w['label']} ({_pct(w['share_mixed'])} of them vs {_pct(w['share_all'])} of all rows)."
        if ex.get("rows"):
            f += f" {ex['rows']:,} rows in {ex['groups']:,} groups are identical on every column with different labels."
        gaps.append({"id": "twins", "kind": "twins", "title": "Look-alike rows with different outcomes", "finding": f,
                     "request": "Join information that differs between these look-alikes: a source the table lacks (bureau data, a "
                                "history of the key columns, behaviour over time). Reviewing the identical-but-different rows tells a "
                                "wrong label from a missing column.",
                     "gain": _excess_gain(orc.get("twins"), "simulated perfect information on the look-alike rows"),
                     "evidence": {k: tw.get(k) for k in ("r_nn", "r_nn_ci", "lower", "lower_ci", "model_error", "errors_in_mixed",
                                                         "mixed_share_holdout", "closeness", "k", "exact_conflicts", "where")}, "method": tw.get("method")})
    con = g.get("concentration") or {}
    for s in (con.get("segments") or [])[:3]:
        mi = s.get("missing_inside") or []
        o = orc.get(f"seg:{s['column']}={s['value']}")
        lr = s.get("label_review")
        f = (f"Errors concentrate in {s['label']} ({s['n']:,} holdout rows): {s['rate']:.1%} of them are wrong vs {con['overall_rate']:.1%} "
             f"overall ({s['lift']:.2f}×, {s['excess']:.0f} more errors than an average group of that size).")
        if lr and not any(x["kind"] == "label_review" for x in gaps):  # the top such segment only
            gaps.append({"id": f"labels:{s['column']}={s['value']}", "kind": "label_review", "title": f"Wrong labels or missing information? {s['label']}",
                         "finding": f + f" cleanlab flags {_pct(lr['rate_inside'])} of the labels in this group vs {_pct(lr['rate_outside'])} "
                                        f"elsewhere ({lr['flagged']:,} holdout rows)"
                                        + (f", more than labels drawn from a reference model's own probabilities would give here "
                                           f"({_pct(lr['expected_rate'])} after the table-wide calibration, z = {lr['z']:.1f}): these labels "
                                           "disagree with the rest of the table more than the cases' hardness explains"
                                           if lr.get("z") is not None else "")
                                        + ". Two explanations fit: the outcome is recorded wrongly here (a process, a form, a team), or the "
                                          "table lacks what separates these cases. A sample of reviewed labels tells them apart.",
                         "request": f"First review a sample of the {lr['flagged']:,} flagged labels where {s['label']} (Doctor → Labels): "
                                    "if many are wrong, fix how the outcome is recorded there (and the holdout labels, or no score can "
                                    "show the gain); if they are right, the gap is missing information (below).",
                         "gain": None, "suggested": True, "evidence": {**lr, "column": s["column"], "value": s["value"]}})
        if o and o.get("excess") is not None and not o.get("beyond_noise"):
            continue  # what a random group of the same size would gain: not a property of this segment
        req = (f"For {s['label']}, find what separates the cases the model gets wrong from the ones it gets right: a source "
               "the table lacks for this group")
        if mi:
            m = mi[0]
            f += f" Inside its errors {m['column']} is missing {_pct(m['missing_in_errors'])} vs {_pct(m['missing_elsewhere'])} elsewhere."
            req = f"Fix how {m['column']} is captured for {s['label']} (missing {_pct(m['missing_in_errors'])} in these errors), then " + req[0].lower() + req[1:]
        tf = [c for c in s.get("top_features") or [] if c not in unanswered]
        if tf:
            req += f"; the model already leans on {', '.join(tf[:2])} here, so a finer or historical version of those is a start"
        if lr:
            req = "If the labels check out: " + req[0].lower() + req[1:]
        gain = None
        if o:
            gain = {"delta": float(o["excess"]["delta"]), "ci": o["excess"]["ci"], "metric": o.get("metric"), "simulated": True,
                    "source": (f"simulated perfect information on {s['label']}: {_sv(o['delta'])}, minus {_sv(max(o['random']['delta'], 0), ref=o['delta'])} that a random "
                               f"{_pct(o.get('share') or 0)} of rows gets"), "raw": o["delta"], "random": o["random"]["delta"]}
        gaps.append({"id": f"segment:{s['column']}={s['value']}", "kind": "concentration", "title": f"Errors concentrate: {s['label']}",
                     "finding": f, "request": req + ".", "gain": gain,
                     "evidence": {k: s.get(k) for k in ("column", "value", "n", "errors", "rate", "lift", "excess", "q")} | {"missing_inside": mi}})
    for m in (con.get("missing_in_errors") or [])[:1]:
        if any(m["column"] == x["column"] for s in con.get("segments") or [] for x in s.get("missing_inside") or []):
            continue
        gaps.append({"id": f"missing:{m['column']}", "kind": "missingness", "title": f"Missing where it matters: {m['column']}",
                     "finding": f"{m['column']} is missing in {_pct(m['missing_in_errors'])} of the error rows vs {_pct(m['missing_elsewhere'])} of the rest (z = {m['z']:.1f}).",
                     "request": f"Fix how {m['column']} is collected: it is missing where the model goes wrong.", "gain": None, "evidence": m})
    for t in ((g.get("thin") or {}).get("features") or [])[:2]:
        gaps.append({"id": f"thin:{t['column']}", "kind": "thin", "title": f"Thin feature: {t['column']}",
                     "finding": (f"{t['column']} has {t['levels']} values and carries {_pct(t['importance_share'])} of the signal, but "
                                 f"{_pct(t['within_share'])} of the outcome's variance is left inside its levels (worst: {t['column']} = "
                                 f"{t['worst_level']}, {t['worst_n']:,} rows, rate {t['worst_rate']:.3f})."),
                     "request": f"Collect a finer measure of {t['column']}, or its history (a trend over months, not one snapshot).",
                     "gain": _excess_gain(orc.get(f"thin:{t['column']}"), f"simulated perfect information on {t['column']} = {t['worst_level']}"), "evidence": t})
    for p in ((g.get("confusion") or {}).get("pairs") or [])[:3]:
        gaps.append({"id": f"confusion:{p['a']}|{p['b']}", "kind": "label_overlap", "title": f"Labels overlap: '{p['a']}' ↔ '{p['b']}'",
                     "finding": f"'{p['a']}' is predicted as '{p['b']}' {p['a_as_b']} times and '{p['b']}' as '{p['a']}' {p['b_as_a']} times: the model confuses them both ways, so the label definitions overlap.",
                     "request": f"Redefine the label: write the rule that separates '{p['a']}' from '{p['b']}', relabel the examples, or merge them.",
                     "gain": {"delta": p["gain"], "ci": None, "metric": "accuracy", "simulated": False,
                              "source": "holdout count: the errors between the two, an upper bound if they were one label"}, "evidence": p})
    mr = _more_rows(ctx)
    if mr and mr.get("ci") and mr["ci"][1] > 0:
        gaps.append({"id": "more_rows", "kind": "more_rows", "title": "More rows of the same kind",
                     "finding": f"Doubling the training rows: {_sv(mr['delta'], ref=max(abs(x) for x in [mr['delta'], *(mr['ci'] or [])]))} "
                                f"{_cis(mr['ci'], ref=max(abs(x) for x in [mr['delta'], *(mr['ci'] or [])]))} {mr['metric']}{_better(mr['metric'])} ({mr['source']}).",
                     "request": "Collect more labelled rows, if the gain is worth the cost.", "gain": _gain(mr, mr["source"]), "evidence": mr})
    cl = g.get("clean_labels")
    eo = (g.get("errors_top") or {}).get("cleanlab")
    if cl or eo:
        f = ""
        le = g.get("label_expect") or {}
        if le.get("observed") is not None:  # A2 M3: against what clean labels would give, not "errors vs correct rows" (circular)
            f += (f"cleanlab flags {_pct(le['observed'])} of the holdout labels; labels drawn from the model's own probabilities "
                  f"(clean by construction) would be flagged at {_pct(le['expected'])} (95 % up to {_pct(le['expected_hi'])}): "
                  + ("more than clean labels explain. " if le["observed"] > le["expected_hi"] else "no more than clean labels explain. "))
        unmeas = bool(cl and cl.get("measurable") is False)
        if cl and cl.get("delta") is not None:
            f += (f"Retraining without the {cl['flagged']:,} flagged training rows: {cl['delta']:+.4f} {_ci(cl['ci'])} {cl['metric']} "
                  + ("against the holdout's own labels, which cleanlab flags at a similar rate: not a measure of the gain. "
                     + cl["unmeasurable_reason"] if unmeas else "on the untouched holdout."))
        gaps.append({"id": "labels", "kind": "label_quality", "title": "Label quality", "finding": f.strip() or "No label issues measured.",
                     "request": "Review the flagged labels at their source and fix the labelling rule where they cluster.",
                     "gain": None if unmeas else (_gain(cl, "quick model refit without the flagged training rows") if cl else None),
                     "unmeasurable": unmeas, "suggested": unmeas, "evidence": {"holdout": eo, "train": cl}})
    fl = ctx.get("feature_lab")
    if fl:
        gaps.append({"id": "new_features", "kind": "new_features", "title": "New features from the same columns",
                     "finding": f"Feature Lab tried {fl['tried']} typed features and kept {fl['kept']}: {fl['source']}.",
                     "request": ("Derived features add little: settle the open leak or label question first, then read this again."
                                 if lk_open or lab_q else
                                 "Derived features add little: the gain must come from new information, not new arithmetic.") if not fl["kept"]
                                else "Use the Feature Lab version for the next run.",
                     "gain": _gain(fl, fl["source"]), "evidence": fl})
    caveat = None
    if lk_open:
        sus_q = [x["feature"] for x in lev.get("suspects") or [] if x.get("reason") != "identifier"]
        ovq = lev.get("overlap") or {}
        qs = ([f"is {' / '.join(sus_q)} known when the decision is made?"] if sus_q else []) \
            + ([f"{_pct(ovq.get('seen_share') or 0)} of holdout rows are not new to the model (shared entity or near-duplicate)"] if ovq else []) \
            + (["the model errs below the look-alike estimate: which column gives it away?"] if lev.get("below_bound") else [])
        tonly = bool(lev.get("timing_only"))
        caveat = (("Measured with open decision-time questions (" if tonly else "Measured with an open leak question (") + "; ".join(qs)
                  + "): these gaps, and the look-alike estimate, describe a model that reads "
                  + ("those columns" if tonly else "it") + ", and a gap on a column with an open question is not a data request. "
                  "Answer in Build → Data Report (decision time), then read them again"
                  + (f" on the run without the column ({lev['ablation']['run_id']})" if (lev.get("ablation") or {}).get("run_id") else "") + ".")
    if unanswered:
        # A3 N1: a gap about a column with an open question is never a data request ("collect a finer X"): a named
        # suspect's gaps go; a top feature's open timing question keeps the segment gap, re-worded to confirm first
        timing_q = {x["feature"] for x in lev.get("suspects") or [] if x.get("reason") == "concentrated"}
        kept = []
        for x in gaps:
            col = next((c for c in unanswered if x["id"].split(":", 1)[-1].split("=")[0] == c), None)
            if col is None:
                kept.append(x)
            elif col in timing_q and x["kind"] in ("concentration", "label_review", "missingness"):
                x["request"] = (f"First confirm {col} is known at decision time (an open timing question, see Leakage); if it is: "
                                + x["request"][0].lower() + x["request"][1:])
                x["pending_question"] = col
                kept.append(x)
        gaps = kept
    for x in gaps:
        x["measured"] = x.get("gain") is not None
    def _gap_key(x: dict[str, Any]) -> tuple[float, float]:
        gn = x.get("gain") or {}
        if x.get("kind") != "label_review" and gn.get("delta") is not None and not gn.get("simulated") and gn.get("ci") and gn["ci"][0] > 0:
            return (0.5, -float(gn["delta"]))  # D5: a gain measured beyond noise and available now, before the simulated upper bounds
        return _rank_key({"delta": gn.get("delta"), "suggested": x.get("suggested")})

    gaps.sort(key=_gap_key)
    lr_i = next((i for i, x in enumerate(gaps) if x["kind"] == "label_review"), None)
    if lr_i is not None:  # the label check goes just before the first gap about the same segment (it is the cheaper test)
        lr_g = gaps.pop(lr_i)
        seg_i = next((i for i, x in enumerate(gaps) if x["id"].split(":", 1)[-1] == lr_g["id"].split(":", 1)[-1]), len(gaps))
        gaps.insert(min(seg_i, lr_i), lr_g)
    for i, x in enumerate(gaps):
        x["rank"] = i + 1
    top = gaps[0] if gaps else None
    head = None
    if top:
        head = f"{top['title']}: " + (f"worth up to {_sv(top['gain']['delta'])} {top['gain']['metric'] or ''}" if top.get("gain") else "not measured") + f". {top['request']}"
        if caveat and qs:  # A3 N1: the open question comes before any data request
            head = f"First answer the open question ({qs[0]}), then: " + head
    return {"schema": "missing/1", "gaps": gaps, "headline": head, "served": served, "quick": g.get("quick"), "caveat": caveat,
            "caveat_questions": qs if lk_open else [],
            "irreducible": tw and {k: tw.get(k) for k in ("r_nn", "r_nn_ci", "lower", "lower_ci", "model_error", "explained", "explained_upper")},
            "rules": {"rank": "a gain measured beyond noise (its 95 % CI above 0, not a simulation) first, then measured potential gain "
                              "(Δ of the run's quick-model metric; an oracle is a simulation and an upper bound), then the unmeasured gaps",
                      "oracle": (g.get("oracle") or {}).get("rule"), "concentration": con.get("rule"), "thin": (g.get("thin") or {}).get("rule"),
                      "twins": tw and tw.get("method"), "confusion": (g.get("confusion") or {}).get("rule"),
                      "curve": (g.get("quick_curve") or {}).get("rule"), "clean": (g.get("clean_labels") or {}).get("rule")},
            "domain_gaps": _domain_gaps(ctx.get("review")),
            "problem_type": pt, "seconds": g.get("seconds"), "errors": g.get("errors") or []}


def _domain_gaps(rv: dict[str, Any] | None) -> dict[str, Any]:
    label = "Suggestions to investigate (Claude, D4 review): never mixed with measured numbers"
    if not rv:
        return {"status": "pending", "source": "llm", "label": label, "items": [],
                "note": "Run a review (Doctor → Review) to fill this: Claude compares the columns with what is typically predictive for this task."}
    items = rv.get("domain_gaps") or []
    return {"status": "done" if items else "none", "source": "llm", "label": label, "items": items, "review_id": rv["id"],
            "decider": rv.get("decider"), "model": rv.get("model"), "at": rv.get("finished_at"),
            "note": None if items else ("The review ran offline: no language model, so no suggestions." if rv.get("mode") == "offline"
                                        else "The review's language model returned no suggestions.")}


def _errors_view(g: dict[str, Any]) -> dict[str, Any] | None:
    if not g:
        return None
    return {"served": g.get("served"), "top": g.get("errors_top"), "slices": (g.get("concentration") or {}).get("segments"),
            "overall_rate": (g.get("concentration") or {}).get("overall_rate"),
            "missing_in_errors": (g.get("concentration") or {}).get("missing_in_errors"),
            "slice_rule": (g.get("concentration") or {}).get("rule"), "by_time": g.get("by_time")}
