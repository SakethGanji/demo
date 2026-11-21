"""The BISG tables: P(group | surname) and the population of each group per zip code.

Real BISG uses the Census Bureau's surname file (≈ 162,000 surnames with the share of each group) and a census
population table by zip (ZCTA) or tract. Those are public but large and need a download, so ENSEMBLE bundles a **small
synthetic stand-in in the same format** (2,000 invented surnames, 400 invented zip codes), generated here with a fixed
seed. The page says so everywhere it shows a number. To use the real tables, put them in `<data_dir>/bisg/`:

    surnames.csv   name, count, pctwhite, pctblack, pctapi, pctaian, pct2prace, pcthispanic   (Census 2010 surname file;
                   percentages 0–100, "(S)" for suppressed)
    zips.csv       zip, white, black, api, aian, multi, hispanic                                 (people per group)

The synthetic world is built so that surname and zip are independent given the group (the BISG assumption), which makes
the stand-in's posteriors calibrated by construction. Real tables are not: expect a less accurate proxy on real data.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, frames

GROUPS = ["white", "black", "hispanic", "api", "aian", "multi"]
LABELS = {"white": "White", "black": "Black", "hispanic": "Hispanic", "api": "Asian / Pacific Islander",
          "aian": "American Indian / Alaska Native", "multi": "Multiracial"}
REFERENCE = "white"  # the control group of the adverse impact ratio, as in fair-lending practice
CENSUS_COLS = {"white": "pctwhite", "black": "pctblack", "hispanic": "pcthispanic", "api": "pctapi", "aian": "pctaian",
               "multi": "pct2prace"}

# Synthetic surname "kinds": the mean of P(group | surname) and the share of distinct surnames of that kind.
_KINDS = {
    "anglo":    (0.38, [0.80, 0.14, 0.02, 0.01, 0.01, 0.02]),
    "mixed":    (0.15, [0.52, 0.40, 0.02, 0.01, 0.02, 0.03]),
    "hispanic": (0.22, [0.06, 0.01, 0.91, 0.01, 0.00, 0.01]),
    "asian":    (0.13, [0.05, 0.01, 0.01, 0.92, 0.00, 0.01]),
    "african":  (0.07, [0.10, 0.86, 0.01, 0.01, 0.00, 0.02]),
    "native":   (0.05, [0.30, 0.04, 0.04, 0.01, 0.55, 0.06]),
}
# Synthetic neighbourhood types: the mean composition of a zip and the share of zips of that type.
_HOODS = {
    "white suburb":  (0.40, [0.86, 0.05, 0.05, 0.02, 0.005, 0.015]),
    "black":         (0.15, [0.12, 0.80, 0.05, 0.01, 0.005, 0.015]),
    "hispanic":      (0.15, [0.15, 0.06, 0.75, 0.02, 0.005, 0.015]),
    "asian":         (0.07, [0.28, 0.03, 0.07, 0.60, 0.005, 0.015]),
    "mixed city":    (0.18, [0.45, 0.22, 0.20, 0.08, 0.01, 0.04]),
    "reservation":   (0.05, [0.30, 0.02, 0.05, 0.01, 0.58, 0.04]),
}
_SYL = ["ar", "bel", "cor", "dan", "el", "fen", "gal", "har", "is", "jor", "kel", "lun", "mar", "nor", "ol", "pel",
        "quin", "ros", "sal", "tor", "ul", "ven", "wil", "yar", "zan", "bri", "cas", "dov", "esk", "fal", "gor", "hal"]


@dataclass(frozen=True)
class Tables:
    source: str                 # "synthetic" | "census"
    label: str                  # what the page shows
    groups: list[str]
    surname: pd.DataFrame       # index: normalised surname; columns: groups (P(group | surname), rows sum to 1); "count"
    zip: pd.DataFrame           # index: 5-digit zip string; columns: groups (people per group)
    prior: np.ndarray           # P(group) over the surname table's population

    @property
    def p_zip_given_group(self) -> pd.DataFrame:
        """P(zip | group) = N_zip,group / N_group: the share of each group's population living in each zip."""
        return self.zip[self.groups] / self.zip[self.groups].sum(axis=0).replace(0, np.nan)


def norm_surname(s: pd.Series) -> pd.Series:
    return s.astype(str).str.upper().str.replace(r"[^A-Z]", "", regex=True)


def norm_zip(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.strip().str.extract(r"^(\d{1,5})")[0]
    return x.str.zfill(5)


@lru_cache(maxsize=1)
def synthetic_tables(seed: int = 2024) -> Tables:
    rng = np.random.default_rng([seed, 4])
    K = len(GROUPS)
    names: set[str] = set()
    rows = []
    kinds = list(_KINDS)
    shares = np.array([_KINDS[k][0] for k in kinds])
    while len(rows) < 2000:
        kind = kinds[rng.choice(len(kinds), p=shares / shares.sum())]
        nm = "".join(rng.choice(_SYL, size=rng.integers(2, 4))).upper()
        if nm in names:
            continue
        names.add(nm)
        p = rng.dirichlet(np.array(_KINDS[kind][1]) * 30 + 0.05)
        rows.append((nm, kind, int(np.exp(rng.normal(7.0, 1.0))) + 50, *p))
    sur = pd.DataFrame(rows, columns=["name", "kind", "count", *GROUPS]).set_index("name")
    hoods = list(_HOODS)
    hs = np.array([_HOODS[h][0] for h in hoods])
    zrows = []
    zips = rng.choice(np.arange(10_000, 99_999), size=400, replace=False)
    for z in zips:
        h = hoods[rng.choice(len(hoods), p=hs / hs.sum())]
        comp = rng.dirichlet(np.array(_HOODS[h][1]) * 25 + 0.05)
        pop = int(np.exp(rng.normal(9.3, 0.6)))
        zrows.append((f"{z:05d}", h, *(np.round(comp * pop).astype(int))))
    zt = pd.DataFrame(zrows, columns=["zip", "kind", *GROUPS]).set_index("zip")
    prior = (sur[GROUPS].to_numpy() * sur["count"].to_numpy()[:, None]).sum(0)
    return Tables("synthetic", "Synthetic stand-in tables (2,000 invented surnames, 400 invented zips): not Census data",
                  GROUPS, sur, zt, prior / prior.sum())


def _census_tables(d: Path) -> Tables:
    s = frames.read_artifact(d / "surnames.csv", dtype=str)
    s.columns = [c.lower() for c in s.columns]
    pct = pd.DataFrame({g: pd.to_numeric(s[CENSUS_COLS[g]], errors="coerce") for g in GROUPS})
    # "(S)": suppressed small shares. Spread what is left of 100% evenly over them.
    rest = (100 - pct.sum(axis=1, skipna=True)).clip(lower=0)
    nmiss = pct.isna().sum(axis=1).replace(0, np.nan)
    pct = pct.apply(lambda c: c.fillna(rest / nmiss)).fillna(0)
    p = pct.div(pct.sum(axis=1).replace(0, np.nan), axis=0).fillna(1 / len(GROUPS))
    sur = pd.concat([p, pd.to_numeric(s["count"], errors="coerce").fillna(0).rename("count")], axis=1)
    sur.index = norm_surname(s["name"]).to_numpy()
    sur = sur[~sur.index.duplicated()]
    z = frames.read_artifact(d / "zips.csv", dtype={"zip": str})
    z.columns = [c.lower() for c in z.columns]
    z["zip"] = norm_zip(z["zip"])
    zt = z.set_index("zip")[GROUPS].apply(pd.to_numeric, errors="coerce").fillna(0)
    prior = (sur[GROUPS].to_numpy() * sur["count"].to_numpy()[:, None]).sum(0)
    return Tables("census", f"Census tables from {d} ({len(sur):,} surnames, {len(zt):,} zips)", GROUPS, sur, zt,
                  prior / prior.sum())


def load() -> Tables:
    d = config.load_settings().data_dir / "bisg"
    if (d / "surnames.csv").exists() and (d / "zips.csv").exists():
        return _census_tables(d)
    return synthetic_tables()
