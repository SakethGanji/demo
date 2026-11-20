"""The catalog over the shared SQLite: write hooks (datasets, runs, versions), edges, projects, tags and notes, champions."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from ..db import Database
from . import schema as S
from .schema import add_edge, edge_meta_for_run, ensure_project, file_sha256, now, run_columns


def _loads(v: str | None) -> Any:
    return json.loads(v) if v else None


class Catalog:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- write hooks ----------------------------------------------------------------------------------------------

    def record_root_dataset(self, rec: dict[str, Any], csv_path: Path, op: str | None = None) -> None:
        """A dataset with no parent (a sample, an upload, a first SQL load): its own root and its own default project."""
        with self.db.connect() as con:
            pid = ensure_project(con, rec["id"], rec["name"], rec.get("created_at"))
            con.execute("UPDATE datasets SET project_id = ?, parent_id = NULL, root_id = ?, sha256 = ?, n_rows = ?, op = ?"
                        " WHERE id = ?", (pid, rec["id"], file_sha256(csv_path), rec.get("rows"), op or rec.get("source"), rec["id"]))

    def record_version(self, parent_id: str, rec: dict[str, Any], csv_path: Path, *, op: str, actor: str = "system",
                       meta: dict[str, Any] | None = None, paired: bool = True) -> None:
        """A derived dataset: same project and root as the parent, and a derived_from edge. `paired` = False when the
        new rows have no root-row mapping (a monitoring window, a SQL refresh): the edge is marked inferred."""
        with self.db.connect() as con:
            p = con.execute("SELECT project_id, root_id FROM datasets WHERE id = ?", (parent_id,)).fetchone()
            root = (p["root_id"] if p and p["root_id"] else parent_id)
            pid = p["project_id"] if p and p["project_id"] else S.project_id_for(root)
            con.execute("UPDATE datasets SET project_id = ?, parent_id = ?, root_id = ?, sha256 = ?, n_rows = ?, op = ? WHERE id = ?",
                        (pid, parent_id, root, file_sha256(csv_path), rec.get("rows"), op, rec["id"]))
            add_edge(con, ("dataset", parent_id), ("dataset", rec["id"]), "derived_from", actor=actor,
                     meta={"op": op, **(meta or {})}, inferred=not paired)
            S.drop_if_phantom(con, S.project_id_for(rec["id"]))  # F-P2: the default project an upload path made for it

    def record_run(self, run_id: str, dataset_id: str, cfg: dict[str, Any], actor: str | None = None) -> None:
        """A new run: indexed columns, trained_on its dataset, branched_from its parent run (Gym, Vela, experiments)."""
        cols = run_columns(cfg)
        with self.db.connect() as con:
            d = con.execute("SELECT project_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
            con.execute("UPDATE runs SET project_id = ?, parent_run_id = ?, source = ?, kind = ?, predictor_state = ? WHERE id = ?",
                        (d["project_id"] if d else None, cols["parent_run_id"], cols["source"], cols["kind"],
                         S.NONE, run_id))  # 'present' once the run finishes with a predictor (RunStore.finish)
            add_edge(con, ("dataset", dataset_id), ("run", run_id), "trained_on", actor=actor or "system")
            if cfg.get("parent_run_id"):
                add_edge(con, ("run", cfg["parent_run_id"]), ("run", run_id), "branched_from", actor=actor or "system",
                         meta=edge_meta_for_run(cfg))

    def record_result(self, con, run_id: str, result: dict[str, Any] | None) -> None:
        """Called inside RunStore's write: keep primary_metric / primary_score in step with the result JSON."""
        m, v, g = S.primary(result)
        if m is not None:
            con.execute("UPDATE runs SET primary_metric = ?, primary_score = ?, greater_is_better = ? WHERE id = ?", (m, v, g, run_id))

    def record_build(self, version: dict[str, Any]) -> None:
        with self.db.connect() as con:
            add_edge(con, ("run", version["run_id"]), ("version", version["id"]), "built_from", actor=version.get("created_by") or "system",
                     meta={"model_name": version.get("model_name"), "version": version.get("version")})

    def update_edge_meta(self, src: tuple[str, str], dst: tuple[str, str], relation: str, meta: dict[str, Any]) -> None:
        with self.db.connect() as con:
            r = con.execute("SELECT meta FROM lineage_edges WHERE src_kind=? AND src_id=? AND dst_kind=? AND dst_id=? AND relation=?",
                            (*src, *dst, relation)).fetchone()
            if r is None:
                return
            m = {**(_loads(r["meta"]) or {}), **{k: v for k, v in meta.items() if v is not None}}
            con.execute("UPDATE lineage_edges SET meta = ? WHERE src_kind=? AND src_id=? AND dst_kind=? AND dst_id=? AND relation=?",
                        (json.dumps(m, default=str), *src, *dst, relation))

    # -- edges ----------------------------------------------------------------------------------------------------

    def children(self, run_id: str, source: str | None = None) -> list[dict[str, Any]]:
        """Runs branched from `run_id` (from the edge table, not a scan of every run)."""
        q = ("SELECT r.id, r.name, r.status, r.created_at, r.dataset_id, r.source, r.primary_metric, r.primary_score, e.meta"
             " FROM lineage_edges e JOIN runs r ON r.id = e.dst_id WHERE e.src_kind = 'run' AND e.src_id = ?"
             " AND e.dst_kind = 'run' AND e.relation = 'branched_from'")
        args: list[Any] = [run_id]
        if source:
            q += " AND r.source = ?"
            args.append(source)
        with self.db.connect() as con:
            rows = con.execute(q + " ORDER BY r.created_at DESC", args).fetchall()
        return [{**dict(r), "meta": _loads(r["meta"])} for r in rows]

    # -- projects ---------------------------------------------------------------------------------------------------

    def projects(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM datasets d WHERE d.project_id = p.id) AS n_datasets,"
                " (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS n_runs,"
                " (SELECT MAX(r.created_at) FROM runs r WHERE r.project_id = p.id) AS last_run_at"
                " FROM projects p ORDER BY last_run_at IS NULL, last_run_at DESC, p.created_at").fetchall()
        return [dict(r) for r in rows]

    def project(self, pid: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return dict(r) if r else None

    def project_of(self, kind: str, id_: str) -> str | None:
        with self.db.connect() as con:
            if kind == "dataset":
                r = con.execute("SELECT project_id FROM datasets WHERE id = ?", (id_,)).fetchone()
            elif kind == "run":
                r = con.execute("SELECT project_id FROM runs WHERE id = ?", (id_,)).fetchone()
            elif kind == "version":
                r = con.execute("SELECT r.project_id FROM model_versions v JOIN runs r ON r.id = v.run_id WHERE v.id = ?", (id_,)).fetchone()
            else:
                r = con.execute("SELECT id FROM projects WHERE id = ?", (id_,)).fetchone()
        return r[0] if r else None

    def rename_project(self, pid: str, name: str | None, description: str | None = None) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE projects SET name = COALESCE(?, name), description = COALESCE(?, description) WHERE id = ?",
                        (name, description, pid))

    def create_project(self, name: str, description: str, by: str) -> dict[str, Any]:
        """A user-made project (no root dataset yet: upload into it, or move a dataset tree into it)."""
        pid = "prj_" + secrets.token_hex(4)
        with self.db.connect() as con:
            con.execute("INSERT INTO projects (id, name, root_dataset_id, description, created_at, created_by) VALUES (?, ?, NULL, ?, ?, ?)",
                        (pid, name, description, now(), by))
        return self.project(pid)

    def move_dataset_tree(self, root_id: str, pid: str) -> dict[str, int]:
        """Move a dataset root, every version derived from it and every run trained on them into project `pid`. The
        project's root becomes this dataset if it had none. The project the tree leaves is removed if it is now empty
        and was made by the system (a default project)."""
        with self.db.connect() as con:
            old = con.execute("SELECT project_id FROM datasets WHERE id = ?", (root_id,)).fetchone()
            old_pid = old["project_id"] if old else None
            ids = [r[0] for r in con.execute("SELECT id FROM datasets WHERE id = ? OR root_id = ?", (root_id, root_id))]
            q = ",".join("?" * len(ids))
            con.execute(f"UPDATE datasets SET project_id = ? WHERE id IN ({q})", (pid, *ids))
            n_runs = con.execute(f"UPDATE runs SET project_id = ? WHERE dataset_id IN ({q})", (pid, *ids)).rowcount
            con.execute("UPDATE projects SET root_dataset_id = COALESCE(root_dataset_id, ?) WHERE id = ?", (root_id, pid))
            if old_pid and old_pid != pid:
                con.execute("UPDATE projects SET root_dataset_id = NULL WHERE id = ? AND root_dataset_id = ?", (old_pid, root_id))
                S.drop_if_phantom(con, old_pid)
        return {"datasets": len(ids), "runs": n_runs}

    def project_roots(self, pid: str) -> list[str]:
        """Every dataset root in the project (a user project can hold several uploads)."""
        with self.db.connect() as con:
            return [r[0] for r in con.execute("SELECT id FROM datasets WHERE project_id = ? AND parent_id IS NULL ORDER BY created_at",
                                              (pid,))]

    # -- tags and notes ---------------------------------------------------------------------------------------------

    def annotations(self, kind: str, id_: str) -> dict[str, Any]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM annotations WHERE target_kind = ? AND target_id = ? AND deleted_at IS NULL"
                               " ORDER BY id", (kind, id_)).fetchall()
        rows = [dict(r) for r in rows]
        return {"tags": sorted({r["value"] for r in rows if r["kind"] == "tag"}),
                "notes": [r for r in rows if r["kind"] == "note"]}

    def annotate(self, kind: str, id_: str, akind: str, value: str, actor: str) -> dict[str, Any]:
        with self.db.connect() as con:
            if akind == "tag" and con.execute("SELECT 1 FROM annotations WHERE target_kind=? AND target_id=? AND kind='tag'"
                                              " AND lower(value)=lower(?) AND deleted_at IS NULL", (kind, id_, value)).fetchone():
                return self.annotations(kind, id_)
            con.execute("INSERT INTO annotations (target_kind, target_id, kind, value, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (kind, id_, akind, value, actor, now()))
        return self.annotations(kind, id_)

    def remove_tag(self, kind: str, id_: str, value: str, actor: str) -> dict[str, Any] | None:
        """None when there is no such tag (case-insensitive)."""
        with self.db.connect() as con:
            n = con.execute("UPDATE annotations SET deleted_at = ?, deleted_by = ? WHERE target_kind=? AND target_id=? AND kind='tag'"
                            " AND lower(value)=lower(?) AND deleted_at IS NULL", (now(), actor, kind, id_, value)).rowcount
        return self.annotations(kind, id_) if n else None

    def note(self, note_id: int) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM annotations WHERE id = ? AND kind = 'note'", (note_id,)).fetchone()
        return dict(r) if r else None

    def edit_note(self, note_id: int, text: str | None, actor: str) -> None:
        """Edit (text) or delete (None) a note: the old row is soft-deleted, so the history stays in the table."""
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM annotations WHERE id = ?", (note_id,)).fetchone()
            con.execute("UPDATE annotations SET deleted_at = ?, deleted_by = ? WHERE id = ?", (now(), actor, note_id))
            if text is not None:
                con.execute("INSERT INTO annotations (target_kind, target_id, kind, value, actor, created_at) VALUES (?, ?, 'note', ?, ?, ?)",
                            (r["target_kind"], r["target_id"], text, actor, now()))

    def tagged(self, kind: str, value: str) -> set[str]:
        """Targets carrying the tag, case-insensitively ("Keep" and "KEEP" are keep)."""
        with self.db.connect() as con:
            rows = con.execute("SELECT target_id FROM annotations WHERE target_kind=? AND kind='tag' AND lower(value)=lower(?)"
                               " AND deleted_at IS NULL", (kind, value)).fetchall()
        return {r[0] for r in rows}

    # -- champions ----------------------------------------------------------------------------------------------------

    def champion(self, pid: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM champions WHERE project_id = ? AND status = 'approved' ORDER BY decided_at DESC LIMIT 1",
                            (pid,)).fetchone()
        return dict(r) if r else None

    def champion_requests(self, pid: str) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM champions WHERE project_id = ? ORDER BY requested_at DESC LIMIT 20", (pid,)).fetchall()
        return [dict(r) for r in rows]

    def request_champion(self, pid: str, version: dict[str, Any], by: str, comment: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """A new pending request; an older pending one is withdrawn (not rejected: nobody decided it). Returns the new
        request and the withdrawn ones (the caller audits them)."""
        cid = "chp_" + secrets.token_hex(4)
        with self.db.connect() as con:
            old = [dict(r) for r in con.execute("SELECT * FROM champions WHERE project_id = ? AND status = 'pending'", (pid,))]
            con.execute("UPDATE champions SET status = 'withdrawn', decided_at = ?, comment = comment || ' (withdrawn: a newer request by ' || ? || ')'"
                        " WHERE project_id = ? AND status = 'pending'", (now(), by, pid))
            con.execute("INSERT INTO champions (id, project_id, version_id, run_id, status, requested_by, requested_at, comment)"
                        " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)", (cid, pid, version["id"], version["run_id"], by, now(), comment))
        return self.get_champion_request(cid), old

    def get_champion_request(self, cid: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM champions WHERE id = ?", (cid,)).fetchone()
        return dict(r) if r else None

    def decide_champion(self, cid: str, approve: bool, by: str, comment: str) -> dict[str, Any]:
        with self.db.connect() as con:
            r = con.execute("SELECT project_id FROM champions WHERE id = ?", (cid,)).fetchone()
            if approve:
                con.execute("UPDATE champions SET status = 'superseded' WHERE project_id = ? AND status = 'approved'", (r[0],))
            con.execute("UPDATE champions SET status = ?, decided_by = ?, decided_at = ?, comment = CASE WHEN ? = '' THEN comment"
                        " ELSE comment || ' · ' || ? END WHERE id = ?",
                        ("approved" if approve else "rejected", by, now(), comment, comment, cid))
        return self.get_champion_request(cid)

    def champion_version_ids(self) -> set[str]:
        with self.db.connect() as con:
            return {r[0] for r in con.execute("SELECT version_id FROM champions WHERE status = 'approved'")}
