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
    "MASE": "MASE = mean|e_t| / ((1/(n−m)) Σ_{t>m} |y_t − y_{t−m}|)",
    "WQL": "WQL = 2 Σ_τ Σ_t ρ_τ(y_t − q̂_τ,t) / (|T| Σ_t |y_t|)",
    "SQL": "SQL = (1/|T|) Σ_τ mean ρ_τ(y_t − q̂_τ,t) / ((1/(n−m)) Σ |y_t − y_{t−m}|)",
    "MAPE": "MAPE = mean |e_t| / |y_t|",
    "SMAPE": "sMAPE = mean 2|e_t| / (|y_t| + |ŷ_t|)",
    "WAPE": "WAPE = Σ|e_t| / Σ|y_t|",
    "RMSE": "RMSE = √(mean e_t²)",
    "RMSSE": "RMSSE = √(mean e_t² / ((1/(n−m)) Σ (y_t − y_{t−m})²))",
    "MAE": "MAE = mean |e_t|",
    "MSE": "MSE = mean e_t²",
    "RMSLE": "RMSLE = √(mean (ln(1+ŷ_t) − ln(1+y_t))²)",
    "MQL": "MQL = Σ_τ mean ρ_τ(y_t − q̂_τ,t)",
    "BIAS": "BIAS = mean (ŷ_t − y_t)",
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


def read_long(cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None, dict[str, Any]]:
    """(observed, future, static, info). `observed` has a target on every row; `future` holds rows past each item's
    last observation (their known covariates). Columns renamed to item_id / timestamp / target."""
    m = cfg["mapping"]
    df = pd.read_csv(cfg["csv_path"])
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


def preview(csv_path: str, mapping: dict[str, Any]) -> dict[str, Any]:
    """What the mapping gives: items, lengths, the inferred frequency, the season m, future rows, a few series."""
    obs, fut, static, info = read_long({"csv_path": csv_path, "mapping": mapping})
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


def _fmt_ts(idx) -> list[str]:
    s = pd.DatetimeIndex(idx).strftime("%Y-%m-%d %H:%M:%S")
    return [x.replace(" 00:00:00", "") for x in s]


def chart_payload(full, holdout_pred, future_pred, quantiles: list[float], history: int = 120) -> dict[str, Any]:
    """Per item: the last `history` observed points, the holdout window's actuals with the forecast made without
    them, and the future forecast; quantiles as columns 'q0.1' … plus the mean."""
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
    nvw = int(cfg.get("num_val_windows") or 1)
    if min_len < H * (nvw + 2) + 1:
        raise ForecastError(f"the shortest series has {min_len} steps; with prediction_length {H} and "
                            f"{nvw} backtest window(s) it needs at least {H * (nvw + 2) + 1}")
    train = full.slice_by_timestep(None, -H)
    info.update({"freq": freq, "seasonal_period": m, "prediction_length": H, "train_end": str(train.index.get_level_values(1).max()),
                 "holdout_start": str(full.slice_by_timestep(-H, None).index.get_level_values(1).min()),
                 "train_rows": int(len(train)), "holdout_rows": int(len(full) - len(train))})
    emit("data_ready", **info)
    merge(data=info)

    pk, fk = resolve(cfg, freq)
    quantiles = [float(q) for q in pk["quantile_levels"]]
    resolved = {"predictor_kwargs": pk, "fit_kwargs": fk, "path": cfg["predictor_path"],
                "call": "TimeSeriesPredictor(**predictor_kwargs).fit(train_data, **fit_kwargs)",
                "notes": [f"the last {H} steps of every series held out; fit on the rest",
                          f"seasonal period m = {m} (from freq '{freq}')"]}
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
        try:
            mdl = predictor._trainer.load_model(name)
        except Exception:  # noqa: BLE001
            continue
        w = getattr(mdl, "model_to_weight", None)
        if w:
            w = {k: float(v) for k, v in sorted(w.items(), key=lambda kv: -kv[1])}
            weights[name] = w
            ens.append({"model": name, "weights": w, "best": name == best, "stack_level": 2})
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
    hold_pred = predictor.predict(train, known_covariates=kc_h, model=best)
    hist = {it: train.loc[it]["target"].to_numpy(dtype=float) for it in train.item_ids}
    actual = full.slice_by_timestep(-H, None)
    per_item = item_errors(hist, actual, hold_pred, quantiles, m)
    ev = predictor.evaluate(full, model=best, metrics=["MASE", "WQL", "SMAPE", "MAPE", "RMSE", "WAPE"])
    hold_metrics = {k: -float(v) for k, v in ev.items()}
    cov = [r["coverage"] for r in per_item]
    holdout = {"model": best, "metrics": hold_metrics, "eval_metric": pk["eval_metric"],
               "coverage": float(np.mean(cov)) if cov else None, "interval": [min(quantiles), max(quantiles)],
               "n_items": len(per_item), "horizon": H, "items": per_item}
    merge(holdout=holdout)
    emit("evaluation", model=best, metrics=hold_metrics, coverage=holdout["coverage"])

    # the forecast past the end of the data
    emit("explaining", what="forecast")
    kc_f, kc_source = future_covariates(predictor, full, fut, known, m)
    fpred = predictor.predict(full, known_covariates=kc_f, model=best)
    fpred.to_data_frame().reset_index().to_csv(run_dir / "forecast.csv", index=False)
    hold_pred.to_data_frame().reset_index().to_csv(run_dir / "holdout_forecast.csv", index=False)
    chart = chart_payload(full, hold_pred, fpred, quantiles)
    (run_dir / "chart.json").write_text(json.dumps(chart))
    fc = {"model": best, "horizon": H, "start": str(fpred.index.get_level_values(1).min()),
          "end": str(fpred.index.get_level_values(1).max()), "rows": int(len(fpred)),
          "known_covariates_source": kc_source, "quantiles": quantiles}
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
    obs, fut, static, info = read_long({**cfg, "csv_path": csv_path})
    data = to_tsdf(obs, static)
    if p.freq and data.freq != p.freq:
        data = data.convert_frequency(p.freq)
    known = list(cfg["mapping"].get("known_covariates") or [])
    m = seasonal_period(p.freq)
    kc, src = future_covariates(p, data, fut, known, m)
    t0 = time.perf_counter()
    pred = p.predict(data, known_covariates=kc)
    secs = time.perf_counter() - t0
    pred.to_data_frame().reset_index().to_csv(out_path, index=False)
    q = [float(x) for x in p.quantile_levels]
    return {"items": int(pred.num_items), "rows": int(len(pred)), "seconds": secs, "known_covariates_source": src,
            "chart": chart_payload(data, None, pred, q, history=90), "model": p.model_best}


def _nan_none(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x
