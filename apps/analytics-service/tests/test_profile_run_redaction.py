"""Everything a viewer can read back from a profile run must stay redacted.

``POST /datasets/{id}/versions/{v}/profile-runs`` requires only
``dataset:read``, so a viewer may profile a dataset that declares a sensitive
column even though ``POST /profile`` refuses them with 403. That is only
defensible if the *read-back* is complete: the run persists verbatim cell
values (``top_values``, and the evidence of the insight rules computed from
them), and if any of them survives into a response the viewer can fetch, the
open POST is a masking bypass rather than a convenience.

So these tests plant a sentinel string in a sensitive column, have a viewer
create the run, and then read the run back **every way a viewer can** — the
create response, the listing, the detail, health, missing, duplicates, the
column drawer, the timeline, jobs, search — asserting the sentinel appears in
none of them. They are written to fail loudly on any new derived surface that
forgets to redact, which is why they sweep the serialized payloads rather than
naming individual fields.
"""

from __future__ import annotations

import datetime
import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
    upload_inline,
)

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

# Distinctive enough that a substring hit anywhere in a JSON payload is a leak
# and never a coincidence.
SENTINEL = "ZQ7-KESTREL-SENTINEL-4413"
SENTINEL_2 = "ZQ7-PEREGRINE-SENTINEL-9021"

# `secret_code` is constant across every row on purpose: that is what makes the
# `constant-column` insight rule fire, and that rule copies the top value into
# its evidence.
CONSTANT_ROWS = [
    {"id": 1, "secret_code": SENTINEL, "amount": 100.0},
    {"id": 2, "secret_code": SENTINEL, "amount": 250.0},
    {"id": 3, "secret_code": SENTINEL, "amount": 175.0},
    {"id": 4, "secret_code": SENTINEL, "amount": 250.0},
]

# Two versions of a categorical sensitive column, the second introducing a new
# category — what the `new-categories` rule reports.
CATEGORY_V1 = [
    {"id": 1, "secret_code": "alpha"},
    {"id": 2, "secret_code": "beta"},
    {"id": 3, "secret_code": "alpha"},
]
CATEGORY_V2 = [
    {"id": 1, "secret_code": "alpha"},
    {"id": 2, "secret_code": SENTINEL_2},
    {"id": 3, "secret_code": SENTINEL_2},
]


async def _declare_sensitive(client, admin_id, ds, column="secret_code"):
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/{column}",
        headers=auth(admin_id),
        json={"business_name": "Secret code", "sensitivity": "confidential"})
    assert r.status_code == 200, r.text


async def _viewer(client, admin_id):
    """A member of the dataset's team with dataset:read and nothing more."""
    uid, _ = await create_team_user(client, admin_id, "viewer",
                                    team_id=DEFAULT_TEAM)
    return uid


def _leaks(payload, sentinel: str = SENTINEL) -> bool:
    return sentinel in json.dumps(payload, default=str)


async def _every_viewer_read(client, uid, ds, run_id, version: int = 1):
    """Every response a viewer can obtain that is derived from the stored run.

    Returns ``{label: payload}``; non-200s are recorded as their status so a
    surface that is *refused* still shows up in the sweep (a 403 body cannot
    leak, but the assertion should not silently skip it either).
    """
    h = auth(uid)
    reads = {
        "run detail": f"/api/v1/datasets/{ds}/profile-runs/{run_id}",
        "run list": f"/api/v1/datasets/{ds}/versions/{version}/profile-runs",
        "health": f"/api/v1/datasets/{ds}/health",
        "missing (version)": f"/api/v1/datasets/{ds}/versions/{version}/missing",
        "missing (sheet)":
            f"/api/v1/datasets/{ds}/versions/{version}/sheets/data/missing",
        "duplicates": f"/api/v1/datasets/{ds}/versions/{version}/duplicates",
        "column drawer":
            f"/api/v1/datasets/{ds}/versions/{version}/columns/secret_code",
        "preview": f"/api/v1/datasets/{ds}/versions/{version}/preview",
        "timeline": f"/api/v1/datasets/{ds}/timeline",
        "dataset detail": f"/api/v1/datasets/{ds}",
        "sheet metadata": f"/api/v1/datasets/{ds}/sheet-metadata",
        "jobs": "/api/v1/jobs?job_type=profiling",
        "column search": f"/api/v1/search/columns?q={SENTINEL}",
        "diff with profile":
            f"/api/v1/datasets/{ds}/versions/1/diff/{version}?include=profile",
    }
    out: dict[str, object] = {}
    for label, url in reads.items():
        r = await client.get(url, headers=h)
        out[label] = r.json() if r.status_code == 200 else {"status": r.status_code}
    return out


# --- the sweep ----------------------------------------------------------------

async def test_a_viewer_may_still_create_a_profile_run(client, admin_id):
    """The behaviour the rest of this file is predicated on, pinned.

    ``POST /profile`` refuses a viewer on this same dataset (403
    ``sensitive-data-restricted``); ``profile-runs`` does not. If either half
    of that changes, this test says so before the sweep below turns confusing.
    """
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    uid = await _viewer(client, admin_id)

    r = await client.post("/api/v1/profile", headers=auth(uid),
                          json={"dataset_id": ds})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sensitive-data-restricted"

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                          headers=auth(uid))
    assert r.status_code == 200, r.text


async def test_no_viewer_readable_surface_leaks_a_profiled_sentinel(
        client, admin_id):
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    uid = await _viewer(client, admin_id)

    created = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                                headers=auth(uid))
    assert created.status_code == 200, created.text
    runs = created.json()
    assert not _leaks(runs), f"create response leaked the sentinel: {runs}"

    run_id = runs[0]["id"]
    for label, payload in (await _every_viewer_read(client, uid, ds, run_id)).items():
        assert not _leaks(payload), f"{label} leaked the sentinel: {payload}"


async def test_new_category_insights_do_not_name_a_sensitive_value(
        client, admin_id):
    """The cross-version rule that reports *which* categories appeared.

    ``new-categories`` puts the new values in both its message and its
    evidence — verbatim cells, on a column the same viewer sees as ``***``
    everywhere else.
    """
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CATEGORY_V1)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    await upload_inline(client, admin_id, json.dumps(CATEGORY_V2), dataset_id=ds)
    uid = await _viewer(client, admin_id)
    h = auth(uid)

    assert (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=h)).status_code == 200
    created = await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs",
                                headers=h)
    assert created.status_code == 200, created.text
    runs = created.json()
    assert "new-categories" in {i["rule"] for i in runs[0]["insights"]}, runs
    assert not _leaks(runs, SENTINEL_2), f"create response leaked: {runs}"

    reads = await _every_viewer_read(client, uid, ds, runs[0]["id"], version=2)
    for label, payload in reads.items():
        assert not _leaks(payload, SENTINEL_2), f"{label} leaked: {payload}"


async def test_a_run_an_admin_created_is_redacted_for_a_viewer_too(
        client, admin_id):
    """The leak was never about *who ran* the profiling.

    Redaction belongs to the read, so a run created by an elevated colleague —
    the normal case — must read back to a viewer exactly as redacted as one the
    viewer triggered. If this ever passes only because viewers cannot create
    runs, it is testing the wrong thing.
    """
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=auth(admin_id))).json()
    uid = await _viewer(client, admin_id)

    for label, payload in (await _every_viewer_read(
            client, uid, ds, runs[0]["id"])).items():
        assert not _leaks(payload), f"{label} leaked the sentinel: {payload}"


async def test_a_redacted_insight_keeps_its_finding(client, admin_id):
    """Withholding the values must not withhold the fact."""
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    uid = await _viewer(client, admin_id)

    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=auth(uid))).json()
    constant = next(i for i in runs[0]["insights"]
                    if i["rule"] == "constant-column")
    assert constant["column_name"] == "secret_code"
    assert constant["severity"] == "warning"
    assert constant["message"] == "'secret_code' has a single value across all rows"
    assert "value" not in constant["evidence"]

    # And the insight on a column nobody masked is untouched.
    pk = next(i for i in runs[0]["insights"] if i["rule"] == "likely-primary-key")
    assert pk["evidence"] == {"unique_count": 4, "row_count": 4}


async def test_run_detail_works_on_a_multi_sheet_version(client, admin_id,
                                                         tmp_path):
    """Every workbook's run detail answered 400, for every caller.

    The redaction added to this route resolved the run's sheet by a
    ``sheet_key`` that profile-run rows do not have, so it asked for "the
    version's only sheet" — and a version with three sheets answers
    ``sheet-selection-required``. Masking must be resolved by the run's own
    ``logical_sheet_id``, which also means each sheet is redacted with *its*
    policy and not another's.
    """
    book = tmp_path / "book.xlsx"
    make_workbook(book)
    ds = (await upload_file(client, admin_id, book, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/revenue/columns/region",
        headers=h, json={"sensitivity": "confidential"})
    assert r.status_code == 200, r.text

    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=h)).json()
    assert {r["sheet_name"] for r in runs} == {"Revenue", "Expenses", "Secrets"}
    uid = await _viewer(client, admin_id)

    by_sheet = {}
    for run in runs:
        r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{run['id']}",
                             headers=auth(uid))
        assert r.status_code == 200, (run["sheet_name"], r.text)
        by_sheet[run["sheet_name"]] = r.json()["profile"]

    def _values(profile, column):
        col = next(c for c in profile["columns"] if c["name"] == column)
        return [tv["value"] for tv in col["top_values"]]

    # Revenue.Region is masked...
    assert set(_values(by_sheet["Revenue"], "Region")) == {"***"}
    # ...and the sheets that declare nothing are not collaterally redacted.
    assert set(_values(by_sheet["Expenses"], "Item")) == {"rent", "power"}


async def test_a_masked_date_column_keeps_its_extremes_to_itself(
        client, admin_id, tmp_path):
    """``min_date``/``max_date`` are two literal cells of the column.

    The column drawer already withholds them for a masked column
    (``explorer.service._VALUE_BEARING_PROFILE_FIELDS``); the stored profile
    did not, so a masked date of birth read back exactly — the earliest and the
    latest, which on a small sheet is most of the column.
    """
    from openpyxl import Workbook

    born = datetime.datetime(1971, 3, 17)
    book = tmp_path / "people.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "People"
    ws.append(["id", "birth_date"])
    ws.append([1, born])
    ws.append([2, datetime.datetime(1988, 11, 2)])
    wb.save(book)

    ds = (await upload_file(client, admin_id, book, name="people.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/people/columns/birth_date",
        headers=auth(admin_id), json={"sensitivity": "pii"})
    assert r.status_code == 200, r.text
    uid = await _viewer(client, admin_id)

    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=auth(uid))).json()
    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{runs[0]['id']}",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    col = next(c for c in r.json()["profile"]["columns"]
               if c["name"] == "birth_date")
    assert col["min_date"] is None and col["max_date"] is None
    assert set(tv["value"] for tv in col["top_values"]) == {"***"}
    assert col["dtype"] == "datetime" and col["unique_count"] == 2
    assert not _leaks(r.json(), str(born.date())), r.text

    # The elevated reader still gets them.
    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{runs[0]['id']}",
                         headers=auth(admin_id))
    col = next(c for c in r.json()["profile"]["columns"]
               if c["name"] == "birth_date")
    assert col["min_date"].startswith(str(born.date()))


async def test_an_elevated_reader_still_sees_the_real_values(client, admin_id):
    """Redaction is per-principal, not a destructive write.

    The stored run keeps the raw profile; only the response is filtered. If a
    fix ever redacts at *write* time this test fails, which is the point —
    admins would silently lose the profile they are entitled to.
    """
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    uid = await _viewer(client, admin_id)

    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=auth(uid))).json()
    run_id = runs[0]["id"]

    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{run_id}",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert _leaks(body), "the superuser should still see the raw profile"
    codes = {tv["value"]
             for c in body["profile"]["columns"] if c["name"] == "secret_code"
             for tv in c["top_values"]}
    assert codes == {SENTINEL}


async def test_the_viewers_own_run_detail_masks_top_values(client, admin_id):
    """The positive half: shape survives, values do not."""
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CONSTANT_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds)
    uid = await _viewer(client, admin_id)

    runs = (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=auth(uid))).json()
    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{runs[0]['id']}",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    col = next(c for c in r.json()["profile"]["columns"]
               if c["name"] == "secret_code")
    assert [tv["value"] for tv in col["top_values"]] == ["***"]
    assert col["unique_count"] == 1 and col["null_count"] == 0
    # A non-sensitive column keeps everything.
    amount = next(c for c in r.json()["profile"]["columns"] if c["name"] == "amount")
    assert amount["max"] == 250.0


# --- correlation surfaces -------------------------------------------------
#
# A correlation coefficient is a bare float, so the sentinel sweep above is
# structurally blind to this class of leak. These tests assert on structure
# instead: the masked column must appear nowhere in the correlation matrix a
# viewer reads back, and no high-correlation insight may mention it from
# either side of the pair.

# Three numeric columns, pairwise correlated at r=1.0, named so the sensitive
# one sorts BETWEEN the other two: the pair (alpha, mmm_secret) puts the
# masked column second — the ordering the insight gate used to miss entirely —
# and (mmm_secret, zeta) puts it first. alpha↔zeta is the control pair that
# must survive redaction untouched.
CORRELATED_ROWS = [
    {"alpha": float(i), "mmm_secret": 100.0 * i, "zeta": 100.0 * i + 1.0}
    for i in range(1, 6)
]


async def _correlated_run(client, admin_id):
    ds = (await upload_inline(client, admin_id,
                              json.dumps(CORRELATED_ROWS)))["dataset_id"]
    await _declare_sensitive(client, admin_id, ds, column="mmm_secret")
    uid = await _viewer(client, admin_id)
    created = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=auth(uid))
    assert created.status_code == 200, created.text
    return ds, uid, created.json()


async def test_a_masked_numeric_column_leaves_no_trace_in_correlations(
        client, admin_id):
    ds, uid, created = await _correlated_run(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{created[0]['id']}",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    detail = r.json()

    corr = (detail.get("profile") or {}).get("correlations") or {}
    assert corr, "expected a correlation matrix over the unmasked numeric pair"
    assert "mmm_secret" not in corr
    assert all("mmm_secret" not in (row or {}) for row in corr.values())
    # The unmasked pair is untouched — masking must not over-redact.
    assert corr["alpha"]["zeta"] is not None

    # The superuser still sees the full matrix (redaction is per-caller).
    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{created[0]['id']}",
                         headers=auth(admin_id))
    assert r.status_code == 200, r.text
    admin_corr = r.json()["profile"]["correlations"]
    assert admin_corr["alpha"]["mmm_secret"] is not None


async def test_no_high_correlation_insight_names_a_masked_column(
        client, admin_id):
    """Every insight-bearing response a viewer gets: the create response, the
    listing, and the detail. Checked from BOTH sides of the pair, because the
    insight attributes itself to the alphabetically-first column only."""
    ds, uid, created = await _correlated_run(client, admin_id)
    run_id = created[0]["id"]

    listing = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=auth(uid))
    assert listing.status_code == 200, listing.text
    detail = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{run_id}",
                              headers=auth(uid))
    assert detail.status_code == 200, detail.text

    surfaces = {
        "create response": [i for run in created
                            for i in run.get("insights") or []],
        "run list": [i for run in listing.json()["items"]
                     for i in run.get("insights") or []],
        "run detail": detail.json().get("insights") or [],
    }
    for label, insights in surfaces.items():
        offenders = [
            i for i in insights if i["rule"] == "high-correlation" and (
                "mmm_secret" in (i.get("column_name") or "")
                or "mmm_secret" in ((i.get("evidence") or {}).get("other_column") or "")
                or "mmm_secret" in (i.get("message") or ""))]
        assert not offenders, f"{label} leaked a masked correlation: {offenders}"

    # The control pair's insight survives, full coefficient and all.
    kept = [i for i in surfaces["run detail"] if i["rule"] == "high-correlation"]
    assert kept, "the alpha↔zeta correlation insight should survive redaction"
    for i in kept:
        assert {i["column_name"], i["evidence"]["other_column"]} == {"alpha", "zeta"}
        assert i["evidence"]["correlation"] is not None
