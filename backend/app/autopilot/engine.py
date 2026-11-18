"""Vela Autopilot: a brief (dataset, label, requirements, budget, autonomy) driven to Met or Not Met with real runs.

Layer 1 is a fixed strategy; every training step is a real `POST /api/runs` chained by parent_run_id:

    profile → baseline (5-fold bagged, so every training row has an out-of-fold p̂) → audit (cleanlab on the
    out-of-fold and held-out p̂: label issues, duplicates, near-duplicate conflicts, outliers → Ground-Truth Inbox)
    → [your answers] → apply (a new dataset version with lineage) → retrain → rebalance / calibrate if imbalanced
    → escalate preset and budget → stop when the requirements are met or the budget is spent.

Layer 2 (after the audit): Claude, through app/llm.py (the claude CLI, then the Anthropic API), picks the next action
from the same fixed menu given the state (scores, gaps, inbox stats, budget left) and writes the rationale in Vela's
voice. If no model answers within 60 s, or it picks something not on the menu, layer 1 decides that step. Every
decision records which layer made it.

The orchestrator is a thread in the API process that calls the API over HTTP, the way the UI does, so every run goes
through the same validation. State is one JSON file per autopilot (data/autopilot/<id>.json); a restart resumes
autopilots that were running.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

from .. import llm
from . import audit as audit_mod
from . import metrics as M

log = logging.getLogger("ensemble.autopilot")

ACTIONS = {
    "rebalance": "Retrain with balanced class weights (TabularPredictor(sample_weight='balance_weight')); helps a starved class's recall.",
    "calibrate_threshold": "Binary only: retrain and calibrate the decision threshold for the requirement's metric.",
    "escalate_preset": "Retrain with the next stronger preset (medium → good → high quality) and a larger time limit.",
    "add_families": "Retrain with more model families (gradient boosting on n-grams / CatBoost / extra trees) at the same preset.",
    "synthesize_minority": "Text only: write synthetic examples for the starved class (marked synthetic, training folds only, never the holdout), then retrain.",
    "reduce_latency": "Retrain with refit_full + set_best_to_refit_full: the bagged fold models are refit as one model per family on all training rows and that is what serves; cuts per-row latency by about the number of folds.",
    "stop": "Stop: the requirements are met, or nothing on the menu can meet them within the budget left.",
}
PRESETS = ["medium_quality", "good_quality", "high_quality"]
MIN_STEP_SECONDS = 45


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stop(Exception):
    pass


class AutopilotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, aid: str) -> Path:
        return self.root / f"{aid}.json"

    def get(self, aid: str) -> dict[str, Any] | None:
        p = self.path(aid)
        with self.lock:
            return json.loads(p.read_text()) if p.exists() else None

    def save(self, st: dict[str, Any]) -> None:
        with self.lock:
            st["updated_at"] = now()
            tmp = self.path(st["id"]).with_suffix(".tmp")
            tmp.write_text(json.dumps(st, default=_json_default))
            tmp.replace(self.path(st["id"]))

    def list(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.glob("ap_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
        return out


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


class Manager:
    """Starts, resumes and cancels autopilot threads."""

    def __init__(self, app: Any, root: Path) -> None:
        self.app = app
        self.store = AutopilotStore(root)
        self.threads: dict[str, threading.Thread] = {}
        self.cancel: dict[str, threading.Event] = {}
        self.wake: dict[str, threading.Event] = {}

    def start(self, aid: str) -> None:
        t = self.threads.get(aid)
        if t and t.is_alive():
            self.wake.setdefault(aid, threading.Event()).set()
            return
        self.cancel[aid] = threading.Event()
        self.wake[aid] = threading.Event()
        t = threading.Thread(target=self._run, args=(aid,), name=f"autopilot-{aid}", daemon=True)
        self.threads[aid] = t
        t.start()

    def resume_all(self) -> None:
        for st in self.store.list():
            if st["status"] in ("queued", "running"):
                log.info("resuming autopilot %s", st["id"])
                self.start(st["id"])

    def stop(self, aid: str) -> None:
        if aid in self.cancel:
            self.cancel[aid].set()
            self.wake[aid].set()

    def _run(self, aid: str) -> None:
        try:
            Pilot(self, aid).run()
        except Exception:  # noqa: BLE001
            log.exception("autopilot %s crashed", aid)


class Pilot:
    def __init__(self, mgr: Manager, aid: str) -> None:
        self.mgr = mgr
        self.store = mgr.store
        self.aid = aid
        self.st = self.store.get(aid)
        self.c = httpx.Client(base_url=self.st["base_url"], timeout=httpx.Timeout(300, connect=10))
        self._holdout_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ plumbing

    def save(self) -> None:
        self.store.save(self.st)

    def cancelled(self) -> bool:
        return self.mgr.cancel.get(self.aid, threading.Event()).is_set()

    def http(self, method: str, path: str, **kw: Any) -> Any:
        for attempt in range(30):
            try:
                r = self.c.request(method, path, **kw)
            except httpx.TransportError:
                time.sleep(2)  # the API is restarting
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} → {r.status_code}: {r.text[:400]}")
            return r.json()
        raise RuntimeError(f"{method} {path}: the API did not answer")

    def note(self, text: str, layer: str = "layer-1", **extra: Any) -> None:
        self.st["log"].append({"t": now(), "text": text, "layer": layer, **extra})
        self.save()

    def spent(self) -> float:
        return float(sum(s.get("seconds") or 0 for s in self.st["steps"] if s.get("run_id")))

    def remaining(self) -> float:
        return float(self.st["brief"]["budget_seconds"]) - self.spent()

    def step(self, kind: str, title: str, **kw: Any) -> dict[str, Any]:
        s = {"id": f"s{len(self.st['steps'])}", "n": len(self.st["steps"]), "kind": kind, "title": title,
             "status": "running", "started_at": now(), **kw}
        self.st["steps"].append(s)
        self.st["current"] = s["id"]
        self.save()
        return s

    def finish(self, s: dict[str, Any], status: str = "done", **kw: Any) -> None:
        s.update(status=status, finished_at=now(), **kw)
        self.st["current"] = None
        self.save()

    # ------------------------------------------------------------------ main loop

    def run(self) -> None:
        st = self.st
        st["status"] = "running"
        self.save()
        try:
            self._wait_api()
            self._recover()
            if not any(s["kind"] == "profile" and s["status"] == "done" for s in st["steps"]):
                self.do_profile()
            base = next((s for s in st["steps"] if s["kind"] == "baseline" and s["status"] == "done"), None)
            if not base:
                self.do_train("baseline", "Fast baseline", parent=None)
            if not st.get("inbox"):
                self.do_audit()
            if st["inbox"] and not st["inbox"].get("applied"):
                self.wait_for_inbox()
                if self.cancelled():
                    raise Stop()
                if self.do_apply():
                    self.do_train("retrain", "Retrain on the corrected data", parent=self.best_step())
            self.loop()
            self.finalize()
        except Stop:
            self.finalize(cancelled=True)
        except Exception as e:  # noqa: BLE001
            log.exception("autopilot %s failed", self.aid)
            st["status"] = "failed"
            st["error"] = f"{type(e).__name__}: {e}"[:600]
            st["traceback"] = traceback.format_exc()[-3000:]
            for s in st["steps"]:
                if s["status"] == "running":
                    s["status"] = "failed"
            self.save()

    def _recover(self) -> None:
        """After a restart: a run step still in flight is followed to its end; any other unfinished step is marked
        interrupted and redone by the main loop."""
        for s in self.st["steps"]:
            if s["status"] != "running":
                continue
            if s.get("run_id"):
                self.note(f"Resumed after a restart: following {s['run_id']} ({s['title'].lower()}).")
                self._complete_train(s)
            else:
                s["status"] = "interrupted"
                s["finished_at"] = now()
        self.st["current"] = None
        self.save()

    def _wait_api(self) -> None:
        for _ in range(120):
            try:
                if self.c.get("/api/health", timeout=5).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(1)
        raise RuntimeError("the API is not answering")

    # ------------------------------------------------------------------ steps

    def do_profile(self) -> None:
        b = self.st["brief"]
        s = self.step("profile", "Profile the data")
        d = self.http("GET", f"/api/datasets/{b['dataset_id']}")
        p = d["profile"]
        label = b.get("label") or d["target"]
        cols = {c["name"]: c for c in p["columns"]}
        if label not in cols:
            raise RuntimeError(f"No column {label!r} in {d['name']}")
        y = pd.read_csv(self.mgr.app.state.datasets.csv_path(b["dataset_id"]), usecols=[label])[label]
        task = p.get("task") if label == d["target"] else None
        shares = y.astype(str).value_counts(normalize=True)
        problem = task or ("regression" if cols[label]["type"] == "number" and cols[label]["unique"] > 50 else
                           "binary" if y.nunique() == 2 else "multiclass")
        feats = [c for c in cols if c != label and cols[c]["type"] != "id"]
        facts = {
            "dataset_id": b["dataset_id"], "dataset_name": d["name"], "rows": d["rows"], "label": label,
            "problem_type": problem, "features": feats,
            "text_columns": [c for c in feats if cols[c]["type"] == "text"],
            "numeric_columns": [c for c in feats if cols[c]["type"] == "number"],
            "category_columns": [c for c in feats if cols[c]["type"] not in ("text", "number")],
            "class_shares": {str(k): round(float(v), 4) for k, v in shares.items()} if problem != "regression" else None,
            "minority_class": str(shares.idxmin()) if problem != "regression" else None,
            "minority_share": round(float(shares.min()), 4) if problem != "regression" else None,
            "holdout_rows": p["splits"]["holdout_rows"], "duplicates": p.get("duplicates"),
        }
        self.st["facts"] = facts
        self.st["truth_dataset"] = b["dataset_id"]
        self.st["primary"] = next((r["metric"] for r in b["requirements"] if not M.is_latency(r["metric"])),
                                  "f1_macro" if problem == "multiclass" else "roc_auc" if problem == "binary" else "r2")
        txt = (f"{d['rows']:,} rows, {len(feats)} features ({len(facts['text_columns'])} text), {problem}"
               + (f"; the rarest class is {facts['minority_class']} at {facts['minority_share']:.1%} of rows" if facts["minority_class"] else "")
               + f". I'll score every step on the same {facts['holdout_rows']:,} held-out rows.")
        self.finish(s, facts_text=txt)
        self.note(txt)

    def _eval_metric(self) -> str | None:
        prim = self.st["primary"]
        pt = self.st["facts"]["problem_type"]
        if prim in ("f1_macro", "accuracy", "balanced_accuracy", "mcc", "roc_auc", "f1", "r2"):
            if prim == "f1_macro" and pt == "binary":
                return "f1_macro"
            return {"rmse": "root_mean_squared_error"}.get(prim, prim)
        if prim.startswith("recall["):
            return "balanced_accuracy"
        return None

    def _hp(self, rich: bool) -> dict[str, Any] | None:
        f = self.st["facts"]
        if f["text_columns"]:
            hp: dict[str, Any] = {"TFIDF_LR": {}, "TFIDF_SVM": {}}
            if rich:
                hp["GBM"] = {}
            return hp
        if rich:
            return None  # the preset's own zoo
        return {"GBM": {}, "XT": {}, "LR": {}}

    def base_body(self) -> dict[str, Any]:
        """The body every step starts from: the latest kept step's, or the baseline's."""
        best = self.best_step()
        if best:
            return json.loads(json.dumps(best["body"]))
        f = self.st["facts"]
        b = self.st["brief"]
        budget = float(b["budget_seconds"])
        return {
            "dataset_id": self.st["truth_dataset"], "label": f["label"], "presets": "medium_quality",
            "time_limit": float(max(MIN_STEP_SECONDS, min(180, 0.2 * budget))), "eval_metric": self._eval_metric(),
            "fit_kwargs": {"num_bag_folds": 5, "num_stack_levels": 0, **({"hyperparameters": self._hp(False)} if self._hp(False) else {})},
            "feature_importance": False, "source": "autopilot",
        }

    def do_train(self, kind: str, title: str, parent: dict[str, Any] | None, body: dict[str, Any] | None = None,
                 change: str | None = None, decision: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or self.base_body()
        body["dataset_id"] = self.st["truth_dataset"]
        body["source"] = "autopilot"
        body["parent_run_id"] = parent["run_id"] if parent else None
        body["name"] = f"Autopilot {self.aid} · {title}"
        if parent and change:
            body["patch"] = {"finding": f"autopilot:{kind}", "title": title, "changes": [{"path": change}]}
        s = self.step(kind, title, body=body, change=change, decision=decision)
        s["t0"] = time.time()
        r = self.http("POST", "/api/runs", json=body)
        s["run_id"] = r["id"]
        s["dataset_id"] = body["dataset_id"]
        self.save()
        return self._complete_train(s)

    def _complete_train(self, s: dict[str, Any]) -> dict[str, Any]:
        kind, body, decision = s["kind"], s["body"], s.get("decision")
        rec = self.wait_run(s["run_id"])
        s["seconds"] = round(time.time() - s.get("t0", time.time()), 1)
        s["fit_seconds"] = rec.get("fit_seconds")
        s["best_model"] = rec.get("best_model")
        s["positive_class"] = rec.get("positive_class")
        s["n_models"] = len(rec.get("leaderboard") or [])
        s["holdout_autogluon"] = (rec.get("holdout") or {}).get("scores")
        try:
            lat = self.mgr.app.state.heavy.submit(
                audit_mod.measure_latency, rec["config"]["predictor_path"], rec["config"]["csv_path"], rec["label"],
                rec["config"].get("drop_columns") or [], self._holdout_rows_local(body["dataset_id"])[:60]).result(timeout=300)
        except Exception as e:  # noqa: BLE001
            lat = {"error": str(e)[:200]}
        s["latency"] = lat
        self.rescore_all()
        prev = self.best_step(exclude=s["id"])
        s["kept"] = True
        if prev is not None and kind not in ("baseline", "retrain"):
            s["kept"] = (s["score"] or -1e9) > (prev["score"] or -1e9) or self._closes_more(s, prev)
        self.finish(s, status="done" if s["kept"] else "dropped")
        self.note(self.step_text(s, prev), layer=(decision or {}).get("layer", "layer-1"), step=s["id"])
        return s

    def _closes_more(self, s: dict[str, Any], prev: dict[str, Any]) -> bool:
        a = sum(c["ok"] for c in s.get("checks") or [])
        b = sum(c["ok"] for c in prev.get("checks") or [])
        return a > b

    def step_text(self, s: dict[str, Any], prev: dict[str, Any] | None) -> str:
        prim = self.st["primary"]
        lat = (s.get("latency") or {}).get("p95_ms")
        base = self.baseline()
        t = f"{s['title']}: {s['run_id']} ({s['n_models']} models, best {s['best_model']}) scores {prim} {s['score']:.4f}"
        if base and base is not s and base.get("score") is not None:
            t += f", Δ {s['score'] - base['score']:+.4f} against the baseline on the same labels"
        if lat is not None:
            t += f"; p95 latency {lat:.1f} ms per row"
        if s["kind"] not in ("baseline", "retrain") and not s.get("kept"):
            t += f". It did not beat {prev['title'].lower()} ({prev['score']:.4f}), so I dropped it and kept the earlier model"
        return t + "."

    def wait_run(self, run_id: str) -> dict[str, Any]:
        while True:
            if self.cancelled():
                try:
                    self.http("POST", f"/api/runs/{run_id}/cancel")
                except Exception:  # noqa: BLE001
                    pass
                raise Stop()
            rec = self.http("GET", f"/api/runs/{run_id}")
            if rec["status"] == "succeeded":
                return rec
            if rec["status"] in ("failed", "cancelled"):
                raise RuntimeError(f"run {run_id} {rec['status']}: {rec.get('error')}")
            time.sleep(2)

    # ------------------------------------------------------------------ truth and scores

    def _lineage_rows(self, dataset_id: str) -> np.ndarray:
        """Root row of every row of a dataset (identity for the root)."""
        lin = self.mgr.app.state.datasets.lineage(dataset_id)
        if lin:
            return np.asarray(lin["root_rows"], dtype=int)
        n = len(pd.read_csv(self.mgr.app.state.datasets.csv_path(dataset_id), usecols=[0]))
        return np.arange(n)

    def _holdout_rows_local(self, dataset_id: str) -> list[int]:
        sp = json.loads(self.mgr.app.state.datasets.splits_path(dataset_id).read_text())
        return sp["holdout"]

    def truth(self) -> tuple[dict[int, str], set[int]]:
        """(root row → current label, root rows of the current holdout)."""
        ds = self.st["truth_dataset"]
        label = self.st["facts"]["label"]
        y = pd.read_csv(self.mgr.app.state.datasets.csv_path(ds), usecols=[label])[label]
        roots = self._lineage_rows(ds)
        lab = {int(r): (str(v) if self.st["facts"]["problem_type"] != "regression" else v) for r, v in zip(roots, y)}
        hold = {int(roots[i]) for i in self._holdout_rows_local(ds)}
        return lab, hold

    def holdout_preds(self, run_id: str, dataset_id: str) -> dict[str, Any]:
        if run_id not in self._holdout_cache:
            h = self.http("GET", f"/api/runs/{run_id}/holdout-predictions")
            roots = self._lineage_rows(dataset_id)
            h["root_rows"] = [int(roots[r]) for r in h["rows"]]
            self._holdout_cache[run_id] = h
        return self._holdout_cache[run_id]

    def rescore_all(self) -> None:
        lab, hold = self.truth()
        f = self.st["facts"]
        reqs = self.st["brief"]["requirements"]
        for s in self.st["steps"]:
            if not s.get("run_id") or s.get("status") == "failed":
                continue
            h = self.holdout_preds(s["run_id"], s["dataset_id"])
            idx = [i for i, r in enumerate(h["root_rows"]) if r in hold]
            yt = [lab[h["root_rows"][i]] for i in idx]
            yp = [h["y_pred"][i] for i in idx]
            pr = [h["probabilities"][i] for i in idx] if h.get("probabilities") else None
            m = M.score(yt, yp, pr, h.get("classes"), f["problem_type"], positive=s.get("positive_class"))
            s["metrics"] = m
            s["score"] = m.get(self.st["primary"])
            if "score_own" not in s:  # on the labels of the dataset it was trained on, when it was trained
                s["score_own"] = s["score"]
                s["dataset_version"] = 1 + len(self.st["dataset_versions"])
            s["checks"] = M.check(reqs, m, s.get("latency"))
            s["met"] = all(c["ok"] for c in s["checks"])
        self.save()

    def scored_steps(self) -> list[dict[str, Any]]:
        return [s for s in self.st["steps"] if s.get("run_id") and s.get("score") is not None and s["status"] in ("done", "dropped", "running")]

    def baseline(self) -> dict[str, Any] | None:
        return next((s for s in self.st["steps"] if s["kind"] == "baseline" and s.get("score") is not None), None)

    def best_step(self, exclude: str | None = None) -> dict[str, Any] | None:
        """The model Autopilot would hand over: met all requirements first, then the primary score. Steps on an
        older dataset version only count until a step on the newest version exists."""
        cands = [s for s in self.scored_steps() if s["id"] != exclude and s["status"] == "done"]
        newest = [s for s in cands if s["dataset_id"] == self.st["truth_dataset"]]
        cands = newest or cands
        if not cands:
            return None
        sign = -1 if self.st["primary"] in ("rmse", "mae") else 1
        return max(cands, key=lambda s: (s.get("met", False), sum(c["ok"] for c in s.get("checks") or []), sign * (s["score"] or 0)))

    # ------------------------------------------------------------------ audit and inbox

    def do_audit(self) -> None:
        f = self.st["facts"]
        base = self.baseline()
        s = self.step("audit", "Audit the labels (cleanlab)", run_id_source=base["run_id"])
        if f["problem_type"] == "regression":
            self.st["inbox"] = {"items": [], "stats": {"note": "label issues need class probabilities; regression skips them"}, "applied": True}
            self.finish(s, status="skipped")
            return
        oof = self.http("GET", f"/api/runs/{base['run_id']}/oof")
        h = self.holdout_preds(base["run_id"], base["dataset_id"])
        if oof["classes"] != h["classes"]:
            raise RuntimeError("out-of-fold and holdout classes differ")
        rows = oof["rows"] + h["rows"]
        proba = oof["probabilities"] + h["probabilities"]
        ds = self.mgr.app.state.datasets
        out = self.mgr.app.state.heavy.submit(
            audit_mod.audit, str(ds.csv_path(base["dataset_id"])), f["label"], f["features"], f["text_columns"],
            f["numeric_columns"], f["category_columns"], oof["classes"], rows, proba, h["rows"]).result(timeout=900)
        out["run_id"] = base["run_id"]
        out["model"] = oof["model"]
        out["recall"] = self.recall(out["items"])
        out["applied"] = False
        self.st["inbox"] = out
        st = out["stats"]
        kinds: dict[str, int] = {}
        for it in out["items"]:
            kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
        out["kinds"] = kinds
        self.finish(s, seconds_audit=out["seconds"], stats=st, kinds=kinds)
        txt = (f"cleanlab scored {st['rows_scored']:,} rows with the baseline's out-of-sample p̂ ({oof['model']}): "
               f"{st['label_issues']} label issues (normalised margin), {st['duplicates']} exact duplicates, "
               f"{st['near_conflicts']} near-duplicate conflicts, {st['outliers']} outliers. "
               f"{len(out['items'])} rows are in your inbox.")
        r = out["recall"]
        if r:
            txt += f" Against the planted errors in label_noise.json: {r['found']}/{r['planted']} found ({r['recall']:.1%}), precision {r['precision']:.1%}."
        self.note(txt)

    def recall(self, items: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Recall of the planted label flips (sample datasets with label_noise.json), on root rows."""
        root = self.st["brief"]["dataset_id"]
        p = self.mgr.app.state.datasets.dir(root) / "label_noise.json"
        if not p.exists():
            return None
        side = json.loads(p.read_text())
        truth = {int(f["row"]): f["true_label"] for f in side["flipped"]}
        roots = self._lineage_rows(self.st["inbox_dataset"] if self.st.get("inbox_dataset") else root)
        lab_items = [it for it in items if it["kind"] in ("label", "conflict")]
        found = [it for it in lab_items if int(roots[it["row"]]) in truth]
        right = [it for it in found if it.get("suggested") == truth[int(roots[it["row"]])]]
        k = len(truth)
        topk = sorted(lab_items, key=lambda it: it.get("margin", 0))[:k]
        found_k = sum(int(roots[it["row"]]) in truth for it in topk)
        answered = [it for it in items if it.get("answer")]
        fixed = sum(1 for it in answered if int(roots[it["row"]]) in truth and it["answer"] == "relabel"
                    and it.get("answer_label") == truth[int(roots[it["row"]])])
        return {"planted": k, "found": len(found), "recall": len(found) / k, "top_k": len(lab_items),
                "precision": len(found) / max(1, len(lab_items)), "suggestion_correct": len(right),
                "suggestion_accuracy": len(right) / max(1, len(found)), "recall_at_planted": found_k / k,
                "fixed": fixed, "source": f"{root}/label_noise.json", "train_flips": None}

    def wait_for_inbox(self) -> None:
        ib = self.st["inbox"]
        if not ib["items"]:
            return
        mode = self.st["brief"].get("autonomy", "ask")
        if mode == "auto":
            c = float(self.st["brief"].get("auto_confidence", 0.9))
            n = bulk_accept(self.st, c)
            self.note(f"Autonomy is 'auto': I accepted my own suggestion on the {n} rows with confidence ≥ {c:.2f} and skipped the rest.")
            return
        if not ib.get("submitted"):
            self.st["status"] = "awaiting_inbox"
            self.save()
            self.note(f"I need you on {len(ib['items'])} rows: whether a label is true is yours to say, not mine. Training waits.")
            ev = self.mgr.wake[self.aid]
            while not self.cancelled():
                ev.wait(2)
                ev.clear()
                cur = self.store.get(self.aid)
                if cur and cur["inbox"].get("submitted"):
                    self.st["inbox"] = cur["inbox"]
                    break
            self.st["status"] = "running"
            self.save()

    def do_apply(self) -> bool:
        ib = self.st["inbox"]
        items = ib["items"]
        f = self.st["facts"]
        src = self.st["truth_dataset"]
        ds = self.mgr.app.state.datasets
        relabel = {it["row"]: it["answer_label"] for it in items if it.get("answer") == "relabel" and it.get("answer_label")}
        relabel = {r: v for r, v in relabel.items()}
        drop = {it["row"] for it in items if it.get("answer") == "drop"}
        confirmed = sum(1 for it in items if it.get("answer") == "confirm")
        skipped = sum(1 for it in items if it.get("answer") in (None, "skip"))
        s = self.step("apply", "Apply your answers")
        ib["recall"] = self.recall(items)
        if not relabel and not drop:
            ib["applied"] = True
            self.finish(s, status="skipped", relabelled=0, dropped=0, confirmed=confirmed, skipped=skipped)
            self.note(f"No label changed ({confirmed} confirmed, {skipped} skipped), so there is nothing to retrain on.")
            return False
        df = pd.read_csv(ds.csv_path(src))
        sp = json.loads(ds.splits_path(src).read_text())
        roots = self._lineage_rows(src)
        changes = []
        for r, v in relabel.items():
            if r in drop:
                continue
            changes.append({"row": int(r), "root_row": int(roots[r]), "from": str(df.at[r, f["label"]]), "to": v, "op": "relabel"})
            df.at[r, f["label"]] = v
        keep = np.array([i not in drop for i in range(len(df))])
        for r in sorted(drop):
            changes.append({"row": int(r), "root_row": int(roots[r]), "op": "drop", "label": str(df.at[r, f["label"]])})
        new_pos = np.cumsum(keep) - 1
        hold = [int(new_pos[i]) for i in sp["holdout"] if keep[i]]
        fold = [int(v) for v, k in zip(sp["fold"], keep) if k]
        new_sp = {**sp, "holdout": sorted(hold), "fold": fold, "derived_from": src}
        df2 = df.loc[keep].reset_index(drop=True)
        n_ver = len(self.st["dataset_versions"]) + 2
        root = self.st["brief"]["dataset_id"]
        root_name = self.st["facts"]["dataset_name"].replace(" (sample)", "")
        n_rel = sum(1 for c in changes if c["op"] == "relabel")
        lineage = {"parent": src, "root": root, "version": n_ver, "autopilot": self.aid,
                   "relabelled": n_rel, "dropped": len(drop), "synthetic": 0,
                   "relabelled_holdout": sum(1 for c in changes if c["op"] == "relabel" and c["row"] in set(sp["holdout"])),
                   "root_rows": [int(x) for x in roots[keep]], "changes": changes}
        rec = ds.create_version(src, df2, new_sp, name=f"{root_name} · v{n_ver}",
                                note=f"Autopilot {self.aid}: {n_rel} relabelled, {len(drop)} dropped from {src}",
                                lineage=lineage)
        self.st["dataset_versions"].append({"id": rec["id"], "name": rec["name"], "parent": src, "version": n_ver,
                                            "rows": rec["rows"], "relabelled": n_rel, "dropped": len(drop),
                                            "synthetic": 0, "relabelled_holdout": lineage["relabelled_holdout"],
                                            "created_at": now()})
        self.st["truth_dataset"] = rec["id"]
        ib["applied"] = True
        self._holdout_cache.clear()
        self.rescore_all()
        self.finish(s, dataset_id=rec["id"], relabelled=n_rel, dropped=len(drop), confirmed=confirmed, skipped=skipped)
        base = self.baseline()
        self.note(f"Dataset v{n_ver} ({rec['id']}): {n_rel} rows relabelled ({lineage['relabelled_holdout']} of them in the holdout), "
                  f"{len(drop)} dropped, {confirmed} confirmed as they were, {skipped} skipped. Re-scored on the corrected "
                  f"holdout, the baseline's {self.st['primary']} is {base['score']:.4f}; every later Δ is on these labels.")
        return True

    # ------------------------------------------------------------------ decisions

    def menu(self) -> dict[str, str]:
        f = self.st["facts"]
        tried = {s["kind"] for s in self.st["steps"]}
        body = (self.best_step() or {}).get("body") or self.base_body()
        out: dict[str, str] = {}
        if f["problem_type"] == "multiclass" and "rebalance" not in tried and (f["minority_share"] or 1) < 0.15:
            out["rebalance"] = ACTIONS["rebalance"]
        if f["problem_type"] == "binary" and "calibrate_threshold" not in tried:
            out["calibrate_threshold"] = ACTIONS["calibrate_threshold"]
        if body.get("presets") in PRESETS[:-1]:
            out["escalate_preset"] = ACTIONS["escalate_preset"]
        if "add_families" not in tried:
            out["add_families"] = ACTIONS["add_families"]
        lat_open = any(M.is_latency(c["metric"]) and not c["ok"] for c in (self.best_step() or {}).get("checks") or [])
        if lat_open and not (body.get("fit_kwargs") or {}).get("refit_full"):
            out["reduce_latency"] = ACTIONS["reduce_latency"]
        if (f["text_columns"] and f["problem_type"] != "regression" and "synthesize_minority" not in tried
                and (f["minority_share"] or 1) < 0.06 and llm.enabled() and self.st["brief"].get("allow_synthetic", True)):
            out["synthesize_minority"] = ACTIONS["synthesize_minority"]
        out["stop"] = ACTIONS["stop"]
        return out

    def layer1(self, menu: dict[str, str]) -> tuple[str, str]:
        best = self.best_step()
        prim = self.st["primary"]
        rem = self.remaining()
        if best and best.get("met"):
            return "stop", f"Every requirement holds on {best['run_id']} ({prim} {best['score']:.4f}); stopping."
        if rem < MIN_STEP_SECONDS:
            return "stop", f"{rem:.0f} s of budget left, under the {MIN_STEP_SECONDS} s a step needs; stopping."
        gaps = [c for c in (best or {}).get("checks", []) if not c["ok"]]
        g = "; ".join(f"{c['metric']} {c['observed']:.4g} vs {c['op']} {c['value']}" for c in gaps if c["observed"] is not None)
        for a in ("reduce_latency", "rebalance", "calibrate_threshold", "escalate_preset", "add_families"):
            if a in menu:
                why = {"reduce_latency": "a latency requirement is open and the served model is still a bag of fold models",
                       "rebalance": f"the rarest class ({self.st['facts']['minority_class']}) is {self.st['facts']['minority_share']:.1%} of rows",
                       "calibrate_threshold": "the default 0.5 threshold is rarely right for an imbalanced binary target",
                       "escalate_preset": f"a stronger preset is next with {rem:.0f} s left",
                       "add_families": "more model families widen the ensemble"}[a]
                return a, f"Open: {g or 'the requirements'}. Fixed strategy: {a.replace('_', ' ')}, because {why}."
        return "stop", f"Open: {g}. The fixed strategy has nothing left to try."

    def decide(self) -> dict[str, Any]:
        menu = self.menu()
        l1_action, l1_why = self.layer1(menu)
        want = self.st["brief"].get("decider", "auto")
        d: dict[str, Any] = {"at": now(), "after_step": self.st["steps"][-1]["id"], "menu": list(menu)}
        if want != "layer1" and llm.enabled() and len(menu) > 1:
            prompt = self.decision_prompt(menu)
            obj, meta = llm.ask_json(prompt, timeout=float(self.st["brief"].get("llm_timeout", 60)))
            d["attempts"] = meta["attempts"]
            d["seconds"] = meta["seconds"]
            if obj and obj.get("action") in menu:
                d.update(action=obj["action"], rationale=str(obj.get("rationale") or "")[:600],
                         layer="layer-2", source=meta["source"], model=meta.get("model"),
                         time_limit=obj.get("time_limit"), layer1_would=l1_action, cost_usd=meta.get("cost_usd"))
                if d["action"] == "stop" and l1_action != "stop" and self.remaining() > 2 * MIN_STEP_SECONDS and not (self.best_step() or {}).get("met"):
                    pass  # Claude may stop early; that is its call and it is logged with layer 1's alternative.
                self.st["decisions"].append(d)
                self.save()
                return d
            d["layer2_error"] = ("reply not on the menu: " + json.dumps(obj)[:200]) if obj else "; ".join(a.get("error", "") for a in meta["attempts"]) or "language model disabled"
        d.update(action=l1_action, rationale=l1_why, layer="layer-1", source="computed", model=None)
        self.st["decisions"].append(d)
        self.save()
        return d

    def state_digest(self) -> dict[str, Any]:
        f = self.st["facts"]
        ib = self.st.get("inbox") or {}
        steps = []
        for s in self.st["steps"]:
            if s.get("run_id"):
                steps.append({"step": s["title"], "kept": s.get("kept"), "presets": (s.get("body") or {}).get("presets"),
                              "time_limit": (s.get("body") or {}).get("time_limit"), "seconds": s.get("seconds"),
                              "score": None if s.get("score") is None else round(s["score"], 4),
                              "p95_ms": None if (s.get("latency") or {}).get("p95_ms") is None else round(s["latency"]["p95_ms"], 1),
                              "worst_recall": min(((k, round(v, 3)) for k, v in (s.get("metrics") or {}).items() if k.startswith("recall[")), key=lambda kv: kv[1], default=None),
                              "unmet": [f"{c['metric']} {c['observed']:.4g} {c['op']} {c['value']}" for c in s.get("checks") or [] if not c["ok"] and c["observed"] is not None]})
        return {"problem_type": f["problem_type"], "rows": f["rows"], "text_columns": f["text_columns"],
                "class_shares": f["class_shares"], "primary_metric": self.st["primary"],
                "requirements": self.st["brief"]["requirements"], "steps": steps,
                "inbox": {"items": len(ib.get("items") or []), "kinds": ib.get("kinds"),
                          "answered": sum(1 for it in ib.get("items") or [] if it.get("answer"))},
                "dataset_versions": [{k: v[k] for k in ("version", "relabelled", "dropped", "synthetic")} for v in self.st["dataset_versions"]],
                "budget_seconds": self.st["brief"]["budget_seconds"], "spent_seconds": round(self.spent()),
                "remaining_seconds": round(self.remaining())}

    def decision_prompt(self, menu: dict[str, str]) -> str:
        return (
            "You are Vela, the autopilot of a bank's AutoML workbench (AutoGluon). Pick the next action for this model.\n\n"
            "Menu (choose exactly one id):\n" + "\n".join(f"- {k}: {v}" for k, v in menu.items()) +
            "\n\nState (scores are on the same held-out rows; latency is one row per predict call):\n" +
            json.dumps(self.state_digest(), indent=1) +
            "\n\nRules: stop if every requirement is met, or if the budget left cannot fit another useful step "
            f"(a step needs at least {MIN_STEP_SECONDS} s). Prefer the action most likely to close the largest open gap "
            "without breaking a requirement that already holds (e.g. latency). Give time_limit in seconds, at most the "
            "remaining budget.\n\n"
            'Return only JSON: {"action": "<id>", "time_limit": <seconds>, "rationale": "<one or two sentences, first '
            'person, as Vela, citing the numbers from the state that drove the choice>"}'
        )

    def loop(self) -> None:
        for _ in range(8):
            if self.cancelled():
                raise Stop()
            d = self.decide()
            self.note(d["rationale"], layer=d["layer"], source=d.get("source"), model=d.get("model"), action=d["action"])
            if d["action"] == "stop":
                return
            if self.remaining() < MIN_STEP_SECONDS:
                self.note(f"Only {self.remaining():.0f} s left, below the {MIN_STEP_SECONDS} s a step needs; stopping.")
                return
            self.act(d)

    def _limit(self, d: dict[str, Any], default: float) -> float:
        rem = self.remaining()
        want = d.get("time_limit")
        try:
            t = float(want) if want else default
        except (TypeError, ValueError):
            t = default
        return float(max(30.0, min(t, rem - 10, 900)))

    def act(self, d: dict[str, Any]) -> None:
        a = d["action"]
        best = self.best_step()
        body = self.base_body()
        base_t = float(body.get("time_limit") or 60)
        if a == "rebalance":
            body.setdefault("predictor_kwargs", {})["sample_weight"] = "balance_weight"
            body["time_limit"] = self._limit(d, base_t)
            self.do_train("rebalance", "Balance class weights", best, body, change="predictor_kwargs.sample_weight = 'balance_weight'", decision=d)
        elif a == "calibrate_threshold":
            m = {"f1": "f1", "balanced_accuracy": "balanced_accuracy", "mcc": "mcc"}.get(self.st["primary"], "f1")
            body["calibrate_threshold"] = m
            body["time_limit"] = self._limit(d, base_t)
            self.do_train("calibrate_threshold", f"Calibrate the threshold for {m}", best, body, change=f"calibrate_threshold = '{m}'", decision=d)
        elif a == "escalate_preset":
            cur = body.get("presets") or "medium_quality"
            nxt = PRESETS[min(len(PRESETS) - 1, PRESETS.index(cur) + 1 if cur in PRESETS else 1)]
            body["presets"] = nxt
            body["fit_kwargs"].pop("num_stack_levels", None)
            body["fit_kwargs"].pop("num_bag_folds", None)
            hp = self._hp(True)
            if hp:
                body["fit_kwargs"]["hyperparameters"] = hp
            else:
                body["fit_kwargs"].pop("hyperparameters", None)
            body["time_limit"] = self._limit(d, max(base_t * 2, 0.35 * self.st["brief"]["budget_seconds"]))
            self.do_train("escalate_preset", f"Escalate to {nxt}", best, body, change=f"presets = '{nxt}', time_limit = {body['time_limit']:.0f}", decision=d)
        elif a == "add_families":
            hp = dict(body["fit_kwargs"].get("hyperparameters") or {})
            if self.st["facts"]["text_columns"]:
                hp.setdefault("GBM", {})
            else:
                for k in ("GBM", "CAT", "XT", "RF", "LR"):
                    hp.setdefault(k, {})
            body["fit_kwargs"]["hyperparameters"] = hp
            body["time_limit"] = self._limit(d, base_t * 1.5)
            self.do_train("add_families", "Add model families", best, body, change=f"hyperparameters ⊇ {{{', '.join(hp)}}}", decision=d)
        elif a == "reduce_latency":
            body["fit_kwargs"]["refit_full"] = True
            body["fit_kwargs"]["set_best_to_refit_full"] = True
            body["time_limit"] = self._limit(d, base_t)
            self.do_train("reduce_latency", "Refit one model per family (latency)", best, body,
                          change="fit_kwargs.refit_full = True, set_best_to_refit_full = True", decision=d)
        elif a == "synthesize_minority":
            if self.do_synthesize(d):
                body = self.base_body()
                body["time_limit"] = self._limit(d, base_t)
                self.do_train("retrain", "Retrain with synthetic examples", best, body, change="dataset += synthetic rows (training folds only)", decision=d)

    def do_synthesize(self, d: dict[str, Any], n: int = 40) -> bool:
        f = self.st["facts"]
        cls = f["minority_class"]
        s = self.step("synthesize", f"Write synthetic {cls} examples", decision=d)
        ds = self.mgr.app.state.datasets
        src = self.st["truth_dataset"]
        df = pd.read_csv(ds.csv_path(src))
        sp = json.loads(ds.splits_path(src).read_text())
        mine = df[df[f["label"]].astype(str) == cls]
        hold = set(sp["holdout"])
        examples = [{c: str(mine.iloc[i][c])[:300] for c in f["text_columns"]} for i in range(min(6, len(mine))) if mine.index[i] not in hold]
        prompt = (f"Write {n} new, varied, realistic examples of the class '{cls}' for a text classifier, in the same style "
                  f"as these real examples (do not copy them):\n{json.dumps(examples, ensure_ascii=False, indent=1)}\n\n"
                  f"Return only JSON: {{\"rows\": [{{{', '.join(repr(c) + ': ...' for c in f['text_columns'])}}}, ...]}}")
        obj, meta = llm.ask_json(prompt, timeout=float(self.st["brief"].get("llm_timeout", 60)) * 1.5)
        rows = [r for r in (obj or {}).get("rows", []) if isinstance(r, dict) and all(isinstance(r.get(c), str) and r[c].strip() for c in f["text_columns"])]
        if not rows:
            self.finish(s, status="failed", error="no model wrote usable rows", attempts=meta["attempts"])
            self.note(f"I could not get synthetic {cls} examples ({meta['source']}); continuing without them.")
            return False
        rng = np.random.default_rng(len(self.st["steps"]))
        new = []
        for i, r in enumerate(rows[:n]):
            tmpl = mine.iloc[int(rng.integers(len(mine)))].to_dict()
            for c in df.columns:
                if c in f["text_columns"]:
                    tmpl[c] = r[c]
                elif c not in f["features"] and c != f["label"]:
                    tmpl[c] = f"SYN-{self.aid[-4:]}-{i:03d}"  # id columns
            tmpl[f["label"]] = cls
            new.append(tmpl)
        add = pd.DataFrame(new, columns=df.columns)
        df2 = pd.concat([df, add], ignore_index=True)
        roots = self._lineage_rows(src)
        k = sp["n_folds"]
        new_sp = {**sp, "fold": list(sp["fold"]) + [i % k for i in range(len(add))], "derived_from": src}
        n_ver = len(self.st["dataset_versions"]) + 2
        root_name = f["dataset_name"].replace(" (sample)", "")
        syn_start = len(df)
        lineage = {"parent": src, "root": self.st["brief"]["dataset_id"], "version": n_ver, "autopilot": self.aid,
                   "relabelled": 0, "dropped": 0, "synthetic": len(add), "synthetic_rows": list(range(syn_start, len(df2))),
                   "synthetic_source": meta["source"], "root_rows": [int(x) for x in roots] + [-1 - i for i in range(len(add))],
                   "changes": [{"op": "synthetic", "row": syn_start + i, "label": cls} for i in range(len(add))]}
        rec = ds.create_version(src, df2, new_sp, name=f"{root_name} · v{n_ver}",
                                note=f"Autopilot {self.aid}: {len(add)} synthetic {cls} rows (training folds only) on {src}",
                                lineage=lineage)
        self.st["dataset_versions"].append({"id": rec["id"], "name": rec["name"], "parent": src, "version": n_ver, "rows": rec["rows"],
                                            "relabelled": 0, "dropped": 0, "synthetic": len(add), "relabelled_holdout": 0,
                                            "synthetic_class": cls, "synthetic_source": meta["source"], "created_at": now(),
                                            "synthetic_examples": [{c: r[c] for c in f["text_columns"]} for r in rows[:3]]})
        self.st["truth_dataset"] = rec["id"]
        self._holdout_cache.clear()
        self.finish(s, dataset_id=rec["id"], synthetic=len(add), source=meta["source"], model=meta.get("model"))
        self.note(f"Dataset v{n_ver}: {len(add)} synthetic {cls} tickets written by {llm.SOURCES.get(meta['source'], meta['source'])}, "
                  f"tagged synthetic in the lineage, placed in training folds only; the holdout is unchanged.", layer="layer-2",
                  source=meta["source"], model=meta.get("model"))
        return True

    # ------------------------------------------------------------------ the end

    def finalize(self, cancelled: bool = False) -> None:
        st = self.st
        if any(s.get("run_id") for s in st["steps"]):
            self.rescore_all()
        best = self.best_step()
        base = self.baseline()
        checks = (best or {}).get("checks") or M.check(st["brief"]["requirements"], {}, None)
        met = bool(best and best.get("met"))
        st["verdict"] = {"met": met, "checks": checks, "best_step": best and best["id"], "best_run_id": best and best["run_id"],
                         "score": best and best["score"], "baseline_score": base and base["score"],
                         "delta": (best["score"] - base["score"]) if best and base and best["score"] is not None and base["score"] is not None else None,
                         "spent_seconds": round(self.spent(), 1), "budget_seconds": st["brief"]["budget_seconds"],
                         "dataset_id": st["truth_dataset"]}
        s = self.step("final", "Requirements met" if met else "Requirements not met")
        s["met"] = met
        s["run_id_final"] = best and best["run_id"]
        unmet = [c for c in checks if not c["ok"]]
        txt = ("Met: " if met else "Not met: ") + "; ".join(
            f"{c['metric']} {('—' if c['observed'] is None else format(c['observed'], '.4g'))} {c['op']} {c['value']:g} {'✓' if c['ok'] else '✗'}" for c in checks)
        if best:
            txt += f". Model: {best['run_id']} ({best['title'].lower()}) on {st['truth_dataset']}"
        if st["verdict"]["delta"] is not None:
            txt += f", Δ{st['primary']} {st['verdict']['delta']:+.4f} against the baseline"
        txt += f". Spent {self.spent():.0f} of {st['brief']['budget_seconds']:.0f} s."
        if unmet and not cancelled:
            txt += " " + " ".join(f"{c['name']} is short by {abs(c['gap']):.4g}." for c in unmet if c.get("gap") is not None)
        self.finish(s, status="done", text=txt)
        st["status"] = "cancelled" if cancelled else "finished"
        st["finished_at"] = now()
        self.note(txt, layer="layer-1")


def bulk_accept(st: dict[str, Any], min_conf: float, kinds: list[str] | None = None) -> int:
    n = 0
    for it in st["inbox"]["items"]:
        if it.get("answer") or it["confidence"] < min_conf or (kinds and it["kind"] not in kinds):
            continue
        if it["suggestion"] == "relabel" and it.get("suggested"):
            it["answer"], it["answer_label"] = "relabel", it["suggested"]
        elif it["suggestion"] == "drop":
            it["answer"] = "drop"
        else:
            it["answer"] = "skip"
        it["answered_by"] = "bulk"
        n += 1
    return n


def new_autopilot(brief: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {"id": "ap_" + secrets.token_hex(4), "created_at": now(), "status": "queued", "brief": brief,
            "base_url": base_url, "steps": [], "decisions": [], "log": [], "inbox": None, "dataset_versions": [],
            "facts": None, "verdict": None, "error": None, "current": None}
