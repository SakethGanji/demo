"""Lake garbage collection (plan §5.10). P02 built the dry run; P24 arms the deletes.

Phase 1 (DB-driven): blobs with no blob_refs, state pending older than 24 h or live older than 7 days → candidates.
Phase 2 (store-driven): objects under blobs/ with no blobs row, and tmp/ writer directories, older than 24 h →
orphans. A blob name that does not match its own bytes' sha256 is an **integrity finding**, never a delete candidate.

P24, §11 item 3 (retention is manual): nothing runs on a timer. A person asks for a dry run
(`POST /api/admin/lake/gc?dry_run=1`): the plan is stored under an id and audited (`gc_dry_run`). A run
(`?dry_run=0 {plan_id, confirm: "gc"}`) deletes only the blobs that plan listed **and** that are still candidates now
(re-computed; a blob that got a ref since is skipped). Each blob: one transaction marks it `purging` (only while it has
no ref and is pending/live), the bytes go through `lake.store.delete_blob(..., bypass_governance=True)` (every S3
version; audited `blob_deleted`), then the row becomes `purged` (kept forever, `purge_reason = 'gc:<plan id>'`). A
blob left `purging` by a crash is finished by the next run. `S3BlobStore.abort_stale_uploads()` runs with each run.

Deletes are refused (409 `gc_disarmed`) until `catalog_meta.gc_armed` is set: the first verify-all pass that also
re-hashed the legacy_json blobs sets it (catalog.dsets.arm_gc).

Legal holds: an unreferenced blob whose former owner (the version whose part, manifest or split it was, the join
frame it cached, the run it sampled) is under an active hold is reported as `held`, never deleted.

Versions deleted before P24 kept their part / source / split refs (P24's delete drops them): the dry run lists them as
`deleted_versions_with_refs` and the run drops those refs (audited `gc_refs_dropped`); their blobs are then candidates in
the next dry run (a run deletes only what its own dry run listed).

Phase 2 orphans (objects with no blobs row) are reported only: nothing deletes bytes outside `delete_blob`, which
needs a row. Stale tmp/ writer directories (local scratch, never blobs) are removed by a run.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import config
from . import manifest as M
from .store import BlobStore, delete_blob, parse_key

PLAN_KEY = "gc:plan:"
PLAN_TTL_HOURS = 24.0


class GcError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        self.status, self.payload = status, {"code": code, "message": message, **extra}
        super().__init__(message)


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _grace() -> tuple[float, float, float]:
    """The safety graces (not retention periods): ENSEMBLE_GC_PENDING_GRACE_HOURS / _LIVE_GRACE_DAYS / _ORPHAN_GRACE_HOURS
    override config for an operator (and for a verification run on a fresh lake)."""
    def f(name: str, d: float) -> float:
        try:
            return float(os.environ.get(name, "") or d)
        except ValueError:
            return d
    return (f("ENSEMBLE_GC_PENDING_GRACE_HOURS", config.GC_PENDING_GRACE_HOURS),
            f("ENSEMBLE_GC_LIVE_GRACE_DAYS", config.GC_LIVE_GRACE_DAYS),
            f("ENSEMBLE_GC_ORPHAN_GRACE_HOURS", config.GC_ORPHAN_GRACE_HOURS))


def armed(con) -> bool:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = 'gc_armed'").fetchone()
    return bool(r and r[0] not in ("", "0", None))


def armed_info(con) -> dict[str, Any]:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = 'gc_armed'").fetchone()
    p = con.execute("SELECT value FROM catalog_meta WHERE key = 'verify_all:passes'").fetchone()
    return {"armed": bool(r and r[0] not in ("", "0", None)), "armed_at": r[0] if r else None,
            "verify_all_passes": json.loads(p[0]) if p and p[0] else [],
            "how": "GET /api/admin/lake/verify-all: its first pass (versions + legacy_json blobs) arms GC"}


def former_owner_keys(con, sha: str) -> set[tuple[str, str]]:
    """The hold scopes of whatever this (now unreferenced) blob belonged to."""
    from ..catalog import holds as H

    keys: set[tuple[str, str]] = set()
    vids = {r[0] for r in con.execute("SELECT version_id FROM table_files WHERE blob_sha256 = ? UNION SELECT id FROM datasets"
                                      " WHERE manifest_sha256 = ? UNION SELECT version_id FROM splits WHERE blob_sha256 = ?",
                                      (sha, sha, sha))}
    for v in vids:
        keys |= H.version_keys(con, v)
    for r in con.execute("SELECT id FROM join_frames WHERE cache_blob = ?", (sha,)):
        keys |= H.join_frame_keys(con, r[0])
    for r in con.execute("SELECT run_id FROM run_inputs WHERE sample_sha256 = ?", (sha,)):
        keys |= H.run_keys(con, r[0])
    return keys


def stale_owner_refs(con) -> list[str]:
    """Deleted versions that still hold data refs (deleted before P24 dropped them at delete time)."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT d.id FROM datasets d WHERE d.state = 'deleted' AND (EXISTS (SELECT 1 FROM blob_refs x WHERE x.owner_kind = 'version'"
        " AND x.owner_id = d.id AND (x.role LIKE 'table:%' OR x.role = 'legacy_csv' OR x.role = 'source')) OR EXISTS (SELECT 1 FROM"
        " blob_refs x JOIN uploads u ON u.id = x.owner_id WHERE x.owner_kind = 'upload' AND u.committed_version_id = d.id)"
        " OR EXISTS (SELECT 1 FROM blob_refs x JOIN splits s ON s.id = x.owner_id WHERE x.owner_kind = 'split' AND s.version_id = d.id"
        " AND NOT EXISTS (SELECT 1 FROM splits c JOIN datasets dc ON dc.id = c.version_id WHERE c.carried_from = s.id"
        " AND COALESCE(dc.state, '') <> 'deleted'))"
        # F-X3b (M9): a join frame that read the deleted version still holds its cache (deleted before the release)
        " OR EXISTS (SELECT 1 FROM join_frames f JOIN blob_refs x ON x.owner_kind = 'join_frame' AND x.owner_id = f.id"
        " AND x.role = 'cache' WHERE f.version_id = d.id OR f.inputs_json LIKE '%' || d.id || '%')) ORDER BY d.id")]


def plan(db, store: BlobStore, *, now: datetime | None = None, hash_orphans: bool = True) -> dict[str, Any]:
    from ..catalog import holds as H

    now = now or datetime.now(timezone.utc)
    g_pending, g_live, g_orphan = _grace()
    pending_cut = now - timedelta(hours=g_pending)
    live_cut = now - timedelta(days=g_live)
    orphan_cut = now - timedelta(hours=g_orphan)
    with db.connect() as con:
        unref = [dict(r) for r in con.execute(
            "SELECT b.sha256, b.key, b.kind, b.bytes, b.state, b.created_at FROM blobs b"
            " WHERE b.state IN ('pending', 'live') AND NOT EXISTS (SELECT 1 FROM blob_refs r WHERE r.blob_sha256 = b.sha256)"
            " ORDER BY b.created_at, b.sha256")]
        stuck = [dict(r) for r in con.execute(
            "SELECT b.sha256, b.key, b.kind, b.bytes, b.state, b.created_at, b.purge_reason FROM blobs b WHERE b.state = 'purging'"
            " AND NOT EXISTS (SELECT 1 FROM blob_refs r WHERE r.blob_sha256 = b.sha256) ORDER BY b.sha256")]
        rows = {r[0]: r[1] for r in con.execute("SELECT key, state FROM blobs")}
        is_armed = armed(con)
        counts = {r[0]: r[1] for r in con.execute("SELECT state, COUNT(*) FROM blobs GROUP BY state")}
        n_refs = con.execute("SELECT COUNT(*) FROM blob_refs").fetchone()[0]
        any_hold = H.any_active(con)
        stale = stale_owner_refs(con)
        candidates, waiting, held = [], [], []
        for b in unref:
            cut = pending_cut if b["state"] == "pending" else live_cut
            e = {**b, "eligible_after": (_parse(b["created_at"]) + (now - cut)).isoformat(timespec="seconds")}
            if _parse(b["created_at"]) >= cut:
                waiting.append(e)
                continue
            hs = H.active(con, former_owner_keys(con, b["sha256"])) if any_hold else []
            if hs:
                held.append({**e, "holds": [{k: h[k] for k in ("id", "case_ref", "scope_kind", "scope_id")} for h in hs]})
            else:
                candidates.append(e)
    orphans, integrity, young_orphans, tmp_dirs = [], [], 0, {}
    for key, size, mtime in store.list("blobs"):
        name = key.rsplit("/", 1)[-1]
        if name.startswith("."):  # a put in progress (LocalBlobStore's .part names)
            if mtime < orphan_cut:
                orphans.append({"key": key, "bytes": size, "modified": mtime.isoformat(timespec="seconds"), "why": "stale partial put"})
            continue
        if key in rows:
            continue
        parsed = parse_key(key)
        entry = {"key": key, "bytes": size, "modified": mtime.isoformat(timespec="seconds")}
        if parsed is None:
            integrity.append({**entry, "finding": "not a blob key"})
            continue
        if hash_orphans:
            with store.open(key) as f:
                sha, _ = M.file_sha256(f)
            if sha != parsed[0]:
                integrity.append({**entry, "finding": "name does not match its bytes", "sha256_now": sha})
                continue
        if mtime < orphan_cut:
            orphans.append({**entry, "why": "no blobs row"})
        else:
            young_orphans += 1
    for key, size, mtime in store.list("tmp"):
        d = key.split("/")[1] if key.count("/") >= 2 else key
        t = tmp_dirs.setdefault(d, {"dir": f"tmp/{d}", "bytes": 0, "newest": mtime})
        t["bytes"] += size
        t["newest"] = max(t["newest"], mtime)
    stale_tmp = [{**t, "newest": t["newest"].isoformat(timespec="seconds")} for t in tmp_dirs.values() if t["newest"] < orphan_cut]
    return {"dry_run": True, "armed": is_armed, "at": now.isoformat(timespec="seconds"),
            "grace": {"pending_hours": g_pending, "live_days": g_live, "orphan_hours": g_orphan},
            "blobs": counts, "blob_refs": n_refs,
            "candidates": candidates, "candidate_bytes": sum(c["bytes"] for c in candidates),
            "resume_purging": stuck, "held": held,
            "deleted_versions_with_refs": stale,
            "unreferenced_in_grace": waiting,
            "orphans": orphans, "orphans_in_grace": young_orphans, "stale_tmp": stale_tmp, "integrity": integrity,
            "note": ("dry run: nothing was deleted. " + ("A run (dry_run=0 with this plan_id) deletes only the listed candidates "
                     "that are still unreferenced then." if is_armed else "GC is disarmed: a verify-all pass arms it."))}


def dry_run(db, store: BlobStore, by: str) -> dict[str, Any]:
    """The plan, stored under an id (what a run may delete) and audited."""
    from ..catalog.schema import audit

    out = plan(db, store)
    pid = "gcp_" + secrets.token_hex(5)
    rec = {"id": pid, "by": by, "at": out["at"], "candidates": [c["sha256"] for c in out["candidates"]],
           "resume_purging": [c["sha256"] for c in out["resume_purging"]], "stale_tmp": [t["dir"] for t in out["stale_tmp"]],
           "deleted_versions_with_refs": out["deleted_versions_with_refs"]}
    with db.connect() as con:
        con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (PLAN_KEY + pid, json.dumps(rec)))
        audit(con, by, "gc_dry_run", {"plan_id": pid, "armed": out["armed"], "candidates": len(rec["candidates"]),
                                      "candidate_bytes": out["candidate_bytes"], "held": len(out["held"]),
                                      "resume_purging": len(rec["resume_purging"]), "orphans": len(out["orphans"]),
                                      "integrity": len(out["integrity"]), "stale_tmp": len(rec["stale_tmp"]),
                                      "deleted_versions_with_refs": len(rec["deleted_versions_with_refs"])},
              object_kind="lake", object_id="gc")
    return {**out, "plan_id": pid, "by": by}


def _load_plan(con, pid: str) -> dict[str, Any]:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (PLAN_KEY + str(pid or ""),)).fetchone()
    if not r:
        raise GcError(404, "plan_not_found", f"No GC plan {pid!r}: POST /api/admin/lake/gc?dry_run=1 first and send its plan_id.")
    rec = json.loads(r[0])
    if rec.get("used_at"):
        raise GcError(409, "plan_used", f"GC plan {pid} was run on {rec['used_at']} by {rec.get('used_by')}; make a new dry run.")
    age_h = (datetime.now(timezone.utc) - _parse(rec["at"])).total_seconds() / 3600
    if age_h > PLAN_TTL_HOURS:
        raise GcError(409, "plan_stale", f"GC plan {pid} is {age_h:.0f} h old (≤ {PLAN_TTL_HOURS:g} h); make a new dry run.")
    return rec


def run(db, store: BlobStore, *, by: str, plan_id: str, bypass_governance: bool = True) -> dict[str, Any]:
    """Delete what the dry run `plan_id` listed and is still a candidate now (see the module doc)."""
    from ..catalog.schema import audit, now as S_now

    with db.connect() as con:
        if not armed(con):
            raise GcError(409, "gc_disarmed", "GC deletes stay disarmed until a verify-all pass (GET /api/admin/lake/verify-all) "
                                             "has passed on this deployment.", **armed_info(con))
        rec = _load_plan(con, plan_id)
    stale = rec.get("deleted_versions_with_refs") or []
    if stale:  # versions deleted before P24 still held their data refs: dropped now; their blobs show in the next dry run
        from ..catalog.dsets import drop_version_refs

        with db.connect() as con:
            still = [v for v in stale_owner_refs(con) if v in set(stale)]
            n = sum(drop_version_refs(con, v) for v in still)
            audit(con, by, "gc_refs_dropped", {"plan_id": plan_id, "versions": still[:500], "refs": n,
                                               "note": "deleted versions' part / source / split refs; GC lists their blobs next time"},
                  object_kind="lake", object_id="gc")
    fresh = plan(db, store, hash_orphans=False)
    now_c = {c["sha256"]: c for c in fresh["candidates"]}
    now_r = {c["sha256"]: c for c in fresh["resume_purging"]}
    listed = list(dict.fromkeys(rec["candidates"] + rec.get("resume_purging", [])))
    with db.connect() as con:
        audit(con, by, "gc_run_started", {"plan_id": plan_id, "listed": len(listed), "store": store.root,
                                          "bypass_governance": bool(bypass_governance)}, object_kind="lake", object_id="gc")
    deleted, skipped, failed = [], [], []
    reason = f"gc:{plan_id}"
    for sha in listed:
        c = now_c.get(sha) or now_r.get(sha)
        if c is None:
            skipped.append({"sha256": sha, "why": "no longer a candidate (referenced again, held, or already gone)"})
            continue
        with db.connect() as con:
            if c["state"] != "purging":
                n = con.execute("UPDATE blobs SET state = 'purging', purge_reason = ? WHERE sha256 = ? AND state IN ('pending', 'live')"
                                " AND NOT EXISTS (SELECT 1 FROM blob_refs r WHERE r.blob_sha256 = ?)", (reason, sha, sha)).rowcount
                if n != 1:
                    skipped.append({"sha256": sha, "why": "referenced or changed state just now"})
                    continue
        try:
            out = delete_blob(db, store, sha, by=by, reason=reason, bypass_governance=bypass_governance)
        except Exception as e:  # noqa: BLE001 - left 'purging': the next run finishes it (a writer re-puts it meanwhile)
            failed.append({"sha256": sha, "error": f"{type(e).__name__}: {e}"[:300]})
            continue
        with db.connect() as con:
            n = con.execute("UPDATE blobs SET state = 'purged', purged_at = ?, purged_by = ?, purge_reason = ? WHERE sha256 = ?"
                            " AND state = 'purging'", (S_now(), by, reason, sha)).rowcount
            if n != 1:  # a writer re-put the bytes between the mark and the delete (§7 R-S3): say so loudly
                audit(con, by, "gc_race", {"sha256": sha, "plan_id": plan_id, "note": "the blob left 'purging' before its "
                                           "bytes were deleted; verify the versions that reference it"}, object_kind="blob", object_id=sha)
        deleted.append({"sha256": sha, "key": c["key"], "kind": c["kind"], "bytes": c["bytes"],
                        "versions_removed": out.get("versions_removed")})
    tmp_removed = []
    remove = getattr(store, "remove_tmp", None)
    stale_now = {t["dir"] for t in fresh["stale_tmp"]}
    for d in rec.get("stale_tmp", []):
        if d in stale_now and remove:
            remove(store.tmp_dir(d.split("/", 1)[1]))
            tmp_removed.append(d)
    aborted = None
    if hasattr(store, "abort_stale_uploads"):
        try:
            aborted = store.abort_stale_uploads()
        except Exception as e:  # noqa: BLE001
            aborted = f"failed: {type(e).__name__}: {e}"[:200]
    with db.connect() as con:
        rec.update(used_at=S_now(), used_by=by)
        con.execute("UPDATE catalog_meta SET value = ? WHERE key = ?", (json.dumps(rec), PLAN_KEY + plan_id))
        summary = {"plan_id": plan_id, "deleted": len(deleted), "bytes": sum(d["bytes"] for d in deleted), "skipped": len(skipped),
                   "failed": len(failed), "stale_tmp_removed": len(tmp_removed), "aborted_multipart": aborted,
                   "deleted_versions_refs_dropped": len(stale),
                   "store": store.root}
        audit(con, by, "gc_run", summary, object_kind="lake", object_id="gc")
    return {"dry_run": False, **summary, "deleted_blobs": deleted, "skipped_blobs": skipped, "failed_blobs": failed,
            "stale_tmp": tmp_removed, "orphans_reported_only": len(fresh["orphans"]), "integrity": fresh["integrity"]}
