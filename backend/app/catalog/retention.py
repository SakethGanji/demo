"""Retention. P1 step 7 built predictor slim / evict; P24 makes retention per team and manual (§6.4, §11 item 3).

**§11 item 3: retention is manual.** There is no timer and no default period. A team's periods are the ones its owner
wrote in the team policy (`PATCH /api/teams/{team} {policy: {retention: …}}`, §6.4 keys), or the ones a person sends
with the request (`periods`). A category without a period has no candidates ("no period set"). A person asks for the
plan (`GET /api/retention/plan?team=`), a stored dry run (`POST /api/retention/dry-run`, audited, gives a `plan_id`)
and a run (`POST /api/retention/run {team, plan_id, confirm: "retention", items?}`): the run acts only on items that
dry run listed **and** that are still candidates now. Every action is audited (`retention_*`, and `retention_run`).

Categories (§6.4):
- `versions {keep_days, keep_min, keep_certified}`: versions older than keep_days, beyond the newest keep_min of their
  dataset (1 when unset), not the default, not live-certified (unless keep_certified is false), not in use (a model,
  champion, running or succeeded run: dsets.blockers / run users) → a **delete request** (four eyes, as today: a second
  person decides it on `POST /api/dataset-requests/{id}/decide`);
- `sources {keep_days}`: kept upload source files → the upload's ref is dropped (GC then deletes the bytes no one keeps);
- `join_caches {evict_unused_days}`: a join frame's cache that no run pins (a frame without runs is re-creatable) →
  the frame's ref is dropped;
- `runs {slim_days, evict_days}`: predictor slim (present → slimmed) / evict (the folder goes; tombstone kept);
- `staging {days}`: uploads never committed → expired, their scratch removed.

Pinned runs (never slimmed or evicted): source runs of non-rejected versions; the champion, Production and Staging;
runs under a pending or running review; the top 3 per project, dataset, label and problem type; runs tagged `keep`
and the source runs of versions tagged `keep`; queued and running runs.

**Legal holds** (catalog/holds.py) cover every category: a held item is listed under `held`, never acted on.

slimmed: TabularPredictor.delete_models(models_to_keep='best') + save_space() (tabular runs only).
evicted: the predictor folder is deleted. Kept: config, result JSON, holdout_predictions.csv, leaderboard, the review
journal and the hashes; a tombstone EVICTED.json records when, by whom and the bytes freed. Predict, explain, SHAP and
what-if on an evicted run return 410 "re-fit from config"."""

from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from .schema import DEFAULT_TEAM, EVICTED, PRESENT, SLIMMED, audit, now


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
        # F-P2 #4: top k per (project, dataset, label, problem type), ranked by the metric compare uses: AUC (or average
        # precision) for binary, else the run's own eval metric. json_extract keeps the result JSON out of Python.
        rows = con.execute(
            "SELECT id, project_id, dataset_id, label, primary_metric, primary_score, greater_is_better,"
            " json_extract(result, '$.problem_type') AS pt, CAST(json_extract(result, '$.holdout.metrics.roc_auc') AS REAL) AS auc,"
            " CAST(json_extract(result, '$.holdout.metrics.average_precision') AS REAL) AS ap FROM runs"
            " WHERE status = 'succeeded' AND (primary_score IS NOT NULL OR json_extract(result, '$.holdout.metrics.roc_auc') IS NOT NULL)").fetchall()
        keep_versions = [r[0] for r in con.execute(
            "SELECT v.run_id FROM annotations a JOIN model_versions v ON v.id = a.target_id WHERE a.target_kind = 'version'"
            " AND a.kind = 'tag' AND lower(a.value) = lower(?) AND a.deleted_at IS NULL", (pol["keep_tag"],))]
    groups: dict[tuple, list] = {}
    for r in rows:
        if r["pt"] == "binary" and (r["auc"] is not None or r["ap"] is not None):
            m, score, up = ("roc_auc", r["auc"], True) if r["auc"] is not None else ("average_precision", r["ap"], True)
        else:
            m, score, up = r["primary_metric"], r["primary_score"], r["greater_is_better"] in (1, None)
        if score is None:
            continue
        groups.setdefault((r["project_id"], r["dataset_id"], r["label"], r["pt"], m), []).append((score if up else -score, r["id"]))
    k = int(pol["top_k_pinned"])
    for (_, ds, label, pt, m), rs in groups.items():
        rs.sort(reverse=True)
        for i, (_, rid) in enumerate(rs[:k]):
            pin(rid, f"top {k} on {ds} · {label} ({pt or 'unknown'}) by {m} (#{i + 1})")
    for rid in cat.tagged("run", pol["keep_tag"]):  # case-insensitive: "Keep" and "KEEP" pin too
        pin(rid, f"tagged '{pol['keep_tag']}'")
    for rid in keep_versions:
        pin(rid, f"source of a version tagged '{pol['keep_tag']}'")
    # reviews that are pending or running: the base run and every branch under it
    for p in app.state.settings.runs_dir.glob("run_*/reviews/rv_*.json"):
        try:
            rv = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if rv.get("status") in ("queued", "running"):
            from ..vela import review as R

            tasks = getattr(R, "_tasks", None)  # F-X5b: gone since P22 (reviews run on the job queue) → was a 500
            if tasks is not None and p.stem not in tasks:  # the API restarted while it ran: interrupted, not running
                continue
            base = p.parent.parent.name
            pin(base, f"review {p.stem} is {rv['status']}")
            for j in rv.get("journal") or []:
                pin(j.get("run_id"), f"branch of review {p.stem} ({rv['status']})")
    return why


# --------------------------------------------------------------------------------------------- P24: per-team plan

CATEGORIES = ("versions", "sources", "join_caches", "runs", "staging")
#: the period keys per category (days). None of them has a default (§11 item 3).
PERIOD_KEYS = {"versions": ("keep_days",), "sources": ("keep_days",), "join_caches": ("evict_unused_days",),
               "runs": ("slim_days", "evict_days"), "staging": ("days",)}
PLAN_KEY = "retention:plan:"
PLAN_TTL_HOURS = 24.0


class RetentionError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        self.status, self.payload = status, {"code": code, "message": message, **extra}
        super().__init__(message)


def team_id(con, team: str | None) -> str:
    r = con.execute("SELECT id FROM teams WHERE id = ? OR slug = ?", (team or "", team or "")).fetchone()
    if not r:
        raise RetentionError(404, "team_not_found", f"No team {team!r}.")
    return r[0]


def _num(v: Any, where: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise RetentionError(400, "bad_period", f"{where} is a number of days ≥ 0 (or absent: no period).")
    return float(v)


def periods(con, team: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    """The periods in force: the request's `override` (a person's explicit choice) over the team policy's
    `retention` object. Nothing else: no defaults."""
    r = con.execute("SELECT policy FROM teams WHERE id = ?", (team,)).fetchone()
    try:
        pol = (json.loads(r[0]) if r and r[0] else {}).get("retention") or {}
    except ValueError:
        pol = {}
    if override is not None and not isinstance(override, dict):
        raise RetentionError(400, "bad_period", "periods is {category: {key: days}}.")
    out: dict[str, Any] = {}
    src: dict[str, str] = {}
    for cat, keys in PERIOD_KEYS.items():
        base = pol.get(cat) if isinstance(pol.get(cat), dict) else {}
        ov = (override or {}).get(cat) if isinstance((override or {}).get(cat), dict) else {}
        c: dict[str, Any] = {}
        for k in keys:
            v = ov.get(k, base.get(k))
            c[k] = _num(v, f"{cat}.{k}")
            if c[k] is not None:
                src[f"{cat}.{k}"] = "request" if k in ov else "team policy"
        if cat == "versions":
            km = ov.get("keep_min", base.get("keep_min"))
            c["keep_min"] = int(_num(km, "versions.keep_min") or 0) if km is not None else 1  # a floor, not a period
            kc = ov.get("keep_certified", base.get("keep_certified"))
            c["keep_certified"] = True if kc is None else bool(kc)
        out[cat] = c
    return {**out, "_source": src}


def _age(iso: str | None) -> float | None:
    return _age_days(iso)


def _team_versions(con, team: str) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        "SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.project_id, d.version_no, d.created_at, d.name, d.state FROM datasets d"
        " LEFT JOIN projects p ON p.id = d.project_id WHERE COALESCE(d.team_id, p.team_id, ?) = ? AND COALESCE(d.state, '') <> 'deleted'"
        " ORDER BY COALESCE(d.root_id, d.id), COALESCE(d.version_no, 0) DESC, d.created_at DESC", (DEFAULT_TEAM, team))]


def _versions(app, con, team: str, p: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from . import dsets, holds, shares

    out: dict[str, list[dict[str, Any]]] = {"candidates": [], "kept": [], "held": []}
    rows = _team_versions(con, team)
    days = p["keep_days"]
    by_root: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_root.setdefault(r["root"], []).append(r)
    for root, vs in by_root.items():
        allrows = dsets.tree_rows(con, root)
        default = dsets.effective_default(allrows, dsets.card(con, root))[0]
        for i, v in enumerate(vs):
            age = _age(v["created_at"])
            item = {"id": f"version:{v['id']}", "kind": "version", "version_id": v["id"], "root_id": root, "v": v["version_no"],
                    "name": v["name"], "project_id": v["project_id"], "age_days": None if age is None else round(age, 2)}
            hs = holds.held_versions(con, [v["id"]]).get(v["id"])
            if hs:
                out["held"].append({**item, "holds": hs})
                continue
            if days is None:
                continue
            why = None
            if age is None or age < days:
                continue
            if i < p["keep_min"]:
                why = f"one of the newest {p['keep_min']} version(s) of its dataset"
            elif v["id"] == default:
                why = "the dataset's default version"
            elif p["keep_certified"] and shares.live_certification(con, v["id"]):
                why = "live certification (keep_certified)"
            elif con.execute("SELECT 1 FROM dataset_requests WHERE dataset_id = ? AND status = 'pending'", (v["id"],)).fetchone():
                why = "a delete request is already pending"
            else:
                bl = dsets.blockers(con, [v["id"]]) + [f"run {u['id']} (succeeded) trained on it" for u in dsets._run_users(con, [v["id"]])]
                if bl:
                    why = "in use: " + "; ".join(bl[:3])
            if why:
                out["kept"].append({**item, "why": why})
            else:
                out["candidates"].append({**item, "action": "request_delete",
                                          "reason": f"created {age:.0f} d ago (≥ versions.keep_days {days:g})"})
    return out


def _sources(con, team: str, p: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from . import holds

    out: dict[str, list[dict[str, Any]]] = {"candidates": [], "kept": [], "held": []}
    rows = [dict(r) for r in con.execute(
        "SELECT x.blob_sha256, x.owner_id, x.created_at, b.bytes, b.key FROM blob_refs x JOIN blobs b ON b.sha256 = x.blob_sha256"
        " WHERE x.owner_kind = 'upload' AND x.role = 'source' AND x.team_id = ? ORDER BY x.created_at", (team,))]
    for r in rows:
        age = _age(r["created_at"])
        item = {"id": f"source:{r['owner_id']}:{r['blob_sha256']}", "kind": "source", "upload_id": r["owner_id"],
                "blob_sha256": r["blob_sha256"], "bytes": r["bytes"], "age_days": None if age is None else round(age, 2)}
        hs = holds.active(con, holds.upload_keys(con, r["owner_id"]))
        if hs:
            out["held"].append({**item, "holds": [h["id"] for h in hs]})
        elif p["keep_days"] is not None and age is not None and age >= p["keep_days"]:
            out["candidates"].append({**item, "action": "drop_source", "reason": f"kept {age:.0f} d (≥ sources.keep_days {p['keep_days']:g})"})
    return out


def _join_caches(con, team: str, p: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from . import holds

    out: dict[str, list[dict[str, Any]]] = {"candidates": [], "kept": [], "held": []}
    rows = [dict(r) for r in con.execute(
        "SELECT f.id, f.join_id, f.version_id, f.cache_blob, f.created_at, b.bytes FROM join_frames f JOIN joins j ON j.id = f.join_id"
        " JOIN blob_refs x ON x.blob_sha256 = f.cache_blob AND x.owner_kind = 'join_frame' AND x.owner_id = f.id"
        " JOIN blobs b ON b.sha256 = f.cache_blob WHERE j.team_id = ? ORDER BY f.created_at", (team,))]
    for r in rows:
        age = _age(r["created_at"])
        item = {"id": f"join_cache:{r['id']}", "kind": "join_cache", "join_frame_id": r["id"], "join_id": r["join_id"],
                "version_id": r["version_id"], "blob_sha256": r["cache_blob"], "bytes": r["bytes"],
                "age_days": None if age is None else round(age, 2)}
        hs = holds.active(con, holds.join_frame_keys(con, r["id"]))
        if hs:
            out["held"].append({**item, "holds": [h["id"] for h in hs]})
            continue
        if p["evict_unused_days"] is None or age is None or age < p["evict_unused_days"]:
            continue
        used = [x[0] for x in con.execute("SELECT owner_id FROM blob_refs WHERE blob_sha256 = ? AND owner_kind = 'run'", (r["cache_blob"],))]
        if used:
            out["kept"].append({**item, "why": f"pinned by run(s) {', '.join(used[:5])}"})
        else:
            out["candidates"].append({**item, "action": "evict_cache", "reason": f"no run uses it; {age:.0f} d old (≥ join_caches."
                                                                                 f"evict_unused_days {p['evict_unused_days']:g}); re-creatable"})
    return out


def _staging(con, team: str, p: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    from . import holds

    out: dict[str, list[dict[str, Any]]] = {"candidates": [], "kept": [], "held": []}
    if p["days"] is None:
        return out
    for r in con.execute("SELECT id, status, created_at, created_by FROM uploads WHERE team_id = ? AND status IN ('staged', 'checked')"
                         " ORDER BY created_at", (team,)):
        age = _age(r["created_at"])
        item = {"id": f"staging:{r['id']}", "kind": "staging", "upload_id": r["id"], "status": r["status"],
                "created_by": r["created_by"], "age_days": None if age is None else round(age, 2)}
        if age is None or age < p["days"]:
            continue
        hs = holds.active(con, holds.upload_keys(con, r["id"]))
        if hs:
            out["held"].append({**item, "holds": [h["id"] for h in hs]})
        else:
            out["candidates"].append({**item, "action": "expire_upload", "reason": f"staged {age:.0f} d ago (≥ staging.days {p['days']:g})"})
    return out


def _runs(app, con, team: str, p: dict[str, Any], pinned: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    from . import holds

    out: dict[str, list[dict[str, Any]]] = {"candidates": [], "kept": [], "held": []}
    rows = [dict(r) for r in con.execute(
        "SELECT r.id, r.name, r.status, r.finished_at, r.created_at, r.kind, r.project_id, r.dataset_id, r.predictor_state, r.config"
        " FROM runs r LEFT JOIN projects pr ON pr.id = r.project_id LEFT JOIN datasets d ON d.id = r.dataset_id"
        " WHERE COALESCE(r.team_id, pr.team_id, d.team_id, ?) = ? AND COALESCE(r.predictor_state, 'present') <> 'evicted'"
        " ORDER BY r.created_at", (DEFAULT_TEAM, team))]
    for r in rows:
        cfg = json.loads(r["config"] or "{}")
        pp = cfg.get("predictor_path")
        if not pp or not Path(pp).exists():
            continue
        age = _age(r["finished_at"] or r["created_at"])
        state = r["predictor_state"] or PRESENT
        tabular = (r["kind"] or "tabular") in ("tabular", "import") and (Path(pp) / "predictor.pkl").exists()
        size = dir_bytes(Path(pp))
        item = {"run_id": r["id"], "name": r["name"], "status": r["status"], "project_id": r["project_id"], "dataset_id": r["dataset_id"],
                "state": state, "bytes": size, "age_days": None if age is None else round(age, 2)}
        hs = holds.active(con, holds.run_keys(con, r["id"]))
        if hs:
            out["held"].append({**item, "id": f"run:{r['id']}", "holds": [h["id"] for h in hs]})
            continue
        action = why = None
        if p["evict_days"] is not None and age is not None and age >= p["evict_days"]:
            action, why = "evict", f"finished {age:.0f} d ago (≥ runs.evict_days {p['evict_days']:g})"
        elif p["slim_days"] is not None and age is not None and age >= p["slim_days"] and state == PRESENT and tabular:
            action, why = "slim", f"finished {age:.0f} d ago (≥ runs.slim_days {p['slim_days']:g})"
        if not action:
            continue
        if pinned.get(r["id"]):
            out["kept"].append({**item, "id": f"run:{r['id']}:{action}", "why": "pinned: " + "; ".join(pinned[r["id"]])})
            continue
        out["candidates"].append({**item, "id": f"run:{r['id']}:{action}", "kind": "run", "action": action, "reason": why,
                                  "would_free_bytes": size if action == "evict" else int(size * 0.6)})
    return out


def team_plan(app, team: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    """The plan for one team (read-only): per category, the candidates, what is kept and why, and what a legal hold
    covers. Nothing is changed."""
    with app.state.db.connect() as con:
        team = team_id(con, team)
        p = periods(con, team, override)
    pinned = pins(app)
    with app.state.db.connect() as con:
        cats = {"versions": _versions(app, con, team, p["versions"]), "sources": _sources(con, team, p["sources"]),
                "join_caches": _join_caches(con, team, p["join_caches"]), "runs": _runs(app, con, team, p["runs"], pinned),
                "staging": _staging(con, team, p["staging"])}
    no_period = [c for c in CATEGORIES if all(p[c][k] is None for k in PERIOD_KEYS[c])]
    counts = {c: {k: len(v[k]) for k in ("candidates", "kept", "held")} for c, v in cats.items()}
    return {"team": team, "dry_run": True, "at": now(), "periods": {c: p[c] for c in CATEGORIES}, "periods_from": p["_source"],
            "no_period_set": no_period, "categories": cats, "counts": counts,
            "n_candidates": sum(v["candidates"] for v in counts.values()), "n_held": sum(v["held"] for v in counts.values()),
            "note": "Retention is manual (§11 item 3): no timer, no default period. A category without a period has no "
                    "candidates. POST /api/retention/dry-run stores this plan; POST /api/retention/run acts on its items."}


def plan(app, run_ids: list[str] | None = None, tier: str | None = None) -> dict[str, Any]:
    """Every team's plan (the admin view), or with `tier` the explicit slim / evict of chosen runs (an admin's request:
    pinned and held runs are still refused)."""
    if tier:
        return _explicit(app, run_ids or [], tier)
    with app.state.db.connect() as con:
        teams = [r[0] for r in con.execute("SELECT id FROM teams ORDER BY id")]
    plans = [team_plan(app, t) for t in teams]
    return {"dry_run": True, "teams": {p["team"]: {k: p[k] for k in ("periods", "no_period_set", "counts", "n_candidates", "n_held")}
                                       for p in plans},
            "n_candidates": sum(p["n_candidates"] for p in plans), "n_held": sum(p["n_held"] for p in plans),
            "note": "GET /api/retention/plan?team=<id> for one team's items. Retention is manual: no timer, no default period."}


def _explicit(app, run_ids: list[str], tier: str) -> dict[str, Any]:
    from . import holds

    pinned = pins(app)
    items = []
    with app.state.db.connect() as con:
        for rid in run_ids:
            r = con.execute("SELECT id, name, status, kind, predictor_state, config FROM runs WHERE id = ?", (rid,)).fetchone()
            if not r:
                continue
            cfg = json.loads(r["config"] or "{}")
            pp = cfg.get("predictor_path")
            if not pp or not Path(pp).exists():
                continue
            state = r["predictor_state"] or PRESENT
            tabular = (r["kind"] or "tabular") in ("tabular", "import") and (Path(pp) / "predictor.pkl").exists()
            action = tier if not (tier == "slim" and (state == SLIMMED or not tabular)) else None
            hs = holds.active(con, holds.run_keys(con, rid))
            items.append({"run_id": rid, "name": r["name"], "status": r["status"], "state": state, "bytes": dir_bytes(Path(pp)),
                          "pinned": pinned.get(rid) or [], "holds": [h["id"] for h in hs],
                          "action": None if (pinned.get(rid) or hs) else action,
                          "reason": f"requested: {tier}" if action else ("already slimmed" if state == SLIMMED else "slimming is for tabular predictors")})
    return {"dry_run": True, "runs": items, "counts": {"runs": len(items), "act": sum(1 for i in items if i["action"])}}


# --------------------------------------------------------------------------------------------- dry run and run


def dry_run(app, team: str, by: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    """The team plan, stored under an id (what a run may act on) and audited (`retention_dry_run`)."""
    out = team_plan(app, team, override)
    pid = "rtp_" + secrets.token_hex(5)
    rec = {"id": pid, "team": out["team"], "by": by, "at": out["at"], "override": override or None,
           "items": [c["id"] for v in out["categories"].values() for c in v["candidates"]]}
    with app.state.db.connect() as con:
        con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (PLAN_KEY + pid, json.dumps(rec)))
        audit(con, by, "retention_dry_run", {"plan_id": pid, "team_id": out["team"], "periods": out["periods"],
                                             "periods_from": out["periods_from"], "counts": out["counts"]},
              team_id=out["team"], object_kind="team", object_id=out["team"])
    return {**out, "plan_id": pid, "by": by}


def _load_plan(con, pid: str) -> dict[str, Any]:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (PLAN_KEY + str(pid or ""),)).fetchone()
    if not r:
        raise RetentionError(404, "plan_not_found", f"No retention plan {pid!r}: POST /api/retention/dry-run first and send its plan_id.")
    rec = json.loads(r[0])
    if rec.get("used_at"):
        raise RetentionError(409, "plan_used", f"Retention plan {pid} was run on {rec['used_at']} by {rec.get('used_by')}; make a new dry run.")
    t = datetime.fromisoformat(rec["at"])
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - t).total_seconds() > PLAN_TTL_HOURS * 3600:
        raise RetentionError(409, "plan_stale", f"Retention plan {pid} is older than {PLAN_TTL_HOURS:g} h; make a new dry run.")
    return rec


def run_plan(app, plan_id: str, by: str, items: list[str] | None = None) -> dict[str, Any]:
    """Act on the items of dry run `plan_id` (or the chosen subset `items`) that are still candidates now."""
    from . import dsets
    from ..lake import upload as U

    db = app.state.db
    with db.connect() as con:
        rec = _load_plan(con, plan_id)
    listed = rec["items"]
    if items is not None:
        unknown = [i for i in items if i not in listed]
        if unknown:
            raise RetentionError(400, "not_in_plan", "These items are not in the plan.", unknown=unknown[:50])
        listed = [i for i in listed if i in set(items)]
    fresh = team_plan(app, rec["team"], rec.get("override"))
    now_items = {c["id"]: c for v in fresh["categories"].values() for c in v["candidates"]}
    done, skipped = [], []
    team = rec["team"]
    for iid in listed:
        c = now_items.get(iid)
        if c is None:
            skipped.append({"id": iid, "why": "no longer a candidate (held, in use, changed or already done)"})
            continue
        try:
            if c["action"] == "request_delete":
                r = dsets.request_delete(app, c["version_id"], by, f"retention ({plan_id}): {c['reason']}",
                                         only_version=c["version_id"] == c["root_id"])
                done.append({**c, "ok": True, "request_id": r.get("id"), "status": r.get("status"),
                             "note": "a delete request: a second person decides it (four eyes)"})
            elif c["action"] in ("drop_source", "evict_cache"):
                owner = ("upload", c["upload_id"], "source") if c["action"] == "drop_source" else ("join_frame", c["join_frame_id"], "cache")
                with db.connect() as con:
                    n = con.execute("DELETE FROM blob_refs WHERE blob_sha256 = ? AND owner_kind = ? AND owner_id = ? AND role = ?",
                                    (c["blob_sha256"], *owner)).rowcount
                    left = con.execute("SELECT COUNT(*) FROM blob_refs WHERE blob_sha256 = ?", (c["blob_sha256"],)).fetchone()[0]
                    audit(con, by, f"retention_{c['action']}", {"plan_id": plan_id, "team_id": team, "item": iid,
                                                                "blob_sha256": c["blob_sha256"], "refs_removed": n, "refs_left": left,
                                                                "reason": c["reason"]},
                          comment=c["reason"], team_id=team, object_kind="blob", object_id=c["blob_sha256"])
                done.append({**c, "ok": True, "refs_left": left, "note": "GC deletes the bytes once no ref is left"})
            elif c["action"] == "expire_upload":
                with db.connect() as con:
                    con.execute("UPDATE uploads SET status = 'expired' WHERE id = ? AND status IN ('staged', 'checked')", (c["upload_id"],))
                    con.execute("DELETE FROM blob_refs WHERE owner_kind = 'upload' AND owner_id = ?", (c["upload_id"],))
                    audit(con, by, "retention_expire_upload", {"plan_id": plan_id, "team_id": team, "upload_id": c["upload_id"],
                                                               "reason": c["reason"]}, comment=c["reason"], team_id=team,
                          object_kind="upload", object_id=c["upload_id"])
                remove = getattr(app.state.lake, "remove_tmp", None)
                if remove:
                    remove(app.state.lake.tmp_dir(c["upload_id"]))
                done.append({**c, "ok": True})
            elif c["action"] in ("slim", "evict"):
                done += _apply_runs(app, by, [{**c, "state": c["state"]}], plan_id)
        except Exception as e:  # noqa: BLE001 - one refusal does not stop the others; it is reported
            payload = getattr(e, "payload", None)
            done.append({**c, "ok": False, "error": payload if payload is not None else f"{type(e).__name__}: {e}"[:300]})
    with db.connect() as con:
        rec.update(used_at=now(), used_by=by)
        con.execute("UPDATE catalog_meta SET value = ? WHERE key = ?", (json.dumps(rec), PLAN_KEY + plan_id))
        summary = {"plan_id": plan_id, "team_id": team, "acted": sum(1 for d in done if d.get("ok")),
                   "failed": sum(1 for d in done if not d.get("ok")), "skipped": len(skipped),
                   "by_action": {a: sum(1 for d in done if d.get("ok") and d.get("action") == a) for a in
                                 ("request_delete", "drop_source", "evict_cache", "slim", "evict", "expire_upload")}}
        audit(con, by, "retention_run", summary, team_id=team, object_kind="team", object_id=team)
    return {"dry_run": False, **summary, "actions": done, "skipped_items": skipped}


def _slim(path: str) -> list[str]:
    """Keep only the best model (and what it depends on); returns the models left."""
    from autogluon.tabular import TabularPredictor

    p = TabularPredictor.load(path, verbosity=0, require_py_version_match=False, require_version_match=False)
    p.delete_models(models_to_keep="best", dry_run=False)
    p.save_space()
    return list(p.model_names())


def mark_deleted_models(app, run_id: str, kept: list[str] | None) -> None:
    """F-P2 #20: after a slim or an evict, the stored leaderboard marks the models that are gone (can_infer false), so
    the pickers stop offering them; the scores stay for the record."""
    run = app.state.runs.get(run_id)
    res = (run or {}).get("result") or {}
    lb = res.get("leaderboard")
    if not isinstance(lb, list):
        return
    keep = set(kept or [])
    new = []
    for row in lb:
        if isinstance(row, dict):
            name = row.get("model")
            gone = name not in keep and (row.get("predict_model") or name) not in keep
            row = {**row, **({"deleted": True, "can_infer": False} if gone else {})}
        new.append(row)
    app.state.runs.merge_result(run_id, leaderboard=new, models_kept=sorted(keep))


def _apply_runs(app, by: str, items: list[dict[str, Any]], plan_id: str | None = None) -> list[dict[str, Any]]:
    """Slim or evict each item's predictor (re-checked against legal holds right before). Each action is audited."""
    from . import holds

    reg, done = app.state.registry, []
    for it in items:
        with app.state.db.connect() as con:
            hs = holds.active(con, holds.run_keys(con, it["run_id"]))
        if hs:
            done.append({**it, "ok": False, "error": holds.refusal(hs, f"{it['action']} run {it['run_id']}")})
            continue
        run = app.state.runs.get(it["run_id"])
        pp = Path(run["config"]["predictor_path"])
        before = dir_bytes(pp)
        kept: list[str] = []
        try:
            if it["action"] == "slim":
                kept = _slim(str(pp))
                state = SLIMMED
            else:
                shutil.rmtree(pp, ignore_errors=False) if pp.is_dir() else pp.unlink()
                state = EVICTED
                info = {"at": now(), "by": by, "bytes_freed": before, "kept": ["config.json", "result (database)",
                        "holdout_predictions.csv", "leaderboard (result)", "reviews/ (journal)", "hashes"],
                        "predictor_path": str(pp), **({"plan_id": plan_id} if plan_id else {})}
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
        try:
            mark_deleted_models(app, it["run_id"], kept if state == SLIMMED else [])
        except Exception:  # noqa: BLE001 — the record of the slim matters more than the picker hint
            pass
        reg.audit(actor=by, action=f"retention_{it['action']}", run_id=it["run_id"], from_stage=it["state"], to_stage=state,
                  comment=it.get("reason") or "", details={"bytes_before": before, "bytes_after": after, "freed": before - after,
                                                           "plan_id": plan_id, "manual": True})
        done.append({**it, "ok": True, "state": state, "bytes_before": before, "bytes_after": after, "freed": before - after})
    return done


def execute(app, by: str, run_ids: list[str] | None = None, tier: str | None = None) -> dict[str, Any]:
    """An admin's explicit slim / evict of chosen runs (`tier` + `run_ids`): pinned and held runs are never touched."""
    if not tier or not run_ids:
        raise RetentionError(400, "explicit_only", "Without a plan, retention acts only on chosen runs with a tier: send "
                                                    "{run_ids, tier}. Otherwise POST /api/retention/dry-run, then run its plan_id.")
    pl = _explicit(app, run_ids, tier)
    done = _apply_runs(app, by, [i for i in pl["runs"] if i["action"]])
    return {"by": by, "dry_run": False, "actions": done, "freed_bytes": sum(d.get("freed", 0) for d in done if d.get("ok")),
            "skipped": [{"run_id": i["run_id"], "pinned": i["pinned"], "holds": i["holds"]} for i in pl["runs"] if not i["action"]]}


# --------------------------------------------------------------------------------------------- the 410 guard
# Audit of predictor_path readers (docs/progress/P1.md): every route under /api/<kind>/runs/{run_id}/… loads the run's
# predictor except the ones below, which read only stored artefacts. The guard is an allow-list, so a new route is
# guarded by default.
STORED_ONLY = {
    ("GET", ""), ("DELETE", ""), ("GET", "/events"), ("GET", "/experiments"), ("GET", "/review"), ("GET", "/holdout-predictions"),
    ("GET", "/children"), ("GET", "/export.py"), ("GET", "/split-comparison"), ("GET", "/cost-comparison"), ("GET", "/vela"),
    ("GET", "/gym"), ("GET", "/scores"), ("GET", "/scores.csv"), ("GET", "/top"), ("GET", "/forecast.csv"), ("GET", "/chart"),
    ("POST", "/cancel"), ("GET", "/doctor"), ("GET", "/active-learning"), ("GET", "/annotations"), ("GET", "/learning-curve"),
    ("GET", "/shift"), ("GET", "/slices"), ("GET", "/supervised"), ("GET", "/labels"),
}
STORED_PREFIX = (("GET", "/review/"), ("GET", "/predictions/"), ("GET", "/batches/"))
#: GET routes that serve a copy stored in the run's result when there is one (and no ?refresh=true): the ladder.
STORED_IF_CACHED = {"/ladder": "ladder"}
