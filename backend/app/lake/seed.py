"""A deterministic CSV generator for checking uploads at scale (plan §9: "big files come from `python -m app.lake.seed`").
It is a data generator, not a test.

    python -m app.lake.seed --rows 10000000 --out /path/applications.csv

About 115 bytes a row (10M rows ≈ 1.15 GB). Columns: `zip` and `account_no` are digit codes with leading zeros (they
must stay text), `branch` a 4-digit code with leading zeros, `app_date` ISO dates, `amount` / `income` decimals,
`tenure_months` / `age` whole numbers, `segment` / `channel` / `product` categories, `note` free text, `y` a 0/1 label with
some missing. Every value is arithmetic on the row number, so the same arguments give the same bytes.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

SQL = """
SELECT lpad(CAST((i * 7919) % 100000 AS VARCHAR), 5, '0') AS zip,
       lpad(CAST((i * 104729) % 10000000000 AS VARCHAR), 10, '0') AS account_no,
       lpad(CAST(i % 2000 AS VARCHAR), 4, '0') AS branch,
       CAST(DATE '2019-01-01' + CAST((i * 13) % 2000 AS INTEGER) AS VARCHAR) AS app_date,
       CAST(round(((i * 37) % 9000000) / 100.0 + 50, 2) AS VARCHAR) AS amount,
       CAST(round(((i * 53) % 25000000) / 100.0 + 12000, 2) AS VARCHAR) AS income,
       (i * 31) % 361 AS tenure_months,
       18 + (i * 17) % 60 AS age,
       ['retail', 'sme', 'corporate', 'private'][1 + CAST(i % 4 AS INTEGER)] AS segment,
       ['branch', 'online', 'broker'][1 + CAST((i // 7) % 3 AS INTEGER)] AS channel,
       ['personal-loan-36m', 'personal-loan-60m', 'car-loan-48m', 'overdraft-rolling'][1 + CAST((i * 11) % 4 AS INTEGER)] AS product,
       ['first loan', 'refinance', 'car purchase', 'home repair', 'consolidation'][1 + CAST((i * 3) % 5 AS INTEGER)]
         || ' ref ' || CAST((i * 2654435761) % 1000000 AS VARCHAR) AS note,
       CASE WHEN i % 997 = 0 THEN NULL WHEN (i * 2654435761) % 1000 < 120 THEN 1 ELSE 0 END AS y
FROM range(?, ?) t(i) ORDER BY i
"""


def write(out: Path, rows: int, start: int = 0) -> dict:
    t0 = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = true")
    path = str(out).replace("'", "''")
    con.execute(f"COPY ({SQL}) TO '{path}' (FORMAT csv, HEADER true)", [start, start + rows])
    con.close()
    return {"out": str(out), "rows": rows, "bytes": out.stat().st_size, "seconds": round(time.time() - t0, 2)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(write(a.out, a.rows, a.start))


if __name__ == "__main__":
    main()
