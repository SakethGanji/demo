"""G2 · Credit-risk model report: the pure computations (the API that gathers the data is credit_report_api.py).

For a binary scoring model with p̂ = P(bad | x), "bad" being the positive class (default):

Rank ordering
- Gini = 2·AUC − 1, with DeLong's interval mapped the same way: [2·lo − 1, 2·hi − 1].
- KS = maxₛ |F_bad(s) − F_good(s)|, the largest gap between the cumulative score distributions of bads and goods,
  with a percentile bootstrap interval (B row resamples).
- Both overall and per period: the holdout rows by month of the time column, and every labelled scored batch by the
  month of its data.

Score bands: the points scale of a scorecard, Score = O + F·ln(odds_good), F = PDO / ln 2, chosen so that 600 points
mean good:bad odds of 50:1 and every 20 points (PDO) double the odds. Bands are PDO wide. Per band: count, bads, bad
rate (Wilson 95 %), the cumulative share of bads captured from the riskiest band down, the cumulative share of the
population, lift = bad rate / overall bad rate, and the cumulative lift = capture / population share.

Deciles: ten equal-count groups by p̂, riskiest first: observed bad rate (Wilson) next to the mean p̂.

Stability
- PSI of the score: each batch's p̂ against the version's holdout p̂ (registry/drift.py, the same bins).
- CSI per feature per month: the same sum Σᵢ (aᵢ − eᵢ)·ln(aᵢ / eᵢ) on one characteristic, against the training rows
  (it is the per-feature PSI F18 stores with every batch). Flags: > 0.25 shift, 0.10–0.25 watch.

Vintage curves: for origination cohort c and months on book k, the cumulative bad rate
    V_c(k) = #{loans of c that defaulted by MOB k} / n_c,
shown only while every loan of the cohort has been watched for k months (k ≤ K_c, the as-of month minus the cohort's
last origination month).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..stats import delong, wilson

Z = 1.959963984540054
PDO = 20.0
BASE_SCORE = 600.0
BASE_ODDS = 50.0
FACTOR = PDO / np.log(2)
OFFSET = BASE_SCORE - FACTOR * np.log(BASE_ODDS)
KS_BOOTSTRAP = 300
CSI_SHIFT = 0.25
CSI_WATCH = 0.10
MIN_ROWS = 50
FORMULAS = {
    "gini": "Gini = 2·AUC − 1,  CI = 2·[AUC_lo, AUC_hi] − 1 (DeLong)",
    "ks": "KS = maxₛ |F_bad(s) − F_good(s)|",
    "score": f"Score = {OFFSET:.2f} + {FACTOR:.4f} · ln((1 − p̂) / p̂)   (600 at 50:1, PDO 20)",
    "lift": "lift_b = bad rate_b / bad rate,  capture_b = Σ_{b′ ≤ b} bads_b′ / Σ bads",
    "psi": "PSI = CSI = Σᵢ (aᵢ − eᵢ) · ln(aᵢ / eᵢ)",
    "vintage": "V_c(k) = #{i ∈ c : MOB_default,i ≤ k} / n_c,  k ≤ K_c",
}


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def points(p: np.ndarray) -> np.ndarray:
    """The scorecard points of p̂ = P(bad): higher is safer."""
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return OFFSET + FACTOR * np.log((1 - p) / p)


def ks_stat(y: np.ndarray, p: np.ndarray) -> tuple[float, float | None]:
    """KS and the p̂ at which the gap is largest."""
    o = np.argsort(-p, kind="stable")
    yy, pp = y[o], p[o]
    nb, ng = yy.sum(), (1 - yy).sum()
    if nb == 0 or ng == 0:
        return float("nan"), None
    gap = np.abs(np.cumsum(yy) / nb - np.cumsum(1 - yy) / ng)
    i = int(np.argmax(gap))
    return float(gap[i]), float(pp[i])


def rank_stats(y: np.ndarray, p: np.ndarray, B: int = KS_BOOTSTRAP, seed: int = 2) -> dict[str, Any]:
    """n, bads, bad rate (Wilson), AUC and Gini with DeLong's 95 % interval, KS with a bootstrap interval."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    n, k = len(y), int(y.sum())
    out: dict[str, Any] = {"n": n, "bads": k, "bad_rate": k / n if n else None, "bad_rate_ci": wilson(k, n, Z, empty=None),
                           "mean_p": float(p.mean()) if n else None}
    if not (0 < k < n):
        out["note"] = "one class only: no rank ordering"
        return out
    d = delong(y, p)
    out.update(auc=d["value"], auc_ci=d["ci"], gini=2 * d["value"] - 1, gini_ci=[2 * d["ci"][0] - 1, 2 * d["ci"][1] - 1],
               gini_se=2 * d["se"])
    ks, at = ks_stat(y, p)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(B):
        idx = rng.integers(0, n, size=n)
        yb = y[idx]
        if 0 < yb.sum() < n:
            boots.append(ks_stat(yb, p[idx])[0])
    out.update(ks=ks, ks_at_p=at, ks_at_score=float(points(np.array([at]))[0]) if at is not None else None,
               ks_ci=[float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))] if boots else None, ks_B=len(boots))
    out["small"] = n < MIN_ROWS * 4 or k < 20
    return out


def band_edges(p_ref: np.ndarray) -> list[float]:
    """PDO-wide point bands covering the reference scores' 2nd … 98th percentiles; the ends are open."""
    s = points(p_ref)
    lo, hi = np.quantile(s, 0.02), np.quantile(s, 0.98)
    a = np.floor(lo / PDO) * PDO
    b = np.ceil(hi / PDO) * PDO
    return [float(x) for x in np.arange(a, b + PDO / 2, PDO)]


def _decision(p: np.ndarray, t: float | None) -> str | None:
    if t is None or not len(p):
        return None
    return "decline" if p.min() >= t else "approve" if p.max() < t else "mixed"


def band_table(y: np.ndarray, p: np.ndarray, edges: list[float], threshold: float | None) -> dict[str, Any]:
    """Score bands from the riskiest (lowest points) to the safest."""
    y = np.asarray(y).astype(int)
    s = points(p)
    idx = np.searchsorted(np.asarray(edges[1:-1]), s, side="right")  # band j: [e_j, e_{j+1}), ends open
    n_all, k_all = len(y), int(y.sum())
    rate = k_all / n_all if n_all else 0.0
    rows, cum_n, cum_k = [], 0, 0
    for j in range(len(edges) - 1):
        m = idx == j
        n, k = int(m.sum()), int(y[m].sum())
        cum_n += n
        cum_k += k
        lo = None if j == 0 else edges[j]
        hi = None if j == len(edges) - 2 else edges[j + 1]
        label = f"< {edges[j + 1]:.0f}" if lo is None else f"≥ {lo:.0f}" if hi is None else f"{lo:.0f}–{hi:.0f}"
        rows.append({"band": label, "score_lo": lo, "score_hi": hi, "n": n, "share": n / n_all if n_all else None,
                     "bads": k, "bad_rate": k / n if n else None, "bad_rate_ci": wilson(k, n, Z, empty=None),
                     "mean_p": float(p[m].mean()) if n else None,
                     "cum_share": cum_n / n_all if n_all else None, "cum_capture": cum_k / k_all if k_all else None,
                     "lift": (k / n) / rate if n and rate else None,
                     "cum_lift": (cum_k / k_all) / (cum_n / n_all) if cum_n and k_all else None,
                     "decision": _decision(p[m], threshold)})
    return {"rows": rows, "edges": edges, "n": n_all, "bads": k_all, "bad_rate": rate,
            "scale": {"pdo": PDO, "base_score": BASE_SCORE, "base_odds": BASE_ODDS, "factor": FACTOR, "offset": OFFSET},
            "threshold_score": float(points(np.array([threshold]))[0]) if threshold is not None else None}


def deciles(y: np.ndarray, p: np.ndarray, k: int = 10) -> list[dict[str, Any]]:
    """Ten equal-count groups by p̂, riskiest first."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    o = np.argsort(-p, kind="stable")
    parts = np.array_split(o, k)
    tot_k, tot_n = int(y.sum()), len(y)
    rate = tot_k / tot_n if tot_n else 0.0
    out, ck, cn = [], 0, 0
    for i, ix in enumerate(parts):
        n, b = len(ix), int(y[ix].sum())
        ck += b
        cn += n
        out.append({"decile": i + 1, "n": n, "bads": b, "bad_rate": b / n if n else None, "bad_rate_ci": wilson(b, n, Z, empty=None),
                    "mean_p": float(p[ix].mean()) if n else None, "p_min": float(p[ix].min()) if n else None,
                    "p_max": float(p[ix].max()) if n else None, "lift": (b / n) / rate if n and rate else None,
                    "cum_capture": ck / tot_k if tot_k else None, "cum_share": cn / tot_n if tot_n else None})
    return out


def csi_matrix(points_: list[dict[str, Any]]) -> dict[str, Any]:
    """Feature × period CSI from the batches' stored per-feature PSI, and each feature's first flagged period."""
    feats: dict[str, dict[str, float | None]] = {}
    periods = [p["period"] for p in points_]
    for p in points_:
        for f in p.get("features") or []:
            feats.setdefault(f["feature"], {})[p["period"]] = f.get("psi")
    rows = []
    for name, by in feats.items():
        vals = [by.get(per) for per in periods]
        first = next((per for per, v in zip(periods, vals) if v is not None and v > CSI_SHIFT), None)
        first_watch = next((per for per, v in zip(periods, vals) if v is not None and v > CSI_WATCH), None)
        flagged_after = bool(first) and all(v is not None and v > CSI_SHIFT for per, v in zip(periods, vals) if per >= first)
        rows.append({"feature": name, "csi": vals, "max": max((v for v in vals if v is not None), default=None),
                     "first_shift": first, "first_watch": first_watch, "stays_flagged": flagged_after})
    rows.sort(key=lambda r: -(r["max"] or 0))
    return {"periods": periods, "features": rows, "shift": CSI_SHIFT, "watch": CSI_WATCH}


def _month_index(d: pd.Series) -> np.ndarray:
    return (d.dt.year * 12 + d.dt.month - 1).to_numpy()


def vintage(tape: pd.DataFrame, cohort: str = "auto", max_mob: int = 24) -> dict[str, Any]:
    """tape: open (datetime), default (datetime or NaT), until (datetime). Cohorts by origination month or quarter."""
    t = tape.dropna(subset=["open"]).copy()
    if t.empty:
        return {"cohorts": [], "note": "no loans with an origination date"}
    om = _month_index(t["open"])
    dm = np.where(t["default"].notna(), _month_index(t["default"].fillna(t["open"])), -1)
    um = _month_index(t["until"].fillna(t["until"].max()))
    t["mob_default"] = np.where(dm >= 0, dm - om, np.nan)
    months = pd.PeriodIndex(t["open"], freq="M")
    if cohort == "auto":
        cohort = "quarter" if months.nunique() > 12 else "month"
    t["cohort"] = (pd.PeriodIndex(t["open"], freq="Q") if cohort == "quarter" else months).astype(str)
    t["om"], t["um"] = om, um
    out = []
    for c, g in t.groupby("cohort", sort=True):
        n = len(g)
        K = int(min(g["um"].min() - g["om"].max(), max_mob))
        if K < 1:
            continue
        mob = g["mob_default"].to_numpy()
        curve = [{"mob": k, "cum_bad_rate": float(np.sum(mob <= k) / n), "defaults": int(np.sum(mob <= k))} for k in range(0, K + 1)]
        out.append({"cohort": str(c), "n": n, "observed_mob": K, "curve": curve,
                    "bad_rate_12": next((x["cum_bad_rate"] for x in curve if x["mob"] == 12), None),
                    "defaults_seen": int(np.sum(~np.isnan(mob)))})
    return {"cohort_by": cohort, "cohorts": out, "max_mob": max_mob, "loans": int(len(t)),
            "as_of": str(t["until"].max().date()) if t["until"].notna().any() else None}


# --------------------------------------------------------------------------- export


def _x(v: Any, d: int = 3) -> str:
    f = _f(v)
    return "—" if f is None else f"{f:.{d}f}"


def _pc(v: Any, d: int = 1) -> str:
    f = _f(v)
    return "—" if f is None else f"{f * 100:.{d}f}%"


def _ci(c: Any, d: int = 3) -> str:
    return f"[{_x(c[0], d)}, {_x(c[1], d)}]" if c else "—"


def to_markdown(r: dict[str, Any]) -> str:
    """The "Model Performance Report": every table of the report, in Markdown (the HTML export renders this)."""
    det, h = r["detected"], r["overall"]["holdout"]
    L = [f"# {r['title']}", "",
         f"Positive class (bad): `{r['positive_class']}` · served model {r.get('served_model') or '—'} · decision threshold "
         f"t = {_x(r['threshold'], 4)} · stage {r['stage']} · run `{r['run_id']}`.", "",
         f"Detected inputs (G0 column roles): date `{(det.get('date') or {}).get('column') or '—'}`, id `{(det.get('id') or {}).get('column') or '—'}`, "
         f"label `{det['label']}`.", "",
         "## 1. Rank ordering", "", f"{FORMULAS['gini']}; {FORMULAS['ks']} (bootstrap 95 % CI, {KS_BOOTSTRAP} resamples).", "",
         "| Population | n | Bads | Bad rate | Gini | Gini 95% CI | KS | KS 95% CI |", "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |"]
    for name, s in (("Holdout", h), ("Labelled batches (pooled)", r["overall"].get("batches"))):
        if s:
            L.append(f"| {name} | {s['n']:,} | {s['bads']:,} | {_pc(s['bad_rate'])} | {_x(s.get('gini'))} | {_ci(s.get('gini_ci'))} | {_x(s.get('ks'))} | {_ci(s.get('ks_ci'))} |")
    L += ["", "### By month", "", "| Month | Source | n | Bads | Bad rate | Gini | Gini 95% CI | KS |", "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |"]
    for p in r["periods"]:
        L.append(f"| {p['period']} | {p['source']} | {p['n']:,} | {p['bads']:,} | {_pc(p['bad_rate'])} | {_x(p.get('gini'))} | {_ci(p.get('gini_ci'))} | {_x(p.get('ks'))} |")
    for p in r["pending"]:
        L.append(f"| {p['period']} | batch | {p['n']:,} | — | labels pending | — | — | — |")
    b = r["bands"]
    sc = b["scale"]
    L += ["", f"## 2. Score bands ({r['on']})", "", f"{FORMULAS['score']}; {FORMULAS['lift']}. Threshold t = {_x(r['threshold'], 4)} "
          f"≈ {_x(b.get('threshold_score'), 0)} points. Bands are {sc['pdo']:g} points wide, riskiest first.", "",
          "| Band | n | Share | Bads | Bad rate | Mean p̂ | Cum. capture | Cum. share | Lift | Cum. lift | Decision |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for x in b["rows"]:
        L.append(f"| {x['band']} | {x['n']:,} | {_pc(x['share'])} | {x['bads']:,} | {_pc(x['bad_rate'])} | {_x(x['mean_p'])} | "
                 f"{_pc(x['cum_capture'])} | {_pc(x['cum_share'])} | {_x(x['lift'], 2)} | {_x(x['cum_lift'], 2)} | {x['decision'] or '—'} |")
    L += ["", "### Bad rate by score decile", "", "| Decile | n | Bads | Bad rate | 95% CI | Mean p̂ | Lift | Cum. capture |",
          "| ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |"]
    for x in r["deciles"]:
        L.append(f"| {x['decile']} | {x['n']:,} | {x['bads']:,} | {_pc(x['bad_rate'])} | {_ci(x['bad_rate_ci'])} | {_x(x['mean_p'])} | {_x(x['lift'], 2)} | {_pc(x['cum_capture'])} |")
    st = r["stability"]
    L += ["", "## 3. Stability", "", f"{FORMULAS['psi']}. Bands: < {CSI_WATCH} stable, {CSI_WATCH}–{CSI_SHIFT} watch, > {CSI_SHIFT} shift. {st['reference']}.", ""]
    if st["score_psi"]:
        L += ["| Month | Rows | Mean p̂ | Score PSI |", "| --- | ---: | ---: | ---: |"]
        L += [f"| {x['period']} | {x['n'] or 0:,} | {_x(x['mean_p'])} | {_x(x['psi'])} |" for x in st["score_psi"]]
        cs = st["csi"]
        L += ["", "### CSI per feature per month", "", "| Feature | " + " | ".join(cs["periods"]) + " | First shift |",
              "| --- | " + " | ".join("---:" for _ in cs["periods"]) + " | --- |"]
        for f in cs["features"]:
            L.append(f"| {f['feature']} | " + " | ".join(_x(v, 2) + (" ⚑" if (v or 0) > CSI_SHIFT else "") for v in f["csi"]) + f" | {f['first_shift'] or '—'} |")
        if st.get("first_flag"):
            ff = st["first_flag"]
            L += ["", f"First flag: **{ff['feature']}** from {ff['period']}" + (" and every month after." if ff["stays_flagged"] else ".")]
    else:
        L.append("No scored batches yet: score PSI and CSI need batches (Registry → Batch & Drift or Monitoring).")
    L += ["", "## 4. Vintage curves", ""]
    vin = r.get("vintage")
    if vin and vin.get("cohorts"):
        ks = [k for k in (3, 6, 9, 12, 18, 24) if k <= vin["max_mob"]]
        src = vin["source"]
        L += [f"{FORMULAS['vintage']}. Cohorts by origination {vin['cohort_by']}; {vin['loans']:,} loans as of {vin['as_of']}; "
              f"tape: {src['kind']} ({src.get('file')}).", "",
              "| Cohort | Loans | Watched (MOB) | " + " | ".join(f"MOB {k}" for k in ks) + " |", "| --- | ---: | ---: | " + " | ".join("---:" for _ in ks) + " |"]
        for c in vin["cohorts"]:
            at = {x["mob"]: x["cum_bad_rate"] for x in c["curve"]}
            L.append(f"| {c['cohort']} | {c['n']:,} | {c['observed_mob']} | " + " | ".join(_pc(at.get(k)) if k in at else "·" for k in ks) + " |")
    else:
        L.append("No performance tape: upload one (an origination date and a default date per loan) in Registry → Credit Report.")
    L += ["", "_Generated by ENSEMBLE from the version's holdout predictions and its scored, labelled batches._", ""]
    return "\n".join(L)
