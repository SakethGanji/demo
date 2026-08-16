"""Curation tools — the only tools here that CHANGE state.

Everything else here reads. These four write: they record what columns mean,
declare the quality rules a dataset must satisfy, run those rules, and move the
named pointers (tags) that downstream consumers resolve.

Two deliberate omissions. Nothing here deletes: no rule delete, no tag delete,
no dictionary delete. And nothing masks — sensitivity is recorded as
documentation, and recording it changes no read path.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import render
from ..client import ProblemError
from ._common import Ctx, clamp, guard, page_items, resolve_version, seg

# rule_type -> (scope, needs a column_selector)
RULE_SCOPES: dict[str, tuple[str, bool]] = {
    "sheet_exists": ("dataset", False),
    "row_count_min": ("sheet", False),
    "not_null": ("column", True),
    "unique": ("column", True),
    "accepted_values": ("column", True),
    "range": ("column", True),
    "regex_match": ("column", True),
    "foreign_key": ("cross_sheet", True),
}

#: Dataset documentation fields the tool can write, and the subset that can be
#: erased. ``name``, ``classification`` and ``deprecated`` back NOT NULL columns
#: (as does ``metadata``, which this tool does not write), so PATCH answers an
#: explicit null on them with a 422 instead of clearing them.
DATASET_FIELDS = (
    "name", "description", "classification", "domain", "source_system",
    "refresh_frequency", "deprecated", "deprecation_reason",
)
DATASET_CLEARABLE = (
    "description", "domain", "source_system", "refresh_frequency", "deprecation_reason",
)

RULE_SHAPES = """\
rule_type      | scope       | parameters
sheet_exists   | dataset     | none — sheet_selector names the sheet that must exist
row_count_min  | sheet       | {"min": 100}
not_null       | column      | none
unique         | column      | none
accepted_values| column      | {"values": ["US", "GB", "CA"]}  (required, non-empty)
range          | column      | {"min": 0} and/or {"max": 100000}  (at least one)
regex_match    | column      | {"pattern": "^[^@]+@[^@]+$"}  (DuckDB regexp_matches)
foreign_key    | cross_sheet | {"ref_sheet": "customers", "ref_column": "customer_id"}"""


def _one_of(value: str, allowed: tuple[str, ...], *, param: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in allowed:
        raise ToolError(
            f"{param} must be one of {', '.join(allowed)}, got {value!r}."
        )
    return normalized


def _require(value: Any, *, param: str, because: str) -> Any:
    if value in (None, ""):
        raise ToolError(f"{param} is required {because}.")
    return value


def _changed(body: dict[str, Any]) -> str:
    """Echo the fields that were actually sent, so a write can be confirmed."""
    return render.fields(
        sorted((k, "(cleared)" if v is None else v) for k, v in body.items())
    ) or "(nothing)"


def _patch_body(
    values: dict[str, Any], clear: list[str] | None, allowed: tuple[str, ...], *,
    noun: str, clearable: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Assemble a merge-PATCH body: written fields, plus explicit nulls to clear.

    The metadata PATCH routes distinguish an absent key (keep what is stored)
    from a key whose value is null (erase it). A tool parameter left unset is
    indistinguishable from one set to null, so clearing gets its own parameter
    rather than a magic sentinel value.

    *clearable* is the subset of *allowed* that may be nulled; it defaults to
    all of them. Dataset documentation is the case where the two differ —
    ``name``, ``classification`` and ``deprecated`` back NOT NULL columns and
    the REST layer answers an explicit null on them with a 422. Naming one in
    ``clear`` is a mistake the tool can diagnose from its own contract, so it
    is refused here rather than round-tripped: the 422 talks about a null in a
    request body, not about the ``clear`` parameter the caller actually used.
    """
    clearable = allowed if clearable is None else clearable
    body = {k: v for k, v in values.items() if v is not None}
    for field in clear or []:
        name = str(field).strip().lower()
        if name not in clearable:
            if name in allowed:
                raise ToolError(
                    f"{name} cannot be cleared — it is a required {noun} field. "
                    f"Write a new value for it instead. Clearable {noun} fields: "
                    f"{', '.join(clearable)}."
                )
            raise ToolError(
                f"clear names {field!r}, which is not a {noun} field. "
                f"Clearable {noun} fields: {', '.join(clearable)}."
            )
        if name in body:
            raise ToolError(
                f"{name} is both written and listed in clear — pick one."
            )
        body[name] = None
    if not body:
        raise ToolError(
            f"Provide at least one {noun} field to write, or name one in clear. "
            f"{noun.capitalize()} fields: {', '.join(allowed)}."
        )
    return body


async def _merge_write(ctx: Ctx, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """PATCH the metadata record, creating it with PUT if it does not exist yet.

    PATCH updates but never creates, so the very first piece of documentation
    written for a sheet or column 404s. On a brand-new record PUT and PATCH mean
    the same thing — every field the caller did not send is null either way — so
    the fallback introduces no whole-record-replace hazard. If the 404 was really
    about the dataset, the sheet or the column, PUT raises it too and that error
    is what surfaces.
    """
    try:
        return await ctx.client.merge_patch(path, body)
    except ProblemError as exc:
        if exc.status != 404:
            raise
        return await ctx.client.put(path, body)


def register(server: MCPServer, ctx: Ctx) -> None:
    # -----------------------------------------------------------------------
    # 1. Documentation
    # -----------------------------------------------------------------------

    @server.tool(
        name="write_documentation",
        description=(
            "WRITES. Record what a dataset, a sheet, or a column actually means — the "
            "business documentation that get_data_dictionary and get_dataset_health "
            "report on. This is the only way to fix a 'documentation: none/partial' "
            "finding; every other tool here can report the gap but not close "
            "it.\n\n"
            "Pick one target per call:\n"
            "- target='dataset' — name, description, classification "
            "(public/internal/confidential/restricted), domain, source_system, "
            "refresh_frequency, deprecated, deprecation_reason.\n"
            "- target='sheet' — grain ('one row per order line'), "
            "primary_key_columns, description. Needs sheet_key.\n"
            "- target='column' — business_name, description, semantic_type, unit, "
            "sensitivity, allowed_values. Needs sheet_key and column_name.\n\n"
            "Requires the dataset:write permission on the owning team; a 404 can mean "
            "the dataset exists but belongs to a team you are not in. Sheet and column "
            "entries are stored per LOGICAL sheet, so they survive a confirmed sheet "
            "rename. The column must exist in the dataset's current version.\n\n"
            "Fields you omit keep their current value — the write is a merge. To erase "
            "a field instead, name it in `clear`; omitting a field and clearing it are "
            "different requests. `clear` is the ONLY way to blank a field, for every "
            "target — writing an empty string stores an empty string. On "
            "target='dataset', name, classification and deprecated are required and "
            "cannot be cleared. Recording sensitivity is documentation only — it masks "
            "nothing."
        ),
    )
    @guard
    async def write_documentation(
        target: Annotated[
            str,
            Field(description="What to document: 'dataset', 'sheet', or 'column'."),
        ],
        dataset_id: Annotated[str, Field(description="Dataset UUID from search_datasets.")],
        sheet_key: Annotated[
            str | None,
            Field(description="Sheet key from describe_dataset. Required for target sheet/column."),
        ] = None,
        column_name: Annotated[
            str | None,
            Field(
                description=(
                    "Normalized column name from describe_dataset. Required for "
                    "target='column'. Must exist in the current version."
                )
            ),
        ] = None,
        description: Annotated[
            str | None,
            Field(description="Prose description. Applies to whichever target you chose."),
        ] = None,
        name: Annotated[
            str | None, Field(description="dataset only: rename the dataset.")
        ] = None,
        classification: Annotated[
            str | None,
            Field(description="dataset only: public, internal, confidential, or restricted."),
        ] = None,
        domain: Annotated[
            str | None,
            Field(description="dataset only: business domain, e.g. 'sales'. Drives search_datasets(domain=...)."),
        ] = None,
        source_system: Annotated[
            str | None, Field(description="dataset only: where the data came from, e.g. 'Salesforce'.")
        ] = None,
        refresh_frequency: Annotated[
            str | None, Field(description="dataset only: e.g. 'daily', 'monthly', 'ad-hoc'.")
        ] = None,
        deprecated: Annotated[
            bool | None,
            Field(description="dataset only: mark the dataset as no longer to be used."),
        ] = None,
        deprecation_reason: Annotated[
            str | None, Field(description="dataset only: why it was deprecated, and what to use instead.")
        ] = None,
        grain: Annotated[
            str | None,
            Field(description="sheet only: what one row represents, e.g. 'one row per customer per day'."),
        ] = None,
        primary_key_columns: Annotated[
            list[str] | None,
            Field(description="sheet only: normalized column names that uniquely identify a row."),
        ] = None,
        business_name: Annotated[
            str | None, Field(description="column only: human label, e.g. 'Credit Limit'.")
        ] = None,
        semantic_type: Annotated[
            str | None,
            Field(description="column only: e.g. 'email', 'currency_usd', 'country_code'. Free text."),
        ] = None,
        unit: Annotated[
            str | None, Field(description="column only: e.g. 'USD', 'days', 'percent'.")
        ] = None,
        sensitivity: Annotated[
            str | None,
            Field(
                description=(
                    "column only: e.g. 'public', 'internal', 'confidential', 'pii'. "
                    "Free text, and documentation only — it does not mask reads."
                )
            ),
        ] = None,
        allowed_values: Annotated[
            list[Any] | None,
            Field(description="column only: the closed set of valid values, when there is one."),
        ] = None,
        clear: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Field names to erase back to empty, e.g. ['unit']. Use this to "
                    "remove documentation that is now wrong; simply omitting a field "
                    "leaves it untouched. Works for every target. For "
                    "target='dataset' only description, domain, source_system, "
                    "refresh_frequency and deprecation_reason can be cleared — name, "
                    "classification and deprecated are required, so write a new value "
                    "for those instead."
                )
            ),
        ] = None,
    ) -> str:
        target = _one_of(target, ("dataset", "sheet", "column"), param="target")

        if target == "dataset":
            # Same assembly as the sheet/column paths: a dataset field is
            # cleared by naming it in `clear`, never by writing an empty
            # string. PATCH /datasets/{id} honours an explicit null as a
            # clear, so stripping nulls here (which is what this used to do)
            # made `description`, `domain`, `source_system`,
            # `refresh_frequency` and `deprecation_reason` un-blankable
            # through the tool.
            body = _patch_body(
                {
                    "name": name,
                    "description": description,
                    "classification": classification,
                    "domain": domain,
                    "source_system": source_system,
                    "refresh_frequency": refresh_frequency,
                    "deprecated": deprecated,
                    "deprecation_reason": deprecation_reason,
                },
                clear,
                DATASET_FIELDS,
                noun="dataset",
                clearable=DATASET_CLEARABLE,
            )
            if body.get("classification") is not None:
                body["classification"] = _one_of(
                    body["classification"],
                    ("public", "internal", "confidential", "restricted"),
                    param="classification",
                )
            # merge_patch, not patch: `patch` drops None from the body, which
            # would strip the very nulls `clear` exists to send. The dataset
            # record always exists (a 404 means the dataset does), so there is
            # no PUT fallback here — unlike `_merge_write`.
            row = await ctx.client.merge_patch(f"/datasets/{dataset_id}", body)
            return render.join(
                f"Updated dataset {row.get('id')}.",
                render.section("Wrote", _changed(body)),
                render.section(
                    "Now stored",
                    render.fields(
                        [
                            ("name", row.get("name")),
                            ("description", row.get("description")),
                            ("classification", row.get("classification")),
                            ("domain", row.get("domain")),
                            ("source_system", row.get("source_system")),
                            ("refresh_frequency", row.get("refresh_frequency")),
                            ("deprecated", row.get("deprecated")),
                            ("deprecation_reason", row.get("deprecation_reason")),
                            ("updated_at", row.get("updated_at")),
                        ]
                    ),
                ),
            )

        _require(sheet_key, param="sheet_key", because=f"for target='{target}'")

        if target == "sheet":
            fields = ("grain", "primary_key_columns", "description")
            provided = _patch_body(
                {
                    "grain": grain,
                    "primary_key_columns": primary_key_columns,
                    "description": description,
                },
                clear,
                fields,
                noun="sheet",
            )
            # PATCH merges server-side and tells an absent field from an explicit
            # null, so the old read-merge-write round trip is gone — and with it
            # the race between the read and the write.
            row = await _merge_write(
                ctx, f"/datasets/{dataset_id}/sheet-metadata/{seg(sheet_key)}", provided
            )
            return render.join(
                f"Updated sheet metadata for '{row.get('sheet_key')}'.",
                render.section("Wrote", _changed(provided)),
                render.section(
                    "Now stored",
                    render.fields(
                        [
                            ("sheet_key", row.get("sheet_key")),
                            ("grain", row.get("grain")),
                            ("primary_key_columns", ", ".join(row.get("primary_key_columns") or [])),
                            ("description", row.get("description")),
                            ("updated_at", row.get("updated_at")),
                        ]
                    ),
                ),
            )

        _require(column_name, param="column_name", because="for target='column'")
        fields = (
            "business_name",
            "description",
            "semantic_type",
            "unit",
            "sensitivity",
            "allowed_values",
        )
        provided = _patch_body(
            {
                "business_name": business_name,
                "description": description,
                "semantic_type": semantic_type,
                "unit": unit,
                "sensitivity": sensitivity,
                "allowed_values": allowed_values,
            },
            clear,
            fields,
            noun="column",
        )
        # Same PATCH merge as the sheet route.
        row = await _merge_write(
            ctx,
            f"/datasets/{dataset_id}/sheet-metadata/{seg(sheet_key)}/columns/{seg(column_name)}",
            provided,
        )
        return render.join(
            f"Updated dictionary entry for {row.get('sheet_key')}.{row.get('column_name')}.",
            render.section("Wrote", _changed(provided)),
            render.section(
                "Now stored",
                render.fields(
                    [
                        ("column", row.get("column_name")),
                        ("business_name", row.get("business_name")),
                        ("description", row.get("description")),
                        ("semantic_type", row.get("semantic_type")),
                        ("unit", row.get("unit")),
                        ("sensitivity", row.get("sensitivity")),
                        ("allowed_values", ", ".join(str(v) for v in (row.get("allowed_values") or []))),
                        ("updated_at", row.get("updated_at")),
                    ]
                ),
            ),
            "Stored under the normalized column name. Sensitivity here is a label, not "
            "an access control — nothing is masked as a result of it.",
        )

    # -----------------------------------------------------------------------
    # 2. Quality rules
    # -----------------------------------------------------------------------

    @server.tool(
        name="manage_quality_rules",
        description=(
            "WRITES. Create or update the quality rules a dataset must satisfy. Rules "
            "are declarations only — nothing is evaluated until run_quality_check "
            "action='validate' runs them, and their results are what get_dataset_health "
            "reports under 'validation' and what gates tag promotion.\n\n"
            "action='create' adds a rule (its name must be unique on the dataset; a "
            "duplicate name is a 409). action='update' patches an existing rule by "
            "rule_id — you can change selectors, parameters, severity, and enabled, but "
            "NOT rule_type. There is deliberately no delete: disable a rule with "
            "enabled=false instead.\n\n"
            "Rule types and their parameters:\n"
            f"{RULE_SHAPES}\n\n"
            "sheet_selector is always required and must be a sheet_key from "
            "describe_dataset; it is pinned to that logical sheet, so the rule follows "
            "a confirmed rename. column_selector must be a normalized column name. "
            "severity='error' failures block promote_tag; severity='warning' failures "
            "are recorded but do not block.\n\n"
            "Example — create a foreign key rule:\n"
            '{"action": "create", "dataset_id": "<uuid>", "name": "orders.customer_id '
            'is a real customer", "rule_type": "foreign_key", "sheet_selector": '
            '"orders", "column_selector": "customer_id", "parameters": {"ref_sheet": '
            '"customers", "ref_column": "customer_id"}, "severity": "error"}\n\n'
            "Requires dataset:write. Use list-style reads (get_dataset_health) to see "
            "the effect."
        ),
    )
    @guard
    async def manage_quality_rules(
        action: Annotated[str, Field(description="'create' or 'update'.")],
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        rule_id: Annotated[
            str | None,
            Field(description="Rule UUID. Required for action='update'; ignored on create."),
        ] = None,
        name: Annotated[
            str | None,
            Field(description="Rule name, unique per dataset. Required on create."),
        ] = None,
        rule_type: Annotated[
            str | None,
            Field(
                description=(
                    "One of sheet_exists, row_count_min, not_null, unique, "
                    "accepted_values, range, regex_match, foreign_key. Required on "
                    "create and immutable afterwards."
                )
            ),
        ] = None,
        sheet_selector: Annotated[
            str | None,
            Field(description="Target sheet_key. Required on create for every rule type."),
        ] = None,
        column_selector: Annotated[
            str | None,
            Field(description="Target normalized column name. Required for column and cross_sheet rules."),
        ] = None,
        parameters: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Rule-type specific settings — see the table in this tool's "
                    'description, e.g. {"min": 100} or {"values": ["US", "GB"]}. On '
                    "update this REPLACES the whole parameters object."
                )
            ),
        ] = None,
        severity: Annotated[
            str | None,
            Field(description="'error' (blocks tag promotion) or 'warning' (recorded only). Default error."),
        ] = None,
        description: Annotated[
            str | None, Field(description="Why this rule exists, for whoever reads the failure.")
        ] = None,
        enabled: Annotated[
            bool | None,
            Field(description="Only enabled rules run. Set false to retire a rule — there is no delete."),
        ] = None,
    ) -> str:
        action = _one_of(action, ("create", "update"), param="action")
        if severity is not None:
            severity = _one_of(severity, ("error", "warning"), param="severity")

        if action == "create":
            _require(name, param="name", because="on create")
            _require(rule_type, param="rule_type", because="on create")
            if rule_type not in RULE_SCOPES:
                raise ToolError(
                    f"rule_type {rule_type!r} is not supported. Valid types: "
                    f"{', '.join(sorted(RULE_SCOPES))}."
                )
            scope, needs_column = RULE_SCOPES[rule_type]
            params = parameters or {}
            if not sheet_selector:
                raise ToolError(
                    f"sheet_selector is required for every rule type, including "
                    f"{rule_type} ({scope} scope). Pass a sheet_key from describe_dataset."
                )
            if needs_column and not column_selector:
                raise ToolError(
                    f"{rule_type} is a {scope}-scoped rule and needs column_selector "
                    "(a normalized column name)."
                )
            if rule_type == "accepted_values" and not params.get("values"):
                raise ToolError(
                    'accepted_values needs parameters {"values": [...]} with at least one entry.'
                )
            if rule_type == "foreign_key" and not (
                params.get("ref_sheet") and params.get("ref_column")
            ):
                raise ToolError(
                    'foreign_key needs parameters {"ref_sheet": "<sheet_key>", '
                    '"ref_column": "<column>"}.'
                )
            if rule_type == "range" and params.get("min") is None and params.get("max") is None:
                raise ToolError(
                    'range needs parameters with "min", "max", or both — otherwise the '
                    "rule errors at validation time."
                )
            if rule_type == "regex_match" and not params.get("pattern"):
                raise ToolError('regex_match needs parameters {"pattern": "<regex>"}.')

            body = {
                "name": name,
                "description": description,
                "rule_type": rule_type,
                "sheet_selector": sheet_selector,
                "column_selector": column_selector,
                "parameters": params,
                "severity": severity or "error",
                "enabled": True if enabled is None else enabled,
            }
            body = {k: v for k, v in body.items() if v is not None}
            row = await ctx.client.post(f"/datasets/{dataset_id}/rules", body)
            verb = "Created"
        else:
            _require(rule_id, param="rule_id", because="on update")
            body = {
                "name": name,
                "description": description,
                "sheet_selector": sheet_selector,
                "column_selector": column_selector,
                "parameters": parameters,
                "severity": severity,
                "enabled": enabled,
            }
            body = {k: v for k, v in body.items() if v is not None}
            if not body:
                raise ToolError(
                    "Provide at least one field to update: name, description, "
                    "sheet_selector, column_selector, parameters, severity, or enabled. "
                    "rule_type cannot be changed — create a new rule instead."
                )
            row = await ctx.client.patch(f"/datasets/{dataset_id}/rules/{rule_id}", body)
            verb = "Updated"

        return render.join(
            f"{verb} rule '{row.get('name')}' ({row.get('id')}).",
            render.section("Wrote", _changed(body)),
            render.section(
                "Now stored",
                render.fields(
                    [
                        ("rule_id", row.get("id")),
                        ("name", row.get("name")),
                        ("description", row.get("description")),
                        ("rule_type", row.get("rule_type")),
                        ("scope_type", row.get("scope_type")),
                        ("sheet_selector", row.get("sheet_selector")),
                        ("column_selector", row.get("column_selector")),
                        ("parameters", row.get("parameters")),
                        ("severity", row.get("severity")),
                        ("enabled", row.get("enabled")),
                    ]
                ),
            ),
            "Nothing has been evaluated yet — run run_quality_check with "
            "action='validate' to test this rule against a version."
            if row.get("enabled")
            else "This rule is disabled and will be skipped by validation runs.",
        )

    # -----------------------------------------------------------------------
    # 3. Quality runs
    # -----------------------------------------------------------------------

    @server.tool(
        name="run_quality_check",
        description=(
            "WRITES (persists a run). Actually execute quality work against a version "
            "and store the result. get_dataset_health reports 'unknown' for validation, "
            "missing data, duplicates and drift precisely because no run exists — this "
            "is the tool that creates one.\n\n"
            "action='validate' runs every ENABLED quality rule against a version and "
            "persists a validation run. Requires dataset:write. 409 if the version is "
            "not ready; 400 if the dataset has no enabled rules (create some with "
            "manage_quality_rules first). Its result is what gates manage_tags "
            "action='promote'.\n\n"
            "action='profile' profiles every ready sheet of a version and persists one "
            "run per sheet with deterministic insights (nulls, duplicates, outliers, "
            "constant columns). Only needs dataset:read. It is idempotent per algorithm "
            "version, so re-running is cheap and safe.\n\n"
            "Omit version to use the newest ready version. Returns per-rule and "
            "per-sheet summaries, not raw rows."
        ),
    )
    @guard
    async def run_quality_check(
        action: Annotated[str, Field(description="'validate' (run the rules) or 'profile' (profile the sheets).")],
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        version: Annotated[
            int | None,
            Field(description="Version number. Omit for the newest ready version.", ge=1),
        ] = None,
    ) -> str:
        action = _one_of(action, ("validate", "profile"), param="action")
        version_number = await resolve_version(ctx, dataset_id, version)

        if action == "validate":
            try:
                run = await ctx.client.post(
                    f"/datasets/{dataset_id}/versions/{version_number}/validate"
                )
            except ProblemError as exc:
                if exc.status == 400:
                    raise ToolError(
                        f"{exc.detail} Create at least one rule with "
                        "manage_quality_rules(action='create') — a rule that exists but "
                        "is disabled does not count."
                    ) from exc
                if exc.status == 409:
                    raise ToolError(
                        f"{exc.detail} Only a 'ready' version can be validated; check "
                        "describe_dataset for version statuses."
                    ) from exc
                raise

            results = run.get("results") or []
            rows = [
                {
                    "rule": r.get("rule_name"),
                    "type": r.get("rule_type"),
                    "sheet": r.get("sheet_selector"),
                    "column": r.get("column_selector"),
                    "severity": r.get("severity"),
                    "status": r.get("status"),
                    "failures": r.get("failure_count"),
                    "message": r.get("message"),
                }
                for r in results
            ]
            samples = [
                f"{r.get('rule_name')} -> {r.get('failure_sample_file')}"
                for r in results
                if r.get("failure_sample_file")
            ]
            errored = [r.get("rule_name") for r in results if r.get("status") == "error"]

            notes = []
            if samples:
                notes.append(
                    render.section(
                        "Failing rows saved (up to 5 per rule)",
                        render.bullets(samples)
                        + "\nRead any of these with read_artifact(filename=...).",
                    )
                )
            if errored:
                notes.append(
                    f"Rules that could not be evaluated at all: {', '.join(errored)}. "
                    "'error' means the rule is broken (missing sheet or column, bad "
                    "parameters) — not that the data failed."
                )
            if (run.get("error_failures") or 0) > 0:
                notes.append(
                    "There are error-level failures, so manage_tags action='promote' "
                    "will refuse this version until they are fixed or the rules are "
                    "downgraded to severity='warning'."
                )

            return clamp(
                render.join(
                    f"Validated version {version_number}.",
                    render.fields(
                        [
                            ("validation_run_id", run.get("id")),
                            ("status", run.get("status")),
                            ("rules_total", run.get("rules_total")),
                            ("rules_passed", run.get("rules_passed")),
                            ("rules_failed", run.get("rules_failed")),
                            ("error_failures", run.get("error_failures")),
                            ("warning_failures", run.get("warning_failures")),
                            ("completed_at", run.get("completed_at")),
                        ]
                    ),
                    render.section("Per rule", render.table(rows)),
                    *notes,
                ),
                60_000,
                hint="Narrow the dataset's rule set, or read the per-rule results with "
                "get_dataset_health.",
            )

        runs = await ctx.client.post(
            f"/datasets/{dataset_id}/versions/{version_number}/profile-runs"
        )
        if not isinstance(runs, list):
            runs = page_items(runs)
        summary = [
            {
                "sheet": r.get("sheet_name"),
                "status": r.get("status"),
                "insights": len(r.get("insights") or []),
                "algorithm": r.get("algorithm_version"),
                "error": r.get("error"),
            }
            for r in runs
        ]
        insight_rows = [
            {
                "sheet": r.get("sheet_name"),
                "severity": i.get("severity"),
                "rule": i.get("rule"),
                "column": i.get("column_name"),
                "message": i.get("message"),
            }
            for r in runs
            for i in (r.get("insights") or [])
        ]
        return clamp(
            render.join(
                f"Profiled version {version_number} — {len(runs)} sheet(s), "
                f"{len(insight_rows)} insight(s).",
                render.section("Per sheet", render.table(summary)),
                render.section("Insights", render.table(insight_rows)),
                "These insights are now persisted, so get_dataset_health will stop "
                "reporting 'unknown' for the dimensions they cover. Profiling reads the "
                "data but returns no rows.",
            ),
            60_000,
            hint="Profile a single sheet's columns with profile_column instead.",
        )

    # -----------------------------------------------------------------------
    # 4. Tags
    # -----------------------------------------------------------------------

    @server.tool(
        name="manage_tags",
        description=(
            "WRITES (except action='history'). A tag is a named pointer — 'production', "
            "'certified' — to ONE WHOLE VERSION of a dataset. Tags never point at a "
            "sheet, a column, or a row: moving a tag moves every sheet in that version "
            "together. Versions themselves are immutable, so a tag is the only mutable "
            "handle consumers should resolve.\n\n"
            "action='set' points the tag at a version. This is the ungated escape "
            "hatch: it does not check validation and it does not require the version to "
            "be ready.\n"
            "action='promote' is the governed path. It requires a ready version, "
            "records a reason in the audit history, and IS QUALITY-GATED: if the "
            "dataset has any enabled quality rules, the target version must already "
            "have a completed validation run with zero error-level failures. Otherwise "
            "you get a 409 — 'validation-required' (run run_quality_check "
            "action='validate' first) or 'validation-failed' (fix the data or the "
            "rules).\n"
            "action='rollback' moves the tag back to the previous version in its own "
            "history. It is DELIBERATELY NOT gated — it is the emergency path, so a bad "
            "promotion can always be undone even while validation is failing. It needs "
            "a prior version in the tag's history (409 otherwise).\n"
            "action='history' reads every transition of a tag, newest first, with who "
            "did it and why. It survives everything else.\n\n"
            "Writes require dataset:write. Tag names are case-insensitive and stored "
            "lowercased. There is deliberately no tag deletion here."
        ),
    )
    @guard
    async def manage_tags(
        action: Annotated[
            str, Field(description="'set', 'promote', 'rollback', or 'history'.")
        ],
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        tag: Annotated[
            str,
            Field(description="Tag name, e.g. 'production'. Case-insensitive.", min_length=1),
        ],
        version: Annotated[
            int | None,
            Field(
                description=(
                    "Target version number. Required for 'set' and 'promote'; ignored "
                    "for 'rollback' (which reads the target from history) and 'history'."
                ),
                ge=1,
            ),
        ] = None,
        reason: Annotated[
            str | None,
            Field(
                description=(
                    "Why. Recorded in the tag history for 'promote' and 'rollback'; "
                    "strongly recommended, since the history is the audit trail."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="history only: max entries to return (1-200).", ge=1, le=200)
        ] = 50,
        offset: Annotated[int, Field(description="history only: entries to skip.", ge=0)] = 0,
    ) -> str:
        action = _one_of(action, ("set", "promote", "rollback", "history"), param="action")
        tag = tag.strip().lower()

        if action == "history":
            page = await ctx.client.get(
                f"/datasets/{dataset_id}/tags/{seg(tag)}/history", limit=limit, offset=offset
            )
            rows = [
                {
                    "when": h.get("created_at"),
                    "action": h.get("action"),
                    "from": h.get("from_version_number"),
                    "to": h.get("to_version_number"),
                    "actor": h.get("actor_email") or h.get("actor_user_id"),
                    "reason": h.get("reason"),
                }
                for h in page_items(page)
            ]
            return clamp(
                render.join(
                    render.section(f"History of tag '{tag}'", render.table(rows)),
                    render.count_note(len(rows), page.get("total"), noun="transitions"),
                    "Newest first. The history outlives the tag itself, so it still "
                    "answers 'what was production on that date?'.",
                ),
                60_000,
                hint="Page with offset, or lower limit.",
            )

        if action == "set":
            _require(version, param="version", because="for action='set'")
            row = await ctx.client.put(
                f"/datasets/{dataset_id}/tags",
                {"tag_name": tag, "version_number": version},
            )
            return render.join(
                f"Tag '{row.get('tag_name')}' now points at version {row.get('version_number')}.",
                render.fields(
                    [
                        ("tag", row.get("tag_name")),
                        ("version_number", row.get("version_number")),
                        ("version_id", row.get("version_id")),
                        ("updated_at", row.get("updated_at")),
                    ]
                ),
                "This was the ungated path — no validation was checked. Use "
                "action='promote' when the move should be governed and auditable.",
            )

        if action == "promote":
            _require(version, param="version", because="for action='promote'")
            try:
                row = await ctx.client.post(
                    f"/datasets/{dataset_id}/tags/{seg(tag)}/promote",
                    {"version_number": version, "reason": reason},
                )
            except ProblemError as exc:
                if exc.code == "validation-required":
                    raise ToolError(
                        f"{exc.detail} This dataset has enabled quality rules, so a "
                        "version must pass validation before a tag can be promoted to "
                        f"it. Call run_quality_check(action='validate', version={version}) "
                        "and then retry."
                    ) from exc
                if exc.code == "validation-failed":
                    raise ToolError(
                        f"{exc.detail} Promotion is blocked by error-level rule "
                        f"failures (validation_run_id "
                        f"{exc.extra.get('validation_run_id')}, "
                        f"{exc.extra.get('error_failures')} error / "
                        f"{exc.extra.get('warning_failures')} warning). Fix the data and "
                        "re-validate, downgrade the offending rules to "
                        "severity='warning', or use action='set' as the documented "
                        "ungated escape hatch."
                    ) from exc
                raise
        else:
            try:
                row = await ctx.client.post(
                    f"/datasets/{dataset_id}/tags/{seg(tag)}/rollback", {"reason": reason}
                )
            except ProblemError as exc:
                if exc.status == 409:
                    raise ToolError(
                        f"{exc.detail} Rollback replays the tag's own history, so it "
                        "needs an earlier version this tag previously pointed at and "
                        "that is still ready. Check manage_tags(action='history')."
                    ) from exc
                raise

        return render.join(
            f"{'Promoted' if action == 'promote' else 'Rolled back'} tag "
            f"'{row.get('tag_name')}': version {row.get('from_version_number')} -> "
            f"{row.get('to_version_number')}.",
            render.fields(
                [
                    ("tag", row.get("tag_name")),
                    ("action", row.get("action")),
                    ("from_version", row.get("from_version_number")),
                    ("to_version", row.get("to_version_number")),
                    ("reason", row.get("reason")),
                ]
            ),
            "The acting user is recorded automatically as the actor — see "
            "manage_tags(action='history').",
            "Promotion passed the quality gate: the target version has a completed "
            "validation run with no error-level failures."
            if action == "promote"
            else "Rollback is never quality-gated, so this succeeded regardless of "
            "validation state.",
        )
