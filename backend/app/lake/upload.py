"""P05 (plan §5.1, R10): staged uploads, typed import, and the commit of a new single-table Parquet dataset.

    POST /uploads            stage(): every file part streams to lake/tmp/<up>/<n>.src in 8 MB chunks, sha256 and a
                             UTF-8 check on the fly. Nothing is held in memory. Cap ENSEMBLE_MAX_UPLOAD_GB (5); the
                             free-disk guard wants 3x the size.
    PATCH /uploads/{u}       the plan: {name, target, project_id, types: {column: TYPE}} (declared types win).
    POST /uploads/{u}/check  check(): detect, type (§5.1 step 3), write tmp/<up>/typed.parquet, return the decided
                             schema with a reason per column, warnings and blocks; stores check_sha256.
    POST /uploads/{u}/commit commit(): the VersionWriter (job_id = the upload id), so a repeat returns the same version.

Typing rules (A §2.6 / §5.1): DuckDB `sniff_csv` proposes a type per column; one `all_varchar` scan then counts, per
column, the values with a leading zero, the values that do not cast to the proposed type, and the day/month evidence
of slash dates. A leading zero → VARCHAR ("leading zeros"); a value that does not cast → VARCHAR plus a warning with
5 examples; dd/mm vs mm/dd that no value settles → VARCHAR plus "declare the format". NA tokens come from the team
policy (`na_tokens`, §6.4). The typed file is written with the same cast expressions the scan counted, so a value
that passed the count cannot fail the write.
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from ..catalog import schema as S
from . import duck
from .writer import ROW_ID, LakeError, VersionWriter, _lit, _q, is_reserved_name, reserve_row_ids

log = logging.getLogger("ensemble.upload")

CHUNK = 8 * 1024 * 1024
DEFAULT_NA = ["", "NA", "N/A", "NULL", "null", "NaN", "-"]
STAGING_DAYS = 7
PRIMARY = "data"  # the table name when the file name gives none
TEXT_EXT = (".csv", ".tsv", ".txt")
INT_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}
FLOAT_TYPES = {"FLOAT", "DOUBLE", "REAL"}
DECLARABLE = ("VARCHAR", "BIGINT", "DOUBLE", "BOOLEAN", "DATE", "TIMESTAMP")
LEADING_ZERO_RE = r"^\s*[-+]?0[0-9]"
SLASH_DATE_RE = r"^\s*([0-9]{1,2})[/.-]([0-9]{1,2})[/.-]([0-9]{2,4})\s*$"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _duck_kw() -> dict[str, int]:
    """An upload is the one job a person waits on: all cores, and a memory limit that keeps the API under 3 GB RSS
    at 10M rows (ENSEMBLE_UPLOAD_DUCK_MB, default 1536)."""
    try:
        mb = int(os.environ.get("ENSEMBLE_UPLOAD_DUCK_MB", "1536"))
    except ValueError:
        mb = 1536
    return {"threads": max(2, os.cpu_count() or 2), "mem_mb": max(256, mb)}


def max_upload_bytes() -> int:
    try:
        gb = float(os.environ.get("ENSEMBLE_MAX_UPLOAD_GB", "5"))
    except ValueError:
        gb = 5.0
    return int(max(1e-6, gb) * 1024 ** 3)


def _lock(up_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(up_id, threading.Lock())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


# --------------------------------------------------------------------------- staging (streamed, hashed)


class SinkFile:
    """One file part being received: written to <tmp>/<n>.src as it arrives, sha256 and a UTF-8 check on the fly."""

    def __init__(self, path: Path, filename: str, n: int, cap: int) -> None:
        self.path, self.filename, self.n, self.cap = path, filename, n, cap
        self.f: BinaryIO = open(path, "wb", buffering=CHUNK)
        self.h = hashlib.sha256()
        self.bytes = 0
        self.utf8 = codecs.getincrementaldecoder("utf-8")()
        self.is_utf8 = True
        self.head = b""

    def write(self, data: bytes) -> None:
        if not data:
            return
        self.bytes += len(data)
        if self.bytes > self.cap:
            raise LakeError(413, "too_large", f"{self.filename or 'The file'} is larger than {self.cap / 1024 ** 3:g} GB "
                                              "(ENSEMBLE_MAX_UPLOAD_GB).")
        self.f.write(data)
        self.h.update(data)
        if len(self.head) < 65536:
            self.head += data[:65536 - len(self.head)]
        if self.is_utf8:
            try:
                self.utf8.decode(data)
            except UnicodeDecodeError:
                self.is_utf8 = False

    def close(self) -> dict[str, Any]:
        self.f.close()
        if self.is_utf8:
            try:
                self.utf8.decode(b"", final=True)
            except UnicodeDecodeError:
                self.is_utf8 = False
        return {"n": self.n, "filename": self.filename, "bytes": self.bytes, "sha256": self.h.hexdigest(),
                "utf8": self.is_utf8, "src": self.path.name}


def disk_guard(where: Path, need: int) -> None:
    free = shutil.disk_usage(where).free
    if need and free < 3 * need:
        raise LakeError(507, "disk_full", f"Not enough free disk for this upload: it needs 3x its size "
                                          f"({3 * need / 1024 ** 3:.2f} GB), {free / 1024 ** 3:.2f} GB is free.")


def new_upload(db, store, *, by: str, project_id: str | None = None, plan: dict[str, Any] | None = None) -> tuple[str, Path]:
    """A staging row (status 'staged', no files yet) and its tmp directory."""
    up = "up_" + secrets.token_hex(6)
    try:
        sweep_expired(db, store)  # staged uploads past their expiry: their tmp files go (cheap: one indexed query)
    except Exception:  # noqa: BLE001
        pass
    with db.connect() as con:
        if project_id and not con.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
            raise LakeError(404, "project_not_found", f"No project '{project_id}'.")
        team = S.team_of_project(con, project_id)
        now = _now()
        con.execute("INSERT INTO uploads (id, team_id, project_id, status, files_json, plan_json, created_by, created_at, expires_at)"
                    " VALUES (?, ?, ?, 'staged', '[]', ?, ?, ?, ?)",
                    (up, team, project_id or "", json.dumps({**(plan or {}), **({"project_id": project_id} if project_id else {})}),
                     by, _iso(now), _iso(now + timedelta(days=STAGING_DAYS))))
        if (plan or {}).get("parent_version_id"):
            _set_parent_cols(con, up, plan["parent_version_id"])
    return up, store.tmp_dir(up)


def add_files(db, up: str, files: list[dict[str, Any]]) -> None:
    with db.connect() as con:
        con.execute("UPDATE uploads SET files_json = ? WHERE id = ?", (json.dumps(files), up))


def get(db, up: str) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT * FROM uploads WHERE id = ?", (up,)).fetchone()
    if not r:
        raise LakeError(404, "upload_not_found", f"No upload {up}.")
    d = dict(r)
    for k in ("files_json", "plan_json", "check_json"):
        d[k.removesuffix("_json")] = json.loads(d.pop(k)) if d.get(k) else None
    return d


def public(u: dict[str, Any], *, full: bool = True) -> dict[str, Any]:
    out = {"upload_id": u["id"], "status": u["status"], "team_id": u["team_id"], "project_id": u["project_id"] or None,
           "files": [{k: f[k] for k in ("n", "filename", "bytes", "sha256") if k in f} for f in u["files"] or []],
           "parent_version_id": u.get("parent_version_id"), "root_id": u.get("root_id"),
           "plan": u["plan"] or {}, "check_sha256": u.get("check_sha256"), "committed_version_id": u.get("committed_version_id"),
           "created_by": u["created_by"], "created_at": u["created_at"], "expires_at": u["expires_at"]}
    if full and u.get("check"):
        out["check"] = u["check"]
    return out


def stage_stream(db, store, *, by: str, filename: str, chunks, project_id: str | None = None,
                 plan: dict[str, Any] | None = None, size_hint: int | None = None) -> dict[str, Any]:
    """A whole upload from one iterable of byte chunks (the POST /datasets wrapper and raw-body uploads)."""
    up, tmp = new_upload(db, store, by=by, project_id=project_id, plan=plan)
    try:
        disk_guard(tmp, size_hint or 0)
        sink = SinkFile(tmp / "0.src", filename, 0, max_upload_bytes())
        try:
            for c in chunks:
                sink.write(c)
        finally:
            info = sink.close()
        add_files(db, up, [info])
    except BaseException:
        discard(db, store, up, "failed while receiving")
        raise
    return get(db, up)


def _actor() -> str:
    """Who is acting in this request (the principal, else the typed actor), for the audit row."""
    from ..catalog import governance as G

    try:
        from ..authz.context import current

        ctx = current()
        p = ctx.principal if ctx is not None else None
    except Exception:  # noqa: BLE001
        p = None
    return p.display_name if p is not None else (G.current_actor() or "system")


def discard(db, store, up: str, why: str = "discarded") -> None:
    from ..catalog.schema import audit

    with db.connect() as con:
        r = con.execute("SELECT status, team_id, project_id, created_by FROM uploads WHERE id = ?", (up,)).fetchone()
        n = con.execute("UPDATE uploads SET status = 'discarded' WHERE id = ? AND status NOT IN ('committed', 'discarded')",
                        (up,)).rowcount
        if n and r is not None:  # F-X4b (X4 M6): a discarded upload (and its staged bytes) is audited
            audit(con, _actor(), "upload_discarded", {"upload_id": up, "why": why, "status_was": r["status"],
                                                      "created_by": r["created_by"], "project_id": r["project_id"]},
                  team_id=r["team_id"], project_id=r["project_id"], object_kind="upload", object_id=up)
    remove = getattr(store, "remove_tmp", None)
    if remove:
        remove(store.tmp_dir(up))


#: the fields PATCH /uploads/{u} takes (`by` is the actor, read by the route)
PLAN_KEYS = frozenset({"name", "target", "project_id", "table", "reason", "types", "parent_version_id", "root_id", "match",
                       "formats", "tables", "primary_table", "relations", "by"})


def patch_plan(db, up: str, body: dict[str, Any]) -> dict[str, Any]:
    u = get(db, up)
    if u["status"] in ("committed", "discarded", "expired"):
        raise LakeError(409, f"upload_{u['status']}", f"Upload {up} is {u['status']}.")
    plan = dict(u["plan"] or {})
    unknown = sorted(str(k) for k in body if k not in PLAN_KEYS)
    if unknown:  # F-X3a m7: a typo (`tabels`) is a 400, never silently dropped
        raise LakeError(400, "bad_plan", f"Unknown plan field(s) {unknown}: the plan takes {', '.join(sorted(PLAN_KEYS - {'by'}))}.",
                        fields=unknown)
    for k in ("name", "target", "project_id", "table", "reason"):
        if k in body:
            v = body[k]
            if v is not None and not isinstance(v, str):
                raise LakeError(400, "bad_plan", f"{k} is text.")
            plan[k] = (v or "").strip() or None
    if "types" in body:
        t = body["types"] or {}
        if not isinstance(t, dict):
            raise LakeError(400, "bad_plan", "types is {column: TYPE}.")
        bad = {c: v for c, v in t.items() if str(v).upper() not in DECLARABLE}
        if bad:
            raise LakeError(400, "bad_type", f"Declared types are one of {', '.join(DECLARABLE)}: {bad}.")
        plan["types"] = {str(c): str(v).upper() for c, v in t.items()}
    if "parent_version_id" in body or "root_id" in body:  # P06: the upload is the next version of this dataset
        plan["parent_version_id"] = resolve_parent(db, body.get("parent_version_id"), body.get("root_id")) \
            if (body.get("parent_version_id") or body.get("root_id")) else None
    if "match" in body:  # P06: the match the check also simulates (the commit may still name another)
        from ..dataset_versions import parse_match

        try:
            plan["match"] = parse_match(body["match"], plan.get("table") or "") if body["match"] else None
        except ValueError as e:
            raise LakeError(400, "bad_match", str(e)) from None
    if "formats" in body:  # a date / timestamp format per column (strptime), e.g. {"opened": "%d/%m/%Y"}
        fm = body["formats"] or {}
        if not isinstance(fm, dict) or not all(isinstance(v, str) and "%" in v for v in fm.values()):
            raise LakeError(400, "bad_plan", "formats is {column: strptime format}, e.g. {\"opened\": \"%d/%m/%Y\"}.")
        plan["formats"] = {str(c): v for c, v in fm.items()}
    if "tables" in body:  # P12: the plan per table: {name: {action, types, formats, match, key}}
        plan["tables"] = _parse_tables_plan(body["tables"])
    if "primary_table" in body:
        v = body["primary_table"]
        if v is not None and not isinstance(v, str):
            raise LakeError(400, "bad_plan", "primary_table is a table name.")
        plan["primary_table"] = (v or "").strip() or None
    if "relations" in body:  # P12: relations declared with the commit (orphans counted by the check)
        plan["relations"] = _parse_relations(body["relations"])
    with db.connect() as con:
        if plan.get("project_id") and not con.execute("SELECT 1 FROM projects WHERE id = ?", (plan["project_id"],)).fetchone():
            raise LakeError(404, "project_not_found", f"No project '{plan['project_id']}'.")
        # a changed plan makes the stored check stale: the commit asks for a new one
        con.execute("UPDATE uploads SET plan_json = ?, status = 'staged', check_json = NULL, check_sha256 = NULL,"
                    " project_id = COALESCE(?, project_id) WHERE id = ?", (json.dumps(plan), plan.get("project_id"), up))
        _set_parent_cols(con, up, plan.get("parent_version_id"))
    return get(db, up)


def resolve_parent(db, parent_version_id: str | None, root_id: str | None = None) -> str:
    """P06: the version an upload continues: the version named, else the root's default version."""
    with db.connect() as con:
        vid = parent_version_id
        if not vid and root_id:
            r = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (root_id,)).fetchone()
            vid = (r[0] if r and r[0] else None) or root_id
        r = con.execute("SELECT id, state FROM datasets WHERE id = ?", (vid,)).fetchone() if vid else None
        if not r:
            raise LakeError(404, "parent_not_found", f"No dataset version {vid or root_id!r} to add a version to.")
        if r["state"] == "deleted":
            raise LakeError(410, "parent_deleted", f"{vid} was deleted: pick another version as the parent.")
    return vid


def _set_parent_cols(con, up: str, vid: str | None) -> None:
    """uploads.parent_version_id / root_id / project_id / team_id follow the plan's parent."""
    if not vid:
        con.execute("UPDATE uploads SET parent_version_id = NULL, root_id = NULL WHERE id = ?", (up,))
        return
    d = con.execute("SELECT root_id, project_id, team_id FROM datasets WHERE id = ?", (vid,)).fetchone()
    root = (d["root_id"] if d else None) or vid
    rr = con.execute("SELECT project_id, team_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
    pid = (rr["project_id"] if rr else None) or (d["project_id"] if d else None)
    team = (rr["team_id"] if rr else None) or (d["team_id"] if d else None) or S.team_of_project(con, pid)
    con.execute("UPDATE uploads SET parent_version_id = ?, root_id = ?, project_id = COALESCE(?, project_id),"
                " team_id = COALESCE(?, team_id) WHERE id = ?", (vid, root, pid, team, up))


# --------------------------------------------------------------------------- detect + type


def _plan_sha(plan: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan or {}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def na_tokens(db, team_id: str | None) -> list[str]:
    try:
        with db.connect() as con:
            r = con.execute("SELECT policy FROM teams WHERE id = ?", (team_id,)).fetchone()
        pol = json.loads(r[0]) if r and r[0] else {}
        toks = pol.get("na_tokens")
        if isinstance(toks, list) and all(isinstance(t, str) for t in toks):
            return toks
    except Exception:  # noqa: BLE001 - a team without a policy has the defaults
        pass
    return list(DEFAULT_NA)


def _decode_head(head: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return head.decode(enc)
        except UnicodeDecodeError:
            continue
    return head.decode("latin-1", "replace")


def sniff_sep(text: str) -> str:
    from ..datasets import sniff_sep as legacy

    return legacy(text)


def _to_utf8(src: Path, dst: Path) -> str:
    """A file that is not UTF-8 → a UTF-8 copy (BOM decides UTF-16; else cp1252, then latin-1), streamed."""
    with open(src, "rb") as f:
        head = f.read(4096)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    elif head.count(b"\x00") > len(head) // 4:
        enc = "utf-16-le" if head[1::2].count(b"\x00") > head[0::2].count(b"\x00") else "utf-16-be"
    else:
        enc = "cp1252"
        try:
            with open(src, "rb") as f:
                dec = codecs.getincrementaldecoder("cp1252")()
                while b := f.read(CHUNK):
                    dec.decode(b)
        except UnicodeDecodeError:
            enc = "latin-1"
    with open(src, "rb") as fi, open(dst, "w", encoding="utf-8", newline="") as fo:
        dec = codecs.getincrementaldecoder(enc)()
        while b := fi.read(CHUNK):
            fo.write(dec.decode(b))
        fo.write(dec.decode(b"", final=True))
    return enc.upper()


def _header(path: Path, sep: str) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return next(csv.reader(f, delimiter=sep), [])


def _err_line(e: Exception) -> str:
    """A DuckDB CSV error in words (the engine text goes to the log, never to the response)."""
    s = str(e)
    log.info("csv read error: %s", s[:2000])
    m = re.search(r"[Ll]ine:?\s*(\d+)", s)
    if "Expected Number of Columns" in s or "too many columns" in s.lower() or "column count" in s.lower():
        return (f"Line {m[1]} has a different number of fields than the header. " if m else
                "A line has a different number of fields than the header. ") + \
            "Quote values that contain the separator or fix that line, then upload again."
    if m:
        return f"Could not read line {m[1]} of the file as CSV. Fix that line and upload again."
    return "Could not read the file as CSV."


def _cast_expr(col: str, typ: str, *, fmt: str | None = None, decimal_comma: bool = False, try_: bool = True) -> str:
    c = _q(col)
    cast = "TRY_CAST" if try_ else "CAST"
    if typ == "BIGINT":
        return f"{cast}(trim({c}) AS BIGINT)"
    if typ == "DOUBLE":
        x = f"replace(trim({c}), ',', '.')" if decimal_comma else f"trim({c})"
        return f"{cast}({x} AS DOUBLE)"
    if typ == "BOOLEAN":
        return f"{cast}(trim({c}) AS BOOLEAN)"
    if typ in ("DATE", "TIMESTAMP"):
        if fmt:
            f = "'" + fmt.replace("'", "''") + "'"
            return f"{'try_strptime' if try_ else 'strptime'}(trim({c}), {f})::{typ}"
        return f"{cast}(trim({c}) AS {typ})"
    return c


def _norm_type(t: str) -> str:
    t = (t or "VARCHAR").upper()
    if t in INT_TYPES:
        return "BIGINT"
    if t in FLOAT_TYPES or t.startswith("DECIMAL") or t.startswith("NUMERIC"):
        return "DOUBLE"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if t in ("DATE", "BOOLEAN"):
        return t
    return "VARCHAR"


def _sniff(con, path: Path, sep: str) -> tuple[dict[str, str], dict[str, str | None]]:
    """DuckDB's proposal per column (types) and its date / timestamp formats. Falls back to all VARCHAR."""
    try:
        row = con.execute(f"SELECT Columns, DateFormat, TimestampFormat FROM sniff_csv({_lit(path)}, delim = ?, header = true,"
                          " sample_size = 20480)", [sep]).fetchone()
        cols = row[0] or []
        types = {}
        for c in cols:
            name = c.get("name") if isinstance(c, dict) else c[0]
            typ = c.get("type") if isinstance(c, dict) else c[1]
            types[str(name)] = str(typ)
        return types, {"date": row[1] or None, "timestamp": row[2] or None}
    except Exception as e:  # noqa: BLE001
        log.info("sniff_csv failed (%s): every column starts as VARCHAR", str(e)[:300])
        return {}, {"date": None, "timestamp": None}


def check(db, store, up: str, dstore: Any = None) -> dict[str, Any]:
    """Detect and type the staged file, write tmp/<up>/typed.parquet, store the check (§5.1 steps 2, 3, 6). P06: an
    upload with a parent also gets `next_version`: key candidates, every method simulated, warnings (§5.3)."""
    with _lock(up):
        u = get(db, up)
        if u["status"] in ("committed", "discarded", "expired"):
            raise LakeError(409, f"upload_{u['status']}", f"Upload {up} is {u['status']}.")
        files = u["files"] or []
        if not files:
            raise LakeError(400, "no_files", "This upload has no file.")
        t0 = time.time()
        tmp = store.tmp_dir(up)
        plan = u["plan"] or {}
        srcs, sheet_notes = _sources(tmp, files, plan)
        if len(srcs) == 1 and not plan.get("tables") and not plan.get("relations"):
            # one table (a CSV, a Parquet file, or a workbook with one sheet): P05 / P06 exactly as before
            s0 = srcs[0]
            p0 = {**plan, "table": plan.get("table") or s0["table"]} if s0.get("sheet") is not None else plan
            try:
                out = _check_file(db, tmp, s0["f"], p0, u["team_id"], sep=s0.get("sep"),
                                  min_rows=1 if plan.get("parent_version_id") else 2)
            except LakeError as e:
                if s0.get("sheet") is None:
                    raise
                raise LakeError(e.status, e.code, f"{_label(s0)}: {e.message}", **e.extra) from None
            if s0.get("sheet") is not None:
                out["tables"][0].update(source_file=s0["source_file"], source_sha256=s0["source_sha256"], sheet=s0["sheet"])
                out["kind"] = "xlsx"
                out["sheets"] = sheet_notes
                out["warnings"] += [{**x, "table": out["tables"][0]["name"]} for x in s0.get("excel_warnings") or []]
            if plan.get("parent_version_id"):
                out["next_version"] = _check_next(db, store, dstore, tmp, out, plan)
                out["warnings"] += _next_integrity(db, tmp, out, plan)
                if any(c["name"] == "_op" for c in out["tables"][0]["schema"]):  # P13: not applied unless the plan says
                    tn = out["tables"][0]["name"]
                    out["warnings"].append({"code": "looks_like_delta", "table": tn,
                                            "message": f"The file has an _op column, so it may be a delta: as it stands it "
                                                       f"replaces the table and _op becomes a column. To apply it by key, PATCH "
                                                       f"{{table: '{tn}', tables: {{{tn}: {{action: 'delta', key: [...]}}}}}}."})
        else:
            out = _check_multi(db, store, dstore, tmp, srcs, sheet_notes, plan, u["team_id"])
        out.update(upload_id=up, plan_sha256=_plan_sha(plan), seconds=round(time.time() - t0, 2))
        csha = hashlib.sha256(json.dumps({k: v for k, v in out.items() if k != "seconds"}, sort_keys=True,
                                         separators=(",", ":"), default=str).encode()).hexdigest()
        out["check_sha256"] = csha
        with db.connect() as con:
            con.execute("UPDATE uploads SET status = 'checked', check_json = ?, check_sha256 = ? WHERE id = ?",
                        (json.dumps(out, default=str), csha, up))
        return out


def _table_name(plan: dict[str, Any], filename: str) -> str:
    t = plan.get("table") or re.sub(r"\.(csv|tsv|txt|parquet)$", "", Path(filename or "").name, flags=re.I)
    t = re.sub(r"[^A-Za-z0-9_]+", "_", t).strip("_").lower()[:64]
    if not t or t.startswith("__") or t[0].isdigit():
        t = PRIMARY if not t else f"t_{t}".strip("_")
    return t


#: F-X1 C1: columns per aggregate query in the typing scan (AGGS_PER_COL aggregates each).
SCAN_CHUNK_COLS = 64
#: non-null, leading zeros, cast failures, DOUBLE failures, slash dates (3), lossy casts (F-X3a M2)
AGGS_PER_COL = 8


def _lossy_pred(n: str, want: str, fmt: str | None, decimal_comma: bool) -> str | None:
    """F-X3a M2: the values that read as `want` only by changing: a decimal into BIGINT (TRY_CAST rounds 1.5 to 2), a
    date-time into DATE (the time is dropped). None when the type has no such cast."""
    c = _q(n)
    if want == "BIGINT":
        b = _cast_expr(n, "BIGINT")
        return f"{b} IS NOT NULL AND {_cast_expr(n, 'DOUBLE', decimal_comma=decimal_comma)} IS DISTINCT FROM {b}::DOUBLE"
    if want == "DATE" and not fmt:
        return (f"TRY_CAST(trim({c}) AS DATE) IS NOT NULL AND TRY_CAST(trim({c}) AS TIMESTAMP) IS DISTINCT FROM "
                f"TRY_CAST(trim({c}) AS DATE)::TIMESTAMP")
    return None


def _lossy_agg(n: str, want: str, fmt: str | None, decimal_comma: bool) -> str:
    p = _lossy_pred(n, want, fmt, decimal_comma)
    return f"count(*) FILTER (WHERE {p})" if p else "0"


def _lossy_examples(con, n: str, want: str, fmt: str | None, decimal_comma: bool) -> list[str]:
    p = _lossy_pred(n, want, fmt, decimal_comma)
    if not p:
        return []
    rows = con.execute(f"SELECT DISTINCT {_q(n)} FROM (SELECT {_q(n)} FROM src WHERE {p} LIMIT 1000) LIMIT 3").fetchall()
    return [str(r[0])[:60] for r in rows]


def _slash_examples(con, n: str) -> list[str]:
    c = _q(n)
    rows = con.execute(f"SELECT DISTINCT {c} FROM (SELECT {c} FROM src WHERE regexp_matches({c}, '{SLASH_DATE_RE}') LIMIT 1000)"
                       " LIMIT 3").fetchall()
    return [str(r[0])[:60] for r in rows]


def _guard_names(names: list[str]) -> None:
    """F-X1 C1 / M5: refuse a file wider than ENSEMBLE_MAX_COLUMNS, and any reserved column name (the system's
    `__row_id`, and every name the API uses for the row id: a user column must never be hidden by it)."""
    from .. import config

    cap = config.max_columns()
    if len(names) > cap:
        raise LakeError(400, "too_many_columns", f"This file has {len(names):,} columns; the limit is {cap:,} "
                                                 "(ENSEMBLE_MAX_COLUMNS). Split it into narrower files.")
    reserved = [h for h in names if is_reserved_name(h)]
    if reserved:
        raise LakeError(400, "reserved_column", f"These column names are reserved for the row id: {', '.join(reserved[:10])}. "
                                                "Rename them (names starting with '__', and '_row_id' / '_row', which "
                                                "the API uses for the row id, are reserved) and upload again.")


def _check_file(db, tmp: Path, f: dict[str, Any], plan: dict[str, Any], team_id: str, *, typed_name: str = "typed.parquet",
                sep: str | None = None, soft_types: dict[str, str] | None = None, min_rows: int = 2) -> dict[str, Any]:
    """One CSV / Parquet file (or an Excel sheet written as CSV, P12: `sep` forced) typed into tmp/`typed_name`.
    `soft_types` (P12, an append): the parent table's types, declared for the columns the file has.
    `min_rows` (F-X3a M4): two data rows for a new dataset; one for a file that continues a parent (a one-row delta)."""
    src = tmp / f["src"]
    name = f["filename"] or ""
    with open(src, "rb") as fh:
        head = fh.read(8)
    blocks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    notes: list[str] = []
    table = _table_name(plan, name)
    if head[:4] == b"PAR1" or name.lower().endswith(".parquet"):
        return _check_parquet(tmp, src, f, plan, table, min_rows=min_rows)
    if head[:2] == b"PK" or re.search(r"\.(xlsx|xlsm|xls)$", name, re.I):  # P12: expanded into sheets before this
        raise LakeError(400, "excel_unreadable", f"{name or 'This file'} looks like an Excel workbook: upload it as .xlsx.")
    if not re.search(r"\.(csv|tsv|txt)$", name, re.I):
        raise LakeError(400, "not_csv", f"{name or 'This file'} is not a CSV. Upload a .csv or .tsv file with a header row "
                                        "(or a .parquet file).")
    if not f["bytes"]:
        raise LakeError(400, "empty", "The file is empty.")
    path, encoding = src, "utf-8"
    if not f.get("utf8", True):
        path = tmp / f"{f['n']}.utf8.csv"
        if not path.exists():
            encoding = _to_utf8(src, path)
        else:
            encoding = (f.get("encoding") or "transcoded")
        notes.append(f"read as {encoding}")
    with open(path, "rb") as fh:
        head_text = _decode_head(fh.read(65536))
    if not head_text.strip():
        raise LakeError(400, "empty", "The file is empty.")
    ctrl = sum(1 for ch in head_text[:20000] if ord(ch) < 32 and ch not in "\t\r\n")
    if ctrl > max(2, len(head_text[:20000]) // 100):
        raise LakeError(400, "binary", "This looks like a binary file, not a CSV. Upload a .csv or .tsv text file.")
    sep = sep or ("\t" if name.lower().endswith(".tsv") else sniff_sep(head_text))
    if sep != ",":
        notes.append(f"separator {'tab' if sep == chr(9) else repr(sep)}")
    decimal_comma = sep == ";"
    raw_names = _header(path, sep)
    names = [h.strip() or f"col_{i + 1}" for i, h in enumerate(raw_names)]
    # F-X3a m1: names are compared without case (DuckDB's identifiers are case-insensitive: 'a' and 'A' are one column)
    lows = [h.lower() for h in names]
    dup = sorted({h.lower() for h in names if lows.count(h.lower()) > 1})
    if dup:
        where = "; ".join(f"'{h}' is columns {', '.join(str(i + 1) for i, n in enumerate(lows) if n == h)}"
                          f"{' (' + ', '.join(repr(names[i]) for i, n in enumerate(lows) if n == h) + ')' if len({names[i] for i, n in enumerate(lows) if n == h}) > 1 else ''}"
                          for h in dup)
        raise LakeError(400, "duplicate_header", f"The header repeats a column name ({where}; names that differ only in "
                                                 "case are the same column). Rename or remove the duplicate and upload again, "
                                                 "so no column is silently renamed.")
    _guard_names(names)
    if len(names) < 2:
        raise LakeError(400, "one_column", "No separated columns found (tried comma, semicolon, tab, pipe). Give a CSV with a "
                                           "header row and at least two columns.")
    nas = na_tokens(db, team_id)
    declared = {str(k): str(v).upper() for k, v in (plan.get("types") or {}).items()}
    soft = {c: t for c, t in (soft_types or {}).items() if c in names and c not in declared and t in DECLARABLE}
    unknown = [c for c in declared if c not in names]
    declared.update(soft)
    if unknown:
        blocks.append({"code": "declared_unknown", "message": f"Declared types name no column of the file: {', '.join(unknown)}."})
    con = duck.connect("job", **_duck_kw())
    try:
        sniffed, fmts = _sniff(con, path, sep)
        # sniff_csv names columns as the file does; map them to our stripped names by position
        sn_types = list(sniffed.values())
        proposal = {n: _norm_type(sn_types[i]) if i < len(sn_types) else "VARCHAR" for i, n in enumerate(names)}
        nul = "[" + ", ".join("'" + t.replace("'", "''") + "'" for t in nas) + "]"
        cols_struct = "{" + ", ".join(f"'{n.replace(chr(39), chr(39) * 2)}': 'VARCHAR'" for n in names) + "}"
        rel = (f"read_csv({_lit(path)}, delim = {_lit(sep)}, quote = '\"', escape = '\"', header = true, "
               f"columns = {cols_struct}, nullstr = {nul}, auto_detect = false, strict_mode = true)")
        con.execute(f"CREATE OR REPLACE TEMP VIEW src AS SELECT * FROM {rel}")
        # one scan: rows, non-null counts, leading zeros, cast failures for the proposal, slash-date evidence
        aggs = ["count(*)"]
        spec: list[tuple[str, str, str]] = []
        for n in names:
            want = declared.get(n) or proposal[n]
            fmt = (plan.get("formats") or {}).get(n) if want in ("DATE", "TIMESTAMP") else None
            fmt = fmt or (fmts["date"] if want == "DATE" else fmts["timestamp"] if want == "TIMESTAMP" else None)
            spec.append((n, want, fmt or ""))
            c = _q(n)
            aggs.append(f"count({c})")
            aggs.append(f"count(*) FILTER (WHERE regexp_matches({c}, '{LEADING_ZERO_RE}') AND NOT regexp_matches({c}, '[.eE:/]'))")
            if want == "VARCHAR":  # (failures: a decimal-comma number?; "digits only": a code such as an account number)
                dc = _cast_expr(n, "DOUBLE", decimal_comma=True)
                aggs += [f"count({c}) - count({dc})" if decimal_comma else "1",
                         f"count({c}) - count(TRY_CAST(trim({c}) AS UBIGINT))"]
            else:
                ex = _cast_expr(n, want, fmt=fmt, decimal_comma=decimal_comma)
                aggs.append(f"count({c}) - count({ex})")
                dbl = _cast_expr(n, "DOUBLE", decimal_comma=decimal_comma)
                aggs.append(f"count({c}) - count({dbl})" if want == "BIGINT" else "0")
            if want in ("VARCHAR", "DATE"):  # slash dates: is the day/month order settled by some value above 12?
                sl = f"length({c}) BETWEEN 6 AND 12 AND regexp_matches({c}, '{SLASH_DATE_RE}')"
                aggs.append(f"count(*) FILTER (WHERE {sl} AND TRY_CAST(regexp_extract({c}, '{SLASH_DATE_RE}', 1) AS INTEGER) > 12)")
                aggs.append(f"count(*) FILTER (WHERE {sl} AND TRY_CAST(regexp_extract({c}, '{SLASH_DATE_RE}', 2) AS INTEGER) > 12)")
                aggs.append(f"count(*) FILTER (WHERE {sl})")
            else:
                aggs += ["0", "0", "0"]
            aggs.append(_lossy_agg(n, want, fmt, decimal_comma))  # F-X3a M2: a cast that would change the value
        # F-X1 C1: one query per chunk of columns. A single SELECT of every aggregate makes DuckDB's planning cost
        # quadratic in the column count (5,000 columns took the API past 12 GB); chunks keep it linear.
        # (count(*) rides with the first chunk: a narrow file is still one scan of the CSV)
        row: list[Any] = []
        step = AGGS_PER_COL * SCAN_CHUNK_COLS
        try:
            for k in range(1, len(aggs), step):
                head = [aggs[0]] if k == 1 else []
                row += list(con.execute(f"SELECT {', '.join(head + aggs[k:k + step])} FROM src").fetchall()[0])
        except Exception as e:  # noqa: BLE001 - malformed CSV: a 400 in words
            raise LakeError(400, "csv_unreadable", _err_line(e)) from None
        n_rows = int(row[0])
        if n_rows < min_rows:
            raise LakeError(400, "too_few_rows", "This file has no data rows." if min_rows <= 1 else
                            "This file has fewer than two data rows (a new dataset needs at least two).")
        schema: list[dict[str, Any]] = []
        exprs: list[str] = []
        kept_text: list[str] = []
        for i, (n, want, fmt) in enumerate(spec):
            nn, lz, fail, fail_dbl, gt12a, gt12b, n_slash, lossy = \
                (int(x) for x in row[1 + i * AGGS_PER_COL: 1 + (i + 1) * AGGS_PER_COL])
            typ, reason = want, None
            is_declared = n in declared
            own_fmt = bool((plan.get("formats") or {}).get(n))
            if is_declared and n in soft and want == "DATE" and n_slash and not gt12a and not gt12b and not own_fmt:
                # F-X3a M3: the parent's DATE type does not settle dd/mm against mm/dd. As in a plain upload (§11), the
                # column is kept as text with date_ambiguous (the delta / append then blocks on its type): the order
                # must come from the plan's formats, never from what the other rows of this file happen to hold.
                typ, reason = "VARCHAR", "ambiguous day/month order: kept as text; give the format"
                warnings.append({"code": "date_ambiguous", "column": n, "n": n_slash,
                                 "examples": _slash_examples(con, n),
                                 "message": f"'{n}' could be dd/mm or mm/dd: no value has a day above 12, so the table's DATE "
                                            f"type cannot be applied safely. It is kept as text; send the format in the plan "
                                            f"(tables.<table>.formats {{\"{n}\": \"%d/%m/%Y\"}} or \"%m/%d/%Y\") to read it as "
                                            "a date."})
            elif is_declared:
                reason = f"the parent table's type ({want})" if n in soft else f"declared {want}"
                if fail and want != "VARCHAR":  # (P12: any value reads as text; the scan's VARCHAR slots hold other counts)
                    blocks.append({"code": "declared_type_mismatch", "column": n,
                                   "message": f"'{n}' is declared {want} but {fail:,} value(s) do not read as {want}.",
                                   "examples": _examples(con, n, want, fmt, decimal_comma)})
                elif lossy:  # F-X3a M2: 1.5 into BIGINT is 2, a time into DATE is dropped: refused, never applied
                    ex = _lossy_examples(con, n, want, fmt, decimal_comma)
                    blocks.append({"code": "lossy_cast", "column": n, "type": want, "n": lossy, "examples": ex,
                                   "message": f"'{n}' is {reason} but {lossy:,} value(s) would change when read as {want} "
                                              f"(e.g. {', '.join(repr(x) for x in ex)}"
                                              f"{': decimals would be rounded' if want == 'BIGINT' else ': the time of day would be dropped' if want == 'DATE' else ''}). "
                                              "Nothing is rounded silently: fix the values, or "
                                              + ("change the table's type first (a rewrite)." if n in soft else
                                                 f"declare another type for '{n}'.")})
            elif want == "VARCHAR" and decimal_comma and nn and not fail and not lz:
                typ, reason = "DOUBLE", "numbers (decimal comma)"
            elif want == "VARCHAR":
                reason = "text"
                if lz and not fail_dbl:  # every value is digits, some start with 0: a code (ZIP, account number)
                    reason = "leading zeros: kept as text"
                    kept_text.append(n)
                elif nn and n_slash == nn:
                    if not gt12a and not gt12b:
                        reason = "ambiguous day/month order: kept as text; declare the format"
                        warnings.append({"code": "date_ambiguous", "column": n,
                                         "message": f"'{n}' could be dd/mm or mm/dd: no value settles the order. It is kept "
                                                    "as text; declare its type (PATCH the upload's types) to read it as a date."})
                    else:
                        reason = "dates in a format DuckDB did not settle: kept as text; declare the format"
            elif lz and want in ("BIGINT", "DOUBLE"):
                typ, reason = "VARCHAR", "leading zeros: kept as text"
                kept_text.append(n)
            elif want == "DATE" and n_slash and not gt12a and not gt12b and fmt and ("%d" in fmt and "%m" in fmt):
                typ, reason = "VARCHAR", "ambiguous day/month order: kept as text; declare the format"
                warnings.append({"code": "date_ambiguous", "column": n,
                                 "message": f"'{n}' could be dd/mm or mm/dd: no value has a day above 12. It is kept as text; "
                                            "declare its type (PATCH the upload's types) to read it as a date."})
            elif (fail or lossy) and want == "BIGINT" and not fail_dbl:
                typ, reason = "DOUBLE", "numbers with decimals"
            elif fail:
                ex = _examples(con, n, want, fmt, decimal_comma)
                typ, reason = "VARCHAR", f"{fail:,} value(s) do not read as {want}: kept as text"
                warnings.append({"code": "cast_failed", "column": n, "proposed": want, "n": fail, "examples": ex,
                                 "message": f"'{n}' looks like {want} but {fail:,} value(s) do not read as one "
                                            f"(e.g. {', '.join(repr(x) for x in ex)}); it is kept as text."})
            else:
                reason = {"BIGINT": "whole numbers", "DOUBLE": "numbers", "BOOLEAN": "true / false",
                          "DATE": f"dates{f' ({fmt})' if fmt else ''}", "TIMESTAMP": f"timestamps{f' ({fmt})' if fmt else ''}"}[want]
            schema.append({"name": n, "type": typ, "nullable": nn < n_rows, "reason": reason, "non_null": nn})
            ex = _cast_expr(n, typ, fmt=fmt or None, decimal_comma=decimal_comma, try_=False) if typ != "VARCHAR" else _q(n)
            exprs.append(f"{ex} AS {_q(n)}")
        preview = [list(r) for r in con.execute("SELECT * FROM src LIMIT 5").fetchall()]
        typed = tmp / typed_name
        if not blocks:
            # the typed copy, in file order (row ids are file_row_number of this file)
            con.execute("SET preserve_insertion_order = true")
            try:
                con.execute(f"COPY (SELECT {', '.join(exprs)} FROM src) TO {_lit(typed)} (FORMAT parquet, COMPRESSION zstd,"
                            " ROW_GROUP_SIZE 122880)")
            except Exception as e:  # noqa: BLE001
                raise LakeError(400, "csv_unreadable", _err_line(e)) from None
        target = plan.get("target")
        if target and target not in names:
            blocks.append({"code": "target_unknown", "message": f"Column '{target}' is not in the file."})
    finally:
        _close(con)
    if kept_text:
        notes.append(f"kept as text (codes with leading zeros): {', '.join(kept_text)}")
    return {"kind": "csv", "tables": [{"name": table, "source_file": name, "source_sha256": f["sha256"], "bytes": f["bytes"],
                                       "encoding": encoding, "separator": sep, "na_tokens": nas, "n_rows": n_rows,
                                       "n_cols": len(names), "schema": schema, "kept_as_text": kept_text,
                                       "preview": {"columns": names, "rows": preview}}],
            "warnings": warnings, "blocks": blocks, "notes": notes, "typed": typed_name if not blocks else None}


def _close(con) -> None:
    """Close a job connection and remove its spill directory (as duck.job() does)."""
    try:
        spill = con.execute("SELECT current_setting('temp_directory')").fetchone()[0]
        con.close()
        if spill and Path(spill).name.startswith("job-"):
            shutil.rmtree(spill, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _examples(con, n: str, want: str, fmt: str | None, decimal_comma: bool) -> list[str]:
    ex = _cast_expr(n, want, fmt=fmt, decimal_comma=decimal_comma)
    rows = con.execute(f"SELECT DISTINCT {_q(n)} FROM (SELECT {_q(n)} FROM src WHERE {_q(n)} IS NOT NULL AND {ex} IS NULL LIMIT 1000)"
                       " LIMIT 5").fetchall()
    return [str(r[0])[:60] for r in rows]


def _check_parquet(tmp: Path, src: Path, f: dict[str, Any], plan: dict[str, Any], table: str, *, min_rows: int = 2) -> dict[str, Any]:
    con = duck.connect("job", **_duck_kw())
    blocks: list[dict[str, Any]] = []
    try:
        try:
            cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_lit(src)})").fetchall()
            n_rows = con.execute(f"SELECT count(*) FROM read_parquet({_lit(src)})").fetchone()[0]
        except Exception as e:  # noqa: BLE001
            log.info("parquet read error: %s", str(e)[:500])
            raise LakeError(400, "parquet_unreadable", f"Could not read {f['filename'] or 'the file'} as Parquet.") from None
        names = [c[0] for c in cols]
        _guard_names(names)
        lows = [h.lower() for h in names]
        dup = sorted({h for h in lows if lows.count(h) > 1})
        if dup:  # F-X3a m1
            raise LakeError(400, "duplicate_header", f"The file repeats a column name ({', '.join(repr(h) for h in dup)}; names "
                                                     "that differ only in case are the same column). Rename one and upload again.")
        if len(names) < 2:
            raise LakeError(400, "one_column", "A dataset needs at least two columns.")
        if n_rows < min_rows:
            raise LakeError(400, "too_few_rows", "This file has no data rows." if min_rows <= 1 else
                            "This file has fewer than two data rows (a new dataset needs at least two).")
        nested = [f"{c[0]} ({c[1]})" for c in cols if any(k in c[1] for k in ("STRUCT", "MAP", "[]", "LIST", "UNION"))]
        if nested:
            blocks.append({"code": "nested_type", "message": f"Nested columns are not supported: {', '.join(nested)}."})
        nn = con.execute("SELECT " + ", ".join(f"count({_q(n)})" for n in names) + f" FROM read_parquet({_lit(src)})").fetchone()
        schema = [{"name": c[0], "type": c[1], "nullable": int(nn[i]) < n_rows, "reason": "as stored in the Parquet file",
                   "non_null": int(nn[i])} for i, c in enumerate(cols)]
        preview = [[None if v is None else str(v) for v in r] for r in
                   con.execute(f"SELECT * FROM read_parquet({_lit(src)}) LIMIT 5").fetchall()]
    finally:
        _close(con)
    target = plan.get("target")
    if target and target not in names:
        blocks.append({"code": "target_unknown", "message": f"Column '{target}' is not in the file."})
    return {"kind": "parquet", "tables": [{"name": table, "source_file": f["filename"], "source_sha256": f["sha256"],
                                           "bytes": f["bytes"], "n_rows": int(n_rows), "n_cols": len(names), "schema": schema,
                                           "kept_as_text": [], "preview": {"columns": names, "rows": preview}}],
            "warnings": [], "blocks": blocks, "notes": [], "typed": f["src"] if not blocks else None}


# --------------------------------------------------------------------------- P06: the next version (§4.2, §5.3)


class _Parent:
    """The parent table an upload continues: its relation, its row ids by position, its holdout ids, its keys."""

    def __init__(self, db, store, dstore, vid: str, table: str | None, *, split: bool = True) -> None:
        from .. import frames

        self.vid = vid
        with db.connect() as con:
            r = con.execute("SELECT id, root_id, storage, target, name, primary_table, rows FROM datasets WHERE id = ?",
                            (vid,)).fetchone()
            if not r:
                raise LakeError(404, "parent_not_found", f"No dataset version {vid}.")
            self.rec = dict(r)
        self.root = self.rec["root_id"] or vid
        self.storage = self.rec["storage"] or "csv"
        self.arrow = None
        self.declared: list[list[str]] = []
        if self.storage == "parquet":
            if table:  # F-X3a m2: a plan `table` the parent does not have is a 400 (not a FrameError 500)
                with db.connect() as con:
                    have = [x[0] for x in con.execute("SELECT table_name FROM version_tables WHERE version_id = ?"
                                                      " ORDER BY ordinal", (vid,))]
                if have and table not in have:
                    raise LakeError(400, "table_not_in_parent",
                                    f"{vid} has no table '{table}' (its tables: {', '.join(have)}). To add it as a new table, "
                                    f"PATCH {{tables: {{{table}: {{action: 'new'}}}}}}; to continue a table, name one of these.",
                                    table=table, tables=have)
            lt = frames.lake_table(frames.Table(vid, table or frames.PRIMARY_TABLE)) if table else \
                frames.lake_table(frames.table(vid))
            self.table, self.sql = lt.name, f"SELECT * FROM {frames.lake_sql(lt)}"
            self.tables = []
            with db.connect() as con:
                self.tables = [x[0] for x in con.execute("SELECT table_name FROM version_tables WHERE version_id = ?"
                                                         " ORDER BY ordinal", (vid,))]
                self.declared = [json.loads(k[0]) for k in con.execute(
                    "SELECT columns_json FROM table_keys WHERE version_id = ? AND table_name = ? AND valid = 1"
                    " ORDER BY kind, columns_json", (vid, lt.name))]
            from . import splitstore as SS

            self.ids = SS.table_row_ids(vid, lt.name)  # by read position (P10: a migrated table is in data.csv order)
        else:
            # a CSV-era parent not migrated yet (P10 gives it these same ids, §5.11 step 3): its lineage row map, else
            # 0..n-1 for a root; a version with no row mapping cannot be continued without breaking identity
            import numpy as np
            import pyarrow as pa

            from .migrate import legacy_row_map

            df = frames.read_version(vid)
            lin = dstore.lineage(vid) if dstore is not None else None
            rr = legacy_row_map(lin, len(df))
            if rr is not None:
                ids = rr
            elif self.root == vid:
                ids = np.arange(len(df), dtype="int64")
            else:
                raise LakeError(409, "parent_unmapped", f"{vid} is a CSV-era version with no row mapping to its root: its rows "
                                                        "have no stable ids to match against. Add the version to its root's "
                                                        "default, or wait for the migration (P10).")
            self.table = (table or "data")
            self.tables = [self.table]
            self.ids = ids
            df = df.reset_index(drop=True)
            df.insert(0, "__row_id", ids)
            self.arrow = pa.Table.from_pandas(df, preserve_index=False)
            self.sql = "SELECT * FROM _parent_csv"
        tg = self.rec.get("target")
        # P12: a table other than the version's primary has no dataset target and no holdout of its own (a split is
        # drawn for a table when a run names it), so nothing is drawn here for it
        self.is_primary = self.storage != "parquet" or self.table == (self.rec.get("primary_table") or self.table)
        if not self.is_primary:
            tg = None
        self.target = tg
        self.holdout_ids = None
        self.split_key = None
        self.split = None
        if dstore is not None and self.is_primary and split:  # F-X5a: an edit preview asks for no split (split=False)
            try:
                _, sp, _ = dstore.ensure_splits(vid, tg)
                import numpy as np

                pos = np.asarray(sp.get("holdout") or [], dtype="int64")
                self.holdout_ids = self.ids[pos[pos < len(self.ids)]]
                self.split = sp
                self.split_key = sp.get("key")
            except Exception as e:  # noqa: BLE001 - too few rows for a split: no holdout to lose
                log.info("parent %s split: %s", vid, str(e)[:200])
                self.split = None

    def attach(self, con) -> None:
        if self.arrow is not None:
            con.register("_parent_csv", self.arrow)

    def next_row_id(self, db) -> int:
        with db.connect() as con:
            r = con.execute("SELECT next_row_id FROM dataset_root_tables WHERE root_id = ? AND table_name = ?",
                            (self.root, self.table)).fetchone()
        hi = int(self.ids.max()) + 1 if len(self.ids) else 0
        return max(hi, int(r[0]) if r else 0, 0)


def _new_sql(typed: Path) -> str:
    return (f"SELECT file_row_number::BIGINT AS __fr, * EXCLUDE (file_row_number) FROM read_parquet({_lit(typed)},"
            " file_row_number = true)")


def _matcher(con, parent: "_Parent", typed: Path):
    from ..dataset_versions import Matcher

    parent.attach(con)
    return Matcher(con, parent.sql, _new_sql(typed), parent.holdout_ids, parent.declared)


def _check_next(db, store, dstore, tmp: Path, out: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """§5.3 steps 1–3 for the check: candidates, the top candidate / declared key / row hash / the plan's match
    simulated, the warnings each would raise, and what the commit will need."""
    from ..dataset_versions import ACK_TOKEN

    parent = _Parent(db, store, dstore, plan["parent_version_id"], plan.get("table"))
    t = out["tables"][0]
    t["name"] = parent.table  # the next version rewrites the parent's table
    nv: dict[str, Any] = {"parent_version_id": parent.vid, "root_id": parent.root, "table": parent.table,
                          "parent_storage": parent.storage, "parent_rows": int(len(parent.ids)),
                          "parent_target": parent.target, "split_key": parent.split_key}
    if out.get("blocks") or not out.get("typed"):
        return {**nv, "note": "the file is blocked: matching runs once the blocks are fixed"}
    con = duck.connect("job", **_duck_kw())
    try:
        mt = _matcher(con, parent, tmp / out["typed"])
        cands = mt.candidates()
        nv["parent_holdout"] = mt.n_holdout
        nv["candidates"] = cands
        sims: list[dict[str, Any]] = []
        seen: set[str] = set()

        def sim(m: dict[str, Any], role: str) -> dict[str, Any]:
            k = json.dumps(m, sort_keys=True)
            for s_ in sims:
                if s_["_k"] == k:
                    s_["roles"].append(role)
                    return s_
            r = {**mt.simulate(m), "roles": [role], "_k": k}
            sims.append(r)
            seen.add(k)
            return r

        best = None
        valid_cands = [c for c in cands if c["valid"]]
        if valid_cands:
            best = sim({"method": "key", "columns": valid_cands[0]["columns"]}, "best_candidate")
        if parent.declared:
            sim({"method": "declared", "columns": parent.declared[0]}, "declared")
        sim({"method": "row_hash"}, "row_hash")
        chosen = None
        pm = plan.get("match")
        if pm:
            m = dict(pm)
            if m["method"] == "declared":
                m["columns"] = parent.declared[0] if parent.declared else []
            chosen = sim(m, "chosen") if m.get("columns") or m["method"] in ("row_hash", "none") else \
                {"method": "declared", "valid": False, "problem": {"code": "no_declared_key",
                                                                   "message": f"{parent.vid} has no confirmed key."}}
        for s_ in sims:
            s_["warnings"] = mt.warnings_for(s_, best)
            s_["needs_acknowledge"] = [ACK_TOKEN] if any(w["needs_ack"] for w in s_["warnings"]) else []
        nv["methods"] = [{k: v for k, v in s_.items() if k != "_k"} for s_ in sims]
        nv["preselect"] = (dict(pm) if pm else {"method": "declared"} if parent.declared else
                           {"method": "key", "columns": valid_cands[0]["columns"]} if valid_cands else None)
        if chosen is not None:
            nv["chosen"] = {k: v for k, v in chosen.items() if k != "_k"}
        nv["contract"] = mt.contract(plan.get("target") or parent.target)
        nv["commit_needs"] = {"match": True, "acknowledge_token": ACK_TOKEN,
                              "note": "POST …/commit {match: {method: key|declared|row_hash|none, columns}, "
                                      f"acknowledge: [\"{ACK_TOKEN}\"] when the chosen method shows needs_acknowledge}}"}
    except ValueError as e:
        raise LakeError(400, "match_impossible", str(e)) from None
    finally:
        _close(con)
    return nv


def _next_integrity(db, tmp: Path, out: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    """F-X3a M1 on the one-file path (P06): the rewritten table against the parent's keys and relations."""
    pvid = plan["parent_version_id"]
    if out.get("blocks") or not out.get("typed"):
        return []
    with db.connect() as con:
        r = con.execute("SELECT storage FROM datasets WHERE id = ?", (pvid,)).fetchone()
    if not r or r["storage"] != "parquet":
        return []
    prec, ptabs = _parent_tables(db, pvid)
    t = out["tables"][0]
    name = t["name"]
    if name not in ptabs or (not prec.get("relations") and not ptabs[name].get("all_keys")):
        return []
    rel = {n: _rel_sql(db, tmp, pvid, None, n) for n in ptabs if n != name}
    rel[name] = f"(SELECT * FROM read_parquet({_lit(tmp / out['typed'])}))"
    cols_of = {n: [c["name"] for c in ptabs[n]["schema"]] for n in ptabs}
    cols_of[name] = [c["name"] for c in t["schema"]]
    con = duck.connect("job", **_duck_kw())
    try:
        return _integrity_warnings(con, prec, ptabs, rel, cols_of, {name}, list(ptabs), {}, plan)
    finally:
        _close(con)


def _mask(db, root: str, table: str, problem: dict[str, Any]) -> dict[str, Any]:
    """key_invalid examples: values of a PII-tagged key column are masked (§6.3; P20: the masks of lake.mask, with
    the team's salt, like every other masked read)."""
    from . import mask as M

    with db.connect() as con:
        tags = {r[0]: r[1] for r in con.execute(
            "SELECT column_name, mask FROM column_tags WHERE root_id = ? AND table_name = ? AND mask <> 'none'", (root, table))}
        salt = M.team_salt(con, M.root_and_team(con, root)[1]) if tags else ""
    cols = problem.get("columns") or []
    for side in (problem.get("sides") or {}).values():
        for ex in side.get("examples") or []:
            ex["value"] = [M.mask_value(v, tags[c], salt) if c in tags else v for c, v in zip(cols, ex["value"])]
    return problem


def _write_lineage_summary(dstore, vid: str, parent: "_Parent", rids: Any, match_rec: dict[str, Any], by: str,
                           reason: str) -> None:
    """lineage.json for the CSV-era readers (kind, parent, how rows were matched). P09: the split is a Parquet blob
    carried by join inside the commit, and a row's identity is its __row_id, so neither a split JSON nor a row map
    is written any more."""
    d = dstore.dir(vid)
    d.mkdir(parents=True, exist_ok=True)
    lin = {"parent": parent.vid, "root": parent.root, "kind": "upload_version", "op": "upload_version",
           "storage": "parquet", "rows": "__row_id (P09: no row map; the manifest's provenance.ops[].match is the record)",
           "matched_by": match_rec["matched_by"], "row_key": (match_rec.get("columns") or [None])[0]
           if match_rec["method"] in ("key", "declared") and len(match_rec.get("columns") or []) == 1 else None,
           "match": match_rec, "rows_before": int(len(parent.ids)), "rows_after": int(len(rids)), "by": by,
           **({"reason": reason} if reason else {})}
    (d / "lineage.json").write_text(json.dumps(lin))


# --------------------------------------------------------------------------- commit


def commit(db, store, up: str, *, by: str, reason: str = "", check_sha256: str | None = None,
           make_default: bool | None = None, match: Any = None, acknowledge: Any = None,
           dstore: Any = None) -> tuple[dict[str, Any], bool]:
    """(the committed version, created). Idempotent on the upload id: a repeat returns the version (created=False).
    P06: an upload with a parent needs `match` (400 match_required with the candidates otherwise) and, when the chosen
    method loses ≥ 1 % of the parent's holdout identities or a better key exists, acknowledge: ["holdout_identity_loss"]."""
    with _lock(up):
        u = get(db, up)
        if u["status"] == "committed" and u.get("committed_version_id"):
            return _version(db, u["committed_version_id"], up), False
        if u["status"] in ("discarded", "expired"):
            raise LakeError(409, f"upload_{u['status']}", f"Upload {up} is {u['status']}.")
        if u["status"] != "checked" or not u.get("check"):
            raise LakeError(409, "check_required", f"Check upload {up} first (POST /api/uploads/{up}/check): the commit writes "
                                                   "what the check showed.")
        chk = u["check"]
        if check_sha256 and check_sha256 != u["check_sha256"]:
            raise LakeError(409, "check_stale", "The upload was checked again or its plan changed after the check you saw.",
                            check_sha256=u["check_sha256"])
        plan = u["plan"] or {}
        if chk.get("plan_sha256") != _plan_sha(plan):
            raise LakeError(409, "check_stale", "The plan changed after the check; check again.")
        if chk.get("blocks"):
            raise LakeError(409, "check_blocked", "The check found problems that block the commit.", blocks=chk["blocks"])
        tmp = store.tmp_dir(up)
        if chk.get("kind") == "multi":  # P12: several tables (files, sheets), or a plan per table
            return _commit_multi(db, store, dstore, u, chk, plan, tmp, by=by, reason=reason, make_default=make_default,
                                 match=match, acknowledge=acknowledge)
        typed = tmp / (chk.get("typed") or "typed.parquet")
        if not typed.exists():
            raise LakeError(409, "check_required", "The checked file is gone from staging; check again.")
        t = chk["tables"][0]
        f = (u["files"] or [])[0]
        if plan.get("parent_version_id"):
            return _commit_next(db, store, dstore, u, chk, plan, typed, tmp, by=by, reason=reason, make_default=make_default,
                                match=match if match is not None else plan.get("match"), acknowledge=acknowledge)
        name = plan.get("name") or re.sub(r"\.(csv|tsv|txt|parquet)$", "", Path(f["filename"] or "upload").name, flags=re.I)
        target = plan.get("target")
        reasons = {c["name"]: c["reason"] for c in t["schema"] if c.get("reason")}
        sources = [{"filename": f["filename"], "sha256": f["sha256"], "bytes": f["bytes"], "kept": True}]
        warns = [{k: v for k, v in w.items() if k != "examples"} for w in chk.get("warnings") or []]
        w = VersionWriter(db, store, op="upload", by=by, name=name, project_id=plan.get("project_id") or None,
                          reason=reason or plan.get("reason") or "", job_id=up, sources=sources, warnings=warns,
                          primary_table=t["name"], duck_kw=_duck_kw())
        with w:
            done = w.committed_for_job()
            if done:
                _mark_committed(db, up, done)
                return _version(db, done, up), False
            cols = ", ".join(_q(c["name"]) for c in t["schema"])
            w.write(t["name"], f"SELECT file_row_number::BIGINT AS {ROW_ID}, {cols} FROM read_parquet({_lit(typed)},"
                               " file_row_number = true)", reasons=reasons, source_file=f["filename"])
            out = w.commit(make_default=True if make_default is None else make_default, target=target,
                           kind="tabular", note=f"Uploaded file {f['filename']}")
        vid = out["version_id"]
        _keep_source(db, store, tmp / f["src"], f, up, vid)
        _mark_committed(db, up, vid)
        with db.connect() as con:
            S.audit(con, by, "upload_committed", {"upload_id": up, "dataset_id": vid, "root_id": out.get("root_id") or vid,
                                                  "check_sha256": u["check_sha256"], "source_sha256": f["sha256"]})
        if store and hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
        return _version(db, vid, up, out), True


def _commit_next(db, store, dstore, u: dict[str, Any], chk: dict[str, Any], plan: dict[str, Any], typed: Path, tmp: Path, *,
                 by: str, reason: str, make_default: bool | None, match: Any, acknowledge: Any) -> tuple[dict[str, Any], bool]:
    """§5.3 steps 3–4 and §5.2: the person's `match`, re-simulated now, warnings acknowledged, ids assigned (matched rows
    keep the parent's __row_id, new rows get next_row_id + k in file order), written through the VersionWriter with the
    decision in provenance.ops[].match and the warnings in provenance.checks."""
    from ..dataset_versions import parse_match

    up = u["id"]
    t = chk["tables"][0]
    f = (u["files"] or [])[0]
    nv = chk.get("next_version") or {}
    try:
        m = parse_match(match, t["name"])
    except ValueError as e:
        raise LakeError(400, "bad_match", str(e)) from None
    if m is None:
        raise LakeError(400, "match_required",
                        "Say how this file's rows are matched to the parent's: match = {method: key, columns: [...]}, "
                        "{method: declared}, {method: row_hash} or {method: none}. Nothing is guessed. The candidates and "
                        "what each method would do are below (and in the check).",
                        candidates=nv.get("candidates") or [], methods=nv.get("methods") or [], preselect=nv.get("preselect"))
    acks = acknowledge if isinstance(acknowledge, list) else [acknowledge] if isinstance(acknowledge, str) and acknowledge else []
    acks = [str(a) for a in acks]
    integrity = _need_integrity_acks(chk, acks)  # F-X3a M1
    parent = _Parent(db, store, dstore, plan["parent_version_id"], plan.get("table"))
    if parent.vid != nv.get("parent_version_id", parent.vid):
        raise LakeError(409, "check_stale", "The parent changed after the check; check again.")
    if m["method"] == "declared":
        if not parent.declared:
            raise LakeError(400, "no_declared_key", f"{parent.vid} has no confirmed key for table {parent.table!r}: choose "
                                                    "{method: key, columns: [...]} instead.")
        m["columns"] = parent.declared[0]
    sources = [{"filename": f["filename"], "sha256": f["sha256"], "bytes": f["bytes"], "kept": True}]
    target = plan.get("target") or (parent.target if parent.target in {c["name"] for c in t["schema"]} else None)
    w = VersionWriter(db, store, op="upload_version", by=by, parent_id=parent.vid, reason=reason or plan.get("reason") or "",
                      job_id=up, sources=sources, primary_table=parent.table, duck_kw=_duck_kw())
    if parent.storage == "parquet":
        w.relations_acknowledged = {x["relation"] for x in integrity if x["code"] == "relation_dropped"}
    with w:
        done = w.committed_for_job()
        if done:
            _mark_committed(db, up, done)
            return _version(db, done, up), False
        rec, mw, contract, chosen, rids = _write_matched(w, db, parent, t, typed, m, acks, by, target, f["filename"])
        if parent.storage == "parquet":
            _guard_keys(w, _parent_tables(db, parent.vid)[1], integrity)
        warns = [{k: v for k, v in x.items() if k != "examples"} | ({"acknowledged": True} if x.get("needs_ack")
                 and x.get("code") in INTEGRITY_ACKS else {}) for x in chk.get("warnings") or []] + \
            [{k: v for k, v in x.items() if k not in ("values",)} for x in contract["warnings"]] + \
            [{**x, "source": "match"} for x in mw]
        for other in parent.tables:  # a multi-table parent: the other tables are carried (zero bytes)
            if other != parent.table:
                w.carry(other)
        w.warnings = warns
        out = w.commit(make_default=make_default, target=target, kind="tabular",
                       note=f"Uploaded file {f['filename']} as the next version of {parent.vid}: {chosen['matched']:,} rows "
                            f"matched by {chosen['label']}, {chosen['changed']:,} changed, {chosen['added']:,} new, "
                            f"{chosen['removed']:,} gone")
    vid = out["version_id"]
    created = not out.get("deduped")
    if created and dstore is not None:
        _write_lineage_summary(dstore, vid, parent, rids, rec, by, reason or plan.get("reason") or "")
    _keep_source(db, store, tmp / f["src"], f, up, vid)
    _mark_committed(db, up, vid)
    with db.connect() as con:
        S.audit(con, by, "upload_committed", {"upload_id": up, "dataset_id": vid, "root_id": parent.root, "parent": parent.vid,
                                              "check_sha256": u["check_sha256"], "source_sha256": f["sha256"],
                                              "match": {k: rec[k] for k in ("method", "matched", "changed", "added", "removed",
                                                                            "holdout_identity_lost", "acknowledged")}
                                              | ({"columns": rec["columns"]} if rec.get("columns") else {}),
                                              "deduped": out.get("deduped")})
    if store and hasattr(store, "remove_tmp"):
        store.remove_tmp(tmp)
    res = _version(db, vid, up, out)
    res.update(match=rec, warnings=warns, contract=contract, deduped=out.get("deduped"))
    return res, created


def _write_matched(w, db, parent: "_Parent", t: dict[str, Any], typed: Path, m: dict[str, Any], acks: list[str], by: str,
                   target: str | None, filename: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], list[int]]:
    """§5.3 steps 3–4 for one table (P06, shared by P12's multi-table commit): the person's `match` re-simulated, its
    warnings acknowledged, ids assigned (matched rows keep the parent's __row_id, new rows get next_row_id + k in file
    order), the table written, the parent's keys re-checked, the split carried. Returns (match record, match warnings,
    contract, the chosen simulation, the new row ids)."""
    from ..dataset_versions import ACK_TOKEN

    try:
        mt = _matcher(w.con, parent, typed)
    except ValueError as e:
        raise LakeError(400, "match_impossible", str(e)) from None
    if m["method"] in ("key", "declared"):
        prob = mt.key_problems(m["columns"])
        if prob:
            if prob["code"] == "key_invalid":
                masked = _mask(db, parent.root, parent.table, prob)
                raise LakeError(400, "key_invalid", prob["message"], table=parent.table,
                                **{k: v for k, v in masked.items() if k not in ("code", "message")})
            raise LakeError(400, "bad_match", prob["message"], columns=prob["columns"], table=parent.table)
    chosen = mt.simulate(m)
    cands = mt.candidates()
    best = next((mt.simulate({"method": "key", "columns": c["columns"]}) for c in cands if c["valid"]), None)
    if best is not None and best.get("columns") == chosen.get("columns") and m["method"] in ("key", "declared"):
        best = chosen
    mw = mt.warnings_for(chosen, best)
    need = [x for x in mw if x["needs_ack"]]
    if need and ACK_TOKEN not in acks and not all(x["code"] in acks for x in need):
        raise LakeError(400, "acknowledge_required",
                        " ".join(x["message"] for x in need) +
                        f" Send acknowledge: [\"{ACK_TOKEN}\"] to commit it anyway (it is recorded), or choose another match.",
                        warnings=need, chosen=chosen, best_candidate=best, table=parent.table)
    for x in mw:
        x["acknowledged"] = bool(x["needs_ack"])
    contract = mt.contract(target)
    mt.materialise(m, "_m")
    # F-X1 M1: the new rows' ids are taken atomically on the root counter (UPSERT ... RETURNING), so sibling
    # uploads committed at the same time can never be given the same range.
    n_new = int(w.con.execute("SELECT count(*) FROM (SELECT __fr FROM _n) n LEFT JOIN _m m USING (__fr)"
                              " WHERE m.pid IS NULL").fetchone()[0])
    nxt = reserve_row_ids(db, parent.root, parent.table, parent.next_row_id(db), n_new) if n_new else parent.next_row_id(db)
    w.con.execute("CREATE OR REPLACE TEMP TABLE _ids AS SELECT n.__fr, m.pid,"
                  f" CASE WHEN m.pid IS NOT NULL THEN m.pid ELSE {int(nxt)} + row_number() OVER"
                  " (PARTITION BY m.pid IS NULL ORDER BY n.__fr) - 1 END::BIGINT AS rid"
                  " FROM (SELECT __fr FROM _n) n LEFT JOIN _m m USING (__fr)")
    reasons = {c["name"]: c["reason"] for c in t["schema"] if c.get("reason")}
    cols = ", ".join(f"n.{_q(c['name'])}" for c in t["schema"])
    w.write(parent.table, f"SELECT i.rid AS {ROW_ID}, {cols} FROM _n n JOIN _ids i USING (__fr)", reasons=reasons,
            source_file=filename)
    new_cols = {c["name"] for c in t["schema"]}
    for k in _parent_keys(db, parent):
        if all(c in new_cols for c in k["columns"]):
            w.key(parent.table, k["columns"], kind=k["kind"], declared_by=k["declared_by"])
    if m["method"] in ("key", "declared"):  # the chosen key is saved as a confirmed key (§5.3 step 3)
        w.key(parent.table, m["columns"], kind="primary", declared_by=by)
    rows = w.con.execute("SELECT rid, pid FROM _ids ORDER BY rid").fetchall()
    rids = [int(r[0]) for r in rows]
    scores = [[",".join(c["columns"]), f"{c['score']:.4f}"] for c in cands]
    rh = mt.simulate({"method": "row_hash"}) if m["method"] != "row_hash" else chosen
    if rh.get("valid"):
        scores.append(["row_hash", f"{rh['matched_share']:.4f}"])
    rec = {"method": m["method"], **({"columns": m["columns"]} if m.get("columns") else {}), "confirmed_by": by,
           "matched_by": chosen["label"], "candidates": scores,
           **{k: chosen[k] for k in ("matched", "changed", "added", "removed", "holdout_identity_lost", "parent_holdout")},
           "acknowledged": [x["code"] for x in mw if x["needs_ack"]], "warnings": [x["code"] for x in mw],
           "parent": parent.vid, "parent_storage": parent.storage, "next_row_id": int(nxt),
           **({"best_candidate": {"columns": best["columns"], "matched": best["matched"],
                                  "holdout_identity_lost": best["holdout_identity_lost"]}} if best else {})}
    w.extra_ops.append({"op": "upload_version", "table": parent.table, "match": rec})
    # P09 (§5.7): the parent's split carried by join on __row_id, committed with the version (no JSON bridge)
    from . import splitstore as SS
    from .. import config as _cfg

    split_rec = SS.carry_into(w, parent.table, parent.ids, parent.split, _cfg.HOLDOUT_SHARE,
                              target_in_child=parent.target in new_cols, parent_vid=parent.vid) if parent.split else None
    if split_rec:
        from .derive import no_floats

        w.extra_ops.append(no_floats({"op": "split_carried", "table": parent.table, **split_rec}))
    return rec, mw, contract, chosen, rids


def _parent_keys(db, parent: "_Parent") -> list[dict[str, Any]]:
    if parent.storage != "parquet":
        return []
    with db.connect() as con:
        return [{"columns": json.loads(r["columns_json"]), "kind": r["kind"], "declared_by": r["declared_by"]}
                for r in con.execute("SELECT * FROM table_keys WHERE version_id = ? AND table_name = ?", (parent.vid, parent.table))]


# --------------------------------------------------------------------------- P12: several tables (files, Excel sheets)

ACTIONS = ("new", "rewrite", "append", "delta", "carry", "drop", "skip")


def _parse_tables_plan(v: Any) -> dict[str, dict[str, Any]]:
    """PATCH {tables: {name: {action?, types?, formats?, match?, key?}}}: what happens to each table. `action` for an
    uploaded table: new | rewrite (match it to the parent's table, P06) | append (its rows added to the parent's) |
    delta (P13, §5.6: insert / update / delete rows of the parent's table by `key`, from the `op_column`, default
    `_op`, mapped by `ops`; a new column needs `allow_new_columns`) | skip (leave it out); for a parent table not
    uploaded: carry (the default) | drop."""
    from ..dataset_versions import parse_match

    if v is None:
        return {}
    if not isinstance(v, dict):
        raise LakeError(400, "bad_plan", "tables is {table name: {action, types, formats, match, key}}.")
    out: dict[str, dict[str, Any]] = {}
    for name, spec in v.items():
        if not isinstance(name, str) or not name.strip():
            raise LakeError(400, "bad_plan", "A table name is text.")
        spec = spec or {}
        if not isinstance(spec, dict):
            raise LakeError(400, "bad_plan", f"tables.{name} is an object.")
        unknown = set(spec) - {"action", "types", "formats", "match", "key", "op_column", "ops", "allow_new_columns"}
        if unknown:
            raise LakeError(400, "bad_plan", f"tables.{name}: unknown field(s) {sorted(unknown)}; use action, types, formats, "
                                             "match, key (and op_column, ops, allow_new_columns for a delta).")
        e: dict[str, Any] = {}
        if spec.get("action") is not None:
            a = str(spec["action"]).strip().lower()
            if a not in ACTIONS:
                raise LakeError(400, "bad_action", f"tables.{name}.action is one of {', '.join(ACTIONS)}.")
            e["action"] = a
        if spec.get("types"):
            t = spec["types"]
            if not isinstance(t, dict) or any(str(x).upper() not in DECLARABLE for x in t.values()):
                raise LakeError(400, "bad_type", f"tables.{name}.types: declared types are one of {', '.join(DECLARABLE)}.")
            e["types"] = {str(c): str(x).upper() for c, x in t.items()}
        if spec.get("formats"):
            fm = spec["formats"]
            if not isinstance(fm, dict) or not all(isinstance(x, str) and "%" in x for x in fm.values()):
                raise LakeError(400, "bad_plan", f"tables.{name}.formats is {{column: strptime format}}.")
            e["formats"] = {str(c): x for c, x in fm.items()}
        if spec.get("match"):
            try:
                e["match"] = parse_match(spec["match"], name)
            except ValueError as ex:
                raise LakeError(400, "bad_match", f"tables.{name}.match: {ex}") from None
        if spec.get("key") is not None:
            k = spec["key"]
            k = [k] if isinstance(k, str) else k
            if not isinstance(k, list) or not k or not all(isinstance(c, str) and c for c in k):
                raise LakeError(400, "bad_key", f"tables.{name}.key is a column or a list of columns.")
            e["key"] = list(k)
        if spec.get("op_column") is not None:  # P13: a delta's operation column (default _op)
            if not isinstance(spec["op_column"], str) or not spec["op_column"].strip():
                raise LakeError(400, "bad_plan", f"tables.{name}.op_column is a column name.")
            e["op_column"] = spec["op_column"].strip()
        if spec.get("ops") is not None:
            from ..dataset_versions import parse_op_map

            try:
                e["ops"] = parse_op_map(spec["ops"])
            except ValueError as ex:
                raise LakeError(400, "bad_plan", f"tables.{name}.{ex}") from None
        if spec.get("allow_new_columns") is not None:
            e["allow_new_columns"] = bool(spec["allow_new_columns"])
        out[name.strip()] = e
    return out


def _parse_relations(v: Any) -> list[dict[str, Any]]:
    """[{name?, from: {table, columns}, to: {table, columns}, cardinality?: 'n:1' | '1:1'}] (or the flat
    from_table / from_columns / to_table / to_columns form)."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise LakeError(400, "bad_relation", "relations is a list of {from: {table, columns}, to: {table, columns}}.")
    out = []
    for r in v:
        out.append(norm_relation(r))
    names = [r["name"] for r in out]
    if len(set(names)) != len(names):
        raise LakeError(400, "bad_relation", "Two relations have the same name.")
    return out


def norm_relation(r: Any, name: str | None = None) -> dict[str, Any]:
    if not isinstance(r, dict):
        raise LakeError(400, "bad_relation", "A relation is {from: {table, columns}, to: {table, columns}, cardinality}.")
    fr = r.get("from") if isinstance(r.get("from"), dict) else {"table": r.get("from_table"), "columns": r.get("from_columns")}
    to = r.get("to") if isinstance(r.get("to"), dict) else {"table": r.get("to_table"), "columns": r.get("to_columns")}
    sides = []
    for side in (fr, to):
        cols = side.get("columns")
        cols = [cols] if isinstance(cols, str) else cols
        if not isinstance(side.get("table"), str) or not side["table"] or not isinstance(cols, list) or not cols \
                or not all(isinstance(c, str) and c for c in cols):
            raise LakeError(400, "bad_relation", "A relation names a table and its column(s) on each side.")
        sides.append({"table": side["table"], "columns": list(cols)})
    if len(sides[0]["columns"]) != len(sides[1]["columns"]):
        raise LakeError(400, "bad_relation", "Both sides of a relation have the same number of columns.")
    card = str(r.get("cardinality") or "n:1")
    if card not in ("n:1", "1:1"):
        raise LakeError(400, "bad_relation", "cardinality is n:1 or 1:1 (an n:m relation is a join, P14).")
    nm = name or r.get("name") or f"{sides[0]['table']}_{sides[1]['table']}"
    if not isinstance(nm, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", nm):
        raise LakeError(400, "bad_relation", "A relation name is 1–64 letters, digits, '_', '.' or '-'.")
    return {"name": nm, "from": sides[0], "to": sides[1], "cardinality": card}


def _sources(tmp: Path, files: list[dict[str, Any]], plan: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The tables an upload holds, in order: one per CSV / Parquet file, one per non-empty sheet of a workbook
    (hidden sheets listed and left out unless the plan names them). Two sources that give the same table name are
    refused (400 duplicate_table): nothing is renamed silently."""
    from . import excel as X

    ptables = plan.get("tables") or {}
    srcs: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    for f in files:
        name = f.get("filename") or ""
        src = tmp / f["src"]
        with open(src, "rb") as fh:
            head = fh.read(8)
        is_x = bool(re.search(r"\.(xlsx|xlsm|xls)$", name, re.I)) or (
            head[:2] == b"PK" and not re.search(r"\.(csv|tsv|txt|parquet)$", name, re.I))
        if not is_x:
            t = _table_name({}, name) if len(files) > 1 else _table_name(plan, name)  # P13: one file may name its table
            if (ptables.get(t) or {}).get("action") == "skip":
                sheets.append({"file": name, "table": t, "skipped": "left out by the plan (action skip)"})
                continue
            srcs.append({"table": t, "f": f, "source_file": name, "source_sha256": f["sha256"], "file_n": f["n"]})
            continue
        if not f.get("bytes"):
            raise LakeError(400, "empty", f"{name or 'The file'} is empty.")
        info = X.read_workbook(src, tmp, str(f["n"]), filename=name)
        used = 0
        for sh in info:
            t, renamed = X.sheet_table_name(sh["sheet"], sh["index"])  # F-X3a m14: accents folded, no `data` collisions
            e = {"file": name, "sheet": sh["sheet"], "table": t, "hidden": sh["hidden"], "rows": sh["n_rows"],
                 "columns": sh["n_cols"], "reader": sh["reader"], **({"renamed": renamed} if renamed else {}),
                 **({"notes": sh["notes"]} if sh.get("notes") else {})}
            act = (ptables.get(t) or {}).get("action")
            if sh["empty"]:
                sheets.append({**e, "skipped": "empty sheet"})
                continue
            if act == "skip":
                sheets.append({**e, "skipped": "left out by the plan (action skip)"})
                continue
            if sh["hidden"] and act not in ("new", "rewrite", "append"):
                sheets.append({**e, "skipped": "hidden sheet: name it in the plan (tables: {%s: {action: new}}) to include it" % t})
                continue
            csvp = tmp / sh["csv"]
            ff = {"n": f"{f['n']}.s{sh['index']}", "src": sh["csv"], "filename": f"{t}.csv", "bytes": csvp.stat().st_size,
                  "sha256": f["sha256"], "utf8": True}
            srcs.append({"table": t, "f": ff, "sep": ",", "sheet": sh["sheet"], "source_file": name,
                         "source_sha256": f["sha256"], "file_n": f["n"],
                         "excel_warnings": X.note_warnings(sh["sheet"], sh.get("notes"))
                         + ([{"code": "sheet_renamed", "sheet": sh["sheet"], "table": t,
                              "message": f"Sheet '{sh['sheet']}' is table '{t}' ({renamed})."}] if renamed else [])})
            sheets.append({**e, "included": True})
            used += 1
        if not used:
            raise LakeError(400, "empty_workbook", f"{name} has no sheet with data (every sheet is empty, hidden or left out). "
                                                   "Fill a sheet (a header row and at least two rows) and upload again.")
    seen: dict[str, dict[str, Any]] = {}
    for s_ in srcs:
        if s_["table"] in seen:
            a, b = seen[s_["table"]], s_
            lab = lambda x: f"{x['source_file']}" + (f" sheet '{x['sheet']}'" if x.get("sheet") is not None else "")  # noqa: E731
            raise LakeError(400, "duplicate_table", f"{lab(a)} and {lab(b)} would both become table '{s_['table']}'. Rename "
                                                    "one of them (a sheet or a file) and upload again: nothing is renamed "
                                                    "silently.", table=s_["table"])
        seen[s_["table"]] = s_
    if not srcs:
        raise LakeError(400, "no_tables", "Nothing to import: every file or sheet was left out.")
    return srcs, sheets


def _label(s_: dict[str, Any]) -> str:
    return f"{s_['source_file']}" + (f", sheet '{s_['sheet']}'" if s_.get("sheet") is not None else "")


def _parent_tables(db, vid: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    with db.connect() as con:
        r = con.execute("SELECT id, root_id, storage, primary_table, target FROM datasets WHERE id = ?", (vid,)).fetchone()
        if not r:
            raise LakeError(404, "parent_not_found", f"No dataset version {vid}.")
        if r["storage"] != "parquet":
            raise LakeError(409, "parent_not_parquet", f"{vid} is a CSV-era version: continue it with one file (its one "
                                                       "table), or migrate it first (P10) to add tables.")
        tabs = {t["table_name"]: {"schema": json.loads(t["schema_json"]), "n_rows": int(t["n_rows"]), "sha": t["table_sha256"],
                                  "ordinal": t["ordinal"], "row_id_max": t["row_id_max"]}
                for t in con.execute("SELECT * FROM version_tables WHERE version_id = ? ORDER BY ordinal", (vid,))}
        keys: dict[str, list[list[str]]] = {}
        allk: dict[str, list[dict[str, Any]]] = {}
        for k in con.execute("SELECT table_name, columns_json, valid, kind FROM table_keys WHERE version_id = ?"
                             " ORDER BY kind, columns_json", (vid,)):
            if int(k[2]):
                keys.setdefault(k[0], []).append(json.loads(k[1]))
            allk.setdefault(k[0], []).append({"columns": json.loads(k[1]), "valid": bool(int(k[2])), "kind": k[3]})
        rels = [{"name": x["name"], "from": {"table": x["from_table"], "columns": json.loads(x["from_cols_json"])},
                 "to": {"table": x["to_table"], "columns": json.loads(x["to_cols_json"])}, "cardinality": x["cardinality"]}
                for x in con.execute("SELECT * FROM table_relations WHERE version_id = ? ORDER BY name", (vid,))]
        for t in tabs:
            tabs[t]["keys"] = keys.get(t, [])
            tabs[t]["all_keys"] = allk.get(t, [])
    out = dict(r)
    out["relations"] = rels
    return out, tabs


def _rel_sql(db, tmp: Path, parent_vid: str | None, t: dict[str, Any] | None, name: str) -> str:
    """The DuckDB relation of a table as the version will hold it (typed file, parent parts, or both for an append)."""
    from .. import frames

    if t is None or t["action"] in ("append",):
        lt = frames.lake_table(frames.Table(parent_vid, name))
        base = f"SELECT * EXCLUDE ({ROW_ID}) FROM {frames.lake_sql(lt)}"
        if t is None:
            return f"({base})"
        return f"({base} UNION ALL BY NAME SELECT * FROM read_parquet({_lit(tmp / t['typed'])}))"
    return f"(SELECT * FROM read_parquet({_lit(tmp / t['typed'])}))"


def _check_multi(db, store, dstore, tmp: Path, srcs: list[dict[str, Any]], sheets: list[dict[str, Any]],
                 plan: dict[str, Any], team_id: str) -> dict[str, Any]:
    """§5.1 steps 2–3 and 6 per table, plus what happens to each table of the parent (P12): new, rewrite (matched to
    the parent's table, P06's simulation), append (the parent's columns and types), carry, drop; the keys and
    relations the plan declares, checked on the typed files; relations suggested by shared key columns."""
    ptables = plan.get("tables") or {}
    pvid = plan.get("parent_version_id")
    prec, ptabs = _parent_tables(db, pvid) if pvid else ({}, {})
    names = [s_["table"] for s_ in srcs]
    primary = plan.get("primary_table") or (prec.get("primary_table") if pvid else None) or names[0]
    tables: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    notes: list[str] = []
    for i, s_ in enumerate(srcs):
        name = s_["table"]
        pt = ptables.get(name) or {}
        act = pt.get("action") or ("rewrite" if name in ptabs else "new")
        tb: list[dict[str, Any]] = []
        if act in ("carry", "drop"):
            tb.append({"code": "bad_action", "message": f"Table '{name}' is in the upload: its action is new, rewrite, append or "
                                                        "skip (carry / drop are for the parent's tables you did not upload)."})
            act = "rewrite" if name in ptabs else "new"
        if act in ("rewrite", "append", "delta") and name not in ptabs:
            tb.append({"code": "table_not_in_parent", "message": f"Table '{name}' is not in the parent version"
                                                                 f"{' ' + pvid if pvid else ' (this is a new dataset)'}: "
                                                                 f"it can only be new, not {act}."})
            act = "new"
        if act == "new" and name in ptabs:
            tb.append({"code": "table_exists", "message": f"The parent already has a table '{name}': rewrite it (match its rows) "
                                                          "or append to it."})
        top = name == (plan.get("primary_table") or names[0])  # top-level types / formats apply to the first (primary) table
        tplan = {"table": name, "types": pt.get("types") or (plan.get("types") if top else None),
                 "formats": pt.get("formats") or (plan.get("formats") if top else None)}
        soft = {c["name"]: _norm_type(c["type"]) for c in ptabs[name]["schema"]} if act in ("append", "delta") else None
        try:
            r = _check_file(db, tmp, s_["f"], tplan, team_id, typed_name=f"typed.{i}.parquet", sep=s_.get("sep"), soft_types=soft,
                            min_rows=1 if pvid else 2)
        except LakeError as e:
            raise LakeError(e.status, e.code, f"{_label(s_)}: {e.message}", table=name, **e.extra) from None
        t = r["tables"][0]
        t.update(name=name, source_file=s_["source_file"], source_sha256=s_["source_sha256"], typed=r["typed"], action=act,
                 kind=r["kind"])
        if s_.get("sheet") is not None:
            t["sheet"] = s_["sheet"]
        tb += r["blocks"]
        if act == "append":
            pcols = [c["name"] for c in ptabs[name]["schema"]]
            fcols = [c["name"] for c in t["schema"]]
            miss, extra = [c for c in pcols if c not in fcols], [c for c in fcols if c not in pcols]
            if miss or extra:
                tb.append({"code": "append_columns", "message": f"Rows appended to '{name}' must have its columns: "
                                                                + (f"missing {', '.join(miss)}" if miss else "")
                                                                + ("; " if miss and extra else "")
                                                                + (f"not in the table {', '.join(extra)}" if extra else "")
                                                                + ". Rewrite the table instead, or fix the file."})
            else:
                ptype = {c["name"]: _norm_type(c["type"]) for c in ptabs[name]["schema"]}
                amb = [c["name"] for c in t["schema"] if str(c.get("reason") or "").startswith("ambiguous day/month")
                       and ptype[c["name"]] in ("DATE", "TIMESTAMP")]
                if amb:  # F-X3a M3
                    tb.append({"code": "date_ambiguous", "columns": amb,
                               "message": f"Rows appended to '{name}' have dates in {', '.join(amb)} that could be dd/mm or "
                                          f"mm/dd. Send the format in the plan: tables.{name}.formats {{\"{amb[0]}\": \"%d/%m/%Y\"}}."})
                bad = [f"{c['name']} ({c['type']}, the table has {ptype[c['name']]})" for c in t["schema"]
                       if _norm_type(c["type"]) != ptype[c["name"]] and c["name"] not in amb]
                if bad:
                    tb.append({"code": "append_type", "message": f"These columns of '{name}' do not read as the table's types: "
                                                                 f"{', '.join(bad)}."})
            t["append"] = {"parent_rows": ptabs[name]["n_rows"], "rows_added": t["n_rows"],
                           "rows_after": ptabs[name]["n_rows"] + t["n_rows"]}
        if act == "delta":  # P13 (§5.6): the key, the columns and types; the counts come from the parent below
            t["delta"] = _delta_check(name, pt, ptabs[name], t, tb)
        warnings += [{**w_, "table": name} for w_ in r["warnings"] + (s_.get("excel_warnings") or [])]
        blocks += [{**b_, "table": name} for b_ in tb]
        notes += [f"{name}: {n}" for n in r["notes"]]
        t["blocked"] = bool(tb)
        if tb:
            t["typed"] = None
        tables.append(t)
    by_name = {t["name"]: t for t in tables}
    carried = [n for n in ptabs if n not in by_name and (ptables.get(n) or {}).get("action") != "drop"]
    dropped = [n for n in ptabs if n not in by_name and (ptables.get(n) or {}).get("action") == "drop"]
    for n, spec in ptables.items():
        if n not in by_name and n not in ptabs and spec.get("action") not in (None, "skip"):
            blocks.append({"code": "table_unknown", "table": n, "message": f"The plan names table '{n}', which is neither in the "
                                                                          "upload nor in the parent."})
    final = [n for n in ptabs if n not in dropped] + [n for n in names if n not in ptabs]
    if primary not in final:
        blocks.append({"code": "primary_table", "message": f"The primary table '{primary}' is not in the new version."})
    target = plan.get("target")
    if target:
        pcols = [c["name"] for c in (by_name[primary]["schema"] if primary in by_name else ptabs.get(primary, {}).get("schema", []))]
        if target not in pcols:
            hold = [n for n in final if target in [c["name"] for c in (by_name[n]["schema"] if n in by_name else ptabs[n]["schema"])]]
            blocks.append({"code": "target_unknown", "message": f"Column '{target}' is not in the primary table '{primary}'"
                                                                + (f"; it is in {', '.join(hold)}: set primary_table" if hold else "")
                                                                + "."})
    for t in tables:  # P06 per table: candidates, every method simulated, what the commit needs
        if t["action"] == "rewrite" and not t["blocked"]:
            mini = {"tables": [t], "typed": t["typed"], "blocks": []}
            tp = {**plan, "table": t["name"], "match": (ptables.get(t["name"]) or {}).get("match"),
                  "target": target if t["name"] == primary else None}
            t["next_version"] = _check_next(db, store, dstore, tmp, mini, tp)
    con = duck.connect("job", **_duck_kw())
    keys_out, rels_out, sugg = [], [], []
    try:
        delta_rel: dict[str, str] = {}
        for i, t in enumerate(tables):  # P13: each delta applied to its parent table (counts, warnings, the result)
            if t["action"] == "delta" and t.get("typed"):
                d, par, allow = _delta_for(db, store, dstore, con, pvid, t, tmp, ptables.get(t["name"]) or {}, f"_dl{i}")
                lossy = d.lossy()
                if lossy:  # F-X3a M2: a value the parent's type would change is refused, never rounded
                    blocks.append(_lossy_block(t["name"], lossy))
                    t["blocked"], t["typed"] = True, None
                    continue
                t["delta"].update(d.counts, mode=d.mode(allow))
                warnings += [{**x, "table": t["name"]} for x in d.warnings()]
                delta_rel[t["name"]] = f"({d.result_sql(par.next_row_id(db), allow)})"
        rel = {n: delta_rel.get(n) or _rel_sql(db, tmp, pvid, by_name.get(n), n) for n in final
               if n not in by_name or by_name[n].get("typed")}
        for t in tables:  # F-X3a M2: an append's typed values written in the parent's exact types (DECIMAL, INTEGER…)
            if t["action"] == "append" and t.get("typed"):
                pt_ = {c["name"]: c["type"] for c in ptabs[t["name"]]["schema"]}
                lossy = _dv_lossy(con, f"SELECT * FROM read_parquet({_lit(tmp / t['typed'])})",
                                  {c["name"]: c["type"] for c in t["schema"] if c["name"] in pt_}, pt_)
                if lossy:
                    blocks.append(_lossy_block(t["name"], lossy))
        cols_of = {n: [c["name"] for c in (by_name[n]["schema"] if n in by_name else ptabs[n]["schema"])] for n in final}
        for n, spec in ptables.items():
            if spec.get("key") and n in rel:
                miss = [c for c in spec["key"] if c not in cols_of[n]]
                if miss:
                    blocks.append({"code": "key_column_missing", "table": n, "message": f"'{n}' has no column(s) {', '.join(miss)}."})
                    continue
                k = _key_stats(con, rel[n], spec["key"])
                keys_out.append({"table": n, "columns": spec["key"], **k})
                if not k["valid"]:
                    blocks.append({"code": "key_invalid", "table": n, "message": f"Key {', '.join(spec['key'])} of '{n}' is not "
                                                                                 f"unique and present: {k['n_duplicates']:,} repeated, "
                                                                                 f"{k['n_nulls']:,} missing."})
        declared = {(r["from"]["table"], tuple(r["from"]["columns"]), r["to"]["table"]) for r in plan.get("relations") or []}
        for r in plan.get("relations") or []:
            a, b = r["from"]["table"], r["to"]["table"]
            miss = [f"{a}.{c}" for c in r["from"]["columns"] if c not in cols_of.get(a, [])] + \
                [f"{b}.{c}" for c in r["to"]["columns"] if c not in cols_of.get(b, [])]
            if a not in final or b not in final or miss:
                blocks.append({"code": "relation_unknown", "relation": r["name"], "message": f"Relation {r['name']} names "
                                                                                             f"{'tables or ' if a not in final or b not in final else ''}"
                                                                                             f"columns the version will not have: {', '.join(miss) or a + ' / ' + b}."})
                continue
            if a not in rel or b not in rel:
                continue
            st = _relation_stats(con, rel[a], r["from"]["columns"], rel[b], r["to"]["columns"])
            rels_out.append({**r, **st})
            if not st["to_unique"]:
                blocks.append({"code": "relation_to_not_unique", "relation": r["name"],
                               "message": f"Relation {r['name']}: {b}.{', '.join(r['to']['columns'])} repeats ({st['to_duplicates']:,} "
                                          f"repeated values), so it is not {r['cardinality']}. Declare it from the other side, or "
                                          "join with an aggregate (P14)."})
        sugg = _suggest_relations(con, rel, cols_of, {n: (([ptables[n]["key"]] if (ptables.get(n) or {}).get("key") else [])
                                                         + (ptabs.get(n, {}).get("keys") or [])) for n in final}, declared)
        if pvid:  # F-X3a M1: what the parent declared and this upload would break, said at the check
            changed = {t["name"] for t in tables if t["action"] in ("delta", "append", "rewrite")}
            warnings += _integrity_warnings(con, prec, ptabs, rel, cols_of, changed, final, ptables, plan)
    finally:
        _close(con)
    out = {"kind": "multi", "tables": tables, "primary_table": primary, "carried": carried, "dropped": dropped,
           "keys": keys_out, "relations": rels_out, "suggested_relations": sugg, "sheets": sheets,
           "warnings": warnings, "blocks": blocks, "notes": notes,
           "typed": (by_name.get(primary) or {}).get("typed") if not blocks else None}
    if pvid:
        out["parent"] = {"version_id": pvid, "root_id": prec.get("root_id") or pvid, "primary_table": prec.get("primary_table"),
                         "tables": {n: {"n_rows": x["n_rows"], "table_sha256": x["sha"]} for n, x in ptabs.items()}}
        rw = [t["name"] for t in tables if t["action"] == "rewrite"]
        if rw:
            out["commit_needs"] = {"match": {n: True for n in rw},
                                   "note": "POST …/commit {match: {<table>: {method: key|declared|row_hash|none, columns}}} "
                                           "(one rewritten table: the bare {method, …} works too), or tables.<t>.match in "
                                           "the plan."}
    return out


def _delta_check(name: str, spec: dict[str, Any], ptab: dict[str, Any], t: dict[str, Any], tb: list[dict[str, Any]]) -> dict[str, Any]:
    """P13: a delta table's key (the plan's `key`, else the parent table's confirmed key: 400 delta_key_required when
    neither), its columns against the parent's (a type change blocks; a new column blocks unless allow_new_columns)."""
    from ..dataset_versions import DELTA_OP_COLUMN

    key, src = spec.get("key"), "plan"
    allk = ptab.get("all_keys") or [{"columns": k, "valid": True, "kind": "primary"} for k in ptab.get("keys") or []]
    prim = [k for k in allk if k["kind"] == "primary"]
    broken = [k for k in prim if not k["valid"]]
    if not key and broken:  # F-X3a M1: the table's primary key no longer holds: never fall back to another key silently
        bk = broken[0]["columns"]
        raise LakeError(400, "delta_key_required", f"A delta of '{name}' is applied by key, and the primary key of '{name}' "
                                                   f"({', '.join(bk)}) is no longer valid in this version (it repeats or has "
                                                   f"missing values). Name the key in the plan (tables: {{{name}: {{action: "
                                                   "delta, key: [...]}}}}) or declare a valid one first "
                                                   f"(PUT …/tables/{name}/key).", table=name, invalid_key=bk)
    if not key:
        valid = [k for k in prim if k["valid"]] or [k for k in allk if k["valid"]]
        if valid:
            key, src = valid[0]["columns"], "parent"
    if not key:
        raise LakeError(400, "delta_key_required", f"A delta of '{name}' is applied by key, and '{name}' has no confirmed key: "
                                                   f"name it in the plan (tables: {{{name}: {{action: delta, key: [...]}}}}) "
                                                   f"or declare one first (PUT …/tables/{name}/key).", table=name)
    opc = spec.get("op_column") or DELTA_OP_COLUMN
    ptype = {c["name"]: _norm_type(c["type"]) for c in ptab["schema"]}
    fcols = [c for c in t["schema"] if c["name"] != opc]
    bad = [f"{c['name']} ({c['type']}, the table has {ptype[c['name']]})" for c in fcols
           if c["name"] in ptype and _norm_type(c["type"]) != ptype[c["name"]]]
    amb = [c["name"] for c in fcols if str(c.get("reason") or "").startswith("ambiguous day/month") and c["name"] in ptype]
    if amb:  # F-X3a M3: as on a plain upload, ambiguous slash dates are not read as dates without a format
        tb.append({"code": "date_ambiguous", "columns": amb,
                   "message": f"The delta of '{name}' has dates in {', '.join(amb)} that could be dd/mm or mm/dd (no day above "
                              f"12), and the table's type is a date. Send the format in the plan: tables.{name}.formats "
                              f"{{\"{amb[0]}\": \"%d/%m/%Y\"}} (or \"%m/%d/%Y\")."})
        bad = [b for b in bad if b.split(" (")[0] not in amb]
    if bad:
        tb.append({"code": "delta_type", "message": f"These columns of the delta of '{name}' do not read as the table's types: "
                                                    f"{', '.join(bad)}."})
    if not spec.get("op_column") and opc in ptype and any(c["name"] == opc for c in t["schema"]):
        # F-X3a (X3 m12): the table already has a data column named _op: never consume it as operations unannounced
        tb.append({"code": "delta_op_column_ambiguous", "column": opc,
                   "message": f"'{name}' already has a data column {opc}, so the delta's {opc} could be data or the operation "
                              f"of each row. Say which: tables.{name}.op_column = \"{opc}\" to read it as operations (I / U / D), "
                              "or name another op column (or send no op column: every row is an upsert)."})
    new = [c["name"] for c in fcols if c["name"] not in ptype]
    if new and not spec.get("allow_new_columns"):
        tb.append({"code": "delta_new_columns", "columns": new,
                   "message": f"The delta of '{name}' has column(s) the table does not: {', '.join(new)}. Send "
                              f"tables.{name}.allow_new_columns = true to add them (existing rows get missing values), or "
                              "drop them from the file."})
    return {"key": list(key), "key_from": src, "op_column": opc if any(c["name"] == opc for c in t["schema"]) else None}


def _delta_for(db, store, dstore, con, pvid: str, t: dict[str, Any], tmp: Path, spec: dict[str, Any], tag: str):
    """(Delta classified on `con`, the parent table, allow_new_columns); a refusal is a 400 (duplicate keys listed)."""
    from ..dataset_versions import Delta

    name = t["name"]
    par = _Parent(db, store, dstore, pvid, name)
    d = Delta(con, par.sql, _new_sql(tmp / t["typed"]), t["delta"]["key"], op_column=spec.get("op_column"),
              op_map=spec.get("ops"), tag=tag, holdout_ids=par.holdout_ids)
    prob = d.problems()
    if prob:
        if prob.get("keys"):
            _mask_keys(db, par.root, name, prob["columns"], prob["keys"])
        raise LakeError(400, prob["code"], f"Delta of '{name}': {prob['message']}", table=name,
                        **{k: v for k, v in prob.items() if k not in ("code", "message")})
    d.classify()
    return d, par, bool(spec.get("allow_new_columns"))


#: F-X3a M1: check warnings that the commit needs acknowledged by code (acknowledge: ["key_invalidated", …])
INTEGRITY_ACKS = ("key_invalidated", "relation_dropped")


def _dv_lossy(con, rel_sql: str, src: dict[str, str], dst: dict[str, str]) -> list[dict[str, Any]]:
    from ..dataset_versions import lossy_columns

    return lossy_columns(con, rel_sql, src, dst)


def _lossy_block(table: str, lossy: list[dict[str, Any]]) -> dict[str, Any]:
    """F-X3a M2: one block for a table, a count and examples per column."""
    parts = [f"{x['column']}: {x['n']:,} value(s) ({x['from']} → {x['to']}, e.g. {', '.join(repr(e) for e in x['examples'])})"
             for x in lossy]
    return {"code": "lossy_cast", "table": table, "columns": lossy,
            "message": f"Values of '{table}' would change when written in the table's column types: {'; '.join(parts)}. "
                       "Nothing is rounded or truncated silently: fix the values, or change the table's types first."}


def _integrity_warnings(con, prec: dict[str, Any], ptabs: dict[str, dict[str, Any]], rel: dict[str, str],
                        cols_of: dict[str, list[str]], changed: set[str], final: list[str], ptables: dict[str, Any],
                        plan: dict[str, Any]) -> list[dict[str, Any]]:
    """F-X3a M1: the parent's valid keys and its relations, checked on the tables as this upload leaves them. A key that
    stops being unique and present (`key_invalidated`) and a relation the version would lose (`relation_dropped`:
    its `to` side repeats, or a column is gone) need the commit's acknowledge; a relation lost because the plan drops
    one of its tables is listed too (the drop is the person's own action, no acknowledgement)."""
    out: list[dict[str, Any]] = []
    for n in sorted(changed):
        if n not in rel:
            continue
        own = (ptables.get(n) or {}).get("key")
        for k in ptabs.get(n, {}).get("all_keys") or []:
            if not k["valid"] or (own and list(own) == k["columns"]):
                continue
            gone = [c for c in k["columns"] if c not in cols_of.get(n, [])]
            if gone:
                out.append({"code": "key_invalidated", "table": n, "columns": k["columns"], "kind": k["kind"], "needs_ack": True,
                            "why": "column_gone",
                            "message": f"'{n}' loses its {k['kind']} key {', '.join(k['columns'])}: the new table has no "
                                       f"{', '.join(gone)}. Send acknowledge: [\"key_invalidated\"] to commit it anyway."})
                continue
            st = _key_stats(con, rel[n], k["columns"])
            if not st["valid"]:
                out.append({"code": "key_invalidated", "table": n, "columns": k["columns"], "kind": k["kind"], "needs_ack": True,
                            "why": "not_unique", "n_duplicates": st["n_duplicates"], "n_nulls": st["n_nulls"],
                            "message": f"After this upload, {', '.join(k['columns'])} is no longer a key of '{n}' "
                                       f"({st['n_duplicates']:,} repeated, {st['n_nulls']:,} missing). Its {k['kind']} key "
                                       "is kept but marked invalid, and later deltas must name their key. Send "
                                       "acknowledge: [\"key_invalidated\"] to commit it anyway, or fix the file."})
    redeclared = {r["name"] for r in plan.get("relations") or []}
    for r in prec.get("relations") or []:
        if r["name"] in redeclared:
            continue
        a, b = r["from"]["table"], r["to"]["table"]
        base = {"code": "relation_dropped", "relation": r["name"], "from": r["from"], "to": r["to"], "table": b}
        if a not in final or b not in final:
            out.append({**base, "needs_ack": False, "why": "table_dropped",
                        "message": f"Relation {r['name']} ({a} → {b}) is left out: the plan drops "
                                   f"{a if a not in final else b}."})
            continue
        if a not in changed and b not in changed:
            continue
        gone = [f"{a}.{c}" for c in r["from"]["columns"] if c not in cols_of.get(a, [])] + \
            [f"{b}.{c}" for c in r["to"]["columns"] if c not in cols_of.get(b, [])]
        if gone:
            out.append({**base, "needs_ack": True, "why": "column_gone",
                        "message": f"Relation {r['name']} ({a} → {b}) would be dropped: {', '.join(gone)} is gone. Send "
                                   "acknowledge: [\"relation_dropped\"] to commit it anyway."})
            continue
        if a not in rel or b not in rel:
            continue
        try:
            st = _relation_stats(con, rel[a], r["from"]["columns"], rel[b], r["to"]["columns"])
        except Exception:  # noqa: BLE001 - the columns no longer compare
            out.append({**base, "needs_ack": True, "why": "relation_types",
                        "message": f"Relation {r['name']} ({a} → {b}) would be dropped: its columns no longer compare. Send "
                                   "acknowledge: [\"relation_dropped\"] to commit it anyway."})
            continue
        if not st["to_unique"]:
            out.append({**base, "needs_ack": True, "why": "relation_to_not_unique", "to_duplicates": st["to_duplicates"],
                        "message": f"Relation {r['name']} ({a} → {b}, {r['cardinality']}) would be dropped: "
                                   f"{b}.{', '.join(r['to']['columns'])} would repeat ({st['to_duplicates']:,} repeated "
                                   "value(s)), so it is no longer what each row points at. Send acknowledge: "
                                   "[\"relation_dropped\"] to commit it anyway, or fix the file."})
    return out


def _need_integrity_acks(chk: dict[str, Any], acks: list[str]) -> list[dict[str, Any]]:
    """F-X3a M1: the check's key_invalidated / relation_dropped warnings the commit has not acknowledged → 400."""
    need = [x for x in chk.get("warnings") or [] if x.get("needs_ack") and x.get("code") in INTEGRITY_ACKS]
    missing = sorted({x["code"] for x in need if x["code"] not in acks})
    if missing:
        raise LakeError(400, "acknowledge_required",
                        " ".join(x["message"] for x in need if x["code"] in missing),
                        acknowledge=missing, warnings=[{k: v for k, v in x.items() if k != "message"} | {"message": x["message"]}
                                                       for x in need if x["code"] in missing])
    return need


def _mask_keys(db, root: str, table: str, cols: list[str], keys: list[dict[str, Any]]) -> None:
    """Key values listed in a refusal, masked where the column is PII-tagged (§6.3), like _mask."""
    from . import mask as MK

    with db.connect() as con:
        tags = {r[0]: r[1] for r in con.execute(
            "SELECT column_name, mask FROM column_tags WHERE root_id = ? AND table_name = ? AND mask <> 'none'", (root, table))}
        salt = MK.team_salt(con, MK.root_and_team(con, root)[1]) if tags else ""
    for k in keys:
        k["value"] = [MK.mask_value(v, tags[c], salt) if c in tags else v for c, v in zip(cols, k["value"])]


def _key_stats(con, rel: str, cols: list[str]) -> dict[str, Any]:
    cs = ", ".join(_q(c) for c in cols)
    nulls = " OR ".join(f"{_q(c)} IS NULL" for c in cols)
    n, nd, nn = con.execute(f"SELECT count(*), count(DISTINCT ({cs})), count(*) FILTER (WHERE {nulls}) FROM {rel}").fetchone()
    return {"valid": bool(nd == n and nn == 0), "n_rows": int(n), "n_duplicates": int(n - nd), "n_nulls": int(nn)}


def _relation_stats(con, a: str, acols: list[str], b: str, bcols: list[str]) -> dict[str, Any]:
    """Orphans (from-rows with a value that matches no to-row; rows with a missing value are counted apart) and
    whether the to side is unique."""
    on = " AND ".join(f"f.{_q(x)} = t.{_q(y)}" for x, y in zip(acols, bcols))
    nn = " AND ".join(f"f.{_q(x)} IS NOT NULL" for x in acols)
    n_from, n_null = con.execute(f"SELECT count(*), count(*) FILTER (WHERE NOT ({nn})) FROM {a} f").fetchone()
    orph = con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} f WHERE {nn}) f ANTI JOIN {b} t ON {on}").fetchone()[0]
    bs = ", ".join(_q(c) for c in bcols)
    n_to, nd = con.execute(f"SELECT count(*), count(DISTINCT ({bs})) FROM {b}").fetchone()
    return {"n_from_rows": int(n_from), "n_orphans": int(orph), "n_from_missing": int(n_null),
            "coverage": f"{(1 - orph / max(1, n_from - n_null)):.4f}", "to_unique": bool(nd == n_to), "to_duplicates": int(n_to - nd)}


KEYISH = re.compile(r"(^id$|_id$|id_|_key$|^key$|_no$|_num$|_code$|_ref$)", re.I)


def _suggest_relations(con, rel: dict[str, str], cols_of: dict[str, list[str]], keys: dict[str, list[list[str]]],
                       declared: set) -> list[dict[str, Any]]:
    """Relations nobody declared yet, proposed (never stored): a column of table A with the name of a one-column key of
    table B (declared, or unique and id-like), with the orphan count. At most 12 pairs are scanned."""
    out = []
    tried = 0
    for b, bcols in cols_of.items():
        if b not in rel:
            continue
        cands = {k[0] for k in keys.get(b) or [] if len(k) == 1} | {c for c in bcols if KEYISH.search(c)}
        for c in sorted(cands):
            for a, acols in cols_of.items():
                if a == b or a not in rel or c not in acols or (a, (c,), b) in declared or tried >= 12:
                    continue
                tried += 1
                try:
                    st = _relation_stats(con, rel[a], [c], rel[b], [c])
                except Exception:  # noqa: BLE001 - incomparable types: no suggestion
                    continue
                if not st["to_unique"] or st["n_from_rows"] == st["n_from_missing"]:
                    continue
                out.append({"name": f"{a}_{b}", "from": {"table": a, "columns": [c]}, "to": {"table": b, "columns": [c]},
                            "cardinality": "n:1", **st, "suggested": True})
    return out


def _commit_multi(db, store, dstore, u: dict[str, Any], chk: dict[str, Any], plan: dict[str, Any], tmp: Path, *, by: str,
                  reason: str, make_default: bool | None, match: Any, acknowledge: Any) -> tuple[dict[str, Any], bool]:
    """P12: one version from several tables (§5.2): each uploaded table new / rewritten (P06's match per table) /
    appended (an 'append' part: the parent's parts are reused, the new rows get fresh ids); every parent table not
    uploaded is carried by reference (zero bytes) unless dropped; the plan's keys and relations are declared."""
    from .. import config as _cfg
    from ..dataset_versions import parse_match
    from . import splitstore as SS
    from .derive import no_floats

    up = u["id"]
    files = u["files"] or []
    pvid = plan.get("parent_version_id")
    ptables = plan.get("tables") or {}
    tabs = chk["tables"]
    for t in tabs:
        if not t.get("typed") or not (tmp / t["typed"]).exists():
            raise LakeError(409, "check_required", "A checked file is gone from staging; check again.")
    acks = acknowledge if isinstance(acknowledge, list) else [acknowledge] if isinstance(acknowledge, str) and acknowledge else []
    acks = [str(a) for a in acks]
    integrity = _need_integrity_acks(chk, acks)  # F-X3a M1: a broken key / dropped relation is acknowledged first
    rewrites = [t for t in tabs if t["action"] == "rewrite"]
    matches: dict[str, dict[str, Any]] = {}
    for t in rewrites:
        raw = None
        if isinstance(match, dict) and "method" not in match:
            raw = match.get(t["name"])
        elif isinstance(match, dict) and len(rewrites) == 1:
            raw = match
        if raw is None:
            raw = (ptables.get(t["name"]) or {}).get("match")
        try:
            m = parse_match(raw, t["name"])
        except ValueError as e:
            raise LakeError(400, "bad_match", f"{t['name']}: {e}", table=t["name"]) from None
        if m is None:
            nv = t.get("next_version") or {}
            raise LakeError(400, "match_required",
                            f"Say how the rows of '{t['name']}' are matched to the parent's: match = {{\"{t['name']}\": {{method: "
                            "key, columns: [...]}} (or declared / row_hash / none). Nothing is guessed. The candidates "
                            "and what each method would do are below (and in the check).", table=t["name"],
                            candidates=nv.get("candidates") or [], methods=nv.get("methods") or [], preselect=nv.get("preselect"))
        matches[t["name"]] = m
    sources = [{"filename": f["filename"], "sha256": f["sha256"], "bytes": f["bytes"], "kept": True} for f in files]
    primary = chk["primary_table"]
    by_name = {t["name"]: t for t in tabs}
    prec, ptabs = _parent_tables(db, pvid) if pvid else ({}, {})
    order = [n for n in ptabs if n in by_name or n in chk.get("carried", [])] + [t["name"] for t in tabs if t["name"] not in ptabs]
    pschema = {c["name"] for c in (by_name[primary]["schema"] if primary in by_name else ptabs.get(primary, {}).get("schema", []))}
    target = plan.get("target") or prec.get("target")
    target = target if target in pschema else None
    fname = files[0]["filename"] if files else "upload"
    name = plan.get("name") or re.sub(r"\.(csv|tsv|txt|parquet|xlsx|xlsm)$", "", Path(fname or "upload").name, flags=re.I)
    w = VersionWriter(db, store, op="upload_version" if pvid else "upload", by=by, parent_id=pvid,
                      name=None if pvid else name, project_id=None if pvid else (plan.get("project_id") or None),
                      reason=reason or plan.get("reason") or "", job_id=up, sources=sources, primary_table=primary,
                      duck_kw=_duck_kw())
    recs: dict[str, Any] = {}
    contracts: dict[str, Any] = {}
    deltas: dict[str, Any] = {}
    warns = [{k: v for k, v in x.items() if k != "examples"} for x in chk.get("warnings") or []]
    for x in warns:
        if x.get("needs_ack") and x.get("code") in INTEGRITY_ACKS:
            x["acknowledged"] = True
    # F-X3a M1: the writer refuses to leave out any other relation (the check and the commit read the same bytes)
    w.relations_acknowledged = {x["relation"] for x in integrity if x["code"] == "relation_dropped"}
    with w:
        done = w.committed_for_job()
        if done:
            _mark_committed(db, up, done)
            return _version(db, done, up), False
        for n in order:
            t = by_name.get(n)
            if t is None:
                w.carry(n)
                w.carry_splits(n)
                continue
            typed = tmp / t["typed"]
            reasons = {c["name"]: c["reason"] for c in t["schema"] if c.get("reason")}
            src = f"read_parquet({_lit(typed)}, file_row_number = true)"
            if t["action"] == "new":
                cols = ", ".join(_q(c["name"]) for c in t["schema"])
                start = reserve_row_ids(db, w.root_id, n, 0, t["n_rows"]) if pvid else 0
                w.write(n, f"SELECT ({int(start)} + file_row_number)::BIGINT AS {ROW_ID}, {cols} FROM {src}", reasons=reasons,
                        source_file=t["source_file"])
            elif t["action"] == "append":
                base = w._parent_table(n)
                start = reserve_row_ids(db, w.root_id, n, (base.row_id_max if base.row_id_max is not None else -1) + 1, t["n_rows"])
                casts = ", ".join(f"CAST({_q(c['name'])} AS {c['type']}) AS {_q(c['name'])}" for c in base.schema)
                try:
                    w.append(n, f"SELECT ({int(start)} + file_row_number)::BIGINT AS {ROW_ID}, {casts} FROM {src}",
                             source_file=t["source_file"])
                except LakeError:
                    raise
                except Exception as e:  # noqa: BLE001 - a value the parent's type cannot hold: 400 in words
                    log.info("append cast failed: %s", str(e)[:500])
                    raise LakeError(400, "append_type", f"A value in the rows appended to '{n}' does not fit the table's "
                                                        "column types; check the file again.", table=n) from None
                w.extra_ops.append({"op": "upload_append", "table": n, "rows_added": int(t["n_rows"]), "first_row_id": int(start),
                                    "source_file": t["source_file"]})
                if n == prec.get("primary_table"):  # the holdout grows by the seeded share of the new rows (§5.7)
                    par = _Parent(db, store, dstore, pvid, n)
                    sr = SS.carry_into(w, n, par.ids, par.split, _cfg.HOLDOUT_SHARE, target_in_child=True, parent_vid=pvid) \
                        if par.split else None
                    if sr:
                        w.extra_ops.append(no_floats({"op": "split_carried", "table": n, **sr}))
            elif t["action"] == "delta":  # P13 (§5.6): insert-only → an append part; otherwise the table is rewritten
                deltas[n] = _write_delta(w, db, store, dstore, pvid, t, tmp, ptables.get(n) or {}, f"_dlc{len(deltas)}",
                                         reasons, by)
            else:  # rewrite
                par = _Parent(db, store, dstore, pvid, n)
                m = dict(matches[n])
                if m["method"] == "declared":
                    if not par.declared:
                        raise LakeError(400, "no_declared_key", f"{pvid} has no confirmed key for table {n!r}: choose "
                                                                "{method: key, columns: [...]} instead.", table=n)
                    m["columns"] = par.declared[0]
                rec, mw, contract, chosen, _ = _write_matched(w, db, par, t, typed, m, acks, by,
                                                              target if par.is_primary else None, t["source_file"])
                recs[n], contracts[n] = rec, contract
                warns += [{**{k: v for k, v in x.items() if k not in ("values",)}, "table": n} for x in contract["warnings"]]
                warns += [{**x, "source": "match", "table": n} for x in mw]
        for n, spec in ptables.items():
            # (F-X3a M1: a delta's `key` is the key it is applied by: _write_delta records it, as the primary key only
            # when the table has none)
            if spec.get("key") and n in w.tables and spec.get("action") != "delta":
                k = w.key(n, spec["key"], declared_by=by)
                if not k["valid"]:
                    raise LakeError(400, "key_invalid", f"Key {', '.join(spec['key'])} of '{n}' is not unique and present: "
                                                        f"{k['n_duplicates']:,} repeated, {k['n_nulls']:,} missing.", table=n)
        for r in plan.get("relations") or []:
            w.relation(r["name"], r["from"]["table"], r["from"]["columns"], r["to"]["table"], r["to"]["columns"],
                       cardinality=r["cardinality"], declared_by=by)
        _guard_keys(w, ptabs, integrity)
        w.warnings = warns
        acts = {t["name"]: t["action"] for t in tabs}
        out = w.commit(make_default=(True if make_default is None else make_default) if not pvid else make_default,
                       target=target, kind="tabular",
                       note=f"Uploaded {len(tabs)} table(s) from {', '.join(f['filename'] for f in files)}"
                            + (f" as the next version of {pvid}" if pvid else ""))
    vid = out["version_id"]
    created = not out.get("deduped")
    for f in files:
        _keep_source(db, store, tmp / f["src"], f, up, vid)
    _mark_committed(db, up, vid)
    with db.connect() as con:
        S.audit(con, by, "upload_committed", {"upload_id": up, "dataset_id": vid, "root_id": out.get("root_id") or vid,
                                              "parent": pvid, "check_sha256": u["check_sha256"], "tables": acts,
                                              "carried": chk.get("carried") or [], "dropped": chk.get("dropped") or [],
                                              "sources": [{"filename": f["filename"], "sha256": f["sha256"]} for f in files],
                                              "deduped": out.get("deduped")})
    if store and hasattr(store, "remove_tmp"):
        store.remove_tmp(tmp)
    res = _version(db, vid, up, out)
    res.update(matches=recs, warnings=warns, deduped=out.get("deduped"))
    if deltas:
        res["deltas"] = deltas
    if contracts:
        res["contract"] = contracts
    return res, created


def _guard_keys(w, ptabs: dict[str, dict[str, Any]], integrity: list[dict[str, Any]]) -> None:
    """F-X3a M1: a parent key that the written tables no longer satisfy must have been acknowledged at commit."""
    ok = {(x["table"], tuple(x["columns"])) for x in integrity if x["code"] == "key_invalidated"}
    for n, t in w.tables.items():
        if t.change == "carried" or n not in ptabs:
            continue
        was = {tuple(k["columns"]) for k in ptabs[n].get("all_keys") or [] if k["valid"]}
        for k in t.keys:
            if not k["valid"] and tuple(k["columns"]) in was and (n, tuple(k["columns"])) not in ok:
                raise LakeError(400, "acknowledge_required",
                                f"After this upload, {', '.join(k['columns'])} is no longer a key of '{n}'. Check the upload "
                                "again and send acknowledge: [\"key_invalidated\"] to commit it anyway.",
                                acknowledge=["key_invalidated"], table=n, columns=k["columns"])


def _write_delta(w, db, store, dstore, pvid: str, t: dict[str, Any], tmp: Path, spec: dict[str, Any], tag: str,
                 reasons: dict[str, str], by: str) -> dict[str, Any]:
    """P13: one delta table written (§5.6), classified again on the writer's connection (the parent is immutable, so
    the counts are the check's). Nothing changes → carried (with its splits); insert-only (no deletes, no update that
    changes a value, no new column) → one `append` part, the parent's parts reused; otherwise → rewritten: kept rows
    and updated rows keep their `__row_id` (so their holdout folds carry by join), inserted rows get new ids from the
    root counter in file order. The parent's keys are checked again."""
    from .. import config as _cfg
    from . import splitstore as SS
    from .derive import no_floats

    n = t["name"]
    d, par, allow = _delta_for(db, store, dstore, w.con, pvid, t, tmp, spec, tag)
    c, mode = d.counts, d.mode(allow)
    start = None
    if mode == "carry":
        w.carry(n)
        w.carry_splits(n)
    elif mode == "append":
        base = w._parent_table(n)
        start = reserve_row_ids(db, w.root_id, n, (base.row_id_max if base.row_id_max is not None else -1) + 1, c["inserted"])
        w.append(n, d.insert_sql(start), source_file=t["source_file"])
    else:
        nxt = par.next_row_id(db)
        start = reserve_row_ids(db, par.root, n, nxt, c["inserted"]) if c["inserted"] else nxt
        w.write(n, d.result_sql(start, allow), reasons=reasons, source_file=t["source_file"])
        cols = set(d.ptypes) | (set(d.new_cols) if allow else set())
        for k in _parent_keys(db, par):
            if all(x in cols for x in k["columns"]):
                w.key(n, k["columns"], kind=k["kind"], declared_by=k["declared_by"])
    if mode != "carry":
        # the key the delta was applied by is a confirmed key; F-X3a M1: it becomes the primary key only when the table
        # has none, else it is kept as a unique key, so later deltas never switch to it without being told
        prim = [k["columns"] for k in _parent_keys(db, par) if k["kind"] == "primary"]
        k = w.key(n, d.key, kind="primary" if not prim or list(d.key) in prim else "unique", declared_by=by)
        if not k["valid"]:
            raise LakeError(400, "key_invalid", f"After the delta, {', '.join(d.key)} is not a key of '{n}': "
                                                f"{k['n_duplicates']:,} repeated, {k['n_nulls']:,} missing.", table=n)
        if par.split:  # the holdout: kept / updated rows keep their folds by __row_id, new rows get the seeded share
            sr = SS.carry_into(w, n, par.ids, par.split, _cfg.HOLDOUT_SHARE,
                               target_in_child=par.target in (set(d.ptypes) | set(d.new_cols)), parent_vid=pvid)
            if sr:
                w.extra_ops.append(no_floats({"op": "split_carried", "table": n, **sr}))
    rec = {"mode": mode, "change": w.tables[n].change, **c, **({"first_row_id": int(start)} if start is not None and c["inserted"] else {}),
           "source_file": t["source_file"], "applied_by": by}
    w.extra_ops.append(no_floats({"op": "delta", "table": n, **rec}))
    return rec


def _keep_source(db, store, src: Path, f: dict[str, Any], up: str, vid: str) -> None:
    """The uploaded bytes, kept as a 'source' blob referenced by the upload (retention: §6.4 sources.keep_days)."""
    if not src.exists():
        return
    ext = (Path(f["filename"] or "").suffix.lstrip(".").lower() or "bin")[:8]
    key = store.put_file(src, f["sha256"], ext)
    with db.connect() as con:
        team = (con.execute("SELECT team_id FROM datasets WHERE id = ?", (vid,)).fetchone() or ["tm_default"])[0]
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'source', ?, NULL, 'live', ?)"
                    " ON CONFLICT (sha256) DO NOTHING", (f["sha256"], key, f["bytes"], S.now()))
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'upload', ?, 'source', ?, ?)", (f["sha256"], up, team, S.now()))


def _mark_committed(db, up: str, vid: str) -> None:
    with db.connect() as con:
        pid = (con.execute("SELECT project_id FROM datasets WHERE id = ?", (vid,)).fetchone() or [None])[0]
        con.execute("UPDATE uploads SET status = 'committed', committed_version_id = ?, project_id = COALESCE(?, project_id)"
                    " WHERE id = ?", (vid, pid, up))


def _version(db, vid: str, up: str, out: dict[str, Any] | None = None) -> dict[str, Any]:
    with db.connect() as con:
        r = dict(con.execute("SELECT id, root_id, version_no, storage, manifest_sha256, data_sha256, n_tables, n_rows, n_cols,"
                             " primary_table, team_id, project_id, op, created_by, created_at, name, target FROM datasets WHERE id = ?",
                             (vid,)).fetchone())
        r["tables"] = [dict(t) for t in con.execute("SELECT table_name, table_sha256, n_rows, n_cols, bytes, change, schema_json"
                                                    " FROM version_tables WHERE version_id = ? ORDER BY ordinal", (vid,))]
    for t in r["tables"]:
        t["schema"] = json.loads(t.pop("schema_json"))
    return {**r, "version_id": vid, "dataset_id": vid, "upload_id": up,
            **({"default": out["default"]} if out and out.get("default") else {})}


def sweep_expired(db, store) -> int:
    """Staged uploads past expires_at: discarded and their tmp removed (called on start)."""
    now = _iso(_now())
    with db.connect() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM uploads WHERE status IN ('staged', 'checked') AND expires_at < ?", (now,))]
        for i in ids:
            con.execute("UPDATE uploads SET status = 'expired' WHERE id = ?", (i,))
    for i in ids:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(store.tmp_dir(i))
    return len(ids)

