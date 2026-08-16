"""A transform pipeline that can never succeed is rejected at SAVE, not run.

A malformed replace-regex or an unrecognized parse_dates strptime format is a
static property of the config — it fails on any input, even zero rows. It must
be caught when the definition is compiled/saved (the same problem+json a run
would raise), not slip past save and fail later inside the worker.
"""

from __future__ import annotations

import json

from conftest import auth, rid, upload_inline


async def _ds(client, uid):
    rows = [{"id": 1, "txt": "abc", "when": "2020-01-01"},
            {"id": 2, "txt": "def", "when": "2020-02-01"}]
    return (await upload_inline(client, uid, json.dumps(rows)))["dataset_id"]


async def test_bad_regex_is_rejected_at_compile_and_save(client, admin_id):
    h = auth(admin_id)
    ds = await _ds(client, admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    steps = [{"type": "replace", "column": "txt", "mode": "regex",
              "find": "(", "replace_with": "x"}]

    comp = await client.post(f"{base}/compile", headers=h, json={"sheet": "data", "steps": steps})
    assert comp.status_code == 422, comp.text
    assert comp.json()["code"] == "invalid-step"

    save = await client.post(base, headers=h, json={"name": f"bad-{rid()}", "sheet": "data", "steps": steps})
    assert save.status_code == 422, save.text
    assert save.json()["code"] == "invalid-step"


async def test_bad_strptime_format_is_rejected_at_save(client, admin_id):
    h = auth(admin_id)
    ds = await _ds(client, admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    steps = [{"type": "parse_dates", "columns": ["when"], "format": "%Q-nonsense"}]
    save = await client.post(base, headers=h, json={"name": f"bad-{rid()}", "sheet": "data", "steps": steps})
    assert save.status_code == 422, save.text
    assert save.json()["code"] == "invalid-step"


async def test_valid_regex_and_dates_still_save(client, admin_id):
    h = auth(admin_id)
    ds = await _ds(client, admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    steps = [{"type": "replace", "column": "txt", "mode": "regex", "find": "a.c", "replace_with": "x"},
             {"type": "parse_dates", "columns": ["when"], "format": "%Y-%m-%d"}]
    comp = await client.post(f"{base}/compile", headers=h, json={"sheet": "data", "steps": steps})
    assert comp.status_code == 200, comp.text
    save = await client.post(base, headers=h, json={"name": f"ok-{rid()}", "sheet": "data", "steps": steps})
    assert save.status_code == 201, save.text
