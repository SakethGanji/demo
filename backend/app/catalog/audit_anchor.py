"""F-X4b (X4 M6 / m1): anchors for the tail of each team's audit chain.

The chain (schema.audit) proves that no row in the middle was edited, removed or inserted. Its tail used to be
anchored only by `catalog_meta audit_head:<team>`, a row in the same database the app can rewrite: delete the last N
rows, set the head back, and verify said ok. Two anchors now sit outside the app's reach:

1. **`audit_heads`** (schema.P01_DDL): every head the chain has had, one row per audit row, written in the same
   transaction. Write-once: the `ahead_ro_u` / `ahead_ro_d` triggers, and on Postgres the app role has SELECT and
   INSERT only (pg_schema.APPEND_ONLY), so it cannot drop the triggers or delete a head. Verify compares the chain's
   last row with the latest head recorded there.
2. **Signed checkpoints**: every ENSEMBLE_AUDIT_CHECKPOINT_SECONDS (default 300; 0 = off) and on demand
   (`POST /api/admin/audit/checkpoint`), each team's latest head is appended as one HMAC-signed JSON line to
   `checkpoints.jsonl` in ENSEMBLE_AUDIT_CHECKPOINT_DIR (default `<data>/audit`). A database owner who also deletes
   `audit_heads` rows is caught by the last checkpoint: the row it names must still be in the chain with that hash.
   The key is ENSEMBLE_AUDIT_CHECKPOINT_KEY, else a 0600 key file made once next to the checkpoints. In production put
   the directory on write-once storage (or ship the lines to a log the database owner can't write) and the key in a
   secret store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

log = logging.getLogger("ensemble.audit")

FILE = "checkpoints.jsonl"


def directory(data_dir: Path | str) -> Path:
    d = (os.environ.get("ENSEMBLE_AUDIT_CHECKPOINT_DIR") or "").strip()
    return Path(d) if d else Path(data_dir) / "audit"


def every_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get("ENSEMBLE_AUDIT_CHECKPOINT_SECONDS", "300")))
    except ValueError:
        return 300.0


def _key(data_dir: Path | str) -> bytes:
    k = (os.environ.get("ENSEMBLE_AUDIT_CHECKPOINT_KEY") or "").strip()
    if k:
        return k.encode()
    d = directory(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ".checkpoint.key"
    if not p.exists():
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return p.read_text().strip().encode()


def _canon(c: dict[str, Any]) -> str:
    return json.dumps({k: c[k] for k in ("team_id", "audit_id", "head", "ts", "at")}, sort_keys=True, separators=(",", ":"))


def _sig(key: bytes, c: dict[str, Any]) -> str:
    return hmac.new(key, _canon(c).encode(), hashlib.sha256).hexdigest()


def read(data_dir: Path | str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """The latest valid checkpoint per team, and the lines that do not verify (bad signature / not JSON)."""
    p = directory(data_dir) / FILE
    latest: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, Any]] = []
    if not p.exists():
        return latest, bad
    key = _key(data_dir)
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            c = json.loads(line)
            ok = hmac.compare_digest(str(c.get("sig")), _sig(key, c))
        except (ValueError, KeyError, TypeError):
            ok, c = False, {}
        if not ok:
            bad.append({"line": n, "team_id": c.get("team_id") if isinstance(c, dict) else None})
            continue
        cur = latest.get(c["team_id"])
        if cur is None or int(c["audit_id"]) >= int(cur["audit_id"]):
            latest[c["team_id"]] = c
    return latest, bad


def checkpoint(db: Any, data_dir: Path | str) -> dict[str, Any]:
    """Append a signed line for every team whose latest recorded head moved since its last checkpoint."""
    from .schema import now

    with db.connect() as con:
        heads = [dict(r) for r in con.execute(
            "SELECT h.team_id, h.audit_id, h.head, h.ts FROM audit_heads h JOIN (SELECT team_id, MAX(audit_id) AS m"
            " FROM audit_heads GROUP BY team_id) x ON x.team_id = h.team_id AND x.m = h.audit_id ORDER BY h.team_id")]
    latest, _bad = read(data_dir)
    key = _key(data_dir)
    out = []
    for h in heads:
        cur = latest.get(h["team_id"])
        if cur and int(cur["audit_id"]) == int(h["audit_id"]) and cur["head"] == h["head"]:
            continue
        c = {"team_id": h["team_id"], "audit_id": int(h["audit_id"]), "head": h["head"], "ts": h["ts"], "at": now()}
        c["sig"] = _sig(key, c)
        out.append(c)
    if out:
        d = directory(data_dir)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / FILE, "a") as f:
            for c in out:
                f.write(json.dumps(c, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return {"written": len(out), "teams": len(heads), "file": str(directory(data_dir) / FILE)}


def latest_head(con: Any, team_id: str) -> dict[str, Any] | None:
    r = con.execute("SELECT audit_id, head FROM audit_heads WHERE team_id = ? ORDER BY audit_id DESC LIMIT 1", (team_id,)).fetchone()
    return {"audit_id": int(r[0]), "head": r[1]} if r else None
