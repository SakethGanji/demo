"""Forecasting with AutoGluon's TimeSeriesPredictor: data loading, the fit in a worker process, and the results.

Data (long format, one row per item and time step): an item id column, a timestamp column, the target, known
covariates (values known in advance for the horizon: holidays, paydays, promotions), past covariates (any other
mapped numeric column, observed only up to now) and static features (constant per item: segment, region).
Rows at the end of a series with an empty target are the horizon's known covariates, used for the future forecast.

The fit (in `run`):
1. The last H = prediction_length steps of every series are held out. The predictor is fit on the rest;
   AutoGluon's own backtest windows (num_val_windows) are inside that, and model selection never sees the holdout.
2. leaderboard(full data) scores every model on the held-out window (score_test) next to its validation score.
3. The ensemble's weights, the saved backtest predictions per window (`backtest_predictions` / `backtest_targets`)
   scored per window, the holdout forecast per item with its errors, and the forecast past the end of the data
   (conditioned on the full history), with every quantile. Everything is written to the run folder.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..stats import wilson
from .. import frames

log = logging.getLogger("ensemble.forecast")

Emit = Callable[..., int]

DEFAULT_QUANTILES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

PRESETS = ["fast_training", "medium_quality", "high_quality", "best_quality", "bolt_tiny", "bolt_mini", "bolt_small",
           "bolt_base", "chronos2_small", "chronos2"]

PRESET_INFO = {
    "fast_training": "light zoo: SeasonalNaive, ETS, Theta, Recursive/DirectTabular, Chronos-2 small, Toto-2 4m",
    "medium_quality": "same as fast_training (AutoGluon's 'light' hyperparameters)",
    "high_quality": "default zoo: SeasonalNaive, AutoETS, DynamicOptimizedTheta, Recursive/DirectTabular, TFT, DeepAR, "
                    "Chronos-2 (+ fine-tuned small), Toto-2 22m",
    "best_quality": "default zoo with more backtest windows (num_val_windows='auto') and refits",
    "bolt_tiny": "Chronos-Bolt tiny (9M) zero-shot only: fastest foundation model on CPU",
    "bolt_mini": "Chronos-Bolt mini (21M) zero-shot",
    "bolt_small": "Chronos-Bolt small (48M) zero-shot",
    "bolt_base": "Chronos-Bolt base (205M) zero-shot: slow on CPU",
    "chronos2_small": "Chronos-2 small zero-shot (uses covariates)",
    "chronos2": "Chronos-2 zero-shot (120M, uses covariates): slow on CPU",
}

METRICS = {
    "MASE": "MASE = mean |eₜ| / ((1/(n−m)) Σₜ₌ₘ₊₁ⁿ |yₜ − yₜ₋ₘ|)",
    "WQL": "WQL = 2 Σ_τ Σₜ ρ_τ(yₜ − q̂_τ,ₜ) / (|T| Σₜ |yₜ|)",
    "SQL": "SQL = (1/|T|) Σ_τ mean ρ_τ(yₜ − q̂_τ,ₜ) / ((1/(n−m)) Σₜ₌ₘ₊₁ⁿ |yₜ − yₜ₋ₘ|)",
    "MAPE": "MAPE = mean |eₜ| / |yₜ|",
    "SMAPE": "sMAPE = mean 2|eₜ| / (|yₜ| + |ŷₜ|)",
    "WAPE": "WAPE = Σₜ |eₜ| / Σₜ |yₜ|",
    "RMSE": "RMSE = √(mean eₜ²)",
    "RMSSE": "RMSSE = √(mean eₜ² / ((1/(n−m)) Σₜ₌ₘ₊₁ⁿ (yₜ − yₜ₋ₘ)²))",
    "MAE": "MAE = mean |eₜ|",
    "MSE": "MSE = mean eₜ²",
    "RMSLE": "RMSLE = √(mean (ln(1 + ŷₜ) − ln(1 + yₜ))²)",
    "MQL": "MQL = Σ_τ mean ρ_τ(yₜ − q̂_τ,ₜ)",
    "BIAS": "BIAS = mean (ŷₜ − yₜ)",
}
QUANTILE_METRICS = {"WQL", "SQL", "MQL"}

FAMILIES = [
    ("ensemble", re.compile(r"Ensemble", re.I)),
    ("foundation", re.compile(r"Chronos|Toto|TabPFN|Moirai|TimesFM", re.I)),
    ("deep", re.compile(r"DeepAR|TemporalFusion|PatchTST|TiDE|DLinear|SimpleFeedForward|WaveNet", re.I)),
    ("tabular", re.compile(r"Tabular", re.I)),
    ("baseline", re.compile(r"^(Naive|SeasonalNaive|Average|SeasonalAverage|Zero)", re.I)),
    ("statistical", re.compile(r"ETS|ARIMA|Theta|NPTS|Croston|ADIDA|IMAPA", re.I)),
]


def family(model: str) -> str:
    for fam, rx in FAMILIES:
        if rx.search(model or ""):
            return fam
    return "other"


class ForecastError(ValueError):
    """Bad input; the message is shown to the user."""


# --------------------------------------------------------------------------- data


def read_long(cfg: dict[str, Any], df: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None, dict[str, Any]]:
    """(observed, future, static, info). `observed` has a target on every row; `future` holds rows past each item's
    last observation (their known covariates). Columns renamed to item_id / timestamp / target."""
    m = cfg["mapping"]
    if df is None:  # §6.5 R-E: the run's input through the typed reader (engine_v1: the time column stays a string)
        df = frames.read(frames.resolve(cfg))
    need = [m["item_id"], m["timestamp"], m["target"], *m.get("known_covariates", []), *m.get("static_features", []),
            *m.get("past_covariates", [])]
    missing = [c for c in need if c and c not in df.columns]
    if missing:
        raise ForecastError(f"column(s) {', '.join(missing)} not in the data")
    df = df.copy()
    if m.get("item_id"):
        df["__item"] = df[m["item_id"]].astype(str)
    else:
        df["__item"] = "series"
    df["__ts"] = pd.to_datetime(df[m["timestamp"]], errors="coerce")
    bad_ts = int(df["__ts"].isna().sum())
    df = df[df["__ts"].notna()]
    df["__y"] = pd.to_numeric(df[m["target"]], errors="coerce")
    df = df.sort_values(["__item", "__ts"]).drop_duplicates(["__item", "__ts"], keep="last")
    known = list(m.get("known_covariates") or [])
    past = list(m.get("past_covariates") or [])
    for c in known + past:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    last_obs = df[df["__y"].notna()].groupby("__item")["__ts"].max()
    df["__last"] = df["__item"].map(last_obs)
    future = df[df["__y"].isna() & (df["__ts"] > df["__last"])]
    observed = df[df["__ts"] <= df["__last"]]
    cols = {"__item": "item_id", "__ts": "timestamp", "__y": "target"}
    obs = observed[["__item", "__ts", "__y", *known, *past]].rename(columns=cols)
    fut = future[["__item", "__ts", *known]].rename(columns=cols)
    static = None
    statics = list(m.get("static_features") or [])
    not_constant = []
    if statics:
        g = df.groupby("__item")
        not_constant = [c for c in statics if (g[c].nunique(dropna=False) > 1).any()]
        static = g[statics].first().reset_index().rename(columns={"__item": "item_id"})
    info = {
        "rows": int(len(df)), "items": int(obs["item_id"].nunique()), "observed_rows": int(len(obs)),
        "future_rows": int(len(fut)), "dropped_bad_timestamps": bad_ts,
        "missing_target_inside": int(obs["target"].isna().sum()),
        "start": str(obs["timestamp"].min()), "end": str(obs["timestamp"].max()),
        "known_covariates": known, "past_covariates": past, "static_features": statics,
        "static_not_constant": not_constant,
        "length_min": int(obs.groupby("item_id").size().min()) if len(obs) else 0,
        "length_max": int(obs.groupby("item_id").size().max()) if len(obs) else 0,
    }
    return obs, fut, static, info


def to_tsdf(obs: pd.DataFrame, static: pd.DataFrame | None):
    from autogluon.timeseries import TimeSeriesDataFrame

    return TimeSeriesDataFrame.from_data_frame(obs, id_column="item_id", timestamp_column="timestamp",
                                               static_features_df=static)


def infer_freq(tsdf) -> str | None:
    try:
        return tsdf.infer_frequency(num_items=None)
    except Exception:  # noqa: BLE001
        try:
            return tsdf.freq
        except Exception:  # noqa: BLE001
            return None


def seasonal_period(freq: str | None) -> int:
    try:
        from autogluon.timeseries.utils.datetime import get_seasonality

        return int(get_seasonality(freq)) if freq else 1
    except Exception:  # noqa: BLE001
        return 1


def preview(input_ref: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """What the mapping gives: items, lengths, the inferred frequency, the season m, future rows, a few series."""
    obs, fut, static, info = read_long({"input_ref": input_ref, "mapping": mapping})
    if obs.empty:
        raise ForecastError("no rows with a timestamp and a target")
    tsdf = to_tsdf(obs, static)
    freq = infer_freq(tsdf)
    tail = obs.groupby("item_id").tail(60)
    series = {str(k): {"t": g["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").str.replace(" 00:00:00", "").tolist(),
                       "y": [None if pd.isna(v) else float(v) for v in g["target"]]}
              for k, g in list(tail.groupby("item_id"))[:12]}
    return {**info, "freq": freq, "seasonal_period": seasonal_period(freq), "series": series,
            "suggested_prediction_length": int(min(max(1, info["future_rows"] // max(info["items"], 1)) or 0, 10_000))
            or {"D": 28, "B": 20, "W": 8, "M": 6, "MS": 6, "h": 48, "H": 48, "Q": 4, "QS": 4}.get((freq or "").split("-")[0], 14)}


# --------------------------------------------------------------------------- progress from AutoGluon's log


class TSProgress(logging.Handler):
    """TimeSeriesPredictor has no callbacks; its trainer logs every model start, validation score and runtime.
    Those lines become model_started / model_finished / ensemble events."""

    START = re.compile(r"Training (?:timeseries|ensemble) model (\S+?)\.(?: Training for up to ([\d.]+)s)?")
    SCORE = re.compile(r"^\s*(-?[\d.]+(?:e-?\d+)?|nan)\s+= Validation score \((-?)(\w+)\)")
    RUNTIME = re.compile(r"^\s*([\d.]+)\s+s\s+= Training runtime")
    PRED = re.compile(r"^\s*([\d.]+)\s+s\s+= Validation \(prediction\) runtime")
    WEIGHTS = re.compile(r"Ensemble weights: (\{.*\})")
    FAILED = re.compile(r"(\S+) failed|Warning: Exception caused (\S+) to fail|Skipping (\S+)")

    def __init__(self, emit: Emit) -> None:
        super().__init__(level=logging.INFO)
        self._emit = emit
        self.current: dict[str, Any] | None = None
        self.models: dict[str, dict[str, Any]] = {}
        self.order = 0
        self.lines = 0

    def emit(self, record: logging.LogRecord) -> None:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        for line in msg.splitlines():
            self._line(line)
        if self.lines < 3000 and msg.strip():
            self.lines += 1
            self._emit("log", level=record.levelname.lower(), logger=record.name, message=msg.strip()[:1000])

    def _finish_current(self) -> None:
        cur = self.current
        if cur and not cur.get("finished") and cur.get("val_score") is not None:
            cur["finished"] = True
            cur["elapsed"] = time.time() - cur["t0"]
            self._emit("model_finished", **{k: v for k, v in cur.items() if k not in ("t0", "finished")})

    def _line(self, line: str) -> None:
        m = self.START.search(line)
        if m:
            self._finish_current()
            name = m.group(1)
            self.order += 1
            self.current = {"model": name, "family": family(name), "order": self.order, "t0": time.time(),
                            "time_limit": float(m.group(2)) if m.group(2) else None, "stack_level": 2 if "Ensemble" in name else 1}
            self.models[name] = self.current
            self._emit("model_started", model=name, family=family(name), order=self.order,
                       time_limit=self.current["time_limit"], stack_level=self.current["stack_level"])
            return
        cur = self.current
        if cur is None:
            return
        m = self.SCORE.search(line)
        if m:
            v = float(m.group(1))
            cur["val_score"] = v
            cur["val_score_natural"] = -v if m.group(2) == "-" else v
            cur["metric"] = m.group(3)
            return
        m = self.RUNTIME.search(line)
        if m:
            cur["fit_time"] = float(m.group(1))
            return
        m = self.PRED.search(line)
        if m:
            cur["predict_time"] = float(m.group(1))
            self._finish_current()
            return
        m = self.WEIGHTS.search(line)
        if m:
            try:
                w = json.loads(m.group(1).replace("'", '"'))
                cur["weights"] = w
                self._emit("ensemble", model=cur["model"], weights=w, stack_level=2)
            except Exception:  # noqa: BLE001
                pass
            return
        if "failed" in line.lower() or "error" in line.lower():
            if not cur.get("finished") and cur.get("val_score") is None:
                cur["finished"] = True
                self._emit("model_failed", model=cur["model"], family=cur["family"], error=line.strip()[:300])

    def close_out(self) -> None:
        self._finish_current()


# --------------------------------------------------------------------------- errors


def _scale(y_hist: np.ndarray, m: int) -> float:
    y = y_hist[~np.isnan(y_hist)]
    if len(y) <= m:
        return float("nan")
    d = np.abs(y[m:] - y[:-m])
    return float(np.mean(d)) if len(d) else float("nan")


def pinball(y: np.ndarray, q: np.ndarray, tau: float) -> np.ndarray:
    e = y - q
    return np.maximum(tau * e, (tau - 1) * e)


def item_errors(hist: dict[str, np.ndarray], actual: pd.DataFrame, pred: pd.DataFrame, quantiles: list[float],
                m: int) -> list[dict[str, Any]]:
    """Per item on the forecast window: MASE (scaled by the in-sample seasonal naive error), WQL, sMAPE, bias, and the
    empirical coverage of the central interval [q_lo, q_hi]."""
    out = []
    qlo, qhi = min(quantiles), max(quantiles)
    for item, a in actual.groupby(level=0):
        if item not in pred.index.get_level_values(0):
            continue
        p = pred.loc[item]
        y = a.droplevel(0)["target"].reindex(p.index).to_numpy(dtype=float)
        mean = p["mean"].to_numpy(dtype=float)
        ok = ~np.isnan(y)
        if not ok.any():
            continue
        y, mean = y[ok], mean[ok]
        e = y - mean
        scale = _scale(hist.get(item, np.array([])), m)
        wql_num = sum(pinball(y, p[str(t)].to_numpy(dtype=float)[ok], t).sum() for t in quantiles)
        lo, hi = p[str(qlo)].to_numpy(dtype=float)[ok], p[str(qhi)].to_numpy(dtype=float)[ok]
        out.append({
            "item_id": str(item), "n": int(ok.sum()),
            "MASE": float(np.mean(np.abs(e)) / scale) if scale and scale == scale and scale > 0 else None,
            "WQL": float(2 * wql_num / (len(quantiles) * np.abs(y).sum())) if np.abs(y).sum() > 0 else None,
            "sMAPE": float(np.mean(2 * np.abs(e) / np.maximum(np.abs(y) + np.abs(mean), 1e-9))),
            "MAPE": float(np.mean(np.abs(e) / np.maximum(np.abs(y), 1e-9))),
            "RMSE": float(np.sqrt(np.mean(e ** 2))), "bias": float(np.mean(mean - y)),
            "coverage": float(np.mean((y >= lo) & (y <= hi))), "interval": [qlo, qhi], "scale": scale,
            "mean_actual": float(np.mean(y)),
        })
    out.sort(key=lambda r: -(r["MASE"] if r["MASE"] is not None else -1))
    return out


def calibration(actual: pd.DataFrame, pred: pd.DataFrame, quantiles: list[float],
                backtests: dict[str, Any] | None = None) -> dict[str, Any]:
    """Is the fan honest? Pooled over every series and step of the holdout window: F̂(τ) = share of actuals at or
    below q̂_τ for each quantile level (calibrated: F̂(τ) ≈ τ), and for each symmetric central interval [q_lo, q_hi]
    its empirical coverage against hi − lo, with a 95% Wilson interval, the probability that a calibrated interval
    would cover at least as many points (binomial upper tail), and its mean width over the mean actual."""
    from scipy.stats import binom

    ys, qs = [], {t: [] for t in quantiles}
    for item, a in actual.groupby(level=0):
        if item not in pred.index.get_level_values(0):
            continue
        p = pred.loc[item]
        y = a.droplevel(0)["target"].reindex(p.index).to_numpy(dtype=float)
        ok = ~np.isnan(y)
        ys.append(y[ok])
        for t in quantiles:
            qs[t].append(p[str(t)].to_numpy(dtype=float)[ok])
    if not ys:
        return {}
    y = np.concatenate(ys)
    Q = {t: np.concatenate(v) for t, v in qs.items()}
    n = int(len(y))
    levels = []
    for t in sorted(quantiles):
        k = int((y <= Q[t]).sum())
        levels.append({"tau": t, "below": k / n, "n": n, "ci": wilson(k, n, empty=[0.0, 1.0])})
    qsorted = sorted(quantiles)
    intervals = []
    for lo in qsorted:
        hi = round(1 - lo, 6)
        if lo >= 0.5 or hi not in [round(x, 6) for x in qsorted]:
            continue
        hi_key = next(x for x in qsorted if round(x, 6) == hi)
        inside = int(((y >= Q[lo]) & (y <= Q[hi_key])).sum())
        nominal = hi_key - lo
        tail = float(binom.sf(inside - 1, n, nominal)) if inside > nominal * n else float(binom.cdf(inside, n, nominal))
        intervals.append({"lo": lo, "hi": hi_key, "nominal": nominal, "empirical": inside / n, "inside": inside, "n": n,
                          "ci": wilson(inside, n, empty=[0.0, 1.0]), "p_all_inside": tail,
                          "width_rel": float(np.mean(Q[hi_key] - Q[lo]) / max(np.mean(np.abs(y)), 1e-9))})
    intervals.sort(key=lambda r: -r["nominal"])
    main = intervals[0] if intervals else None
    verdict = "calibrated"
    if main and not (main["ci"][0] <= main["nominal"] <= main["ci"][1]):
        verdict = "too_wide" if main["empirical"] > main["nominal"] else "too_narrow"
    bt = []
    for model, ws in ((backtests or {}).get("models") or {}).items():
        cov = [w.get("coverage") for w in ws if w.get("coverage") is not None]
        bt.append({"model": model, "nominal": main["nominal"] if main else None, "empirical": float(np.mean(cov)) if cov else None})
    return {"n": n, "levels": levels, "intervals": intervals, "verdict": verdict, "backtest": bt}


# --------------------------------------------------------------------------- ensembles

# The ensemble choices offered on top of AutoGluon's default. Keys go to fit(ensemble_hyperparameters=...).
ENSEMBLES = {
    "greedy": {"label": "Greedy (Caruana) on eval_metric", "hp": None,
               "info": "AutoGluon's default WeightedEnsemble: greedy selection minimising the eval_metric on the backtests"},
    "quantile": {"label": "Quantile-aware (pinball loss, a weight per model and quantile)",
                 "hp": {"LinearStacker": {"weights_per": "mq"}},
                 "info": "LinearStackerEnsemble(weights_per='mq'): softmax weights w_{m,τ} per model and quantile, fitted "
                         "by minimising the mean pinball loss ρ_τ over the backtest windows"},
    "both": {"label": "Both; AutoGluon keeps the better on validation",
             "hp": {"Greedy": {}, "LinearStacker": {"weights_per": "mq"}},
             "info": "fits the greedy and the quantile-aware ensemble; model_best is the one with the better validation score"},
}


def patch_array_ensembles() -> None:
    """AutoGluon 1.6.3 bug: the array-based ensembles (LinearStacker, Median, Tabular) build their labels from every
    column of the ground-truth windows, so with known covariates the label array has 1 + |x| columns and the fit fails
    ('size of tensor a (3) must match … b (8)'). Give them the target column only. Idempotent."""
    try:
        from autogluon.timeseries.models.ensemble.array_based.abstract import ArrayBasedTimeSeriesEnsembleModel as A
    except Exception:  # noqa: BLE001
        return
    if getattr(A._fit, "_target_only", False):
        return
    orig = A._fit

    def _fit(self, predictions_per_window, data_per_window, model_scores=None, time_limit=None):
        data_per_window = [d[[self.target]] if d.shape[1] > 1 else d for d in data_per_window]
        return orig(self, predictions_per_window, data_per_window, model_scores, time_limit)

    _fit._target_only = True  # type: ignore[attr-defined]
    A._fit = _fit


def ensemble_weights(mdl, quantiles: list[float]) -> tuple[dict[str, float] | None, dict[str, dict[str, float]] | None]:
    """(w_m, w_{m,τ}) of an ensemble model: model_to_weight for the greedy kind; for LinearStacker the softmax weights,
    averaged over outputs for w_m and per quantile level for w_{m,τ} (column 'mean' = the mean forecast)."""
    w = getattr(mdl, "model_to_weight", None)
    if w:
        return {k: float(v) for k, v in sorted(w.items(), key=lambda kv: -kv[1])}, None
    reg = getattr(mdl, "ensemble_regressor", None)
    arr = getattr(reg, "weights", None)
    names = list(getattr(mdl, "_model_names", None) or [])
    if arr is None or not names:
        return None, None
    a = np.asarray(arr, dtype=float)
    a = a.reshape(-1, a.shape[-2], a.shape[-1]).mean(axis=0)  # (outputs, models); 'm' has outputs = 1
    wm = a.mean(axis=0)
    w_m = {n: float(v) for n, v in sorted(zip(names, wm), key=lambda kv: -kv[1])}
    per_q = None
    if a.shape[0] == len(quantiles) + 1:
        per_q = {lab: {n: float(v) for n, v in zip(names, a[i])} for i, lab in enumerate(["mean", *[str(q) for q in quantiles]])}
    return w_m, per_q


# --------------------------------------------------------------------------- conformal recalibration

CONFORMAL_FILE = "ensemble_conformal.json"  # inside the predictor folder, so a registry version (a copy of it) keeps it


def _center_col(quantiles: list[float]) -> str:
    """The band's centre: the median column when 0.5 is a quantile level, else the mean."""
    return next((str(t) for t in quantiles if abs(t - 0.5) < 1e-9), "mean")


def _order_stat(r: np.ndarray, level: float) -> tuple[float | None, int]:
    """Split-conformal quantile: the ⌈(n + 1)·level⌉-th smallest score (None when n is too small for that level)."""
    n = len(r)
    k = int(math.ceil((n + 1) * level))
    if n == 0 or k > n:
        return None, k
    return float(np.sort(r)[max(k, 1) - 1]), k


def conformal_fit(preds: list, targets: list, quantiles: list[float], scope: str = "pooled",
                  min_item_points: int = 60) -> dict[str, Any]:
    """Scale factors c_τ per quantile level from backtest windows (split-conformal, CQR-style scaling about the median).

    For every backtest point with centre m = q̂₀.₅ (or the mean), forecast quantile q̂_τ and actual y, the score is
    r = (y − m) / (q̂_τ − m): how many band half-widths away from the centre the actual landed. The recalibrated quantile
    is q̃_τ = m + c_τ (q̂_τ − m). For τ > ½, y ≤ q̃_τ ⟺ r ≤ c_τ, so c_τ = the ⌈(n+1)τ⌉-th smallest r; for τ < ½,
    y ≤ q̃_τ ⟺ r ≥ c_τ, so c_τ = the ⌈(n+1)(1−τ)⌉-th smallest r. Then P(y ≤ q̃_τ) ≥ τ above the median and ≤ τ below
    it, so [q̃_lo, q̃_hi] covers at least hi − lo of exchangeable new points. c_τ < 1 shrinks a band that was too wide,
    c_τ > 1 widens one too narrow; c_τ ≥ 0 so a quantile never crosses the centre. scope 'item' fits c_τ per series
    where it has at least `min_item_points` scores and falls back to the pooled c_τ elsewhere."""
    rows = []
    for p, tgt in zip(preds, targets):
        df = p.to_data_frame() if hasattr(p, "to_data_frame") else p
        y = tgt["target"].reindex(df.index)
        part = df.copy()
        part["__y"] = y.to_numpy(dtype=float)
        rows.append(part)
    if not rows:
        return {"error": "no backtest predictions"}
    allp = pd.concat(rows)
    allp = allp[allp["__y"].notna()]
    centre = _center_col(quantiles)
    m = allp[centre].to_numpy(dtype=float)
    y = allp["__y"].to_numpy(dtype=float)
    items = allp.index.get_level_values(0).astype(str).to_numpy()

    def fit_one(mask: np.ndarray) -> dict[str, Any]:
        out = {}
        for t in quantiles:
            if str(t) == centre:
                continue
            d = allp[str(t)].to_numpy(dtype=float)[mask] - m[mask]
            ok = np.abs(d) > 1e-9 * (np.abs(m[mask]) + 1)
            r = (y[mask][ok] - m[mask][ok]) / d[ok]
            level = t if t > 0.5 else 1 - t
            c, k = _order_stat(r, level)
            out[str(t)] = {"c": None if c is None else max(0.0, c), "n": int(ok.sum()), "k": k,
                           "raw_below": float(np.mean(y[mask] <= allp[str(t)].to_numpy(dtype=float)[mask])) if mask.any() else None}
        return out

    pooled = fit_one(np.ones(len(y), dtype=bool))
    per_item = {}
    if scope == "item":
        for it in np.unique(items):
            mask = items == it
            if mask.sum() >= min_item_points:
                per_item[it] = {k: v["c"] for k, v in fit_one(mask).items()}
    return {"method": "split-conformal scaling about the centre (CQR-style), one c_τ per quantile level",
            "scope": scope, "centre": centre, "quantiles": quantiles, "n_scores": int(len(y)),
            "windows": len(preds), "items": int(len(np.unique(items))),
            "factors": {k: v["c"] for k, v in pooled.items()}, "levels": pooled, "per_item": per_item}


def conformal_apply(pred, conf: dict[str, Any] | None):
    """q̃_τ = m + c_τ (q̂_τ − m) for every quantile column, per item when fitted per item; then the quantiles are sorted
    per row (rearrangement) so they never cross. The mean and the centre column are unchanged."""
    if not conf or not conf.get("factors"):
        return pred
    out = pred.copy()
    centre = conf["centre"]
    m = out[centre].to_numpy(dtype=float)
    items = out.index.get_level_values(0).astype(str).to_numpy()
    qcols = [c for c in out.columns if c != "mean"]
    for col, c in conf["factors"].items():
        if col not in out.columns:
            continue
        cvec = np.full(len(out), np.nan if c is None else float(c))
        for it, fac in (conf.get("per_item") or {}).items():
            if fac.get(col) is not None:
                cvec[items == it] = fac[col]
        q = out[col].to_numpy(dtype=float)
        out[col] = np.where(np.isnan(cvec), q, m + cvec * (q - m))
    qs = sorted(qcols, key=float)
    out[qs] = np.sort(out[qs].to_numpy(dtype=float), axis=1)
    return out


def auto_windows(min_len: int, H: int, items: int) -> tuple[int, str]:
    """num_val_windows when none is given: AutoGluon's own 'auto' recommendation (5 windows under 20 series, else 3
    or 2) with a floor of 3, capped by what the shortest series allows: n_min ≥ H(W + 2) + 1 (W windows, the holdout
    and at least H + 1 steps to train on)."""
    rec = 5 if items < 20 else 3
    cap = max(1, (min_len - 1) // H - 2)
    w = max(1, min(rec, cap))
    why = (f"auto: {rec} for {items} series (AutoGluon's recommendation, at least 3)"
           + (f", capped at {cap} by the shortest series (n_min = {min_len} ≥ H(W + 2) + 1)" if cap < rec else ""))
    return w, why


def wql(actual, pred, quantiles: list[float]) -> float:
    """WQL = 2 Σ_τ Σ_t ρ_τ(y_t − q̂_τ,t) / (|T| Σ_t |y_t|), pooled over every series and step (AutoGluon's definition)."""
    y = actual["target"].reindex(pred.index).to_numpy(dtype=float)
    ok = ~np.isnan(y)
    num = sum(pinball(y[ok], pred[str(t)].to_numpy(dtype=float)[ok], t).sum() for t in quantiles)
    den = len(quantiles) * np.abs(y[ok]).sum()
    return float(2 * num / den) if den > 0 else float("nan")


def model_coverage(predictor, train, kc_h, actual, quantiles: list[float], backtests: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every model's coverage of the widest central interval [q_lo, q_hi]: on the holdout (its own forecast from the
    training data, with a 95% Wilson interval) and averaged over the backtest windows. AutoGluon's raw quantiles."""
    lo, hi = min(quantiles), max(quantiles)
    nominal = hi - lo
    out = []
    bt = (backtests or {}).get("models") or {}
    for name in predictor.model_names():
        try:
            p = predictor.predict(train, known_covariates=kc_h, model=name)
        except Exception as e:  # noqa: BLE001
            out.append({"model": name, "error": f"{type(e).__name__}: {e}"})
            continue
        y = actual["target"].reindex(p.index).to_numpy(dtype=float)
        ok = ~np.isnan(y)
        inside = int(((y[ok] >= p[str(lo)].to_numpy(dtype=float)[ok]) & (y[ok] <= p[str(hi)].to_numpy(dtype=float)[ok])).sum())
        n = int(ok.sum())
        bcov = [w.get("coverage") for w in bt.get(name, []) if w.get("coverage") is not None]
        out.append({"model": name, "family": family(name), "lo": lo, "hi": hi, "nominal": nominal, "inside": inside, "n": n,
                    "empirical": inside / n if n else None, "ci": wilson(inside, n, empty=[0.0, 1.0]),
                    "backtest": float(np.mean(bcov)) if bcov else None})
    return out


def load_conformal(predictor_path: str) -> dict[str, Any] | None:
    p = Path(predictor_path) / CONFORMAL_FILE
    return json.loads(p.read_text()) if p.exists() else None


def _fmt_ts(idx) -> list[str]:
    s = pd.DatetimeIndex(idx).strftime("%Y-%m-%d %H:%M:%S")
    return [x.replace(" 00:00:00", "") for x in s]


def chart_payload(full, holdout_pred, future_pred, quantiles: list[float], history: int = 120,
                  raw: tuple | None = None) -> dict[str, Any]:
    """Per item: the last `history` observed points, the holdout window's actuals with the forecast made without
    them, and the future forecast; quantiles as columns 'q0.1' … plus the mean. With `raw` (holdout, future) before
    conformal recalibration, each also carries `q_raw`."""
    items = {}
    for item in full.item_ids:
        s = full.loc[item]["target"]
        h = s.iloc[-history:]
        rec: dict[str, Any] = {"history": {"t": _fmt_ts(h.index), "y": [None if pd.isna(v) else float(v) for v in h]}}
        for key, pred in (("holdout", holdout_pred), ("future", future_pred)):
            if pred is None or item not in pred.index.get_level_values(0):
                continue
            p = pred.loc[item]
            rec[key] = {"t": _fmt_ts(p.index), "mean": [float(v) for v in p["mean"]],
                        "q": {str(t): [float(v) for v in p[str(t)]] for t in quantiles}}
            if raw is not None:
                r = raw[0 if key == "holdout" else 1].loc[item]
                rec[key]["q_raw"] = {str(t): [float(v) for v in r[str(t)]] for t in quantiles}
            if key == "holdout":
                rec[key]["actual"] = [None if pd.isna(v) else float(v) for v in s.reindex(p.index)]
        items[str(item)] = rec
    return {"items": items, "quantiles": quantiles}


# --------------------------------------------------------------------------- the fit


def resolve(cfg: dict[str, Any], freq: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    pk = {"target": "target", "prediction_length": int(cfg["prediction_length"]), "freq": cfg.get("freq") or freq,
          "eval_metric": cfg.get("eval_metric") or "MASE",
          "quantile_levels": cfg.get("quantile_levels") or DEFAULT_QUANTILES,
          "known_covariates_names": list(cfg["mapping"].get("known_covariates") or []) or None,
          **(cfg.get("predictor_kwargs") or {})}
    if cfg.get("eval_metric_seasonal_period"):
        pk["eval_metric_seasonal_period"] = int(cfg["eval_metric_seasonal_period"])
    fk: dict[str, Any] = {}
    if cfg.get("presets"):
        fk["presets"] = cfg["presets"]
    if cfg.get("time_limit") is not None:
        fk["time_limit"] = cfg["time_limit"]
    if cfg.get("num_val_windows") is not None:
        fk["num_val_windows"] = cfg["num_val_windows"]
    if cfg.get("hyperparameters") is not None:
        fk["hyperparameters"] = cfg["hyperparameters"]
    ens = ENSEMBLES.get(cfg.get("ensemble") or "greedy") or ENSEMBLES["greedy"]
    if ens["hp"] is not None:
        fk["ensemble_hyperparameters"] = json.loads(json.dumps(ens["hp"]))
    fk["random_seed"] = cfg.get("random_seed", 123)
    fk.update(cfg.get("fit_kwargs") or {})
    return pk, fk


def future_covariates(predictor, full, fut: pd.DataFrame, known: list[str], m: int) -> tuple[Any, str]:
    """The horizon's known covariates: the data's own future rows when they cover it, else each covariate's value one
    season (m steps) before, said so in the result."""
    if not known:
        return None, "none"
    from autogluon.timeseries import TimeSeriesDataFrame

    frame = predictor.make_future_data_frame(full)
    given = fut.set_index(["item_id", "timestamp"])[known] if len(fut) else None
    merged = frame.set_index(["item_id", "timestamp"])
    if given is not None:
        merged = merged.join(given, how="left")
    else:
        for c in known:
            merged[c] = np.nan
    source = "data"
    if merged[known].isna().any().any():
        source = "data + one season back" if given is not None else f"one season back (m = {m})"
        for item, g in merged.groupby(level=0):
            hist = full.loc[item]
            for c in known:
                vals = hist[c].to_numpy()
                need = g[c].isna().to_numpy()
                if need.any():
                    filled = g[c].to_numpy().copy()
                    for j in np.where(need)[0]:
                        k = len(vals) - m + (j % m) if len(vals) >= m else len(vals) - 1
                        filled[j] = vals[k] if 0 <= k < len(vals) else 0
                    merged.loc[item, c] = filled
    return TimeSeriesDataFrame(merged.reset_index().set_index(["item_id", "timestamp"])), source


def run(cfg: dict[str, Any], run_dir: Path, emit: Emit, merge: Callable[..., Any]) -> dict[str, Any]:
    from autogluon.timeseries import TimeSeriesPredictor

    obs, fut, static, info = read_long(cfg)
    if obs.empty:
        raise ForecastError("no rows with a timestamp and a target")
    full = to_tsdf(obs, static)
    freq = cfg.get("freq") or infer_freq(full)
    if freq is None:
        raise ForecastError("could not infer a regular frequency; give freq (e.g. 'D', 'W', 'h')")
    if cfg.get("freq") or full.freq is None:
        full = full.convert_frequency(freq)
    H = int(cfg["prediction_length"])
    m = int(cfg.get("eval_metric_seasonal_period") or seasonal_period(freq))
    min_len = int(full.num_timesteps_per_item().min())
    nvw_rule = None
    if cfg.get("num_val_windows") in (None, "auto"):
        nvw, nvw_rule = auto_windows(min_len, H, int(full.num_items))
        cfg = {**cfg, "num_val_windows": nvw}
    nvw = int(cfg.get("num_val_windows") or 1)
    if min_len < H * (nvw + 2) + 1:
        raise ForecastError(f"the shortest series has {min_len} steps; with prediction_length {H} and "
                            f"{nvw} backtest window(s) it needs at least {H * (nvw + 2) + 1}")
    train = full.slice_by_timestep(None, -H)
    info.update({"num_val_windows": nvw, "num_val_windows_rule": nvw_rule,
                 "freq": freq, "seasonal_period": m, "prediction_length": H, "train_end": str(train.index.get_level_values(1).max()),
                 "holdout_start": str(full.slice_by_timestep(-H, None).index.get_level_values(1).min()),
                 "train_rows": int(len(train)), "holdout_rows": int(len(full) - len(train))})
    emit("data_ready", **info)
    merge(data=info)

    pk, fk = resolve(cfg, freq)
    patch_array_ensembles()
    quantiles = [float(q) for q in pk["quantile_levels"]]
    conf_on = bool(cfg.get("conformal", False))
    conf_scope = cfg.get("conformal_scope") or "pooled"
    resolved = {"predictor_kwargs": pk, "fit_kwargs": fk, "path": cfg["predictor_path"],
                "call": "TimeSeriesPredictor(**predictor_kwargs).fit(train_data, **fit_kwargs)",
                "notes": [f"the last {H} steps of every series held out; fit on the rest",
                          f"seasonal period m = {m} (from freq '{freq}')"]
                         + ([f"num_val_windows = {nvw}: {nvw_rule}"] if nvw_rule else [])
                         + [f"ensemble: {(ENSEMBLES.get(cfg.get('ensemble') or 'greedy') or ENSEMBLES['greedy'])['info']}"]
                         + ([f"conformal recalibration ({conf_scope}) of the best model's quantiles from its backtest forecasts"]
                            if conf_on else ["no conformal recalibration: the quantiles are AutoGluon's"])}
    merge(resolved=resolved)
    emit("config_resolved", **resolved)

    prog = TSProgress(emit)
    ag = logging.getLogger("autogluon")
    ag.addHandler(prog)
    ag.setLevel(logging.INFO)
    emit("fit_started", problem_type="forecast", eval_metric=pk["eval_metric"], greater_is_better=False,
         time_limit=fk.get("time_limit"), prediction_length=H)
    predictor = TimeSeriesPredictor(path=cfg["predictor_path"], verbosity=2, **pk)
    t0 = time.time()
    predictor.fit(train, **fk)
    prog.close_out()
    ag.removeHandler(prog)
    fit_seconds = time.time() - t0
    best = predictor.model_best

    weights = {}
    ens = []
    for name in predictor.model_names():
        if "Ensemble" not in name:
            continue
        try:
            mdl = predictor._trainer.load_model(name)
        except Exception:  # noqa: BLE001
            continue
        w, per_q = ensemble_weights(mdl, quantiles)
        if w:
            weights[name] = w
            ens.append({"model": name, "weights": w, "weights_per_quantile": per_q, "kind": type(mdl).__name__,
                        "best": name == best, "stack_level": 2})
    emit("fit_finished", best_model=best, fit_seconds=fit_seconds, n_models=len(predictor.model_names()),
         ensemble=next((e for e in ens if e["best"]), ens[0] if ens else None))

    lb = predictor.leaderboard(full, silent=True)
    sign = -1  # AutoGluon timeseries scores are higher-is-better: errors are negated
    rows = []
    for rank, r in enumerate(lb.to_dict(orient="records"), start=1):
        name = r["model"]
        mi = prog.models.get(name, {})
        rows.append({
            "rank": rank, "model": name, "family": family(name), "score_val": r.get("score_val"),
            "score_test": r.get("score_test"),
            "score_val_natural": None if pd.isna(r.get("score_val")) else sign * float(r["score_val"]),
            "score_test_natural": None if pd.isna(r.get("score_test")) else sign * float(r["score_test"]),
            "fit_time": r.get("fit_time_marginal"), "pred_time_val": r.get("pred_time_val"),
            "pred_time_test": r.get("pred_time_test"), "fit_order": r.get("fit_order"),
            "stack_level": 2 if name in weights else 1, "weights": weights.get(name), "elapsed": mi.get("elapsed"),
            "best": name == best,
        })
    merge(leaderboard=rows, best_model=best, fit_seconds=fit_seconds, ensembles=ens,
          ensemble=next((e for e in ens if e["best"]), None))
    emit("leaderboard", rows=rows)

    # backtest windows: AutoGluon's saved validation predictions, scored per window and model
    emit("explaining", what="backtests")
    backtests = backtest_table(predictor, train, quantiles, m)
    merge(backtests=backtests)

    # holdout window per item (best model, conditioned on train only, known covariates of the window)
    emit("explaining", what="holdout")
    known = list(cfg["mapping"].get("known_covariates") or [])
    kc_h = full.slice_by_timestep(-H, None)[known] if known else None
    hold_raw = predictor.predict(train, known_covariates=kc_h, model=best)
    hist = {it: train.loc[it]["target"].to_numpy(dtype=float) for it in train.item_ids}
    actual = full.slice_by_timestep(-H, None)

    # conformal recalibration: c_τ from the best model's own backtest forecasts (never the holdout)
    conf = None
    if conf_on:
        emit("explaining", what="conformal recalibration")
        try:
            conf = conformal_fit(predictor.backtest_predictions(model=best), predictor.backtest_targets(), quantiles,
                                 scope=conf_scope)
            conf["model"] = best
            if conf.get("error"):
                conf = {"error": conf["error"]}
            else:
                (Path(cfg["predictor_path"]) / CONFORMAL_FILE).write_text(json.dumps(conf, indent=1))
        except Exception as e:  # noqa: BLE001
            conf = {"error": f"{type(e).__name__}: {e}"}
    usable = conf if conf and not conf.get("error") else None
    hold_pred = conformal_apply(hold_raw, usable)

    per_item = item_errors(hist, actual, hold_pred, quantiles, m)
    ev = predictor.evaluate(full, model=best, metrics=["MASE", "WQL", "SMAPE", "MAPE", "RMSE", "WAPE"])
    metrics_raw = {k: -float(v) for k, v in ev.items()}
    hold_metrics = dict(metrics_raw)
    if usable:  # point metrics are unchanged (the mean and the median are not moved); the quantile loss is recomputed
        hold_metrics["WQL"] = wql(actual, hold_pred, quantiles)
    cov = [r["coverage"] for r in per_item]
    cal_raw = calibration(actual, hold_raw, quantiles, backtests)
    cal = calibration(actual, hold_pred, quantiles, backtests) if usable else cal_raw
    per_model = model_coverage(predictor, train, kc_h, actual, quantiles, backtests)
    cal["per_model"] = cal_raw["per_model"] = per_model
    for r in rows:
        pm = next((x for x in per_model if x["model"] == r["model"]), None)
        r["coverage_test"] = pm["empirical"] if pm else None
        r["coverage_val"] = pm["backtest"] if pm else None
    merge(leaderboard=rows)
    holdout = {"model": best, "metrics": hold_metrics, "metrics_raw": metrics_raw, "eval_metric": pk["eval_metric"],
               "coverage": float(np.mean(cov)) if cov else None, "interval": [min(quantiles), max(quantiles)],
               "n_items": len(per_item), "horizon": H, "items": per_item,
               "calibration": cal, "calibration_raw": cal_raw, "conformal": conf,
               "recalibrated": usable is not None}
    merge(holdout=holdout)
    emit("evaluation", model=best, metrics=hold_metrics, coverage=holdout["coverage"])

    # the forecast past the end of the data
    emit("explaining", what="forecast")
    kc_f, kc_source = future_covariates(predictor, full, fut, known, m)
    fpred_raw = predictor.predict(full, known_covariates=kc_f, model=best)
    fpred = conformal_apply(fpred_raw, usable)
    fpred.to_data_frame().reset_index().to_csv(run_dir / "forecast.csv", index=False)
    hold_pred.to_data_frame().reset_index().to_csv(run_dir / "holdout_forecast.csv", index=False)
    if usable:  # both versions are kept: AutoGluon's quantiles and the recalibrated ones
        fpred_raw.to_data_frame().reset_index().to_csv(run_dir / "forecast_raw.csv", index=False)
        hold_raw.to_data_frame().reset_index().to_csv(run_dir / "holdout_forecast_raw.csv", index=False)
    chart = chart_payload(full, hold_pred, fpred, quantiles, raw=(hold_raw, fpred_raw) if usable else None)
    (run_dir / "chart.json").write_text(json.dumps(chart))
    fc = {"model": best, "horizon": H, "start": str(fpred.index.get_level_values(1).min()),
          "end": str(fpred.index.get_level_values(1).max()), "rows": int(len(fpred)),
          "known_covariates_source": kc_source, "quantiles": quantiles, "recalibrated": usable is not None}
    merge(forecast=fc)
    emit("forecast_ready", **fc)

    summary = {"problem_type": "forecast", "eval_metric": {"name": pk["eval_metric"], "greater_is_better": False,
                                                           "formula": METRICS.get(pk["eval_metric"])},
               "features": known + list(cfg["mapping"].get("past_covariates") or []),
               "classes": None, "positive_class": None}
    merge(**summary)
    return {"best_model": best, "score_test": rows[0].get("score_test") if rows else None}


def backtest_table(predictor, train, quantiles: list[float], m: int) -> dict[str, Any]:
    """AutoGluon's backtest windows from training: for every model and window, MASE and WQL of its saved
    predictions against the window's targets (the windows sit inside the training data, before the holdout)."""
    out: dict[str, Any] = {"windows": [], "models": {}}
    try:
        models = [n for n in predictor.model_names()]
        preds = predictor.backtest_predictions(model=models)
        targets = predictor.backtest_targets()
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    for w, tgt in enumerate(targets):
        some = next(iter(preds.values()))[w] if preds else None
        if some is None:
            continue
        ts = some.index.get_level_values(1)
        out["windows"].append({"window": w + 1, "start": str(ts.min()), "end": str(ts.max())})
    for name, plist in preds.items():
        scores = []
        for w, p in enumerate(plist):
            tgt = targets[w]
            hist = {}
            for it in p.item_ids:
                start = p.loc[it].index.min()
                h = tgt.loc[it]["target"] if it in tgt.item_ids else pd.Series(dtype=float)
                hist[it] = h[h.index < start].to_numpy(dtype=float) if len(h) else train.loc[it]["target"].to_numpy(dtype=float)
            errs = item_errors(hist, tgt, p, quantiles, m)
            mase = [e["MASE"] for e in errs if e["MASE"] is not None]
            wql_num = [e["WQL"] * e["mean_actual"] * e["n"] for e in errs if e["WQL"] is not None]
            wql_den = [e["mean_actual"] * e["n"] for e in errs if e["WQL"] is not None]
            scores.append({"window": w + 1, "MASE": float(np.mean(mase)) if mase else None,
                           "WQL": float(np.sum(wql_num) / np.sum(wql_den)) if wql_den and np.sum(wql_den) else None,
                           "coverage": float(np.mean([e["coverage"] for e in errs])) if errs else None})
        out["models"][name] = scores
    return out


def predict_new(predictor_path: str, cfg: dict[str, Any], csv_path: str, out_path: str) -> dict[str, Any]:
    """(In a worker.) Forecast from a new history file with the same mapping: the saved predictor conditioned on it."""
    from autogluon.timeseries import TimeSeriesPredictor

    p = TimeSeriesPredictor.load(predictor_path)
    new = frames.read_upload(csv_path, filename=Path(csv_path).name)  # the user's history file: typed import rules
    try:
        new = frames.align_to(new, frames.resolve(cfg))  # … cast to the training table's column kinds
    except (frames.FrameError, OSError):
        pass
    obs, fut, static, info = read_long(cfg, new)
    data = to_tsdf(obs, static)
    if p.freq and data.freq != p.freq:
        data = data.convert_frequency(p.freq)
    known = list(cfg["mapping"].get("known_covariates") or [])
    m = seasonal_period(p.freq)
    kc, src = future_covariates(p, data, fut, known, m)
    t0 = time.perf_counter()
    pred = p.predict(data, known_covariates=kc)
    conf = load_conformal(predictor_path)
    conf = conf if conf and conf.get("model") == p.model_best else None
    pred = conformal_apply(pred, conf)
    secs = time.perf_counter() - t0
    pred.to_data_frame().reset_index().to_csv(out_path, index=False)
    q = [float(x) for x in p.quantile_levels]
    return {"items": int(pred.num_items), "rows": int(len(pred)), "seconds": secs, "known_covariates_source": src,
            "chart": chart_payload(data, None, pred, q, history=90), "model": p.model_best,
            "recalibrated": conf is not None, "conformal_factors": (conf or {}).get("factors")}
