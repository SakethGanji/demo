"""D4 · Review: an LLM reads the run and proposes hypotheses; the platform proves or rejects each one with a typed
experiment on the same holdout, and writes an improvement plan. The numbers always come from code.

The loop (AIDE's journal and greedy search; AutoGluon Assistant's role split; free-form code is never run):

  1. perceive   the data: columns, types, missing shares, examples, the Data Doctor's findings
  2. task       the task and the decision point: what is predicted, when, for which decision, what an error costs
  3. errors     Data Doctor + Model Doctor + What's missing + Holdout Errors: where and why the model fails, and the
                domain gaps (information typically predictive for this task that the table lacks: suggestions only)
  4. propose    hypotheses, each mapped to ONE typed experiment of `experiments.KINDS` (validated; anything else is
                rejected before it runs)

Then each hypothesis runs through `POST /api/runs/{node}/experiments` (in process, the route the UI calls) and its Δ
is read from `GET …/experiments`: paired bootstrap on the shared holdout rows, in the run's own metric (expected cost
per 1,000 for cost runs). The journal records hypothesis → experiment → Δ with CI → verdict:

  kept         the CI is above 0: better beyond noise. The child becomes the best node (greedy).
  kept-honest  the ablation of a leak suspect: the score falls and that is the point; the child becomes the node.
  equivalent   a simpler option within noise (a simpler rung, a dropped feature): an option, not a new node.
  info         a cost-ratio sensitivity: what confirming the ratio is worth; never a new node.
  rejected     within noise or worse, or nothing changed (Feature Lab kept nothing, cleanlab flagged too few rows).

Policy: greedy on the best node; at most `max_experiments` trained experiments and `max_minutes` of wall time (checked
before each start); a second proposal round only after a new node; stop after 3 trained experiments in a row that do
not beat noise. Instant experiments (serve_rung, cost_ratio) read the run's files and are not counted.

Output: an improvement plan (what was tried, what helped with its numbers, what the bank should do next), computed from
the journal and the final Doctor. The LLM may word a summary and add actions; the number guard blocks any figure not in
the journal. Domain gaps ({title, why}) fill What's missing's slot, labelled as suggestions, with no numbers at all.
Offline (no model): the deterministic Doctor proposes the same kinds from its rules; the plan is the computed one.
Every review is written to the audit log with its inputs' hash, each prompt's hash, the model and the decider.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from .. import llm

SCHEMA = "review/1"
MAX_EXPERIMENTS, MAX_MINUTES = 5, 30
MAX_ROUNDS = 2
STOP_AFTER = 3  # trained experiments in a row within noise or worse
POLL_S = 8
RULES = {
    "verdict": "kept: Δ CI above 0 (better beyond noise) → the new best node · kept-honest: ablation of a leak suspect "
               "(the honest score) → the new node · equivalent: a simpler option whose CI includes 0 · info: a cost-ratio "
               "sensitivity · rejected: within noise, worse, or nothing changed",
    "policy": f"Greedy on the best node; at most {MAX_EXPERIMENTS} trained experiments and {MAX_MINUTES} minutes (checked "
              f"before each start); a second proposal round only after a new node; stop after {STOP_AFTER} trained experiments "
              "in a row that do not beat noise. Instant experiments (serve_rung, cost_ratio) are not counted.",
    "delta": "Δ = score(child) − score(node) on the shared holdout rows, paired bootstrap (B = 1,000), better is positive; "
             "the run's own metric (expected cost per 1,000 for cost runs, AUC for binary, accuracy for multiclass, RMSE "
             "for regression). serve_rung: the D2 ladder's paired gap. segment: G5's routed vs global on its holdout.",
    "guard": "Every figure in the language model's text must appear in the journal; text with any other figure is blocked "
             "and the computed text stands. Domain gaps carry no figures at all.",
}

_tasks: dict[str, asyncio.Task] = {}
C_TITLES = {"threshold": "move the threshold to the cost-optimal one on recent data",
            "recalibrate": "recalibrate the probabilities on recent data"}


# --------------------------------------------------------------------------------------------------------- storage


def _dir(rec: dict[str, Any]) -> Path:
    d = Path(rec["config"]["run_dir"]) / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(rec: dict[str, Any], rv: dict[str, Any]) -> None:
    p = _dir(rec) / f"{rv['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rv, default=lambda v: v.item() if hasattr(v, "item") else str(v)))
    tmp.replace(p)


def load(rec: dict[str, Any], rid: str) -> dict[str, Any] | None:
    p = _dir(rec) / f"{rid}.json"
    if not p.exists():
        return None
    rv = json.loads(p.read_text())
    if rv.get("status") in ("queued", "running") and rid not in _tasks:
        rv["status"] = "interrupted"  # the API restarted while it ran; its finished experiments stay in the journal
        rv["stop_reason"] = "The API restarted while the review ran; start it again (finished experiments are reused)."
    return rv


def list_reviews(rec: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for p in sorted(_dir(rec).glob("rv_*.json")):
        rv = load(rec, p.stem)
        if rv:
            out.append({k: rv.get(k) for k in ("id", "status", "mode", "decider", "started_at", "finished_at", "by", "stop_reason")}
                       | {"experiments": len(rv.get("journal") or []), "kept": sum(1 for e in rv.get("journal") or []
                                                                                  if e.get("verdict") in ("kept", "kept-honest"))})
    out.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return out


def latest_done(rec: dict[str, Any]) -> dict[str, Any] | None:
    for r in list_reviews(rec):
        if r["status"] == "done":
            return load(rec, r["id"])
    return None


def running(rec: dict[str, Any]) -> str | None:
    return next((r["id"] for r in list_reviews(rec) if r["status"] in ("queued", "running")), None)


def _now() -> str:
    from ..datasets import now

    return now()


def _sha(x: Any) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


# --------------------------------------------------------------------------------------------------------- context


def _r(x: Any, d: int = 4) -> Any:
    if isinstance(x, float):
        return round(x, d)
    if isinstance(x, list):
        return [_r(v, d) for v in x]
    if isinstance(x, dict):
        return {k: _r(v, d) for k, v in x.items()}
    return x


def digest(rec: dict[str, Any], doctor: dict[str, Any], dd: dict[str, Any] | None, ds: dict[str, Any] | None) -> dict[str, Any]:
    """What the review reads: compact, every number as the platform computed it (rounded to 4 places)."""
    res = rec.get("result") or {}
    cfg = rec.get("config") or {}
    ho = (res.get("holdout") or {}).get("metrics") or {}
    run = {"id": rec["id"], "name": rec["name"], "label": rec["label"], "problem_type": res.get("problem_type"),
           "positive_class": res.get("positive_class"), "eval_metric": (res.get("eval_metric") or {}).get("name"),
           "holdout": {k: v for k, v in (ho.items() if isinstance(ho, dict) else []) if isinstance(v, (int, float))} or None,
           "features": len(res.get("features") or []), "best_model": res.get("best_model"),
           "cost": cfg.get("cost") and {k: cfg["cost"].get(k) for k in ("c_fp", "c_fn", "g_tp", "bayes_threshold")},
           "threshold": (res.get("threshold") or {}).get("value"), "dropped": cfg.get("drop_columns"),
           "decision_time": cfg.get("decision_time") and {k: cfg["decision_time"].get(k) for k in ("not_known", "fairness_only", "dropped")}}
    cols = []
    for c in ((ds or {}).get("profile") or {}).get("columns") or []:
        cols.append({"name": c["name"], "type": c.get("type"), "role": c.get("role"), "missing": c.get("missing_share"),
                     "unique": c.get("unique"), "example": str(c.get("example"))[:60],
                     "top": [str(t["value"])[:30] for t in (c.get("top") or [])[:4]] or None})
    data = {"rows": (ds or {}).get("rows"), "name": (ds or {}).get("name"), "columns": cols[:80],
            "findings": [{"id": f["id"], "severity": f["severity"], "title": f["title"], "numbers": f.get("numbers"),
                          "columns": f.get("columns"),
                          "fix": {"kind": (f.get("fix") or {}).get("kind"), "selected": (f.get("fix") or {}).get("selected"),
                                  "transforms": (f.get("fix") or {}).get("transforms")}} for f in (dd or {}).get("findings") or []]}
    chk = lambda c: {"id": c["id"], "severity": c.get("severity"), "reason": c.get("reason")}  # noqa: E731
    doc = {"headline": doctor.get("headline"), "primary": doctor.get("primary") and chk(doctor["primary"]),
           "secondary": [chk(c) for c in doctor.get("secondary") or []],
           "findings": [chk(c) for c in doctor.get("checks") or [] if c.get("status") == "finding"]}
    lk = next((c for c in doctor.get("checks") or [] if c["id"] == "leakage" and c["status"] == "finding"), None)
    if lk:
        doc["leak_suspects"] = [s["feature"] for s in (lk.get("evidence") or {}).get("suspects") or [] if s.get("reason") != "identifier"]
    mi = doctor.get("missing_info") or {}
    miss = {"headline": mi.get("headline"), "caveat": mi.get("caveat"), "irreducible": mi.get("irreducible"),
            "gaps": [{"id": g["id"], "kind": g["kind"], "title": g["title"], "finding": g["finding"], "request": g["request"],
                      "gain": g.get("gain") and {k: g["gain"].get(k) for k in ("delta", "ci", "metric", "simulated")}}
                     for g in mi.get("gaps") or []]}
    imp = [{"id": x["id"], "title": x["title"], "delta": x.get("delta"), "ci": x.get("ci"), "metric": x.get("metric"),
            "source": x.get("source")} for x in doctor.get("improve") or []]
    lad = res.get("ladder") or {}
    ladder = lad and {"metric": lad.get("metric_label"), "best_rung": lad.get("best_rung"),
                      "recommended": (lad.get("recommended") or {}).get("name"), "sentence": lad.get("sentence"),
                      "rungs": [{"rung": r["rung"], "name": r.get("name"), "model": r.get("model"), "score": r.get("score"),
                                 "gap": (r.get("gap") or {}).get("delta"), "gap_ci": (r.get("gap") or {}).get("ci"),
                                 "within": r.get("within"), "latency_ms_p95": r.get("latency_ms_p95")} for r in lad.get("rungs") or []]}
    ev = doctor.get("errors_view") or {}
    errs = {"served": ev.get("served"), "slices": [{k: s.get(k) for k in ("label", "column", "value", "n", "rate", "lift")}
                                                    for s in (ev.get("slices") or [])[:5]]}
    return _r({"run": run, "data": data, "doctor": doc, "missing": miss, "improve": imp, "ladder": ladder or None, "errors": errs})


# --------------------------------------------------------------------------------------------------------- prompts

ROLE_INTRO = ("You are one role in the review of a credit-risk model inside a bank's AutoML workbench. The platform "
              "computes every number; you read them and reason. Reply with JSON only, no prose around it.")


def _p_perceive(c: dict[str, Any]) -> str:
    return (f"{ROLE_INTRO}\nROLE 1 · PERCEIVE THE DATA.\nDATA:\n{json.dumps({'run': c['run'], 'data': c['data']}, ensure_ascii=False)}\n"
            "Describe the table as an analyst would: what each group of columns measures, which columns look recorded "
            "after the decision (outcome-side), which are coded or thin, which are protected. Return "
            '{"data_summary": str (≤ 80 words), "columns_of_note": [{"column": str, "note": str}] (≤ 8), '
            '"decision_time_risks": [column names]}')


def _p_task(c: dict[str, Any], perceive: dict[str, Any]) -> str:
    return (f"{ROLE_INTRO}\nROLE 2 · DESCRIBE THE TASK AND THE DECISION POINT.\nRUN:\n{json.dumps(c['run'], ensure_ascii=False)}\n"
            f"PERCEPTION:\n{json.dumps(perceive, ensure_ascii=False)}\n"
            "Say what is predicted, for which decision, at what moment (what is known then), and what each kind of error "
            'costs the bank. Return {"task": str (≤ 50 words), "decision_point": str (≤ 40 words), "error_costs": str (≤ 40 words)}')


def _p_errors(c: dict[str, Any], perceive: dict[str, Any], task: dict[str, Any]) -> str:
    facts = {k: c[k] for k in ("doctor", "missing", "improve", "ladder", "errors")}
    return (f"{ROLE_INTRO}\nROLE 3 · ANALYSE THE ERRORS.\nTASK:\n{json.dumps(task, ensure_ascii=False)}\n"
            f"PERCEPTION:\n{json.dumps(perceive, ensure_ascii=False)}\nFINDINGS (computed):\n{json.dumps(facts, ensure_ascii=False)}\n"
            "Explain why the model errs: the Data Doctor's data problems, the Model Doctor's limiting factor, What's missing "
            "(information the table lacks), and where the errors concentrate. Quote numbers only exactly as given. Then list "
            "DOMAIN GAPS: information typically predictive for THIS task that the table does not contain (for example credit "
            "bureau data, behavioural or transaction history, tenure, income verification), as suggestions to investigate. "
            "Domain gaps must contain no numbers. Return "
            '{"data_doctor": str, "model_doctor": str, "whats_missing": str, "errors": str (each ≤ 70 words), '
            '"domain_gaps": [{"title": str (≤ 8 words), "why": str (≤ 35 words)}] (3–6)}')


def _p_propose(c: dict[str, Any], analysis: dict[str, Any], node: dict[str, Any], journal: list[dict[str, Any]], kinds: dict[str, str]) -> str:
    from .experiments import KINDS

    fixes = [{"finding": f["id"], "title": f["title"], "transforms": f["fix"]["transforms"]} for f in c["data"]["findings"]
             if (f.get("fix") or {}).get("transforms")]
    done = [{"kind": e["experiment"]["kind"], "spec": e["experiment"].get("spec"), "verdict": e.get("verdict"),
             "delta": (e.get("result") or {}).get("delta"), "ci": (e.get("result") or {}).get("ci")} for e in journal]
    return (f"{ROLE_INTRO}\nROLE 4 · PROPOSE HYPOTHESES.\nEach hypothesis must map to exactly ONE typed experiment from this "
            f"whitelist (anything else is rejected):\n{json.dumps(kinds, ensure_ascii=False)}\n"
            f"The node the experiments start from: {json.dumps(node, ensure_ascii=False)}\n"
            f"Features of the node: {json.dumps(node.get('features'), ensure_ascii=False)}\n"
            f"Data Doctor fixes available (use these transforms as given for apply_data_fix): {json.dumps(fixes, ensure_ascii=False)}\n"
            f"Model ladder: {json.dumps(c['ladder'], ensure_ascii=False)}\nRun: {json.dumps(c['run'], ensure_ascii=False)}\n"
            f"ANALYSIS:\n{json.dumps(analysis, ensure_ascii=False)}\nALREADY TRIED (journal): {json.dumps(done, ensure_ascii=False)}\n"
            "Rules: a leak suspect from the Model Doctor is ablated first. Do not repeat a tried experiment. cost_ratio only on "
            "cost runs (try a lower and a higher ratio than the run's). serve_rung when the ladder recommends a simpler rung "
            "(1 or 2) than the model the run serves. Never merge classes of the target (that belongs in the plan). Prefer hypotheses that test whether the "
            "information is in the table at all. At most 6, most promising first. Return "
            '{"hypotheses": [{"title": str (≤ 12 words), "why": str (≤ 40 words), "expected": "better"|"same"|"worse", '
            f'"experiment": {{"kind": one of {list(KINDS)}, ...its fields}}}}]}}')


def _p_plan(c: dict[str, Any], journal_facts: dict[str, Any], plan: dict[str, Any]) -> str:
    return (f"{ROLE_INTRO}\nROLE 5 · WRITE THE IMPROVEMENT PLAN for the bank's model owner.\nJOURNAL (every number you may use):\n"
            f"{json.dumps(journal_facts, ensure_ascii=False)}\nCOMPUTED PLAN:\n{json.dumps(plan, ensure_ascii=False)}\n"
            "Write a summary of what was tried and what helped, with the journal's numbers exactly as written, and up to 4 "
            "extra next actions the computed plan lacks (collect or join a source, redefine a label, drop a column, confirm "
            "a cost ratio, fix how a field is captured). Any figure that is not in the journal will be blocked. Return "
            '{"summary": str (≤ 110 words), "next": [{"kind": "collect"|"join"|"redefine_label"|"drop"|"confirm_cost"|"fix_capture"|"other", '
            '"title": str (≤ 10 words), "why": str (≤ 40 words)}]}')


# --------------------------------------------------------------------------------------------------------- rules (offline)


def rule_hypotheses(c: dict[str, Any], node: dict[str, Any], doctor: dict[str, Any]) -> list[dict[str, Any]]:
    """The deterministic Doctor's proposals, the same typed kinds as the LLM's: leak ablation, the pre-selected Data
    Doctor fixes, clean labels, new features, budget / preset, a segment, a simpler rung, cost-ratio sensitivity."""
    out: list[dict[str, Any]] = []
    run = c["run"]
    feats = set(node.get("features") or [])
    checks = {x["id"]: x for x in doctor.get("checks") or []}
    lk = checks.get("leakage") or {}
    if lk.get("status") == "finding":
        cols = [s["feature"] for s in lk["evidence"]["suspects"] if s.get("reason") != "identifier" and s["feature"] in feats]
        if cols:
            out.append({"title": f"{', '.join(cols)} leaks the outcome", "why": lk["reason"][:300], "expected": "worse",
                        "experiment": {"kind": "ablate", "columns": cols}, "source": "rule: leakage finding → ablation"})
    tr = []
    for f in c["data"]["findings"]:
        fx = f.get("fix") or {}
        if fx.get("selected") and fx.get("transforms") and f["id"] not in ("conflicts", "label_quality", "class_balance"):
            tr += [t for t in fx["transforms"] if t.get("op") not in ("drop_rows", "merge_classes")
                   and (t.get("column") is None or t.get("column") in feats or t.get("op") == "drop_duplicates")]
    if tr:
        out.append({"title": "The Data Doctor's fixes help the model", "why": "Pre-selected fixes: " + "; ".join(
            f["title"] for f in c["data"]["findings"] if (f.get("fix") or {}).get("selected") and (f.get("fix") or {}).get("transforms"))[:300],
                    "expected": "better", "experiment": {"kind": "apply_data_fix", "transforms": tr}, "source": "rule: Data Doctor pre-selected fixes"})
    gaps = {g["id"]: g for g in c["missing"]["gaps"]}
    lab = gaps.get("labels")
    noisy = (checks.get("label_noise") or {}).get("status") == "finding"
    lab_gain = bool(lab and lab.get("gain") and lab["gain"].get("ci") and lab["gain"]["ci"][1] > 0)
    if run["problem_type"] in ("binary", "multiclass") and (noisy or lab_gain):
        out.append({"title": "Noisy labels hold the model back", "why": (lab or {}).get("finding") or "label-noise finding",
                    "expected": "better", "experiment": {"kind": "clean_labels"}, "source": "rule: label quality → clean labels"})
    numeric = [col for col in c["data"]["columns"] if col["name"] in feats and col.get("type") == "number" and (col.get("unique") or 0) > 20]
    if "new_features" not in gaps and len(numeric) >= 2:
        out.append({"title": "Ratios and differences of the amounts add signal", "why": "Feature Lab has not run on this "
                    "dataset: its typed features (ratios, differences, aggregates) are scored on the training folds only.",
                    "expected": "same", "experiment": {"kind": "add_features"}, "source": "rule: new features not measured"})
    if (checks.get("budget") or {}).get("status") == "finding":
        out.append({"title": "The time budget cut the search short", "why": checks["budget"]["reason"][:300], "expected": "better",
                    "experiment": {"kind": "budget"}, "source": "rule: time-budget finding"})
    if (checks.get("underfit") or {}).get("status") == "finding":
        out.append({"title": "A stronger preset fits better", "why": checks["underfit"]["reason"][:300], "expected": "better",
                    "experiment": {"kind": "preset"}, "source": "rule: underfit finding"})
    if run["problem_type"] in ("binary", "regression"):
        for s in c["errors"].get("slices") or []:
            col = s.get("column")
            colinfo = next((x for x in c["data"]["columns"] if x["name"] == col), None)
            if col in feats and colinfo and colinfo.get("type") == "category" and 2 <= (colinfo.get("unique") or 0) <= 12 and (s.get("lift") or 0) >= 1.5:
                out.append({"title": f"A separate model per {col} fits its segments", "why": f"Errors concentrate in {s.get('label')}.",
                            "expected": "same", "experiment": {"kind": "segment", "column": col}, "source": "rule: concentration in a category"})
                break
    lad = c.get("ladder") or {}
    rec = next((r for r in lad.get("rungs") or [] if r.get("name") == lad.get("recommended")), None)
    if rec and rec["rung"] in (1, 2) and rec.get("model") != run.get("best_model"):
        out.append({"title": f"The {rec['name']} is as good as the best", "why": lad.get("sentence") or "", "expected": "same",
                    "experiment": {"kind": "serve_rung", "rung": rec["rung"]}, "source": "rule: ladder recommends a simpler rung"})
    cost = run.get("cost")
    if cost and cost.get("c_fn") and cost.get("c_fp"):
        r0 = cost["c_fn"] / cost["c_fp"]
        for r in sorted({max(1.0, round(r0 / 2, 2)), round(r0 * 2, 2)}):
            if abs(r - r0) > 1e-9:
                out.append({"title": f"The decision changes at a {r:g}:1 cost ratio", "why": f"The run assumes {r0:g}:1; the ratio sets "
                            "the threshold more than any model change.", "expected": "same",
                            "experiment": {"kind": "cost_ratio", "c_fn": r, "c_fp": 1}, "source": "rule: cost run → ratio sensitivity"})
    return out


# --------------------------------------------------------------------------------------------------------- verdict


def verdict(kind: str, res: dict[str, Any] | None, leak_cols: set[str], spec: dict[str, Any]) -> tuple[str, str]:
    if not res or res.get("delta") is None:
        return "rejected", "no result"
    lo, hi = res["ci"]
    d = res["delta"]
    if kind == "ablate" and set(spec.get("columns") or []) & leak_cols:
        return "kept-honest", (f"the leak suspect removed: {res['metric']} {res['parent']:.4f} → {res['child']:.4f}; the run without it "
                               "is the honest score")
    if kind == "cost_ratio":
        return "info", "a sensitivity of the decision to the cost ratio, not a model change"
    if kind == "serve_rung":
        if lo > 0:
            return "kept", "the simpler rung scores better beyond noise"
        if hi >= 0:
            return "equivalent", "the simpler rung is within noise of the best"
        return "rejected", "the simpler rung is worse beyond noise"
    if lo > 0:
        return "kept", f"Δ {d:+.4f}: better beyond noise (CI above 0)"
    if hi < 0:
        return "rejected", f"Δ {d:+.4f}: worse beyond noise (CI below 0)"
    if kind == "ablate":
        return "equivalent", "dropping it costs nothing measurable (CI includes 0): a simpler model"
    return "rejected", f"Δ {d:+.4f}: within noise (CI includes 0)"


# --------------------------------------------------------------------------------------------------------- the loop


class Review:
    def __init__(self, app, rec: dict[str, Any], rv: dict[str, Any]):
        self.app = app
        self.rec = rec
        self.rv = rv
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://review", timeout=900)

    def save(self) -> None:
        save(self.rec, self.rv)

    def progress(self, text: str) -> None:
        self.rv["progress"] = text
        self.save()

    async def get(self, path: str, **params) -> Any:
        r = await self.client.get(path, params=params or None)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    async def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        r = await self.client.post(path, json=body)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"detail": r.text[:300]}

    async def ask(self, role: str, prompt: str, facts: Any) -> dict[str, Any] | None:
        entry = {"role": role, "status": "running", "started_at": _now(), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16]}
        self.rv["roles"].append(entry)
        self.progress(f"role {role}")
        if self.rv["mode"] == "offline":
            entry.update(status="skipped", decider=llm.SOURCES[llm.COMPUTED], why="offline: the deterministic Doctor proposes")
            self.save()
            return None
        obj, meta = await llm.ask_json_async(prompt, facts=facts, timeout=240, path_timeout=180, max_tokens=4000)
        entry.update(status="done" if obj is not None else "failed", decider=meta.get("decider"), source=meta.get("source"),
                     model=meta.get("model"), seconds=meta.get("seconds"), cached=meta.get("cached"),
                     facts_sha256=meta.get("facts_sha256"), output=obj, error=None if obj is not None else meta.get("reason"),
                     attempts=[{k: a.get(k) for k in ("source", "ok", "error", "seconds")} for a in meta.get("attempts") or []])
        if obj is not None:
            self.rv["model"] = meta.get("model") or self.rv.get("model")
            self.rv["decider"] = meta.get("decider")
        self.save()
        return obj

    def raw(self, run_id: str) -> dict[str, Any]:
        """The stored run record (config + result); GET /api/runs/{id} flattens it for the UI."""
        r = self.app.state.runs.get(run_id)
        if not r:
            raise RuntimeError(f"run {run_id} is gone")
        return r

    async def node_info(self, run_id: str) -> dict[str, Any]:
        r = self.raw(run_id)
        res = r.get("result") or {}
        return {"run_id": run_id, "name": r.get("name"), "dataset_id": r.get("dataset_id"), "features": res.get("features") or [],
                "problem_type": res.get("problem_type")}

    async def run(self) -> None:
        rv = self.rv
        t0 = time.monotonic()
        try:
            rv["status"] = "running"
            self.progress("reading the run, the Data Doctor and the Model Doctor")
            rid = self.rec["id"]
            doctor = await self.get(f"/api/runs/{rid}/doctor")
            ds = await self.get(f"/api/datasets/{self.rec['dataset_id']}")
            try:
                dd = await self.get(f"/api/datasets/{self.rec['dataset_id']}/data-doctor")
            except RuntimeError:
                dd = None
            rec = self.raw(rid)
            c = digest(rec, doctor, dd, ds)
            rv["inputs_sha256"] = _sha(c)[:16]
            rv["context"] = c
            self.save()
            # roles 1–3
            perceive = await self.ask("perceive", _p_perceive(c), {"run": c["run"], "data": c["data"]}) or {}
            task = await self.ask("task", _p_task(c, perceive), c["run"]) or {}
            analysis = await self.ask("errors", _p_errors(c, perceive, task), {k: c[k] for k in ("doctor", "missing", "improve", "ladder", "errors")}) or {}
            rv["analysis"] = {"perceive": perceive or None, "task": task or None, "errors": analysis or None}
            rv["domain_gaps"] = clean_domain_gaps(analysis.get("domain_gaps"))
            self.save()
            leak_cols = set(c["doctor"].get("leak_suspects") or [])
            node = await self.node_info(rid)
            rv["node"] = {"start": rid, "best": rid, "path": [rid]}
            trained = 0
            misses = 0
            rounds = 0
            seen: set[str] = set()
            from .experiments import DESCRIBE, INSTANT, validate

            node_doctor = doctor
            while rounds < MAX_ROUNDS:
                rounds += 1
                self.progress(f"round {rounds}: proposing hypotheses")
                hyps: list[dict[str, Any]] = []
                if rv["mode"] == "llm":
                    obj = await self.ask("propose", _p_propose(c, analysis, node, rv["journal"], DESCRIBE),
                                         {"node": node, "journal": rv["journal"]})
                    for h in (obj or {}).get("hypotheses") or []:
                        if isinstance(h, dict) and isinstance(h.get("experiment"), dict):
                            hyps.append({**h, "source": "llm"})
                    if obj is None:
                        rv["mode_note"] = "the language model did not answer: the deterministic Doctor proposed instead"
                if not hyps:
                    hyps = rule_hypotheses(c, node, node_doctor)
                    for h in hyps:
                        h.setdefault("source", "rule")
                # validate and order: a leak ablation first, then the instant ones, then the rest in the proposer's order
                nrec = self.raw(node["run_id"])
                ok = []
                for h in hyps[:8]:
                    h["id"] = "h" + str(len(rv["hypotheses"]) + 1)
                    h["round"] = rounds
                    h["node"] = node["run_id"]
                    try:
                        spec = validate(nrec, h["experiment"])
                        key = node["run_id"] + json.dumps(spec, sort_keys=True)
                        if key in seen:
                            raise ValueError("already tried on this node")
                        seen.add(key)
                        h.update(valid=True, spec=spec)
                        ok.append(h)
                    except ValueError as e:
                        h.update(valid=False, invalid_reason=str(e))
                    rv["hypotheses"].append({k: h.get(k) for k in ("id", "title", "why", "expected", "experiment", "source", "valid",
                                                                  "invalid_reason", "round", "node")})
                self.save()
                ok.sort(key=lambda h: (0 if h["spec"]["kind"] == "ablate" and set(h["spec"].get("columns") or []) & leak_cols else
                                       1 if h["spec"]["kind"] in INSTANT else 2))
                moved = False
                for h in ok:
                    kind = h["spec"]["kind"]
                    instant = kind in INSTANT
                    if not instant:
                        mins = (time.monotonic() - t0) / 60
                        if trained >= rv["options"]["max_experiments"]:
                            rv["stop_reason"] = f"the cap of {rv['options']['max_experiments']} trained experiments"
                            break
                        if mins >= rv["options"]["max_minutes"]:
                            rv["stop_reason"] = f"the cap of {rv['options']['max_minutes']} minutes ({mins:.0f} min used)"
                            break
                        if misses >= STOP_AFTER:
                            rv["stop_reason"] = f"{STOP_AFTER} trained experiments in a row did not beat noise"
                            break
                    e = await self.experiment(h, node, leak_cols)
                    if not instant and e["status"] != "invalid":
                        trained += 1
                        misses = 0 if e.get("verdict") in ("kept", "kept-honest") else misses + 1
                    if e.get("verdict") in ("kept", "kept-honest") and e.get("run_id"):
                        node = await self.node_info(e["run_id"])
                        rv["node"]["best"] = e["run_id"]
                        rv["node"]["path"].append(e["run_id"])
                        moved = True
                        self.save()
                        break  # greedy: the next hypotheses start from the new node
                if rv.get("stop_reason") or not moved:
                    if not rv.get("stop_reason"):
                        rv["stop_reason"] = ("every hypothesis was tried; nothing beat noise" if not any(
                            j.get("verdict") in ("kept", "kept-honest") for j in rv["journal"]) else "every hypothesis was tried")
                    break
                # a new node: read its Doctor for the next round
                self.progress(f"reading the Model Doctor of the new node {node['run_id']}")
                try:
                    node_doctor = await self.get(f"/api/runs/{node['run_id']}/doctor")
                    nrec2 = self.raw(node["run_id"])
                    c2 = digest(nrec2, node_doctor, dd, ds)
                    c = {**c, "doctor": c2["doctor"], "missing": c2["missing"], "improve": c2["improve"], "ladder": c2["ladder"],
                         "errors": c2["errors"], "run": c2["run"]}
                    leak_cols = leak_cols | set(c2["doctor"].get("leak_suspects") or [])
                except RuntimeError:
                    break
            else:
                rv["stop_reason"] = rv.get("stop_reason") or f"{MAX_ROUNDS} proposal rounds"
            await self.postdeploy(rid)
            # the plan, on the final node's Doctor
            self.progress("writing the improvement plan")
            final_doctor = node_doctor if node["run_id"] != rid else doctor
            if node["run_id"] != rid and final_doctor is doctor:
                final_doctor = await self.get(f"/api/runs/{node['run_id']}/doctor")
            plan = compute_plan(rv, c, final_doctor)
            facts = journal_facts(rv)
            if rv["mode"] == "llm":
                obj = await self.ask("plan", _p_plan(c, facts, plan), facts)
                plan = merge_llm_plan(plan, obj, facts, rv)
            plan["domain_gaps"] = rv.get("domain_gaps") or []
            rv["plan"] = plan
            rv["status"] = "done"
        except Exception as e:  # noqa: BLE001
            rv["status"] = "failed"
            rv["error"] = f"{type(e).__name__}: {e}"[:500]
        finally:
            rv["finished_at"] = _now()
            rv["seconds"] = round(time.monotonic() - t0, 1)
            rv["progress"] = None
            self.audit()
            self.save()
            await self.client.aclose()

    async def experiment(self, h: dict[str, Any], node: dict[str, Any], leak_cols: set[str]) -> dict[str, Any]:
        rv = self.rv
        spec = h["spec"]
        e = {"step": len(rv["journal"]) + 1, "hypothesis": {k: h.get(k) for k in ("id", "title", "why", "expected", "source")},
             "experiment": {"kind": spec["kind"], "spec": spec}, "node": node["run_id"], "status": "starting",
             "started_at": _now(), "run_id": None, "result": None, "verdict": None}
        rv["journal"].append(e)
        self.progress(f"experiment {e['step']}: {spec['kind']}")
        reuse = await self._reuse(node["run_id"], spec)
        if reuse:
            e.update(reused=True, run_id=reuse.get("run_id"), status=reuse.get("status"))
            x = reuse
        else:
            code, out = await self.post(f"/api/runs/{node['run_id']}/experiments", {**spec, "by": f"review {rv['id']} for {rv['by']}",
                                                                                     "review_id": rv["id"]})
            if code >= 400:
                e.update(status="invalid", verdict="rejected", reason=f"refused: {out.get('detail')}", finished_at=_now())
                self.save()
                return e
            e.update(run_id=out.get("run_id"), status=out.get("status"), prepared=out.get("prepared"), title=out.get("title"))
            self.save()
            x = None
            if out.get("status") == "no-change":
                e.update(verdict="rejected", reason=(out.get("prepared") or {}).get("why") or "nothing changed", finished_at=_now())
                self.save()
                return e
            if out.get("result") is not None:  # instant
                x = out
            else:
                while True:
                    r = await self.get(f"/api/runs/{out['run_id']}")
                    e["status"] = r["status"]
                    if r["status"] in ("succeeded", "failed", "cancelled"):
                        break
                    self.progress(f"experiment {e['step']}: {spec['kind']} training ({out['run_id']}, {r['status']})")
                    await asyncio.sleep(POLL_S)
                lst = await self.get(f"/api/runs/{node['run_id']}/experiments")
                x = next((i for i in lst["experiments"] if i.get("run_id") == out["run_id"]), None) or {}
        e["status"] = x.get("status") or e["status"]
        e["result"] = x.get("result")
        e["title"] = e.get("title") or x.get("title")
        if x.get("prepared") and not e.get("prepared"):
            e["prepared"] = x["prepared"]
        if e["status"] != "succeeded":
            e.update(verdict="rejected", reason=f"the experiment {e['status']}: {x.get('error') or ''}".strip())
        else:
            e["verdict"], e["reason"] = verdict(spec["kind"], e["result"], leak_cols, spec)
        e["finished_at"] = _now()
        self.save()
        return e

    async def postdeploy(self, rid: str) -> None:
        """A registered version of this run with a newer labelled batch: the instant post-deployment changes (threshold,
        recalibration) on that batch, as journal entries; a better one becomes a 'request' action in the plan."""
        try:
            vs = (await self.get("/api/registry/versions")).get("versions") or []
        except RuntimeError:
            return
        for v in [v for v in vs if v.get("run_id") == rid and v.get("status") == "ready"][:2]:
            ch = await self.get(f"/api/registry/versions/{v['id']}/changes")
            if not ch.get("eligible"):
                continue
            for kind in ("threshold", "recalibrate"):
                e = {"step": len(self.rv["journal"]) + 1, "hypothesis": {"id": f"pd-{kind}", "title": f"{ch['label']}: {C_TITLES[kind]}",
                                                                         "why": f"newest labelled batch {ch['newest_batch']['filename']}",
                                                                         "source": "rule: a registered version with a newer labelled batch"},
                     "experiment": {"kind": f"postdeploy:{kind}", "spec": {"kind": kind, "version_id": v["id"]}}, "node": rid,
                     "started_at": _now(), "status": "starting", "run_id": None}
                self.rv["journal"].append(e)
                code, out = await self.post(f"/api/registry/versions/{v['id']}/changes", {"kind": kind, "actor": f"review {self.rv['id']} for {self.rv['by']}",
                                                                                        "review_id": self.rv["id"]})
                if code >= 400:
                    e.update(status="invalid", verdict="rejected", reason=str(out.get("detail")))
                    continue
                r = out.get("result") or {}
                e.update(status=out.get("status"), change_id=out.get("id"), version_id=v["id"],
                         result={"metric": r.get("metric"), "parent": r.get("current"), "child": r.get("proposed"), "delta": r.get("delta"),
                                 "ci": r.get("ci"), "rows": r.get("rows"), "threshold": r.get("threshold"), "cost": r.get("cost")},
                         verdict="request" if out.get("verdict") == "better" else "rejected",
                         reason=("better beyond noise on the newest labelled batch: request it (four-eyes review, then promotion)"
                                 if out.get("verdict") == "better" else f"{out.get('verdict')} on the newest labelled batch"),
                         finished_at=_now())
                self.save()

    async def _reuse(self, node_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
        """An identical experiment already finished on this node (an earlier review, or the Doctor's own button)."""
        if spec["kind"] in ("cost_ratio", "serve_rung"):
            return None
        lst = await self.get(f"/api/runs/{node_id}/experiments")
        for i in lst["experiments"]:
            s = i.get("spec") or {"kind": i.get("kind"), **({"columns": i["columns"]} if i.get("columns") else {})}
            if i.get("status") == "succeeded" and i.get("result") and json.dumps(s, sort_keys=True) == json.dumps(spec, sort_keys=True):
                return i
        return None

    def audit(self) -> None:
        rv = self.rv
        try:
            reg = self.app.state.registry
            j = rv.get("journal") or []
            entry = reg.audit(actor=rv["by"], action="review", run_id=self.rec["id"],
                              comment=(f"review {rv['id']} ({rv['mode']}, {rv.get('decider') or 'computed'}): {len(j)} experiments, "
                                       f"{sum(1 for e in j if e.get('verdict') in ('kept', 'kept-honest'))} kept · {rv['status']}"),
                              details={"review_id": rv["id"], "inputs_sha256": rv.get("inputs_sha256"), "model": rv.get("model"),
                                       "decider": rv.get("decider"), "mode": rv["mode"], "status": rv["status"],
                                       "prompts": [{k: r.get(k) for k in ("role", "prompt_sha256", "facts_sha256", "model", "decider", "status")}
                                                   for r in rv.get("roles") or []],
                                       "experiments": [{"step": e["step"], "kind": e["experiment"]["kind"], "run_id": e.get("run_id"),
                                                        "verdict": e.get("verdict"), "delta": (e.get("result") or {}).get("delta"),
                                                        "ci": (e.get("result") or {}).get("ci")} for e in j],
                                       "best_node": (rv.get("node") or {}).get("best"), "options": rv.get("options")})
            rv["audit_id"] = entry.get("id")
        except Exception as e:  # noqa: BLE001
            rv["audit_error"] = f"{type(e).__name__}: {e}"[:200]


# --------------------------------------------------------------------------------------------------------- the plan


def clean_domain_gaps(items: Any) -> list[dict[str, Any]]:
    """{title, why} suggestions, with no figures (anything with a number is blocked: they are never measurements)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not isinstance(it.get("title"), str) or not isinstance(it.get("why"), str):
            continue
        nums = llm.unsupported_numbers(it["title"] + " " + it["why"], {})
        out.append({"title": it["title"][:80], "why": it["why"][:300], "blocked": bool(nums), "numbers": nums or None})
    return [x for x in out if not x["blocked"]][:6]


def _fmt(res: dict[str, Any]) -> str:
    return f"{res['metric']} {res['delta']:+.4f} [{res['ci'][0]:+.4f}, {res['ci'][1]:+.4f}]"


def journal_facts(rv: dict[str, Any]) -> dict[str, Any]:
    """The numbers the plan may quote: the journal (with each result) and the context it read."""
    return {"context": rv.get("context"), "journal": [{k: e.get(k) for k in ("step", "hypothesis", "experiment", "run_id", "result",
                                                                             "verdict", "reason", "prepared")} for e in rv.get("journal") or []]}


def compute_plan(rv: dict[str, Any], c: dict[str, Any], doctor: dict[str, Any]) -> dict[str, Any]:
    j = rv.get("journal") or []
    tried = [{"step": e["step"], "title": e["hypothesis"]["title"], "kind": e["experiment"]["kind"], "verdict": e.get("verdict"),
              "reason": e.get("reason"), "run_id": e.get("run_id"), "result": e.get("result") and {k: e["result"].get(k) for k in
                                                                                                   ("metric", "parent", "child", "delta", "ci")}}
             for e in j]
    helped = [t for t in tried if t["verdict"] in ("kept", "kept-honest", "equivalent") and t["result"]]
    nxt: list[dict[str, Any]] = []
    for e in j:
        r = e.get("result") or {}
        k = e["experiment"]["kind"]
        sp = e["experiment"]["spec"]
        if e.get("verdict") == "kept-honest":
            cols = ", ".join(sp.get("columns") or [])
            nxt.append({"kind": "drop", "title": f"Drop {cols} and confirm when it is known",
                        "why": f"Without {cols}: {r['metric']} {r['parent']:.4f} → {r['child']:.4f} ({_fmt(r)}). If {cols} is recorded "
                               "after the decision, the run without it is the honest model; answer the decision-time question in Build.",
                        "source": f"journal step {e['step']}", "run_id": e.get("run_id")})
        elif e.get("verdict") == "kept":
            nxt.append({"kind": "adopt", "title": f"Adopt: {e.get('title') or e['hypothesis']['title']}",
                        "why": f"{_fmt(r)} on the same holdout rows: better beyond noise. Register run {e.get('run_id')} after review.",
                        "source": f"journal step {e['step']}", "run_id": e.get("run_id")})
        elif e.get("verdict") == "request":
            nxt.append({"kind": "request", "title": f"Request: {e['hypothesis']['title']}",
                        "why": f"{_fmt(r)} on the newest labelled batch: better beyond noise. Registry → Changes → Request: a new "
                               "version, then the four-eyes review and promotion.", "source": f"journal step {e['step']}",
                        "version_id": e.get("version_id"), "change_id": e.get("change_id")})
        elif e.get("verdict") == "equivalent" and k == "serve_rung":
            rg = r.get("rung") or {}
            lat = (f" ({rg['latency_ms_p95']:.1f} ms vs {r['best']['latency_ms_p95']:.1f} ms per row)"
                   if rg.get("latency_ms_p95") and (r.get("best") or {}).get("latency_ms_p95") else "")
            nxt.append({"kind": "serve", "title": f"Serve the {rg.get('name')}", "why": f"Within noise of the best: {_fmt(r)}{lat}. "
                        "Simpler to explain and to validate.", "source": f"journal step {e['step']}", "serve_model": rg.get("model")})
        elif e.get("verdict") == "equivalent" and k == "ablate":
            nxt.append({"kind": "drop", "title": f"Drop {', '.join(sp.get('columns') or [])}: it adds nothing measurable",
                        "why": f"{_fmt(r)}: within noise.", "source": f"journal step {e['step']}"})
    cr = [e for e in j if e["experiment"]["kind"] == "cost_ratio" and e.get("result")]
    if cr or c["run"].get("cost"):
        cost = c["run"].get("cost") or {}
        r0 = (cost.get("c_fn") or 0) / (cost.get("c_fp") or 1) if cost else None
        parts = [f"if it is {e['result']['ratio']['alternative']:g}:1, keeping today's threshold costs {-e['result']['delta']:.1f} per 1,000 "
                 f"[{-e['result']['ci'][1]:.1f}, {-e['result']['ci'][0]:.1f}] (decline rate {e['result']['operating']['served_threshold']['under_alternative']['decline_rate']:.3f} → "
                 f"{e['result']['operating']['alternative_threshold']['under_alternative']['decline_rate']:.3f})" for e in cr]
        nxt.append({"kind": "confirm_cost", "title": f"Confirm the cost ratio{f' (now {r0:g}:1)' if r0 else ''} with the business",
                    "why": ("; ".join(parts) + ". " if parts else "") + "The ratio sets the operating point more than any model change.",
                    "source": "journal: cost-ratio sensitivity" if cr else "Model Doctor: cost trade-off"})
    mi = doctor.get("missing_info") or {}
    kinds_done = set()
    for g in mi.get("gaps") or []:
        kd = g["kind"]
        gn = g.get("gain") or {}
        gtxt = (f" Worth {'up to ' if gn.get('simulated') else ''}{gn['delta']:+.4f} {gn.get('metric') or ''}"
                f"{' (simulation)' if gn.get('simulated') else ''}." if gn.get("delta") is not None else "")
        if kd == "twins" and "twins" not in kinds_done:
            nxt.append({"kind": "join", "title": "Join a source that separates the look-alikes", "why": g["finding"][:400] + gtxt,
                        "source": "What's missing: twins", "request": g["request"]})
        elif kd == "thin" and "thin" not in kinds_done:
            nxt.append({"kind": "collect", "title": g["title"].replace("Thin feature: ", "Collect a finer or historical ") ,
                        "why": g["finding"][:300] + gtxt, "source": "What's missing: thin feature", "request": g["request"]})
        elif kd == "label_overlap":
            nxt.append({"kind": "redefine_label", "title": g["title"].replace("Labels overlap", "Redefine or merge"),
                        "why": g["finding"][:300] + gtxt, "source": "What's missing: label overlap", "request": g["request"]})
        elif kd == "concentration" and "concentration" not in kinds_done:
            nxt.append({"kind": "collect", "title": g["title"].replace("Errors concentrate: ", "Collect what separates "),
                        "why": g["finding"][:300] + gtxt, "source": "What's missing: concentration", "request": g["request"]})
        elif kd == "more_rows" and gn.get("ci") and gn["ci"][0] > 0:
            nxt.append({"kind": "collect", "title": "Collect more labelled rows", "why": g["finding"][:300],
                        "source": "What's missing: more rows", "request": g["request"]})
        elif kd == "missingness":
            nxt.append({"kind": "fix_capture", "title": g["title"].replace("Missing where it matters: ", "Fix how it is captured: "),
                        "why": g["finding"][:300], "source": "What's missing: missingness", "request": g["request"]})
        kinds_done.add(kd)
    kept = [e for e in j if e.get("verdict") == "kept"]
    honest = [e for e in j if e.get("verdict") == "kept-honest"]
    start = rv["node"]["start"] if rv.get("node") else None
    best = rv["node"]["best"] if rv.get("node") else None
    if kept:
        head = "; ".join(f"{e.get('title') or e['hypothesis']['title']} ({_fmt(e['result'])})" for e in kept)
        s = f"{len(j)} experiments; {len(kept)} improved the model beyond noise: {head}."
    elif j:
        s = (f"{len(j)} experiments; none improved the model beyond noise. Tuning or arithmetic on these columns will not help: "
             "the gain must come from new information (the data requests below).")
    else:
        s = "No experiment ran."
    if honest:
        e = honest[0]
        s = (f"The leak is confirmed: without {', '.join(e['experiment']['spec'].get('columns') or [])} the score is "
             f"{e['result']['child']:.4f} instead of {e['result']['parent']:.4f} ({_fmt(e['result'])}); that is the honest model. ") + s
    return {"schema": SCHEMA, "summary": s, "summary_source": "computed", "tried": tried, "helped": helped, "next": nxt,
            "start_run": start, "best_run": best, "stop_reason": rv.get("stop_reason"), "rules": RULES}


def merge_llm_plan(plan: dict[str, Any], obj: dict[str, Any] | None, facts: dict[str, Any], rv: dict[str, Any]) -> dict[str, Any]:
    guard: dict[str, Any] = {"checked": 0, "blocked": []}
    if obj:
        s = obj.get("summary")
        if isinstance(s, str) and s.strip():
            guard["checked"] += 1
            bad = llm.unsupported_numbers(s, facts)
            if bad:
                guard["blocked"].append({"field": "summary", "numbers": bad, "text": s[:400]})
            else:
                plan["computed_summary"] = plan["summary"]
                plan["summary"] = s.strip()
                plan["summary_source"] = "llm"
        for it in obj.get("next") or []:
            if not isinstance(it, dict) or not isinstance(it.get("title"), str) or not isinstance(it.get("why"), str):
                continue
            guard["checked"] += 1
            bad = llm.unsupported_numbers(it["title"] + " " + it["why"], facts)
            if bad:
                guard["blocked"].append({"field": "next", "numbers": bad, "text": (it["title"] + ": " + it["why"])[:300]})
                continue
            if any(it["title"].strip().lower() == x["title"].strip().lower() for x in plan["next"]):
                continue
            plan["next"].append({"kind": str(it.get("kind") or "other")[:20], "title": it["title"][:90], "why": it["why"][:300],
                                 "source": "language model (numbers checked against the journal)", "llm": True})
    rv["guard"] = guard
    return plan


# --------------------------------------------------------------------------------------------------------- start


def start(app, rec: dict[str, Any], by: str, options: dict[str, Any]) -> dict[str, Any]:
    mode = "offline" if options.get("offline") or not llm.route() else "llm"
    rv = {"schema": SCHEMA, "id": "rv_" + secrets.token_hex(4), "run_id": rec["id"], "status": "queued", "by": by,
          "started_at": _now(), "finished_at": None, "mode": mode,
          "mode_note": None if mode == "llm" else ("offline requested" if options.get("offline") else
                                                   "no language model available: the deterministic Doctor proposes"),
          "options": {"max_experiments": int(options.get("max_experiments") or MAX_EXPERIMENTS),
                      "max_minutes": float(options.get("max_minutes") or MAX_MINUTES)},
          "llm_route": llm.route(), "model": llm.settings()["model"] if mode == "llm" else None, "decider": None,
          "roles": [], "hypotheses": [], "journal": [], "node": None, "plan": None, "domain_gaps": [], "rules": RULES,
          "progress": "queued"}
    save(rec, rv)
    task = asyncio.get_running_loop().create_task(Review(app, rec, rv).run())
    _tasks[rv["id"]] = task
    task.add_done_callback(lambda _t, k=rv["id"]: _tasks.pop(k, None))
    return rv
