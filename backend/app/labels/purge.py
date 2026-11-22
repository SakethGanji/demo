"""Purge reaches every label copy (labeling plan §8.4; LB-A minimal L06a). Called by catalog/purge.execute inside its
transaction, after the purge request is 'approved':

- label events on a purged column (any row) or a purged row (any column) are redacted (value/provenance/hashes → NULL,
  purged_at, purge_id): the ONLY UPDATE the le_ro_u trigger allows, and only for an approved purge request;
- every snapshot of the root/table's layers that holds a purged column or row → state 'purged', its blobs (Parquet +
  manifest) and the overlay blobs of runs pinned to it → 'purging' with the purge's reason, so the purge's own byte
  deletion and sweep cover them (run pins become `purged:` markers; run verify answers 'purged', never 'mismatch');
- LLM cache rows of purged rows (or of the layer, for a purged column) are deleted.
catalog/artifacts.SKIP_TABLES holds label_events / label_snapshots: the generic DB scrub never touches them."""

from __future__ import annotations

from typing import Any

from ..catalog.schema import now


def _q(n: int) -> str:
    return ",".join("?" for _ in range(n))


def purge_in_tx(con: Any, pid: str, root: str, table: str, columns: list[str], row_ids: list[int], by: str) -> dict[str, Any]:
    import json

    layers = [r[0] for r in con.execute("SELECT id FROM label_layers WHERE root_id = ? AND table_name = ?", (root, table))]
    out = {"layers": layers, "events_redacted": 0, "snapshots_purged": [], "overlay_blobs": 0, "llm_cache_deleted": 0}
    if not layers:
        return out
    ok = "EXISTS (SELECT 1 FROM approval_requests WHERE id = ? AND status = 'approved')"
    cols, rids = list(columns or []), [int(i) for i in row_ids or []]
    for lid in layers:
        conds, args = [], []
        if cols:
            conds.append(f"column_name IN ({_q(len(cols))})")
            args += cols
        for i in range(0, len(rids), 500):
            chunk = rids[i:i + 500]
            conds.append(f"row_id IN ({_q(len(chunk))})")
            args += chunk
        if not conds:
            continue
        n = con.execute(f"UPDATE label_events SET value = NULL, provenance = '{{}}', row_hash_ctx = NULL, row_hash_all = NULL,"
                        f" base_value_hash = NULL, purged_at = ?, purge_id = ? WHERE layer_id = ? AND purged_at IS NULL"
                        f" AND ({' OR '.join(conds)}) AND {ok}", (now(), pid, lid, *args, pid)).rowcount or 0
        out["events_redacted"] += n
        if cols:
            out["llm_cache_deleted"] += con.execute("DELETE FROM label_llm_cache WHERE layer_id = ?", (lid,)).rowcount or 0
        elif rids:
            for i in range(0, len(rids), 500):
                chunk = rids[i:i + 500]
                out["llm_cache_deleted"] += con.execute(f"DELETE FROM label_llm_cache WHERE layer_id = ? AND row_id IN ({_q(len(chunk))})",
                                                        (lid, *chunk)).rowcount or 0
        for s in con.execute("SELECT id, blob_sha256, manifest_blob, counts, policy FROM label_snapshots WHERE layer_id = ?"
                             " AND state <> 'purged'", (lid,)).fetchall():
            hit = bool(rids)  # a row purge: assume the snapshot may hold the row (cheap and safe)
            if cols and not hit:
                pol = json.loads(s["policy"] or "{}")
                pc = pol.get("columns")
                hit = pc is None or any(c in pc for c in cols)
            if not hit:
                continue
            con.execute("UPDATE label_snapshots SET state = 'purged' WHERE id = ?", (s["id"],))
            shas = [s["blob_sha256"], s["manifest_blob"]]
            runs = [r[0] for r in con.execute("SELECT overlay_blob FROM run_inputs WHERE label_snapshot_id = ? AND overlay_blob IS NOT NULL",
                                              (s["id"],))]
            out["overlay_blobs"] += len(runs)
            shas += runs
            con.execute(f"UPDATE blob_refs SET role = 'purged:' || role WHERE owner_kind = 'run' AND role NOT LIKE 'purged:%'"
                        f" AND blob_sha256 IN ({_q(len(shas))})", shas)
            con.execute(f"DELETE FROM blob_refs WHERE owner_kind = 'label_snapshot' AND blob_sha256 IN ({_q(len(shas))})", shas)
            con.execute(f"UPDATE blobs SET state = 'purging', purge_reason = ? WHERE sha256 IN ({_q(len(shas))})"
                        " AND state IN ('pending', 'live', 'purging')", (f"purge:{pid}", *shas))
            out["snapshots_purged"].append(s["id"])
    return out
