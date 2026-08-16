"""Version size accounting for multi-sheet uploads, and status-poller stability.

Two silent-wrong-answer regressions a UI would surface directly:

- ``version.size_bytes`` must be the TOTAL across all sheets (matching
  ``row_count`` and the sheet-replace path), not just the canonical/default
  sheet's parquet — otherwise the catalog, version list, and search under-report
  a multi-sheet workbook's size by the size of every non-default sheet.
- ``GET /upload/status`` must report the same ``file_size_bytes`` whether it
  answers from the warm in-memory cache or the durable DB fallback, so the value
  a completed upload shows doesn't change after a process restart.
"""

from __future__ import annotations

import io

from openpyxl import Workbook

from conftest import auth, upload_file, XLSX_MIME


def _big_second_sheet_workbook(path):
    """Default sheet 'Small' (tiny) + a much larger 'Big' sheet.

    The canonical/default parquet ('Small') is tiny while the workbook as a
    whole is large, so a size that counts only the canonical parquet is clearly
    distinguishable from one that sums every sheet.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Small"
    ws.append(["a", "b"])
    ws.append([1, 2])
    ws.append([3, 4])
    big = wb.create_sheet("Big")
    big.append(["c", "d", "e", "f"])
    for i in range(5000):
        big.append([i, f"row-{i}-value", i * 2, f"tag-{i % 7}"])
    wb.save(path)


async def _versions(client, uid, ds):
    r = await client.get(f"/api/v1/datasets/{ds}/versions", headers=auth(uid))
    assert r.status_code == 200, r.text
    return {v["version_number"]: v for v in r.json()["items"]}


async def _sheet_sizes(client, uid, ds, vn):
    r = await client.get(f"/api/v1/datasets/{ds}/versions/{vn}/sheets", headers=auth(uid))
    assert r.status_code == 200, r.text
    return {s["name"]: (s["size_bytes"] or 0) for s in r.json()["items"]}


async def test_multisheet_upload_size_bytes_is_the_sum_across_sheets(client, admin_id, tmp_path):
    src = tmp_path / "wb.xlsx"
    _big_second_sheet_workbook(src)
    up = await upload_file(client, admin_id, src, name="wb.xlsx", content_type=XLSX_MIME)
    ds = up["dataset_id"]

    v1 = (await _versions(client, admin_id, ds))[1]
    per_sheet = await _sheet_sizes(client, admin_id, ds, 1)
    sum_sheets = sum(per_sheet.values())

    # row_count is documented as the total across sheets; size_bytes must match.
    assert v1["row_count"] == 2 + 5000
    assert v1["size_bytes"] == sum_sheets
    # And it must be materially larger than the canonical/default sheet alone —
    # the bug reported only the tiny 'Small' parquet.
    assert v1["size_bytes"] > per_sheet["Small"]


async def test_upload_and_sheet_replace_agree_on_size_semantics(client, admin_id, tmp_path):
    src = tmp_path / "wb2.xlsx"
    _big_second_sheet_workbook(src)
    up = await upload_file(client, admin_id, src, name="wb2.xlsx", content_type=XLSX_MIME)
    ds = up["dataset_id"]

    v1 = (await _versions(client, admin_id, ds))[1]
    assert v1["size_bytes"] == sum((await _sheet_sizes(client, admin_id, ds, 1)).values())

    # Replace the small default sheet; 'Big' is reused copy-on-write.
    r = await client.post(
        f"/api/v1/datasets/{ds}/sheets/Small/replace",
        headers=auth(admin_id),
        files={"file": ("small.csv", io.BytesIO(b"a,b\n9,9\n"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    v2 = (await _versions(client, admin_id, ds))[2]
    assert v2["size_bytes"] == sum((await _sheet_sizes(client, admin_id, ds, 2)).values())

    # Both versions store essentially the same bytes (Big reused), so the
    # version-level sizes are dominated by the shared big sheet and close.
    assert abs(v2["size_bytes"] - v1["size_bytes"]) < per_sheet_delta_bound(v1["size_bytes"])


def per_sheet_delta_bound(total):
    # The only real difference between the two versions is the tiny replaced
    # sheet, so the sizes must be within a small fraction of each other.
    return max(2000, total // 5)


async def test_upload_status_file_size_bytes_is_stable_across_cache_eviction(client, admin_id, tmp_path):
    src = tmp_path / "wb3.xlsx"
    _big_second_sheet_workbook(src)
    up = await upload_file(client, admin_id, src, name="wb3.xlsx", content_type=XLSX_MIME)
    vid = up["version_id"]
    raw_size = up["file_size_bytes"]
    assert raw_size and raw_size > 0

    # Warm cache path.
    r = await client.get(f"/api/v1/upload/status/{vid}", headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["file_size_bytes"] == raw_size

    # Evict the cache to force the durable DB fallback (simulates a restart).
    from app.features.files.services.processing import processing_status
    processing_status.pop(vid, None)
    r = await client.get(f"/api/v1/upload/status/{vid}", headers=auth(admin_id))
    assert r.status_code == 200, r.text
    # The DB fallback must report the SAME (raw) size, not the parquet size.
    assert r.json()["file_size_bytes"] == raw_size
