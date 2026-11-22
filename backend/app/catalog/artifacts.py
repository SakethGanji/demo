"""F-X4c (X4 C3, plan §5.10): the derived copies of data values, and how a purge removes them.

A purge (catalog/purge.py) deletes the lake bytes of a table's columns or rows. The platform also copies values out of
the lake: export caches, AutoGluon's saved training frame, model pickles, run artifacts (anomaly top rows, the SHAP
cache, the scorecard, fairness and credit reports, Doctor / Feature Lab / shift / AL / annotation files), batch inputs
and outputs, and JSON in the database (runs, batch_jobs, uploads, datasets.profile, table_stats, the model card). This
module finds them and makes the purged values unreadable.

**1. The registry** (`derived_artifacts`). A writer of a value-bearing artifact calls `record(...)` with where it
wrote (a path under the data dir, or a DB object), the source version / table, the run, and the columns it holds.
Hooked writers: run creation (the run dir: predictor, artifacts), model version creation (the deploy copy), registry
batches (input / scored), the export cache. `inventory()` reads the registry rows of the affected versions and runs.

**2. The walker** (exhaustive, by convention and by value). The registry is not trusted to be complete:
- **linked locations**: every affected run's dir and predictor, the model versions built from those runs (their dirs,
  their batches), the export caches and dataset dirs of the affected versions;
- **every text file under the data dir** (JSON, CSV, text) and **every JSON document in every DB table** (any table,
  any text column) is searched for the purged values;
- **binary files** (pickles, parquet, numpy) are searched for the numeric values as int64 / float64 bytes.

**What happens to each** (`scrub()`):
| artifact | action |
|---|---|
| export cache `lake/exports/<v>` of an affected version | deleted (re-creatable) |
| AutoGluon `utils/data/*` (X.pkl, X_val.pkl, y.pkl …) of an affected run | deleted; the predictor still loads and predicts |
| model pickles of a run that **used a purged column as a feature** (or whose files hold a purged value) | the predictor dir and the deploy copies are deleted; the run is `purged_model` and its model versions `failed` with error `purged_model: …`: predict / batch / card answer 409 `purged_model` (retrain on the new version) |
| JSON / CSV / text artifacts (top.json, shap/, scorecard/, credit reports, fairness, AL / annotations / shift, batches, agent conversations …) | **redacted in place**: a purged column's entries and any purged value become `"purged"`; rows of purged `__row_id`s are dropped from CSVs |
| other binary files holding a value (caches) | deleted |
| DB JSON (runs.result / config, batch_jobs, uploads.check_json, datasets.profile, table_stats, model_versions.card, jobs …) | redacted in place (the rows stay); then SQLite VACUUM + WAL truncate, Postgres VACUUM FULL of the touched tables |
| audit_log | never rewritten (hash chain); the sweep checks it |

Keyed redaction (a dict key equal to a purged column, a record naming the column in `feature` / `column` / `group`
…) is applied to linked artifacts only: another dataset may have a column of the same name. Value redaction applies
everywhere: the values are chosen to be distinctive (see `Needles`).

**3. The sweep** (`sweep()`), run by every purge execution after the scrub: a sample of the purged values (up to
`ENSEMBLE_PURGE_SWEEP_SAMPLE`, 500) is searched in every file under the data dir and the lake dir (text forms, and
int64 / float64 bytes for numbers), in every row of every DB table, and in every object version in the S3 bucket. It
also checks that no purged blob has an object version or a cache file left. Any hit → `purge_incomplete` (recorded in
the purge, audited, and the purge's status stays `approved`, not `used`).

The purged values are never stored: the needles are read from the lake before the bytes are deleted and live only in
memory during the execution. A re-sweep after a restart (`POST /api/purges/{id}/sweep`) without them checks the
structure only (blobs, model dirs, export caches) and says `values: unavailable`.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .schema import now

PURGED = "purged"
NAME_KEYS = ("feature", "column", "col", "name", "variable", "group", "group_column", "attribute", "by", "field", "protected")
TIME_KEYS = re.compile(r"(^|_)(at|ts|time|date_utc|created|updated)$|_at$")
TEXT_EXT = {".json", ".csv", ".txt", ".md", ".html", ".htm", ".jsonl", ".log", ".tsv", ".yaml", ".yml", ".xml"}
SKIP_TABLES = {"audit_log", "audit_head"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


SAMPLE = _env_int("ENSEMBLE_PURGE_SWEEP_SAMPLE", 500)
MAX_NEEDLES = _env_int("ENSEMBLE_PURGE_MAX_VALUES", 2_000_000)
MIN_DIGITS = _env_int("ENSEMBLE_PURGE_MIN_DIGITS", 6)
MIN_CHARS = _env_int("ENSEMBLE_PURGE_MIN_CHARS", 6)
BINARY_MB = _env_int("ENSEMBLE_PURGE_SWEEP_BINARY_MB", 256)
TEXT_MB = _env_int("ENSEMBLE_PURGE_SWEEP_TEXT_MB", 2048)


# --------------------------------------------------------------------------- the registry


def record(con, kind: str, path: str | os.PathLike | None, *, version_id: str | None = None, table: str | None = None,
           run_id: str | None = None, object_id: str | None = None, columns: Iterable[str] | None = None,
           data_dir: Path | None = None) -> str:
    """Register a value-bearing artifact. `path` is stored relative to the data dir when it is inside it."""
    p = str(path) if path else None
    if p and data_dir is not None:
        try:
            p = Path(p).resolve().relative_to(Path(data_dir).resolve()).as_posix()
        except ValueError:
            pass
    aid = "art_" + secrets.token_hex(6)
    con.execute("INSERT INTO derived_artifacts (id, kind, path, version_id, table_name, run_id, object_id, columns_json, created_at,"
                " state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')",
                (aid, kind, p, version_id, table, run_id, object_id, json.dumps(sorted(columns or [])), now()))
    return aid


def record_safe(db, kind: str, path: Any, **kw: Any) -> None:
    """For writers: never fails the write (the walker finds what the registry misses)."""
    try:
        dd = Path(db.path).parent
        with db.connect() as con:
            record(con, kind, path, data_dir=dd, **kw)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- the purged values


def _canon_num(x: float) -> str | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(f):
        return None
    if f == int(f) and abs(f) < 2 ** 63:
        return str(int(f))
    return repr(f)


def _digits(s: str) -> int:
    return len(s.lstrip("-").replace(".", "").lstrip("0"))


_NUM_RX = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w])")
_TOK_RX = re.compile(r"[^\s,;:\"'\[\]\(\)\{\}<>|=\\]+")


class Needles:
    """The purged values, canonicalised: numbers as their shortest form (integral floats as ints), strings as stored.
    Only distinctive values are kept (≥ MIN_DIGITS significant digits, strings ≥ MIN_CHARS) and none that also occur in
    the kept data (the new version), so a hit means a copy of a purged value."""

    def __init__(self, values: Iterable[Any], *, columns: list[str], row_ids: list[int], sampled: bool = False) -> None:
        self.columns = list(columns)
        self.row_ids = set(int(i) for i in row_ids)
        self.canon: set[str] = set()
        nums: list[float] = []
        self.multi: list[str] = []
        self.sampled = sampled
        for v in values:
            if v is None or isinstance(v, bool):
                continue
            if isinstance(v, (int, float, np.integer, np.floating)):
                c = _canon_num(v)
                if c and _digits(c) >= MIN_DIGITS:
                    self.canon.add(c)
                    nums.append(float(c))
                continue
            s = str(v).strip()
            if len(s) < MIN_CHARS:
                continue
            c = _canon_num(s) if _NUM_RX.fullmatch(s) else None
            if c is not None:
                if _digits(c) >= MIN_DIGITS:
                    self.canon.add(c)
                    nums.append(float(c))
                continue
            self.canon.add(s)
            if _TOK_RX.fullmatch(s) is None:
                self.multi.append(s)
        self.nums_f = np.unique(np.array(nums, dtype="<f8")) if nums else np.zeros(0, "<f8")
        ints = [int(x) for x in self.nums_f if x == int(x) and abs(x) < 2 ** 63]
        self.nums_i = np.unique(np.array(ints, dtype="<i8")) if ints else np.zeros(0, "<i8")
        self._multi_rx = re.compile("|".join(re.escape(m) for m in sorted(self.multi, key=len, reverse=True)[:5000])) if self.multi else None

    def __len__(self) -> int:
        return len(self.canon)

    def sample(self, n: int = SAMPLE) -> "Needles":
        if len(self.canon) <= n:
            return self
        rng = np.random.default_rng(20261122)
        pick = sorted(self.canon)
        idx = rng.choice(len(pick), size=n, replace=False)
        chosen = [pick[i] for i in sorted(idx)]
        return Needles([float(c) if _NUM_RX.fullmatch(c) else c for c in chosen], columns=self.columns, row_ids=list(self.row_ids))

    # -- matching ------------------------------------------------------------------------------------------------

    def hit_scalar(self, v: Any) -> bool:
        if v is None or isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            c = _canon_num(v)
            return c is not None and c in self.canon
        s = str(v).strip()
        if s in self.canon:
            return True
        if _NUM_RX.fullmatch(s):
            c = _canon_num(s.replace(",", ""))
            return c is not None and c in self.canon
        return False

    def sub_text(self, s: str) -> tuple[str, int]:
        """Replace every purged value inside a text (numbers in any of their forms, tokens, multi-word values)."""
        n = 0

        def num(m: re.Match) -> str:
            nonlocal n
            c = _canon_num(m.group(1).replace(",", ""))
            if c is not None and c in self.canon:
                n += 1
                return PURGED
            return m.group(0)

        def tok(m: re.Match) -> str:
            nonlocal n
            if m.group(0) in self.canon:
                n += 1
                return PURGED
            return m.group(0)

        if self._multi_rx is not None:
            s, k = self._multi_rx.subn(PURGED, s)
            n += k
        s = _NUM_RX.sub(num, s)
        s = _TOK_RX.sub(tok, s)
        return s, n

    def text_hits(self, b: bytes) -> set[str]:
        """Distinct purged values found in bytes, as text."""
        out: set[str] = set()
        try:
            t = b.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return out
        for m in _NUM_RX.finditer(t):
            c = _canon_num(m.group(1).replace(",", ""))
            if c is not None and c in self.canon:
                out.add(c)
        for m in _TOK_RX.finditer(t):
            if m.group(0) in self.canon:
                out.add(m.group(0))
        if self._multi_rx is not None:
            out |= set(self._multi_rx.findall(t))
        return out

    def bin_hits(self, b: bytes) -> set[str]:
        """Distinct purged numbers found as int64 / float64 at any byte offset."""
        out: set[str] = set()
        if not len(self.nums_f) or len(b) < 8:
            return out
        for off in range(8):
            n = (len(b) - off) // 8
            if n <= 0:
                continue
            if len(self.nums_i):
                a = np.frombuffer(b, dtype="<i8", count=n, offset=off)
                out |= {str(int(x)) for x in np.unique(a[np.isin(a, self.nums_i)])}
            f = np.frombuffer(b, dtype="<f8", count=n, offset=off)
            out |= {_canon_num(float(x)) for x in np.unique(f[np.isin(f, self.nums_f)])}
        out.discard(None)  # type: ignore[arg-type]
        return out


def collect(app, con, versions: list[str], table: str, columns: list[str], row_ids: list[int], new_version: str | None) -> Needles:
    """Read the purged values from the lake (before the bytes go): the purged columns' distinct values, or every value of
    the purged rows, from every version that holds them; minus the values the kept data (the new version) still has."""
    import pyarrow as pa

    from ..lake import duck
    from ..lake.writer import ROW_ID, _q as qi

    store = app.state.lake
    keys = []
    for vid in versions:
        for r in con.execute("SELECT b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = ?"
                             " AND f.table_name = ? AND b.state IN ('pending', 'live', 'purging')", (vid, table)):
            if r[0] not in keys:
                keys.append(r[0])
    kept = []
    if new_version:
        kept = [r[0] for r in con.execute("SELECT b.key FROM table_files f JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE"
                                          " f.version_id = ? AND f.table_name = ?", (new_version, table))]
    values: list[Any] = []
    if not keys:
        return Needles([], columns=columns, row_ids=row_ids)
    uris = []
    for k in keys:
        try:
            uris.append(store.uri(k))
        except Exception:  # noqa: BLE001 - a part already gone
            continue
    if not uris:
        return Needles([], columns=columns, row_ids=row_ids)
    from ..lake.duck import parquet_rel

    rel = parquet_rel(uris)
    sampled = False
    with duck.job() as dc:
        have = [r[0] for r in dc.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
        if columns:
            for c in columns:
                if c not in have:
                    continue
                vals = dc.execute(f"SELECT DISTINCT {qi(c)} FROM {rel} WHERE {qi(c)} IS NOT NULL LIMIT {MAX_NEEDLES + 1}").to_arrow_table()
                col = vals.column(0).to_pylist()
                sampled |= len(col) > MAX_NEEDLES
                values += col[:MAX_NEEDLES]
        if row_ids:
            dc.register("_pids", pa.table({ROW_ID: pa.array(sorted(set(row_ids)), pa.int64())}))
            t = dc.execute(f"SELECT * FROM {rel} WHERE {ROW_ID} IN (SELECT {ROW_ID} FROM _pids)").to_arrow_table()
            for name in t.column_names:
                if name == ROW_ID:
                    continue
                values += t.column(name).to_pylist()
        n = Needles(values, columns=columns, row_ids=row_ids, sampled=sampled)
        if kept and len(n):  # values the kept data still holds are not purged values
            kuris = []
            for k in kept:
                try:
                    kuris.append(store.uri(k))
                except Exception:  # noqa: BLE001
                    pass
            if kuris:
                krel = parquet_rel(kuris)
                kcols = [(r[0], r[1]) for r in dc.execute(f"DESCRIBE SELECT * FROM {krel}").fetchall() if r[0] != ROW_ID]
                strs = sorted(c for c in n.canon if not _NUM_RX.fullmatch(c))
                dc.register("_nn", pa.table({"v": pa.array(n.nums_f, pa.float64())}))
                dc.register("_ns", pa.table({"v": pa.array(strs, pa.string())}))
                both: set[str] = set()
                for name, typ in kcols:
                    t = str(typ).upper()
                    if any(x in t for x in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "REAL", "NUMERIC")):
                        q = f"SELECT DISTINCT v FROM _nn WHERE v IN (SELECT CAST({qi(name)} AS DOUBLE) FROM {krel})"
                        both |= {_canon_num(x[0]) for x in dc.execute(q).fetchall()}
                    else:
                        q = f"SELECT DISTINCT v FROM _ns WHERE v IN (SELECT CAST({qi(name)} AS VARCHAR) FROM {krel})"
                        both |= {x[0] for x in dc.execute(q).fetchall()}
                        q = f"SELECT DISTINCT v FROM _nn WHERE v IN (SELECT TRY_CAST(CAST({qi(name)} AS VARCHAR) AS DOUBLE) FROM {krel})"
                        both |= {_canon_num(x[0]) for x in dc.execute(q).fetchall()}
                if both:
                    keep = [float(c) if _NUM_RX.fullmatch(c) else c for c in n.canon if c not in both]
                    n = Needles(keep, columns=columns, row_ids=row_ids, sampled=sampled)
    return n


# --------------------------------------------------------------------------- redaction


def redact_obj(obj: Any, nd: Needles, keyed: bool) -> tuple[Any, int]:
    """A JSON value with every purged value (and, when `keyed`, every entry of a purged column) replaced by "purged"."""
    cols = set(nd.columns) if keyed else set()
    if isinstance(obj, dict):
        if cols:
            for k in NAME_KEYS:
                v = obj.get(k)
                if isinstance(v, str) and v in cols and len(obj) > 1:
                    return _purge_column_entry(obj, k, nd)
        if nd.row_ids and keyed:
            rid = obj.get("__row_id", obj.get("row_id"))
            if isinstance(rid, int) and not isinstance(rid, bool) and rid in nd.row_ids:
                return {PURGED: True}, 1
        out, n = {}, 0
        for k, v in obj.items():
            nk = k
            if isinstance(k, str) and nd.hit_scalar(k):
                nk = f"{PURGED}_{n}"
                n += 1
            if isinstance(k, str) and k in cols:
                out[nk] = PURGED
                n += 1
                continue
            if isinstance(k, str) and TIME_KEYS.search(k) and isinstance(v, str):
                out[nk] = v
                continue
            nv, m = redact_obj(v, nd, keyed)
            out[nk] = nv
            n += m
        return out, n
    if isinstance(obj, list):
        out_l, n = [], 0
        for v in obj:
            nv, m = redact_obj(v, nd, keyed)
            out_l.append(nv)
            n += m
        return out_l, n
    if isinstance(obj, bool) or obj is None:
        return obj, 0
    if isinstance(obj, (int, float)):
        return (PURGED, 1) if nd.hit_scalar(obj) else (obj, 0)
    if isinstance(obj, str):
        if nd.hit_scalar(obj):
            return PURGED, 1
        return nd.sub_text(obj)
    return obj, 0


META_KEYS = {"type", "dtype", "kind", "role", "tag", "mask", "source", "semantic", "semantic_type", "nullable", "logical_type",
             "physical_type", "pii", "masked", "status", "direction", "constraint", "monotone", "unit", "description", "note"}
VALUE_KEY = re.compile(r"(^|_)(min|max|mean|median|mode|avg|q\d+|p\d+|quantiles?|cuts?|edges?|bounds?|lo|hi|low|high|values?|"
                       r"from|to|first|last|thresholds?|splits?|bins?|top|examples?|samples?|categories|levels|range|ranges|"
                       r"histogram|lower|upper|left|right|center|centre|point|x|y_true|default)$", re.I)


def _purge_column_entry(obj: dict[str, Any], name_key: str, nd: Needles) -> tuple[dict[str, Any], int]:
    """A record about a purged column (a profile entry, a scorecard feature, a fairness group, a SHAP contribution …):
    its metadata and counts stay (the schema still reads), every value-like field becomes "purged"."""
    out: dict[str, Any] = {}
    n = 0
    for k, v in obj.items():
        if k == name_key or k in META_KEYS or isinstance(v, bool) or v is None:
            out[k] = v
        elif isinstance(v, (int, float)):
            if VALUE_KEY.search(str(k)) or nd.hit_scalar(v):
                out[k] = PURGED
                n += 1
            else:
                out[k] = v
        else:
            out[k] = PURGED
            n += 1
    out[PURGED] = True
    return out, n + 1


def redact_json_text(text: str, nd: Needles, keyed: bool) -> tuple[str | None, int]:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None, 0
    new, n = redact_obj(obj, nd, keyed)
    if not n:
        return text, 0
    return json.dumps(new, default=str), n


def redact_csv_text(text: str, nd: Needles, keyed: bool) -> tuple[str, int]:
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return text, 0
    head = rows[0]
    cols = set(nd.columns) if keyed else set()
    kill = {i for i, h in enumerate(head) if h in cols}
    rid = head.index("__row_id") if (keyed and nd.row_ids and "__row_id" in head) else None
    n = 0
    out = [head]
    for r in rows[1:]:
        if rid is not None and rid < len(r):
            try:
                if int(float(r[rid])) in nd.row_ids:
                    n += 1
                    continue
            except ValueError:
                pass
        nr = []
        for i, cell in enumerate(r):
            if i in kill and cell != "":
                nr.append(PURGED)
                n += 1
            elif cell and nd.hit_scalar(cell):
                nr.append(PURGED)
                n += 1
            else:
                nr.append(cell)
        out.append(nr)
    if not n:
        return text, 0
    buf = io.StringIO()
    csv.writer(buf, delimiter=delim, lineterminator="\n").writerows(out)
    return buf.getvalue(), n


def _is_text(b: bytes) -> bool:
    head = b[:65536]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _write_atomic(p: Path, data: str) -> None:
    tmp = p.with_name(f".{p.name}.{secrets.token_hex(3)}.purge")
    tmp.write_text(data)
    os.replace(tmp, p)


def redact_file(p: Path, nd: Needles, keyed: bool) -> dict[str, Any] | None:
    """Redact one file in place. Returns what was done, or None when nothing changed."""
    try:
        if p.stat().st_size > TEXT_MB * 1024 * 1024:
            return {"path": str(p), "action": "skipped_large"}
        b = p.read_bytes()
    except OSError:
        return None
    if not _is_text(b):
        return None
    text = b.decode("utf-8", "replace")
    ext = p.suffix.lower()
    n = 0
    new: str | None = None
    if ext in (".json",) or (ext not in (".csv", ".tsv") and text.lstrip()[:1] in ("{", "[")):
        new, n = redact_json_text(text, nd, keyed)
    if new is None and ext == ".jsonl":
        lines, n = [], 0
        for line in text.splitlines():
            nl, m = redact_json_text(line, nd, keyed) if line.strip() else (line, 0)
            lines.append(nl if nl is not None else nd.sub_text(line)[0])
            n += m
        new = "\n".join(lines) + "\n"
    if new is None and ext in (".csv", ".tsv"):
        new, n = redact_csv_text(text, nd, keyed)
    if new is None:
        new, n = nd.sub_text(text)
    if not n:
        return None
    _write_atomic(p, new)
    return {"path": str(p), "action": "redacted", "n": n}


# --------------------------------------------------------------------------- inventory: what holds the values


def _run_features(rec: dict[str, Any]) -> set[str] | None:
    res = rec.get("result") or {}
    feats = res.get("features")
    out: set[str] = set()
    if isinstance(feats, list):
        for f in feats:
            if isinstance(f, str):
                out.add(f)
            elif isinstance(f, dict):
                for k in ("feature", "name", "column"):
                    if isinstance(f.get(k), str):
                        out.add(f[k])
                        break
    elif isinstance(feats, dict):
        out |= {k for k in feats if isinstance(k, str)}
    cfg = rec.get("config") or {}
    for k in ("features", "feature_columns"):
        if isinstance(cfg.get(k), list):
            out |= {x for x in cfg[k] if isinstance(x, str)}
    return out or None


def _load_json(s: Any) -> Any:
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s) if s else {}
    except (ValueError, TypeError):
        return {}


def inventory(app, con, rep: dict[str, Any], columns: list[str], table: str) -> dict[str, Any]:
    """The derived copies of the purged data, for the impact report: registry rows + the linked locations the walker
    derives (run dirs, predictors and their saved training data, model versions and their batches, exports, dataset
    dirs), and the DB tables that hold JSON. Whether a model must be retrained is decided here (a purged column among
    its features); the value walk at execution adds any model whose files hold a purged value."""
    dd = Path(app.state.settings.data_dir)
    lake = Path(app.state.settings.lake_dir)
    vids = sorted({v["id"] for v in rep["versions_with_values"]} | {v["id"] for v in rep["versions"]})
    rids = sorted({r["id"] for r in rep["runs"]})
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, path: Any, action: str, why: str, **kw: Any) -> None:
        if path is None:
            return
        p = Path(path)
        if not p.is_absolute():
            p = dd / p
        key = f"{kind}:{p}"
        if key in seen:
            return
        seen.add(key)
        items.append({"kind": kind, "path": str(p), "rel": _rel(p, dd), "exists": p.exists(), "action": action, "why": why, **kw})

    purged_models: dict[str, dict[str, Any]] = {}
    runs: dict[str, dict[str, Any]] = {}
    for rid in rids:
        r = con.execute("SELECT id, config, result, dataset_id FROM runs WHERE id = ?", (rid,)).fetchone()
        if not r:
            continue
        rec = {"id": r["id"], "config": _load_json(r["config"]), "result": _load_json(r["result"]), "dataset_id": r["dataset_id"]}
        runs[rid] = rec
        cfg = rec["config"]
        feats = _run_features(rec)
        used = sorted(set(columns) & feats) if (columns and feats is not None) else []
        if columns and feats is None:  # no feature list: everything not dropped
            drop = set(cfg.get("drop_columns") or []) | set(cfg.get("id_columns_dropped") or [])
            used = sorted(c for c in columns if c not in drop and c != cfg.get("label"))
        if columns and cfg.get("label") in columns:
            used = sorted(set(used) | {cfg["label"]})
        if used:
            purged_models[rid] = {"run_id": rid, "columns": used, "why": f"trained with purged column(s) {', '.join(used)}"}
        rd = cfg.get("run_dir")
        if rd:
            add("run_dir", rd, "redact", f"artifacts of run {rid} (top rows, SHAP cache, scorecard, reports, AL / annotations / shift)",
                run_id=rid)
        pp = cfg.get("predictor_path")
        if pp:
            if rid in purged_models:
                add("model", pp, "delete", purged_models[rid]["why"] + ": the model is purged (retrain required)", run_id=rid)
            else:
                add("training_data", Path(pp) / "utils" / "data", "delete", "AutoGluon's saved training / validation frame", run_id=rid)
    mvs = []
    if rids:
        q = ",".join("?" * len(rids))
        mvs = [dict(x) for x in con.execute(f"SELECT id, run_id, model_name, version, status, path FROM model_versions WHERE run_id IN ({q})", rids)]
    for mv in mvs:
        if mv.get("path"):
            vdir = Path(mv["path"]).parent if Path(mv["path"]).name in ("predictor", "model") else Path(mv["path"])
            if mv["run_id"] in purged_models:
                add("model_version", mv["path"], "delete", f"deploy copy of {mv['model_name']} v{mv['version']}: purged model",
                    object_id=mv["id"], run_id=mv["run_id"])
            add("model_version_dir", vdir, "redact", f"files of {mv['model_name']} v{mv['version']} (parity, credit report, card)",
                object_id=mv["id"], run_id=mv["run_id"])
            if mv["run_id"] not in purged_models:
                add("training_data", Path(mv["path"]) / "utils" / "data", "delete", "saved training frame in the deploy copy",
                    object_id=mv["id"])
    mids = [m["id"] for m in mvs]
    if mids:
        q = ",".join("?" * len(mids))
        for b in con.execute(f"SELECT id, version_id FROM batch_jobs WHERE version_id IN ({q})", mids):
            add("batch", dd / "registry" / "batches" / b["id"], "redact", f"batch input / scored rows of model version {b['version_id']}",
                object_id=b["id"])
    for vid in vids:
        add("export", lake / "exports" / vid, "delete", f"export cache of {vid}", version_id=vid)
        add("dataset_dir", dd / "datasets" / vid, "redact", f"dataset dir of {vid} (profiles, holdout sample, ops)", version_id=vid)
    # the registry: every artifact recorded against an affected version or run
    if vids or rids:
        conds, args = [], []
        if vids:
            conds.append(f"version_id IN ({','.join('?' * len(vids))})")
            args += vids
        if rids:
            conds.append(f"run_id IN ({','.join('?' * len(rids))})")
            args += rids
        for a in con.execute(f"SELECT * FROM derived_artifacts WHERE state = 'live' AND ({' OR '.join(conds)})", args):
            a = dict(a)
            act = "delete" if a["kind"] in ("export",) else "redact"
            if a["kind"] in ("predictor", "model_version") and a.get("run_id") in purged_models:
                act = "delete"
            add(a["kind"], a["path"], act, f"registered {a['kind']}" + (f" of {a['version_id']}" if a.get("version_id") else ""),
                registry_id=a["id"], run_id=a.get("run_id"), version_id=a.get("version_id"), object_id=a.get("object_id"))
    return {"files": items, "purged_models": list(purged_models.values()),
            "model_versions": [{"id": m["id"], "model_name": m["model_name"], "version": m["version"], "run_id": m["run_id"],
                                "purged": m["run_id"] in purged_models} for m in mvs],
            "db": {"tables": "every text column of every table that holds JSON (runs.result/config, batch_jobs, uploads, datasets.profile,"
                             " table_stats, model_versions.card, jobs …); audit_log is checked, never rewritten",
                   "linked": {"runs": rids, "versions": vids, "model_versions": mids}},
            "walker": "every text file under the data dir and every DB JSON document is also searched for the purged values"}


def _rel(p: Path, dd: Path) -> str:
    try:
        return p.resolve().relative_to(dd.resolve()).as_posix()
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------- scrub


def _platform_db_files(app) -> set[Path]:
    db = app.state.db
    p = Path(db.path).resolve()
    return {p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm"), p.with_name(p.name + "-journal")}


def _under(p: Path, roots: Iterable[Path]) -> bool:
    rp = p.resolve()
    for r in roots:
        try:
            rp.relative_to(Path(r).resolve())
            return True
        except ValueError:
            continue
    return False


def _rm(p: Path) -> bool:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
        return True
    if p.exists():
        p.unlink(missing_ok=True)
        return True
    return False


def scrub(app, pid: str, by: str, inv: dict[str, Any], nd: Needles, table: str) -> dict[str, Any]:
    """Delete or redact every derived copy (see the module doc). Returns counts and the lists, never values."""
    dd = Path(app.state.settings.data_dir).resolve()
    lake = Path(app.state.settings.lake_dir).resolve()
    db = app.state.db
    out: dict[str, Any] = {"deleted": [], "redacted": [], "purged_models": [], "model_versions_purged": [], "db": {}, "unresolved": []}
    linked_roots = [Path(i["path"]) for i in inv["files"] if i["action"] == "redact"]
    purged_runs = {m["run_id"] for m in inv["purged_models"]}

    # 1. deletes from the inventory (exports, training data, purged models and their deploy copies)
    for it in inv["files"]:
        if it["action"] == "delete":
            if _rm(Path(it["path"])):
                out["deleted"].append(_rel(Path(it["path"]), dd))

    # 2. the value walk over every file: redact text, find binary copies
    blob_roots = [lake / "blobs"]
    cache_root = None
    try:
        cache_root = Path(app.state.lake.cache.root).resolve()  # S3 read cache (purged blobs are dropped by delete_blob)
    except Exception:  # noqa: BLE001
        pass
    skip = _platform_db_files(app)
    roots = [dd] + ([lake] if not _under(lake, [dd]) else [])
    bin_hits: list[Path] = []
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dp = Path(dirpath)
            if _under(dp, blob_roots) or (cache_root is not None and _under(dp, [cache_root])):
                dirs[:] = []
                continue
            for f in files:
                p = dp / f
                if p.resolve() in skip or f.endswith(".purge"):
                    continue
                if p.suffix.lower() in (".sqlite", ".sqlite3", ".db") and _is_sqlite(p):
                    r = _scrub_sqlite_file(p, nd)
                    if r:
                        out["redacted"].append({"path": _rel(p, dd), **r})
                    continue
                keyed = _under(p, linked_roots)
                if p.suffix.lower() in TEXT_EXT or _sniff_text(p):
                    r = redact_file(p, nd, keyed)
                    if r:
                        out["redacted"].append({**r, "path": _rel(p, dd)})
                    continue
                if len(nd.nums_f) or nd.multi or nd.canon:
                    try:
                        if p.stat().st_size > BINARY_MB * 1024 * 1024:
                            continue
                        b = p.read_bytes()
                    except OSError:
                        continue
                    if nd.bin_hits(b) or nd.text_hits(b):
                        bin_hits.append(p)
    # binary copies: in a run's predictor → that model is purged; anything else → deleted
    runs_dir = dd / "runs"
    for p in bin_hits:
        rid = _run_of(p, runs_dir)
        if rid and ("predictor" in p.parts or "models" in p.parts) and "utils" not in p.parts:
            purged_runs.add(rid)
            continue
        if _rm(p):
            out["deleted"].append(_rel(p, dd))
    # 3. purged models: the predictor dir (and every deploy copy) goes; the run and its versions are marked
    with db.connect() as con:
        for rid in sorted(purged_runs):
            r = con.execute("SELECT config FROM runs WHERE id = ?", (rid,)).fetchone()
            if not r:
                continue
            cfg = _load_json(r[0])
            why = next((m["why"] for m in inv["purged_models"] if m["run_id"] == rid), "its model files held purged values")
            for k in ("predictor_path",):
                if cfg.get(k) and _rm(Path(cfg[k])):
                    out["deleted"].append(_rel(Path(cfg[k]), dd))
            cfg["purged_model"] = {"purge_id": pid, "at": now(), "by": by, "why": why}
            con.execute("UPDATE runs SET config = ? WHERE id = ?", (json.dumps(cfg, default=str), rid))
            out["purged_models"].append({"run_id": rid, "why": why})
            for mv in con.execute("SELECT id, path FROM model_versions WHERE run_id = ?", (rid,)).fetchall():
                if mv["path"] and _rm(Path(mv["path"])):
                    out["deleted"].append(_rel(Path(mv["path"]), dd))
                con.execute("UPDATE model_versions SET status = 'failed', error = ? WHERE id = ?",
                            (f"purged_model: {why} (purge {pid}); retrain on the new version", mv["id"]))
                out["model_versions_purged"].append(mv["id"])
    # 4. the database: every JSON document, keyed on linked rows
    try:
        out["db"] = scrub_db(app, nd, inv)
    except Exception as e:  # noqa: BLE001 - the sweep reports what is left
        out["db"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    with db.connect() as con:
        ids = [i.get("registry_id") for i in inv["files"] if i.get("registry_id")]
        for i in ids:
            con.execute("UPDATE derived_artifacts SET state = 'purged', purge_id = ? WHERE id = ?", (pid, i))
    out["n_deleted"], out["n_redacted"] = len(out["deleted"]), len(out["redacted"])
    return out


def _run_of(p: Path, runs_dir: Path) -> str | None:
    try:
        rel = p.resolve().relative_to(runs_dir.resolve())
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _sniff_text(p: Path) -> bool:
    if p.suffix:
        return False
    try:
        with open(p, "rb") as f:
            return _is_text(f.read(4096))
    except OSError:
        return False


def _is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# --------------------------------------------------------------------------- the database


def _tables(con, dialect: str) -> list[tuple[str, list[str]]]:
    out = []
    if dialect == "postgres":
        rows = con.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
                           " AND data_type IN ('text', 'character varying', 'json', 'jsonb') ORDER BY table_name, ordinal_position").fetchall()
        d: dict[str, list[str]] = {}
        for r in rows:
            d.setdefault(r[0], []).append(r[1])
        return sorted(d.items())
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")').fetchall() if (r[2] or "").upper() in ("TEXT", "", "JSON", "VARCHAR")]
        out.append((t, cols))
    return out


def _linked_rows(con, inv: dict[str, Any]) -> dict[str, set[str]]:
    """Primary keys of the rows that belong to the purged data (keyed redaction applies to them)."""
    L = inv.get("db", {}).get("linked", {})
    runs, vids, mids = set(L.get("runs") or []), set(L.get("versions") or []), set(L.get("model_versions") or [])
    out: dict[str, set[str]] = {"runs": runs, "datasets": vids, "model_versions": mids, "batch_jobs": set(), "uploads": set(), "table_stats": set()}
    if mids:
        q = ",".join("?" * len(mids))
        out["batch_jobs"] = {r[0] for r in con.execute(f"SELECT id FROM batch_jobs WHERE version_id IN ({q})", sorted(mids))}
    if vids:
        q = ",".join("?" * len(vids))
        out["uploads"] = {r[0] for r in con.execute(f"SELECT id FROM uploads WHERE committed_version_id IN ({q}) OR parent_version_id IN ({q})",
                                                     (*sorted(vids), *sorted(vids)))}
        out["table_stats"] = {r[0] for r in con.execute(f"SELECT table_sha256 FROM version_tables WHERE version_id IN ({q})", sorted(vids))}
    return out


_PK = {"runs": "id", "datasets": "id", "model_versions": "id", "batch_jobs": "id", "uploads": "id", "table_stats": "table_sha256"}


def scrub_db(app, nd: Needles, inv: dict[str, Any]) -> dict[str, Any]:
    db = app.state.db
    dialect = db.dialect
    touched: dict[str, int] = {}
    with db.connect() as con:
        linked = _linked_rows(con, inv)
        tables = _tables(con, dialect)
    for t, cols in tables:
        if t in SKIP_TABLES or not cols:
            continue
        pk = _PK.get(t)
        for c in cols:
            if c == pk:
                continue
            with db.connect() as con:
                sel = f'SELECT {pk + ", " if pk else ""}"{c}" FROM "{t}" WHERE "{c}" IS NOT NULL AND ("{c}" LIKE ? OR "{c}" LIKE ?)'
                rows = con.execute(sel, ("{%", "[%")).fetchall()
            ups = []
            for r in rows:
                key = r[0] if pk else None
                val = r[-1]
                if not isinstance(val, str):
                    val = json.dumps(val, default=str)
                keyed = bool(pk and key in linked.get(t, set()))
                if not keyed and not nd.text_hits(val.encode()):
                    continue
                new, n = redact_json_text(val, nd, keyed)
                if new is not None and n:
                    ups.append((new, key, val))
            if ups:
                with db.connect() as con:
                    for new, key, old in ups:
                        if pk:
                            con.execute(f'UPDATE "{t}" SET "{c}" = ? WHERE "{pk}" = ?', (new, key))
                        else:
                            con.execute(f'UPDATE "{t}" SET "{c}" = ? WHERE "{c}" = ?', (new, old))
                touched[f"{t}.{c}"] = touched.get(f"{t}.{c}", 0) + len(ups)
    compact = compact_db(app, sorted({k.split(".")[0] for k in touched}))
    return {"redacted": touched, "compact": compact}


def compact_db(app, tables: list[str]) -> dict[str, Any]:
    """Old row versions keep the old text (SQLite free pages and WAL, Postgres dead tuples): rewrite them away."""
    db = app.state.db
    try:
        if db.dialect == "postgres":
            import psycopg

            from .. import db_pg
            from .pg_schema import owner_url

            url = owner_url() or db.url
            with psycopg.connect(db_pg._plain(url) if hasattr(db_pg, "_plain") else url, autocommit=True) as c:
                for t in tables:
                    c.execute(f'VACUUM FULL "{t}"')
            return {"vacuum_full": tables}
        import sqlite3

        c = sqlite3.connect(db.path, timeout=60)
        try:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.execute("VACUUM")
            r = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            c.close()
        return {"vacuum": True, "wal_checkpoint": list(r) if r else None}
    except Exception as e:  # noqa: BLE001 - reported; the sweep decides
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def _scrub_sqlite_file(p: Path, nd: Needles) -> dict[str, Any] | None:
    """Another SQLite file under the data dir (e.g. mlflow.db): redact its text cells that hold purged values, then VACUUM."""
    import sqlite3

    b = p.read_bytes() if p.stat().st_size <= BINARY_MB * 1024 * 1024 else b""
    if not (nd.text_hits(b) or nd.bin_hits(b)):
        return None
    n = 0
    c = sqlite3.connect(p, timeout=30)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall():
            cols = [r[1] for r in c.execute(f'PRAGMA table_info("{t}")').fetchall()]
            for col in cols:
                for (v,) in c.execute(f'SELECT DISTINCT "{col}" FROM "{t}" WHERE "{col}" IS NOT NULL').fetchall():
                    if isinstance(v, (int, float)) and nd.hit_scalar(v):
                        c.execute(f'UPDATE "{t}" SET "{col}" = ? WHERE "{col}" = ?', (PURGED, v))
                        n += 1
                    elif isinstance(v, str):
                        new, k = redact_json_text(v, nd, False)
                        if new is None:
                            new, k = nd.sub_text(v)
                        if k:
                            c.execute(f'UPDATE "{t}" SET "{col}" = ? WHERE "{col}" = ?', (new, v))
                            n += 1
        c.commit()
        c.execute("VACUUM")
    finally:
        c.close()
    return {"action": "sqlite_redacted", "n": n}


# --------------------------------------------------------------------------- the sweep


def sweep(app, nd: Needles | None, purged_shas: list[str], inv: dict[str, Any] | None = None) -> dict[str, Any]:
    """Search for the purged values everywhere (a sample of them), and check the structure. Never returns values: only
    where (paths, tables, S3 keys) and how many distinct values."""
    dd = Path(app.state.settings.data_dir).resolve()
    lake = Path(app.state.settings.lake_dir).resolve()
    s = nd.sample() if nd is not None else None
    hits: list[dict[str, Any]] = []
    skipped: list[str] = []
    n_files = 0
    roots = [dd] + ([lake] if not _under(lake, [dd]) else [])
    dbf = _platform_db_files(app)
    if s is not None and len(s):
        for root in roots:
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    p = Path(dirpath) / f
                    if p.resolve() in dbf:
                        continue  # searched below as the database and its file
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    n_files += 1
                    if size > TEXT_MB * 1024 * 1024:
                        skipped.append(_rel(p, dd))
                        continue
                    try:
                        b = p.read_bytes()
                    except OSError:
                        continue
                    t = s.text_hits(b)
                    bn = s.bin_hits(b) if size <= BINARY_MB * 1024 * 1024 else set()
                    if size > BINARY_MB * 1024 * 1024:
                        skipped.append(_rel(p, dd) + " (binary not searched)")
                    if t or bn:
                        hits.append({"where": "file", "path": _rel(p, dd), "distinct_text": len(t), "distinct_binary": len(bn - t)})
        db_hits = _sweep_db(app, s)
        hits += db_hits
    s3 = _sweep_s3(app, s, purged_shas)
    hits += s3.pop("hits", [])
    # structure: purged blobs gone from the store and the cache; purged models' dirs gone; exports gone
    store = app.state.lake
    for sha in purged_shas:
        with app.state.db.connect() as con:
            r = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
        if not r:
            continue
        try:
            if store.exists(r[0]):
                hits.append({"where": "blob", "sha256": sha, "key": r[0]})
        except Exception:  # noqa: BLE001
            pass
        try:
            cp = store.cache_path(r[0]) if hasattr(store, "cache_path") else None
            if cp is not None and Path(cp).exists():
                hits.append({"where": "blob_cache", "sha256": sha})
        except Exception:  # noqa: BLE001
            pass
    for it in (inv or {}).get("files", []):
        q = Path(it["path"])
        if it["action"] == "delete" and (q.is_file() or (q.is_dir() and any(x.is_file() for x in q.rglob("*")))):
            hits.append({"where": "not_deleted", "path": it["rel"], "kind": it["kind"]})
    status = "clean" if not hits else "purge_incomplete"
    return {"status": status, "at": now(), "values": ("unavailable" if s is None else len(s)),
            "values_total": (None if nd is None else len(nd)), "sampled_from_lake": bool(nd.sampled) if nd is not None else None,
            "files_searched": n_files, "skipped": skipped[:50], "hits": hits[:200], "n_hits": len(hits), **s3}


def _sweep_db(app, s: Needles) -> list[dict[str, Any]]:
    db = app.state.db
    out = []
    with db.connect() as con:
        tables = [t for t, _c in _tables(con, db.dialect)] if db.dialect == "postgres" else \
            [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        for t in tables:
            found: set[str] = set()
            for row in con.execute(f'SELECT * FROM "{t}"').fetchall():
                for v in tuple(row):
                    if v is None:
                        continue
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        if s.hit_scalar(v):
                            found.add(_canon_num(v) or "")
                        continue
                    if isinstance(v, (bytes, bytearray, memoryview)):
                        found |= s.text_hits(bytes(v))
                        continue
                    found |= s.text_hits(str(v).encode() if not isinstance(v, (dict, list)) else json.dumps(v, default=str).encode())
            if found:
                out.append({"where": "db", "table": t, "distinct": len(found)})
    if db.dialect != "postgres":  # the file itself (free pages, WAL)
        for p in _platform_db_files(app):
            if p.exists():
                b = p.read_bytes()
                t = s.text_hits(b)
                if t:
                    out.append({"where": "db_file", "path": p.name, "distinct_text": len(t)})
    return out


def _sweep_s3(app, s: Needles | None, purged_shas: list[str]) -> dict[str, Any]:
    store = app.state.lake
    if not hasattr(store, "client"):
        return {"s3": None}
    hits = []
    n = 0
    keys_purged = set()
    with app.state.db.connect() as con:
        for sha in purged_shas:
            r = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
            if r:
                keys_purged.add(store._k(r[0]))
    try:
        pag = store.client.get_paginator("list_object_versions")
        for page in pag.paginate(Bucket=store.bucket, Prefix=store._k("")):
            for v in page.get("Versions", []):
                n += 1
                if v["Key"] in keys_purged:
                    hits.append({"where": "s3", "key": v["Key"], "version": v.get("VersionId"), "why": "purged blob version left"})
                    continue
                if s is not None and len(s) and int(v.get("Size") or 0) <= BINARY_MB * 1024 * 1024:
                    kw = {"VersionId": v["VersionId"]} if v.get("VersionId") and v["VersionId"] != "null" else {}
                    b = store.client.get_object(Bucket=store.bucket, Key=v["Key"], **kw)["Body"].read()
                    t = s.text_hits(b)
                    bn = s.bin_hits(b)
                    if t or bn:
                        hits.append({"where": "s3", "key": v["Key"], "version": v.get("VersionId"), "distinct_text": len(t),
                                     "distinct_binary": len(bn - t)})
            for d in page.get("DeleteMarkers", []):
                if d["Key"] in keys_purged:
                    hits.append({"where": "s3", "key": d["Key"], "why": "delete marker of a purged blob"})
    except Exception as e:  # noqa: BLE001
        return {"s3": {"error": f"{type(e).__name__}: {e}"[:300]}, "hits": [{"where": "s3", "error": "listing failed"}]}
    return {"s3": {"bucket": store.bucket, "object_versions": n}, "hits": hits}


# --------------------------------------------------------------------------- serving a purged model


def purged_model(run_config: dict[str, Any] | None) -> dict[str, Any] | None:
    return (run_config or {}).get("purged_model")


def refusal(run_id: str, pm: dict[str, Any]) -> dict[str, Any]:
    return {"code": "purged_model", "message": f"The model of run {run_id} was purged ({pm.get('why')}; purge {pm.get('purge_id')}): "
                                               "retrain on the new version.", "purge_id": pm.get("purge_id")}


def refuse_purged(rec: dict[str, Any] | None) -> None:
    """409 `purged_model` for a run whose model a purge removed (predict, score, SHAP)."""
    pm = purged_model((rec or {}).get("config"))
    if pm:
        from fastapi import HTTPException

        raise HTTPException(409, refusal(rec["id"], pm))
