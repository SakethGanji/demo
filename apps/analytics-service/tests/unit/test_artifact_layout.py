"""Unit tests — the derived-artifact key layout and the retention policy.

Two properties carry the design, and both are easy to break by accident:

1. ``ArtifactLayout.key`` is a *pure function* of its fields. Two places build
   it independently — the service that writes the parquet, and the code that
   registers ownership — and they never exchange the string. If the key ever
   depended on time or on call order, the registered key would drift from the
   written one and the artifact would be silently unreachable.
2. Retention is decided by *kind*, which is why the kind is a path segment.
"""

from __future__ import annotations

import pytest

from app.features.files.services import retention
from app.infra.db.storage import (
    ADHOC_DATASET,
    ARTIFACT_ROOT,
    SHARED_TEAM,
    ArtifactLayout,
)


# ---------------------------------------------------------------------------
# Key construction
# ---------------------------------------------------------------------------


def test_key_layout_is_team_dataset_kind_filename():
    key = ArtifactLayout("sample_output", team_id="team-1",
                         dataset_id="ds-abc").key("sample_x.parquet")
    assert key == f"{ARTIFACT_ROOT}/team-1/ds-abc/sample_output/sample_x.parquet"


def test_key_is_a_pure_function_of_its_fields():
    """The writer and the registrar must derive the same string separately."""
    writer = ArtifactLayout("query_output", team_id="t", dataset_id="d")
    registrar = ArtifactLayout("query_output", team_id="t", dataset_id="d")
    assert writer.key("q.parquet") == registrar.key("q.parquet")
    # …and repeatedly, so nothing time- or order-dependent creeps back in.
    assert writer.key("q.parquet") == writer.key("q.parquet")


def test_layout_is_hashable_and_comparable():
    a = ArtifactLayout("export", team_id="t", dataset_id="d")
    b = ArtifactLayout("export", team_id="t", dataset_id="d")
    assert a == b and len({a, b}) == 1


def test_missing_owner_falls_back_to_explicit_shared_segments():
    """Ownerless artifacts still land somewhere a prefix rule can reach."""
    key = ArtifactLayout("query_output").key("q.parquet")
    assert key == f"{ARTIFACT_ROOT}/{SHARED_TEAM}/{ADHOC_DATASET}/query_output/q.parquet"


def test_dataset_prefix_covers_every_kind_for_that_dataset():
    layout = ArtifactLayout("sample_output", team_id="t", dataset_id="d")
    prefix = layout.dataset_prefix()
    assert layout.key("f.parquet").startswith(prefix + "/")
    # A different kind of the same dataset is swept by the same prefix, which
    # is what makes dataset deletion one call instead of an enumeration.
    other = ArtifactLayout("diff_output", team_id="t", dataset_id="d")
    assert other.key("g.parquet").startswith(prefix + "/")


@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "a/b/c.parquet",
    "..",
    "",
])
def test_path_traversal_cannot_escape_the_prefix(hostile):
    """A filename reaching the key builder must not climb out of its prefix."""
    layout = ArtifactLayout("sample_output", team_id="t", dataset_id="d")
    key = layout.key(hostile)
    assert key.startswith(layout.dataset_prefix() + "/")
    assert ".." not in key.split("/")


def test_hostile_team_and_dataset_ids_are_sanitised_too():
    key = ArtifactLayout("k", team_id="../../x", dataset_id="../y").key("f")
    assert ".." not in key.split("/")
    assert key.startswith(ARTIFACT_ROOT + "/")


# ---------------------------------------------------------------------------
# Retention policy
# ---------------------------------------------------------------------------


def test_published_sources_are_kept_forever():
    """A published source backs a dataset version; expiring it is data loss."""
    from datetime import datetime, timezone

    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    assert retention.expires_at("published_source", now=now) is None


def test_scratch_kinds_expire_sooner_than_saved_outputs():
    from datetime import datetime, timezone

    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    query = retention.expires_at("query_output", now=now)
    sample = retention.expires_at("sample_output", now=now)
    assert query is not None and sample is not None
    assert query < sample


def test_unknown_kind_gets_a_deadline_rather_than_immortality():
    """New code adding a kind must not silently opt out of collection."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    deadline = retention.expires_at("some_future_kind", now=now)
    assert deadline == now + timedelta(days=retention.DEFAULT_RETENTION_DAYS)


def test_every_artifact_type_the_db_accepts_has_a_policy():
    """The CHECK constraint and the policy table must not drift apart."""
    import re
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[2] / (
        "app/infra/db/postgres/migrations")
    latest = sorted(
        p for p in migrations.glob("*.sql")
        if "artifacts_artifact_type_check" in p.read_text())[-1]
    body = latest.read_text().split("-- migrate:down")[0]
    match = re.search(r"artifact_type IN \(([^)]*)\)", body, re.S)
    assert match, f"no artifact_type CHECK found in {latest.name}"
    declared = set(re.findall(r"'([a-z_]+)'", match.group(1)))

    missing = declared - set(retention.RETENTION_DAYS)
    assert not missing, f"artifact kinds with no retention policy: {sorted(missing)}"
