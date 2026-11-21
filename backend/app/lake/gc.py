"""P02: lake garbage collection, **dry run only** (plan §5.10; deletes are P24's, behind catalog_meta.gc_armed).

Phase 1 (DB-driven): blobs with no blob_refs, state pending older than 24 h or live older than 7 days → candidates.
Phase 2 (store-driven): objects under blobs/ with no blobs row, and tmp/ writer directories, older than 24 h →
orphans. A blob name that does not match its own bytes' sha256 is an **integrity finding**, never a delete candidate.

Nothing here deletes or changes a row; `plan()` only reports what a real run would do.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .. import config
from . import manifest as M
from .store import BlobStore, parse_key


def _parse(ts: str) -> datetime:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def armed(con) -> bool:
    r = con.execute("SELECT value FROM catalog_meta WHERE key = 'gc_armed'").fetchone()
    return bool(r and r[0] not in ("", "0", None))


def plan(db, store: BlobStore, *, now: datetime | None = None, hash_orphans: bool = True) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    pending_cut = now - timedelta(hours=config.GC_PENDING_GRACE_HOURS)
    live_cut = now - timedelta(days=config.GC_LIVE_GRACE_DAYS)
    orphan_cut = now - timedelta(hours=config.GC_ORPHAN_GRACE_HOURS)
    with db.connect() as con:
        unref = [dict(r) for r in con.execute(
            "SELECT b.sha256, b.key, b.kind, b.bytes, b.state, b.created_at FROM blobs b"
            " WHERE b.state IN ('pending', 'live') AND NOT EXISTS (SELECT 1 FROM blob_refs r WHERE r.blob_sha256 = b.sha256)"
            " ORDER BY b.created_at")]
        rows = {r[0]: r[1] for r in con.execute("SELECT key, state FROM blobs")}
        is_armed = armed(con)
        counts = {r[0]: r[1] for r in con.execute("SELECT state, COUNT(*) FROM blobs GROUP BY state")}
        n_refs = con.execute("SELECT COUNT(*) FROM blob_refs").fetchone()[0]
    candidates, waiting = [], []
    for b in unref:
        cut = pending_cut if b["state"] == "pending" else live_cut
        (candidates if _parse(b["created_at"]) < cut else waiting).append(
            {**b, "eligible_after": (_parse(b["created_at"]) + (now - cut)).isoformat(timespec="seconds")})
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
            "grace": {"pending_hours": config.GC_PENDING_GRACE_HOURS, "live_days": config.GC_LIVE_GRACE_DAYS,
                      "orphan_hours": config.GC_ORPHAN_GRACE_HOURS},
            "blobs": counts, "blob_refs": n_refs,
            "candidates": candidates, "candidate_bytes": sum(c["bytes"] for c in candidates),
            "unreferenced_in_grace": waiting,
            "orphans": orphans, "orphans_in_grace": young_orphans, "stale_tmp": stale_tmp, "integrity": integrity,
            "note": "dry run: nothing was deleted. Deletes are built in P24 and stay disarmed until verify-all has passed."}
