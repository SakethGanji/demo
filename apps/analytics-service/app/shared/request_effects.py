"""Does an audited request actually CHANGE anything?

Two read-models are derived from the audit trail: ``GET /datasets/{id}/usage``
(downloads / writes / reads) and the ``audit`` events inside
``GET /datasets/{id}/timeline`` ("what happened to this dataset"). Both used to
answer that question with the HTTP method — ``POST/PUT/PATCH/DELETE`` was a
write, everything else was not.

The method is a transport fact, not a semantic one. The studio's ordinary row
read is ``POST /datasets/{id}/versions/{v}/sheets/{s}/query``, a POST solely
because a QuerySpec (projection, filter tree, multi-sort, cursor) does not fit
in a query string. Merely OPENING a dataset therefore reported ``writes: 1``,
and a caller holding nothing but ``dataset:read`` could run that counter up
forever. The Library panel publishes the number, so "this dataset is being
written to constantly" was a conclusion a human could reach — and act on —
about a dataset nobody had touched.

So the effect is DECLARED, per route, here, once. Any surface that needs to
know whether an audited request changed something reads this table; nothing
pattern-matches paths for ``/query``-like suffixes, because a suffix rule is
exactly what gets forgotten when the next read-shaped route lands on a
different name (``/compile``, ``/preview``, ``/render``, ``/suggest``…).

WHY EVERY MUTATING ROUTE IS LISTED, not just the read-shaped ones: a missing
entry must FAIL, not default. ``tests/unit/test_route_effect_tripwire.py``
diffs this table against the live route table in both directions, so a new
POST cannot reach main without someone answering "does it persist anything?" —
the question that was never asked for ``/query``. Defaulting an unlisted route
to WRITE would reproduce today's bug for tomorrow's endpoint, silently.

THE TEST IS PERSISTENCE, not intuition: does the handler write a row, or write
AND register an artifact? Each verdict below was read out of the handler, and
the ones that surprise are commented. In particular ``/sql`` and ``/pivot``
*look* like reads and are not — they persist their result as a fetchable
parquet artifact — while ``/charts/{id}/render`` *looks* like the analytics
run it resembles and is not: it computes with ``persist=False`` precisely so a
render does not litter run history.
"""

from __future__ import annotations

READ = "read"
WRITE = "write"

# (HTTP method, declared route path) → effect. The key is the route TEMPLATE,
# never a concrete path: ids and sheet names are caller data and must not be
# able to steer classification.
ROUTE_EFFECTS: dict[tuple[str, str], str] = {
    # -- identity & tenancy ------------------------------------------------
    ("POST", "/api/v1/auth/users"): WRITE,
    ("POST", "/api/v1/teams"): WRITE,
    ("POST", "/api/v1/teams/{team_id}/members"): WRITE,
    ("PATCH", "/api/v1/teams/{team_id}/members/{user_id}"): WRITE,
    ("DELETE", "/api/v1/teams/{team_id}/members/{user_id}"): WRITE,

    # -- dataset lifecycle & metadata --------------------------------------
    ("PATCH", "/api/v1/datasets/{dataset_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}"): WRITE,
    ("PUT", "/api/v1/datasets/{dataset_id}/favorite"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/favorite"): WRITE,
    ("PUT", "/api/v1/datasets/{dataset_id}/sheet-metadata/{sheet_key}"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/sheet-metadata/{sheet_key}"): WRITE,
    ("PUT", "/api/v1/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}"): WRITE,
    ("PUT", "/api/v1/datasets/{dataset_id}/tags"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/tags/{tag_name}/promote"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/tags/{tag_name}/rollback"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/tags/{tag_name}"): WRITE,
    # Reassigns the logical sheet: UPDATEs dataset_version_sheets, the sheet's
    # metadata and its quality rules.
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/confirm-rename"): WRITE,

    # -- ingestion ---------------------------------------------------------
    ("POST", "/api/v1/upload"): WRITE,
    ("POST", "/api/v1/tus/"): WRITE,
    ("PATCH", "/api/v1/tus/{upload_id}"): WRITE,
    ("DELETE", "/api/v1/tus/{upload_id}"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/sheets/{sheet_name}/replace"): WRITE,

    # -- explorer ----------------------------------------------------------
    # The two shapes of the same read: whole-version and single-sheet. A POST
    # because the QuerySpec is a body, not because anything changes.
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/query"): READ,
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/sheets/{sheet_name}/query"): READ,
    # Running a saved view is the same query with the spec fetched from the
    # view row; it records no run of its own.
    ("POST", "/api/v1/datasets/{dataset_id}/views/{view_id}/run"): READ,
    # NOT a read, despite being the "SQL console": the result set is written as
    # parquet and registered as a `query_output` artifact, so every execution
    # leaves durable, downloadable state behind (which is also why it is gated
    # on raw access). See explorer/api.py::sql_query.
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/sql"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/views"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/views/{view_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/views/{view_id}"): WRITE,
    # Opens a job and INSERTs profile_runs + profile_insights.
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/profile-runs"): WRITE,
    # Materializes the changed cells as a `diff_output` artifact when there are
    # any. Conditional persistence is still persistence: classifying by the
    # outcome would make the counter depend on the data.
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{from_version}/sheets/{sheet_name}/row-diff/{to_version}"): WRITE,

    # -- analytics engines (dataset in the body, not the path) -------------
    # These four take `dataset_id` in the request body, so they never match a
    # dataset's usage/timeline scan by path. Classified anyway: the table is
    # about what a route DOES, and a future by-resource_id read-model would
    # inherit the wrong answer from a gap here.
    ("POST", "/api/v1/sample"): WRITE,
    ("POST", "/api/v1/sample/coordinated"): WRITE,
    ("POST", "/api/v1/pivot"): WRITE,
    ("POST", "/api/v1/aggregate"): WRITE,
    # Profiling is the one that computes in DuckDB and returns; unlike its
    # neighbours it registers no artifact.
    ("POST", "/api/v1/profile"): READ,
    ("POST", "/api/v1/samples/{filename}/export"): WRITE,
    ("POST", "/api/v1/storage/gc"): WRITE,

    # -- quality -----------------------------------------------------------
    ("POST", "/api/v1/datasets/{dataset_id}/rules"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/rules/{rule_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/rules/{rule_id}"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/versions/{version_number}/validate"): WRITE,

    # -- library (saved definitions, runs, charts) -------------------------
    ("POST", "/api/v1/datasets/{dataset_id}/analytics"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/analytics/{definition_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/analytics/{definition_id}"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/analytics/{definition_id}/run"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/analytics/runs/{run_id}/publish"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/charts"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/charts/{chart_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/charts/{chart_id}"): WRITE,
    # The deliberate read twin of `/analytics/{id}/run`: `compute_definition`
    # re-runs the operation with `persist=False` and opens no run, so that
    # re-rendering a dashboard does not manufacture history or orphan blobs.
    ("POST", "/api/v1/datasets/{dataset_id}/charts/{chart_id}/render"): READ,

    # -- transformations ---------------------------------------------------
    ("POST", "/api/v1/datasets/{dataset_id}/transformations"): WRITE,
    ("PATCH", "/api/v1/datasets/{dataset_id}/transformations/{definition_id}"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/transformations/{definition_id}"): WRITE,
    # Compile validates a pipeline (optionally against a bounded sample) and
    # preview runs it over a capped sample; neither opens a run nor writes an
    # artifact. They are what the editor calls on every keystroke-ish save.
    ("POST", "/api/v1/datasets/{dataset_id}/transformations/compile"): READ,
    ("POST", "/api/v1/datasets/{dataset_id}/transformations/{definition_id}/preview"): READ,
    ("POST", "/api/v1/datasets/{dataset_id}/transformations/{definition_id}/run"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/transformations/runs/{run_id}/publish"): WRITE,

    # -- relationships & joins ---------------------------------------------
    ("POST", "/api/v1/datasets/{dataset_id}/relationships"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/relationships/{relationship_id}/confirm"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/relationships/{relationship_id}/reject"): WRITE,
    ("DELETE", "/api/v1/datasets/{dataset_id}/relationships/{relationship_id}"): WRITE,
    # Both persist `suggested` relationship rows — "suggest" names what the
    # rows mean, not whether they are stored.
    ("POST", "/api/v1/datasets/{dataset_id}/relationships/seed"): WRITE,
    ("POST", "/api/v1/datasets/{dataset_id}/relationships/suggest"): WRITE,
    # Measures the join and returns sample rows; execute is what materializes
    # the `join_output` artifact and the run.
    ("POST", "/api/v1/joins/preview"): READ,
    ("POST", "/api/v1/joins/execute"): WRITE,
    ("POST", "/api/v1/joins/{run_id}/publish"): WRITE,

    # -- webhooks ----------------------------------------------------------
    ("POST", "/api/v1/webhooks"): WRITE,
    ("PATCH", "/api/v1/webhooks/{subscription_id}"): WRITE,
    ("DELETE", "/api/v1/webhooks/{subscription_id}"): WRITE,
    # "test" delivers for real and INSERTs the webhook_deliveries row.
    ("POST", "/api/v1/webhooks/{subscription_id}/test"): WRITE,

    # -- MCP ---------------------------------------------------------------
    # One JSON-RPC envelope carrying any tool call, so the route cannot say
    # what the message did. WRITE is the safe reading: under-reporting a write
    # is the failure that lets a change look like it never happened.
    ("POST", "/api/v1/mcp"): WRITE,
    ("DELETE", "/api/v1/mcp"): WRITE,
}

# The audit row's ``action`` column is exactly ``"{method} {route template}"``
# (see AuditMiddleware), so a read-model can select read-shaped rows by value
# instead of pattern-matching paths. Passed straight into SQL as a parameter.
READ_ACTIONS: list[str] = sorted(
    f"{method} {path}" for (method, path), effect in ROUTE_EFFECTS.items()
    if effect == READ
)


def effect_of(method: str, route_path: str) -> str:
    """The declared effect of a route, defaulting to WRITE when unknown.

    Unknown means the route table and this table disagree, which the tripwire
    test exists to prevent. The default matters only in that window, and it
    errs the way the old code did — over-reporting writes — because a *missed*
    write is the more damaging error for a history view.
    """
    return ROUTE_EFFECTS.get((method.upper(), route_path), WRITE)
