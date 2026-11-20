"""Step 7: predictor retention. present → slimmed → evicted.

slimmed: TabularPredictor.delete_models(models_to_keep='best') + save_space() (tabular runs only).
evicted: the predictor folder is deleted. Kept: config, result JSON, holdout_predictions.csv, leaderboard, the review
journal and the hashes; a tombstone EVICTED.json records when, by whom and the bytes freed. Predict, explain, SHAP and
what-if on an evicted run return 410 "re-fit from config"; paired compare, the ladder's stored results and journals still
work.

Pinned (never slimmed or evicted): source runs of non-rejected versions; the champion, Production and Staging;
runs under a pending or running review (the base run and its branches); the top 3 per project and dataset; runs tagged
`keep`; queued and running runs."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from .schema import EVICTED, PRESENT, SLIMMED, now

TOMBSTONE = "EVICTED.json"


class EvictedError(Exception):
    def __init__(self, run_id: str, info: dict[str, Any] | None = None):
        self.run_id, self.info = run_id, info or {}
        super().__init__(f"Run {run_id}'s predictor was evicted by retention")


def gone_message(run_id: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = info or {}
    return {"detail": f"Run {run_id}'s predictor was evicted by retention"
                      f"{' on ' + info['at'][:10] if info.get('at') else ''}: re-fit from config "
                      f"(GET /api/runs/{run_id}/gym gives the body; POST /api/runs trains it again). Its scores, holdout "
                      "predictions, leaderboard and journal are kept, so compare and the review still work.",
            "run_id": run_id, "predictor_state": EVICTED, "evicted": info, "refit": f"/api/runs/{run_id}/gym"}


def tombstone(run: dict[str, Any]) -> dict[str, Any] | None:
    p = Path(run["config"].get("run_dir") or "") / TOMBSTONE
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return {}


def dir_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def pins(app) -> dict[str, list[str]]:
    """run_id → why it is pinned."""
    db, cat = app.state.db, app.state.catalog
    pol = config.RETENTION_POLICY
    why: dict[str, list[str]] = {}

    def pin(rid: str | None, reason: str) -> None:
        if rid:
            why.setdefault(rid, [])
            if reason not in why[rid]:
                why[rid].append(reason)

    champs = cat.champion_version_ids()
    with db.connect() as con:
        for v in con.execute("SELECT id, run_id, stage, approval, model_name, version FROM model_versions").fetchall():
            label = f"{v['model_name']} v{v['version']}"
            if v["approval"] != "rejected":
                pin(v["run_id"], f"source of {label} ({v['approval']})")
            if v["stage"] in ("Production", "Staging"):
                pin(v["run_id"], f"{label} is in {v['stage']}")
            if v["id"] in champs:
                pin(v["run_id"], f"{label} is the champion")
        for r in con.execute("SELECT id FROM runs WHERE status IN ('queued', 'running')"):
            pin(r[0], "queued or running")
        # top k per (project, dataset) by the primary score, in the metric's direction
        rows = con.execute("SELECT id, project_id, dataset_id, primary_metric, primary_score, greater_is_better FROM runs"
                           " WHERE status = 'succeeded' AND primary_score IS NOT NULL").fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["project_id"], r["dataset_id"], r["primary_metric"]), []).append(r)
    k = int(pol["top_k_pinned"])
    for (_, ds, m), rs in groups.items():
        rs.sort(key=lambda r: r["primary_score"] if r["greater_is_better"] in (1, None) else -r["primary_score"], reverse=True)
        for i, r in enumerate(rs[:k]):
            pin(r["id"], f"top {k} on {ds} by {m} (#{i + 1})")
    for rid in cat.tagged("run", pol["keep_tag"]):
        pin(rid, f"tagged '{pol['keep_tag']}'")
    # reviews that are pending or running: the base run and every branch under it
    for p in app.state.settings.runs_dir.glob("run_*/reviews/rv_*.json"):
        try:
            rv = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if rv.get("status") in ("queued", "running"):
            from ..vela import review as R

            if p.stem not in R._tasks:  # the API restarted while it ran: it is interrupted, not running
                continue
            base = p.parent.parent.name
            pin(base, f"review {p.stem} is {rv['status']}")
            for j in rv.get("journal") or []:
                pin(j.get("run_id"), f"branch of review {p.stem} ({rv['status']})")
    return why


def plan(app, run_ids: list[str] | None = None, tier: str | None = None) -> dict[str, Any]:
    """The dry run: every run with a predictor on disk, its bytes, its age, its pins and what the policy would do."""
    pol = config.RETENTION_POLICY
    pinned = pins(app)
    db = app.state.db
    with db.connect() as con:
        q = ("SELECT id, name, status, finished_at, created_at, kind, project_id, dataset_id, predictor_state, config FROM runs"
             " WHERE COALESCE(predictor_state, 'present') != 'evicted'")
        rows = con.execute(q).fetchall()
    items, free = [], {"slim": 0, "evict": 0}
    for r in rows:
        if run_ids is not None and r["id"] not in run_ids:
            continue
        cfg = json.loads(r["config"])
        pp = cfg.get("predictor_path")
        if not pp or not Path(pp).exists():
            continue
        size = dir_bytes(Path(pp))
        age = _age_days(r["finished_at"] or r["created_at"])
        state = r["predictor_state"] or PRESENT
        tabular = (r["kind"] or "tabular") in ("tabular", "import") and (Path(pp) / "predictor.pkl").exists()
        action, why_action = None, ""
        if tier:  # an admin's explicit request on chosen runs (still refused on pinned ones)
            action = tier if not (tier == "slim" and (state == SLIMMED or not tabular)) else None
            why_action = f"requested: {tier}" if action else ("already slimmed" if state == SLIMMED else "slimming is for tabular predictors")
        elif r["status"] in ("failed", "cancelled") and age is not None and age >= pol["failed_evict_after_days"]:
            action, why_action = "evict", f"{r['status']} {age:.0f} d ago (≥ {pol['failed_evict_after_days']} d)"
        elif age is not None and age >= pol["evict_after_days"]:
            action, why_action = "evict", f"finished {age:.0f} d ago (≥ {pol['evict_after_days']} d)"
        elif age is not None and age >= pol["slim_after_days"] and state == PRESENT and tabular:
            action, why_action = "slim", f"finished {age:.0f} d ago (≥ {pol['slim_after_days']} d)"
        pin_why = pinned.get(r["id"]) or []
        est = size if action == "evict" else (int(size * 0.6) if action == "slim" else 0)  # slim: an estimate until measured
        item = {"run_id": r["id"], "name": r["name"], "status": r["status"], "kind": r["kind"], "project_id": r["project_id"],
                "dataset_id": r["dataset_id"], "state": state, "bytes": size, "age_days": None if age is None else round(age, 2),
                "pinned": pin_why, "action": None if pin_why else action, "reason": why_action,
                "would_free_bytes": 0 if pin_why or not action else est}
        if action and not pin_why:
            free[action] += item["would_free_bytes"]
        items.append(item)
        with db.connect() as con:
            con.execute("UPDATE runs SET predictor_bytes = ? WHERE id = ?", (size, r["id"]))
    items.sort(key=lambda x: (x["action"] is None, -x["bytes"]))
    return {"policy": pol, "dry_run": True, "runs": items, "would_free_bytes": free["slim"] + free["evict"],
            "would_free": free, "total_bytes": sum(i["bytes"] for i in items),
            "counts": {"runs": len(items), "slim": sum(1 for i in items if i["action"] == "slim"),
                       "evict": sum(1 for i in items if i["action"] == "evict"), "pinned": sum(1 for i in items if i["pinned"])},
            "note": "slim estimates 60 % freed until a run is actually slimmed; evict frees the whole predictor folder"}


def _slim(path: str) -> None:
    from autogluon.tabular import TabularPredictor

    p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False, require_version_match=False)
    p.delete_models(models_to_keep="best", dry_run=False)
    p.save_space()


def execute(app, by: str, run_ids: list[str] | None = None, tier: str | None = None) -> dict[str, Any]:
    """Apply the plan (or `tier` to `run_ids`): pinned runs are never touched. Each action is audited."""
    pl = plan(app, run_ids, tier)
    reg, done = app.state.registry, []
    for it in pl["runs"]:
        if not it["action"]:
            continue
        run = app.state.runs.get(it["run_id"])
        pp = Path(run["config"]["predictor_path"])
        before = dir_bytes(pp)
        try:
            if it["action"] == "slim":
                _slim(str(pp))
                state = SLIMMED
            else:
                shutil.rmtree(pp, ignore_errors=False) if pp.is_dir() else pp.unlink()
                state = EVICTED
                info = {"at": now(), "by": by, "bytes_freed": before, "kept": ["config.json", "result (database)",
                        "holdout_predictions.csv", "leaderboard (result)", "reviews/ (journal)", "hashes"],
                        "predictor_path": str(pp)}
                (Path(run["config"]["run_dir"]) / TOMBSTONE).write_text(json.dumps(info, indent=1))
        except Exception as e:  # noqa: BLE001
            done.append({**it, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
            continue
        after = dir_bytes(pp)
        with app.state.db.connect() as con:
            con.execute("UPDATE runs SET predictor_state = ?, predictor_bytes = ?, state_changed_at = ? WHERE id = ?",
                        (state, after, now(), it["run_id"]))
        try:
            app.state.predictors.drop(str(pp))
        except Exception:  # noqa: BLE001
            pass
        reg.audit(actor=by, action=f"retention_{it['action']}", run_id=it["run_id"], from_stage=it["state"], to_stage=state,
                  comment=it["reason"], details={"bytes_before": before, "bytes_after": after, "freed": before - after,
                                                 "policy": config.RETENTION_POLICY["status"]})
        done.append({**it, "ok": True, "state": state, "bytes_before": before, "bytes_after": after, "freed": before - after})
    return {"by": by, "dry_run": False, "actions": done, "freed_bytes": sum(d.get("freed", 0) for d in done if d.get("ok")),
            "skipped_pinned": [{"run_id": i["run_id"], "pinned": i["pinned"]} for i in pl["runs"] if i["pinned"]
                               and (run_ids is not None)]}


# --------------------------------------------------------------------------------------------- the 410 guard
# Audit of predictor_path readers (docs/progress/P1.md): every route under /api/<kind>/runs/{run_id}/… loads the run's
# predictor except the ones below, which read only stored artefacts. The guard is an allow-list, so a new route is
# guarded by default.
STORED_ONLY = {
    ("GET", ""), ("DELETE", ""), ("GET", "/events"), ("GET", "/experiments"), ("GET", "/review"), ("GET", "/holdout-predictions"),
    ("GET", "/oof"), ("GET", "/export.py"), ("GET", "/split-comparison"), ("GET", "/cost-comparison"), ("GET", "/vela"),
    ("GET", "/gym"), ("GET", "/scores"), ("GET", "/scores.csv"), ("GET", "/top"), ("GET", "/forecast.csv"), ("GET", "/chart"),
    ("POST", "/cancel"), ("GET", "/doctor"), ("GET", "/active-learning"), ("GET", "/annotations"), ("GET", "/learning-curve"),
    ("GET", "/shift"), ("GET", "/slices"), ("GET", "/supervised"), ("GET", "/labels"),
}
STORED_PREFIX = (("GET", "/review/"), ("GET", "/predictions/"), ("GET", "/batches/"))
