"""Which run-closing writes are guarded, and which are deliberately not.

``tests/test_completed_run_demotion.py`` proves the behaviour against Postgres.
This file is the cheap tripwire over the same SQL, plus the part only a written
test can carry: a map of every ``fail_*`` in the service saying whether its
missing guard is a defect or a decision.

Guarded, because a caller can reach them with the row already terminal:

* ``transform/repo.py``  — ``start_run``'s belt-and-braces close racing the
  handler's precise one.
* ``explorer/repo.py``   — ``_run_out`` raising after ``complete_run``.
* ``quality/repo.py``    — anything after ``complete_run`` in ``validate_version``.

Unguarded on purpose — do not "fix" these without reading why:

* ``shared/jobs.py::fail_job`` and ``library/repo.py::fail_run`` are what
  ``relationships/joins.py::execute_join`` closes its partial failures with. It
  gates them on its own ``run_closed`` / ``job_closed`` flags instead of on SQL,
  and ``tests/unit/test_join_run_bookkeeping.py`` pins that table. A guard there
  would be redundant rather than wrong — but the flags are the contract, so the
  guard must not be what anyone starts relying on.
* ``files/repo.py::fail_version`` closes ``dataset_versions``, not runs, and its
  callers fail a version that is still ``processing``.
"""

from __future__ import annotations

import inspect

import pytest

from app.features.explorer import repo as explorer_repo
from app.features.library import repo as library_repo
from app.features.quality import api as quality_api
from app.features.quality import repo as quality_repo
from app.features.transform import repo as transform_repo
from app.shared import jobs

GUARDED = [
    pytest.param(transform_repo.fail_run, id="transform"),
    pytest.param(explorer_repo.fail_run, id="explorer"),
    pytest.param(quality_repo.fail_run, id="quality"),
    pytest.param(library_repo.fail_run, id="library"),
]


@pytest.mark.parametrize("fn", GUARDED)
def test_the_close_is_conditional_on_the_run_still_being_open(fn):
    """``WHERE id = ...`` alone would rewrite a COMPLETED run as ``failed``.

    Every one of these is called from an ``except`` that spans more than the
    run it closes, so each can be handed a run that already ended. The guard is
    what makes the write idempotent instead of destructive.
    """
    source = inspect.getsource(fn)
    assert "status = 'running'" in source, (
        f"{fn.__module__}.fail_run can demote a completed run")
    assert "still open" in (fn.__doc__ or ""), (
        f"{fn.__module__}.fail_run must say why the guard is there")


@pytest.mark.parametrize("fn", [pytest.param(jobs.fail_job, id="jobs.fail_job")])
def test_the_job_close_stays_unguarded(fn):
    """A tripwire on the carve-out, not an endorsement of it.

    ``execute_join`` decides whether to close each row in Python. If someone
    adds a SQL guard here they must come and read that first, because the
    partial-failure table in ``tests/unit/test_join_run_bookkeeping.py`` is
    written against this being unconditional — specifically the case where
    ``complete_job`` is what failed, so the job legitimately IS still open and
    must be closable.

    ``library_repo.fail_run`` used to be in this list and has since moved to
    ``GUARDED``. That was checked, not assumed: ``execute_join`` gates its
    recovery on a Python ``run_closed`` flag, and
    ``test_a_completed_run_is_not_clobbered_when_only_the_job_fails`` asserts
    ``"fail_run" not in kinds`` — so the SQL guard is unreachable on that path
    and cannot change the contract. It is defence in depth for
    ``execute_definition``, which DOES call it after a successful
    ``complete_run``.
    """
    assert "status = 'running'" not in inspect.getsource(fn), (
        "guarding this changes execute_join's contract — see "
        "relationships/joins.py and tests/unit/test_join_run_bookkeeping.py")


def test_the_validation_webhook_is_outside_the_bookkeeping_block():
    """Announcing a run is not part of running it.

    The emit used to sit inside the ``except`` that fails the run, so a webhook
    that could not be queued both demoted a successful validation and handed
    the caller a 500. The guard fixes the row; only moving the call fixes the
    status code.
    """
    source = inspect.getsource(quality_api.validate_version)
    bookkeeping = source.index("await repo.fail_run")
    emit = source.index("await webhooks.emit")
    assert emit > bookkeeping, (
        "webhooks.emit must run after the run and job are closed, not inside "
        "the block that fails them")
