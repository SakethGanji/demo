"""The audit trail's attribution columns: team, resource, action, metadata.

The only writer of ``audit_log`` is ``AuditMiddleware``, and it used to supply
method/path/status/actor and nothing else. Everything a reader would filter on
— ``team_id``, ``resource_type``, ``resource_id``, ``metadata`` — was NULL on
every row in the service, with two consequences in production:

* ``audit.query(team_ids=[...])`` compiles to ``WHERE team_id = ANY(:tids)``,
  which no NULL row can match. A team-scoped audit read (the only kind that
  could ever be exposed to a non-superuser) answered zero rows and ``total=0``
  for every team — an empty history that looks like "nothing ever happened"
  rather than like a bug.
* Anything wanting one dataset's history had to LIKE-match the path text
  (``path LIKE '%/datasets/' || :did || '%'``), which is why discovery's usage
  and timeline queries are written that way.

``action`` mattered too: defaulting it to ``"{method} {path}"`` put a fresh
UUID in the column meant to name the *kind* of operation, so grouping the trail
by action produced one group per request.
"""

from __future__ import annotations

from app.shared import audit

from conftest import DEFAULT_TEAM_ID, auth, upload_inline

ROWS = '[{"id": 1, "region": "EU"}, {"id": 2, "region": "US"}]'


async def _audit_rows_for(client, admin_id, dataset_id):
    page = (await client.get("/api/v1/audit", params={"limit": 200},
                             headers=auth(admin_id))).json()
    return [e for e in page["items"] if e.get("resource_id") == dataset_id]


async def test_an_audited_write_is_attributed_to_the_dataset_and_its_owning_team(
        client, admin_id):
    """A write must land in the trail carrying WHAT it touched and WHOSE it is.

    Without the resource/team columns the row cannot be found except by string
    matching the path, and every team-scoped audit read is empty.
    """
    up = await upload_inline(client, admin_id, ROWS)
    dataset_id = up["dataset_id"]

    r = await client.put(f"/api/v1/datasets/{dataset_id}/favorite",
                         headers=auth(admin_id))
    assert r.status_code == 204, r.text

    rows = await _audit_rows_for(client, admin_id, dataset_id)
    favorite = next(e for e in rows if e["method"] == "PUT")
    assert favorite["resource_type"] == "dataset"
    assert favorite["resource_id"] == dataset_id
    assert favorite["team_id"] == DEFAULT_TEAM_ID
    # The action names the ROUTE, not this one request's URL.
    assert favorite["action"] == "PUT /api/v1/datasets/{dataset_id}/favorite"


async def test_a_nested_route_is_recorded_against_the_dataset_with_the_inner_ids_kept(
        client, admin_id):
    """Sub-resource writes anchor on the dataset — that is what RBAC scopes and
    what "show me this dataset's history" means — while the inner path params
    stay in metadata so the row still says exactly which tag/rule was touched.
    """
    up = await upload_inline(client, admin_id, ROWS)
    dataset_id = up["dataset_id"]

    r = await client.delete(f"/api/v1/datasets/{dataset_id}/tags/nope",
                            headers=auth(admin_id))
    assert r.status_code == 404, r.text  # audited even when it failed

    rows = await _audit_rows_for(client, admin_id, dataset_id)
    deleted = next(e for e in rows if e["method"] == "DELETE")
    assert deleted["resource_type"] == "dataset"
    assert deleted["resource_id"] == dataset_id
    assert deleted["team_id"] == DEFAULT_TEAM_ID
    assert deleted["action"] == "DELETE /api/v1/datasets/{dataset_id}/tags/{tag_name}"
    assert deleted["metadata"] == {"path_params": {"dataset_id": dataset_id,
                                                   "tag_name": "nope"}}
    assert deleted["status_code"] == 404


async def test_a_team_scoped_audit_query_returns_that_teams_writes(client, admin_id):
    """``audit.query(team_ids=...)`` is the only team-safe way to read the
    trail. While team_id was never written it could not match a single row, so
    the scoping was dead code that silently reported an empty history."""
    up = await upload_inline(client, admin_id, ROWS)
    dataset_id = up["dataset_id"]
    assert (await client.put(f"/api/v1/datasets/{dataset_id}/favorite",
                             headers=auth(admin_id))).status_code == 204

    rows, total = await audit.query(team_ids=[DEFAULT_TEAM_ID], limit=200)
    assert total > 0
    assert all(r["team_id"] == DEFAULT_TEAM_ID for r in rows)
    assert any(r["resource_id"] == dataset_id for r in rows)

    # A team that owns nothing sees nothing — the scoping really does scope.
    other = "00000000-0000-0000-0000-0000000000ff"
    _, other_total = await audit.query(team_ids=[other], limit=10)
    assert other_total == 0
