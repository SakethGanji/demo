"""Jobs timestamps — one wire format across list and detail.

`GET /jobs` renders its timestamps in SQL (`created_at::text`) while
`GET /jobs/{id}` renders them in Python (`str(datetime)`). Postgres and
Python disagree about the UTC offset ('+00' vs '+00:00') and both use a
space where ISO-8601 wants 'T'. A client that polls the listing for a
running job and then switches to the detail endpoint therefore sees the
same instant spelled two different ways, and neither spelling parses with
a strict ISO-8601 reader.
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import SAMPLE_CSV, auth, upload_file

_TS_FIELDS = ("created_at", "started_at", "completed_at")


def _assert_iso_utc(value: str, where: str) -> None:
    assert "T" in value, f"{where} is not ISO-8601 (no 'T' separator): {value!r}"
    assert value.endswith("+00:00"), f"{where} lacks a full UTC offset: {value!r}"
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"{where} parsed without a timezone: {value!r}"
    assert parsed.utcoffset() == timezone.utc.utcoffset(None), (
        f"{where} is not UTC: {value!r}"
    )


async def test_job_timestamps_are_iso_8601_in_both_the_listing_and_the_detail(
    client, admin_id,
):
    """Both jobs endpoints must emit ISO-8601 UTC timestamps.

    Without this, `GET /jobs` emits Postgres' '2026-08-07 10:00:00+00' and
    `GET /jobs/{id}` emits Python's '2026-08-07 10:00:00+00:00'. Neither is
    ISO-8601, so a consumer using a strict parser (JavaScript's `new Date`,
    Go's `time.RFC3339`) gets an Invalid Date or an error on a field the
    schema types as a timestamp, and a consumer that string-compares the
    listing's value against the detail's value concludes the job changed
    when nothing changed.
    """
    h = auth(admin_id)
    up = await upload_file(client, admin_id, SAMPLE_CSV, name="ts.csv")
    ds = up["dataset_id"]

    listing = (await client.get("/api/v1/jobs",
                                params={"job_type": "import", "status": "completed"},
                                headers=h)).json()
    listed = next(j for j in listing["items"] if j["dataset_id"] == ds)

    detail = (await client.get(f"/api/v1/jobs/{listed['id']}", headers=h)).json()

    for field in _TS_FIELDS:
        assert listed[field] is not None, f"expected the import job to have {field}"
        _assert_iso_utc(listed[field], f"listing {field}")
        _assert_iso_utc(detail[field], f"detail {field}")
        assert listed[field] == detail[field], (
            f"{field} differs between the listing and the detail: "
            f"{listed[field]!r} vs {detail[field]!r}"
        )
