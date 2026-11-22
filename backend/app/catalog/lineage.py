"""GET /api/lineage/{kind}/{id}: the project tree around a node, from one recursive CTE over lineage_edges.

dataset root → derived versions → runs (trained_on) → branches (branched_from; agent/experiment branches grouped by
their review) → registered versions (built_from). P14: input versions → join frames (joined_from; merged_from for another
dataset's version) → runs (trained_on)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# P08 (§7 risk 2): a dataset's versions are listed by root_id (no recursion, so no depth cap on a version chain); the
# CTE seeds every version of the root at the depth of its version number and walks only from there: derived versions in
# other roots, runs, branches and registered versions. `depth < 64` bounds those cross-object walks only.
TREE = """
WITH RECURSIVE down(kind, id, depth) AS (
    SELECT 'dataset', id, COALESCE(version_no, 1) - 1 FROM datasets WHERE id = ? OR root_id = ?
    UNION
    SELECT e.dst_kind, e.dst_id, d.depth + 1 FROM lineage_edges e JOIN down d ON e.src_kind = d.kind AND e.src_id = d.id
    WHERE d.depth < 64 + (SELECT count(*) FROM datasets WHERE id = ? OR root_id = ?)
      AND NOT (e.dst_kind = 'dataset' AND e.dst_id IN (SELECT id FROM datasets WHERE id = ? OR root_id = ?))
)
SELECT kind, id, MIN(depth) AS depth FROM down GROUP BY kind, id
"""

TREE_NODE = """
WITH RECURSIVE down(kind, id, depth) AS (
    SELECT ?, ?, 0
    UNION
    SELECT e.dst_kind, e.dst_id, d.depth + 1 FROM lineage_edges e JOIN down d ON e.src_kind = d.kind AND e.src_id = d.id
    WHERE d.depth < 64
)
SELECT kind, id, MIN(depth) AS depth FROM down GROUP BY kind, id
"""

# up to the first dataset (runs → their dataset, branches → their run, registered versions → their run); a dataset's
# root is then its root_id column, never a walk up its version chain
UP = """
WITH RECURSIVE up(kind, id, depth) AS (
    SELECT ?, ?, 0
    UNION
    SELECT e.src_kind, e.src_id, u.depth + 1 FROM lineage_edges e JOIN up u ON e.dst_kind = u.kind AND e.dst_id = u.id
    WHERE u.depth < 64 AND u.kind <> 'dataset' AND e.relation IN ('trained_on', 'branched_from', 'built_from', 'joined_from')
)
SELECT kind, id, MAX(depth) AS depth FROM up GROUP BY kind, id ORDER BY depth DESC
"""


def _j(v: str | None) -> Any:
    return json.loads(v) if v else None


def root_of(con, kind: str, id_: str) -> tuple[str, str]:
    """The top dataset upstream of a node (the project's root): the node's dataset, then its root_id; a root derived
    from another dataset's version (a cross-root derived_from edge) continues to that dataset's root."""
    if kind == "dataset":
        ds = id_
    else:
        rows = con.execute(UP, (kind, id_)).fetchall()
        hit = [r for r in rows if r["kind"] == "dataset"]
        if not hit:
            return (kind, id_)
        ds = hit[0]["id"]
    seen: set[str] = set()
    while ds not in seen:
        seen.add(ds)
        r = con.execute("SELECT id, root_id FROM datasets WHERE id = ?", (ds,)).fetchone()
        root = (r["root_id"] or r["id"]) if r else ds
        up = con.execute("SELECT src_id FROM lineage_edges WHERE dst_kind = 'dataset' AND dst_id = ? AND src_kind = 'dataset'"
                         " AND relation = 'derived_from' AND src_id NOT IN (SELECT id FROM datasets WHERE id = ? OR root_id = ?)"
                         " LIMIT 1", (root, root, root)).fetchone()
        if not up:
            return ("dataset", root)
        ds = up[0]
    return ("dataset", ds)


def review_journal(run: dict[str, Any] | None, review_id: str) -> dict[str, Any] | None:
    if not run:
        return None
    p = Path(run["config"].get("run_dir") or "") / "reviews" / f"{review_id}.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


def tree(app, kind: str, id_: str) -> dict[str, Any]:
    db = app.state.db
    with db.connect() as con:
        if kind == "project":
            p = con.execute("SELECT * FROM projects WHERE id = ?", (id_,)).fetchone()
            if not p:
                raise KeyError(id_)
            # F-P2: every dataset root in the project (a user project can hold several uploads; a new one none yet)
            roots = [r[0] for r in con.execute("SELECT id FROM datasets WHERE project_id = ? AND parent_id IS NULL ORDER BY created_at",
                                               (id_,))]
            if not roots and p["root_dataset_id"] and con.execute("SELECT 1 FROM datasets WHERE id = ? AND project_id = ?",
                                                                  (p["root_dataset_id"], id_)).fetchone():
                roots = [p["root_dataset_id"]]
            root = ("dataset", roots[0]) if roots else ("project", id_)
        else:
            root = root_of(con, kind, id_)
            roots = [root[1]] if root[0] == "dataset" else []
        nodes = []
        for r0 in roots or ([] if kind == "project" else [root[1]]):
            nodes += (con.execute(TREE, (r0,) * 6) if roots else con.execute(TREE_NODE, root)).fetchall()
        keys = {(n["kind"], n["id"]) for n in nodes}
        depth = {(n["kind"], n["id"]): n["depth"] for n in nodes}
        ids = {k: [i for (kk, i) in keys if kk == k] for k in ("dataset", "run", "version", "join_frame")}

        def q(sql: str, vals: list[str]) -> list[dict[str, Any]]:
            out = []
            for i in range(0, len(vals), 500):
                chunk = vals[i:i + 500]
                out += [dict(r) for r in con.execute(sql.format(",".join("?" * len(chunk))), chunk).fetchall()]
            return out

        dsets = q("SELECT id, name, rows, created_at, project_id, parent_id, root_id, sha256, op, target, state FROM datasets WHERE id IN ({})",
                  ids["dataset"])
        runs = q("SELECT id, name, dataset_id, label, status, created_at, project_id, parent_run_id, source, kind, primary_metric,"
                 " primary_score, greater_is_better, predictor_state, predictor_bytes, json_extract(config, '$.run_dir') AS run_dir"
                 " FROM runs WHERE id IN ({})", ids["run"])
        vers = q("SELECT id, model_name, version, run_id, status, stage, approval, created_at, created_by FROM model_versions"
                 " WHERE id IN ({})", ids["version"])
        # P14: join frames (dataset -joined_from / merged_from-> join_frame -trained_on-> run)
        jfs = q("SELECT f.id, f.join_id, f.version_id, f.grain_table, f.frame_sha256, f.n_rows, f.cache_blob, f.created_at,"
                " f.created_by, j.name AS join_name, j.spec_version FROM join_frames f LEFT JOIN joins j ON j.id = f.join_id"
                " WHERE f.id IN ({})", ids["join_frame"])
        all_ids = [i for (_, i) in keys]
        edges = q("SELECT * FROM lineage_edges WHERE dst_id IN ({})", all_ids)
        edges = [e for e in edges if (e["src_kind"], e["src_id"]) in keys and (e["dst_kind"], e["dst_id"]) in keys]
        tags = q("SELECT target_kind, target_id, value FROM annotations WHERE kind = 'tag' AND deleted_at IS NULL AND target_id IN ({})",
                 all_ids)
        notes = q("SELECT target_id, COUNT(*) AS n FROM annotations WHERE kind = 'note' AND deleted_at IS NULL AND target_id IN ({})"
                  " GROUP BY target_id", all_ids)
        proj_id = id_ if kind == "project" else next((d["project_id"] for d in dsets if d["id"] == root[1]), None)
        project = dict(con.execute("SELECT * FROM projects WHERE id = ?", (proj_id,)).fetchone() or {}) if proj_id else None
    champ = app.state.catalog.champion(proj_id) if proj_id else None
    tag_map: dict[str, list[str]] = {}
    for t in tags:
        tag_map.setdefault(t["target_id"], []).append(t["value"])
    note_map = {n["target_id"]: n["n"] for n in notes}
    # -- review branches: review_id (and the verdict once the review is over) are stored on the edge at write time
    # (F-P2 backfilled the old ones), so the tree reads one journal per review, never one glob per edge
    reviews: dict[str, dict[str, Any]] = {}
    run_dir = {r["id"]: r.pop("run_dir") for r in runs}
    for e in edges:
        e["meta"] = _j(e["meta"]) or {}
    # fallback for a branch written before its review journal named it (one directory check per parent, not per edge)
    by_child: dict[str, str] = {}
    for src in {e["src_id"] for e in edges if e["relation"] == "branched_from" and not e["meta"].get("review_id")}:
        d = Path(run_dir.get(src) or "/nonexistent") / "reviews"
        if d.is_dir():
            for p in d.glob("rv_*.json"):
                for j in (review_journal({"config": {"run_dir": run_dir.get(src)}}, p.stem) or {}).get("journal") or []:
                    if j.get("run_id"):
                        by_child.setdefault(j["run_id"], p.stem)
    for e in edges:
        if e["relation"] == "branched_from" and not e["meta"].get("review_id") and by_child.get(e["dst_id"]):
            e["meta"]["review_id"] = by_child[e["dst_id"]]
            app.state.catalog.update_edge_meta(("run", e["src_id"]), ("run", e["dst_id"]), "branched_from",
                                               {"review_id": e["meta"]["review_id"]})
    for e in edges:
        m = e["meta"]
        if e["relation"] != "branched_from":
            continue
        rid = m.get("review_id")
        if not rid:
            continue
        if rid not in reviews:
            rv = review_journal({"config": {"run_dir": run_dir.get(e["src_id"])}}, rid) or {}
            reviews[rid] = {"id": rid, "run_id": e["src_id"], "status": rv.get("status"), "by": rv.get("by"),
                            "mode": rv.get("mode"), "started_at": rv.get("started_at"), "finished_at": rv.get("finished_at"),
                            "_journal": {j.get("run_id"): j for j in rv.get("journal") or [] if j.get("run_id")}}
        if m.get("verdict"):
            continue
        j = reviews[rid]["_journal"].get(e["dst_id"])
        if j:
            m.update(verdict=j.get("verdict"), hypothesis_id=(j.get("hypothesis") or {}).get("id"), step=j.get("step"),
                     hypothesis=(j.get("hypothesis") or {}).get("title"))
            if reviews[rid]["status"] in ("done", "stopped", "interrupted", "failed") and j.get("verdict"):
                app.state.catalog.update_edge_meta(("run", e["src_id"]), ("run", e["dst_id"]), "branched_from",
                                                   {"verdict": j.get("verdict"), "hypothesis_id": m.get("hypothesis_id"), "step": j.get("step")})
    for rv in reviews.values():
        rv.pop("_journal", None)
    out_nodes = []
    for d in dsets:
        out_nodes.append({"kind": "dataset", **d, "depth": depth.get(("dataset", d["id"])), "tags": tag_map.get(d["id"], []),
                          "notes": note_map.get(d["id"], 0)})
    for r in runs:
        out_nodes.append({"kind": "run", "run_kind": r.pop("kind"), **r, "depth": depth.get(("run", r["id"])),
                          "tags": tag_map.get(r["id"], []), "notes": note_map.get(r["id"], 0)})
    for v in vers:
        out_nodes.append({"kind": "version", **v, "depth": depth.get(("version", v["id"])), "tags": tag_map.get(v["id"], []),
                          "notes": note_map.get(v["id"], 0), "champion": bool(champ and champ["version_id"] == v["id"])})
    for f in jfs:
        out_nodes.append({"kind": "join_frame", **f, "depth": depth.get(("join_frame", f["id"]))})
    return {"focus": {"kind": kind, "id": id_}, "root": {"kind": root[0], "id": root[1]}, "project": project,
            "champion": champ, "nodes": out_nodes,
            "edges": [{"src": {"kind": e["src_kind"], "id": e["src_id"]}, "dst": {"kind": e["dst_kind"], "id": e["dst_id"]},
                       "relation": e["relation"], "actor": e["actor"], "meta": e["meta"], "inferred": bool(e["inferred"]),
                       "created_at": e["created_at"]} for e in edges],
            "reviews": list(reviews.values()),
            "counts": {"datasets": len(dsets), "runs": len(runs), "versions": len(vers), "join_frames": len(jfs), "edges": len(edges)}}
