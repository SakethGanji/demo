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


def discard(db, store, up: str, why: str = "discarded") -> None:
    with db.connect() as con:
        con.execute("UPDATE uploads SET status = 'discarded' WHERE id = ? AND status <> 'committed'", (up,))
    remove = getattr(store, "remove_tmp", None)
    if remove:
        remove(store.tmp_dir(up))


def patch_plan(db, up: str, body: dict[str, Any]) -> dict[str, Any]:
    u = get(db, up)
    if u["status"] in ("committed", "discarded", "expired"):
        raise LakeError(409, f"upload_{u['status']}", f"Upload {up} is {u['status']}.")
    plan = dict(u["plan"] or {})
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
        if len(files) > 1:
            raise LakeError(400, "single_table", "One file per upload for now: multi-table uploads (several files, Excel "
                                                 "sheets) arrive with P12.")
        t0 = time.time()
        tmp = store.tmp_dir(up)
        f = files[0]
        plan = u["plan"] or {}
        out = _check_file(db, tmp, f, plan, u["team_id"])
        if plan.get("parent_version_id"):
            out["next_version"] = _check_next(db, store, dstore, tmp, out, plan)
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


#: F-X1 C1: columns per aggregate query in the typing scan (7 aggregates each).
SCAN_CHUNK_COLS = 64


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


def _check_file(db, tmp: Path, f: dict[str, Any], plan: dict[str, Any], team_id: str) -> dict[str, Any]:
    src = tmp / f["src"]
    name = f["filename"] or ""
    with open(src, "rb") as fh:
        head = fh.read(8)
    blocks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    notes: list[str] = []
    table = _table_name(plan, name)
    if head[:4] == b"PAR1" or name.lower().endswith(".parquet"):
        return _check_parquet(tmp, src, f, plan, table)
    if head[:2] == b"PK" or re.search(r"\.(xlsx|xlsm|xls)$", name, re.I):
        raise LakeError(400, "excel_later", f"{name or 'This file'} is an Excel workbook: Excel uploads arrive with P12. "
                                            "Save the sheet as CSV and upload that.")
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
    sep = "\t" if name.lower().endswith(".tsv") else sniff_sep(head_text)
    if sep != ",":
        notes.append(f"separator {'tab' if sep == chr(9) else repr(sep)}")
    decimal_comma = sep == ";"
    raw_names = _header(path, sep)
    names = [h.strip() or f"col_{i + 1}" for i, h in enumerate(raw_names)]
    dup = sorted({h for h in names if names.count(h) > 1})
    if dup:
        where = "; ".join(f"'{h}' is columns {', '.join(str(i + 1) for i, n in enumerate(names) if n == h)}" for h in dup)
        raise LakeError(400, "duplicate_header", f"The header repeats a column name ({where}). Rename or remove the duplicate "
                                                 "and upload again, so no column is silently renamed.")
    _guard_names(names)
    if len(names) < 2:
        raise LakeError(400, "one_column", "No separated columns found (tried comma, semicolon, tab, pipe). Give a CSV with a "
                                           "header row and at least two columns.")
    nas = na_tokens(db, team_id)
    declared = {str(k): str(v).upper() for k, v in (plan.get("types") or {}).items()}
    unknown = [c for c in declared if c not in names]
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
        # F-X1 C1: one query per chunk of columns. A single SELECT of every aggregate makes DuckDB's planning cost
        # quadratic in the column count (5,000 columns took the API past 12 GB); chunks keep it linear.
        # (count(*) rides with the first chunk: a narrow file is still one scan of the CSV)
        row: list[Any] = []
        step = 7 * SCAN_CHUNK_COLS
        try:
            for k in range(1, len(aggs), step):
                head = [aggs[0]] if k == 1 else []
                row += list(con.execute(f"SELECT {', '.join(head + aggs[k:k + step])} FROM src").fetchall()[0])
        except Exception as e:  # noqa: BLE001 - malformed CSV: a 400 in words
            raise LakeError(400, "csv_unreadable", _err_line(e)) from None
        n_rows = int(row[0])
        if n_rows < 2:
            raise LakeError(400, "too_few_rows", "This file has fewer than two data rows.")
        schema: list[dict[str, Any]] = []
        exprs: list[str] = []
        kept_text: list[str] = []
        for i, (n, want, fmt) in enumerate(spec):
            nn, lz, fail, fail_dbl, gt12a, gt12b, n_slash = (int(x) for x in row[1 + i * 7: 8 + i * 7])
            typ, reason = want, None
            is_declared = n in declared
            if is_declared:
                reason = f"declared {want}"
                if fail:
                    blocks.append({"code": "declared_type_mismatch", "column": n,
                                   "message": f"'{n}' is declared {want} but {fail:,} value(s) do not read as {want}.",
                                   "examples": _examples(con, n, want, fmt, decimal_comma)})
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
            elif fail and want == "BIGINT" and not fail_dbl:
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
        typed = tmp / "typed.parquet"
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
            "warnings": warnings, "blocks": blocks, "notes": notes, "typed": "typed.parquet" if not blocks else None}


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


def _check_parquet(tmp: Path, src: Path, f: dict[str, Any], plan: dict[str, Any], table: str) -> dict[str, Any]:
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
        if len(names) < 2:
            raise LakeError(400, "one_column", "A dataset needs at least two columns.")
        if n_rows < 2:
            raise LakeError(400, "too_few_rows", "This file has fewer than two data rows.")
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

    def __init__(self, db, store, dstore, vid: str, table: str | None) -> None:
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
        self.target = tg
        self.holdout_ids = None
        self.split_key = None
        if dstore is not None:
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
    from ..dataset_versions import ACK_TOKEN, parse_match

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
    parent = _Parent(db, store, dstore, plan["parent_version_id"], plan.get("table"))
    if parent.vid != nv.get("parent_version_id", parent.vid):
        raise LakeError(409, "check_stale", "The parent changed after the check; check again.")
    if m["method"] == "declared":
        if not parent.declared:
            raise LakeError(400, "no_declared_key", f"{parent.vid} has no confirmed key for table {parent.table!r}: choose "
                                                    "{method: key, columns: [...]} instead.")
        m["columns"] = parent.declared[0]
    sources = [{"filename": f["filename"], "sha256": f["sha256"], "bytes": f["bytes"], "kept": True}]
    reasons = {c["name"]: c["reason"] for c in t["schema"] if c.get("reason")}
    target = plan.get("target") or (parent.target if parent.target in {c["name"] for c in t["schema"]} else None)
    w = VersionWriter(db, store, op="upload_version", by=by, parent_id=parent.vid, reason=reason or plan.get("reason") or "",
                      job_id=up, sources=sources, primary_table=parent.table, duck_kw=_duck_kw())
    with w:
        done = w.committed_for_job()
        if done:
            _mark_committed(db, up, done)
            return _version(db, done, up), False
        try:
            mt = _matcher(w.con, parent, typed)
        except ValueError as e:
            raise LakeError(400, "match_impossible", str(e)) from None
        if m["method"] in ("key", "declared"):
            prob = mt.key_problems(m["columns"])
            if prob:
                if prob["code"] == "key_invalid":
                    masked = _mask(db, parent.root, parent.table, prob)
                    raise LakeError(400, "key_invalid", prob["message"],
                                    **{k: v for k, v in masked.items() if k not in ("code", "message")})
                raise LakeError(400, "bad_match", prob["message"], columns=prob["columns"])
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
                            warnings=need, chosen=chosen, best_candidate=best)
        for x in mw:
            x["acknowledged"] = bool(x["needs_ack"])
        contract = mt.contract(target)
        warns = [{k: v for k, v in x.items() if k != "examples"} for x in chk.get("warnings") or []] + \
            [{k: v for k, v in x.items() if k not in ("values",)} for x in contract["warnings"]] + \
            [{**x, "source": "match"} for x in mw]
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
        cols = ", ".join(f"n.{_q(c['name'])}" for c in t["schema"])
        w.write(parent.table, f"SELECT i.rid AS {ROW_ID}, {cols} FROM _n n JOIN _ids i USING (__fr)", reasons=reasons,
                source_file=f["filename"])
        for other in parent.tables:  # a multi-table parent: the other tables are carried (zero bytes)
            if other != parent.table:
                w.carry(other)
        new_cols = {c["name"] for c in t["schema"]}
        for k in _parent_keys(db, parent):
            if all(c in new_cols for c in k["columns"]):
                w.key(parent.table, k["columns"], kind=k["kind"], declared_by=k["declared_by"])
        if m["method"] in ("key", "declared"):  # the chosen key is saved as a confirmed key (§5.3 step 3)
            w.key(parent.table, m["columns"], kind="primary", declared_by=by)
        rows = w.con.execute("SELECT rid, pid FROM _ids ORDER BY rid").fetchall()
        rids = [int(r[0]) for r in rows]
        pids = [None if r[1] is None else int(r[1]) for r in rows]
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
                                  target_in_child=parent.target in new_cols, parent_vid=parent.vid)
        if split_rec:
            from .derive import no_floats

            w.extra_ops.append(no_floats({"op": "split_carried", "table": parent.table, **split_rec}))
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


def _parent_keys(db, parent: "_Parent") -> list[dict[str, Any]]:
    if parent.storage != "parquet":
        return []
    with db.connect() as con:
        return [{"columns": json.loads(r["columns_json"]), "kind": r["kind"], "declared_by": r["declared_by"]}
                for r in con.execute("SELECT * FROM table_keys WHERE version_id = ? AND table_name = ?", (parent.vid, parent.table))]


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

