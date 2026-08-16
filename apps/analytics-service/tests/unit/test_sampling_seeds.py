"""Unit tests — sampling seed derivation and determinism (in-memory DuckDB)."""

from __future__ import annotations

import duckdb
import pytest

from app.features.data_accelerator.schemas import SamplingStep
from app.features.data_accelerator.services.methods import (
    duckdb_set_seed,
    resolve_target,
    sample_random,
)
from app.features.data_accelerator.services.sampling import execute_step


@pytest.fixture()
def pool():
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE pool AS
        SELECT i AS id, 'row_' || i AS label, i % 4 AS bucket
        FROM range(200) t(i)
    """)
    yield conn
    conn.close()


def _ids(df):
    return df["id"].tolist()


def test_sample_random_is_deterministic_for_fixed_seed(pool):
    a = sample_random(pool, "pool", 10, None, seed=42)
    b = sample_random(pool, "pool", 10, None, seed=42)
    assert _ids(a) == _ids(b) and len(a) == 10


def test_sample_random_differs_across_seeds(pool):
    a = sample_random(pool, "pool", 10, None, seed=42)
    b = sample_random(pool, "pool", 10, None, seed=43)
    assert _ids(a) != _ids(b)  # 200-choose-10: same pick is astronomically unlikely


def test_resolve_target_semantics():
    assert resolve_target(100, 10, None) == 10
    assert resolve_target(5, 10, None) == 5          # clamped without replacement
    assert resolve_target(5, 10, None, replace=True) == 10
    assert resolve_target(100, None, 0.25) == 25
    assert resolve_target(100, None, 1.5) == 100     # frac clamped to 1.0
    assert resolve_target(100, None, 1.5, replace=True) == 150


def test_duckdb_set_seed_normalizes_any_int(pool):
    # setseed() takes [-1, 1]; large/negative seeds must be normalized in.
    duckdb_set_seed(pool, 2**40)
    duckdb_set_seed(pool, -7)
    duckdb_set_seed(pool, None)  # no-op


async def test_execute_step_round_seed_derivation(pool):
    """Same (seed, step_index) → identical picks; shifted index → different.

    Documents ``round_seed = seed + step_index*100 + round_num``.
    """
    step = SamplingStep(method="random", sample_size=8)
    df1, res1 = await execute_step(pool, step, "pool", seed=7, step_index=0)
    df2, res2 = await execute_step(pool, step, "pool", seed=7, step_index=0)
    assert _ids(df1) == _ids(df2)
    assert res1.rows_selected == 8 and res1.pool_before == 200

    df3, _ = await execute_step(pool, step, "pool", seed=7, step_index=1)
    assert _ids(df3) != _ids(df1)

    # An equivalent round_seed reached a different way picks the same rows:
    # seed 7 / step 1 ≡ seed 107 / step 0 (7 + 1*100 + 0 = 107 + 0*100 + 0).
    df4, _ = await execute_step(pool, step, "pool", seed=107, step_index=0)
    assert _ids(df4) == _ids(df3)


async def test_execute_step_multi_round_without_replacement_is_disjoint(pool):
    step = SamplingStep(method="random", sample_size=20, rounds=3)
    df, res = await execute_step(pool, step, "pool", seed=1, step_index=0)
    assert res.rows_selected == 60
    assert len(set(df["id"])) == 60  # no repeats across rounds


async def test_execute_step_filter_zero_matches_short_circuits(pool):
    step = SamplingStep(method="random", sample_size=5,
                        filter_expr="bucket = 99")
    df, res = await execute_step(pool, step, "pool", seed=1, step_index=0)
    assert df.empty and res.rows_selected == 0 and res.filter_matched == 0
    assert any("0 rows" in w for w in res.warnings)
