"""G2's sample: the performance tape of the F18 loan book (`loan-stream`), for vintage curves.

The F18 sample says *whether* each loan defaulted (`defaulted`), not *when*. A vintage curve needs the month of default
after origination (months on book, MOB), and how long each loan has been watched. This module adds that, without
changing the F18 generator: for every loan of the training book (2024) and of the 12 stream months (2025) it draws,
seeded per loan, the MOB at which a defaulter first reached 90 days past due:

    MOB_default = 1 + ⌊G⌋,  G ~ Gamma(k = 2.2, θ = 3.0),  capped at 24   (mean ≈ 7.6 months, mode ≈ 4)

Non-defaulters never default. The tape is observed **as of 2026-03-31** (the day the last stream month's labels arrive),
so a default whose month lies after that date is not seen yet: its `default_date` is blank, as on a real tape. A loan's
observed months on book are the whole months between its origination month and the as-of month.

Because the stream's online applications default more from 2025-07 (F18's concept change, logit + 1.3), the 2025-Q3 and
2025-Q4 cohorts climb faster than the earlier ones: the vintage chart shows what the labelled performance shows.

Everything is seeded: `performance_tape(seed)` always gives the same rows.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .samples_f18 import generate_loan_stream, stream_month

AS_OF = date(2026, 3, 31)
GAMMA_SHAPE = 2.2
GAMMA_SCALE = 3.0
MAX_MOB = 24
SPEC: dict[str, Any] = {
    "dataset": "loan-stream", "as_of": AS_OF.isoformat(), "id_column": "application_id",
    "open_column": "origination_date", "default_column": "default_date",
    "rule": f"MOB_default = 1 + ⌊G⌋, G ~ Gamma({GAMMA_SHAPE}, {GAMMA_SCALE}), capped at {MAX_MOB}; defaults after "
            f"{AS_OF.isoformat()} are not observed yet",
    "source": "samples_g2.performance_tape (seeded; the F18 generator is unchanged)",
}


def _add_months(d: pd.Series, k: np.ndarray) -> pd.Series:
    """The first day of the month k months after each date's month."""
    m = d.dt.year.to_numpy() * 12 + d.dt.month.to_numpy() - 1 + k
    return pd.to_datetime({"year": m // 12, "month": m % 12 + 1, "day": 1})


def performance_tape(seed: int = 42) -> pd.DataFrame:
    """application_id, origination_date, default_date (blank: no default seen by AS_OF), observed_until."""
    book, _ = generate_loan_stream(seed=seed)
    frames = [book[["application_id", "application_date", "defaulted"]]]
    for m in range(1, 13):
        x, lab, _ = stream_month(m, seed)
        frames.append(x[["application_id", "application_date"]].assign(defaulted=lab["defaulted"].to_numpy()))
    df = pd.concat(frames, ignore_index=True)
    rng = np.random.default_rng([seed + 2, 2])
    mob = np.minimum(1 + np.floor(rng.gamma(GAMMA_SHAPE, GAMMA_SCALE, size=len(df))).astype(int), MAX_MOB)
    opened = pd.to_datetime(df["application_date"])
    when = _add_months(opened, mob)
    seen = (df["defaulted"].to_numpy() == 1) & (when <= pd.Timestamp(AS_OF)).to_numpy()
    return pd.DataFrame({
        "application_id": df["application_id"],
        "origination_date": df["application_date"],
        "default_date": np.where(seen, when.dt.strftime("%Y-%m-%d"), ""),
        "observed_until": AS_OF.isoformat(),
    })
