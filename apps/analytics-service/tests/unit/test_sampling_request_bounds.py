"""Counts that divide, index or LIMIT must be bounded in the schema.

``sample_size``/``sample_fraction``/``target_total_volume`` were already
``gt=0``, but three siblings were not, and each one reaches a place where a
non-positive value is not a no-op but a crash:

* ``SamplingStep.time_bins`` -> ``max(1, target // time_bins)`` (ZeroDivisionError
  at 0) and ``NTILE({time_bins})`` (DuckDB InvalidInputException when <= 0);
* ``SamplingStep.num_clusters`` -> ``random.sample(clusters, n)`` (ValueError on a
  negative) and an empty ``IN ()`` SQL fragment at 0;
* ``ProfileRequest.top_n`` -> ``... LIMIT ?`` (DuckDB BinderException, "LIMIT/OFFSET
  cannot be negative").

None of those are HTTPExceptions, so all three rendered as a 500 whose detail is
"An unexpected error occurred." — the caller is told nothing about which field
they got wrong. Bounding them in the schema turns each into a 422 that names the
field. This is a unit test because the rejection is pure pydantic: no database,
no DuckDB, no dataset needed to prove it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.data_accelerator.schemas import ProfileRequest, SamplingStep


@pytest.mark.parametrize("bad", [0, -1])
def test_time_bins_must_be_positive(bad):
    with pytest.raises(ValidationError) as exc:
        SamplingStep(method="time_stratified", time_column="ts", time_bins=bad)
    assert exc.value.errors()[0]["loc"] == ("time_bins",)


@pytest.mark.parametrize("bad", [0, -1])
def test_num_clusters_must_be_positive(bad):
    with pytest.raises(ValidationError) as exc:
        SamplingStep(method="cluster", cluster_column="region", num_clusters=bad)
    assert exc.value.errors()[0]["loc"] == ("num_clusters",)


def test_top_n_must_not_be_negative_but_zero_stays_legal():
    """Zero is a meaningful ask — "profile the columns, skip the top values" —
    and it maps to a valid ``LIMIT 0``. Only negatives are rejected."""
    assert ProfileRequest(data=[{"a": 1}], top_n=0).top_n == 0
    with pytest.raises(ValidationError) as exc:
        ProfileRequest(data=[{"a": 1}], top_n=-1)
    assert exc.value.errors()[0]["loc"] == ("top_n",)


def test_the_already_bounded_siblings_still_reject_non_positive_sizes():
    """Guards the constraint set as a whole: these are the fields the new
    bounds were made consistent with, and losing one would be silent."""
    for kwargs in ({"sample_size": 0}, {"sample_size": -5}, {"sample_fraction": 0.0}):
        with pytest.raises(ValidationError):
            SamplingStep(method="random", **kwargs)
