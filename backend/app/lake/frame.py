"""P11 (plan §5.9, R12, R13): the training frame, its memory budget and the recorded sample.

    plan(cfg, ref)                → the per-column estimate (R12) against the budget; sample or not
    read_ids_label(ref, label, p) → __row_id + label only (to draw the split masks and the sample without the features)
    draw(ids, y, …)               → the deterministic stratified sample: splitmix64(__row_id ⊕ seed·φ) (R13)
    read_rows(ref, ids, cols, p)  → exactly those rows, every part's bytes hashed while they are parsed (DL3)
    commit_sample(cfg, ids, spec) → the kept ids as a content-addressed 'sample' blob pinned by the run
    holdout_batches(cfg, holdout) → the run's whole holdout in batches of HOLDOUT_BATCH_ROWS
    score_holdout(predictor, …)   → predictions of every holdout row + metrics over all of them (one evaluate_predictions)

**The budget.** The frame the worker would hold in pandas (engine_v1 dtypes) is estimated per column from the committed
table: 8 B per number / bool cell; per text cell (VARCHAR, and DATE / TIMESTAMP, which engine_v1 reads as text) a
pointer plus, when it is not null, the str object: 8 + share_non_null × (FRAME_STR_BASE − 8 + mean length). The mean
length and the null counts come from one DuckDB scan of the table (deterministic: the same table gives the same
estimate, so the same rates and the same sample). The budget is `frame_budget_mb` of the run when given, else
ENSEMBLE_TRAIN_FRAME_SHARE (10 %) × the worker's RAM, capped by the calibrated AutoGluon fit peak of the run's preset.

**Under the budget** nothing changes: `frames.read` of the whole table, as before P11.

**Over it** (Parquet versions with a pinned split; a CSV-era version has no `__row_id` and is read whole until P10):
1. prune columns AutoGluon would drop anyway: all null or > 99 % null, constant, ID-like text (unique, short; never
   the label, a text column or a column with a wizard type);
2. sample the training rows only (folds ≥ 0 with a label): keep a row when splitmix64(__row_id ⊕ seed·φ) mod 10⁶ <
   10⁶ · rate[stratum]. Classification: strata = classes, a class with ≤ SAMPLE_MIN_CLASS_ROWS rows is kept whole and
   the others share the rest of the budget; regression: one rate, per-decile counts recorded. Rates are decimal
   strings (6 places), so the selection never depends on float formatting;
3. the tuning slice is kept whole; the in-memory holdout (leaderboard, importance, ladder…) is a splitmix64 sample of
   the holdout at the same rate, but **holdout metrics and predictions cover every holdout row**, read from the lake
   and scored in batches.

`sample_sha256` = sha256("ensemble.sample/1\\n" + int64-LE kept __row_id, sorted) (§3.3, `manifest.sample_sha256`).
Two runs with the same sample_sha256 trained on provably the same rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
import pandas as pd

from .. import config, frames
from . import manifest as M

log = logging.getLogger("ensemble.lake.frame")

ROW_ID = "__row_id"
RATE_SCALE = 1_000_000
_U64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15
#: the holdout's in-memory diagnostics sample uses its own stream (seed + 1), so it is independent of the training sample
_HOLDOUT_SALT = 1
_runs: dict[str, dict[str, Any]] = {}  # run_id → where the full holdout is (read by holdout_batches after the fit)


# --------------------------------------------------------------------------- RAM and budget


def _cgroup_limit() -> int | None:
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(p).read_text().strip()
        except OSError:
            continue
        if v.isdigit() and int(v) < (1 << 60):
            return int(v)
    return None


def worker_ram() -> tuple[int, str]:
    """(bytes, where the number came from): ENSEMBLE_WORKER_RAM_MB, else the cgroup limit when lower, else the host's RAM."""
    mb = config.worker_ram_mb()
    if mb:
        return mb * 2 ** 20, "ENSEMBLE_WORKER_RAM_MB"
    phys = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    cg = _cgroup_limit()
    if cg and cg < phys:
        return cg, "the cgroup memory limit"
    return phys, "the host's RAM"


def preset_of(cfg: dict[str, Any]) -> str:
    p = (cfg.get("fit_kwargs") or {}).get("presets", cfg.get("presets"))
    if isinstance(p, (list, tuple)):
        p = p[0] if p else None
    return str(p or "medium_quality")


def budget(cfg: dict[str, Any]) -> dict[str, Any]:
    ram, src = worker_ram()
    preset = preset_of(cfg)
    factor = config.FIT_PEAK_PER_FRAME_BYTE.get(preset)
    if cfg.get("frame_budget_mb"):
        b = int(float(cfg["frame_budget_mb"]) * 2 ** 20)
        rule = f"frame_budget_mb = {int(cfg['frame_budget_mb'])} (set on the run)"
    else:
        share = config.train_frame_share()
        b = int(share * ram)
        rule = f"{share:.0%} of the worker's RAM ({ram / 2 ** 30:.1f} GB, {src})"
        if factor:
            cap = int(config.FIT_RAM_SHARE * ram / factor)
            if cap < b:
                b = cap
                rule += (f", capped so that the {preset} fit's calibrated peak (× {factor:g} the frame) stays under "
                         f"{config.FIT_RAM_SHARE:.0%} of it")
    return {"bytes": b, "mb": round(b / 2 ** 20, 1), "rule": rule, "ram_bytes": ram, "ram_source": src, "preset": preset,
            "fit_peak_per_frame_byte": factor}


# --------------------------------------------------------------------------- the estimate (R12)


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _scan(lt: frames.LakeTable, cols: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One DuckDB pass: non-null count of every column; mean length, min = max and approximate distinct of text ones."""
    from . import duck

    sel: list[str] = ["count(*)"]
    slots: list[tuple[str, str, int, int]] = []
    for c in cols:
        x, kind = _q(c["name"]), frames.kind_of_type(c["type"])
        parts = [f"count({x})", f"CAST(min({x}) AS VARCHAR) IS NOT DISTINCT FROM CAST(max({x}) AS VARCHAR)"]
        if kind == "text":
            parts += [f"avg(length(CAST({x} AS VARCHAR)))", f"approx_count_distinct({x})"]
        slots.append((c["name"], kind, len(sel), len(parts)))
        sel += parts
    with duck.job() as con:
        row = con.execute(f"SELECT {', '.join(sel)} FROM {frames.lake_sql(lt)}").fetchone()
    n = int(row[0])
    out: dict[str, dict[str, Any]] = {}
    for name, kind, i, w in slots:
        v = row[i:i + w]
        d: dict[str, Any] = {"kind": kind, "non_null": int(v[0]), "nulls": n - int(v[0]), "constant": bool(v[1])}
        if kind == "text":
            d["mean_len"] = float(v[2]) if v[2] is not None else 0.0
            d["distinct"] = int(v[3] or 0)
        out[name] = d
    return out


def cell_bytes(kind: str, non_null_share: float, mean_len: float | None) -> float:
    if kind != "text":
        return float(config.FRAME_NUM_BYTES)
    return 8.0 + non_null_share * (config.FRAME_STR_BASE - 8 + float(mean_len or 0.0))


def estimate(ref: frames.Table, drop: Iterable[str] = ()) -> dict[str, Any]:
    """R12's per-column estimate of the engine_v1 frame of `ref` without the `drop` columns (index included)."""
    lt = frames.lake_table(ref)
    drop = set(drop)
    cols = [c for c in lt.schema if c["name"] not in drop and c["name"] != ROW_ID]
    t0 = time.perf_counter()
    scan = _scan(lt, cols) if cols and lt.n_rows else {}
    n = lt.n_rows
    per = []
    for c in cols:
        s = scan.get(c["name"]) or {"kind": frames.kind_of_type(c["type"]), "non_null": n, "nulls": 0}
        share = (s["non_null"] / n) if n else 1.0
        b = cell_bytes(s["kind"], share, s.get("mean_len"))
        per.append({"name": c["name"], "type": c["type"], "kind": s["kind"], "bytes_per_cell": round(b, 1),
                    **({"mean_len": round(s["mean_len"], 1)} if s.get("mean_len") is not None else {}),
                    "nulls": s["nulls"], "constant": s.get("constant", False),
                    **({"distinct": s["distinct"]} if "distinct" in s else {})})
    bpr = 8.0 + sum(p["bytes_per_cell"] for p in per)  # + the int64 index (__row_id)
    est = int(math.ceil(n * bpr))
    return {"rows": n, "columns": per, "table_columns": [c["name"] for c in lt.schema if c["name"] != ROW_ID], "bytes_per_row": round(bpr, 1), "bytes": est, "mb": round(est / 2 ** 20, 1),
            "load_peak_mb": round(est * config.FRAME_LOAD_PEAK / 2 ** 20, 1), "scan_seconds": round(time.perf_counter() - t0, 2),
            "rule": (f"R12: {config.FRAME_NUM_BYTES} B per number / bool cell; per text cell (dates are text under "
                     f"engine_v1) 8 + share non-null × ({config.FRAME_STR_BASE - 8} + mean length) B; + 8 B index per row"),
            "table_sha256": lt.table_sha256}


def plan(cfg: dict[str, Any], ref: Any) -> dict[str, Any]:
    """Estimate vs budget for this run. `sample` is True only for a Parquet table with a pinned split over its budget."""
    drop = [c for c in cfg.get("drop_columns") or [] if c != cfg.get("label")]
    bud = budget(cfg)
    parquet = isinstance(ref, frames.Table) and frames.is_parquet(ref)
    if not parquet:
        return {"parquet": False, "budget": bud, "estimate": None, "sample": False,
                "why": "a CSV-era version (no __row_id) is read whole: sampling needs a Parquet table (P10 migrates it)"}
    est = estimate(ref, drop)
    over = est["bytes"] > bud["bytes"]
    pinned = bool((cfg.get("split_pin") or {}).get("blob_sha256"))
    sample = over and pinned and not cfg.get("import")
    why = ("the estimated frame fits the budget: every row is read" if not over else
           "over the budget: a recorded sample of the training rows" if sample else
           "over the budget, but the run has no pinned Parquet split (or imports a predictor): read whole")
    return {"parquet": True, "budget": bud, "estimate": est, "sample": sample, "over_budget": over, "why": why}


def compact(p: dict[str, Any]) -> dict[str, Any]:
    """The plan as recorded on the run (the per-column list is kept: it is the estimate's evidence)."""
    return {k: v for k, v in p.items() if k not in ("nonnull",)}


# --------------------------------------------------------------------------- reading rows by __row_id


def _has_nulls(p: dict[str, Any]) -> dict[str, bool]:
    return {c["name"]: c["nulls"] > 0 for c in ((p.get("estimate") or {}).get("columns") or [])}


def _v1(arr: Any, name: str, table_has_nulls: bool) -> pd.Series:
    """engine_v1 of one column of a row subset, typed as the WHOLE table reads: an int / bool column with nulls anywhere in
    the table is float64 / object even when the rows read have none (so a sampled frame has the full read's dtypes)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    t = arr.type
    if table_has_nulls and arr.null_count == 0:
        if pa.types.is_integer(t):
            return pd.Series(pc.cast(arr, pa.float64()).to_numpy(zero_copy_only=False), name=name)
        if pa.types.is_boolean(t):
            return pd.Series(arr.to_pandas(), name=name).astype(object)
    return frames._v1_column(arr, name)


def _to_frame(at: Any, cols: list[str], nulls: dict[str, bool]) -> pd.DataFrame:
    idx = pd.Index(at.column(ROW_ID).to_numpy(zero_copy_only=False).astype("int64"), name=None)
    df = pd.DataFrame({c: _v1(at.column(c), c, nulls.get(c, True)) for c in cols}, columns=cols)
    df.index = idx
    return df


def _part_buffer(p: dict[str, Any], version_id: str, hash_into: Any) -> Any:
    """The part's bytes (memory-mapped), hashed first when asked: the rows are then parsed from these same bytes."""
    import pyarrow as pa

    try:
        buf = pa.memory_map(str(p["path"]), "r").read_buffer()
    except FileNotFoundError as e:
        raise frames.FrameError(f"A file of {version_id} ({p['sha256'][:12]}…) is missing from the lake.") from e
    if hash_into is not None:
        h = hashlib.sha256()
        mv = memoryview(buf)
        step = 8 * 1024 * 1024
        for i in range(0, len(mv), step):
            h.update(mv[i:i + step])
        hash_into.add_part(p["role"], h.hexdigest(), p["sha256"])
    return buf


def _rg_range(pf: Any, rg: int, col: int) -> tuple[int, int] | None:
    st = pf.metadata.row_group(rg).column(col).statistics
    if st is None or not st.has_min_max:
        return None
    return int(st.min), int(st.max)


def iter_rows(ref: frames.Table, ids: np.ndarray, columns: list[str], p: dict[str, Any], *, batch_rows: int | None = None,
              hash_into: Any = None) -> Iterator[pd.DataFrame]:
    """The rows whose __row_id is in `ids` (sorted, unique), `columns` only, as engine_v1 frames of about `batch_rows`
    rows (one frame when None), in __row_id order. Row groups without a wanted id are skipped by their statistics;
    with `hash_into` every part is hashed in full (skipped row groups included), so the data gate sees the table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    lt = frames.lake_table(ref)
    ids = np.asarray(ids, dtype="int64")
    nulls = _has_nulls(p)
    pending: list[Any] = []
    n_pending = 0
    for part in lt.parts:
        buf = _part_buffer(part, ref.version_id, hash_into)
        if not len(ids):
            continue
        pf = pq.ParquetFile(pa.BufferReader(buf))
        rid_col = pf.schema_arrow.get_field_index(ROW_ID)
        for rg in range(pf.num_row_groups):
            rng = _rg_range(pf, rg, rid_col)
            if rng is not None:
                a, b = np.searchsorted(ids, [rng[0], rng[1] + 1])
                if a == b:
                    continue
                sub = ids[a:b]
            else:
                sub = ids
            rid = pf.read_row_group(rg, columns=[ROW_ID]).column(0).to_numpy(zero_copy_only=False).astype("int64")
            pos = np.searchsorted(sub, rid)
            mask = (pos < len(sub)) & (sub[np.minimum(pos, len(sub) - 1)] == rid)
            if not mask.any():
                continue
            t = pf.read_row_group(rg, columns=[ROW_ID, *columns]).filter(pa.array(mask))
            pending.append(t)
            n_pending += t.num_rows
            if batch_rows and n_pending >= batch_rows:
                yield _to_frame(pa.concat_tables(pending), columns, nulls)
                pending, n_pending = [], 0
        del buf
    if pending:
        yield _to_frame(pa.concat_tables(pending), columns, nulls)
    elif batch_rows is None:
        yield pd.DataFrame({c: pd.Series([], dtype=object) for c in columns}, index=pd.Index([], dtype="int64"))


def read_rows(ref: frames.Table, ids: np.ndarray, columns: list[str], p: dict[str, Any], *, hash_into: Any = None) -> pd.DataFrame:
    return next(iter_rows(ref, ids, columns, p, hash_into=hash_into))


def read_ids_label(ref: frames.Table, label: str, p: dict[str, Any]) -> pd.DataFrame:
    """Every row's __row_id (the index) and label, in read order: what the split masks and the sample are drawn on."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    lt = frames.lake_table(ref)
    pieces = []
    for part in lt.parts:
        try:
            pieces.append(pq.read_table(str(part["path"]), columns=[ROW_ID, label]))
        except FileNotFoundError as e:
            raise frames.FrameError(f"A file of {ref.version_id} ({part['sha256'][:12]}…) is missing from the lake.") from e
    at = pa.concat_tables(pieces) if len(pieces) > 1 else pieces[0]
    return _to_frame(at, [label], _has_nulls(p))


# --------------------------------------------------------------------------- the sample (R13)


def _seed_mix(seed: int, salt: int = 0) -> np.uint64:
    return np.uint64(((int(seed) + salt) * _GOLDEN) & _U64)


def _u(ids: np.ndarray, seed: int, salt: int = 0) -> np.ndarray:
    """splitmix64(__row_id ⊕ seed·φ) mod 10⁶ for every id."""
    z = M.splitmix64(np.asarray(ids, dtype="int64").astype(np.uint64) ^ _seed_mix(seed, salt))
    return (z % np.uint64(RATE_SCALE)).astype("int64")


def rate_str(r: float) -> str:
    """A rate as the decimal string that is recorded and applied (6 places, rounded down, within [0, 1])."""
    r = min(1.0, max(0.0, float(r)))
    return str(Decimal(repr(r)).quantize(Decimal("0.000001"), rounding=ROUND_FLOOR))


def _thr(rs: str) -> int:
    return int(Decimal(rs) * RATE_SCALE)


def is_classification(cfg: dict[str, Any], y: pd.Series) -> bool:
    pt = cfg.get("problem_type")
    if pt in ("binary", "multiclass"):
        return True
    if pt in ("regression", "quantile") or cfg.get("task") == "regression":
        return False
    if y.dtype.kind not in "iuf":
        return True
    v = y.dropna()
    return bool(len(v)) and v.nunique() <= 20 and bool((v == np.round(v)).all())


def draw(ids: np.ndarray, y: pd.Series, *, seed: int, rate: float, classification: bool) -> tuple[np.ndarray, dict[str, Any]]:
    """(keep mask over ids, spec). Deterministic in (ids, y, seed, rate): no engine hash, no RNG state."""
    ids = np.asarray(ids, dtype="int64")
    n = len(ids)
    u = _u(ids, seed)
    target = rate * n
    per: dict[str, Any] = {}
    if classification:
        ys = pd.Series(y.to_numpy()).map(lambda v: None if pd.isna(v) else str(v))
        counts = ys.value_counts(dropna=False)
        small = {k: int(c) for k, c in counts.items() if c <= config.SAMPLE_MIN_CLASS_ROWS}
        rest = n - sum(small.values())
        r_rest = (target - sum(small.values())) / rest if rest else 1.0
        rs_rest = rate_str(r_rest)
        rates = {str(k): ("1.000000" if k in small else rs_rest) for k in counts.index}
        thr = ys.map(lambda k: _thr(rates[str(k)])).to_numpy(dtype="int64")
        keep = u < thr
        for k in counts.index:
            m = (ys == k).to_numpy() if k is not None else ys.isna().to_numpy()
            per[str(k)] = {"rows": int(m.sum()), "kept": int(keep[m].sum()), "rate": rates[str(k)],
                           **({"whole": True} if k in small else {})}
        strata = f"class of the label (a class with ≤ {config.SAMPLE_MIN_CLASS_ROWS:,} training rows is kept whole)"
    else:
        rs = rate_str(rate)
        rates = {"*": rs}
        keep = u < _thr(rs)
        yv = pd.to_numeric(pd.Series(y.to_numpy()), errors="coerce").to_numpy(dtype=float)
        if n and np.isfinite(yv).any():
            edges = np.nanquantile(yv, np.linspace(0.1, 0.9, 9))
            dec = np.searchsorted(edges, yv, side="right")
            for d in range(10):
                m = dec == d
                per[f"decile {d + 1}"] = {"rows": int(m.sum()), "kept": int(keep[m].sum()), "rate": rs}
        strata = "deciles of the label (one rate; the per-decile counts are recorded)"
    spec = {"method": "splitmix64", "format": "ensemble.sample/1", "seed": int(seed),
            "hash": "keep a row when splitmix64(__row_id XOR seed*0x9E3779B97F4A7C15) mod 1000000 < 1000000 * rate[stratum]",
            "rate": rate_str(rate), "rates": rates, "strata": strata, "per_stratum": per,
            "rows_total": int(n), "rows_used": int(keep.sum())}
    return keep, spec


def holdout_subset(ids: np.ndarray, *, seed: int, rate: float) -> np.ndarray:
    """The in-memory diagnostics rows of the holdout (its own stream): a keep mask."""
    return _u(ids, seed, _HOLDOUT_SALT) < _thr(rate_str(rate))


# --------------------------------------------------------------------------- the sample blob


def commit_sample(cfg: dict[str, Any], ids: np.ndarray, spec: dict[str, Any]) -> dict[str, Any]:
    """Write the kept ids as a content-addressed Parquet blob (kind 'sample') that the run pins (blob_refs run/sample)."""
    import secrets

    import pyarrow as pa
    import pyarrow.parquet as pq

    from ..catalog import schema as S
    from ..db import Database
    from . import splitstore as SS

    store = frames._store()
    db = Database(Path(cfg["db_path"]))
    ids = np.unique(np.asarray(ids, dtype="int64"))
    tmp = store.tmp_dir("sample-" + secrets.token_hex(4))
    try:
        f = tmp / "sample.parquet"
        pq.write_table(pa.table({ROW_ID: pa.array(ids, pa.int64())}), f, compression="zstd")
        bsha, nbytes = M.file_sha256(f)
        key = store.put_file(f, bsha, "parquet")
    finally:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
    vid = (cfg.get("input_table") or {}).get("version_id") or cfg.get("dataset_id")
    with db.connect() as con:
        _, team = SS._team(con, vid)
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'sample', ?, ?, 'live', ?)"
                    " ON CONFLICT (sha256) DO UPDATE SET state = 'live' WHERE state IN ('pending', 'live')",
                    (bsha, key, nbytes, int(len(ids)), S.now()))
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'run', ?, 'sample', ?, ?)", (bsha, cfg["run_id"], team, S.now()))
    return {"blob_sha256": bsha, "bytes": int(nbytes), "rows": int(len(ids))}


# --------------------------------------------------------------------------- column types (as the fit casts them)


def cast_column_types(cfg: dict[str, Any], df: pd.DataFrame) -> pd.DataFrame:
    """The wizard's column types applied to a frame in place, as `worker.apply_column_types` does to train / holdout."""
    for col, t in (cfg.get("column_types") or {}).items():
        if col not in df.columns or col == cfg.get("label"):
            continue
        if t == "number":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = df[col].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    return df


# --------------------------------------------------------------------------- the training frame


def prune(cfg: dict[str, Any], p: dict[str, Any]) -> list[dict[str, Any]]:
    """§5.9 step 1 (only when sampling): columns AutoGluon drops anyway. Never the label, a text column or a typed column."""
    keep = {cfg.get("label"), *(cfg.get("text_columns") or []), *(cfg.get("column_types") or {}).keys()}
    n = (p.get("estimate") or {}).get("rows") or 0
    out = []
    for c in (p.get("estimate") or {}).get("columns") or []:
        if c["name"] in keep or not n:
            continue
        nn = n - c["nulls"]
        if nn == 0 or c["nulls"] / n > 0.99:
            out.append({"name": c["name"], "why": f"{c['nulls'] / n:.1%} null"})
        elif c.get("constant") and c["nulls"] == 0:
            out.append({"name": c["name"], "why": "one value in every row"})
        elif c["kind"] == "text" and c.get("distinct", 0) >= 0.99 * nn and (c.get("mean_len") or 0) <= 40 and nn > 1000:
            out.append({"name": c["name"], "why": f"ID-like (≈ {c.get('distinct', 0):,} distinct of {nn:,} values)"})
    return out


def remember(run_id: str | None, d: dict[str, Any]) -> None:
    if run_id:
        _runs[run_id] = d


def holdout_batches(cfg: dict[str, Any], holdout: pd.DataFrame, batch_rows: int | None = None) -> Iterator[pd.DataFrame]:
    """Every holdout row of the run in batches: from the lake when the run was sampled (the in-memory holdout is then a
    diagnostics subset), else slices of the in-memory holdout."""
    b = int(batch_rows or config.HOLDOUT_BATCH_ROWS)
    src = _runs.get(cfg.get("run_id") or "")
    if src and src.get("holdout_ids") is not None:
        for df in iter_rows(src["ref"], src["holdout_ids"], src["columns"], src["plan"], batch_rows=b):
            yield cast_column_types(cfg, df)
        return
    for i in range(0, max(len(holdout), 1), b):
        yield holdout.iloc[i:i + b]


def full_holdout_rows(cfg: dict[str, Any], holdout: pd.DataFrame) -> int:
    src = _runs.get(cfg.get("run_id") or "")
    return int(len(src["holdout_ids"])) if src and src.get("holdout_ids") is not None else int(len(holdout))


# --------------------------------------------------------------------------- scoring the whole holdout in batches


def score_holdout(predictor: Any, batches: Iterable[pd.DataFrame], label: str, out_csv: Path | None,
                  prediction_frame: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Predict every holdout row batch by batch (predictions appended to `out_csv`), then compute the metrics once over all
    rows, exactly as `predictor.evaluate` does (predict_proba when it can, then evaluate_predictions). Returns (the
    holdout result, {rows, batches, seconds})."""
    t0 = time.time()
    ys, preds, weights = [], [], []
    n = k = 0
    first = True
    sw = getattr(predictor, "sample_weight", None) if getattr(predictor, "weight_evaluation", False) else None
    quantile = predictor.problem_type == "quantile"
    for batch in batches:
        if not len(batch):
            continue
        k += 1
        n += len(batch)
        y_pred = predictor.predict_proba(batch) if predictor.can_predict_proba else predictor.predict(batch)
        ys.append(batch[label])
        preds.append(y_pred)
        if sw and sw in batch:
            weights.append(batch[sw])
        if out_csv is not None:
            if quantile:
                pf = prediction_frame(predictor, batch, label)
            else:
                pf = pd.DataFrame({"row": batch.index, "y_true": batch[label].to_numpy()})
                if predictor.can_predict_proba:
                    pf["y_pred"] = predictor.predict_from_proba(y_pred).to_numpy()
                    for c in y_pred.columns:
                        pf[f"p_{c}"] = y_pred[c].to_numpy()
                else:
                    pf["y_pred"] = pd.Series(y_pred).to_numpy()
            pf.to_csv(out_csv, index=False, mode="w" if first else "a", header=first)
            first = False
    if not n:
        raise ValueError("The holdout has no rows with a label: nothing to score.")
    y_true = pd.concat(ys)
    y_all = pd.concat(preds) if isinstance(preds[0], (pd.DataFrame, pd.Series)) else np.concatenate(preds)
    scores = predictor.evaluate_predictions(y_true=y_true, y_pred=y_all, sample_weight=pd.concat(weights) if weights else None,
                                            silent=True)
    yhat = predictor.predict_from_proba(y_all) if predictor.can_predict_proba else None
    return {"scores": scores, "y_true": y_true, "y_pred": yhat}, {"rows": int(n), "batches": int(k),
                                                                   "batch_rows": int(config.HOLDOUT_BATCH_ROWS),
                                                                   "seconds": round(time.time() - t0, 2)}


def card_text(info: dict[str, Any]) -> str:
    """'trained on 2.0M of 40M training rows (stratified sample, seed 42); holdout scored on all 5M rows'."""
    fr = info.get("frame") or {}
    if not fr.get("sampled"):
        return (f"trained on all {info.get('train_rows', 0):,} training rows; holdout scored on all "
                f"{info.get('holdout_rows', 0):,} rows")
    sp = fr.get("sample_spec") or {}
    return (f"trained on {fr['rows_used']:,} of {fr['train_rows_total']:,} training rows (stratified sample, seed "
            f"{sp.get('seed')}, sample_sha256 {str(fr.get('sample_sha256'))[:12]}…); holdout scored on all "
            f"{info.get('holdout_rows', 0):,} rows")


def spec_json(spec: dict[str, Any] | None) -> str | None:
    return None if spec is None else json.dumps(spec, sort_keys=True, separators=(",", ":"))
