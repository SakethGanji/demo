"""``replace_sheet`` must not rewrap an actionable 4xx as a 500.

The same exception-hierarchy defect as ``execute_definition`` (see
``test_run_failure_bookkeeping.py``): ``ProblemException`` subclasses
STARLETTE's ``HTTPException`` while the guard caught FASTAPI's. Those are
siblings, not parent and child, so a ``ProblemException`` raised while
replacing a sheet fell through to the generic branch and reached the caller as
``500 "Sheet replacement failed: 400: …"`` — status gone, machine-readable
``code`` gone, and the extra fields that make it actionable gone with them.

The severity is NOT the same. There, one branch re-raised without doing any
failure bookkeeping, so runs were left in ``running`` forever. Here both
branches already call ``fail_version``, so no version was ever left
``uploading``; only the client-facing error was wrong. This file pins both
halves: the error the caller sees, and the bookkeeping that was already right
and must stay that way.

Driven against stubs — which exception class produces which outcome is not a
database behaviour.
"""

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from app.api.errors import ProblemException
from app.features.files.services import replace

DATASET = {"id": "11111111-1111-1111-1111-111111111111",
           "team_id": "22222222-2222-2222-2222-222222222222",
           "name": "payments"}

#: The problem the tools care most about surviving: it names the sheets the
#: caller should have picked from, which is the entire point of the code.
SHEET_CHOICE = ProblemException(
    400, "This dataset version has 2 sheets — name one via the 'sheet' parameter",
    code="sheet-selection-required", sheets=["Revenue", "Expenses"])
UNSUPPORTED = HTTPException(400, "Upload rejected by scanner: infected")
BOOM = RuntimeError("duckdb exploded")


class _Principal:
    user_id = "44444444-4444-4444-4444-444444444444"


class _Upload:
    filename = "sheet.csv"


@pytest.fixture
def failures(monkeypatch):
    """Stub the version side of replace_sheet; record every bookkeeping write.

    ``scan_upload`` is the raise point: it is inside the try block, after the
    version row exists, so it exercises exactly the path the guard protects.
    """
    recorded: list[tuple] = []

    base_version = {"id": "55555555-5555-5555-5555-555555555555", "version_number": 3}
    sheet_row = {"sheet_key": "data", "sheet_name": "Data", "sheet_index": 0,
                 "storage_key": "k", "is_default": True}

    async def _get_current_version_or_404(dataset_id):
        return base_version

    async def get_version_sheet_rows(ver):
        return [sheet_row]

    async def ensure_sheet_schema(ver, row):
        return row

    async def create_version(dataset_id, **kwargs):
        return {"id": "66666666-6666-6666-6666-666666666666", "version_number": 4}

    async def fail_version(version_id, error=None):
        recorded.append(("fail_version", version_id, error))

    async def complete_version(*a, **k):  # pragma: no cover - never reached here
        recorded.append(("complete_version",))

    monkeypatch.setattr(replace, "_get_current_version_or_404", _get_current_version_or_404)
    monkeypatch.setattr(replace, "get_version_sheet_rows", get_version_sheet_rows)
    monkeypatch.setattr(replace, "ensure_sheet_schema", ensure_sheet_schema)
    monkeypatch.setattr(replace, "repo", types.SimpleNamespace(
        create_version=create_version, fail_version=fail_version,
        complete_version=complete_version))
    monkeypatch.setattr(replace, "get_storage", lambda: types.SimpleNamespace(
        resolve=lambda key: "/tmp/x.parquet", put_file=lambda *a: None))
    monkeypatch.setattr(replace, "DatasetLayout", lambda *a, **k: types.SimpleNamespace(
        ensure_dirs=lambda: None))

    async def stream_to_disk(file, path):
        path.write_bytes(b"col\n1\n")

    monkeypatch.setattr(replace, "stream_to_disk", stream_to_disk)
    return recorded


def _raising(exc):
    async def scan_upload(path, filename=None):
        raise exc
    return scan_upload


async def _replace(monkeypatch, exc):
    monkeypatch.setattr(replace, "scan_upload", _raising(exc))
    return await replace.replace_sheet(DATASET, "Data", _Upload(), _Principal())


async def test_a_problem_exception_reaches_the_caller_intact(failures, monkeypatch):
    """Status, ``code`` and extra fields survive — they are the actionable part."""
    with pytest.raises(ProblemException) as e:
        await _replace(monkeypatch, SHEET_CHOICE)

    assert e.value is SHEET_CHOICE
    assert e.value.status_code == 400
    assert e.value.code == "sheet-selection-required"
    assert e.value.extra["sheets"] == ["Revenue", "Expenses"]
    # The pre-fix shape: a 500 whose detail had swallowed the real one.
    assert "Sheet replacement failed" not in str(e.value.detail)


async def test_a_fastapi_http_exception_still_reaches_the_caller_intact(failures, monkeypatch):
    """The catch was widened, never narrowed: FastAPI's HTTPException is a
    subclass of Starlette's, so it takes the same branch it always did."""
    with pytest.raises(HTTPException) as e:
        await _replace(monkeypatch, UNSUPPORTED)

    assert e.value is UNSUPPORTED and e.value.status_code == 400


async def test_an_unexpected_error_is_still_a_500(failures, monkeypatch):
    """Only genuinely unexpected failures become a 500 — unchanged."""
    with pytest.raises(HTTPException) as e:
        await _replace(monkeypatch, BOOM)

    assert e.value.status_code == 500
    assert e.value.detail == "Sheet replacement failed: duckdb exploded"


@pytest.mark.parametrize("exc", [SHEET_CHOICE, UNSUPPORTED, BOOM],
                         ids=["problem", "fastapi-http", "generic"])
async def test_every_exception_class_still_fails_the_version(failures, monkeypatch, exc):
    """The half that was NOT broken, pinned so it stays that way.

    Unlike ``execute_definition``, both branches here already closed the
    version, so no path ever left a row stuck in ``uploading`` — and widening
    the catch must not create one.
    """
    with pytest.raises(Exception):
        await _replace(monkeypatch, exc)

    assert [c[0] for c in failures] == ["fail_version"]
    assert failures[0][1] == "66666666-6666-6666-6666-666666666666"
    assert failures[0][2], "the version was failed with no error recorded"
