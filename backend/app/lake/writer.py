"""P02: the version writer and the commit point (plan §5.2, A §3 / §5.2).

    w = VersionWriter(db, store, root_id=…, parent_id=…, op=…, by=…, reason=…, job_id=…)
    w.carry(t) | w.append(t, rel_sql) | w.write(t, rel_sql) | w.write_arrow(t, arrow_with___row_id)
    w.key(t, cols) ; w.relation(…) ; w.split(t, key, rel_sql, …)
    out = w.commit()

Every table file has `__row_id BIGINT NOT NULL` first, unique, sorted; parts are written by DuckDB `COPY` into
`tmp/<writer>/`, hashed while still in tmp, moved into `blobs/` (state `pending`) before the commit. The manifest is
canonical JSON, its sha256 is the commit hash, and it is a blob too. **The datasets row is the commit point**: one
`BEGIN IMMEDIATE` transaction inserts every row (§5.2) and does no file I/O and no pandas. Readers only reach a blob
through a committed version row, so a crash leaves at most `pending` blobs (GC) or tmp files (swept).

Root safety (P01's note on `sync_version`): the writer **never changes the root of an existing datasets row**. A new
dataset gets a fresh id that is its own root, and every later version is a fresh row of that root, so the re-root path
that deletes a `dataset_roots` row is never taken for anything the writer made. `sync_version` refuses that delete
when the root row has lake rows hanging off it (`catalog/schema.py root_has_dependants`).
"""

from __future__ import annotations

import json
import math
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..catalog import schema as S
from . import duck
from . import manifest as M
from .store import BlobStore

#: ops whose result is the existing version when one of the root's live versions already has the same data_sha256
#: (today's datasets.DEDUPE_OPS, plus a pure carry). P06: an upload as the next version whose data (row ids, values,
#: keys) equals a version of the root returns that version; a new dataset's first upload still makes a version.
DEDUPE_OPS = ("shift_weights", "edit", "anomaly_review", "feature_lab", "carry", "upload_version")
#: P07: derive() ops are written by the writer too; their dedupe follows datasets.DEDUPE_OPS unless the caller says
ROW_ID = "__row_id"


class LakeError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    @property
    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


@dataclass
class _Part:
    sha256: str
    key: str
    role: str
    bytes: int
    n_rows: int
    row_id_min: int | None
    row_id_max: int | None
    kind: str = "table"      # blobs.kind
    tmp: Path | None = None  # our own bytes (re-put if a blob turned out to be purging, §7 R-S3)

    def manifest(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "key": self.key, "role": self.role, "bytes": self.bytes, "n_rows": self.n_rows,
                "row_id_min": self.row_id_min, "row_id_max": self.row_id_max}


@dataclass
class _Table:
    name: str
    change: str  # new | carried | appended | rewritten
    parts: list[_Part]
    schema: list[dict[str, Any]]
    n_rows: int
    from_version: str | None = None
    from_table: str | None = None
    source_file: str | None = None
    keys: list[dict[str, Any]] = field(default_factory=list)

    @property
    def row_id_min(self) -> int | None:
        v = [p.row_id_min for p in self.parts if p.row_id_min is not None]
        return min(v) if v else None

    @property
    def row_id_max(self) -> int | None:
        v = [p.row_id_max for p in self.parts if p.row_id_max is not None]
        return max(v) if v else None

    @property
    def sha256(self) -> str:
        return M.table_sha256([(p.role, p.sha256) for p in self.parts])

    def manifest(self) -> dict[str, Any]:
        return {"name": self.name, "table_sha256": self.sha256, "n_rows": self.n_rows, "n_cols": len(self.schema),
                "row_id": {"column": ROW_ID, "min": self.row_id_min, "max": self.row_id_max, "sorted": True},
                "schema": self.schema, "schema_sha256": M.schema_sha256(self.schema),
                "files": [p.manifest() for p in self.parts],
                "keys": [{k: x[k] for k in ("kind", "columns", "valid", "declared_by")} for x in self.keys]}


@dataclass
class _Split:
    id: str
    table: str
    split_key: str
    target: str | None
    strategy: str
    params: dict[str, Any]
    seed: int
    holdout_share: str
    n_folds: int
    part: _Part
    split_sha256: str
    n_rows: int
    n_holdout: int
    carried_from: str | None
    legacy_sha256: str | None


def _q(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _lit(p: Path | str) -> str:
    return "'" + str(p).replace("'", "''") + "'"


class VersionWriter:
    """Builds one new dataset version. Not thread-safe; one writer per job. Use as a context manager (or call
    close()) so its DuckDB connection and tmp directory go away even when nothing is committed."""

    def __init__(self, db, store: BlobStore, *, op: str, by: str, root_id: str | None = None, parent_id: str | None = None,
                 reason: str = "", job_id: str | None = None, name: str | None = None, project_id: str | None = None,
                 primary_table: str | None = None, ops: list[dict[str, Any]] | None = None,
                 sources: list[dict[str, Any]] | None = None, warnings: list[dict[str, Any]] | None = None,
                 duck_kw: dict[str, Any] | None = None, dedupe: bool | None = None) -> None:
        self.db, self.store = db, store
        #: P07: dedupe by data_sha256 (None = when `op` is in DEDUPE_OPS); derive() callers can turn it on or off
        self.dedupe = (op in DEDUPE_OPS) if dedupe is None else bool(dedupe)
        self.op, self.by, self.reason, self.job_id = op, (by or "system"), reason or "", job_id
        self.name, self.project_id, self.primary_table = name, project_id, primary_table
        self.extra_ops, self.sources, self.warnings = list(ops or []), list(sources or []), list(warnings or [])
        self.writer_id = "w_" + secrets.token_hex(8)
        self.tables: dict[str, _Table] = {}
        self.relations: list[dict[str, Any]] = []
        self.splits: list[_Split] = []
        self.op_log: list[dict[str, Any]] = []
        self._closed = False
        with db.connect() as con:
            if parent_id:
                p = con.execute("SELECT id, root_id, storage, manifest_sha256, project_id, name, state FROM datasets WHERE id = ?",
                                (parent_id,)).fetchone()
                if not p:
                    raise LakeError(404, "parent_not_found", f"No dataset version {parent_id}.")
                proot = p["root_id"] or p["id"]
                if root_id and root_id != proot:
                    raise LakeError(400, "parent_not_in_root", f"{parent_id} belongs to {proot}, not {root_id}.")
                root_id = proot
                self.parent = dict(p)
            elif root_id:
                r = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (root_id,)).fetchone()
                if not r and not con.execute("SELECT 1 FROM datasets WHERE id = ?", (root_id,)).fetchone():
                    raise LakeError(404, "root_not_found", f"No dataset {root_id}.")
                pid = (r["default_version_id"] if r else None) or root_id
                self.parent = dict(con.execute("SELECT id, root_id, storage, manifest_sha256, project_id, name, state FROM datasets"
                                               " WHERE id = ?", (pid,)).fetchone())
            else:
                self.parent = None
                if not name:
                    raise LakeError(400, "name_required", "A new dataset needs a name.")
                if project_id and not con.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                    raise LakeError(404, "project_not_found", f"No project {project_id}.")
        self.root_id = root_id
        self.tmp = store.tmp_dir(self.writer_id)
        self.con = duck.connect("job", **(duck_kw or {}))

    # -- lifecycle ------------------------------------------------------------------------------------------------

    def __enter__(self) -> "VersionWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            spill = self.con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
            self.con.close()
            if spill and Path(spill).name.startswith("job-"):
                shutil.rmtree(spill, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
        remove = getattr(self.store, "remove_tmp", None)
        if remove:
            remove(self.tmp)

    # -- table operations -------------------------------------------------------------------------------------------

    def _parent_table(self, table: str, from_version: str | None = None) -> _Table:
        src = from_version or (self.parent or {}).get("id")
        if not src:
            raise LakeError(400, "no_parent", f"Nothing to carry {table!r} from: this is a new dataset.")
        with self.db.connect() as con:
            v = con.execute("SELECT storage FROM datasets WHERE id = ?", (src,)).fetchone()
            if not v:
                raise LakeError(404, "version_not_found", f"No dataset version {src}.")
            if v["storage"] != "parquet":
                raise LakeError(409, "not_parquet", f"{src} is a CSV-era version; it can be carried once it is migrated (P10).")
            t = con.execute("SELECT * FROM version_tables WHERE version_id = ? AND table_name = ?", (src, table)).fetchone()
            if not t:
                raise LakeError(404, "table_not_found", f"{src} has no table {table!r}.")
            parts = [_Part(r["blob_sha256"], r["key"], r["role"], r["bytes"], r["n_rows"], r["row_id_min"], r["row_id_max"])
                     for r in con.execute("SELECT f.*, b.key, b.bytes FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256"
                                          " WHERE f.version_id = ? AND f.table_name = ? ORDER BY f.part", (src, table))]
            keys = [{"kind": k["kind"], "columns": json.loads(k["columns_json"]), "valid": bool(k["valid"]),
                     "declared_by": k["declared_by"], "n_duplicates": k["n_duplicates"], "n_nulls": k["n_nulls"]}
                    for k in con.execute("SELECT * FROM table_keys WHERE version_id = ? AND table_name = ? ORDER BY kind, columns_json",
                                         (src, table))]
        return _Table(table, "carried", parts, json.loads(t["schema_json"]), t["n_rows"], from_version=src, from_table=table,
                      keys=keys)

    def _check_name(self, table: str) -> None:
        if not table or table.startswith("__") or len(table) > 128 or "/" in table:
            raise LakeError(400, "bad_table_name", f"Bad table name {table!r}.")
        if table in self.tables:
            raise LakeError(400, "table_twice", f"Table {table!r} is already in this version.")

    def carry(self, table: str, from_version: str | None = None, as_name: str | None = None) -> dict[str, Any]:
        """Reuse a table of the parent (or `from_version`) by reference: zero bytes written."""
        name = as_name or table
        self._check_name(name)
        t = self._parent_table(table, from_version)
        t.name = name
        self.tables[name] = t
        self.op_log.append({"op": "carry", "table": name, "from_version": t.from_version, "from_table": table})
        return t.manifest()

    def _schema_of(self, rel_sql: str, params: list[Any] | None) -> list[tuple[str, str]]:
        cols = [(r[0], r[1]) for r in self.con.execute(f"DESCRIBE {rel_sql}", params or []).fetchall()]
        if not cols or cols[0][0] != ROW_ID:
            raise LakeError(400, "row_id_first", f"The first column must be {ROW_ID} (got {cols[0][0] if cols else 'none'}).")
        if cols[0][1] != "BIGINT":
            raise LakeError(400, "row_id_type", f"{ROW_ID} must be BIGINT (got {cols[0][1]}).")
        seen: set[str] = set()
        for c, _ in cols[1:]:
            if c.startswith("__"):
                raise LakeError(400, "reserved_column", f"Column {c!r}: names starting with '__' are reserved.")
            if c.lower() in seen:
                raise LakeError(400, "duplicate_column", f"Column {c!r} appears twice.")
            seen.add(c.lower())
        return cols

    def _copy_parts(self, table: str, rel_sql: str, params: list[Any] | None, role: str,
                    reasons: dict[str, str] | None) -> tuple[list[_Part], list[dict[str, Any]], int]:
        """COPY the relation sorted by __row_id into tmp, check row ids, cut it into parts of ≤ PART_MAX_ROWS, hash each
        part and put it in the store (blob rows 'pending'). Returns (parts, schema, n_rows)."""
        cols = self._schema_of(rel_sql, params)
        rg = config.ROW_GROUP_ROWS_WIDE if len(cols) > config.WIDE_COLS else config.ROW_GROUP_ROWS
        opts = (f"FORMAT parquet, COMPRESSION {config.PARQUET_COMPRESSION}, COMPRESSION_LEVEL {config.PARQUET_LEVEL}, "
                f"ROW_GROUP_SIZE {rg}")
        staged = self.tmp / f"{secrets.token_hex(4)}.staged.parquet"
        self.con.execute(f"COPY (SELECT * FROM ({rel_sql}) ORDER BY {ROW_ID}) TO {_lit(staged)} ({opts})", params or [])
        src = f"read_parquet({_lit(staged)})"
        counts = ", ".join(f"count({_q(c)})" for c, _ in cols[1:])
        row = self.con.execute(f"SELECT count(*), count({ROW_ID}), count(DISTINCT {ROW_ID}), min({ROW_ID}), max({ROW_ID})"
                               f"{', ' + counts if counts else ''} FROM {src}").fetchone()
        n, n_id, n_distinct, lo, hi = row[:5]
        if n_id != n:
            raise LakeError(400, "row_id_null", f"{n - n_id} rows have no {ROW_ID}.")
        if n_distinct != n:
            raise LakeError(400, "row_id_duplicate", f"{n - n_distinct} {ROW_ID} values repeat in table {table!r}.")
        reasons = reasons or {}
        schema = []
        for (c, typ), nn in zip(cols[1:], row[5:]):
            e = {"name": c, "type": typ, "nullable": bool(nn < n)}
            if reasons.get(c):
                e["reason"] = str(reasons[c])
            schema.append(e)
        n_parts = max(1, math.ceil(n / config.PART_MAX_ROWS))
        files: list[Path] = []
        if n_parts == 1:
            files.append(staged)
        else:
            per = math.ceil(n / n_parts)
            for k in range(n_parts):
                f = self.tmp / f"{secrets.token_hex(4)}.part{k}.parquet"
                self.con.execute(f"COPY (SELECT * EXCLUDE (file_row_number) FROM read_parquet({_lit(staged)}, file_row_number=true)"
                                 f" WHERE file_row_number >= {k * per} AND file_row_number < {(k + 1) * per} ORDER BY {ROW_ID})"
                                 f" TO {_lit(f)} ({opts})")
                files.append(f)
            staged.unlink()
        parts = []
        for f in files:
            pn, plo, phi = self.con.execute(f"SELECT count(*), min({ROW_ID}), max({ROW_ID}) FROM read_parquet({_lit(f)})").fetchone()
            parts.append(self._put(f, "parquet", "table", role, pn, plo, phi))
        return parts, schema, int(n)

    def _put(self, f: Path, ext: str, kind: str, role: str, n_rows: int | None, lo: int | None, hi: int | None) -> _Part:
        sha, nbytes = M.file_sha256(f)
        key = self.store.put_file(f, sha, ext)
        with self.db.connect() as con:
            con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)"
                        " ON CONFLICT (sha256) DO NOTHING", (sha, key, kind, nbytes, n_rows, S.now()))
        return _Part(sha, key, role, nbytes, int(n_rows or 0), lo, hi, kind=kind, tmp=f)

    def write(self, table: str, rel_sql: str, params: list[Any] | None = None, *, reasons: dict[str, str] | None = None,
              source_file: str | None = None) -> dict[str, Any]:
        """A whole table from a DuckDB relation (`SELECT __row_id, … FROM …`). 'rewritten' when the parent has it."""
        self._check_name(table)
        parts, schema, n = self._copy_parts(table, rel_sql, params, "base", reasons)
        had = False
        if self.parent and self.parent.get("storage") == "parquet":
            with self.db.connect() as con:
                had = bool(con.execute("SELECT 1 FROM version_tables WHERE version_id = ? AND table_name = ?",
                                       (self.parent["id"], table)).fetchone())
        t = _Table(table, "rewritten" if had else "new", parts, schema, n, source_file=source_file,
                   from_version=self.parent["id"] if had else None, from_table=table if had else None)
        self.tables[table] = t
        self.op_log.append({"op": "write", "table": table, "change": t.change, "n_rows": n})
        return t.manifest()

    def write_arrow(self, table: str, arrow_table: Any, **kw: Any) -> dict[str, Any]:
        """A table handed over as Arrow (Data Doctor / Feature Lab transforms), __row_id first."""
        view = "_arrow_" + secrets.token_hex(4)
        self.con.register(view, arrow_table)
        try:
            return self.write(table, f"SELECT * FROM {view}", **kw)
        finally:
            self.con.unregister(view)

    def append(self, table: str, rel_sql: str, params: list[Any] | None = None, **kw: Any) -> dict[str, Any]:
        """The parent's table plus new rows as an 'append' part (insert-only delta). New __row_id must be above the
        parent's, and the columns must be the same."""
        self._check_name(table)
        base = self._parent_table(table)
        parts, schema, n = self._copy_parts(table, rel_sql, params, "append", kw.get("reasons"))
        strip = [{k: c[k] for k in ("name", "type")} for c in schema]
        if strip != [{k: c[k] for k in ("name", "type")} for c in base.schema]:
            raise LakeError(400, "append_schema", f"The new rows of {table!r} must have the parent's columns and types.")
        if base.row_id_max is not None and min(p.row_id_min for p in parts) <= base.row_id_max:
            raise LakeError(400, "append_row_ids", f"New {ROW_ID} values must be above {base.row_id_max}.")
        schema = [{**b, "nullable": b["nullable"] or s["nullable"]} for b, s in zip(base.schema, schema)]
        t = _Table(table, "appended", base.parts + parts, schema, base.n_rows + n, from_version=base.from_version,
                   from_table=table, source_file=kw.get("source_file"))
        self.tables[table] = t
        self.op_log.append({"op": "append", "table": table, "n_rows_added": n, "from_version": base.from_version})
        return t.manifest()

    # -- keys, relations, splits ------------------------------------------------------------------------------------

    def _rel(self, table: str) -> str:
        t = self.tables.get(table)
        if not t:
            raise LakeError(400, "unknown_table", f"No table {table!r} in this version.")
        return "read_parquet([" + ",".join(_lit(self.store.uri(p.key)) for p in t.parts) + "])"

    def key(self, table: str, columns: list[str], *, kind: str = "primary", declared_by: str | None = None) -> dict[str, Any]:
        """Declare a key (a person's decision, never a guess): checked for duplicates and nulls now."""
        if kind not in ("primary", "unique") or not columns:
            raise LakeError(400, "bad_key", "A key is primary or unique and names at least one column.")
        t = self.tables.get(table) or self._parent_table(table)
        names = {c["name"] for c in t.schema}
        miss = [c for c in columns if c not in names]
        if miss:
            raise LakeError(400, "key_column_missing", f"{table!r} has no column(s) {miss}.")
        cs = ", ".join(_q(c) for c in columns)
        nulls = " OR ".join(f"{_q(c)} IS NULL" for c in columns)
        n, n_dist, n_null = self.con.execute(f"SELECT count(*), count(DISTINCT ({cs})), count(*) FILTER (WHERE {nulls})"
                                             f" FROM {self._rel(table)}").fetchone()
        k = {"kind": kind, "columns": list(columns), "valid": n_dist == n and n_null == 0,
             "declared_by": declared_by or self.by, "n_duplicates": int(n - n_dist), "n_nulls": int(n_null)}
        t.keys = [x for x in t.keys if not (x["kind"] == kind and x["columns"] == k["columns"])] + [k]
        return k

    def relation(self, name: str, from_table: str, from_cols: list[str], to_table: str, to_cols: list[str], *,
                 cardinality: str = "n:1", declared_by: str | None = None) -> dict[str, Any]:
        if cardinality not in ("n:1", "1:1") or len(from_cols) != len(to_cols) or not from_cols:
            raise LakeError(400, "bad_relation", "A relation maps the same number of columns, n:1 or 1:1.")
        on = " AND ".join(f"f.{_q(a)} = t.{_q(b)}" for a, b in zip(from_cols, to_cols))
        orphans = self.con.execute(f"SELECT count(*) FROM {self._rel(from_table)} f ANTI JOIN {self._rel(to_table)} t ON {on}"
                                   ).fetchone()[0]
        r = {"name": name, "from": {"table": from_table, "columns": list(from_cols)},
             "to": {"table": to_table, "columns": list(to_cols)}, "cardinality": cardinality, "n_orphans": int(orphans),
             "declared_by": declared_by or self.by}
        self.relations = [x for x in self.relations if x["name"] != name] + [r]
        return r

    def split(self, table: str, split_key: str, rel_sql: str, params: list[Any] | None = None, *, strategy: str,
              seed: int, holdout_share: str, n_folds: int, target: str | None = None,
              params_json: dict[str, Any] | None = None, carried_from: str | None = None,
              legacy_sha256: str | None = None) -> dict[str, Any]:
        """A split of one of this version's tables: a relation (__row_id BIGINT, fold) covering exactly the table's
        rows. Written as a sorted Parquet blob; split_sha256 is recomputed from the file. (Drawing and carrying the
        folds is P09's; this only stores what it is given.)"""
        if not isinstance(holdout_share, str):
            raise LakeError(400, "share_as_text", "holdout_share is decimal text (no floats in hashed data).")
        f = self.tmp / f"{secrets.token_hex(4)}.split.parquet"
        self.con.execute(f"COPY (SELECT {ROW_ID}::BIGINT AS {ROW_ID}, fold::TINYINT AS fold FROM ({rel_sql}) ORDER BY {ROW_ID})"
                         f" TO {_lit(f)} (FORMAT parquet, COMPRESSION zstd)", params or [])
        src = f"read_parquet({_lit(f)})"
        tbl = self._rel(table)
        extra, missing = self.con.execute(
            f"SELECT (SELECT count(*) FROM {src} s ANTI JOIN {tbl} t USING ({ROW_ID})),"
            f" (SELECT count(*) FROM {tbl} t ANTI JOIN {src} s USING ({ROW_ID}))").fetchone()
        if extra or missing:
            raise LakeError(400, "split_rows", f"The split must cover exactly the rows of {table!r}: {extra} unknown, {missing} missing.")
        sha, n = M.split_sha256_parquet(f)  # also refuses repeated or unsorted ids
        n_hold = self.con.execute(f"SELECT count(*) FROM {src} WHERE fold = -1").fetchone()[0]
        part = self._put(f, "parquet", "split", "base", n, None, None)
        s = _Split("spl_" + secrets.token_hex(6), table, split_key, target, strategy, dict(params_json or {}), int(seed),
                   holdout_share, int(n_folds), part, sha, n, int(n_hold), carried_from, legacy_sha256)
        M.canonical(s.params)  # params are hashed elsewhere: refuse floats now
        self.splits.append(s)
        return {"id": s.id, "split_sha256": sha, "blob_sha256": part.sha256, "n_rows": n, "n_holdout": s.n_holdout}

    # -- commit -------------------------------------------------------------------------------------------------------

    def _team_project(self, con: sqlite3.Connection) -> tuple[str, str]:
        if self.root_id:
            r = con.execute("SELECT team_id, project_id FROM dataset_roots WHERE id = ?", (self.root_id,)).fetchone()
            if r:
                return r["team_id"], r["project_id"]
            d = con.execute("SELECT project_id FROM datasets WHERE id = ?", (self.root_id,)).fetchone()
            pid = (d["project_id"] if d else None) or S.project_id_for(self.root_id)
            return S.team_of_project(con, pid), pid
        pid = self.project_id or S.project_id_for(self._new_id)
        return S.team_of_project(con, pid), pid

    @staticmethod
    def _next_no(con: sqlite3.Connection, root: str) -> int:
        nmax = con.execute("SELECT COALESCE(MAX(version_no), 0) FROM datasets WHERE id = ? OR root_id = ?", (root, root)).fetchone()[0]
        nxt = con.execute("SELECT next_version_no FROM dataset_roots WHERE id = ?", (root,)).fetchone()
        return max(nmax + 1, nxt[0] if nxt else 1)

    def _manifest(self, version_id: str, root: str, version_no: int, team: str, project: str) -> tuple[dict, bytes, str]:
        if not self.tables:
            raise LakeError(400, "no_tables", "A version has at least one table.")
        primary = self.primary_table or next(iter(self.tables))
        if primary not in self.tables:
            raise LakeError(400, "primary_table", f"No table {primary!r} in this version.")
        data = {"primary_table": primary, "tables": [t.manifest() for t in self.tables.values()],
                "relations": [{k: r[k] for k in ("name", "from", "to", "cardinality", "n_orphans")} for r in self.relations]}
        parent = None
        if self.parent:
            parent = {"version_id": self.parent["id"], "manifest_sha256": self.parent.get("manifest_sha256")}
        m = {"format": M.FORMAT,
             "dataset": {"root_id": root, "version_id": version_id, "version_no": version_no, "team_id": team, "project_id": project},
             "data": data,
             "provenance": {"parent": parent, "other_parents": [], "op": self.op, "ops": (self.op_log + self.extra_ops)[:200],
                            "sources": self.sources, "checks": {"warnings": self.warnings}, "created_at": M.ts(),
                            "created_by": self.by, "reason": self.reason, "engine": M.engine_versions()},
             "legacy": None}
        return M.seal(m)

    def commit(self, *, make_default: bool | None = None, target: str | None = None, kind: str = "tabular",
               note: str = "") -> dict[str, Any]:
        """Write the manifest blob, then ONE transaction (§5.2). Returns {version_id, …, deduped}. Idempotent on job_id;
        returns the existing version when `op` is a dedupe op and the data is the same."""
        try:
            for attempt in range(4):
                out = self._try_commit(make_default=make_default, target=target, kind=kind, note=note, regone_ok=attempt == 0)
                if out is not None:
                    if not out.get("deduped"):  # P08: the profile job for the tables this commit wrote (background)
                        from . import stats

                        stats.after_commit(out["version_id"])
                    return out
            raise LakeError(409, "commit_contended", "Other versions of this dataset kept being committed; try again.")
        finally:
            self.close()

    def _try_commit(self, *, make_default: bool | None, target: str | None, kind: str, note: str,
                    regone_ok: bool) -> dict[str, Any] | None:
        self._new_id = getattr(self, "_new_id", None) or self._fresh_id()
        version_id = self._new_id
        root = self.root_id or version_id
        # outside the transaction: predict the number, build and store the manifest blob
        with self.db.connect() as con:
            team, project = self._team_project(con)
            predicted = self._next_no(con, root) if self.root_id else 1
            before_default = None
            if self.root_id:
                r = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
                before_default = r[0] if r else None
        m, mbytes, msha = self._manifest(version_id, root, predicted, team, project)
        early = self._existing(m["data_sha256"], root)  # read-only: skip the manifest blob on a re-run or a dedupe
        if early:
            return self._result(early[0], deduped=early[1])
        mf = self.tmp / f"{msha}.json"
        mf.write_bytes(mbytes)
        mpart = self._put(mf, "json", "manifest", "manifest", None, None, None)
        parts = [p for t in self.tables.values() for p in t.parts]
        blob_shas = sorted({p.sha256 for p in parts} | {mpart.sha256} | {s.part.sha256 for s in self.splits})
        dsha = m["data_sha256"]
        prim = self.tables[m["data"]["primary_table"]]
        total_bytes = sum(p.bytes for p in {p.sha256: p for p in parts}.values())
        n_cols = len(prim.schema)
        n_classes = 0  # the legacy profile shape (GET /datasets): distinct target values, 0 without a target
        if target and any(c["name"] == target for c in prim.schema):
            n_classes = int(self.con.execute(f"SELECT count(DISTINCT {_q(target)}) FROM {self._rel(prim.name)}").fetchone()[0])

        with self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # idempotent re-run of the same job (a lease that expired, B §9)
            if self.job_id:
                r = con.execute("SELECT id FROM datasets WHERE job_id = ?", (self.job_id,)).fetchone()
                if r:
                    con.rollback()
                    return self._result(r[0], deduped="job")
            if self.root_id:
                S.ensure_root(con, root)  # a CSV-era root that predates its dataset_roots row
                rr = con.execute("SELECT state FROM dataset_roots WHERE id = ?", (root,)).fetchone()
                if not rr:
                    con.rollback()
                    raise LakeError(404, "root_not_found", f"No dataset {root}.")
                if rr["state"] == "deleted":
                    con.rollback()
                    raise LakeError(409, "root_deleted", f"Dataset {root} is deleted.")
                if self.dedupe:
                    r = con.execute("SELECT id FROM datasets WHERE (id = ? OR root_id = ?) AND data_sha256 = ? AND storage = 'parquet'"
                                    " AND state IS NOT 'deleted' ORDER BY version_no LIMIT 1", (root, root, dsha)).fetchone()
                    if r:
                        con.rollback()
                        return self._result(r[0], deduped="data")
                n = con.execute("UPDATE dataset_roots SET next_version_no = ? WHERE id = ? RETURNING next_version_no - 1",
                                (self._next_no(con, root) + 1, root)).fetchone()[0]
                if n != predicted:  # another commit took the number the manifest names: rebuild it
                    con.rollback()
                    return None
            else:
                if con.execute("SELECT 1 FROM datasets WHERE id = ?", (version_id,)).fetchone():
                    con.rollback()
                    self._new_id = None
                    return None
                if self.project_id is None:
                    S.ensure_project(con, version_id, self.name or version_id)
                con.execute("INSERT INTO dataset_roots (id, team_id, project_id, name, default_version_id, next_version_no, state,"
                            " created_at, created_by) VALUES (?, ?, ?, ?, NULL, 2, 'active', ?, ?)",
                            (root, team, project, S.free_root_name(con, project, self.name, root), S.now(), self.by))
                n = 1
            name = self.name or (self.parent or {}).get("name") or root
            profile = {"n_cols": n_cols, "n_classes": n_classes, "storage": "parquet", "tables": list(self.tables),
                       "columns": [{"name": c["name"], "dtype": c["type"], "nullable": c["nullable"]} for c in prim.schema]}
            con.execute(
                "INSERT INTO datasets (id, name, filename, source, note, created_at, size_bytes, rows, target, kind, profile,"
                " project_id, parent_id, root_id, sha256, n_rows, op, created_by, team_id, version_no, storage, manifest_sha256,"
                " data_sha256, legacy_sha256, n_tables, n_cols, primary_table, reason, job_id)"
                " VALUES (?, ?, ?, 'upload', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'parquet', ?, ?, NULL, ?, ?, ?, ?, ?)",
                (version_id, name, f"{prim.name}.parquet", note or self.reason, S.now(), total_bytes, prim.n_rows, target, kind,
                 json.dumps(profile), project, (self.parent or {}).get("id"), root, dsha, prim.n_rows, self.op, self.by, team, n,
                 msha, dsha, len(self.tables), n_cols, prim.name, self.reason, self.job_id))
            now = S.now()
            for i, t in enumerate(self.tables.values()):
                con.execute("INSERT INTO version_tables (version_id, table_name, ordinal, table_sha256, n_rows, n_cols, bytes,"
                            " schema_json, schema_sha256, row_id_min, row_id_max, row_id_sorted, change, from_version, from_table,"
                            " source_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                            (version_id, t.name, i, t.sha256, t.n_rows, len(t.schema), sum(p.bytes for p in t.parts),
                             json.dumps(t.schema), M.schema_sha256(t.schema), t.row_id_min, t.row_id_max, t.change,
                             t.from_version, t.from_table, t.source_file))
                for j, p in enumerate(t.parts):
                    con.execute("INSERT INTO table_files (version_id, table_name, part, blob_sha256, role, n_rows, row_id_min,"
                                " row_id_max) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (version_id, t.name, j, p.sha256, p.role, p.n_rows, p.row_id_min, p.row_id_max))
                    con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                                " VALUES (?, 'version', ?, ?, ?, ?)", (p.sha256, version_id, f"table:{t.name}#{j}", team, now))
                for k in t.keys:
                    con.execute("INSERT INTO table_keys (version_id, table_name, columns_json, kind, declared_by, valid, n_duplicates,"
                                " n_nulls) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                (version_id, t.name, json.dumps(k["columns"]), k["kind"], k["declared_by"], int(k["valid"]),
                                 k.get("n_duplicates", 0), k.get("n_nulls", 0)))
                if t.row_id_max is not None:
                    con.execute("INSERT INTO dataset_root_tables (root_id, table_name, next_row_id) VALUES (?, ?, ?)"
                                " ON CONFLICT (root_id, table_name) DO UPDATE SET next_row_id = MAX(next_row_id, excluded.next_row_id)",
                                (root, t.name, t.row_id_max + 1))
            for r in self.relations:
                con.execute("INSERT INTO table_relations (version_id, name, from_table, from_cols_json, to_table, to_cols_json,"
                            " cardinality, declared_by, n_orphans) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (version_id, r["name"], r["from"]["table"], json.dumps(r["from"]["columns"]), r["to"]["table"],
                             json.dumps(r["to"]["columns"]), r["cardinality"], r["declared_by"], r["n_orphans"]))
            con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                        " VALUES (?, 'version', ?, 'manifest', ?, ?)", (msha, version_id, team, now))
            for s in self.splits:
                con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed,"
                            " holdout_share, n_folds, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from,"
                            " created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (s.id, version_id, s.table, s.split_key, s.target, s.strategy, json.dumps(s.params), s.seed,
                             s.holdout_share, s.n_folds, s.part.sha256, s.split_sha256, s.legacy_sha256, s.n_rows, s.n_holdout,
                             s.carried_from, now, self.by))
                con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                            " VALUES (?, 'split', ?, 'split', ?, ?)", (s.part.sha256, s.id, team, now))
            # every referenced blob must still be there: GC may have marked an unreferenced one (§7 R-S3)
            q = ",".join("?" * len(blob_shas))
            con.execute(f"UPDATE blobs SET state = 'live' WHERE sha256 IN ({q}) AND state IN ('pending', 'live')", blob_shas)
            gone = [dict(r) for r in con.execute(f"SELECT sha256, state FROM blobs WHERE sha256 IN ({q}) AND state <> 'live'", blob_shas)]
            have = {r[0] for r in con.execute(f"SELECT sha256 FROM blobs WHERE sha256 IN ({q})", blob_shas)}
            gone += [{"sha256": s_, "state": "missing"} for s_ in blob_shas if s_ not in have]
            if gone:
                con.rollback()
                self._reput(gone, parts + [mpart] + [s.part for s in self.splits], regone_ok)
                return None
            if self.parent:
                S.add_edge(con, ("dataset", self.parent["id"]), ("dataset", version_id), "derived_from", actor=self.by,
                           meta={"op": self.op, "storage": "parquet"})
            details = {"dataset_id": version_id, "root_id": root, "parent": (self.parent or {}).get("id"), "op": self.op,
                       "rows": prim.n_rows, "sha256": dsha, "storage": "parquet", "version_no": n, "manifest_sha256": msha,
                       "data_sha256": dsha, "tables": {t.name: {"change": t.change, "table_sha256": t.sha256}
                                                       for t in self.tables.values()}, "job_id": self.job_id}
            S.audit(con, self.by, "dataset_version_created" if self.root_id else "dataset_created", details, comment=self.reason)
            for s in self.splits:  # P09: everything needed to rebuild the row (restore-splits), not only the hashes
                S.audit(con, self.by, "split_created", {"dataset_id": version_id, "root_id": root, "split_id": s.id,
                                                        "split_sha256": s.split_sha256, "blob_sha256": s.part.sha256,
                                                        "table": s.table, "split_key": s.split_key, "target": s.target,
                                                        "strategy": s.strategy, "params_json": json.dumps(s.params),
                                                        "seed": s.seed, "holdout_share": s.holdout_share,
                                                        "n_folds": s.n_folds, "n_rows": s.n_rows, "n_holdout": s.n_holdout,
                                                        "carried_from": s.carried_from, "legacy_sha256": s.legacy_sha256,
                                                        "basis": "committed with the version"})
            con.commit()
        default = self._settle_default(version_id, root, before_default, make_default)
        return self._result(version_id, deduped=None, default=default)

    def committed_for_job(self) -> str | None:
        """The version this writer's job_id already committed (check before doing the work of a re-run)."""
        if not self.job_id:
            return None
        with self.db.connect() as con:
            r = con.execute("SELECT id FROM datasets WHERE job_id = ?", (self.job_id,)).fetchone()
        return r[0] if r else None

    def _existing(self, dsha: str, root: str) -> tuple[str, str] | None:
        """(version id, 'job' | 'data') when the commit would return an existing version. The same checks run again
        inside the transaction, which is what decides; this one only avoids writing a manifest blob for nothing."""
        j = self.committed_for_job()
        if j:
            return j, "job"
        if self.root_id and self.dedupe:
            with self.db.connect() as con:
                r = con.execute("SELECT id FROM datasets WHERE (id = ? OR root_id = ?) AND data_sha256 = ? AND storage = 'parquet'"
                                " AND state IS NOT 'deleted' ORDER BY version_no LIMIT 1", (root, root, dsha)).fetchone()
            if r:
                return r[0], "data"
        return None

    def _reput(self, gone: list[dict[str, Any]], parts: list[_Part], allowed: bool) -> None:
        """A referenced blob is purging / purged / missing: put our own bytes back and retry once; a carried part whose
        bytes we do not hold cannot be restored (409)."""
        mine = {p.sha256: p for p in parts if p.tmp is not None and p.tmp.exists()}
        lost = [g for g in gone if g["sha256"] not in mine]
        if lost or not allowed:
            raise LakeError(409, "blob_gone", "Some files this version needs were purged or are being garbage-collected.",
                            blobs=[{"sha256": g["sha256"], "state": g["state"]} for g in gone])
        for g in gone:
            p = mine[g["sha256"]]
            self.store.put_file(p.tmp, p.sha256, p.key.rsplit(".", 1)[1])
            with self.db.connect() as con:
                con.execute("UPDATE blobs SET state = 'pending', purged_at = NULL, purged_by = NULL, purge_reason = NULL"
                            " WHERE sha256 = ? AND state IN ('purging', 'purged')", (p.sha256,))
                con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)"
                            " ON CONFLICT (sha256) DO NOTHING", (p.sha256, p.key, p.kind, p.bytes, p.n_rows, S.now()))

    def _settle_default(self, version_id: str, root: str, before: str | None, make_default: bool | None) -> dict[str, Any]:
        """After the commit: compare-and-set dataset_roots.default_version_id (and the dataset_cards copy). A new
        dataset's first version is its default; a later one only when asked (or, by default, with no warnings)."""
        want = make_default if make_default is not None else not self.warnings
        if not want:
            return {"is_default": False, "default_stays": before}
        with self.db.connect() as con:
            n = con.execute("UPDATE dataset_roots SET default_version_id = ?, updated_at = ?, updated_by = ?"
                            " WHERE id = ? AND default_version_id IS ?", (version_id, S.now(), self.by, root, before)).rowcount
            if not n:
                cur = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
                return {"is_default": False, "why": "the default changed while this version was written", "default_stays": cur[0] if cur else None}
            con.execute("INSERT OR IGNORE INTO dataset_cards (root_id) VALUES (?)", (root,))
            con.execute("UPDATE dataset_cards SET default_version_id = ?, updated_at = ?, updated_by = ? WHERE root_id = ?",
                        (version_id, S.now(), self.by, root))
            S.audit(con, self.by, "dataset_default_set", {"dataset_id": version_id, "root_id": root, "from": before,
                                                          "to": version_id, "auto": True, "data_changed": False})
        return {"is_default": True}

    def _fresh_id(self) -> str:
        return "ds_" + secrets.token_hex(4)

    def _result(self, version_id: str, *, deduped: str | None, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.db.connect() as con:
            r = dict(con.execute("SELECT id, root_id, version_no, storage, manifest_sha256, data_sha256, n_tables, n_rows, n_cols,"
                                 " primary_table, team_id, project_id, op, created_by, created_at FROM datasets WHERE id = ?",
                                 (version_id,)).fetchone())
            r["tables"] = [dict(t) for t in con.execute("SELECT table_name, table_sha256, n_rows, n_cols, bytes, change FROM"
                                                        " version_tables WHERE version_id = ? ORDER BY ordinal", (version_id,))]
            r["splits"] = [dict(s) for s in con.execute("SELECT id, table_name, split_key, split_sha256, blob_sha256 FROM splits"
                                                        " WHERE version_id = ?", (version_id,))]
        return {**r, "version_id": version_id, "deduped": deduped, **({"default": default} if default else {})}


def for_app(app, **kw: Any) -> VersionWriter:
    return VersionWriter(app.state.db, app.state.lake, **kw)
