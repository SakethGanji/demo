"""F13 · Active-learning labelling queue.

A finished classification run's exact fit call (`worker.prepare_fit` / `fit_predictor`, as in F12) is refitted on a
growing labelled set. The pool of unlabelled rows is either
  - `hide`:    a share h of the run's training rows has its labels hidden (stratified, seeded), the rest are the seed
               labels; the hidden labels are the oracle a simulated labeller reads, or
  - `missing`: the training rows of the run's dataset whose label cell is empty (upload a CSV with blank labels).
The holdout (F4 / F6 split record) is fixed: the same rows score every fit, and they are never in the pool. The split's
tuning slice keeps its labels (it is validation, not training data); group splits keep their bagging-fold column.

Each round: fit on the labelled set L_r, score the holdout, then pick b rows of the pool P_r = U \\ L_r:
  uncertainty u(x) of the fitted model's p̂(x):
    margin           u = 1 − (p̂₍₁₎ − p̂₍₂₎)            (the two largest class probabilities)
    entropy          u = −Σ_k p̂_k log p̂_k / log K
    least_confident  u = 1 − p̂₍₁₎
  diversity among the βb most uncertain rows (β = `candidates_factor`), on TF-IDF→SVD / standardised / one-hot
  embeddings (the Autopilot audit's embedding):
    cluster  weighted k-means (weights u) into b clusters; the most uncertain row of each (Zhdanov 2019)
    kcenter  greedy k-center (core-set): repeatedly the candidate farthest from L_r and the rows already picked
    none     the b most uncertain rows
The `queue` arm is the real one: its picks go to the labelling queue (the Ground-Truth Inbox's answer shape:
confirm / relabel / skip), and every submitted round makes a dataset version with lineage and refits.

Honest comparison (only when an oracle exists, pool = hide): S seeds × {active, random}, each starting from the same
seed labels L₀(s), labelled by the simulated oracle (the sample's own hidden labels), at equal budgets. Per round the
paired gap d_s = sign·(score_active − score_random) is reported with mean, sd and a 95 % t-interval over the seeds.
Seed 0's first fit and first active batch are the queue's.

Runs in its own process:  python -m app.engine.active_learning <run_dir> [api pid]
Files in <run_dir>/active_learning/: request.json (API), answers.json (API: answers and submitted rounds),
state.json (this process only, written atomically).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..splits import holdout_mask

SCHEMA = "active_learning/1"
DEFAULTS = {"pool": "hide", "hide_share": 0.9, "budget": 200, "rounds": 2, "strategy": "margin", "diversity": "cluster",
            "candidates_factor": 10, "compare_random": True, "seeds": 3}
MIN_PER_CLASS = 5
LEVEL = 0.95

log = logging.getLogger("ensemble.active_learning")


def al_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "active_learning"


def _read(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def read_state(run_dir: Path) -> dict[str, Any] | None:
    return _read(al_dir(run_dir) / "state.json")


def read_answers(run_dir: Path) -> dict[str, Any]:
    return _read(al_dir(run_dir) / "answers.json") or {"answers": {}, "submitted": {}}


def _write(p: Path, obj: dict[str, Any]) -> None:
    from .jsonsafe import jsonable

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(jsonable(obj), indent=1))
    os.replace(tmp, p)


def write_state(run_dir: Path, state: dict[str, Any]) -> None:
    _write(al_dir(run_dir) / "state.json", state)


def write_answers(run_dir: Path, answers: dict[str, Any]) -> None:
    _write(al_dir(run_dir) / "answers.json", answers)


def headline(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the run payload carries: status, progress and the summary F17 reads."""
    if not state:
        return None
    return {"schema": state.get("schema"), "status": state.get("status"), "fits_done": state.get("fits_done"),
            "fits_total": state.get("fits_total"), "error": state.get("error"), "summary": state.get("summary"),
            "finished_at": state.get("finished_at")}


# --------------------------------------------------------------------------- selection


def uncertainty(P: np.ndarray, strategy: str) -> np.ndarray:
    """u(x) ∈ [0, 1] from the class probabilities (rows of P), higher = less sure."""
    S = np.sort(P, axis=1)[:, ::-1]
    if strategy == "entropy":
        K = P.shape[1]
        Q = np.clip(P, 1e-12, 1)
        return -(P * np.log(Q)).sum(axis=1) / np.log(K)
    if strategy == "least_confident":
        return 1 - S[:, 0]
    return 1 - (S[:, 0] - S[:, 1])  # margin


def select(u: np.ndarray, E_pool: np.ndarray, E_lab: np.ndarray | None, b: int, diversity: str, beta: int,
           seed: int) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Positions (into the pool arrays) of the b rows to label, their cluster id (cluster) and the candidate count."""
    n = len(u)
    b = min(b, n)
    order = np.argsort(-u, kind="stable")
    if diversity == "none" or b == n:
        return order[:b], None, b
    m = min(n, max(b, beta * b))
    cand = order[:m]
    X = E_pool[cand]
    if diversity == "kcenter":
        if E_lab is not None and len(E_lab):
            d = np.full(m, np.inf)
            for s in range(0, len(E_lab), 2048):  # min distance of every candidate to the labelled set
                D = ((X[:, None, :] - E_lab[None, s:s + 2048, :]) ** 2).sum(-1)
                d = np.minimum(d, D.min(axis=1))
        else:
            d = np.full(m, np.inf)
            d[0] = 0.0
        picked = []
        for _ in range(b):
            i = int(np.argmax(np.where(np.isinf(d), 1e9 + u[cand], d)))
            picked.append(i)
            d = np.minimum(d, ((X - X[i]) ** 2).sum(-1))
            d[i] = -1.0
        return cand[np.array(picked)], None, m
    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=b, n_init=1, random_state=seed, max_iter=100).fit(X, sample_weight=u[cand] + 1e-6)
    lab = km.labels_
    picked, cl = [], []
    for c in range(b):
        members = np.flatnonzero(lab == c)
        if len(members):
            picked.append(members[np.argmax(u[cand][members])])
            cl.append(c)
    if len(picked) < b:  # empty clusters: fill with the most uncertain rows not yet picked
        rest = [i for i in range(m) if i not in set(picked)]
        for i in rest[: b - len(picked)]:
            picked.append(i)
            cl.append(-1)
    return cand[np.array(picked)], np.array(cl), m


def initial_labelled(y: pd.Series, hide_share: float, seed: int) -> np.ndarray:
    """L₀(s): a stratified (1 − h) share of the rows (≥ MIN_PER_CLASS per class), seeded. Index labels of y."""
    rng = np.random.default_rng(2000 + seed)
    keep = []
    for c in sorted(pd.unique(y), key=str):
        ix = y.index[(y == c).to_numpy()].to_numpy()
        k = min(len(ix), max(MIN_PER_CLASS, int(round((1 - hide_share) * len(ix)))))
        keep.append(rng.permutation(ix)[:k])
    return np.sort(np.concatenate(keep))


# --------------------------------------------------------------------------- the comparison


def _t(df: int) -> float:
    from scipy import stats

    return float(stats.t.ppf(0.5 + LEVEL / 2, df))


def summarise(state: dict[str, Any]) -> dict[str, Any]:
    """The small JSON F17 reads: labels used, score per round (queue), and active vs random with a CI per round."""
    mi = state.get("metric") or {}
    gib = bool(mi.get("greater_is_better", True))
    sign = 1.0 if gib else -1.0
    req = state.get("request") or {}
    arms = state.get("arms") or {}
    q = arms.get("queue") or {}
    seeds = int(req.get("seeds", 0)) if state.get("compare") else 0
    rounds_out = []
    for r in range(int(req.get("rounds", 0)) + 1):
        act = [next((p for p in (arms.get(f"active/{s}") or {}).get("points", []) if p["round"] == r), None) for s in range(seeds)]
        rnd = [next((p for p in (arms.get(f"random/{s}") or {}).get("points", []) if p["round"] == r), None) for s in range(seeds)]
        pairs = [(a, b) for a, b in zip(act, rnd) if a and b]
        if not pairs:
            continue
        A = np.array([a["score"] for a, _ in pairs])
        R = np.array([b["score"] for _, b in pairs])
        D = sign * (A - R)
        k = len(D)
        sd = float(D.std(ddof=1)) if k > 1 else None
        half = _t(k - 1) * sd / np.sqrt(k) if sd is not None else None

        def arm(v):
            return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None, "scores": [float(x) for x in v]}

        rounds_out.append({
            "round": r, "n": [int(a["n"]) for a, _ in pairs], "n_random": [int(b["n"]) for _, b in pairs],
            "active": arm(A), "random": arm(R),
            "gap": {"mean": float(D.mean()), "sd": sd, "ci": [float(D.mean() - half), float(D.mean() + half)] if half is not None else None,
                    "level": LEVEL, "wins": int((D > 0).sum()), "ties": int((D == 0).sum()), "n_seeds": k,
                    "per_seed": [float(x) for x in D], "units": f"{mi.get('name')}, improvement direction (+ = active better)"},
        })
    final = next((x for x in rounds_out if x["round"] == int(req.get("rounds", 0)) and x["gap"]["n_seeds"] == seeds), None)
    if not seeds:
        verdict = {"label": "not-compared", "reason": "no oracle for the pool (its labels are really missing), so random "
                   "selection could not be simulated" if req.get("pool") == "missing" else "the comparison was switched off"}
    elif final is None:
        verdict = {"label": "pending", "reason": "the simulated comparison has not reached the last round yet"}
    else:
        g = final["gap"]
        ci = g["ci"]
        tail = (f"after {final['round']} rounds (n = {final['n'][0]} labels), active − random = {g['mean']:+.4f} "
                + (f"(95% CI {ci[0]:+.4f} to {ci[1]:+.4f}, " if ci else "(") + f"{g['wins']}/{g['n_seeds']} seeds won by active)")
        if ci and ci[0] > 0:
            verdict = {"label": "active-better", "reason": "active selection is better than random at the same budget: " + tail}
        elif ci and ci[1] < 0:
            verdict = {"label": "random-better", "reason": "random selection is better than active at the same budget: " + tail}
        else:
            verdict = {"label": "no-clear-difference",
                       "reason": ("active selection scored higher on average, but the interval contains 0: " if g["mean"] > 0
                                  else "active selection did not score higher: ") + tail}
        verdict.update({"round": final["round"], "n": final["n"][0], "gap": g["mean"], "ci": ci, "wins": g["wins"],
                        "n_seeds": g["n_seeds"]})
    qp = q.get("points", [])
    return {
        "schema": SCHEMA, "metric": mi.get("name"), "greater_is_better": gib,
        "budget": req.get("budget"), "rounds": req.get("rounds"), "strategy": req.get("strategy"), "diversity": req.get("diversity"),
        "pool": {k: (state.get("pool") or {}).get(k) for k in ("source", "hide_share", "n_pool", "n_initial")},
        "slice": ({k: (state.get("slice") or {}).get(k) for k in ("id", "label", "n_pool")} if state.get("slice") else None),
        "labels_used": [p["n"] for p in qp],
        "queue": [{"round": p["round"], "n_labels": p["n"], "score": p["score"]} for p in qp],
        "comparison": {"simulated": True, "labeller": "oracle: the sample's own hidden labels (simulated, not a person)",
                       "seeds": seeds, "rounds": rounds_out} if seeds else None,
        "verdict": verdict,
    }


# --------------------------------------------------------------------------- the job


def _rows_sha(index) -> str:
    return hashlib.sha256(json.dumps([int(v) for v in np.sort(np.asarray(index))]).encode()).hexdigest()[:16]


def _watch_parent(ppid: int) -> None:
    def loop():
        while True:
            try:
                os.kill(ppid, 0)
            except ProcessLookupError:
                os._exit(3)
            except PermissionError:
                pass
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()


def _missing_pool(cfg: dict[str, Any], train: pd.DataFrame) -> pd.DataFrame:
    """Training rows of data.csv (not holdout, tuning or excluded by the split) whose label is empty."""
    from . import worker as w

    df = pd.read_csv(cfg["csv_path"])
    sp = json.loads(Path(cfg["splits_path"]).read_text())
    fold = np.asarray(sp["fold"], dtype=int)
    hold = holdout_mask(sp, len(df))
    structured = bool(sp.get("params"))
    out_of_train = hold | ((fold == -3) | (fold == -2) if structured else False)
    sel = ~out_of_train & df[cfg["label"]].isna().to_numpy()
    drop = [c for c in cfg.get("drop_columns") or [] if c in df.columns and c != cfg["label"]]
    pool = df.loc[sel].drop(columns=drop)
    if w.GROUP_FOLD_COLUMN in train.columns:
        pool[w.GROUP_FOLD_COLUMN] = fold[pool.index.to_numpy()]
    return pool[[c for c in train.columns]]


class Job:
    def __init__(self, run_dir: Path):
        from ..db import Database
        from .store import RunStore

        self.run_dir = Path(run_dir)
        self.d = al_dir(self.run_dir)
        self.req = json.loads((self.d / "request.json").read_text())
        self.cfg = json.loads((self.run_dir / "config.json").read_text())
        self.store = RunStore(Database(Path(self.cfg["db_path"])))
        self.run_id = self.cfg["run_id"]
        self.res = (self.store.get(self.run_id) or {}).get("result") or {}
        self.state = read_state(self.run_dir) or {}

    def save(self) -> None:
        self.state["summary"] = summarise(self.state)
        write_state(self.run_dir, self.state)

    # ---------------------------------------------------------------- data

    def load(self) -> None:
        from ..autopilot import audit
        from . import worker as w

        cfg, req = self.cfg, self.req
        self.label = label = cfg["label"]
        train, holdout, info, tuning = w.load_frames(cfg)
        self.info, self.holdout, self.tuning = info, holdout, tuning
        if req["pool"] == "missing":
            pool = _missing_pool(cfg, train)
            U = pd.concat([train, pool]).sort_index()
            self.pool_rows = pool.index.to_numpy()
        else:
            U = train
            self.pool_rows = None
        w.apply_column_types(cfg, U, holdout.copy(), None)  # the same casts prepare_fit applies to every fit
        self.U = U
        # F16: a queue pre-filtered to a weak slice picks only pool rows inside the slice's conditions (every arm, so
        # active and random are compared on the same restricted pool).
        sli = req.get("slice")
        if sli:
            from . import slices as sl

            raw = U.drop(columns=[c for c in (label, w.GROUP_FOLD_COLUMN) if c in U.columns])
            self.slice_rows = set(int(r) for r in U.index[sl.slice_mask(raw, sli["conditions"])])
        else:
            self.slice_rows = None
        self.class_map = {str(c): c for c in pd.unique(pd.concat([U[label], holdout[label]]).dropna())}
        self.oracle = U[label].copy() if req["pool"] == "hide" else None
        feats = [c for c in U.columns if c not in (label, w.GROUP_FOLD_COLUMN)]
        types = cfg.get("column_types") or {}
        text = [c for c in feats if c in (cfg.get("text_columns") or []) or types.get(c) == "text"]
        num = [c for c in feats if c not in text and (types.get(c) == "number" or (types.get(c) is None and pd.api.types.is_numeric_dtype(U[c])))]
        cat = [c for c in feats if c not in text and c not in num]
        self.text_cols, self.other_cols = text, [c for c in feats if c not in text]
        t0 = time.time()
        self.E = pd.DataFrame(audit._embed(U[feats], text, num, cat), index=U.index)
        self.embed_info = {"text": text, "numeric": num, "categorical": cat, "dims": int(self.E.shape[1]),
                           "seconds": time.time() - t0,
                           "method": "TF-IDF (1–2-grams) → truncated SVD of the text, standardised numbers, one-hot categories; "
                                     "rows scaled to unit length (the Autopilot audit's embedding)"}
        self.prep_full = w.prepare_fit(dict(cfg), U.loc[U[label].notna()].copy(), holdout.copy(),
                                       None if tuning is None else tuning.copy(), info)

    def l0(self, seed: int) -> np.ndarray:
        if self.req["pool"] == "missing":
            return np.sort(self.U.index[self.U[self.label].notna().to_numpy()].to_numpy())
        return initial_labelled(self.U[self.label], float(self.req["hide_share"]), seed)

    # ---------------------------------------------------------------- one fit

    def fit_score(self, rows: np.ndarray, labels: dict[int, str], tag: str):
        """Fit the run's call on U[rows] (labels overridden by `labels`), score the holdout. → (predictor, point)."""
        from . import worker as w

        sub = self.U.loc[np.sort(rows)].copy()
        if labels:  # answers come as text; back to the label column's own values (0/1 stays an int)
            ix = [r for r in labels if r in sub.index]
            sub.loc[ix, self.label] = [self.class_map.get(str(labels[r]), labels[r]) for r in ix]
        sub_cfg = dict(self.cfg)
        sub_cfg["problem_type"] = self.res.get("problem_type") or sub_cfg.get("problem_type")
        if self.req.get("time_limit") is not None:
            sub_cfg["time_limit"] = self.req["time_limit"]
        sub_cfg["predictor_path"] = str(self.d / "fits" / tag)
        shutil.rmtree(sub_cfg["predictor_path"], ignore_errors=True)
        prep = w.prepare_fit(sub_cfg, sub, self.holdout.copy(), None if self.tuning is None else self.tuning.copy(), self.info)
        if prep["unlabeled"] is not None:
            prep["unlabeled"] = self.prep_full["unlabeled"]
        t0 = time.time()
        predictor, fit_s = w.fit_predictor(sub_cfg, prep, sub, sub_cfg["predictor_path"])
        w._decide_threshold(sub_cfg, predictor, sub, lambda *a, **k: None)
        hm = w.holdout_metrics(predictor, self.holdout, self.label)
        metric = self.state["metric"]["name"]
        pt = {"n": len(sub), "score": float(hm["metrics"][metric]),
              "metrics": {k: v for k, v in hm["metrics"].items() if isinstance(v, (int, float))},
              "best_model": predictor.model_best, "models": list(predictor.model_names()), "fit_seconds": fit_s,
              "seconds": time.time() - t0, "holdout_sha": self.state["holdout_sha"], "rows_sha": _rows_sha(sub.index),
              "classes": int(sub[self.label].nunique())}
        self.state["fits_done"] = int(self.state.get("fits_done") or 0) + 1
        return predictor, pt

    def drop_fit(self, predictor, tag: str) -> None:
        del predictor
        shutil.rmtree(self.d / "fits" / tag, ignore_errors=True)

    def pool_of(self, arm: dict[str, Any]) -> np.ndarray:
        taken = set(arm["labelled"]) | set(arm.get("skipped") or [])
        cand = self.pool_rows if self.pool_rows is not None else self.U.index.to_numpy()
        keep = self.slice_rows
        return np.array([r for r in cand if r not in taken and (keep is None or r in keep)], dtype=int)

    def pick(self, predictor, arm: dict[str, Any], seed: int, r: int, kind: str) -> tuple[list[int], dict[str, Any]]:
        """The next batch for an arm: (rows, details per row for the queue)."""
        req = self.req
        b = int(req["budget"])
        pool = self.pool_of(arm)
        if len(pool) == 0:
            return [], {}
        if kind == "random":
            rng = np.random.default_rng(5000 + 100 * seed + r)
            return sorted(int(x) for x in rng.choice(pool, size=min(b, len(pool)), replace=False)), {}
        feats = self.U.loc[pool].drop(columns=[self.label])
        P = predictor.predict_proba(feats)
        classes = [str(c) for c in P.columns]
        Pm = P.to_numpy()
        u = uncertainty(Pm, req["strategy"])
        E_pool = self.E.loc[pool].to_numpy()
        E_lab = self.E.loc[np.array(arm["labelled"], dtype=int)].to_numpy() if req["diversity"] == "kcenter" else None
        pos, clusters, m = select(u, E_pool, E_lab, b, req["diversity"], int(req["candidates_factor"]), 7000 + 100 * seed + r)
        rows = [int(pool[i]) for i in pos]
        if kind != "queue":
            return sorted(rows), {"candidates": m, "u_mean_picked": float(u[pos].mean()), "u_mean_pool": float(u.mean())}
        rank = np.empty(len(u), dtype=int)
        rank[np.argsort(-u, kind="stable")] = np.arange(1, len(u) + 1)
        items = []
        from ..autopilot import audit

        for j, i in enumerate(pos):
            row = int(pool[i])
            top = np.argsort(-Pm[i])[:2]
            p1, p2 = float(Pm[i, top[0]]), float(Pm[i, top[1]]) if len(top) > 1 else 0.0
            ent = float(-(Pm[i] * np.log(np.clip(Pm[i], 1e-12, 1))).sum() / np.log(Pm.shape[1]))
            div = (f"the most uncertain row of cluster {int(clusters[j]) + 1} of {b}" if req["diversity"] == "cluster" and clusters is not None and clusters[j] >= 0
                   else "the candidate farthest from every labelled row and every row picked before it" if req["diversity"] == "kcenter"
                   else "no diversity step")
            items.append({
                "id": f"r{r + 1}-{row}", "round": r + 1, "row": row, "split": "train (pool)", **audit._text_of(self.U.loc[row], self.text_cols, self.other_cols),
                "suggested": classes[top[0]], "p_top": [[classes[t], float(Pm[i, t])] for t in top],
                "margin": p1 - p2, "entropy": ent, "uncertainty": float(u[i]), "rank": int(rank[i]),
                "cluster": None if clusters is None else int(clusters[j]),
                "confidence": p1,
                "why": f"u = {u[i]:.3f} ({req['strategy']}), rank {int(rank[i])} of {len(u)} unlabelled rows; {div}",
            })
        return sorted(rows), {"candidates": m, "items": items, "classes": classes,
                              "u_mean_picked": float(u[pos].mean()), "u_mean_pool": float(u.mean()), "n_pool": len(pool)}

    # ---------------------------------------------------------------- tasks

    def new_arm(self, kind: str, seed: int, l0: np.ndarray) -> dict[str, Any]:
        return {"kind": kind, "seed": seed, "labelled": [int(x) for x in l0], "labels": {}, "skipped": [], "points": [],
                "next_batch": None, "done": False}

    def init_seed(self, s: int) -> None:
        """Round 0 of seed s: one fit on L₀(s), shared by active/s, random/s (and the queue for s = 0)."""
        st = self.state
        arms = st["arms"]
        l0 = self.l0(s)
        st["current"] = {"task": "init", "seed": s}
        self.save()
        tag = f"init_s{s}"
        predictor, pt = self.fit_score(l0, {}, tag)
        pt.update(round=0, added=0)
        if s == 0:
            q = arms["queue"]
            q["points"].append({**pt, "answered_by": {}})
            rows, det = self.pick(predictor, q, 0, 0, "queue")
            q["queue"] = det.get("items", [])
            q["queue_round"] = 1 if rows else None
            q["selection"] = [{k: v for k, v in det.items() if k != "items"} | {"round": 1}]
            if not rows:
                q["done"] = True
            if st["compare"]:
                arms["active/0"]["next_batch"] = rows
        if st["compare"]:
            for kind in ("active", "random"):
                a = arms[f"{kind}/{s}"]
                a["points"].append(dict(pt))
                if kind == "random" or s != 0:
                    rows, det = self.pick(predictor, a, s, 0, kind)
                    a["next_batch"] = rows
                    a.setdefault("selection", []).append({**det, "round": 1})
        self.drop_fit(predictor, tag)
        st["init_done"] = sorted(set(st.get("init_done") or []) | {s})
        self.save()

    def step_sim(self, key: str) -> None:
        st = self.state
        a = st["arms"][key]
        r = len(a["points"])  # the round this fit makes
        batch = a["next_batch"] or []
        a["labelled"] = sorted(set(a["labelled"]) | set(batch))
        st["current"] = {"task": key, "round": r}
        self.save()
        tag = f"{key.replace('/', '_')}_r{r}"
        predictor, pt = self.fit_score(np.array(a["labelled"]), {}, tag)  # the oracle's labels: U's own
        pt.update(round=r, added=len(batch))
        a["points"].append(pt)
        a["next_batch"] = None
        if r < int(self.req["rounds"]):
            rows, det = self.pick(predictor, a, a["seed"], r, a["kind"])
            a["next_batch"] = rows
            a.setdefault("selection", []).append({**det, "round": r + 1})
        else:
            a["done"] = True
        self.drop_fit(predictor, tag)
        self.save()

    def step_queue(self, r: int, sub: dict[str, Any], answers: dict[str, Any]) -> None:
        """Round r of the queue: add the submitted answers of round r, refit, score, pick round r + 1."""
        st = self.state
        q = st["arms"]["queue"]
        added, by = [], {}
        for it in q.get("queue") or []:
            a = answers.get(it["id"]) or {}
            ans = a.get("answer")
            if ans in ("confirm", "relabel"):
                lab = a.get("label") or it["suggested"]
                q["labels"][str(it["row"])] = lab
                added.append(it["row"])
                who = a.get("by") or "you"
                by[who] = by.get(who, 0) + 1
            else:
                q["skipped"].append(it["row"])
        q["labelled"] = sorted(set(q["labelled"]) | set(added))
        q["history"] = (q.get("history") or []) + [{"round": r, "items": len(q.get("queue") or []), "labelled": len(added),
                                                      "skipped": len(q.get("queue") or []) - len(added), "answered_by": by,
                                                      "dataset_id": sub.get("dataset_id"), "submitted_at": sub.get("at")}]
        st["current"] = {"task": "queue", "round": r}
        q["queue"] = []
        self.save()
        tag = f"queue_r{r}"
        labels = {int(k): v for k, v in q["labels"].items()}
        predictor, pt = self.fit_score(np.array(q["labelled"]), labels, tag)
        agree = None
        if self.oracle is not None and added:  # how often the labeller agreed with the hidden label (simulated check)
            agree = float(np.mean([str(labels[x]) == str(self.oracle.loc[x]) for x in added]))
        pt.update(round=r, added=len(added), answered_by=by, dataset_id=sub.get("dataset_id"), agreement_with_hidden=agree)
        q["points"].append(pt)
        if r < int(self.req["rounds"]):
            rows, det = self.pick(predictor, q, 0, r, "queue")
            q["queue"] = det.get("items", [])
            q["queue_round"] = r + 1 if rows else None
            q["selection"].append({k: v for k, v in det.items() if k != "items"} | {"round": r + 1})
            if not rows:
                q["done"] = True
        else:
            q["queue_round"] = None
            q["done"] = True
        self.drop_fit(predictor, tag)
        self.save()

    def next_task(self) -> tuple[str, Any] | None:
        st = self.state
        q = st["arms"]["queue"]
        ans = read_answers(self.run_dir)
        if 0 not in (st.get("init_done") or []):
            return ("init", 0)
        qr = q.get("queue_round")
        if qr is not None and not q["done"] and str(qr) in (ans.get("submitted") or {}):
            return ("queue", (qr, ans["submitted"][str(qr)], ans.get("answers") or {}))
        if st["compare"]:
            for s in range(int(self.req["seeds"])):
                if s not in st["init_done"]:
                    return ("init", s)
            for r in range(1, int(self.req["rounds"]) + 1):  # round by round, so a partial comparison is balanced
                for s in range(int(self.req["seeds"])):
                    for kind in ("active", "random"):
                        a = st["arms"][f"{kind}/{s}"]
                        if not a["done"] and len(a["points"]) == r and a["next_batch"] is not None:
                            return ("sim", f"{kind}/{s}")
        return None

    def run(self) -> int:
        st = self.state
        from .store import now

        try:
            import ensemble_models  # noqa: F401 — custom models (TF-IDF, cost, monotone, scorecard)

            fresh = not st.get("arms")
            st.update({"schema": SCHEMA, "status": "running", "pid": os.getpid(), "error": None})
            if fresh:
                st.update({"started_at": now(), "request": self.req, "fits_done": 0})
            write_state(self.run_dir, st)
            self.load()
            em = self.res.get("eval_metric") or {}
            if fresh:
                req = self.req
                compare = bool(req["compare_random"]) and req["pool"] == "hide" and int(req["seeds"]) > 0
                S, R = int(req["seeds"]), int(req["rounds"])
                n_pool = len(self.pool_rows) if self.pool_rows is not None else len(self.U) - len(self.l0(0))
                if self.slice_rows is not None:
                    n_pool = len(self.pool_of(self.new_arm("queue", 0, self.l0(0))))
                st.update({
                    "metric": {"name": em.get("name") or self.cfg.get("eval_metric"), "greater_is_better": em.get("greater_is_better", True),
                               "optimum": em.get("optimum")},
                    "problem_type": self.res.get("problem_type"), "classes": [str(c) for c in self.res.get("classes") or []],
                    "compare": compare, "holdout_rows": len(self.holdout), "holdout_sha": _rows_sha(self.holdout.index),
                    "holdout_in_pool": 0, "tuning_rows": 0 if self.tuning is None else len(self.tuning),
                    "pool": {"source": req["pool"], "hide_share": req.get("hide_share") if req["pool"] == "hide" else None,
                             "n_train": len(self.U), "n_pool": n_pool, "n_initial": len(self.l0(0)),
                             "initial_classes": {str(k): int(v) for k, v in self.U.loc[self.l0(0), self.label].value_counts().items()},
                             "rule": (f"{req['hide_share']:.0%} of the run's {len(self.U)} training rows have their labels hidden "
                                      f"(stratified, ≥ {MIN_PER_CLASS} per class kept, seeded per comparison seed); the holdout and "
                                      "the tuning slice keep theirs") if req["pool"] == "hide"
                             else f"the {n_pool} training rows of the dataset whose label is empty"},
                    "slice": ({**req["slice"], "n_train_in_slice": len(self.slice_rows), "n_pool": n_pool}
                              if req.get("slice") else None),
                    "embedding": self.embed_info,
                    "split": {"key": (self.cfg.get("split") or {}).get("key"), "sha256": (self.cfg.get("split") or {}).get("sha256"),
                              "strategy": (self.cfg.get("split") or {}).get("strategy")},
                    "fit_call": {"predictor_kwargs": self.prep_full["resolved"]["predictor_kwargs"],
                                 "fit_kwargs": self.prep_full["resolved"]["fit_kwargs"], "notes": self.prep_full["resolved"]["notes"],
                                 "time_limit": req.get("time_limit")},
                    "fits_total": (R + 1) + (S * (1 + 2 * R) - 1 if compare else 0),
                    "arms": {"queue": self.new_arm("queue", 0, self.l0(0))},
                    "init_done": [],
                })
                if compare:
                    for s in range(S):
                        l0 = self.l0(s)
                        for kind in ("active", "random"):
                            st["arms"][f"{kind}/{s}"] = self.new_arm(kind, s, l0)
                hold = set(int(x) for x in self.holdout.index)
                st["holdout_in_pool"] = sum(1 for r in self.pool_of(st["arms"]["queue"]) if int(r) in hold)
                self.save()
            while True:
                t = self.next_task()
                if t is None:
                    break
                kind, arg = t
                if kind == "init":
                    self.init_seed(arg)
                elif kind == "queue":
                    self.step_queue(*arg)
                else:
                    self.step_sim(arg)
            st.pop("current", None)
            q = st["arms"]["queue"]
            sims_done = not st["compare"] or all(a["done"] for k, a in st["arms"].items() if k != "queue")
            if q["done"] and sims_done:
                st.update({"status": "succeeded", "finished_at": now()})
            else:
                st["status"] = "awaiting_labels"
            self.store.merge_result(self.run_id, active_learning=headline({**st, "summary": summarise(st)}))
            self.save()
            shutil.rmtree(self.d / "fits", ignore_errors=True)
            return 0
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            log.error("active learning of %s failed:\n%s", self.run_id, tb)
            st.update({"status": "failed", "error": f"{type(e).__name__}: {e}", "traceback": tb[-3000:], "finished_at": now()})
            self.store.merge_result(self.run_id, active_learning=headline(st))
            self.save()
            shutil.rmtree(self.d / "fits", ignore_errors=True)
            return 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) > 2:
        _watch_parent(int(sys.argv[2]))
    import fcntl

    run_dir = Path(sys.argv[1])
    with open(al_dir(run_dir) / "lock", "w") as lock:  # one process per queue: a second one waits, then re-reads the state
        fcntl.flock(lock, fcntl.LOCK_EX)
        sys.exit(Job(run_dir).run())


if __name__ == "__main__":
    main()
