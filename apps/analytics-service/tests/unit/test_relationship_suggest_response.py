"""What POST /relationships/suggest tells the caller about the run it started.

Two things a UI cannot work without, neither of which the response used to
carry:

* With ``sync=false`` the response IS the whole answer — the work has not run
  yet — so the job id is the only handle on it. Without it a client has to
  scrape ``GET /jobs?job_type=relationship_discovery`` and guess which of the
  concurrent discovery jobs was its own (that route has no dataset filter).
* Discovery caps how many candidate pairs reach SQL. A capped run is NOT
  exhaustive, and "no relationships found" means something very different when
  hundreds of pairs were never probed. The service measures that number; the
  response used to drop it, leaving the truth only in the server log.

These are exercised against the route function with the job plumbing stubbed,
so they stay in tests/unit (no Postgres).
"""

from __future__ import annotations

import pytest

from app.features.auth.deps import Principal
from app.features.auth.permissions import Role
from app.features.relationships import api, repo
from app.shared import jobs, worker

TEAM = "00000000-0000-0000-0000-0000000000aa"
DATASET = "11111111-1111-1111-1111-111111111111"
JOB_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def principal() -> Principal:
    return Principal(user_id="u-1", email="e@x.com", name="E", is_superuser=False,
                     home_team_id=TEAM, memberships={TEAM: Role.EDITOR})


@pytest.fixture(autouse=True)
def _stub_io(monkeypatch):
    """Everything the route touches except the code under test."""
    async def fake_ensure(_principal, dataset_id, _permission):
        return {"id": dataset_id, "team_id": TEAM}

    async def fake_list(*_a, **_k):
        return [], 0

    async def fake_create_job(job_type, **_kwargs):
        assert job_type == "relationship_discovery"
        return {"id": JOB_ID}

    monkeypatch.setattr(api, "ensure_dataset_permission", fake_ensure)
    monkeypatch.setattr(repo, "list_relationships", fake_list)
    monkeypatch.setattr(jobs, "create_job", fake_create_job)


async def test_enqueuing_discovery_returns_the_job_id_of_the_row_it_created(
        monkeypatch, principal):
    """``sync=false`` must hand back the handle for the work it just queued."""
    async def fake_dispatch(job_type, **kwargs):
        # This is what shared.worker.dispatch does today: it creates the row
        # and returns None, throwing the id away.
        assert kwargs["inline"] is False
        await jobs.create_job(job_type)
        return None

    monkeypatch.setattr(worker, "dispatch", fake_dispatch)

    body = await api.suggest_relationships(DATASET, sync=False, principal=principal)

    assert body.job_id == JOB_ID
    assert body.suggested == 0        # nothing has run yet — that part is right


async def test_a_capped_discovery_run_reports_the_pairs_it_never_probed(
        monkeypatch, principal):
    """A run that hit MAX_CANDIDATE_PAIRS is not exhaustive, and must say so."""
    async def fake_dispatch(_job_type, **kwargs):
        assert kwargs["inline"] is True
        return {"pairs_examined": 400, "suggested": 2, "skipped": 57,
                "job_id": JOB_ID}

    monkeypatch.setattr(worker, "dispatch", fake_dispatch)

    body = await api.suggest_relationships(DATASET, sync=True, principal=principal)

    assert body.skipped == 57
    assert body.pairs_examined == 400 and body.suggested == 2
    assert body.job_id == JOB_ID      # the inline handle still comes through
