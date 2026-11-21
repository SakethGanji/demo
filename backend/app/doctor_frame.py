"""F-X1 C2: the bounded frame the Data Doctor and Feature Lab work on (plan §7 risk 4: no full-table pandas read in a
request).

A version of at most `config.doctor_rows()` rows (200,000) is read whole, as before. A larger Parquet version is read
as a deterministic sample of about that many rows: a row is in it when splitmix64(__row_id XOR seed*phi) mod 10^6 is
below 10^6 * rate (the same hash P11's training frame uses, lake.frame._u), so the same version gives the same rows
every time, on any engine. Only `__row_id` is read in full (8 bytes a row); the sample's rows are fetched with a semi
join. The sample is recorded: its rule, seed, rate, size and the sha256 of its ids.

Row numbers in the Doctor's evidence and fixes are positions in the version (in `__row_id` order, as apply reads
them). On a sample they are positions in the sample, so `Frame.remap` turns them into the version's positions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import config, frames

#: salt for the Doctor's sample (a different stream from the training frame's)
SALT = 7


@dataclass
class Frame:
    df: pd.DataFrame                    # index 0..n-1 (positions in the frame)
    n_rows: int                         # rows in the version
    sample: dict[str, Any] | None       # None: the whole version
    positions: np.ndarray | None = None  # sample position -> the version's position
    row_ids: np.ndarray | None = None    # sample position -> __row_id

    def remap(self, rows: list[Any]) -> list[Any]:
        if self.positions is None:
            return rows
        return [int(self.positions[r]) if isinstance(r, (int, np.integer)) and 0 <= r < len(self.positions) else r
                for r in rows]


def n_rows(version_id: str) -> int:
    from .lake.pages import source

    return int(source(version_id).n_rows)


def load(version_id: str, *, rows: int | None = None, columns: list[str] | None = None) -> Frame:
    """The version whole when it is small (or CSV-era), else the recorded splitmix64 sample of about `rows` rows."""
    from .lake import duck
    from .lake import frame as LF
    from .lake.pages import ROW_ID, q, source

    budget = int(rows or config.doctor_rows())
    src = source(version_id)
    if src.storage != "parquet" or src.n_rows <= budget:
        return Frame(frames.read_version(version_id, columns).reset_index(drop=True), int(src.n_rows), None)
    names = [c for c, _ in src.columns if columns is None or c in columns]
    rate = budget / src.n_rows
    thr = int(LF.RATE_SCALE * rate)
    with duck.job() as con:
        ids = np.sort(con.execute(f"SELECT {q(ROW_ID)} FROM {src.rel}").fetchnumpy()[ROW_ID].astype("int64"))
        sel = ids[LF._u(ids, config.SEED, SALT) < thr]
        pos = np.searchsorted(ids, sel)
        del ids
        import pyarrow as pa

        con.register("_doctor_ids", pa.table({ROW_ID: pa.array(sel, pa.int64())}))
        at = con.execute(f"SELECT t.{q(ROW_ID)}, {', '.join('t.' + q(c) for c in names)} FROM {src.rel} t"
                         f" SEMI JOIN _doctor_ids s ON t.{q(ROW_ID)} = s.{q(ROW_ID)} ORDER BY t.{q(ROW_ID)}").to_arrow_table()
    # the job connection runs with preserve_insertion_order = false: sort here so row k is sel[k], whatever DuckDB did
    at = at.sort_by(ROW_ID)
    if not np.array_equal(at.column(ROW_ID).to_numpy(zero_copy_only=False), sel):
        raise RuntimeError("the sampled rows do not match the sampled ids")
    df = pd.DataFrame({c: frames._v1_column(at.column(c), c) for c in names}, columns=names)
    info = {"rows": int(len(df)), "of": int(src.n_rows), "rate": round(rate, 6), "seed": config.SEED, "salt": SALT,
            "rule": f"splitmix64(__row_id XOR (seed+salt)*0x9E3779B97F4A7C15) mod {LF.RATE_SCALE} < {thr}",
            "ids_sha256": hashlib.sha256(sel.astype("<i8").tobytes()).hexdigest(),
            "note": f"checked on a deterministic sample of {len(df):,} of {src.n_rows:,} rows: counts in the findings are "
                    "the sample's; row numbers are the version's"}
    return Frame(df, int(src.n_rows), info, positions=pos.astype("int64"), row_ids=sel)


def remap_report(out: dict[str, Any], fr: Frame) -> dict[str, Any]:
    """Sample positions → the version's positions in a Data Report: evidence tables whose first column is "row", the
    unit-change preview, and the row lists of drop_rows / relabel fixes."""
    if fr.positions is None:
        return out
    seen: set[int] = set()  # selected_fixes holds the findings' own transform dicts: map each once
    for f in out.get("findings") or []:
        ev = f.get("evidence") or {}
        if (ev.get("columns") or [None])[0] == "row":
            ev["rows"] = [[*fr.remap([r[0]]), *r[1:]] for r in ev.get("rows") or []]
        pv = f.get("preview")
        if isinstance(pv, dict):
            for r in pv.get("rows") or []:
                if isinstance(r, dict) and "row" in r:
                    r["row"] = fr.remap([r["row"]])[0]
        for t in [*((f.get("fix") or {}).get("transforms") or [])]:
            _remap_fix(t, fr, seen)
    for t in out.get("selected_fixes") or []:
        _remap_fix(t, fr, seen)
    out["rows"] = fr.n_rows
    out["sample"] = fr.sample
    return out


def _remap_fix(t: Any, fr: Frame, seen: set[int]) -> None:
    if isinstance(t, dict) and id(t) not in seen and t.get("op") in ("drop_rows", "relabel") and isinstance(t.get("rows"), list):
        seen.add(id(t))
        t["rows"] = fr.remap(t["rows"])
