# analytics-service — verified defect report

Every claim below was produced by a domain agent reading the source, then handed to a
SECOND, independent agent whose instruction was to **refute** it — default to REFUTED
unless the code clearly supports the claim. Only `CONFIRMED_BUG` survived that.

`BY_DESIGN` means the behaviour is real but deliberate and defensible (this repo
documents its choices heavily — e.g. 404-not-403 for cross-tenant reads is a stated
security property, not a bug). `REFUTED` means the claim was factually wrong about the
code; they are kept at the bottom so nobody re-raises them.

## Tally

- **105 confirmed bugs** — 5 critical, 41 high, 54 medium, 5 low
- **41 of them return a confident WRONG answer** rather than an error. That is the worst class here: a UI cannot detect it, and neither can the user.
- 37 by design · 34 refuted · 0 uncertain
- 176 claims adjudicated in total


# CONFIRMED_BUG

## [critical] PATCHing a rule's sheet_selector to a name with no live logical sheet leaves the old logical_sheet_id, so the rule keeps validating the previous sheet. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/quality/api.py:47-52 — `if fields.get("sheet_selector"): ls = await get_live_dataset_sheet(...); if ls: fields["sheet_selector"] = ls["current_sheet_key"]; fields["logical_sheet_id"] = ls["id"]`. There is no `else`: when `ls` is None the new selector text is stored but `logical_sheet_id` is left out of `fields`.

api.py:91-94 passes `body.model_dump(exclude_unset=True)` to `repo.update_rule`, and repo.py:71-84 builds the SET list only from keys present in `fields` (`_MUTABLE` includes `logical_sheet_id`), so an absent key means the old column value survives. RuleUpdate (schemas.py:61-70) has `sheet_selector: str | None = None` with no validator — nothing at the pydantic layer rejects a selector that does not resolve, and creating rules for sheets that do not exist yet is explicitly supported (test_quality.py:187 creates a "refunds" rule and expects a per-rule error).

engine.py:32-49 `_find_sheet` checks `logical_sheet_id` first and returns on the first match, only falling through to the selector text if the id is absent/unmatched — deliberate and unit-tested (tests/unit/test_quality_engine_logical.py). engine.py:119 and :125 both call it with `rule.get("logical_sheet_id")`. So after such a PATCH the rule evaluates the OLD sheet's parquet, while `_result` (engine.py:66-80) snapshots `rule.get("sheet_selector")` — the NEW text — into `validation_rule_results`. GET /rules also returns the new selector (RuleOut), with logical_sheet_id the only, unexplained, contradicting field.

Sub-case, same root: PATCH `{"sheet_selector": null}` takes the same falsy branch, nulls the selector column, and still leaves the stale id, so the rule silently keeps running against the old sheet with no selector shown at all.

No test exercises PATCH of sheet_selector: the only PATCH in tests/test_quality.py:122 sets `enabled: False`; tests/test_logical_sheets.py:87-133 covers create-time pinning and confirm-rename rewrite, never a user-initiated selector change. Nothing in HANDOFF.md or ARCHITECTURE.md (§ logical_sheet_id, HANDOFF.md:412) documents stickiness on update — the docstring at api.py:41-46 states the intended contract ("the logical id is what makes the rule follow a confirmed rename"), which a user retarget is not.

**Fix**

In `_resolve_sheet_selector`, key off presence rather than truthiness and always write the id (including None) whenever the caller supplied `sheet_selector`:

    if "sheet_selector" in fields:
        sel = fields["sheet_selector"]
        ls = await get_live_dataset_sheet(dataset_id, sel) if sel else None
        fields["logical_sheet_id"] = ls["id"] if ls else None
        if ls:
            fields["sheet_selector"] = ls["current_sheet_key"]

`logical_sheet_id` is already in repo `_MUTABLE`, so the explicit None clears the column; the create path is unaffected (it always dumps all fields, and an unresolvable selector yields None as today).

**Test to pin it**

"test_patching_a_rule_to_an_unknown_sheet_clears_its_logical_pin_instead_of_silently_validating_the_old_sheet" — integration, tests/test_quality.py (needs the real dataset/version/validate path: create a rule on 'orders', PATCH sheet_selector to 'refunds', run validate, assert the result is an error/not-found rather than a pass sourced from 'orders', and assert GET /rules shows logical_sheet_id is null).

## [critical] Saved definitions can carry params.file_path, and the library run/render path never applies the superuser file_path guard, so any member reads arbitrary server files. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

I tried to refute this on four fronts (pydantic rejects file_path in params; base overrides it; load_data sandboxes the path; a test asserts 403). All four fail.

1. params is unvalidated free-form JSON at save time. app/features/library/schemas.py:34 `params: dict[str, Any] = Field(default_factory=dict, ...)` on DefinitionCreate, and schemas.py:45 on DefinitionUpdate. app/features/library/api.py:47-52 `create_definition` only does `ensure_dataset_permission(..., DATASET_WRITE)` then `repo.create_definition(dataset_id, {**body.model_dump(...)})` — nothing inspects params keys.

2. file_path survives the bind and is NOT shadowed. app/features/library/service.py:117 `base = {"dataset_id": dataset_id, "sheet": definition.get("sheet"), **pin}` — base has no `file_path` key — and service.py:81 `return model(**{**params, **base})`. Since base wins only on keys it contains, a stored `params["file_path"]` lands on the request model verbatim. The models accept it: schemas.py:588 (SampleRequest), :796 (ProfileRequest), :902 (AggregateRequest), :992 (PivotRequest) all declare `file_path: str | None = Field(default=None, ...)` with no validator.

3. file_path beats the authorized dataset in every executor: services/aggregation.py:320-322, services/sampling.py:330-332, services/profiling.py:195-197, services/pivot.py:136-138 all read `file_path = request.file_path` / `if not file_path and request.dataset_id: file_path = await resolve_dataset_path(...)`. So resolve_dataset_path is never reached when file_path is set.

4. load_data does not sandbox. app/shared/data_io.py:79-96: `s3://` URIs stream via httpfs, otherwise `Path(file_path)` is opened directly if it exists and ends in .csv/.parquet/.xlsx/.xls, or globbed as a directory of parquet. No root check, no key mapping.

5. The guard genuinely exists only on the direct endpoints. app/features/data_accelerator/api.py:97-111 `_authorize_source` raises `HTTPException(403, "file_path sources are restricted to platform administrators")`, docstring at :100-103 "reads straight from the server filesystem with no team scoping ... otherwise any member could point it at another team's parquet files". `grep -rn "restricted to platform"` finds it only in data_accelerator/api.py, files/api.py (retention/GC) and audit/api.py — nothing in app/features/library/. execute_definition (service.py:111-187) and compute_definition (service.py:341-378) call run_sampling_pipeline/run_aggregation/run_pivot/run_profiling directly, with no _authorize_source anywhere on the path.

6. No test contradicts it: `grep -rn "file_path" tests/ | grep -i "librar|definition|chart|403|restricted"` returns nothing. And no doc blesses it: the only mention in ARCHITECTURE.md:164 is "Ownerless outputs (inline-data sources, superuser `file_path`)", which reaffirms file_path is meant to be superuser-only.

7. Reach: api.py:110-119 run_definition needs only DATASET_WRITE on the attacker's own dataset, returns the rows inline in RunResponse.result, and the output is registered as an artifact in the attacker's team (service.py:168-182), publishable via publish_run. api.py:253-277 render_chart requires only DATASET_READ and calls compute_definition on the same unguarded path — but note the chart must point at a definition, and creating/updating a definition or chart needs DATASET_WRITE, so a pure viewer can only re-trigger a poisoned definition someone with WRITE already saved.

**Fix**

Strip/reject source-selection keys when binding a stored definition. In `build_definition_request` (app/features/library/service.py:79-81), drop `file_path` (and `data`, `dataset_id`, `version_id`, `version_number`, `tag`) from `params` before the merge, raising a 400 `invalid-definition` naming `file_path` if present — the definition's source is the dataset it hangs off, never its params. Belt-and-braces: reject the same keys in DefinitionCreate/DefinitionUpdate via a `params` validator so a poisoned definition can't be stored in the first place.

**Test to pin it**

"a saved definition whose params carry file_path is rejected instead of reading that file" — integration, tests/ (library run + chart render endpoints), plus a unit test in tests/unit/ "build_definition_request drops source-selection keys from stored params".

## [critical] POST /charts/{id}/render calls run_view without a principal, so a view-backed chart returns raw sensitive column values that /views/{id}/run masks. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Masking is conditional on a principal being passed. app/features/explorer/service.py:184 `async def run_view(ds, view, overrides: RunViewRequest, principal=None)` and 217-223: `if principal is not None: ... masked = await resolve_masking(dataset_id, row, principal); if masked: result.items = mask_rows(...); result.masked_columns = sorted(masked)`. The explorer route does pass it — app/features/explorer/api.py:179 `return await service.run_view(ds, view, body or RunViewRequest(), principal)`.

The chart render route does not. app/features/library/api.py:255-300 `render_chart` gates only on `ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)` (line 267) and then at line 288 calls `result = await run_view(ds, view, RunViewRequest(limit=1000))` — four positional args, no principal — so `principal is None` and the masking block is skipped entirely. `rows = result.result.items` (line 289) are therefore raw dataset rows.

Those raw values reach the response body. app/features/library/charts.py:112-155 `build_series` puts `str(row[x_field])` into `categories` and `row.get(field)` into each series' `data`; `infer_fields` (charts.py:62-95) defaults `x_field` to the first non-numeric column and `y_fields` to every numeric column, and config can name any column explicitly. So a declared-PII email/name column becomes the raw `categories` list and a sensitive numeric column (salary, account id) becomes raw series data — per-row, unaggregated, with no `masked_columns` signal in ChartRenderResponse.

This is not a documented exemption. app/shared/masking.py:7-11 states "**This is a real control, not a display convenience.** Masking rows while leaving `/download` open would be theatre" — and ensure_raw_access (masking.py:159-176) 403s the raw download for any dataset declaring sensitive columns. HANDOFF.md:143-147 says masking is "enforced wherever raw rows are returned". ARCHITECTURE.md:126 says DATASET_READ_SENSITIVE "gates both the unmasked read path and the raw download". Chart render is a raw read path that is ungated. ARCHITECTURE.md:576 lists resolve_masking's callers as "explorer, downloads, row diff" — descriptive of as-built, not a rationale for exempting charts. `grep -rn "run_view(" app/` shows only two callers: explorer/api.py:179 (with principal) and library/api.py:288 (without).

No test contradicts it: tests/test_pii_masking.py has test_a_saved_view_masks_when_run (line 66) but nothing for chart render; `grep -n "chart\|render" tests/test_pii_masking.py` returns nothing.

One overstatement in the claim: the response is not "a full data grid". ChartRenderResponse returns only categories/series for x_field, y_fields and series_field, capped at MAX_CATEGORIES, and `chart_type` (including "table", schemas.py:107) does not change build_series' output shape. The leak is real but scoped to the charted columns — which the caller chooses via config, so it is selectable to any sensitive column. The definition-backed branch (compute_definition, library/service.py:340-375) also never masks; for kind="sample" that returns raw rows too, so the same hole exists on that arm.

**Fix**

Pass the principal through: `run_view(ds, view, RunViewRequest(limit=1000), principal)` at app/features/library/api.py:288. Also surface `result.masked_columns` on ChartRenderResponse so the UI can label the axis. Separately, mask the definition arm: apply `mask_rows(rows, await resolve_masking(...))` to compute_definition's output in render_chart (at minimum for kind="sample", which returns raw rows), or make run_view's `principal` parameter required so a future caller cannot silently opt out.

**Test to pin it**

"test_a_chart_over_a_view_masks_the_same_columns_the_view_run_masks" in tests/test_pii_masking.py (integration layer, tests/ — it needs the dictionary, view, chart and principal plumbing end to end): declare a column sensitive, save a view and a chart over it with that column as x_field, then assert the render's `categories` equal the masked values a non-elevated caller gets from POST /views/{id}/run, and that an admin gets the raw ones.

## [critical] confirm-rename deletes the spurious logical sheet, cascade-destroying transformation definitions/runs and relationships the 409 guard never counts. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out and the call path is complete.

1. The guard, /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/shared/repo.py:237-254 `count_logical_sheet_state`, issues exactly three COUNTs — `dataset_sheet_metadata`, `quality_rules`, `dataset_column_metadata` — and returns `{"sheet_metadata", "quality_rules", "column_metadata"}`. No count of `transformation_definitions` or `dataset_relationships`.

2. The only caller is app/features/data_accelerator/services/sheet_identity.py:83-90: `state = await count_logical_sheet_state(to_row["logical_sheet_id"])` / `if any(state.values()): raise ProblemException(409, ..., code="conflicting-sheet-state")`. With all three counts zero the 409 does not fire, and line 92 calls `reassign_logical_sheet`.

3. The DELETE is real: repo.py:287-290 `DELETE FROM dataset_sheets WHERE id = :id` with `:id = new_logical_id` (the auto-created identity), executed inside the same transaction that then re-keys the survivor. Nothing in `reassign_logical_sheet` moves transformation or relationship rows off `new_logical_id` first — it only UPDATEs `dataset_version_sheets`, `dataset_sheet_metadata` and `quality_rules`.

4. The CASCADE is real: migrations/20260809010000_transformations.sql:15 `logical_sheet_id UUID NOT NULL REFERENCES dataset_sheets(id) ON DELETE CASCADE`, and `transformation_runs.definition_id ... REFERENCES transformation_definitions(id) ON DELETE CASCADE` — so the run history goes with it. 20260810000000_relationships.sql:19,22 have the identical `ON DELETE CASCADE` on `from_logical_sheet_id`/`to_logical_sheet_id`.

5. The scenario is reachable. app/features/transform/service.py:93-94,138 resolves the sheet from the *current* version and stores `logical_sheet_id=str(row["logical_sheet_id"])`, so a definition created against the renamed sheet in v2 (before confirming) is keyed to the spurious identity — precisely the id that gets deleted.

6. No test contradicts it. tests/test_transformations.py:402 `test_a_definition_survives_a_confirmed_sheet_rename` creates the definition against the *old* sheet ("Expenses" in v1) — the surviving id — and only asserts that direction. The 409 tests (tests/test_logical_sheets.py:211, tests/test_column_metadata.py:373) use metadata/column state, never a transformation. `grep` finds `transformation_definitions`/`dataset_relationships` nowhere under app/shared/ or app/features/data_accelerator/, so there is no second guard.

7. Not by design — the docs say the opposite. ARCHITECTURE.md:260-265: "If you add sheet-keyed state, either key it by logical_sheet_id (preferred ...) or add it to shared/repo.py::reassign_logical_sheet and extend count_logical_sheet_state so the 409 conflict guard sees it." Both later tables took option A and skipped the guard extension; the migration header comment "Definitions key on logical_sheet_id ... so confirm-rename never has to rewrite them" is only true for the survivor side and misses the deleted side. WAVE4-6-PLAN.md:327-328 flags the same requirement.

**Fix**

Add two COUNTs to `count_logical_sheet_state` (repo.py:237): `SELECT COUNT(*) FROM transformation_definitions WHERE logical_sheet_id = :id` and `SELECT COUNT(*) FROM dataset_relationships WHERE from_logical_sheet_id = :id OR to_logical_sheet_id = :id`, returned as `transformations` / `relationships` keys so the existing `any(state.values())` 409 in sheet_identity.py:84 covers them. (Alternative, if silently folding is wanted instead of refusing: UPDATE those tables' logical ids from new→old in `reassign_logical_sheet` before the DELETE — but the relationships UNIQUE (from,from_col,to,to_col) can collide, so the 409 is the safe fix.)

**Test to pin it**

"test_confirm_rename_refuses_when_the_new_identity_owns_a_transformation" in tests/ (integration, alongside tests/test_logical_sheets.py::test_confirm_rename_conflicting_state_409): create the transformation against the v2 renamed sheet, POST confirm-rename, assert 409 code="conflicting-sheet-state" and that GET /transformations/{id} still resolves. A sibling case should do the same for a relationship whose from/to endpoint is the spurious identity.

## [critical] PII masking's exemption check is team-unscoped: admin/owner in ANY team unmasks sensitive columns and unlocks raw downloads in every team the caller can read. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/shared/masking.py:130-134 — `def _has_permission(principal, permission): return any(role_has(role, permission) for role in (principal.memberships or {}).values())`. It takes no team argument and ORs across every membership the caller has. `may_see_raw(principal)` (masking.py:121-127) is its only caller, and `resolve_masking` (masking.py:144) and `ensure_raw_access` (masking.py:168) are gated purely on it — neither is ever passed the dataset's team, even though both receive `dataset_id`.

This directly contradicts the codebase's own stated model. app/features/auth/permissions.py:3 — "A user's authority is evaluated *within a team*." The correct, team-scoped primitive already exists and is used everywhere else: app/features/auth/deps.py:40-44 `Principal.can(team_id, permission)` looks up `self.memberships.get(team_id)`. Every other authorization site uses it (deps.py:139 `ensure_dataset_permission`, deps.py:155 `require_team_permission`, auth/api.py:128/141/157/176). masking.py is the single place that drops the team dimension. DATASET_READ_SENSITIVE is in `_ADMIN` (permissions.py:51-55), so admin/owner anywhere grants it.

The call sites confirm the blast radius is normal read paths, not exotic ones: app/features/explorer/service.py:101 (preview/query — sets `page.masked_columns`), explorer/service.py:220, data_accelerator/services/rowdiff.py:300, and files/api.py:668 and :686 (dataset and version download, right after a team-scoped `ensure_dataset_permission(..., DATASET_READ)`). So a caller who is merely a viewer/editor of team A — which passes the DATASET_READ gate — but admin or owner of any other team gets raw PII from team A's dataset with `masked_columns: []`, and a 200 on `/download` instead of the 403 `sensitive-data-restricted`.

The escalation half of the claim is also accurate: app/features/auth/api.py:104-112 `create_team` declares only `Depends(get_principal)` — no permission check at all — and then `await repo.upsert_member(team["id"], principal.user_id, Role.OWNER.value)`. Any authenticated user can mint a team and become its owner. But the bug does not need this; a genuinely multi-team user (admin of team B, viewer of team A) triggers it with no attack at all.

No test contradicts it. tests/test_pii_masking.py only ever uses single-membership users: `analyst()` (line 32-36) calls `create_team_user(..., team_id=DEFAULT_TEAM)`, and tests/conftest.py:249-270 gives each user exactly one membership. `test_editors_are_deliberately_not_exempt` (test_pii_masking.py:107) and `test_the_raw_download_is_gated_once_pii_is_declared` (line 115) pass only because of that single membership; neither exercises a second team.

Not deliberate. HANDOFF.md:143-147 describes the control as "`dataset:read_sensitive` (admin/owner, deliberately not editor)" and masking.py:5-11 calls it "a real control, not a display convenience" — the docs claim a team-scoped RBAC control, and the code implements a global one. permissions.py:39-43 documents only the editor exclusion as deliberate; nothing anywhere documents cross-team exemption.

**Fix**

Thread the dataset's team through the check. Change `may_see_raw(principal)` to `may_see_raw(principal, team_id)` and implement it as `principal.is_superuser or principal.can(team_id, Permission.DATASET_READ_SENSITIVE)` — deleting `_has_permission` entirely, since `Principal.can` (deps.py:40) already does the right thing. Then: `resolve_masking(dataset_id, sheet_row, principal)` and `ensure_raw_access(principal, dataset_id)` both already have `dataset_id`, so load the dataset row (`app.shared.repo.get_dataset`) or accept `team_id` from the callers, which all hold it — explorer/service.py:101 and :220, rowdiff.py:300, files/api.py:668 and :686 all sit immediately after an `ensure_dataset_permission` call that already returns the dataset row containing `team_id`; pass that through rather than re-reading. Separately (smaller, independent): decide whether `POST /api/v1/teams` should be superuser-only or documented as intentionally open, and add the corresponding gate or docstring.

**Test to pin it**

"a user who is an admin of a second team still sees masked PII in a team where they are only a viewer" — integration, tests/test_pii_masking.py (needs a real second team + membership, so it belongs in tests/, not tests/unit/). Pair it with "a user who is an admin of a second team is still denied the raw download in a team where they are only a viewer" asserting 403 / code sensitive-data-restricted on both /datasets/{id}/download and /versions/1/download. A tests/unit/ companion can pin the primitive directly: "may_see_raw is false for a principal who holds the elevated role only in another team".

## [high] PATCH rule renaming onto an existing rule name escapes as an opaque 500 while POST returns a 409 for the identical collision.

- **domain:** ?
- **where:** 

**Evidence**

Every cited location checks out and nothing on the path intercepts the violation.

1. The constraint is a plain (non-partial) unique index: app/infra/db/postgres/migrations/20260804030000_quality.sql:38-39 `CREATE UNIQUE INDEX IF NOT EXISTS idx_quality_rules_dataset_name ON quality_rules (dataset_id, name);`

2. `name` is patchable: app/features/quality/schemas.py, RuleUpdate declares `name: str | None = Field(default=None, min_length=1, max_length=255)`. Pydantic only bounds length — it cannot know about other rows, so validation does NOT reject the collision (this was the most likely refutation and it fails).

3. app/features/quality/repo.py:87-94 (update_rule) issues a bare `UPDATE quality_rules SET {sets} WHERE id = :id AND dataset_id = :did RETURNING ...` with no ON CONFLICT and no try/except, in contrast to create_rule at repo.py:31 which has `ON CONFLICT (dataset_id, name) DO NOTHING` and returns None so api.py:70-71 can raise `HTTPException(409, f"A rule named '{body.name}' already exists on this dataset")`.

4. app/features/quality/api.py:91-97 (PATCH handler) calls repo.update_rule with no exception handling; only a falsy row maps to 404.

5. No IntegrityError mapping exists anywhere on this path. `grep -rn "IntegrityError|UniqueViolation" app/` returns exactly two hits, both in app/features/explorer/service.py (18, 178). app/api/errors.py:145-147 registers only three handlers (StarletteHTTPException, RequestValidationError, bare Exception), so the IntegrityError lands in `_unhandled_exception_handler` (errors.py:136-140) which renders problem 500 with detail "An unexpected error occurred." unless settings.debug.

6. Not deliberate — the repo's own sibling does the opposite. app/features/explorer/service.py:176-179 wraps the analogous view rename in `except IntegrityError: raise HTTPException(409, f"A view named '{fields.get('name')}' already exists on this dataset")`. Quality rules simply missed that treatment.

7. No test contradicts it: tests/test_quality.py never PATCHes a rule `name` at all (its only PATCH is `{"enabled": False}` at line 122); the 409 assertions at lines 97 and 105 are promotion-gate cases, not name collisions. Nothing asserts the claimed-wrong behaviour is intended, and neither HANDOFF.md nor ARCHITECTURE.md documents a rename-collision policy for rules.

**Fix**

Mirror explorer/service.py: in app/features/quality/api.py::update_rule, wrap the `repo.update_rule(...)` call in `try: ... except IntegrityError: raise HTTPException(409, f"A rule named '{body.name}' already exists on this dataset")` (importing `from sqlalchemy.exc import IntegrityError`). Alternatively push the guard into repo.update_rule so the MCP tool path gets it too — check whether app/features/mcp calls repo.update_rule directly before choosing.

**Test to pin it**

"Renaming a quality rule onto an existing rule name in the same dataset returns 409, not 500" — integration layer, tests/test_quality.py (needs two real rules on one dataset plus the DB unique index, so it cannot live in tests/unit/).

## [high] Running a `join` definition via POST /datasets/{ds}/analytics/{def_id}/run raises a bare KeyError and returns an opaque 500 instead of a 400.

- **domain:** ?
- **where:** 

**Evidence**

Every link in the chain checks out.

1. `execute_join` really does create a persisted definition with kind="join": /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/relationships/joins.py:246 `"kind": "join",` inside `_join_definition`, written through `library_repo.create_definition` (joins.py:240) — i.e. it bypasses the API's `DefinitionCreate`, whose `Kind = Literal["sample", "aggregate", "profile", "pivot"]` (app/features/library/schemas.py:9) excludes "join". So pydantic never blocks this.

2. It is listed: `list_definitions` (app/features/library/api.py:59-67) returns `DefinitionOut(**r)` and `DefinitionOut.kind` is a bare `str` (schemas.py:53), so no filter and no validation error. tests/test_join_builder.py:156-157 asserts `defs.json()["items"][0]["kind"] == "join"` — the test confirms the claim rather than contradicting it.

3. The run path has no kind guard: api.py:110-121 `run_definition` -> `execute_definition(ds, definition, principal)`; service.py:119-123 sets `kind = definition["kind"]` and calls `build_definition_request(kind, params, base)` BEFORE `jobs.create_job` (service.py:125) and before the `try:` at service.py:133. `build_definition_request` line 79 is `model = _REQUEST_MODELS[kind]`, and `_REQUEST_MODELS` (service.py:50-55) has only sample/aggregate/pivot/profile. KeyError, raised outside any try — the `except ValidationError` at line 82 does not cover the lookup, and the blanket `except Exception` in `execute_definition` is not yet entered.

4. Nothing upstream catches it: the app's catch-all `_unhandled_exception_handler` (app/api/errors.py:135-140) turns it into `problem_response(500, "An unexpected error occurred.")` with no `code` field.

5. It is not deliberate. The sibling read path proves the intended contract is a 400: `compute_definition` (service.py:354-356) explicitly guards `if kind not in ("pivot", "aggregate", "sample"): raise HTTPException(400, f"A '{kind}' definition produces no tabular result to chart")`. The run path simply lacks the equivalent guard.

6. No test covers it: `grep -rn "analytics/.*/run"` over tests/ hits test_library.py, test_pivot.py, test_stranded_runs.py, test_artifact_retention.py, test_timeline.py, test_completed_run_demotion.py — none uses a join definition; tests/test_join_builder.py only lists the definition and reads its /runs history.

One mitigating detail the claim does not mention (and which does not save it): because the KeyError fires before `jobs.create_job`, no job row and no stranded `running` analytics_runs row is left behind — the blast radius is the response only.

**Fix**

In `build_definition_request` (app/features/library/service.py:79) replace the bare subscript with a lookup that raises the same 400-shaped ProblemException used elsewhere, e.g. `model = _REQUEST_MODELS.get(kind)` and if None `raise ProblemException(400, f"A '{kind}' definition cannot be run here — join definitions are executed via POST /joins/execute", code="unrunnable-definition-kind", kind=kind)`. That fixes both `execute_definition` and `compute_definition` callers in one place and keeps the "no job/run row for something that never started" property intact.

**Test to pin it**

"test_running_a_join_definition_returns_400_not_500" in tests/ (integration layer — it needs the real join to exist first: declare a relationship, POST /api/v1/joins/execute, read the auto-created join definition from GET /datasets/{ds}/analytics, then POST .../run and assert 400 with a problem `code`). A cheap companion unit test belongs in tests/unit/test_definition_binding.py: "test_binding_an_unknown_definition_kind_raises_a_400_problem_not_a_keyerror".

## [high] POST /relationships/suggest?sync=false always returns job_id=null, and the truncation `skipped` count is dropped by SuggestResponse in both modes. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/shared/worker.py:122-126 — `job = await jobs.create_job(...)` then `if not inline: return None`. The job row (and its id) exists but is thrown away; the docstring at :119-120 confirms "``inline=False`` returns immediately with the result being ``None``".

app/features/relationships/api.py:73-83 — `result = await worker.dispatch(..., inline=sync) or {}`, then `SuggestResponse(job_id=result.get("job_id"), ...)`. With sync=false, `result` is `{}`, so job_id is None. The only place job_id is ever populated is service.py:302 `return {**result, "job_id": str(job["id"])}` inside `_handle_discovery`, which reaches the caller ONLY on the inline path (worker.py:140-145) — i.e. job_id is non-null exactly when the caller does not need it, and null in the async mode the field was added for.

Existing tests do NOT contradict this — they confirm it. tests/test_relationships.py:97 asserts `r.json()["job_id"]` only for the default sync=true call. The async test (tests/test_relationships.py:115-128, `test_discovery_runs_as_a_job_the_worker_can_claim`) deliberately asserts only `suggested == 0` and never touches job_id, then reaches into `worker.run_pending_jobs_once()` in-process rather than polling by handle.

Second half: service.py:210-212 and :288-290 both return `"skipped": proposed - len(candidates)`, and the cap is deliberate and documented (service.py:45-47: "so the number that reach SQL is capped, and what was skipped is reported", MAX_CANDIDATE_PAIRS = 400, applied at service.py:168 `return candidates[:MAX_CANDIDATE_PAIRS], total`). But SuggestResponse (schemas.py:54-60) declares only job_id/pairs_examined/suggested/relationships, and api.py:79-83 constructs it field-by-field, so `skipped` is silently discarded. The truncation is only logged (service.py:207-209). So the comment's promise "what was skipped is reported" is true of the service and false of the API.

Mitigation, not refutation: GET /jobs (app/features/jobs/api.py:37-47) can filter by job_type and returns `result`, so a UI could scrape the newest `relationship_discovery` job — but list_jobs takes no dataset_id filter, so with concurrent discovery runs the UI cannot reliably correlate the job it just enqueued.

**Fix**

Make `worker.dispatch` return `{"job_id": str(job["id"])}` instead of `None` on the `inline=False` path (worker.py:125-126) — the job row already exists at that point and every caller already treats the return as an optional dict. Then add `skipped: int = 0` to SuggestResponse (schemas.py:54-60) and pass `skipped=result.get("skipped", 0)` at api.py:79-83 so a capped run is visible to the caller, not just to the log.

**Test to pin it**

"test_async_discovery_returns_a_job_id_the_caller_can_poll" in tests/test_relationships.py (integration layer): POST suggest?sync=false, assert job_id is not null, then GET /api/v1/jobs/{job_id} returns 200 with status pending, and after worker.run_pending_jobs_once() the same job id reports completed. Plus a unit test "test_suggest_response_reports_pairs_skipped_by_the_candidate_cap" in tests/unit/ that stubs _candidate_pairs to propose more than MAX_CANDIDATE_PAIRS and asserts the response carries a non-zero skipped count.

## [high] validate_version's blanket `except Exception` rewrites in-run HTTPExceptions (e.g. the 404 "Sheet data unreadable") into a generic 500.

- **domain:** ?
- **where:** 

**Evidence**

The cited code is exactly as claimed. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/quality/api.py:139-159 wraps the run body in `try:` and ends with:
  156 `    except Exception as e:`
  157 `        await repo.fail_run(run["id"], str(e))`
  158 `        await jobs.fail_job(str(job["id"]), str(e))`
  159 `        raise HTTPException(500, f"Validation run failed: {e}")`
There is no `isinstance(exc, StarletteHTTPException): raise` branch, and `HTTPException` is a subclass of `Exception`, so any 4xx raised inside is caught and re-raised as 500.

A 4xx is genuinely reachable inside that block. Line 140 calls `ensure_sheet_schema` for every sheet, and /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/shared/datasets.py:171-176 does:
  `    except (duckdb.Error, OSError) as e:`
  `        raise HTTPException(404, f"Sheet data unreadable for '{sheet_row['sheet_name']}': {type(e).__name__}")`
(reached when `schema_json` is empty and the parquet is missing/corrupt — the lazy backfill path documented in that docstring). Grep shows no other HTTPException raiser inside quality/engine.py, so this is the main in-run 4xx, but it is a real one.

This is NOT by design — the repo treats this exact shape as a bug everywhere else and fixed it, with comments saying so:
- app/features/library/service.py:188-210: "every actionable 4xx raised DURING a run (unknown-column, sheet-selection-required, invalid-sort-order, unknown-operator) fell through to the generic branch and surfaced as a 500 'Analytics run failed'. Re-raising the original preserves its status, its problem+json `code` and its extra fields." — it does bookkeeping then `if isinstance(exc, StarletteHTTPException): raise`.
- app/features/transform/service.py:255-261: `except StarletteHTTPException as exc:` … "Spelling it as the base is the same rule the other two sites use (``library/service.py``, ``mcp/identity.py``)".
quality/api.py is the site that was never converted to that rule. No test asserts the 500 (grep for "Validation run failed" hits only api.py:159; no test references the unreadable-sheet path), and no docstring/comment defends it — the only explanatory comments in this handler (lines 161-166) are about the webhook block after the try, not the status-code flattening.

**Fix**

Mirror library/service.py: keep the bookkeeping unconditional, then preserve the original status.
```python
except Exception as e:
    await repo.fail_run(run["id"], str(e))
    await jobs.fail_job(str(job["id"]), str(e))
    if isinstance(e, StarletteHTTPException):  # covers ProblemException + FastAPI's
        raise
    raise HTTPException(500, f"Validation run failed: {e}") from e
```
(import `from starlette.exceptions import HTTPException as StarletteHTTPException`, same as the other three sites.)

**Test to pin it**

"test_validate_version_surfaces_unreadable_sheet_as_404_not_500" — integration layer, tests/test_quality.py: create a ready version whose sheet row has no schema_json and whose parquet is missing, POST /datasets/{id}/versions/{n}/validate, assert 404 and that the detail still says "Sheet data unreadable" (and that the validation_runs/jobs rows are closed as failed, not left running).

## [high] Publishing a join into any dataset other than the join's left side records a lineage parent pairing the target dataset's id/name with the LEFT dataset's version id/number. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/relationships/api.py:255-260 — `target_id = body.dataset_id or relationship["dataset_id"]`; `target_ds = await ensure_dataset_permission(principal, target_id, Permission.DATASET_WRITE)`; then `joins.publish_join(run, relationship, target_ds, mode=body.mode, ...)`. The only check on body.dataset_id is WRITE permission; nothing requires it to be one of the relationship's two sides. Schema does not constrain it either: app/features/relationships/schemas.py:125-130 `dataset_id: str | None = Field(default=None, description="Target dataset for new_version mode; defaults to the join's left side")` — a free-form string.

app/features/relationships/joins.py:341-366 — `publish_join(run, relationship, target_ds, ...)` does `artifact, parent_ver = await resolve_publishable_artifact(run)` and then `publish_artifact_as_version(target_ds, artifact, parent_ver, ...)`. So the single positional `ds` is the TARGET, while `parent_ver` is the run's source version. app/features/library/service.py:226 shows `parent_ver = await get_version(run["dataset_version_id"])`, and joins.py:267 shows the join run is created with `dataset_version_id=str(left.version["id"])` — i.e. parent_ver is always the LEFT dataset's version.

app/features/library/service.py:322-327 — `await repo.record_lineage(target_id, version_id, parent_dataset_id=str(ds["id"]), parent_version_id=str(parent_ver["id"]), parent_dataset_name=ds["name"], parent_version_number=parent_ver["version_number"], ...)`. With ds = target_ds, the parent edge mixes the target dataset's identity with the left dataset's version. The same mismatch is baked into the version's own source metadata at service.py:281-283 (`"from_dataset_id": str(ds["id"]), "from_version_number": parent_ver["version_number"]`).

No DB guard: app/infra/db/postgres/migrations/20260804040000_reuse.sql:69-82 has independent FKs on parent_dataset_id and parent_version_id, so the mismatched pair is stored silently.

The sibling callers are unaffected because they pass the run's own dataset as ds (library/service.py:242-247 publish_run; transform/service.py:427-433), which is why this only bites the join path.

Not by design: the route docstring (api.py:241) says "Publish a join output as a new dataset (or a new version of one side)" — even the documented case body.dataset_id = the RIGHT dataset is broken: the left parent row disappears and is replaced by (right dataset id/name, left version id/number). Neither ARCHITECTURE.md:431/570 nor HANDOFF.md:228 documents a third-dataset target.

Test coverage claim also holds: tests/test_join_builder.py has 4 publish calls (lines 223, 245, 265, 296) and every one sends only mode/name; `new_version` and `dataset_id` appear nowhere in that file. The passing test test_publish_records_both_parents (line 215) omits dataset_id, so target == left and the bug is masked.

**Fix**

Give publish_artifact_as_version an explicit lineage parent that is independent of the publish target — e.g. `parent_ds: dict | None = None` defaulting to `ds`, used only in the record_lineage call (and in the version `source` dict). In joins.publish_join, fetch the LEFT dataset (`await get_dataset(relationship["dataset_id"])`) and pass it as parent_ds while target_ds stays the publish target. Optionally also reject body.dataset_id that is neither side of the relationship with a 400 in api.py:255.

**Test to pin it**

"test_publishing_a_join_as_a_new_version_of_the_right_side_records_the_left_parent_correctly" — integration level, tests/test_join_builder.py: execute a left/right join, POST /joins/{run_id}/publish with {"mode": "new_version", "dataset_id": right}, then GET the new version's lineage and assert one parent is (left dataset id, left version id) and the other is (right dataset id, right version id) — i.e. no row pairs one dataset's id with another dataset's version.

## [high] PATCH /datasets/{id}/charts/{cid} with definition_id and/or view_id explicitly null clears both source columns and 500s on the DB CHECK.

- **domain:** ?
- **where:** 

**Evidence**

app/features/library/schemas.py:130-136 — `ChartUpdate` has NO `@model_validator`; `ChartCreate` (schemas.py:110-127) does have `_exactly_one_source` raising when `(definition_id is None) == (view_id is None)`. So the create path is guarded and the update path deliberately is not covered by pydantic.

app/features/library/api.py:220-241 (`update_chart`): `fields = body.model_dump(exclude_unset=True)`; the only source guard is `if fields.get("definition_id") is not None and fields.get("view_id") is not None: raise HTTPException(400, ...)`. `_validate_chart_source` (api.py:169-179) no-ops for None on both branches. The retarget-clearing branches are `if fields.get("definition_id") is not None: ... elif fields.get("view_id") is not None: ...` — neither fires when both are None. So `{"definition_id": null, "view_id": null}` reaches the repo with both keys present and None.

app/features/library/repo.py:452-463 (`update_chart`): the loop `for col in ("name","description","chart_type","definition_id","view_id"): if col in fields:` uses key presence, not truthiness, so it emits `definition_id = NULL, view_id = NULL` in the UPDATE.

app/infra/db/postgres/migrations/20260807030000_charts.sql:25 — `CHECK ((definition_id IS NULL) <> (view_id IS NULL))` with comment "-- Exactly one data source." The resulting IntegrityError is not caught anywhere in the library feature (the only `except IntegrityError` in app/ is app/features/explorer/service.py:178), so it lands in `_unhandled_exception_handler` (app/api/errors.py:136-140) => problem+json 500.

Worse than the claim states: a body of just `{"definition_id": null}` on a definition-backed chart also nulls the only populated column and 500s — no need to send both fields.

Tests do not contradict: tests/test_charts.py:51-54 covers only the retarget case (`{"view_id": ..., "chart_type": "line"}` -> old source cleared) and :72 covers create-with-both -> 4xx. No test PATCHes a null source.

Deliberateness check argues the other way: HANDOFF.md:348-349 states the invariant is enforced by "DB CHECK + pydantic validator", which is true for POST and false for PATCH — the 500 is an oversight, not a documented choice.

**Fix**

Add a `@model_validator(mode="after")` to `ChartUpdate` (or an explicit check in `update_chart`) that rejects clearing a source: if `definition_id` and `view_id` are both explicitly set to None -> 422/400; if exactly one is explicitly set to None with the other unset, either reject with 400 ("a chart must render exactly one source") or require the caller to supply the replacement. Simplest handler-level version in api.py after the both-set check: `if "definition_id" in fields or "view_id" in fields: if fields.get("definition_id") is None and fields.get("view_id") is None: raise HTTPException(400, "A chart renders exactly one source — set definition_id or view_id")`.

**Test to pin it**

"test_patch_chart_cannot_clear_its_only_source_returns_400" in tests/test_charts.py (integration layer, since it needs the DB CHECK path), plus a pure-schema case "test_chart_update_rejects_both_sources_null" in tests/unit/ if the guard lands in the pydantic model.

## [high] POST /datasets/{id}/analytics/{def_id}/run on a `join` definition raises KeyError('join') and returns a bare 500 instead of a typed 400.

- **domain:** ?
- **where:** 

**Evidence**

Every link in the chain checks out.

1. `join` definitions are real and are written straight into `analytics_definitions`: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/relationships/joins.py:238-247 — `_join_definition` calls `library_repo.create_definition(... "kind": "join", "params": {"relationship_id": ..., "how": ...})`. Reached from POST /joins/execute (`execute_join`, joins.py:255-258).

2. The DB CHECK allows it: app/infra/db/postgres/migrations/20260810010000_join_builder.sql:14-15 `CHECK (kind IN ('sample','aggregate','profile','pivot','join'))`. Deliberate — the migration header says "the guided join builder is a new analytics KIND, not a new table".

3. The library list returns it unfiltered: repo.list_definitions (library/repo.py:40-47) is a plain `SELECT ... WHERE dataset_id`, and `DefinitionOut.kind` is a bare `str` (library/schemas.py:53), not the `Kind = Literal["sample","aggregate","profile","pivot"]` used only on the *create* request (schemas.py:9,31). So pydantic does NOT filter it out on the way out. An existing test asserts exactly this: tests/test_join_builder.py:155-157 `assert defs.json()["items"][0]["kind"] == "join"` — it confirms the claim rather than contradicting it.

4. The run path has no kind guard: api.py:110-121 `run_definition` -> `execute_definition(ds, definition, principal)`; service.py:119-123 `kind = definition["kind"]` then `request = build_definition_request(kind, params, base)`; service.py:79 `model = _REQUEST_MODELS[kind]` with `_REQUEST_MODELS` = sample/aggregate/pivot/profile only (service.py:50-55). kind="join" -> KeyError, raised before the job/run rows are opened, so no blanket `except Exception` catches it.

5. It surfaces as a contentless 500: the global `_unhandled_exception_handler` (app/api/errors.py:136-140) returns `problem_response(500, "An unexpected error occurred.")` with no `code` and no detail unless `settings.debug`. (Small correction to the claim's wording: it *is* rendered as problem+json, just an opaque 500 — the substance stands.)

6. The contrast is exactly as claimed and shows the omission is an oversight, not a policy: compute_definition (service.py:354-356) `if kind not in ("pivot","aggregate","sample"): raise HTTPException(400, f"A '{kind}' definition produces no tabular result to chart")`. And the library explicitly knows about joins elsewhere — publish maps `"join": "joined_from"` (service.py:241). Nothing in HANDOFF.md, ARCHITECTURE.md, or ROADMAP.md documents "join definitions are not re-runnable"; grep for such a note found nothing.

**Fix**

In `execute_definition` (app/features/library/service.py, before the `build_definition_request` call at line 123), mirror `compute_definition`'s guard: `if kind not in _REQUEST_MODELS: raise ProblemException(400, f"A '{kind}' definition cannot be re-run from the library; use POST /joins/execute", code="kind-not-runnable", kind=kind)`. Raising before `jobs.create_job` keeps the existing "never leave a failed run behind" property. Optionally add a `runnable: bool` (or `runnable_kinds`) field to `DefinitionOut` so the UI can disable the Run button.

**Test to pin it**

"test_running_a_join_definition_from_the_library_is_a_400_not_a_500" — integration layer, tests/test_join_builder.py (needs a real join definition created via POST /joins/execute, then POST /datasets/{ds}/analytics/{def_id}/run asserting 400 with a `code` and no analytics_runs row added).

## [high] PATCH on an analytics definition or chart renaming onto a sibling's name raises an uncaught IntegrityError and returns 500 instead of the 409 the POST path returns.

- **domain:** ?
- **where:** 

**Evidence**

The claim checks out at every layer.

1. The uniqueness constraints exist. app/infra/db/postgres/migrations/20260804040000_reuse.sql:45-46 `CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_definitions_dataset_name ON analytics_definitions (dataset_id, name);` and app/infra/db/postgres/migrations/20260807030000_charts.sql:23 `UNIQUE (dataset_id, name)` on chart_definitions.

2. The create paths absorb the collision. app/features/library/repo.py:26 `ON CONFLICT (dataset_id, name) DO NOTHING` -> returns None; app/features/library/api.py:54-55 `if not row: raise HTTPException(409, f"A definition named '{body.name}' already exists on this dataset")`. Same pair at repo.py:416 / api.py:193 for charts.

3. The update paths do not. app/features/library/repo.py:76-80 issues a bare `UPDATE analytics_definitions SET {sets} WHERE id = :id AND dataset_id = :did RETURNING ...` with no ON CONFLICT and no try/except; repo.py:465-472 is the same for chart_definitions. `name` is explicitly in the allowed set (repo.py:63) and in the chart column loop (repo.py:457), so a rename really does reach the index.

4. The schema does not pre-empt it. schemas.py:41 `DefinitionUpdate.name: str | None = Field(default=None, min_length=1, max_length=255)` and schemas.py:131 `ChartUpdate.name` — only length validation, no uniqueness. The handlers (api.py:83-97, api.py:220-237) do no pre-check either; their only failure branch is `if not row: raise HTTPException(404, ...)`, which is unreachable for this case because the IntegrityError propagates out of `await repo.update_*` first.

5. Nothing catches it downstream. `grep -rn "IntegrityError|UniqueViolation" app/` returns exactly one hit outside imports: app/features/explorer/service.py:176-179. app/api/errors.py:136-141 `_unhandled_exception_handler` catches bare `Exception` and returns `problem_response(500, "An unexpected error occurred.", ...)`.

6. It is not deliberate — the sibling feature does it correctly. explorer/service.py:176-179:
    try:
        updated = await repo.update_view(dataset_id, view["id"], fields)
    except IntegrityError:
        raise HTTPException(409, f"A view named '{fields.get('name')}' already exists on this dataset")
So the repo's own house style is 409-on-rename-collision; library/repo.py just omits it.

7. No test contradicts it. tests/test_library.py's 409 assertions are at lines 43 and 101 (create-path duplicates) and 157-176 (publish name collision); there is no PATCH-rename-collision test anywhere in tests/.

**Fix**

Wrap the two update calls the way explorer/service.py already does. In app/features/library/api.py:94 and :237, `from sqlalchemy.exc import IntegrityError` and:

    try:
        row = await repo.update_definition(dataset_id, definition_id, fields)
    except IntegrityError:
        raise HTTPException(409, f"A definition named '{fields['name']}' already exists on this dataset")

(and the chart analogue with ChartUpdate's name). Putting it in the API layer keeps repo.py free of HTTP concerns and matches the create path's message wording exactly.

**Test to pin it**

"test_renaming_a_definition_onto_a_sibling_name_is_409_not_500" (plus "test_renaming_a_chart_onto_a_sibling_name_is_409_not_500") in tests/test_library.py — this needs the real Postgres unique index, so it belongs in the integration layer tests/, not tests/unit/.

## [high] Join runs can be published through the generic library publish route, which records only the left parent in lineage and never authorizes the right dataset. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim holds on every link of the path.

1. A join run IS an ordinary analytics run on the LEFT dataset. `app/features/relationships/joins.py:228-253` `_join_definition` creates an `analytics_definitions` row on `relationship["dataset_id"]` (the left side) with `"kind": "join"`, and `joins.py:270-271` `create_run(definition["id"], str(left.version["id"]), ...)` records the run against it. `app/features/library/repo.py:306-323` `get_run` joins `analytics_runs` to `analytics_definitions` and returns `d.dataset_id` — the left dataset — plus `d.kind`.

2. The library publish route has NO kind guard. `app/features/library/api.py:139-151`:
   `ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)` / `run = await repo.get_run(run_id)` / `if not run or run["dataset_id"] != dataset_id: raise HTTPException(404, ...)` / `result = await publish_run(...)`. A join run satisfies `run["dataset_id"] == dataset_id` for the left dataset, so it passes. Nothing anywhere in `app/features/library/` filters `kind == "join"` (grep for "join" in that package returns only string-`.join()` calls, the relation map, and comments).

3. It succeeds and records ONE parent. `app/features/library/service.py:232-247` `publish_run` explicitly anticipates join runs — `{"sample": ..., "join": "joined_from"}.get(run.get("kind"), "published_from")` — and calls `publish_artifact_as_version` with NO `extra_lineage`. `resolve_publishable_artifact` (service.py:213-229) is satisfied: the join run is `completed`, has `artifact_id` (joins.py:326-327) and a live `dataset_version_id` (the left version). Contrast `joins.publish_join` (joins.py:341-366), which builds `extra` with the right dataset/version and passes `extra_lineage=extra`, and whose docstring says "recording BOTH parents in lineage".

4. The right-side permission check really is skipped. `app/features/relationships/api.py:236-261` `publish_join` resolves `relationship_id` from `result_summary` and calls `_authorized_relationship` (api.py:187-202), whose docstring says "Checking only the owning side would make a relationship a side channel for reading ... another team's dataset", then additionally requires WRITE on the target. The library route consults no relationship at all. Permissions are TEAM-scoped (`app/features/auth/deps.py:125-145`), and cross-TEAM relationships are a real, tested configuration — `tests/test_join_builder.py:269-285` `test_reading_a_join_never_leaks_the_other_side` builds left in one team and right in another. So an editor holding only DATASET_WRITE in the left team can POST `/datasets/{left}/analytics/runs/{join_run_id}/publish` and materialize a dataset containing the right team's columns.

5. No test contradicts it. The join RBAC tests (`tests/test_join_builder.py:252-298`) only exercise `/joins/...`; grep of `analytics/runs/.../publish` across tests/ (test_library.py, test_pivot.py, test_timeline.py, test_artifact_retention.py) shows no case that feeds it a join run.

6. Not documented as deliberate — the opposite. `ARCHITECTURE.md:294` "A join writes **two** rows"; `HANDOFF.md:250` "Publish writes two `joined_from` lineage rows"; `app/features/relationships/api.py:183-184` "This is the ONLY place cross-dataset joins are allowed."

**Fix**

In `app/features/library/api.py:139-151` `publish`, after loading the run, reject cross-dataset producers: `if run.get("kind") == "join": raise HTTPException(409, "Publish a join through POST /joins/{run_id}/publish so both parents are recorded")`. (Equivalently, gate in `publish_run` on any kind whose lineage needs extra parents, so the transform path is covered too.)

**Test to pin it**

"test_a_join_run_cannot_be_published_through_the_library_route" in tests/test_join_builder.py (integration layer, tests/), asserting a 409 for the owner and that an editor holding only left-team write cannot obtain the right team's columns.

## [high] View-backed chart render silently caps at 1000 rows and can never report truncated=true, since MAX_CATEGORIES equals that same 1000-row cap. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/library/api.py:288-291 (render_chart, view branch): `result = await run_view(ds, view, RunViewRequest(limit=1000))` then `rows = result.result.items`. The full `ViewRunResponse` is discarded except `.items`.

app/features/explorer/schemas.py:87: `limit: int | None = Field(default=None, ge=1, le=1000)` — 1000 is also the schema maximum, so the handler is pinned at the largest allowed page, one page only. No cursor loop follows; `result.result.next_cursor` is never inspected.

app/features/library/charts.py:24 `MAX_CATEGORIES = 1000` and charts.py:124 `truncated = len(categories) > MAX_CATEGORIES`. Categories are the distinct values of x_field over `rows`, so `len(categories) <= len(rows) <= 1000`. On the view path the condition is structurally unreachable: `truncated` is always False. api.py:300 sets `truncated=data.truncated`, so the response asserts completeness for a row set that was cut.

The full count is computed and thrown away: app/shared/query/compile.py:141-159 computes `total` and returns `QueryPage(items=..., next_cursor=..., total=total)`. render_chart never reads `result.result.total` or `next_cursor`. The response's only count is api.py:299 `row_count=len(rows)` — i.e. the capped 1000, not the real total (tests/test_charts.py:197 asserts `row_count == 4` on a 4-row fixture, consistent with it being the returned-row count).

Not contradicted by tests: tests/unit/test_chart_series.py:163-171 only exercises `build_series` directly with MAX_CATEGORIES+50 synthetic rows — a row set the view path can never produce. tests/test_charts.py:178-197 (`test_rendering_a_view_backed_chart`) uses 4 rows and never touches the cap.

Partially deliberate, but not enough to excuse it: app/features/library/schemas.py:177-178 documents `truncated` as "Category axis was capped for readability", and charts.py:22-24 explains the category cap. Nothing in the code, HANDOFF.md or ARCHITECTURE.md documents or justifies the 1000-row read cap on the view path, and the definition path (compute_definition, service.py:340-375) applies no such cap — so the two sources of the same endpoint behave inconsistently.

**Fix**

In render_chart's view branch, keep the whole ViewRunResponse and surface the real count: set `row_count`/a new `total_rows` from `result.result.total`, and compute `truncated = data.truncated or (result.result.next_cursor is not None) or (result.result.total is not None and result.result.total > len(rows))`. Either that, or page the cursor until MAX_CATEGORIES distinct x-values are collected so the existing category cap becomes the only cap that fires.

**Test to pin it**

"test_rendering_a_view_backed_chart_over_more_than_a_page_reports_truncated" in tests/ (integration — it needs a >1000-row uploaded dataset and a real view, which the pure-unit layer cannot produce); assert `truncated is True` and that the reported total exceeds the rendered row count.

## [high] The library publish route accepts join runs and records only the left parent, so the same run published there yields a lineage graph missing the right-hand source. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Reachability is real, not hypothetical. `joins.execute_join` creates its run against a library definition owned by the LEFT dataset: `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/relationships/joins.py:233` (`dataset_id = relationship["dataset_id"]`) and `joins.py:246` (`"kind": "join"`), then `library_repo.create_run(definition["id"], str(left.version["id"]), ...)` (joins.py:269-270) and `complete_run(..., artifact_id=artifact["id"])` (joins.py:325-326). `library/repo.py:306-323` `get_run` joins to the definition and returns `d.dataset_id` (= left) and `d.kind` (= "join").

The library route therefore matches that run: `app/features/library/api.py:139-151` — `ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)`; `if not run or run["dataset_id"] != dataset_id: 404`. There is NO kind guard, and no read check on the right-hand dataset. Contrast `app/features/relationships/api.py:236-262`, which requires `run.get("kind") == "join"`, pulls `relationship_id` out of `result_summary`, and calls `_authorized_relationship(...)` (api.py:186-198) which does `ensure_dataset_permission(principal, relationship["to_dataset_id"], DATASET_READ)`.

`app/features/library/service.py:232-247` `publish_run` then maps `"join": "joined_from"` and calls `publish_artifact_as_version(...)` with `source_extra={"analytics_run_id": run["id"]}` and NO `extra_lineage` — even though `result_summary` contains `relationship_id` (joins.py:319). The docstring of `publish_artifact_as_version` (service.py:256-262) states plainly: "*extra_lineage* records ADDITIONAL parents (a join has two)". `joins.publish_join` (joins.py:341-366) builds exactly that `extra` list with the right dataset/version/sheet and `relation="joined_from"`.

Precondition checks all pass on the library path: `resolve_publishable_artifact` (service.py:213-229) only needs completed + artifact_id + a surviving `dataset_version_id`, all of which a join run has.

Nothing documents this as deliberate. The migration comment says the opposite — `app/infra/db/postgres/migrations/20260810010000_join_builder.sql:7`: "A join has TWO parents, so publishing writes two `joined_from` lineage rows". HANDOFF.md:250 and WAVE4-6-PLAN.md:214 say the same ("lineage `joined_from` ×2"). The plan line "Add a `join` branch to `library.service.publish_run`'s relation map" (WAVE4-6-PLAN.md:217) explains why the branch exists but does not sanction single-parent join publishing.

No test contradicts it: `tests/test_join_builder.py:215` (`test_publish_records_both_parents`) exercises only `/api/v1/joins/{run_id}/publish`; grep of tests/ for `analytics/runs/{...}/publish` (test_library.py, test_pivot.py, test_timeline.py, test_artifact_retention.py) never uses a join run.

Secondary authz claim is true but narrower than stated: permissions are team-scoped (`app/features/auth/deps.py:125-145`), so the missing right-side READ only matters when the two datasets are in different teams — possible, since `create_relationship` (relationships/api.py:93-97) allows a cross-team `to_dataset_id` as long as the creator can read it. A later principal with WRITE on the left team but no membership in the right team can republish the joined output via the library route.

**Fix**

In `publish_run` (or the library publish route), special-case `run.get("kind") == "join"`: pull `relationship_id` from `run["result_summary"]`, authorize both sides as `_authorized_relationship` does, and delegate to `joins.publish_join` so `extra_lineage` is recorded. Simpler and equally correct: raise 409 for `kind == "join"` from the library route with a message pointing at `POST /joins/{run_id}/publish`, so there is exactly one join publish path.

**Test to pin it**

"test_publishing_a_join_run_through_the_library_route_still_records_both_parents" (integration, tests/test_join_builder.py) — execute a cross-dataset join, POST to /api/v1/datasets/{left}/analytics/runs/{run_id}/publish, and assert the published dataset's lineage has two `joined_from` parents (or that the route rejects with 409); plus "test_library_publish_of_a_join_run_requires_read_on_the_right_dataset" for the cross-team authz half.

## [high] /sample silently drops post-processing sort when sort_by names a nonexistent column, while /aggregate, /pivot and file downloads all 400. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The cited code is exactly as claimed. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/services/sampling.py:473-476:

    # Post-processing: sort
    if request.sort_by and not combined.empty:
        if request.sort_by in combined.columns:
            combined = combined.sort_values(request.sort_by, ascending=not request.sort_descending, ignore_index=True)

There is no `else` — an unknown column falls through with no error, no warning appended to `steps_summary` (contrast the dedup block at :461-471, which does append a warning StepResult), and `success` is still True. The unknown name is even echoed back verbatim into ReproducibilityInfo.post_processing["sort_by"] at :534, so the response asserts a sort that never happened.

Nothing upstream validates it. Schema field is an unconstrained string: schemas.py:604 `sort_by: str | None = Field(default=None, description="Column to sort the final output by")` (same for the coordinated request at :696). The route app/features/data_accelerator/api.py:589-597 only calls `_authorize_source` then `run_sampling_pipeline`. `grep -n columns` over sampling.py shows no column-existence check and no ProblemException/unknown-column anywhere in the file. The coordinated path just forwards it (sampling.py:762).

The contrast is real and deliberate elsewhere — aggregation.py:214-222:

    if sort_by:
        # An unknown sort_by used to drop the ORDER BY silently, so results came
        # back in arbitrary order while the response still said success.
        valid_sort = sorted({name for name, _ in group_entries} | set(agg_aliases))
        if sort_by not in valid_sort:
            raise ProblemException(400, ..., code="unknown-column", ...)

and the same choice is made in two more siblings: pivot.py:232-235 raises 400 for a sort_by that isn't a row dimension, and files/services/downloads.py:366-369 raises `HTTPException(400, f"Sort column not found: {sort_by}")`. So sampling is the lone outlier, not a house style.

No test contradicts the claim: every `sort_by` hit under tests/ is aggregate/pivot/library/files (tests/test_aggregation_extras.py:361 `test_invalid_sort_by_is_rejected_naming_the_valid_options`, tests/unit/test_aggregation_assembly.py:211). There is no sampling sort_by test at all — the behaviour is untested, not asserted. No docstring, comment, HANDOFF.md or ARCHITECTURE.md text defends lenient sorting on /sample; HANDOFF.md:187 lists the silent-wrong-answer fix pass (`sort_order` lowercasing on `aggregate`) and sampling was simply missed.

**Fix**

In `_run_sampling_pipeline_inner` (sampling.py:473), replace the silent `if request.sort_by in combined.columns:` guard with a validation that raises the same typed error the siblings use:

    if request.sort_by and not combined.empty:
        if request.sort_by not in combined.columns:
            available = sorted(map(str, combined.columns))
            raise ProblemException(
                400,
                f"sort_by column not found: {request.sort_by}. Valid options: {available}",
                code="unknown-column", columns=[request.sort_by], available=available)
        combined = combined.sort_values(...)

Validate before the pipeline runs if an early 400 is preferred (the source columns are known once the pool view exists), and apply it on the coordinated path too since it forwards sort_by at sampling.py:762. Note the empty-result case: `combined.empty` currently short-circuits validation, so a typo on a zero-row sample would still pass — check sort_by against the source columns rather than gating on emptiness.

**Test to pin it**

"test_sample_rejects_unknown_sort_by_instead_of_silently_returning_pipeline_order" — integration level, tests/test_advanced.py (alongside the existing /sample coverage), POSTing a sample with sort_by="ghost" and asserting 400 with code="unknown-column"; plus a unit companion in tests/unit/ pinning that a valid sort_by still orders the frame and that the empty-result case is validated too.

## [high] Version-targeting keys left in a definition's stored params override the selector, so analytics_runs records the wrong version. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and the merge really is params-first/base-second with a pin that can be empty.

app/features/library/service.py:101-108 — `_selector_pin` returns `{"tag": ...}` for mode=tag, `{"version_number": ...}` for mode=version, and `{}` otherwise (mode=current, the default from `VersionSelector.mode: Literal[...] = "current"`, schemas.py:15).

app/features/library/service.py:114-117,123:
  pin = _selector_pin(definition.get("version_selector"))
  ver = await resolve_version(dataset_id, **pin)
  base = {"dataset_id": dataset_id, "sheet": definition.get("sheet"), **pin}
  request = build_definition_request(kind, params, base)
and service.py:81 `return model(**{**params, **base})` — base only overrides the keys it contains. With mode=current, base has no version_id/version_number/tag at all, so any of those left in params survive verbatim.

They are real, declared fields on all four request models, not extras that pydantic would drop: schemas.py:590-592 (SampleRequest), 683-685 (AggregateRequest), 798-800 (ProfileRequest), 994-996 (PivotRequest) each declare `version_id`, `version_number`, `tag`.

The underlying operations resolve from the request, not from `ver`: sampling.py:743-745, aggregation.py:324/334-335, pivot.py:140-141, profiling.py:199 all pass `request.version_id, request.version_number, request.tag` into `resolve_version`.

Meanwhile the durable record uses the selector-derived `ver`: service.py:126 `dataset_version_id=str(ver["id"])` on the job and service.py:131 `repo.create_run(definition["id"], str(ver["id"]), ...)`. Downstream, publish/lineage reads that same field: service.py:227 `parent_ver = await get_version(run["dataset_version_id"])`, then publish_run passes `parent_ver` into `publish_artifact_as_version` (service.py:243-245). So the run row, the published parent version and its lineage all name the selector's version while the numbers came from the params' version.

Nothing sanitizes params anywhere on the write path: schemas.py:34-37 is `params: dict[str, Any]` (its description says "minus dataset/version/sheet", i.e. the intent, but it is only prose), api.py:44-53 passes `body.model_dump()` straight through, and repo.py:18-37 `json.dumps(fields.get("params") or {})` stores it raw. `DefinitionUpdate.params` (schemas.py:45) is equally free-form.

It is worse than the claim in one respect: precedence in `app/shared/datasets.py:56-78` is version_id > version_number > tag, so even mode=tag or mode=version can be defeated — a stored `params.version_id` beats a base `tag`/`version_number`.

The claim's own caveat is also correct: `resolve_version` 404s when `version_id`'s dataset_id differs (datasets.py:64-67), and `dataset_id` is always in base, so this is misattribution within one dataset, not a cross-tenant leak.

No test contradicts it. tests/test_library.py:183-203 (test_retarget_definition_to_pinned_version) only exercises a clean definition with no version keys in params; grep of tests/ found no case asserting that stored version keys are ignored or stripped. HANDOFF.md:496,833 describe version_selector/params but document no deliberate override behaviour.

**Fix**

Make the pin total rather than partial so base always wins on all three keys: have `_selector_pin` return `{"version_id": None, "version_number": None, "tag": None}` and then set only the selected one (tag / version_number). Both `execute_definition` (service.py:117) and `compute_definition` (service.py:350) build `base` from it, so both paths are fixed at once. Optionally also reject/strip `version_id`/`version_number`/`tag`/`dataset_id`/`file_path` in DefinitionCreate/DefinitionUpdate.params so bad definitions cannot be saved in the first place.

**Test to pin it**

"test_stored_params_cannot_override_the_definition_version_selector" in tests/test_library.py (integration layer): upload, publish a 2nd version so current is v2, save a definition with version_selector mode=current and params containing version_number=1, run it, and assert both that the result reflects v2 (not v1) and that the run's dataset_version_id / published parent version is v2. A cheaper unit companion in tests/unit/ can assert `build_definition_request("aggregate", {"version_number": 1, ...}, base).version_number is None` for a mode=current pin.

## [high] A row whose first key column is NULL is reported as both added and removed in the samples and diff artifact while the counts say it matched. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every cited line. `join_condition` (app/features/data_accelerator/services/rowdiff.py:64-66) emits `l.k IS NOT DISTINCT FROM r.k`, so a NULL key genuinely matches, and `counts_sql` decides presence with the sentinels: `COUNT(*) FILTER (WHERE _l IS NULL AND _r IS NOT NULL)` over `(SELECT *, 1 AS _l ...) l FULL OUTER JOIN (SELECT *, 1 AS _r ...)` (rowdiff.py:92-99), with the comment at :87-88 stating that rule as the invariant. But `sample_rows_sql` (rowdiff.py:161-162) uses `LEFT JOIN ... WHERE o.{key_columns[0]} IS NULL`, and `cell_changes_sql` does the same at :143 (`WHERE l.{key_columns[0]} IS NULL`) and :150 (`WHERE r.{key_columns[0]} IS NULL`). When the key value itself is NULL, the join succeeds yet the matched side's key column is still NULL, so the predicate is true and the row is emitted as one-sided.

Executed against in-memory DuckDB using the module's own builders (a = (NULL,'x',1.0),(2,'y',2.0); b = (NULL,'x',9.0),(2,'y',2.0); key=['id']):
  counts_sql -> (added=0, removed=0, changed=1, unchanged=1)
  duplicate_keys_sql('a',['id']) -> 0   (so the 409 ambiguous-diff-key guard at rowdiff.py:252-259 does not fire)
  sample_rows_sql('b','a') -> [(None,'x',9.0)]      # rendered as "added"
  sample_rows_sql('a','b') -> [(None,'x',1.0)]      # rendered as "removed"
  cell_changes_sql -> ('changed','', 'amount','1.0','9.0'), plus ('added','','amount',None,'9.0'), ('added','','name',None,'x'), ('removed','','amount','1.0',None), ('removed','','name','x',None) — the same row_key ('' from key_expr's COALESCE) in all three buckets.
These feed run_row_diff's `added_sample`/`removed_sample`/`changed_sample` and the persisted diff_output parquet (rowdiff.py:268-279), so both the API response and the downloadable artifact contradict the counts.

Not by design: the module docstring says "every row falls into exactly one bucket" (rowdiff.py:8) and tests/unit/test_row_diff_sql.py:92-97 `test_a_null_key_still_matches_itself` asserts (added, removed, changed) == (0,0,1) with the docstring "A plain `=` would drop it and report the row as both added and removed" — which is exactly what the sample/cell builders do. That test only exercises counts_sql; test_added_and_removed_samples_return_whole_rows (:154) and test_cell_changes_cover_all_three_buckets_uniformly (:128) use non-NULL keys, so nothing contradicts the finding. Nothing upstream forbids NULL key values: keys come from explicit request `key` or the declared `primary_key_columns` metadata (rowdiff.py:345-360, app/features/discovery/api.py:73), which is free-text column names with no NOT NULL check.

**Fix**

Give the added/removed builders the same sentinel presence test the counts use. In `cell_changes_sql`, replace `FROM {right} r LEFT JOIN {left} l ON {on} WHERE l.{key0} IS NULL` with a join against a sentinel-bearing subquery — `LEFT JOIN (SELECT *, 1 AS _l FROM {left}) l ON {on} WHERE l._l IS NULL` — and symmetrically for removed; in `sample_rows_sql`, `LEFT JOIN (SELECT *, 1 AS _o FROM {other}) o ON {on} WHERE o._o IS NULL`. (Equivalently `WHERE NOT EXISTS (SELECT 1 FROM other o WHERE {on})`.) Note sample_rows_sql selects `s.*`, so the sentinel on the other side does not leak into the output.

**Test to pin it**

"test_a_null_key_row_is_not_sampled_as_both_added_and_removed" (and a companion "test_cell_changes_put_a_null_key_row_in_exactly_one_bucket") in tests/unit/test_row_diff_sql.py, next to the existing test_a_null_key_still_matches_itself.

## [high] Sensitive-column masking is only applied on explorer/row-diff/download; /sample, /profile, /pivot, /aggregate and the dataset & sheet detail previews return raw values.

- **domain:** ?
- **where:** 

**Evidence**

Masking has exactly three call sites in the whole app. `grep -rn resolve_masking app/` returns only app/features/explorer/service.py:90,101 and :218,220 and app/features/data_accelerator/services/rowdiff.py:298-304; `ensure_raw_access` only at app/features/files/api.py:668,686. Nothing in app/features/data_accelerator/api.py imports app.shared.masking.

The five analytics handlers (app/features/data_accelerator/api.py:589-641) do authorization only and return the service result verbatim, e.g. `profile_data`: "await _authorize_source(principal, request); return await run_profiling(request)" (api.py:614-618). `_authorize_source` (api.py:97-111) checks Permission.DATASET_READ and nothing else — it never consults `may_see_raw`/`ensure_raw_access`, so an ordinary team member passes.

The payloads really are raw rows: services/sampling.py:513-522 builds `preview` from `SELECT * FROM {output} LIMIT 5` and, when `request.return_data`, `sampled_data` from `SELECT * FROM {output}`; services/profiling.py:83-97 builds `top_values` from `SELECT {qcol}, COUNT(*) ... GROUP BY {qcol}` — literal cell values of the sensitive column. PivotResponse.data / AggregateResponse.data (schemas.py:1031, :945) carry group-dimension values likewise.

Dataset/sheet detail: api.py:211-215 `get_dataset` -> services/datasets.py:70-91 `get_dataset_metadata` returns `preview=meta["preview"]`, and api.py:578-583 `get_sheet` -> services/datasets.py:106-123 `get_sheet_metadata` returns `_sheet_response(row, preview=meta["preview"])`. `extract_metadata` (app/shared/data_io.py:532-547) is `SELECT * FROM df LIMIT 5` with no masking hook.

No existing test contradicts it: tests/test_pii_masking.py covers only preview (explorer), structured query, saved view, row diff, download gating and the admin/editor cases — nothing for /sample, /profile, /pivot, /aggregate or GET /datasets/{id}.

It is not documented as deliberate. The opposite: app/shared/masking.py:1-11 says "a caller without the elevated permission sees masked values wherever raw dataset rows are returned", HANDOFF.md:143-147 says "Now enforced wherever raw rows are returned", and tests/test_pii_masking.py:1-6 repeats it. The only documented exemption is MCP `run_sql` (app/features/mcp/tools/orient.py:246 "run_sql is never masked"), which does not cover these routes. ARCHITECTURE.md:576 listing the seam's users as "explorer, downloads, row diff" is an as-built inventory, not a rationale.

One nuance that does NOT rescue the code: masking only ever engages for datasets that declare a sensitive column, so the exposure is scoped to exactly those datasets — i.e. exactly the ones the control exists for.

**Fix**

Two options, smallest first. (1) For the dataset-scoped read paths, resolve the sheet row and mask before returning: in services/datasets.py `get_dataset_metadata`/`get_sheet_metadata` take `principal`, call `resolve_masking(dataset_id, sheet_row, principal)` and `mask_rows(preview, masked)`, and surface `masked_columns` on the response models as the explorer already does (explorer/service.py:100-104). Same for sampling's `preview`/`sampled_data` and profiling's `top_values`, threading `principal` + the resolved sheet row into `run_sampling_pipeline`/`run_profiling`/`run_pivot`/`run_aggregation`. (2) If per-column masking of aggregate output is not wanted, at minimum call `await ensure_raw_access(principal, request.dataset_id)` inside `_authorize_source` (api.py:109-110) whenever the source is a dataset, mirroring the download gate — a 403 with `code="sensitive-data-restricted"` is defensible where masking is impractical, and it closes the bypass. Either way the artifact fetch `GET /api/v1/samples/{filename}` for sample/pivot/aggregate outputs needs the same gate, or the masked response is re-leaked through the saved parquet.

**Test to pin it**

"test_sampling_and_profiling_mask_a_sensitive_column_for_a_plain_member" plus "test_the_dataset_detail_preview_is_masked" — integration layer, tests/test_pii_masking.py (they need a real dataset, a dictionary entry with sensitivity, and a member principal without dataset:read_sensitive, exactly like the existing fixtures there).

## [high] SamplingStep.time_bins / num_clusters and ProfileRequest.top_n have no lower-bound validation, so 0/negative values reach divisors, random.sample and SQL LIMIT and surface as 500.

- **domain:** ?
- **where:** 

**Evidence**

Schemas (absolute path /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/schemas.py):
- :514-515 siblings ARE bounded: `sample_size: int | None = Field(default=None, gt=0, ...)`, `sample_fraction: float | None = Field(default=None, gt=0, ...)`; :595 `target_total_volume: int = Field(..., gt=0, ...)`.
- :529 `num_clusters: int | None = Field(default=None, description="Number of clusters to select")` — no ge/gt.
- :534 `time_bins: int = Field(default=10, description="Number of equal time bins to stratify across")` — no gt.
- :807 `top_n: int = Field(default=10, description="Number of top values to return per column")` — no ge. No `model_validator`/`field_validator` exists anywhere in SamplingStep/SampleRequest/ProfileRequest (grep for field_validator/model_validator in the sampling path returned nothing).

Consumption path is unguarded:
- api.py:590 `sample_data` -> services/sampling.py:279 passes `step.time_bins` straight to `sample_time_stratified`; methods.py:231 `per_bin = max(1, target // time_bins)` (ZeroDivisionError at time_bins=0) and methods.py:238 `NTILE({time_bins})` (negative reaches here since max(1, ...) masks per_bin only).
- sampling.py:264 passes `step.num_clusters` to methods.py:177-183: `if num_clusters is None or num_clusters >= len(all_clusters): ... else: selected = rng.sample(all_clusters, num_clusters)` — a negative fails the `>=` branch and hits `random.sample(list, -1)`.
- api.py:615 `profile_data` -> profiling.py:217 passes `request.top_n` -> profiling.py:84-88 `... ORDER BY cnt DESC LIMIT ?", [top_n]`, not inside any try (the only `except Exception` in that file are at :174 and :236, both after/elsewhere).

Verified the three failure modes against the repo's own venv (no pytest, no DB): `LIMIT ?` with -1 -> duckdb BinderException "LIMIT/OFFSET cannot be negative"; `NTILE(0)` -> InvalidInputException "Argument for ntile must be greater than zero"; `random.sample([1,2,3], -1)` -> ValueError "Sample larger than population or is negative".

None of these are HTTPException, so app/api/errors.py:136 `_unhandled_exception_handler` renders a 500 problem+json with detail "An unexpected error occurred." (internals hidden unless settings.debug), i.e. no field-level information — exactly what the claim describes.

No test contradicts it: grep of tests/ for time_bins/num_clusters found nothing; the only `top_n` hits (tests/unit/test_pagination_and_filters.py:130, tests/unit/test_query_dsl.py:340) are the unrelated `top_n` filter operator. No docstring/comment/HANDOFF.md/ARCHITECTURE.md text defends the omission.

**Fix**

Add bounds in schemas.py: `num_clusters: int | None = Field(default=None, gt=0, ...)` (:529), `time_bins: int = Field(default=10, gt=0, ...)` (:534), `top_n: int = Field(default=10, ge=0, ...)` (:807). That makes them 422 like their already-bounded siblings, with no service-layer change. (Note num_clusters=0 also currently yields an empty `IN ()` SQL fragment at methods.py:184-186, which gt=0 fixes too.)

**Test to pin it**

"test_sampling_and_profiling_reject_non_positive_bin_cluster_and_top_n_counts" — an API-level test in tests/ posting {"method":"time_stratified","time_bins":0}, {"method":"cluster","num_clusters":-1} to /api/v1/sample and {"top_n":-1} to /api/v1/profile, asserting 422 with the offending field named in the problem+json `errors`, plus a tests/unit/ case pinning ProfileRequest/SamplingStep raising ValidationError for those values.

## [high] Four post-creation TUS routes (HEAD/PATCH/DELETE/status) take no principal and skip ensure_dataset_permission, so any authenticated user with an upload_id can write to, cancel, or read another team's upload.

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every cited line.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/api.py:452-453 `@router.head("/tus/{upload_id}")` / `async def tus_head(upload_id: str) -> Response:` — no `principal` param, body only does `load_tus_meta(upload_id)` then returns offset/length.
api.py:478-483 `async def tus_patch(upload_id: str, request: Request, background_tasks: BackgroundTasks)` — no principal; it loads meta, checks Content-Type/offset/lock/checksum only, appends `request.stream()` bytes to `meta["file_path"]`, and on completion schedules `process_uploaded_file_async(dataset_id, ...)` using the team pulled from the *stored* meta (`ds["team_id"]`), never from the caller.
api.py:574-575 `async def tus_terminate(upload_id: str)` — no principal; calls `repo.fail_version(version_id, "Upload cancelled by client")` and `delete_tus_upload(...)`.
api.py:590-591 `async def tus_upload_status(upload_id: str) -> UploadResponse` — no principal; returns `dataset_id`, `version_id`, and, once processing finished, `preview=ps.get("preview")` and column info.

No route-level or router-level authorization backstops it: app/main.py:148 `protected = APIRouter(dependencies=[Depends(get_principal)])` with the comment "The data plane sits behind a blanket authentication guard; individual routes then enforce team-scoped RBAC" — authentication only. files_router is included there (main.py:153) and declares `router = APIRouter()` (api.py:107) with no dependencies.

The contrast the claim asserts also holds. TUS *creation* does authorize (api.py:407-415: `await ensure_dataset_permission(principal, incoming_dataset_id, Permission.DATASET_WRITE)` / `principal.can(team_id, Permission.DATASET_WRITE)`), and the analogous non-TUS poller does too (api.py:319-327: `upload_status(version_id, principal=Depends(get_principal))` → `ensure_dataset_permission(..., Permission.DATASET_READ)`). So the omission is specific to the four post-creation TUS routes.

Not deliberate/documented: grep of HANDOFF.md, ARCHITECTURE.md and docs/ for TUS turns up only staging-location notes (ARCHITECTURE.md:319 "TUS staging is always local disk"; HANDOFF.md:789) — nothing claiming the upload_id is meant to be a bearer capability. No test contradicts it either: tests/test_uploads_tus.py has only 5 tests (chunked upload, offset/content-type guards, checksum retry, terminate, unknown id) and none exercises a second principal or cross-team access.

Only mitigation: `upload_id = uuid.uuid4().hex` (api.py:427), so the id is unguessable — an attacker must obtain it (leaked Location URL, logs, proxy), which is exactly the capability-token exposure the claim describes. That lowers it from critical to high, but it is not a design decision recorded anywhere.

**Fix**

Add `principal: Principal = Depends(get_principal)` to tus_head, tus_patch, tus_terminate and tus_upload_status, and immediately after `meta = load_tus_meta(upload_id)` (and the 404 guard) call `await ensure_dataset_permission(principal, meta["dataset_id"], Permission.DATASET_WRITE)` for PATCH/DELETE and `Permission.DATASET_READ` for HEAD/status. ensure_dataset_permission already raises 404 for cross-tenant, preserving the repo's documented existence-hiding property (so an unauthorized upload_id looks identical to an unknown one).

**Test to pin it**

"test_tus_followup_routes_reject_a_principal_from_another_team" in tests/ (integration layer, alongside tests/test_uploads_tus.py): create a TUS upload as team A's admin, then as a team B principal issue HEAD, PATCH, DELETE and GET /tus/{id}/status with the same upload_id and assert each returns 404 and that team A's version row is still status "uploading" with unchanged offset.

## [high] DELETE /api/v1/tus/{upload_id} unconditionally marks the version failed, so terminating after a completed upload demotes a ready version. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and I could not find a guard anywhere on the path.

app/features/files/api.py:574-587 (tus_terminate) — the only precondition is that the meta file exists:
  meta = load_tus_meta(upload_id); if meta is None: raise HTTPException(404, ...)
  version_id = meta.get("version_id")
  if version_id: await repo.fail_version(version_id, "Upload cancelled by client")
No check of meta["offset"] vs meta["total_size"], no check of processing_status[version_id], no check of the version's current DB status.

app/features/files/repo.py:238-245 (fail_version) — bare, unguarded update:
  "UPDATE dataset_versions SET status = 'failed', error = :error, processed_at = now() WHERE id = :id"
Contrast with the same-class fix in b69bfc0, which added `AND status = 'running'` to fail_run in analytics_runs, validation_runs, transformation_runs and the job table ("The ``status = 'running'`` guard makes this 'fail it if it has not already finished'"). dataset_versions.fail_version was not given the equivalent guard.

The upload id really does stay valid after completion: tus_patch's completion branch (api.py:547-566) calls save_tus_meta again and schedules process_uploaded_file_async; it never calls delete_tus_upload. grep for delete_tus_upload across app/ finds only api.py:586 (terminate) and services/tus.py:109 (cleanup_stale_uploads, expiry-based). services/processing.py never removes the meta file. So HEAD/DELETE on the upload id keep working after the version has been marked ready by repo.py:100-145.

No test contradicts this. tests/test_uploads_tus.py:114 test_tus_terminate_cancels_and_fails_version only terminates mid-upload (it PATCHes CSV[:10], asserts status == "uploading", then DELETEs) — it asserts the correct behaviour for the cancel case and says nothing about terminate-after-ready.

No docstring/comment/HANDOFF.md/ARCHITECTURE.md text defends this; grep -i terminate over the docs returns nothing. The terminate docstring says "client cancels an in-progress upload", which is precisely the case the code fails to enforce.

One correction to the claim's impact wording: fail_version does not clear datasets.current_version_id, so the dataset still points at the demoted version; it is the status='ready' filters (e.g. repo.py:160-165, and the ready-guarded reads elsewhere) that make the data stop resolving. The user-visible effect — a successfully uploaded version showing "failed" with no undo path, bytes still in storage — is as described.

**Fix**

Add a status guard to repo.fail_version, mirroring b69bfc0: `UPDATE dataset_versions SET status='failed', error=:error, processed_at=now() WHERE id=:id AND status IN ('pending','uploading','processing')` (i.e. not 'ready'), and have it return whether a row was updated. Optionally also make tus_terminate short-circuit when meta["offset"] >= meta["total_size"] (delete the upload state, skip fail_version) so a post-completion cleanup DELETE is a no-op rather than a demotion.

**Test to pin it**

"test_tus_terminate_after_completion_does_not_demote_ready_version" — integration layer, tests/test_uploads_tus.py: PATCH the whole CSV, poll until the version is ready, then DELETE the upload location and assert the version is still ready and still the dataset's current version.

## [high] POST /sample interpolates distribution_goals.column into SQL without an existence check, so an unknown column yields an unhandled DuckDB BinderException → 500.

- **domain:** ?
- **where:** 

**Evidence**

The mechanism is exactly as claimed.

- Schema: app/features/data_accelerator/schemas.py:495-504 — `DistributionGoals.column: str = Field(...)` with no validator; no model_validator on SampleRequest (schemas.py:585-605). Pydantic cannot reject an unknown column, so nothing upstream stops it.
- Route: app/features/data_accelerator/api.py:589-597 — `sample_data` calls `run_sampling_pipeline` with no try/except.
- Pipeline: services/sampling.py:322-341 — `run_sampling_pipeline` has only `try/finally: conn.close()`; no `except duckdb.Error`. Line 490 calls `validate_goals(conn, output, request.target_total_volume, request.distribution_goals)` with no prior column check anywhere in `_run_sampling_pipeline_inner` (344-492).
- Sink: services/sampling.py:115 `qcol = quote_ident(distribution_goals.column)` then :121 / :136 `f"SELECT COUNT(*) FROM {sampled_table} WHERE CAST({qcol} AS VARCHAR) = ?"`. `quote_ident` (app/shared/utils/sql.py:13-15) only escapes quotes — it is injection-safe but does not validate existence, so DuckDB raises a Binder Error.
- No rescue: grep for `duckdb.Error` shows handlers in aggregation.py:300,399, pivot.py:87, shared/duck.py:112 — and none in sampling.py. The exception falls to `_unhandled_exception_handler` (app/api/errors.py:136-141), which returns problem+json 500 with "An unexpected error occurred." — no `code`, no `available`.
- No contradicting test: `grep -rn "distribution_goals" tests/` returns 0 hits; tests/test_analytics_errors.py only covers aggregate unknown columns (:20), not sampling.
- No doc justifying it: ARCHITECTURE.md mentions /sample only for artifact/kind plumbing, nothing about error shape.

One part of the claim is overstated and should not be carried forward as written: "Every other user-supplied column on this endpoint family is checked against `available` first." That is true of *other* endpoints (aggregation.py:359-368, pivot.py:154-165, profiling.py:209-210, rowdiff.py:227-239) but NOT of /sample itself. On this same endpoint, `stratify_column` (sampling.py:238-241 → methods.py:79), `cluster_column` (:262-264), `weight_column` (:267-270), `time_column` (:275-278) and the per-step filter columns (sampling.py:185 → shared/filters.py:156-199) are all interpolated with the same unchecked `quote_ident` and 500 the same way; `sort_by` is instead silently ignored (sampling.py:474-476). So `distribution_goals.column` is not a special omission — the whole sampling endpoint lacks the `unknown-column` 400 contract.

**Fix**

In `_run_sampling_pipeline_inner`, resolve `available = {r[0] for r in conn.execute(f"DESCRIBE {source}").fetchall()}` once (mirroring aggregation.py:359) and raise the shared problem+json 400 (`code="unknown-column"`, `columns=[...]`, `available=sorted(available)`) for `request.distribution_goals.column` before line 364 — and, for consistency, for each step's `stratify_column`/`cluster_column`/`weight_column`/`time_column`. A narrower fix is to pass `available` into `validate_goals` and check there, but that leaves the sibling step columns still 500ing.

**Test to pin it**

"test_sample_with_unknown_distribution_goal_column_returns_400_with_available" — integration layer, tests/test_analytics_errors.py (it needs the real POST /sample route plus a dataset, alongside test_aggregate_unknown_columns_and_functions).

## [high] Sync upload maps only InvalidFileError to 400; an include_sheets typo or scanner rejection returns 500 internal_server_error.

- **domain:** ?
- **where:** 

**Evidence**

app/features/files/api.py:202-207 — `if info["status"] == "error": if info.get("error_kind") == "invalid-file": raise ProblemException(400, ..., code="invalid-file")` then unconditionally `raise HTTPException(500, f"Processing failed: {info['error']}")`. error_kind is set at app/features/files/services/processing.py:292-296: `error_kind="invalid-file" if isinstance(e, InvalidFileError) else "processing-error"`, so only InvalidFileError (app/shared/data_io.py:158, raised only at data_io.py:412 and :421) gets the 400.

Both cited user errors are plain ValueErrors, not InvalidFileError:
- app/shared/data_io.py:451-453 `if include_sheets is not None and not sheet_frames: raise ValueError(f"include_sheets matched no non-empty sheets. Workbook has: {sheet_names}")` — inside convert_to_parquet, no outer wrapper converts it (grep shows InvalidFileError only constructed at 412/421).
- app/features/files/services/processing.py:147-148 `if not scan.ok: raise ValueError(f"Upload rejected by scanner: ...")`.

Both land in the generic `except Exception` at processing.py:290, get error_kind="processing-error", and therefore hit the 500 branch. app/api/errors.py:104 + :54-55 + the 500 title entry mean the problem+json body carries `code: "internal_server_error"` — nothing machine-readable, exactly as the claim says.

Not by design: ROADMAP.md §4 and HANDOFF.md:430-432 state the deliberate contract is that user-caused upload failures are problem+json 400s ("Corrupt uploads are 400s ... The old 500 contract test was updated") — these two paths were simply missed. The sibling sheet-replace path already does it right: app/features/files/services/replace.py:70 `raise HTTPException(400, f"Upload rejected by scanner: {scan.reason or 'infected'}")`, pinned by tests/unit/test_sheet_replace_error_passthrough.py:41. No test asserts a 500 for these paths; the only include_sheets test (tests/test_advanced.py:28) uses valid sheet names.

**Fix**

Give the two user errors a typed kind. Simplest: raise InvalidFileError (or a new `SheetSelectionError`/`RejectedUploadError` subclass of ValueError) at data_io.py:452 and processing.py:148, and in processing.py:294 map them to error_kind values ("invalid-file", "sheet-not-found", "upload-rejected"); in api.py:203-207 raise `ProblemException(400, ..., code=info["error_kind"])` for any non-"processing-error" kind, ideally attaching the valid sheet names as an extra field (like the existing `sheet-selection-required` problem) instead of only in the prose message.

**Test to pin it**

"test_sync_upload_with_misspelled_include_sheets_returns_400_invalid_sheet_not_500" — integration layer, tests/ (needs a real multipart xlsx upload through the sync path), plus a unit test in tests/unit/ pinning that processing.py sets a non-"processing-error" error_kind for scanner rejections.

## [high] TUS Upload-Length/Upload-Offset are int()-parsed unvalidated: non-numeric yields 500 and a negative length yields a "ready" version over truncated data. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Both cited lines parse raw headers with no validation anywhere in the path — TUS handlers take `request: Request` and read headers directly, so no pydantic model ever sees them.

app/features/files/api.py:383-386 (tus_create):
```
    upload_length = request.headers.get("Upload-Length")
    if upload_length is None:
        raise HTTPException(400, "Upload-Length header is required")
    total_size = int(upload_length)
```
The only checks are presence and `if total_size > TUS_MAX_SIZE` (line 388) — no `int()` guard, no lower bound.

app/features/files/api.py:493 (tus_patch): `client_offset = int(request.headers.get("Upload-Offset", "-1"))` — same, unguarded.

A ValueError from either escapes to the catch-all handler at app/api/errors.py:136-140 (`app.add_exception_handler(Exception, _unhandled_exception_handler)` at line 147), which renders 500 "An unexpected error occurred." So `Upload-Length: abc` -> 500, not 400. Confirmed.

Negative length: `-5 > TUS_MAX_SIZE` is False; app/features/files/services/tus.py:82-95 `check_disk_space` only tests `if free_bytes < required_bytes * 2:` — `free_bytes < -10` is False, so it passes. The version row is then created with `size_bytes=total_size` (negative) and meta `total_size=-5, offset=0`. On the first PATCH (api.py:531-556) `meta["offset"] += bytes_received`, then `if meta["offset"] >= meta["total_size"]:` is trivially true (even 0 >= -5), so processing_status is set to "uploaded" and `background_tasks.add_task(process_uploaded_file_async, ...)` runs on whatever partial bytes arrived — the version becomes "ready" containing truncated data.

No test contradicts this: tests/test_uploads_tus.py:142-149 only asserts 400 for a *missing* Upload-Length and a bad extension; no test sends a non-numeric or negative value. No comment, docstring, HANDOFF.md or ARCHITECTURE.md note documents lenient header parsing as deliberate (ARCHITECTURE.md:319 and the deferred-work table only mention TUS staging being local-disk-only).

**Fix**

Add a small helper in tus_create/tus_patch: parse with try/except ValueError -> HTTPException(400, "Upload-Length must be a non-negative integer"), and reject `total_size < 0` (and `client_offset < 0`) explicitly. Optionally require `total_size > 0` for creation, since a zero-length dataset upload is not useful.

**Test to pin it**

"test_tus_create_rejects_non_numeric_and_negative_upload_length_with_400" plus "test_tus_patch_rejects_non_numeric_upload_offset_with_400", in tests/test_uploads_tus.py (integration layer, tests/), driving the real routes through the client fixture.

## [high] GET /upload/status/{version_id} skips the permission check when the version row is gone and serves cached preview rows of the deleted dataset to any authenticated caller. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The cited code is exactly as claimed and the premise holds end to end.

app/features/files/api.py:325-328 — `ver = await repo.get_version(version_id)` / `if ver: await ensure_dataset_permission(...)` / `if version_id not in processing_status:`. The permission check is conditional on the row existing; the cache branch (api.py:342-355) is reached with no authorization at all and returns `dataset_id`, `file_path`, `columns` and `preview`.

app/shared/repo.py:46-55 — `get_version` is a plain `SELECT * FROM dataset_versions WHERE id = :id`; it returns None once the row is gone (and also returns None for a non-UUID string, in which case the handler still falls through to the cache branch if the key somehow matches — moot, but confirms no other guard).

Deletion is a HARD delete, not a soft delete, so the row really does vanish: app/features/data_accelerator/repo.py:472-479 — `DELETE FROM dataset_versions WHERE dataset_id = :did` then `DELETE FROM datasets WHERE id = :did`, reached from DELETE /datasets/{id} (app/features/data_accelerator/api.py:238-243). Nothing in that path touches `processing_status`.

The cached payload really contains data rows: processing.py:233-239 does `processing_status[status_key].update(status="complete", file_path=..., sheets=..., **meta)` where `meta` comes from `extract_metadata` (app/shared/data_io.py:532-546), which returns `"preview"` = the first 5 rows plus the full column list.

Unboundedness is also confirmed: `grep -rn processing_status` finds writes at api.py:191, api.py:550 and processing.py:142/233/292 and only one deletion anywhere in the repo — `processing_status.pop(vid, None)` in tests/test_ops.py:16 (a test simulating a restart). No TTL, no LRU, no eviction on job completion or dataset delete.

No test contradicts this: the only /upload/status tests are tests/test_ops.py:8-22 (restart fallback, happy path) and tests/test_e2e_journeys.py:203; none covers a deleted version. HANDOFF.md:518 documents only the *DB fallback* ("answers from the DB when the in-memory cache is gone") as the deliberate design — the inverse case (cache present, row gone) is undocumented and clearly unintended, since the whole point of lines 325-327 is to authorize the caller.

Caveat on exploitability: `get_principal` is required, so the caller must be authenticated, must know the version UUID, and the leak only lasts the process lifetime after the dataset is deleted. That narrows the window but does not refute the claim.

**Fix**

Invert the guard in app/features/files/api.py:325-328: if `ver` is None, raise 404 immediately (the version is the only thing that can authorize the cache read) — i.e. `ver = await repo.get_version(version_id); if not ver: raise HTTPException(404, f"Unknown version: {version_id}"); await ensure_dataset_permission(principal, str(ver["dataset_id"]), Permission.DATASET_READ)` before any `processing_status` access. Separately, drop the cache entry in `delete_dataset_with_files` (and bound the dict — evict on job completion or cap it with an LRU/TTL) so it cannot grow for the process lifetime.

**Test to pin it**

"test_upload_status_404s_for_a_deleted_version_even_when_the_cache_is_warm" — integration layer, tests/ (needs the real DELETE /datasets/{id} path plus a second team's principal to assert a cross-tenant caller gets 404, not preview rows); belongs next to tests/test_ops.py::test_upload_status_survives_restart.

## [high] The inline-JSON upload branch has no try/except, so any parse/storage/DuckDB failure strands the version in `uploading` and orphans the just-created dataset.

- **domain:** ?
- **where:** 

**Evidence**

The cited code is exactly as claimed. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/api.py:234-312 is a bare `elif data is not None:` branch: after the only guards (`_json.loads` in a try at :236-239, and `if not isinstance(rows, list) or not rows` at :240-241), it does `repo.create_dataset(...)` (:250, unguarded — each repo call opens and commits its own session, `async with async_session_factory()` at repo.py:38, so it is already durable), then `repo.create_version(..., status="uploading")` (:253-258), then `load_data(data=rows)` (:266), `conn.execute("COPY df TO ...")` (:269), `storage.put_file(...)` (:272), `repo.complete_version(...)` (:292) — none of it wrapped, and there is no call to `repo.fail_version` anywhere in this branch.

The failure is reachable, not hypothetical. `load_data` for the inline case is just `pdf = pd.DataFrame(data); conn.execute("CREATE TABLE df AS SELECT * FROM pdf")` (app/shared/data_io.py:105-107). I ran the repo's own venv against it: `[{'a': 1}, 5]` raises `TypeError: 'int' object is not iterable`. That payload passes both guards (it is a non-empty list), so a client posting a mixed array of objects and scalars gets an unhandled 500 — where the multipart path would have returned a typed 400 `invalid-file` (api.py:202-207).

The contrast the claim draws is real: `process_uploaded_file_async` wraps its body and on `except Exception` calls `repo.fail_version(version_id, str(e))` (app/features/files/services/processing.py:290-298); `replace.py:157-160` does the same. The inline branch does not.

Nothing cleans up afterwards. `grep -rn "uploading"` over app/ shows the only consumers are writers plus the status map at api.py:332 — `"uploading": "processing"` — so `GET /upload/status/{version_id}` and the durable fallback report the stranded row as "processing" indefinitely. The retention passes do not help: `sweep_expired` deletes expired artifacts and `sweep_orphans` deletes blobs with no owning row (app/features/files/services/retention.py:78,100) — neither touches `dataset_versions` rows stuck in `uploading`. No test asserts anything about inline-upload failure (tests/test_files_misc.py:82,100 and tests/test_sheets_and_tags.py:152,156 only exercise the happy path), and neither HANDOFF.md nor ARCHITECTURE.md documents this as a deliberate choice.

Two nits against the claim's impact wording, neither of which saves it: there IS a way to clear the orphan — `@router.delete("/datasets/{dataset_id}")` at app/features/data_accelerator/api.py:239 — and on the create path the 500 returns no body, so the UI never learns the version_id to spin on; the stranded row is instead visible via the dataset's version listing. When `dataset_id` was supplied the caller does have the dataset and sees a permanent "processing" version.

**Fix**

Wrap api.py:266-308 (everything after `create_version`) in try/except, mirroring processing.py:290-298: on exception call `await repo.fail_version(str(version["id"]), str(e))`, and on the create path also delete the just-created dataset (or defer `create_dataset` until after the parquet is successfully written). Re-raise as ProblemException(400, code="invalid-file") for pandas/DuckDB parse errors so the inline path matches the multipart path's typed 400 instead of a 500.

**Test to pin it**

"test_inline_upload_failure_fails_the_version_and_leaves_no_orphan_dataset" — an integration test in tests/ (needs the real DB + storage), posting `data='[{"a": 1}, 5]'` to /api/v1/upload and asserting a 400 invalid-file plus no dataset in the caller's catalog listing; a companion tests/unit/ case can pin that `load_data(data=[{"a":1},5])` raises so the handler must catch it.

## [high] PATCH transformation rename onto a taken name raises IntegrityError and returns a generic 500; the service's 409 branch is unreachable.

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and the near-identical sibling feature does it correctly.

1. The constraint exists: app/infra/db/postgres/migrations/20260809010000_transformations.sql:23 — `UNIQUE (dataset_id, name)`.

2. The UPDATE has no conflict handling. app/features/transform/repo.py:73-94, `update_definition` builds `sets` (including `name = :name` when `"name" in fields`) and runs a bare statement:
   `UPDATE transformation_definitions SET {...} WHERE id = :id AND dataset_id = :did RETURNING id::text`
   followed by `await s.commit()`. No `ON CONFLICT`, no try/except. A rename onto a taken name raises psycopg UniqueViolation, wrapped by SQLAlchemy as IntegrityError, which propagates out of the service and the route.

3. Nothing catches it on the way out. `grep -rn IntegrityError --include=*.py .` returns exactly two hits, both in app/features/explorer/service.py (18, 178) — none in transform. So it lands in app/api/errors.py:135-139 `_unhandled_exception_handler`: `detail = f"{type(exc).__name__}: {exc}" if settings.debug else "An unexpected error occurred."` then `problem_response(500, detail, request.url.path)` — no `code`, so `code=_code_for(500)`, a generic status slug.

4. The 409 branch is dead. app/features/transform/service.py:170-176:
   `updated = await repo.update_definition(dataset_id, definition["id"], fields)` / `if updated is None: raise HTTPException(409, ...)`.
   `update_definition` returns None only when `not is_uuid(definition_id)` or when the UPDATE matched zero rows. app/features/transform/api.py:89 already called `_definition_or_404(dataset_id, definition_id)` (api.py:34-38), which did `repo.get_definition` — itself is_uuid-gated and matching the same `id`/`dataset_id` predicate. So both None paths are already excluded except by a concurrent delete between the two calls. It can never fire for a name collision.

5. Create really does answer 409 for the same collision: repo.py:24-43 `create_definition` uses `ON CONFLICT (dataset_id, name) DO NOTHING RETURNING id::text` and returns None, which service.py:136-138 turns into a 409. tests/test_transformations.py:61 asserts that (`assert (await client.post(base, headers=h, json=body())).status_code == 409`). No test anywhere covers the PATCH rename collision — grep of tests/ for transform 409s finds only the create case and the publish-incomplete-run case (test_transformations.py:370-382).

6. Deliberate? No. app/features/explorer/service.py:176-180 — the structurally identical `update_view` — wraps the repo call in `try/except IntegrityError: raise HTTPException(409, f"A view named '{fields.get('name')}' already exists on this dataset")`. Transform's update was written from the same template but lost the try/except and kept the (now meaningless) `updated is None` check. Nothing in HANDOFF.md/ARCHITECTURE.md defends a 500 here.

**Fix**

Mirror explorer/service.py:176-180 in app/features/transform/service.py:170-176 — wrap the repo call in `try: updated = await repo.update_definition(...)` / `except IntegrityError: raise HTTPException(409, f"A transformation named '{fields.get('name')}' already exists on this dataset")`, importing IntegrityError from sqlalchemy.exc. Keep the `if updated is None` check but make it a 404 (the row was deleted concurrently), or drop it. Alternatively push it into the repo: add `ON CONFLICT DO NOTHING` to the UPDATE — but that is not expressible for UPDATE, so the service-level catch is the right layer.

**Test to pin it**

"test_renaming_a_transformation_to_a_name_already_used_on_the_dataset_is_a_409" in tests/test_transformations.py (integration layer — it needs the real Postgres unique constraint to fire, so it cannot live in tests/unit/). Create two definitions on one dataset, PATCH the second with the first's name, assert 409 and that the problem body's detail names the collision.

## [high] sweep_orphans has no limit, so POST /storage/gc violates its own "bounded per call" contract and can block the event loop for minutes.

- **domain:** ?
- **where:** 

**Evidence**

The claim's factual core is accurate and, unusually, it is contradicted by the repo's own stated contract.

app/features/files/services/retention.py:61-63 — "Bound one sweep so a backlog degrades into several runs rather than one very long transaction holding a connection. GC_BATCH = 500". Only sweep_expired honours it (line 78: `async def sweep_expired(*, limit: int = GC_BATCH)` -> `library_repo.list_expired_artifacts(limit=limit)`).

sweep_orphans (retention.py:100-130) takes no limit at all: line 109 `keys = storage.list_keys(ARTIFACT_ROOT)` lists EVERY object under the artifact root; line 113 `known = await library_repo.known_artifact_keys(keys)` passes the whole list into one statement — app/features/library/repo.py:597 `SELECT storage_key FROM artifacts WHERE storage_key = ANY(:sks)`; then the loop at 117-129 does `storage.modified_at(key)` and `storage.size(key)` per orphan. On S3 those are two separate HEAD requests per orphan (app/infra/db/storage.py:488-495), the exact pattern the file elsewhere calls out as pathological ("Summing a prefix by calling :meth:`size` per key costs a HEAD request per object on S3, which turns a usage report into thousands of round trips", storage.py:285-292 — list_sizes exists precisely to avoid it, and sweep_orphans does not use it).

run_gc (retention.py:133-142) runs both passes inline, and the route runs it synchronously inside the request: app/features/files/api.py:854-866 `return GcResponse(**await retention.run_gc())`. These are blocking boto3/filesystem calls inside an `async def` handler, so a large bucket stalls the whole event loop, not just this request. The endpoint docstring at api.py:861-862 asserts the opposite of what the code does: "Bounded per call, so a large backlog clears over several runs rather than one long request."

Not deliberate: ARCHITECTURE.md:209-213 and HANDOFF.md:81-83 describe the two passes and the grace window but never claim the orphan pass is unbounded on purpose; there is no comment justifying the asymmetry. No test pins it — tests/test_artifact_retention.py only exercises small-fixture cases (test_gc_reclaims_orphan_blobs_past_the_grace_period, line ~155).

One part of the claim is overstated: "the UI has no way to know it should call again" is false. GcResponse (app/features/files/schemas.py:79-85) indeed has no such field, but GET /storage/retention returns `expired_pending` (schemas.py:75-77, api.py:846-851), so a UI can poll for remaining backlog. That value is itself computed with `limit=retention.GC_BATCH`, so it saturates at 500 and cannot show backlog depth — but ">0 means call again" works. The unbounded orphan sweep stands regardless.

**Fix**

Give sweep_orphans the same bound as sweep_expired: `async def sweep_orphans(*, limit: int = GC_BATCH, now=None)`, break out of the loop once `deleted >= limit`, and chunk the key list into GC_BATCH-sized slices for known_artifact_keys instead of one giant ANY(). Replace the per-key `modified_at`+`size` pair with a single `storage.list_sizes(ARTIFACT_ROOT)` traversal so size costs nothing extra (only `modified_at` then needs a HEAD, and only for candidate orphans). Add a `work_remaining: bool` (or `orphans_pending`/`expired_pending`) field to GcResponse so the caller knows to re-invoke, and consider dispatching the sweep as the already-registered `artifact_gc` job rather than running it inline in the request.

**Test to pin it**

"test_gc_stops_after_the_batch_limit_and_reports_that_work_remains" — with more than GC_BATCH aged orphan blobs seeded under the artifact root, one POST /api/v1/storage/gc must delete at most GC_BATCH orphans and report work remaining, and a second call must clear the rest. Belongs in tests/test_artifact_retention.py (integration, since it goes through the route); a companion unit test on retention.sweep_orphans(limit=...) can live in tests/unit/.

## [high] PATCH /transformations re-resolves the sheet by its CURRENT name inside a pinned older version, so a confirmed rename makes the definition uneditable (404).

- **domain:** ?
- **where:** 

**Evidence**

Every link in the chain checks out.

1. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/transform/service.py:159-167 — PATCH sets `retargeting = (body.sheet is not None or body.version_selector is not None or body.steps is not None)`, so a steps-only PATCH retargets. Then line 163: `sheet = body.sheet if body.sheet is not None else definition["sheet_key"]` — a NAME — passed to `_resolve_target(dataset_id, sheet, selector)` (line 164), and line 166 `fields["logical_sheet_id"] = str(row["logical_sheet_id"])` is set unconditionally from that name match.

2. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/transform/repo.py:12-20 — `_DEF_COLS` is explicit: `s.current_sheet_key AS sheet_key`, joined from `dataset_sheets`, with the comment "callers always see the sheet's CURRENT name, however many renames it has been through." `get_definition` (repo.py:61-70) uses those cols, and api.py:34-38/81-90 feeds exactly that row into `service.update_transformation`.

3. A confirmed rename really does move `current_sheet_key`: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/shared/repo.py:279-292 remaps `dataset_version_sheets.logical_sheet_id` and then `UPDATE dataset_sheets SET current_sheet_key = :key, display_name = :name`. The per-version rows keep their own historical `sheet_key`/`sheet_name`.

4. `_resolve_target` (service.py:82-99) resolves the PINNED version via `_selector_pin` (library/service.py:101-108 — `mode="version"` -> `version_number`) and then `resolve_sheet_with_schema(ver, sheet)` -> `resolve_version_sheet_row` -> `_find_sheet` (/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/shared/datasets.py:150-158) which matches only on that version's `sheet_name` then `sheet_key`; miss => `raise HTTPException(404, f"Sheet not found: {sheet}")` at datasets.py:126-128. No logical-id branch anywhere on this path.

5. The correct path exists and is used elsewhere: `_resolve_run_sheet(ver, logical_sheet_id)` (service.py:102-114) matches on `logical_sheet_id` and is what `preview_transformation` (line 189) and `start_run` use. The module docstring (service.py:3-5) states the intended contract: "the sheet is re-resolved **by logical id**, so a confirmed rename never invalidates a saved pipeline." PATCH violates the feature's own stated invariant, so this is not by design.

6. No test contradicts it. tests/test_transformations.py:403-428 (`test_a_definition_survives_a_confirmed_sheet_rename`) creates the definition with NO `version_selector` (mode=current) and only exercises POST /run — which takes the logical-id path. Nothing tests PATCH after a rename, and nothing tests PATCH on a `mode="version"`-pinned definition. grep for "rename" in that file returns only that test.

Scope note (the claim is right but narrower than it sounds): the 404 needs a pinned selector (mode=version/tag) plus a confirmed rename; with the default mode=current the name resolves in the current version. The secondary "silent re-bind" at line 166 additionally requires the pinned version to contain a different logical sheet carrying that name — real, but rarer. The 404 is the certain failure.

**Fix**

In `update_transformation` (app/features/transform/service.py:162-166), only resolve by name when the caller actually supplied one. When `body.sheet is None`, resolve the version via `resolve_version(dataset_id, **_selector_pin(selector))` and then `_resolve_run_sheet(ver, definition["logical_sheet_id"])` — validating the pipeline against that row and leaving `logical_sheet_id` untouched. Set `fields["logical_sheet_id"]` only on the `body.sheet is not None` branch.

**Test to pin it**

"test_patching_steps_on_a_version_pinned_definition_survives_a_confirmed_sheet_rename" — integration, tests/test_transformations.py (needs the real upload/confirm-rename path, so not tests/unit/).

## [high] start_run maps every non-HTTP dispatch failure (DB/infra) to a hardcoded 400 "transformation-failed" that also echoes the raw exception repr.

- **domain:** ?
- **where:** 

**Evidence**

The claim's two citations are accurate and the whole path supports it.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/transform/service.py:216-220
```
def _pipeline_error(exc: Exception) -> Exception:
    """Map a DuckDB execution failure to a 400 naming the pipeline as the cause."""
    return ProblemException(
        400, f"Transformation pipeline failed to execute: {type(exc).__name__}: {exc}",
        code="transformation-failed")
```
Status is hardcoded 400 and the detail interpolates `type(exc).__name__: {exc}` unconditionally.

service.py:255-280: the FIRST except is `except StarletteHTTPException` and re-raises untouched. Since ProblemException and FastAPI HTTPException are both subclasses, every *actionable* 4xx already leaves by that branch — including the compiler's errors (app/features/transform/compile.py:81,92,147,165,319,353 all raise ProblemException) and the in-handler `_pipeline_error` raised at service.py:317. So what actually reaches the blanket `except Exception ... raise _pipeline_error(exc)` at 263-280 is almost exclusively infrastructure failure and internal bugs, not bad pipelines.

app/shared/worker.py:103-145 confirms the surface area: `jobs.create_job` (122), `jobs.start_job` (132), `handler(job)` and `jobs.complete_job` (140-141) with `except Exception: await jobs.fail_job(...); raise` (142-144). Any OperationalError from those propagates out of dispatch as a plain exception and is converted to 400. In async mode (`inline=False`) the only thing dispatch does is `create_job`, so a Postgres blip there is a pure infra error rendered as a client error.

The redaction contrast in the claim is also real. app/api/errors.py:132-136:
```
async def _unhandled_exception_handler(...):
    # Never leak internals to clients unless explicitly running in debug.
    detail = f"{type(exc).__name__}: {exc}" if settings.debug else "An unexpected error occurred."
```
`_http_exception_handler` (errors.py:112-121) does no redaction, so the 400 path emits the raw exception string in every environment while the 500 path suppresses exactly that string outside debug.

Not by design: nothing documents the 400 for infra. The only comment on it, service.py:269, actually mis-describes the helper — "``_pipeline_error`` is a 4xx/5xx problem+json" — when it can only ever be 400. HANDOFF.md/ARCHITECTURE.md say nothing about the status choice (only about `dispatch` inline vs loop). The deliberate, heavily-commented part of this block is the unconditional `repo.fail_run` guard, which is a different concern.

No test contradicts the claim; tests/unit/test_transform_run_bookkeeping.py:87-106 in fact exercises exactly the infra case — `BOOM = RuntimeError("could not enqueue job: connection reset")` — and asserts only `e.value.code == "transformation-failed"`, never the status. tests/test_transformations.py:198 asserts the same code for a genuinely bad pipeline, so the two are indistinguishable to a client.

One partial mitigation for the claim, not enough to refute it: `parse_steps` can raise a bare ValueError (app/features/transform/steps.py:100) for a stored definition with malformed steps, which is a legitimate 400 arriving on this branch in sync mode. That is one narrow case against an entire class of infra failures mapped the same way.

**Fix**

Give `_pipeline_error` a status parameter and split the two call sites. Keep 400 for the DuckDB-execution sites (service.py:202 and 317, where a bad pipeline really is the cause). At service.py:280 — where the exception came from the dispatch plumbing, not from executing the user's SQL — either re-raise the original exception so the unhandled handler turns it into a redacted 500, or raise `ProblemException(503, "Transformation could not be enqueued; retry.", code="dispatch-failed")` with the raw repr logged rather than interpolated. Keep the `repo.fail_run(run["id"], str(exc))` bookkeeping exactly as-is either way.

**Test to pin it**

"test_an_infrastructure_failure_in_dispatch_is_not_reported_as_a_client_error" in tests/unit/test_transform_run_bookkeeping.py — parametrized over sync/async, monkeypatch dispatch to raise an OperationalError-like exception, assert the surfaced status is 5xx (not 400), that the code is not "transformation-failed", that the detail does not contain the raw exception text, and that fail_run still fires.

## [high] GET .../duplicates and GET .../missing return full unmasked dataset rows, bypassing the sensitive-column masking enforced on every other row-returning read. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The masking layer is never invoked on either path. `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/data_quality.py` has no import of `app.shared.masking` anywhere in the file (contrast `explorer/service.py:90` and `:218`, which both do `from app.shared.masking import mask_rows, resolve_masking`). The routes at `app/features/explorer/api.py:259-288` and `:291-314` resolve a `principal` only for `_readable_version(...)` and then call `data_quality.find_duplicates(ver, sheet_name, columns, limit)` / `data_quality.missing_report(ver, sheet_name)` — the principal is not passed on, so masking cannot even be resolved downstream.

Raw values really are in the payload:
- `data_quality.py:140-147` — `examples = _row_dicts(conn.execute(group_examples_sql(physical, EXAMPLES_PER_GROUP), key_values), name_map)` where `group_examples_sql` (line 58) is `SELECT * FROM df WHERE ...`, i.e. every column, and `key={name_map[c]: safe_value(v) ...}` puts the grouped-on values in verbatim.
- `data_quality.py:198-207` — `most_missing_rows_sql` (line 71) is `SELECT *, (...) AS __null_count FROM df`, and `MissingRow(row={n: safe_value(v) for n, v in zip(names[:-1], raw[:-1])})` returns the whole row.

Response models confirm no client-side remedy: `app/features/explorer/schemas.py:132-138` (`DuplicateGroup.key`, `.examples` — "A few full rows from the group") and `:162-179` (`MissingRow.row`, `MissingResponse.rows_most_missing`) have no `masked_columns` field, unlike `PageResponse`/rowdiff which do (`data_accelerator/schemas.py:401`).

This is contrary to documented intent, not a documented exemption. `HANDOFF.md:143-147`: "PII masking — the dictionary's `sensitivity` field was inert. Now enforced **wherever raw rows are returned** ... The raw download is gated by the same permission ... masking alone would be theatre." `app/shared/masking.py:7-11` repeats it: "**This is a real control, not a display convenience.**" And `ensure_raw_access` (masking.py:159-176) 403s a non-privileged caller's `/download` on any dataset declaring sensitive columns — so `/duplicates` and `/missing` hand back the very values the download gate exists to withhold. The only documented scope narrowing I found (`mcp/tools/orient.py:244-246`, "masked on preview_rows and query_rows ... run_sql is never masked") is about MCP tool surfaces and explicitly carves out only `run_sql`, which is separately justified; it does not mention these two endpoints.

No test contradicts the claim — `tests/test_pii_masking.py` covers preview, structured query, saved views, row diff, download gating, editor/admin exemptions, and contains no reference to `/duplicates` or `/missing`.

**Fix**

Thread `principal` from the four routes in `app/features/explorer/api.py` into `find_duplicates`/`missing_report`. In each, after `resolve_sheet_with_schema`, call `masked = await resolve_masking(str(ver["dataset_id"]), row, principal)`; if non-empty, apply `mask_rows` to `examples` and to the `MissingRow.row` dicts, and mask the `DuplicateGroup.key` values (or use `digest()` for keys so grouping identity survives). Add `masked_columns: list[str] = []` to `DuplicatesResponse` and `MissingResponse` and populate it, mirroring `explorer/service.py:101-104`. Note the grouped-on columns themselves: if a sensitive column is in `columns=`, masking `key` to a constant `***` would collapse distinct groups visually — `digest()` is the right shape there.

**Test to pin it**

"test_duplicate_groups_and_missing_rows_mask_a_sensitive_column" in tests/test_pii_masking.py (integration layer, tests/), asserting a viewer sees `***` in `groups[].examples`, `groups[].key`, and `rows_most_missing[].row`, that `masked_columns` names the column, and that an admin still sees raw values.

## [high] The column-explorer endpoints return unmasked cell values (top_values, rare_values, examples) for columns the data dictionary declares sensitive.

- **domain:** ?
- **where:** 

**Evidence**

I tried to refute this and could not. The whole path carries no masking.

Route: `app/features/explorer/api.py:231-241` and `:244-256` — both handlers do `ver = await _readable_version(principal, ...)` then `return await service.explore_column(ver, sheet_name, column)`. The principal is used only for `_readable_version`, which (api.py:41-50) checks `Permission.DATASET_READ` and nothing else — no `DATASET_READ_SENSITIVE`, no `ensure_raw_access`.

Service: `app/features/explorer/service.py:234-272` — `async def explore_column(ver, sheet, column)` takes **no principal parameter at all**, so masking is not even reachable. It returns:
- `base.top_values` from `profile_column_duckdb` (`app/features/data_accelerator/services/profiling.py:83-97`: `SELECT {qcol}, COUNT(*) ... ORDER BY cnt DESC` → `TopValue(value=safe_value(row[0]), ...)`) — raw cell values;
- `rare_values` from service.py:247-251 (`SELECT {qcol}, COUNT(*) ... ORDER BY cnt ASC ... LIMIT 10`);
- `examples` from service.py:252-254 (`SELECT DISTINCT {qcol} ... LIMIT 5`).

Contrast with the surfaces that DO mask, in the same file: `query_sheet` (service.py:90-104) and `run_view` (service.py:217-223) both call `resolve_masking(...)` / `mask_rows(...)` and set `page.masked_columns`. `grep -n mask app/features/explorer/service.py` returns hits only at lines 87-104 and 217-223 — nothing in `explore_column`.

Response model: `app/features/explorer/schemas.py:182-194`, `class ColumnExplorerResponse(ColumnProfile)` — has `rare_values`, `examples`, `normalized_name`, `uniqueness`, `is_candidate_key`; there is **no** `masked_columns` field, unlike `QueryPage`/`ViewRunResponse`. So the claim's second half holds too.

Not by design — the opposite is documented. `app/shared/masking.py:1-16`: "a caller without the elevated permission sees masked values **wherever raw dataset rows are returned**... **This is a real control, not a display convenience.** Masking rows while leaving `/download` open would be theatre". `HANDOFF.md:143-147` repeats "Now enforced wherever raw rows are returned". `ARCHITECTURE.md:125-126`: `DATASET_READ_SENSITIVE` "gates both the unmasked read path and the raw download."

No test contradicts it: `tests/test_pii_masking.py` covers preview (:41), query (:55), saved-view run (:66), row-diff (:79) and the download gate (:118) — it never touches `/columns/{column}`. `grep -rn "columns/" tests/` matched only the dictionary PUT at test_pii_masking.py:25.

Repro shape: declare `email` sensitive via `PUT /datasets/{ds}/sheet-metadata/data/columns/email` with `sensitivity: "confidential"`, then as an editor (deliberately not exempt, per test :107) GET `/datasets/{ds}/versions/1/sheets/data/columns/email` — `top_values`/`rare_values`/`examples` come back as `ana@example.com`, not `a***@***.com`, while `/preview` on the same version masks them.

**Fix**

Give `explore_column` a `principal=None` parameter (both routes already have the principal) and, after building `base`/`rare_rows`/`examples`, do what `query_sheet` does: `masked = await resolve_masking(str(ver["dataset_id"]), row, principal)`; if `col["name"]` (physical name) is in `masked`, map every emitted value through `mask_value(v, masked[name])` for `top_values`, `rare_values` and `examples` — and for text columns also drop/mask `min`/`max`, which are raw values too. Add `masked_columns: list[str] = []` to `ColumnExplorerResponse` so the drawer can render the same badge the grid does. Consider the same audit for the persisted profile-run JSON (`GET /profile-runs/{id}`), which embeds `top_values` via `profile_column_duckdb` as well.

**Test to pin it**

"test_the_column_explorer_masks_a_sensitive_columns_values" in tests/test_pii_masking.py (integration layer, tests/), asserting that for an editor the response's `top_values`, `rare_values` and `examples` contain only `*`-shaped values and that `masked_columns == ["email"]`, plus an admin counterpart seeing the real values.

## [high] POST /versions/{v}/sql returns unmasked sensitive columns to any dataset:read caller and persists them as a fetchable parquet artifact.

- **domain:** ?
- **where:** 

**Evidence**

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/api.py:317-340 — the handler does only `ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)` then `resolve_version(...)` then `service.raw_sql_query(ver, request.sql, ArtifactLayout("query_output", ...))` followed by `_register_output_artifacts(...)`. No `ensure_raw_access`, and `principal` is never passed into the service.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/service.py:409-427 — `async def raw_sql_query(ver: dict, sql: str, layout: ArtifactLayout)` has no `principal` parameter at all; it builds `items = [{c: safe_value(v) ...}]` straight from the DuckDB frame and calls `_persist_frame(df, layout)` first. No masking call is reachable.

grep for the masking API confirms the only call sites are: app/features/explorer/service.py:101 (preview), :220 (structured query), app/features/data_accelerator/services/rowdiff.py:300, and `ensure_raw_access` at app/features/files/api.py:668,686 (the two /download routes). Nothing in the SQL path.

app/shared/masking.py:8-11 states the intent explicitly: "Masking rows while leaving /download open would be theatre — anyone could fetch the original file. So ensure_raw_access gates the raw-file paths too." ARCHITECTURE.md:125: DATASET_READ_SENSITIVE "gates both the unmasked read path and the raw download." Editors are deliberately excluded (tests/test_pii_masking.py::test_editors_are_deliberately_not_exempt), and an editor is exactly the role tests/test_explorer.py:289 uses to run raw SQL successfully.

No test contradicts the claim: tests/test_pii_masking.py covers preview, structured query, saved views, row-diff and both download routes — never /sql. tests/test_explorer.py:281-355 exercises /sql only for joins, select-only gating, file-read blocking, size guard and cross-team 404, and at :303-306 asserts the result parquet IS fetchable by the same caller via GET /api/v1/samples/{result_file}/data (files/api.py:758 only does team-scoped `_authorize_sample_access`, no sensitivity gate).

Not documented as deliberate: ROADMAP.md:87-99 (§6b) lists the SQL sandbox's safety properties as "normal RBAC + cross-team 404 + audit trail" — written before masking existed (HANDOFF.md:143-149 lists PII masking as a later capability gap). The SQL path was simply never revisited.

**Fix**

In app/features/explorer/api.py's sql_query handler, add `await ensure_raw_access(principal, dataset_id)` right after the DATASET_READ check — same gate as the download routes. Per-column masking is not feasible for arbitrary SQL (aliases, expressions, aggregates over a sensitive column), so the raw-file gate is the correct control: a dataset that declares any sensitive column requires dataset:read_sensitive to use the SQL console. That also stops the query_output parquet from ever being written.

**Test to pin it**

"test_the_sql_console_is_gated_once_pii_is_declared" in tests/test_pii_masking.py (integration layer, tests/): an editor POSTing SELECT email FROM data against a dataset with sensitivity=confidential gets 403 with code sensitive-data-restricted, an admin gets 200, and a dataset with no declared sensitivity is unaffected.

## [high] Explorer masks cells after execution, so filters/sort/`total` run on raw values — an unprivileged caller can use `total` as a search oracle to recover masked columns.

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every step of the path, and I tried hard to find the guard that would refute it — there is none.

1. Post-hoc masking, exactly as cited. app/features/explorer/service.py:96-104 in `query_sheet`: `page = execute_query(conn, "df", spec, row["schema_json"], version_id=...)` runs first; only afterwards `masked = await resolve_masking(...)` / `page.items = mask_rows(page.items, masked)`. `run_view` has the identical shape at service.py:213-223.

2. Filters/sort/total are computed on raw values. app/shared/query/compile.py:134-142 builds `where_sql` from the caller's spec and runs `SELECT COUNT(*) FROM {source_table}{where_sql}` — that count becomes `QueryPage.total` (compile.py:159), which `mask_rows` never touches (masking.py:63-78 only rewrites cell values inside `items`). Ordering likewise comes from `compiled.order_by` over raw columns.

3. No sensitivity awareness anywhere in validation/compilation. `grep -n "sensitiv\|mask" app/shared/query/validate.py app/shared/query/compile.py` returns nothing. `validate_spec` only resolves column existence/dtype, so a filter on a declared-sensitive column is fully accepted. The pydantic layer does not help either: `FilterOp` (app/shared/query/schemas.py:26-45) explicitly admits `eq`, `between`, `contains`, `starts_with`, `ends_with`, `regex` and the `len_*` family — i.e. a binary-search / per-character oracle, not just exact match.

4. The route really does expose this to an unprivileged caller. app/features/explorer/api.py:343-359 `POST .../sheets/{sheet_name}/query` requires only `Permission.DATASET_READ` (via `_readable_version`, api.py:45) and passes the caller-supplied `spec` straight through; its own docstring promises "Columns the data dictionary marks sensitive come back masked".

5. No test contradicts it. tests/test_pii_masking.py has 10 tests (preview, structured query, saved view, row diff, admin exemption, editor non-exemption, download gating, no-sensitivity dataset, non-sensitive level) and `grep -rn "filter" tests/test_pii_masking.py` returns nothing — the filter/total path is untested.

6. It is not documented as deliberate; the docs assert the opposite intent. app/shared/masking.py:7-11: "**This is a real control, not a display convenience.** Masking rows while leaving `/download` open would be theatre" — and `ensure_raw_access` (masking.py:159-176) exists precisely to close a bypass. HANDOFF.md:143-147 and ARCHITECTURE.md:125-126 repeat that `dataset:read_sensitive` "gates the unmasked read path". The filter/total oracle is the same class of bypass the module went out of its way to close for downloads, so this is an unnoticed hole, not a documented trade-off.

Related and worse, not required by the claim: `explore_column` (service.py:234-272) and the raw-SQL escape hatch `raw_sql_query` (service.py:409-427) take no `principal` at all and return raw `examples`, `rare_values` and top values / arbitrary SELECT output — neither calls `resolve_masking` or `ensure_raw_access`. So masked values are recoverable directly, not just via the oracle.

**Fix**

In `validate_spec` (app/shared/query/validate.py), take the resolved masked-column set as an argument and reject any filter/search/sort/group reference to a masked column with a 400 `sensitive-column-not-filterable` problem code, resolving masking BEFORE `execute_query` in both `query_sheet` (service.py:92) and `run_view` (service.py:210). Cheapest correct variant: resolve masking first, and if the spec touches a masked column in `filters`, `search`, `sort` or `group_by`, raise the typed problem instead of executing. Also pass `principal` into `explore_column` and gate `raw_sql_query` with `ensure_raw_access`, otherwise the oracle fix is moot.

**Test to pin it**

"test_a_masked_column_cannot_be_filtered_or_sorted_on" in tests/ (integration, alongside tests/test_pii_masking.py) — as a non-admin, POST a filter `{"column":"email","op":"eq","value":"<known raw value>"}` and assert a 400 with code `sensitive-column-not-filterable` rather than a 200 whose `total` is 1; plus a unit test in tests/unit/ that `validate_spec` raises when a spec's sort or search references a masked column.

## [high] `?columns=,` (separators only) yields an empty column list, producing invalid `GROUP BY  HAVING` SQL and an unhandled DuckDB ParserException -> 500.

- **domain:** ?
- **where:** 

**Evidence**

Route does no validation beyond type: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/api.py:264 `columns: str | None = Query(default=None, ...)` — a plain optional string, no min_length/pattern, so "," reaches the service verbatim (same at api.py:280 for the per-sheet route).

Service: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/data_quality.py:118-127:
```
if columns:
    seen: dict[str, dict] = {}
    for ref in (c.strip() for c in columns.split(",") if c.strip()):
        col = resolve_schema_column(ref, schema)  # unknown-column 400
        seen.setdefault(col["name"], col)
    cols = list(seen.values())
```
`columns = ","` is truthy, so the else-branch (all schema columns) is skipped, but every split part is falsy after strip, so the loop body never runs and `cols == []` -> `physical == []`. Same for `" "`, `",,"`, `" , "`.

Builder: data_quality.py:46-51 `cols = ", ".join(...)` over an empty list -> empty string. I executed the real builder against real duckdb from ./venv:
```
'SELECT COUNT(*), COALESCE(SUM(cnt), 0) FROM (SELECT COUNT(*) AS cnt FROM df GROUP BY  HAVING COUNT(*) > 1)'
ERR ParserException Parser Error: syntax error at or near "HAVING"
```
The call site data_quality.py:133-134 executes it inside a bare `try/finally: conn.close()` with no `except duckdb.Error` (unlike app/shared/duck.py:73,112 and app/features/data_accelerator/services/pivot.py:87 which do translate duckdb.Error). So it propagates to the catch-all at app/api/errors.py:136-139 `_unhandled_exception_handler`, which returns "An unexpected error occurred." (500). duplicate_groups_sql at :38-43 and group_examples_sql at :54-58 (empty WHERE) are broken the same way, but totals is reached first.

No test contradicts it: tests/test_dup_missing_explorers.py:48,58,64 only cover valid columns and the unknown-column 400; tests/unit/test_data_quality_helpers.py:30,38,41 only pass non-empty lists. No docstring, HANDOFF.md or ARCHITECTURE.md note treats empty-subset as intentional — the docstrings say "subset to group on", and the unknown-column path is deliberately a 400, showing bad column input is meant to be a typed client error.

**Fix**

In `find_duplicates` (data_quality.py:118-123), after building `cols`, raise a 400 when the caller supplied `columns` but nothing resolved: `if columns and not cols: raise HTTPException(400, "columns must name at least one column")`. (Alternatively normalize to the all-columns/exact path, but 400 matches the existing unknown-column behaviour.) Optionally add a defensive `if not physical_cols: raise ValueError` guard in the three builders so the unit layer pins it.

**Test to pin it**

"test_duplicates_rejects_columns_containing_only_separators_with_400" in tests/test_dup_missing_explorers.py (integration layer, tests/), asserting GET .../duplicates?columns=, returns 400 not 500; plus "test_duplicate_sql_builders_reject_an_empty_column_list" in tests/unit/test_data_quality_helpers.py.

## [high] PATCH /views re-resolves the target sheet by the logical sheet's CURRENT name against the pinned (older) version, so editing a renamed-sheet view 404s "Sheet not found: <new name>".

- **domain:** ?
- **where:** 

**Evidence**

app/features/explorer/service.py:163 `sheet = body.sheet if body.sheet is not None else view["sheet_key"]`, then :166-170 any of sheet/version_selector/query being present calls `_resolve_view_target(dataset_id, sheet, selector)`.

`view["sheet_key"]` is NOT the per-version key: app/features/explorer/repo.py:174-181 `_VIEW_COLS = "... s.current_sheet_key AS sheet_key, s.display_name AS sheet_name ..."` with `_VIEW_FROM = "dataset_views v JOIN dataset_sheets s ON s.id = v.logical_sheet_id"` — i.e. the logical sheet's CURRENT display key.

`_resolve_view_target` (service.py:113-129) resolves the selector-pinned version and does `row = _find_sheet(rows, sheet)` over `get_version_sheet_rows(ver)`; `_find_sheet` (app/shared/datasets.py:150-158) matches only that version's own `sheet_name` then `sheet_key`, and `list_version_sheets` (app/shared/repo.py:90-108) reads `sheet_key, sheet_name` straight off `dataset_version_sheets`, which are frozen per version. Miss -> `raise HTTPException(404, f"Sheet not found: {sheet}")` (service.py:123) — exactly the message the claim predicts.

Confirm-rename really does mutate only the logical row: app/shared/repo.py:292 `UPDATE dataset_sheets SET current_sheet_key = :key, display_name = :name WHERE id = :id` (called from app/features/data_accelerator/services/sheet_identity.py:92-98). Old versions' `dataset_version_sheets` rows keep the old name.

run_view is the documented opposite (service.py:186-195): docstring "resolving the sheet by logical id (rename-proof — §1's payoff)" and `next((r for r in rows if str(r.get("logical_sheet_id") or "") == view["logical_sheet_id"]), None)`. So the asymmetry is real.

No test contradicts it: tests/test_saved_views.py:133 test_view_survives_confirmed_rename only calls /run after the rename, never PATCH; the only PATCH test (line 61) is on an un-renamed "current"-mode view, where the current version still carries the current key. No docstring/comment/HANDOFF entry justifies name-based resolution on update (grep for view+sheet_key in HANDOFF/ARCHITECTURE/docs: nothing).

Scope note: the failure needs the resolved version to lack the current name — i.e. a version-pinned/tag-pinned view over a renamed sheet, or a PATCH that pins an older version. A "current"-mode view over a confirmed rename still PATCHes fine. There is also a rarer silent-mis-retarget: if the pinned version happens to contain a *different* sheet whose key equals the new current key, update_view rewrites `fields["logical_sheet_id"]` (service.py:170) to that other sheet, silently repointing the view.

**Fix**

In update_view, only resolve by name when the caller actually supplied `body.sheet`. Otherwise resolve the pinned version's row by `view["logical_sheet_id"]` (the same `next(... logical_sheet_id == ...)` lookup run_view uses) and keep `logical_sheet_id` unchanged; factor that lookup into a shared helper in service.py so run_view and update_view cannot drift.

**Test to pin it**

"test_patch_view_query_works_after_confirmed_rename_on_a_version_pinned_view" in tests/test_saved_views.py (integration layer, tests/ — it needs the xlsx upload + confirm-rename + PATCH round trip).

## [high] POST /teams/{tid}/members upserts an existing member's role without the last-owner guard, so the sole owner can be demoted to viewer with a 201. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/auth/api.py:135-148 `add_member` runs only three checks — `principal.can(team_id, Permission.TEAM_MANAGE)`, `_ensure_grantable(principal, team_id, body.role)`, and target-user existence — then calls `await repo.upsert_member(team_id, body.user_id, body.role.value)` and returns 201. There is no `get_membership` lookup and no `_guard_last_owner` call. grep confirms `_guard_last_owner` is invoked at only two sites: api.py:164 (PATCH, `if current["role"] == Role.OWNER.value and body.role != Role.OWNER`) and api.py:182 (DELETE).

repo.py:116-129 `upsert_member` is `INSERT ... ON CONFLICT (team_id, user_id) DO UPDATE SET role = EXCLUDED.role, updated_at = now()`, so POSTing an existing member is a role rewrite, not a no-op or a conflict.

Schema does not block it: schemas.py:55-57 `AddMemberRequest` is just `user_id: str` + `role: Role = Role.VIEWER` — no "must not already be a member" or owner-related validation.

`_ensure_grantable` (api.py:34-44) only caps the role *upward* (`ROLE_RANK[role] > ROLE_RANK[own]`), so demotion to viewer passes. permissions.py:51-55 gives TEAM_MANAGE to `admin`, so even a plain team admin (rank 30) can POST `{user_id: <sole owner>, role: "viewer"}` and strip the only owner — while the identical PATCH returns 409 (api.py:163-164).

No test contradicts the claim: tests/test_teams_membership.py:73-103 exercises the guard only via DELETE (:89) and PATCH (:92); no test POSTs an existing owner. The file's own module docstring (:3-5) asserts the invariant "the last owner can never be removed or demoted", and HANDOFF.md:580 restates "last owner can't be removed" — so this is a stated invariant, not a documented exception. Not BY_DESIGN.

One correction to the claim's framing: TEAM_DELETE loss is real (permissions.py:56 — only owner holds it), but a *platform superuser* is not the only repair path in one common case: the demoted ex-owner keeps whatever role you gave them, and any remaining team admin still has TEAM_MANAGE — yet no one can grant `owner` back, because `_ensure_grantable` forbids granting above your own rank. So the team is genuinely unrecoverable without a superuser.

**Fix**

In `add_member`, before the upsert, look up the existing membership and apply the same guard the PATCH path uses:

    current = await repo.get_membership(team_id, body.user_id)
    if current and current["role"] == Role.OWNER.value and body.role != Role.OWNER:
        await _guard_last_owner(team_id, body.user_id)

(Alternatively, and more cleanly, make the guard the responsibility of a single `_set_member_role` helper that both POST and PATCH call, so a future third write path cannot skip it.)

**Test to pin it**

"test_add_member_cannot_demote_the_last_owner" in tests/test_teams_membership.py (integration layer, alongside test_remove_member_and_last_owner_guard): POST /api/v1/teams/{tid}/members with the sole owner's user_id and role "viewer" must return 409, and a follow-up GET /members must still show that user as owner.

## [high] PATCH /webhooks/{id} renaming onto an existing name in the same team hits the UNIQUE (team_id, name) constraint and 500s instead of returning 409.

- **domain:** ?
- **where:** 

**Evidence**

The unique constraint is real: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/infra/db/postgres/migrations/20260813000000_webhooks.sql:28 `UNIQUE (team_id, name)`.

Create handles it: repo.py:33 `ON CONFLICT (team_id, name) DO NOTHING` returns no row, and api.py:80-81 `if not row: raise HTTPException(409, f"A webhook named '{body.name}' already exists")`.

Update does not: repo.py:76-93 `update_subscription` builds `sets.append(f"{col} = :{col}")` for `name` (repo.py:81-83) and executes a bare `UPDATE webhook_subscriptions SET ... WHERE id = :id RETURNING ...` (repo.py:88-90) with no ON CONFLICT and no try/except. The asyncpg UniqueViolation surfaces as sqlalchemy IntegrityError out of `await s.execute(...)`.

Nothing upstream converts it. app/features/webhooks/api.py:106-116 `update_webhook` only calls `_manageable` (a read, no name check) and then the repo; the only guard is `if not row: 404`, which never fires because the exception is raised before a row is returned. app/api/errors.py:136-147 registers `_unhandled_exception_handler` for bare `Exception`, producing status 500 with `code` defaulted by `_code_for(500)` (errors.py:55) and detail "An unexpected error occurred." — exactly what the claim describes.

Pydantic does not block it: `WebhookUpdate.name` (app/features/webhooks/schemas.py:47) is just `str | None` with min/max length, no cross-row uniqueness check.

No test contradicts it. tests/test_webhooks.py:78-94 (`test_subscription_crud`) asserts 409 only on the *create* duplicate (line 82-83); the PATCH it exercises (line 85-86) sends only `enabled` and `events`, never a colliding `name`. grep of tests/ finds no rename-collision case.

Not by design — the sibling feature does the opposite deliberately: app/features/explorer/service.py:176-181 wraps `repo.update_view` in `except IntegrityError: raise HTTPException(409, f"A view named '{...}' already exists on this dataset")`. Webhooks' update path simply lacks that wrapper.

(Adjacent, same line range: an explicit `{"name": null}` body also passes `exclude_unset` and sets a NOT NULL column to NULL — another 500 from the same missing guard.)

**Fix**

In `update_webhook` (app/features/webhooks/api.py:106-116), wrap the `repo.update_subscription` call in `try/except IntegrityError` and raise `HTTPException(409, f"A webhook named '{body.name}' already exists")`, mirroring app/features/explorer/service.py:176-181. Optionally also reject an explicit `name=None` in `WebhookUpdate` so it cannot null a NOT NULL column.

**Test to pin it**

"test_renaming_a_webhook_onto_an_existing_name_returns_409" in tests/test_webhooks.py (integration layer — it needs the real Postgres unique constraint, so it belongs in tests/, not tests/unit/).

## [high] AuditMiddleware never supplies team_id, resource_type, resource_id, action or metadata, so those audit_log/AuditEntry fields are always null and audit.query's team scoping is dead.

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every point I could check.

1. `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/api/middleware.py:59-69` — the ONLY call to `audit.record` in the app passes exactly: method, path, status_code, actor_user_id, actor_email, ip, user_agent, request_id, duration_ms. No `team_id`, `resource_type`, `resource_id`, `action`, or `metadata`. `grep -rn "audit\.record"` over the repo returns one hit (middleware.py:59); `audit.query` returns one hit (features/audit/api.py:44).

2. `app/shared/audit.py:20-36` declares all five omitted params with `= None` defaults, and line 55 falls back to `"action": action or f"{method} {path}"`. So `action` is literally the method+path string, and `team`/`rtype`/`rid`/`meta` bind NULL on every row.

3. `app/shared/audit.py:80` — `scope = "" if team_ids is None else " WHERE team_id = ANY(:tids)"`. Since `team_id` is always NULL, `team_id = ANY(:tids)` is NULL (never true) for every row, so any caller passing team_ids gets 0 rows and total=0. `app/features/audit/api.py:44` calls `audit.query(limit=..., offset=...)` with no team_ids — the branch is dead code today.

4. `app/features/audit/api.py:17-33` — `AuditEntry` declares `team_id`, `resource_type`, `resource_id`, `metadata` as optional, so they serialize as JSON nulls forever; nothing else populates them.

5. `app/infra/db/postgres/migrations/20260803010000_hardening.sql:34` creates `ix_audit_log_team ON audit_log (team_id)` on a column that is never written — the index is indeed dead weight.

6. Not deliberate: `ARCHITECTURE.md:100-101` states the opposite — "Audit middleware writes one append-only audit_log row per request: actor, team, method, path, status, resource, duration." Team and resource are documented as captured but are not. No docstring or comment anywhere justifies the omission.

7. Corroborating consequence in-tree: `app/features/discovery/repo.py:554` (`dataset_usage`) and :638 (timeline `audit` branch) both have to answer "what happened to dataset X" with `path LIKE '%/datasets/' || :did || '%'` — a string match — precisely because `resource_id` is never set.

8. No test contradicts the claim. Every audit test (tests/test_api.py:219-231, test_explorer.py:356-364, test_wave0_journeys.py:422-441, test_e2e_journeys.py:180-185, test_coordinated_sampling.py:147-150) asserts only on path/method/status/duration_ms/actor — none asserts team_id or resource fields, and none calls query with team_ids.

Minor over-statement in the claim: the "what happened to dataset X" question is already answered server-side by the LIKE-based `/discovery` usage+timeline endpoints, so a UI is not forced to regex paths client-side for the per-dataset case. The per-team case ("what did team Y do") genuinely has no answer.

**Fix**

Populate the fields in `AuditMiddleware.dispatch` before calling `audit.record`: derive `resource_type`/`resource_id` from `request.scope["path_params"]` (e.g. `dataset_id`, `team_id`, `view_id` — the matched route's params are on the scope after `call_next`), and set `team_id` from the resolved dataset's team (or from `path_params["team_id"]`). Cheapest correct version: have the dataset-permission dependency (`ensure_dataset_permission`) stash `request.state.audit_resource = ("dataset", dataset_id, team_id)` — it already loads the dataset row — and have the middleware read that, falling back to path_params. Leave `action` alone or set it from `request.scope["route"].name` so it is a stable verb rather than a URL. Until team_id is written, either drop `ix_audit_log_team` or keep it but do not expose a team filter on `GET /audit`, since `audit.query(team_ids=...)` currently returns zero rows.

**Test to pin it**

"test_audit_rows_carry_team_and_resource_for_dataset_writes" — an integration test in tests/ (needs the DB + real middleware): POST a dataset write as a team admin, then GET /api/v1/audit as superuser and assert the newest matching entry has team_id == the dataset's team, resource_type == "dataset" and resource_id == the dataset id. Pair it with "test_audit_query_scoped_to_team_ids_returns_that_teams_rows" in tests/ calling audit.query(team_ids=[team_id]) and asserting a non-empty result.

## [high] POST /api/v1/webhooks never reads the X-Team-Id header, so it silently creates the subscription in the caller's home team. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

I tried to refute this and could not. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/webhooks/api.py:64-82 declares only `body`, `team_id: str | None = Query(default=None, description="Defaults to your active team")` and `principal` — there is no `Header(alias="X-Team-Id")` parameter anywhere in the signature (the whole module has zero occurrences of "X-Team-Id" or "Header"; only `Query` is imported at line 11). Line 74 is literally `team = team_id or pick_active_team(principal, None)`, hardcoding the header slot to None.

pick_active_team (app/features/auth/deps.py:92-109) is the resolver: `if x_team_id: ... return x_team_id` / else home team / else sole membership / else `raise HTTPException(400, "Ambiguous team context — specify the X-Team-Id header")`. With None forced in, branch 1 is dead, so (a) a caller with a home team plus `X-Team-Id: <other team>` gets the HOME team, and line 75 `_ensure_can_create(principal, team)` happily passes because they have team:manage there — 201 with a subscription pointing at the wrong team; (b) a multi-team caller with no home team gets a 400 whose message tells them to send a header this route does not read. The query param's own description ("Defaults to your active team") is false: `get_active_team` (deps.py:112-118) defines the active team AS `pick_active_team(principal, x_team_id)` with the header.

This is inconsistent with the sibling routes, not a documented choice. app/features/auth/api.py:72,82 does the same "explicit wins, else fall back" pattern but correctly threads the header: `x_team_id: str | None = Header(default=None, alias="X-Team-Id")` ... `team_id = body.team_id or pick_active_team(principal, x_team_id)`, and carries an explanatory comment. app/features/files/api.py:125 -> 157/247/413 likewise passes `x_team_id`. The only other `pick_active_team(principal, None)` is app/features/data_accelerator/api.py:127, and there it is deliberate and commented (a fallback for dataset-less outputs, wrapped in try/except).

No test contradicts the claim and none covers it: tests/test_webhooks.py:57,82,97,103,291 all POST /api/v1/webhooks with plain `auth(...)` headers and never send X-Team-Id or ?team_id, so the gap is invisible to the suite. No mention of a deliberate webhooks/team-header exception in any *.md. The wrong team_id is echoed back in WebhookCreated (schemas.py:54-70), so it is detectable but not signalled.

**Fix**

In app/features/webhooks/api.py add `x_team_id: str | None = Header(default=None, alias="X-Team-Id")` to create_webhook's signature (import Header from fastapi) and change line 74 to `team = team_id or pick_active_team(principal, x_team_id)`.

**Test to pin it**

"test_create_webhook_honours_x_team_id_header_over_home_team" — integration layer, tests/test_webhooks.py: a multi-team admin whose home team is A POSTs /api/v1/webhooks with X-Team-Id: B and asserts the response team_id == B (plus a second case: a member of two teams with no home team gets 201, not the ambiguous-context 400, when the header is set).

## [high] webhooks.deliver() finds its row by scanning only the newest 200 deliveries; a backlogged row is silently skipped and left 'pending' forever. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/webhooks/service.py:108-111 is exactly as claimed:
  `deliveries, _ = await repo.list_deliveries(subscription_id, limit=200, offset=0)`
  `delivery = next((d for d in deliveries if d["id"] == delivery_id), None)`
  `if delivery is None: return {"delivered": False, "reason": "delivery row missing"}`
— it returns WITHOUT calling `repo.record_attempt`, unlike every other exit path in the function (lines 103, 127, 133).

app/features/webhooks/repo.py:164-177 confirms the window is newest-first and hard-capped: `... WHERE subscription_id = :sid ORDER BY created_at DESC LIMIT :limit OFFSET :offset`. `grep "^async def" repo.py` shows there is NO `get_delivery(delivery_id)` — the list scan is the only lookup, so this is a shortcut, not a fallback.

The FIFO worker makes the hazard worse, not milder: app/shared/worker.py:60-72 claims jobs `ORDER BY created_at` (oldest first). So on a subscription with a backlog of N > 200 deliveries, the worker drains the OLDEST first — precisely the rows that fall outside the newest-200 window. Those N-200 deliveries are never POSTed, never marked failed, and stay `status='pending'`, `attempts=0`, `error=NULL`.

Worse, the job is reported as a success: `_run_one` (worker.py:76-83) only fails a job on exception; `deliver()` returns a dict, so `jobs.complete_job` marks the job completed. Both the jobs table and the deliveries table therefore lie about the same event.

No test contradicts it: `grep -rn "delivery row missing|limit=200" ` matches only service.py; tests/test_webhooks.py only exercises single-delivery cases (`deliveries["total"] == 1`). No comment/docstring/HANDOFF.md/ARCHITECTURE.md text defends the 200 cap — HANDOFF.md:152-154 asserts the opposite intent ("delivery attempts recorded"), and the module docstring says "Delivery failures are recorded on the delivery row rather than raised."

**Fix**

Add `repo.get_delivery(delivery_id)` (`SELECT {_DELIVERY_COLS} FROM webhook_deliveries WHERE id = :id`) and use it at service.py:108 instead of the list scan. Keep the None branch but make it terminal like the others: `await repo.record_attempt(delivery_id, status="failed", response_status=None, error="delivery row missing")` before returning, so no path leaves the row in 'pending'.

**Test to pin it**

"test_delivery_beyond_the_newest_200_rows_for_a_subscription_is_still_attempted_and_recorded" — integration, tests/test_webhooks.py (needs real Postgres rows: create 201+ deliveries on one subscription, run the worker, assert no row remains status='pending').

## [high] POST /teams/{id}/members requires a raw user_id but no endpoint lets a UI resolve a user (by email or otherwise) who is not already in a team the caller belongs to.

- **domain:** ?
- **where:** 

**Evidence**

The claim's facts check out on every point.

1. `AddMemberRequest` takes an opaque id only — /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/auth/schemas.py:54-56: `class AddMemberRequest(BaseModel): user_id: str = Field(..., description="User to add to the team"); role: Role = Role.VIEWER`. No `email` alternative, and the handler resolves strictly by id: api.py:143 `target = await repo.get_user_by_id(body.user_id)` → 404 `f"User not found: {body.user_id}"`.

2. No user-directory route exists. `grep -rn "get_user_by_email\|list_users\|search_users" app/` returns only api.py:87 (internal duplicate check), repo.py:22, bootstrap.py:23 — no route calls it. The only `/users` route in the whole app is /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/auth/api.py:68 `@router.post("/users", ...)`. There is no GET.

3. The POST /auth/users fallback really does dead-end: api.py:87-88 `if await repo.get_user_by_email(body.email): raise HTTPException(409, "A user with that email already exists")` — the 409 body carries no id, so the console gets an error and no forward path.

4. Nothing else fills the gap for a normal admin. `GET /teams` (api.py:113) lists only the caller's own memberships; `GET /teams/{id}/members` (api.py:120) 404s unless the caller has TEAM_READ in that team; `/audit`, which does expose `actor_email` + `actor_user_id`, is superuser-only (/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/audit/api.py:41-42). MCP has no user-lookup tool either (only `orient` → /auth/me).

5. Not documented as deliberate. `E2E-BUSINESS-SUMMARY.md:190-208` enumerates the stated limits and mentions only that sign-on is a placeholder; HANDOFF.md:575-581 ("Auth/RBAC POC") lists the header-identity swap point but never scopes out a user directory. Contrast with the 404-hides-existence choice, which is documented in three places — so this omission is a gap, not a recorded decision.

6. No test contradicts it; tests/test_teams_membership.py obtains every id via `create_team_user` (tests/conftest.py:261 → POST /api/v1/auth/users), i.e. the suite only ever exercises the mint-a-new-user path, which is precisely why the gap survived.

One correction to the claim's wording: "unbuildable" is slightly overstated. If the target user shares any team with the operator, the UI can harvest their id from GET /teams/{that_team}/members. The genuine dead end is the cross-team case — adding a colleague who is in no team the operator can read — which is the ordinary "add an existing colleague" case.

**Fix**

Cheapest correct fix: widen `AddMemberRequest` to accept either identifier — `user_id: str | None` plus `email: EmailStr | None` with a model validator requiring exactly one — and in `add_member` resolve via `repo.get_user_by_email` when `email` is given, keeping the existing 404 shape so non-existent users stay indistinguishable. This adds no directory-enumeration surface (the caller must already know the email) and still requires TEAM_MANAGE. If a real picker UI is wanted later, add `GET /auth/users?email=` gated on TEAM_MANAGE in some team, returning exact-match only.

**Test to pin it**

"test_add_member_accepts_email_for_a_user_outside_the_callers_teams" in tests/test_teams_membership.py (integration layer — it needs the real principal/RBAC path and Postgres): create a user in team A, then as an owner of unrelated team B who is not in team A, POST /api/v1/teams/{B}/members with only {"email": ..., "role": "editor"} and assert 201 plus the returned user_id matching. Pair it with a unit test in tests/unit/ asserting AddMemberRequest rejects both-neither combinations of user_id/email.

## [high] MCP `pivot` forwards include_column_totals but never renders the service's column_totals, so the requested per-column totals are silently dropped. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim holds on every leg of the path.

1. The tool argument exists and is forwarded: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/compute.py:254 `include_column_totals: Annotated[bool, Field(description="Compute per-column totals.")] = False` and :269 `"include_column_totals": include_column_totals,` in the POST /pivot body.

2. The service really computes it as a SEPARATE key, not folded into `totals`: app/features/data_accelerator/services/pivot.py:262-274 builds `column_totals` (re-aggregated over the pivot dim) and :300-301 returns `totals=totals, column_totals=column_totals` as two distinct fields. Schema confirms they are different: app/features/data_accelerator/schemas.py:1032 `totals` = "Grand totals per value alias", :1034 `column_totals` = "Per-output-column totals (include_column_totals)".

3. The renderer only reads `totals`: compute.py:280-291 is the whole return of `pivot`, and its only totals line is :289 `render.fields(list((payload.get("totals") or {}).items()))`. The string "column_totals" appears nowhere else in app/ except the request-side lines above (grep over the repo). render.py has no payload-wide dump — `join`/`fields`/`table` only emit what they are handed. So the key is fetched from the service, paid for, and dropped.

4. Nothing pins or excuses the current behaviour. The two pivot tests in tests/unit/test_mcp_compute.py:684-728 assert only that the flag travels in the request body; neither asserts anything about the rendered totals. `grep -rni "column_totals|column totals" --include=*.md .` returns nothing — no HANDOFF/ARCHITECTURE justification, no code comment. tests/test_pivot.py:91-92 asserts the HTTP layer does return `body["column_totals"]["Q1"]`, i.e. the data is genuinely there for the tool to render.

Aggravating detail the claim did not mention: grand `totals` is computed and rendered unconditionally (pivot.py:253-260, no flag guard), so a caller who asks for column totals gets a totals block anyway — plausible-looking, but it is the grand total per alias, not per-column. The response is confident, well-formed, and missing exactly what was asked for.

**Fix**

In compute.py's `pivot` return (around line 289), render the second totals map alongside the grand totals, labelled so the two are not confused, e.g. add `render.section("column totals", render.fields(list((payload.get("column_totals") or {}).items())))` (and label the existing block "grand totals"). `render.join`/`fields` already drop empty parts, so the unrequested case stays silent.

**Test to pin it**

"test_a_pivot_with_include_column_totals_renders_the_per_column_totals" in tests/unit/test_mcp_compute.py (unit layer — the fake client's pivot_result() helper at line 136 already accepts column_totals, so stub column_totals={"Q1": 111, "Q2": 222} and assert both values appear in the rendered text and are distinguishable from the grand totals block).

## [high] list_saved_objects sorts only the first 1000 objects _fetch_all collected but prints the untruncated service total, so past 1000 the page is wrong and looks complete. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and the contrast with the documented `_scan` path is real.

1. The cap is real and silent. `app/features/mcp/tools/context.py:126-147` — `_fetch_all` loops `while True`, and breaks on `len(items) >= cap` (line 142), returning `items[:cap], total` (line 147). `total` is whatever the *service* reported (line 140), i.e. the true count, not `len(items)`. The docstring (127-133) says "Every item of a Page[] endpoint, up to `cap`" and justifies collecting everything so "sorting and paging be exact" — it never returns or signals that truncation happened. `SCAN_CAP = 1000` (line 24).

2. Sorting happens after the cap. `context.py:852-865`:
   `items, total = await _fetch_all(ctx, f"/datasets/{dataset_id}/{segment}")`
   ... `items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=(order == "desc"))`
   ... `window = items[offset : offset + limit]`
   So the sort key is applied only to the 1000 the service happened to return first. And the service's own order is unrelated to `created_at`: `app/features/explorer/repo.py:217` — `ORDER BY v.name LIMIT :limit OFFSET :offset`. With 1500 views, `_fetch_all` returns views 1-1000 *by name*; `sort_order='asc'` then reports the oldest of that name-ordered slice as the oldest overall. Confidently wrong, not merely omitted.

3. The count note prints the true total. `context.py:875` — `render.count_note(len(rows), total if isinstance(total, int) else len(items), noun=segment)`, and `app/features/mcp/render.py:71-72` — `if isinstance(total, int) and total > shown: return f"{shown} of {total} {noun} shown."` So a 1500-view dataset renders "50 of 1500 views shown." — exactly the phrasing used everywhere else to mean "a page of the full sorted set".

4. The module already knows how to be honest, in the sibling path. `_scan` returns `exhausted` (context.py:159, 176-184) and its callers use it: `context.py:508-510` sets `total = None` with the comment "reporting it would overstate", and 522-526 emits "Timeline was filtered locally over the {scanned} most recent of {all_events} events". Jobs likewise sets `shown_total = None` on the scanned path (context.py:586). The `SCAN_CAP` docstring (24-31) states the principle: "Scanning a bounded window and reporting the window is honest". `_fetch_all` does neither of the two things `_scan` does — no cap note, and it reports the full total.

5. Nothing makes it deliberate. No test covers the cap for this tool: `grep -rn "_fetch_all\|SCAN_CAP" tests/` hits only `tests/unit/test_mcp_context.py:655` (the timeline scan). The nearest tests (`test_mcp_context.py:1290-1322`) assert the *opposite* intent — "The collector pages the endpoint out and sorts the union" — with totals of 250, well under the cap, so they pass and do not contradict the claim. No mention of the cap in HANDOFF.md or ARCHITECTURE.md.

Bonus, same root cause: `context.py:838` uses `_fetch_all` to find a single quality rule by id (the quality API has no single-rule GET). A rule sorted past position 1000 by the service yields "No quality rule with id {object_id} on this dataset" — a confident not-found for a rule that exists.

The only thing standing between this and everyday breakage is scale: it needs >1000 saved objects of one kind on one dataset. Nothing in the service caps that.

**Fix**

Make `_fetch_all` report truncation like `_scan` does: return a third element `capped: bool` (`len(items) >= cap and (not isinstance(total, int) or total > cap)`). In `list_saved_objects`, when `capped`, pass `total=None` to `render.count_note` (so it prints the honest "N views shown.") and append a note in the style of the timeline one — e.g. `f"Sorted over the first {len(items)} of {total} {segment} the service returned (ordered by name, not date); objects past that window are not represented."` For the rule-by-id branch at line 838, when `capped` and no match, say the rule was not found within the first {len(items)} rules rather than asserting it does not exist.

**Test to pin it**

"test_a_saved_object_list_longer_than_the_scan_cap_says_it_was_capped_instead_of_printing_the_full_total" in tests/unit/ (tests/unit/test_mcp_context.py, alongside test_the_whole_list_is_collected_before_sorting_so_paging_is_exact): stub /datasets/ds-1/views with total=1500 across 200-item pages whose created_at ordering is deliberately unrelated to page order, call list_saved_objects(kind="view", sort_order="asc"), and assert the output does NOT contain "of 1500 views shown." and DOES carry a capped-window note.

## [medium] Quality rules have no GET-one route: only list/create/patch/delete exist, so a single rule can only be fetched by listing all rules.

- **domain:** ?
- **where:** 

**Evidence**

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/quality/api.py declares exactly these rule routes: line 59 `@router.post("/datasets/{dataset_id}/rules"...)`, line 75 `@router.get("/datasets/{dataset_id}/rules", response_model=Page[RuleOut])`, line 84 `@router.patch(".../rules/{rule_id}")`, line 100 `@router.delete(".../rules/{rule_id}")`. A repo-wide grep for `rules/{rule_id}` returns only the PATCH, the DELETE, and one MCP caller (app/features/mcp/tools/curate.py:576, a patch) — there is no GET-one handler anywhere.

The cited workaround is real. app/features/mcp/tools/context.py, in the `object_id` branch, special-cases rules:
  `if kind == "rule":  # The quality API exposes no GET for a single rule, so find it in the list rather than reporting a capability we do not have.` followed by `items, _ = await _fetch_all(ctx, f"/datasets/{dataset_id}/{segment}")` and a client-side `next((r for r in items if r.get("id") == object_id), None)`, while the `else` branch for every other kind does `await ctx.client.get(f"/datasets/{dataset_id}/{segment}/{object_id}")`.

Sibling features do expose GET-one, so this is an inconsistency rather than a house style: app/features/library/api.py:70 `GET /datasets/{dataset_id}/analytics/{definition_id}` and app/features/library/api.py:208 `GET /datasets/{dataset_id}/charts/{chart_id}`.

No deliberate rationale is documented. The quality module docstring only explains RBAC and 404-hides-existence; it says nothing about omitting a rule read. ARCHITECTURE.md:373 actually advertises the opposite shape, `POST/GET/PATCH/DELETE  /datasets/{id}/rules[/{rule_id}]`, which reads as if GET-one exists (the section's "(7)" route count is nonetheless consistent with the four rule routes plus the three validation routes). No test in tests/ asserts a GET-one for rules; the only `rules/{id}` test URLs are patch/delete targets.

Two parts of the claim's impact statement are overstated: `list_rules` is unpaginated (returns every rule with `total=len(items)`), so "refetch the entire ruleset" is one cheap call; and PATCH returns the full `RuleOut`, so post-PATCH refresh does not require a separate read. The deep-link/refresh-on-mount and the missing cheap 404 (deleted-elsewhere vs. wrong-dataset) parts stand.

**Fix**

Add to app/features/quality/api.py, next to the PATCH:

@router.get("/datasets/{dataset_id}/rules/{rule_id}", response_model=RuleOut, tags=["quality"])
async def get_rule(dataset_id: str, rule_id: str, principal: Principal = Depends(get_principal)) -> RuleOut:
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    row = await repo.get_rule(dataset_id, rule_id)   # add if repo lacks it; scope the query by dataset_id so a foreign rule 404s
    if not row:
        raise HTTPException(404, f"Rule not found: {rule_id}")
    return RuleOut(**row)

Then drop the `if kind == "rule"` special case in app/features/mcp/tools/context.py so rules take the same `ctx.client.get(f"/datasets/{dataset_id}/{segment}/{object_id}")` path as every other kind, and fix the ARCHITECTURE.md:373 line's implied route list.

**Test to pin it**

"Getting a single quality rule returns it, 404s for a rule id belonging to another dataset, and 404s for a deleted rule" — integration layer, tests/ (the API-level quality test module that already exercises PATCH/DELETE on /api/v1/datasets/{ds}/rules/{rule_id}).

## [medium] POST /validate returns the same run's results with id always null and in rule created_at order, while GET /validations/{run_id} returns real ids ordered by rule_name.

- **domain:** ?
- **where:** 

**Evidence**

Both halves of the claim are literally true in the source.

(a) /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/quality/api.py:181-183 — `return ValidationDetail(**run, results=[RuleResultOut(**r, id=None) for r in results])`. `results` here are engine dicts from `evaluate_rule` (engine.py:66-80 `_result` returns rule_id/rule_name/rule_type/scope_type/selectors/severity/status/failure_count/message/failure_sample_file — no `id`), so `id=None` is not a placeholder that gets filled in later. The row ids exist in Postgres: repo.complete_run (repo.py:143-181) INSERTs into `validation_rule_results` per result but its INSERT has no RETURNING, so it never reads the generated ids back. The GET path is api.py:215-216 -> repo.list_run_results (repo.py:237-256), which selects `r.id::text` — real ids. RuleResultOut.id is `str | None = None` (schemas.py:92), so pydantic happily serializes the null; nothing rejects it.

(b) POST order: api.py:128 `rules = await repo.list_rules(dataset_id, enabled_only=True)`, and list_rules (repo.py:48-57) ends `ORDER BY created_at`; api.py:145-146 builds `results = [evaluate_rule(rule, ...) for rule in rules]`, preserving that order into the response. GET order: repo.py:251 `ORDER BY r.rule_name`. Two different orders for the same run.

No test contradicts it — tests/test_quality.py:68 keys by `by_name = {res["rule_name"]: res ...}` and :134 matches on rule_name, i.e. the suite already works around the missing ids rather than asserting them. No docstring, comment, HANDOFF.md or ARCHITECTURE.md entry defends `id=None` or the differing ORDER BY (the nearby comments defend webhook isolation and failure-artifact registration, not this), so it is not a documented deliberate choice.

Two things temper the severity, but do not refute the claim: the POST payload does carry `rule_id`, which is unique within a run and is a usable client-side key, and `id=None` is an explicit null rather than a wrong value — the API is inconsistent, not silently wrong.

**Fix**

Make complete_run return the persisted rows and have POST reuse the GET's shape: add `RETURNING id::text` (or a single multi-row INSERT ... RETURNING) in repo.complete_run and attach the id to each result dict; simplest correct version is to drop the hand-built list in api.py:181-183 and end validate_version with `results = await repo.list_run_results(run["id"])` / `RuleResultOut(**r)`, which fixes both the ids and the ordering with one change (it also picks up failure_sample_file resolved via the artifacts join).

**Test to pin it**

"test_validate_response_matches_the_subsequent_get_detail_exactly" — POST the validation, GET /datasets/{id}/validations/{run_id}, assert every result id is non-null in both and that the two `results` lists are equal element-for-element (same order). Belongs in tests/ (integration, needs Postgres + a real version), alongside tests/test_quality.py.

## [medium] PATCH /datasets/{id}/rules/{rule_id} re-checks none of the rule invariants POST enforces, so an edit can persist a rule that only fails at validation time.

- **domain:** ?
- **where:** 

**Evidence**

The claim holds on every hop of the path.

1. Schema. `RuleCreate` carries `@model_validator(mode="after") _check_selectors` (app/features/quality/schemas.py:46-58): sheet_selector required for all types, column_selector required for column/cross_sheet scope, foreign_key requires parameters.ref_sheet+ref_column, accepted_values requires non-empty parameters.values. `RuleUpdate` (schemas.py:61-70) is a bare BaseModel — every field is `X | None = None`, no validator, so `{"parameters": {}}`, `{"column_selector": null}`, `{"sheet_selector": null}` all validate.

2. Route. app/features/quality/api.py:91-94 passes `body.model_dump(exclude_unset=True)` straight to `repo.update_rule` with no re-check; `_resolve_sheet_selector` (api.py:47) only acts `if fields.get("sheet_selector")`, so an explicit null falls through untouched.

3. Repo. `_MUTABLE` (repo.py:71-72) includes sheet_selector, column_selector and parameters, and update_rule writes them verbatim: `params["parameters"] = json.dumps(v or {})` (repo.py:83), plain `f"{k} = :{k}"` for the selectors (repo.py:85-86). The DDL is permissive — sheet_selector and column_selector are nullable TEXT, parameters is `JSONB NOT NULL DEFAULT '{}'` with no CHECK (migrations/20260804030000_quality.sql:24-30). Nothing stops the write.

4. Engine. With `parameters = {}` on accepted_values, engine.py:190-192 computes `values = []`, `placeholders = ""`, `where = "col IS NOT NULL AND col NOT IN ()"` — a DuckDB parse error, swallowed by `evaluate_rule`'s catch-all into `_result(rule, "error", ...)` (engine.py:106-110). column_selector=null → `_physical_column` returns None (engine.py:52-55) → `_result(rule, "error")` at engine.py:146-149. foreign_key with ref_sheet/ref_column gone → engine.py:153-161 error. `status="error"` with `severity="error"` counts into `error_failures` (repo.py:147-148), which hard-blocks promotion at app/features/data_accelerator/api.py:442-448.

5. Deliberate? The opposite. The MCP wrapper's create branch front-loads exactly these checks and even explains why — "range needs parameters with min, max, or both — otherwise the rule errors at validation time" (app/features/mcp/tools/curate.py:536-539) — while its update branch only drops None values and does no invariant check (curate.py:559-576); note `{}` is not None, so `parameters={}` passes through MCP too. No docstring, HANDOFF.md or ARCHITECTURE.md text defends a laxer PATCH (ARCHITECTURE.md:373 just lists the verbs).

6. No test contradicts it. The only PATCH-on-rules test is tests/test_quality.py:122-124, which patches `{"enabled": false}` — it never exercises selectors or parameters.

One overstatement in the claim: nulling `sheet_selector` is NOT always fatal. `_find_sheet` tries `logical_sheet_id` first (engine.py:40-43), and update_rule never clears the stored logical_sheet_id, so a rule pinned to a live logical sheet keeps resolving. It only breaks for rules whose sheet was never resolved to a logical sheet. The parameters/column_selector cases are unconditional. "Permanently" is also rhetorical — a later PATCH can restore the field.

**Fix**

In `update_rule` (app/features/quality/api.py:84-97), fetch the existing row first (repo.get_rule → 404 if missing), merge the `exclude_unset` patch over it, and re-run the create-time invariants against the merged rule_type/scope before calling repo.update_rule — e.g. lift the body of `RuleCreate._check_selectors` into a module-level `validate_rule_invariants(rule_type, sheet_selector, column_selector, parameters)` in schemas.py, call it from both the RuleCreate validator and the PATCH handler, and raise 422 on failure. Same helper should be applied in the MCP update branch (curate.py:559-576).

**Test to pin it**

"test_patch_rule_rejects_edits_that_create_would_reject" — integration layer, tests/test_quality.py: create a valid accepted_values rule, then PATCH `{"parameters": {}}`, PATCH `{"column_selector": null}`, and PATCH a foreign_key rule's parameters to `{}`, asserting 422 on each and that a subsequent validate run still reports error_failures == 0 for those rules.

## [medium] Relationship read routes authorize only the owning dataset, so a cross-dataset edge leaks the target dataset id and its current sheet key.

- **domain:** ?
- **where:** 

**Evidence**

The claim checks out on every leg of the path.

1) Read routes check one side only. app/features/relationships/api.py:129 `await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)` in `list_relationships`, and api.py:142 the identical single check in `get_relationship`, followed by api.py:143 `return RelationshipOut(**await _relationship_or_404(...))`. `_relationship_or_404` (api.py:37-41) only re-checks `row["dataset_id"] != dataset_id`; it never touches `to_dataset_id`.

2) The payload really does carry target-side identity. app/features/relationships/repo.py:17-18 `_COLS` selects `r.to_dataset_id::text, r.to_logical_sheet_id::text, r.to_column, ts.current_sheet_key AS to_sheet`, and repo.py:12-13 documents that this is deliberately the CURRENT name after renames. schemas.py `RelationshipOut` declares `to_dataset_id: str`, `to_logical_sheet_id: str`, `to_sheet: str | None`, `to_column: str` — no filtering, no pydantic gate. `list_relationships` (repo.py:84-101) filters on `r.dataset_id` only.

3) The contrast the claim draws is real and explicit. `_authorized_relationship` (api.py:187-202) exists precisely for this: its docstring says "Checking only the owning side would make a relationship a side channel for reading — or learning the existence of — another team's dataset", and api.py:199-201 re-checks `to_dataset_id` with DATASET_READ. Only /joins/preview, /joins/execute, /joins/{run_id}/publish use it. `create_relationship` also double-checks (api.py:96-97). The GET routes, confirm, reject and delete do not.

4) The module docstring asserts the opposite guarantee for the whole module: api.py:4-6 "A cross-dataset edge additionally requires permission on the TARGET dataset, otherwise a relationship would be a side channel for learning that another team's dataset exists." ARCHITECTURE.md:438-441 repeats "both endpoints carry a dataset id whose permissions are checked independently." So this is a documented invariant that the read routes violate — not a documented tradeoff.

5) No test contradicts it. tests/test_relationships.py:227-248 (`test_a_cross_dataset_relationship_needs_access_to_both_sides`) actually only asserts the outsider gets 404 on the LEFT dataset's list route — it never gives a principal read on the left and not the right. test_relationships.py:251-266 and :269-284 are all same-dataset. So the exact one-sided-permission case is untested.

Reachability: cross-dataset edges arise only from manual POST (service.py:129 and :266 hardcode `to_dataset_id=dataset_id` for both seeding and discovery), so the edge requires someone with read on both at creation time. The test at line 227 shows the natural path — a superuser links a left dataset to another team's dataset; thereafter any plain reader of the left team sees `right`'s UUID and sheet name. A membership change produces the same state.

Impact is bounded (dataset UUID + sheet key + column name, no rows), which is why this is medium and not high.

**Fix**

In `list_relationships` and `get_relationship` (and, for consistency, confirm/reject/delete which return RelationshipOut too), after authorizing the owning dataset, redact or authorize the target: for each row where `row["to_dataset_id"] != dataset_id`, call `ensure_dataset_permission(principal, row["to_dataset_id"], Permission.DATASET_READ)`; on the single-row GET let the 404 propagate, and on the list route either drop the row or null out `to_dataset_id`/`to_sheet`/`to_logical_sheet_id`/`to_column`. Dropping rows is simplest and consistent with 404-hides-existence, but note it makes `total` from repo.list_relationships disagree with the returned page — prefer filtering in the repo query with an authorized-dataset-id subquery so the count stays correct.

**Test to pin it**

tests/test_relationships.py — "test a reader of only the owning dataset cannot see the target side of a cross dataset relationship": superuser creates the cross-team edge as in test_relationships.py:227, then a viewer with membership only in the left team GETs both `/datasets/{left}/relationships` and `/datasets/{left}/relationships/{id}` and must not receive the right dataset's id or sheet key. Integration layer (tests/), since it needs real RBAC and two teams.

## [medium] Quality validation writes failure parquet under the raw path-param dataset_id but registers the artifact under str(ds["id"]), so a non-canonical UUID in the URL orphans the file. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every cited line and the path is reachable.

Writer layout (app/features/quality/api.py:141-143) uses the raw path parameter:
    failures = ArtifactLayout("validation_failures", team_id=str(ds["team_id"]), dataset_id=dataset_id)
Registrar layout (app/features/quality/api.py:230-231) uses the DB row:
    layout = ArtifactLayout("validation_failures", team_id=str(ds["team_id"]), dataset_id=str(ds["id"]))

`dataset_id` is an unconstrained `str` path param (api.py:118-119) — no pydantic/UUID coercion. The only normalisation on the lookup path is app/shared/repo.py:36 `if not is_uuid(dataset_id): return None`, and is_uuid (repo.py:19-31) accepts any form `uuid.UUID()` parses: upper-case, and 32-hex without hyphens. Postgres `WHERE id = :id` (repo.py:40) accepts both forms too, so the dataset resolves and `str(ds["id"])` comes back canonical lower-case hyphenated. _sanitize (app/infra/db/storage.py:39-43) only replaces `[^A-Za-z0-9._-]` — it preserves case and preserves a hyphen-less hex string — so ArtifactLayout.key (storage.py:196-203) yields two different keys.

The mismatch is not cosmetic: engine._persist_table (app/features/data_accelerator/services/sampling.py:563-570) does `key = layout.key(filename); storage.put_file(key, local)` with the *writer* layout, while _register_failure_artifacts (api.py:237-248) stores `layout.key(filename)` from the *registrar* layout as `storage_key`. Reads resolve only through that row: files/api.py:751-755 and 782-792 pass `artifact["storage_key"]` into downloads.read_sample_data, which does `if not storage.exists(key): raise HTTPException(404, f"File not found: {filename}")` (downloads.py:331-333). `size = get_storage().size(key)` at api.py:238-241 already swallows the miss, so size_bytes is silently NULL and the run still returns 200 with a `failure_sample_file` that cannot be opened.

The invariant is explicitly documented, so this is a violation, not a design choice: ArtifactLayout's docstring (storage.py:183-185) — "the key is a pure function of its fields, so the writer and the ownership registration derive the same string" — and evaluate_rule's docstring (app/features/quality/engine.py:101-104) — "*layout* must be the same one the caller will register the failure files under ... a mismatch would leave the parquet unreachable". The rest of the codebase honours this via a single factory, `_output_layout` (app/features/data_accelerator/api.py:118-131), which uses `str(ds["id"])` for both writer and registrar; rowdiff.py:208 likewise normalises with `dataset_id = str(ds["id"])` first.

No test contradicts it: tests/test_control_plane.py:79 and tests/test_health.py:119 exercise validation only with the canonical id returned by the upload endpoint, and `grep -rn "upper()" tests/` finds no test that sends a non-canonical UUID. Nothing in HANDOFF/ARCHITECTURE defends the divergence.

Same divergence exists at app/features/explorer/api.py:336 (writer uses raw `dataset_id`, registrar goes through `_output_layout` → `str(ds["id"])`).

**Fix**

Build the layout once and pass it to both places: at app/features/quality/api.py:141-143 use `dataset_id=str(ds["id"])`, then pass that same `failures` object into `_register_failure_artifacts(results, ds, principal.user_id, layout=failures)` instead of reconstructing it at line 230. (Same one-word fix for the writer at app/features/explorer/api.py:336.)

**Test to pin it**

"test_validation_failure_rows_are_reachable_when_the_url_uuid_is_upper_case" in tests/ (integration): upload inline data, create a not_null rule, POST /api/v1/datasets/{ds.upper()}/versions/1/validate, then assert GET /api/v1/samples/{failure_sample_file}/data returns 200 and the artifact row's size_bytes is not null.

## [medium] build_join_sql never checks a collision-prefixed alias against names already emitted, so a join can emit two identically named output columns. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

All three code observations in the claim are accurate; nothing in the schema, route, or tests contradicts them.

1) Projection order. app/features/relationships/joins.py:119-127:
```
        keep = set(select_columns)
        parts = [p for p, n in zip(parts, names) if n in keep]
        names = [n for n in names if n in keep]
```
`names`/`parts` are rebuilt by filtering the JOIN-output order (left.columns then right non-key columns, joins.py:109-117), so caller order is discarded and duplicates collapse (`keep` is a set, and each source column matches at most once). Pydantic does NOT rescue this: `select_columns: list[str] | None` in app/features/relationships/schemas.py:77-78 has no validator, no order or uniqueness constraint. The only existing test, tests/test_join_builder.py:175-184, passes `["order_id", "tier"]` which is already in join order, so it does not contradict the claim. Note the field is documented only as "Projection over the joined result; null keeps everything" (schemas.py:78) and "Project the joined result down to these output column names" (app/features/mcp/tools/pipeline.py:380) — no order guarantee is promised, so this half is closer to an undocumented-but-defensible projection than a defect.

2) Alias uniqueness is the real defect. joins.py:112-117:
```
        alias = c if c not in left.columns else f"{prefix}_{c}"
        parts.append(f"r.{quote_ident(c)} AS {quote_ident(alias)}")
        names.append(alias)
```
`alias` is never checked against `names` already emitted. With left columns [customer_id(key), tier] and a right sheet whose sheet_key is `orders` and whose columns are [customer_id, tier, orders_tier], the loop emits `r.tier AS orders_tier` and then `r.orders_tier AS orders_tier` — two identically named output columns. The sibling implementation's docstring states the invariant this violates: app/features/data_accelerator/services/aggregation.py:72-74 "prefix any other colliding columns with `{sheet}_` so the joined view has unique names."

3) `column_collisions` really does compare raw names only — app/features/relationships/probes.py: `return sorted({c for c in left_columns if c in right_set and c not in keys})` — so `orders_tier` (present only on the right) is never surfaced as a collision, exactly as claimed.

Downstream consequence, preview path (joins.py:219-224): `rows = [{c: safe_value(v) for c, v in zip(cols, row)} ...]` — the duplicate name collapses in the dict, so one column's values are silently overwritten while `output_columns` still lists the name twice. That is a confident, plausible, wrong response body rather than an error. On the execute path (joins.py:276 `CREATE TABLE join_out AS {sql}`) DuckDB rejects duplicate output column names, so that path fails loudly and the run/job are failed correctly. And `select_columns` containing that name keeps both parts (`keep` is a set), i.e. genuinely ambiguous.

Reachability is narrow (requires a right sheet column literally named `{sheet_key}_{leftname}`), which is why this is medium, not high.

**Fix**

In build_join_sql, track emitted names in a set and disambiguate until unique (e.g. `alias = f"{prefix}_{c}"` if `c` collides, then suffix `_2`, `_3`... while `alias in seen`), raising or reporting the extra alias in `column_collisions`. Separately, if caller-specified projection order should be honoured, build the output from `select_columns` order: `order = {n: i for i, n in enumerate(select_columns)}` and sort/emit `parts`/`names` by it after the unknown-column check (and reject duplicate entries in `select_columns` with the existing 400 rather than silently deduping).

**Test to pin it**

"test_a_right_column_named_like_a_prefixed_alias_does_not_produce_duplicate_output_columns" in tests/unit/ (pure build_join_sql unit test over two fake JoinSides — asserts len(set(names)) == len(names)); pair it with an integration test in tests/ that previews such a workbook and asserts every name in output_columns appears as a distinct key in the preview rows.

## [medium] lineage_graph reports truncated:true whenever the deepest edge sits exactly at max_depth, even when that edge is terminal and the DAG is complete. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every point I could check.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/library/repo.py:554 — `"truncated": any(e["depth"] >= max_depth for e in edges),`

The recursive CTE's recursive terms are gated by `WHERE up.depth < :max_depth` (repo.py:512) and `WHERE down.depth < :max_depth` (repo.py:526), so rows with `depth == max_depth` are fully materialised and returned; they are *included*, not cut. Nothing in the query looks one level past the cap, so the code has no evidence at all about whether the DAG continues — it infers continuation from an edge that was successfully returned. Concretely: a two-node chain A -> B queried at B with max_depth=1 yields exactly one edge at depth 1, no further parents or children exist, and `truncated` is still True. The same false positive occurs at the default max_depth=10 for any complete 10-hop chain.

The flag is documented as meaning the opposite of "cap coincidentally equals graph depth":
- schemas.py:211-212 — `truncated: bool = Field(default=False, description="The depth cap was reached; the DAG continues")`
- app/features/mcp/tools/context.py:405-407 — emits, to the model, "truncated=true means the depth cap was hit and the DAG continues beyond these nodes — raise depth (max 25) to see further." Raising depth on a complete graph returns an identical graph, which is exactly the dead-end affordance the claim describes.

No test contradicts it. tests/test_lineage_graph.py:69-77 (`test_depth_can_be_capped`) builds a genuine 2-hop chain, requests max_depth=1 and asserts `truncated is True` — a genuinely truncated case, so it passes under both the current and the correct implementation. tests/test_lineage_graph.py:57 asserts False for a 2-hop chain at the default cap of 10, also consistent with both. There is no test for a graph whose true depth equals the requested cap, i.e. the exact boundary is untested.

Not by design: the repo.py:488-497 docstring explains the depth cap and the cycle guard but says nothing about `truncated` semantics, and grep over HANDOFF.md/ARCHITECTURE.md turns up no discussion of lineage truncation (the only HANDOFF hit for `truncated` is the unrelated aggregation row cap at HANDOFF.md:427). The route handler (api.py:303-322) just passes the flag through; `max_depth` is validated `ge=1, le=25` so nothing upstream changes the arithmetic. The `MIN(depth)` grouping in the SELECT only shrinks reported depths for multi-path edges; it does not fix the boundary case.

**Fix**

Walk one hop past the cap and use the overflow as the signal instead of the boundary. Bind `max_depth + 1` to the CTE, then in Python partition the result: `edges = [e for e in rows if e["depth"] <= max_depth]` and `truncated = any(e["depth"] > max_depth for e in rows)`. Node ids must be collected from the filtered `edges`, not from `rows`, or the probe hop leaks extra nodes into the response.

**Test to pin it**

"test_a_graph_that_ends_exactly_at_the_depth_cap_is_not_truncated" — build the 2-hop chain from the existing `_chain` helper, request `.../lineage/graph?max_depth=2` on the leaf, and assert both edges are present and `truncated is False`; belongs in tests/test_lineage_graph.py alongside `test_depth_can_be_capped` (integration layer, since it needs real lineage rows).

## [medium] GET /analytics and GET /charts accept no limit/offset and emit a synthesized envelope whose limit=len(items) (0 when empty), a value the shared pagination dependency rejects.

- **domain:** ?
- **where:** 

**Evidence**

Every cited line reads as claimed, and nothing downstream rescues it.

1) `app/features/library/api.py:59-67` — `list_definitions(dataset_id, principal)` has NO `page: PageParams = Depends(pagination)` parameter, calls `repo.list_definitions(dataset_id)`, and returns `Page(items=items, total=len(items), limit=len(items), offset=0)`. `app/features/library/repo.py:40-47` is `SELECT ... FROM analytics_definitions WHERE dataset_id = :did ORDER BY name` with no LIMIT/OFFSET — genuinely unbounded.

2) `app/features/library/api.py:197-205` — `list_charts` is identical: no pagination dependency, `Page(..., limit=len(items), offset=0)`; `repo.list_charts` (repo.py:430-437) is an unbounded `SELECT ... FROM chart_definitions`.

3) The `limit=0` inconsistency is real. `app/api/pagination.py:19-25` — `Page.limit: int` has no constraint, so limit=0 serializes fine; but `app/api/pagination.py:40-45` — `limit: int = Query(50, ge=1, le=200)` rejects 0 on every properly-paginated endpoint. The same file's own contract (`app/features/library/api.py:124-136` `list_runs`) does it correctly via `Page.of(..., page)`. So within one module, two list endpoints emit an envelope value the shared dependency forbids.

4) `app/features/library/schemas.py:97-100` — `class LineageResponse: dataset_id: str; parents: list[dict[str, Any]]; children: list[dict[str, Any]]`. `repo.get_lineage` (repo.py:361-394) confirms the two arrays are different shapes: parents select `dataset_version_id, version_number, parent_dataset_id, parent_version_id, parent_dataset_name, parent_version_number, parent_sheet_key, relation`; children select `child_dataset_id, child_dataset_name, child_version_id, child_version_number, parent_version_number, parent_sheet_key, relation`. Neither is described in OpenAPI.

Refutation attempts that failed:
- No Pydantic/route-level guard exists; there is no `limit` query param at all to validate.
- No test contradicts it: `grep -rn "limit" tests/test_library.py` returns nothing, and there is no `analytics?limit`/`charts?limit` test anywhere.
- No deliberate-design note: grep of ARCHITECTURE.md/HANDOFF.md/ROADMAP.md for pagination turns up only `ROADMAP.md:285` ("No cursor pagination exists anywhere. `Page[T]` is offset/limit only"), which does not bless unpaginated list endpoints or a limit=0 envelope.

Mitigating facts the claim understates: the `limit=len(items)` synthesis is a repo-wide convention, not a library-specific slip — it appears identically at `app/features/quality/api.py:81`, `app/features/auth/api.py:120` and `:132`, `app/features/data_accelerator/api.py:78`, `app/features/discovery/api.py:252` and `:364`. And for lineage, a fully typed alternative already exists: `GET /datasets/{id}/lineage/graph` (api.py:303-322) returns `LineageGraphResponse` with typed `LineageNode`/`LineageEdge`, so the UI is not forced to reverse-engineer shapes for the DAG view — only for the immediate-parents/children view. Because the endpoints return the complete collection, the UI can render every screen correctly; the harm is unbounded response size plus a self-contradictory envelope, not wrong data.

**Fix**

Add `page: PageParams = Depends(pagination)` to `list_definitions` and `list_charts`, push `LIMIT/OFFSET` plus a `COUNT(*)` into `repo.list_definitions` / `repo.list_charts` (returning `(rows, total)` like `repo.list_runs` already does), and return `Page.of(items, total, page)`. That removes the limit=0 case by construction. Separately, replace `LineageResponse.parents/children: list[dict[str, Any]]` with two explicit models, e.g. `LineageParent{id, dataset_version_id, version_number, parent_dataset_id, parent_version_id, parent_dataset_name, parent_version_number, parent_sheet_key, relation, created_at}` and `LineageChild{id, child_dataset_id, child_dataset_name, child_version_id, child_version_number, parent_version_number, parent_sheet_key, relation, created_at}`, matching the two SELECT lists in repo.get_lineage. The same Page.of fix should be applied to the five other sites using the `limit=len(items)` idiom.

**Test to pin it**

"test_listing_definitions_and_charts_honours_limit_and_offset_and_never_reports_limit_zero" in tests/ (integration layer — it needs a dataset with more saved definitions/charts than one page plus an empty-dataset case asserting `limit >= 1`), paired with a unit test "test_lineage_response_schema_declares_typed_parent_and_child_rows" in tests/unit/ asserting the generated OpenAPI component for LineageResponse has named properties rather than free-form objects.

## [medium] Tag rollback searches only the newest 100 history rows, so a tag with 100+ consecutive same-target entries 409s "No previous version" though one exists.

- **domain:** ?
- **where:** 

**Evidence**

app/features/data_accelerator/api.py:484-493 is exactly as cited: `history, _ = await repo.list_tag_history(dataset_id, tag_name, limit=100)` then `next((h["to_version_number"] for h in history if ... != current["version_number"] and h["action"] != "delete"), None)` -> `raise HTTPException(409, f"No previous version in history for tag '{tag_name}'")`. The search is over the fetched page only; there is no loop/offset paging and no SQL-side "first differing" predicate.

repo.list_tag_history (repo.py:162-185) is a plain `ORDER BY id DESC LIMIT :limit OFFSET :offset` over dataset_tag_history; id is BIGSERIAL (migrations/20260804000000_sheets.sql:94), so ordering is genuinely newest-first — the cap is the only limiting factor.

The failure precondition is reachable: nothing dedupes a no-op promote/set. promote_tag (api.py:414-457) checks only permission, dataset ownership, ready status and the quality gate, then calls repo.set_tag unconditionally; repo.set_tag (repo.py:91-132) always appends a history row via _record_tag_history. So promoting `production` to the same version N times writes N rows all with the same to_version_number. Delete rows (to_version_number NULL, action 'delete') are skipped by the filter but still consume slots in the 100-row page, so a delete/re-set cycle burns 2 rows per cycle (~50 cycles to trigger).

Not documented as deliberate: HANDOFF.md:561 describes rollback only as "walks history to previous distinct version" — no mention of a bounded window; ARCHITECTURE.md:334 just lists the route. The one nearby comment (api.py:431-432) documents the gating policy, not any history cap.

No test contradicts it: tests/test_sheets_and_tags.py:169-197 (test_tag_promote_rollback_history) exercises exactly three history entries; test_tag_promote_validation_and_rollback_guard (line 200+) covers 404/400 promote guards only. grep for "rollback" across tests/ finds no longer-history case.

Severity is medium, not high: the trigger requires >100 consecutive history rows all pointing at the currently-tagged version, which needs repeated idempotent promotes/sets (or ~50 delete+set cycles) with no intervening move to a different version — plausible for CI-driven re-promotion but not the normal promote-new-version rhythm. It also fails loudly (409) rather than silently returning a wrong rollback target.

**Fix**

Resolve the target in SQL instead of scanning a page: add a repo helper, e.g. `SELECT to_version_number FROM dataset_tag_history WHERE dataset_id = :did AND tag_name = :tag AND action <> 'delete' AND to_version_number IS NOT NULL AND to_version_number <> :current ORDER BY id DESC LIMIT 1`, and have rollback_tag call that in place of list_tag_history(limit=100) + the `next(...)` walk. (A paging loop would also work but the single query is exact and O(1) with an index on (dataset_id, tag_name, id DESC).)

**Test to pin it**

"test_rollback_finds_previous_version_beyond_the_first_history_page" — integration layer, tests/test_sheets_and_tags.py: promote to v1, then promote to v2 ~120 times (or insert 120 same-target history rows via repo.set_tag), then assert POST /tags/production/rollback returns 200 with to_version_number == 1 rather than 409.

## [medium] GET /datasets/{id}/tags/{tag} always returns null sheet_count/source_checksum/manifest_checksum although the row it read has them. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every checked link of the path.

1. Schema: `app/features/data_accelerator/schemas.py:43-63` — `VersionInfo` declares `sheet_count`, `source_checksum`, `manifest_checksum`, all `| None = None`, and the docstring treats `manifest_checksum` as "the version's content identity". Nullable defaults mean an omitted field serializes as `null` with no error.

2. Tag resolver: `app/features/data_accelerator/api.py:526-545` — `resolve_tag` hand-builds the model:
   `return VersionInfo(id=..., version_number=..., status=..., size_bytes=..., row_count=..., checksum=..., created_at=..., processed_at=..., tags=tags)`
   The three fields are simply absent from the constructor call, so they default to `None` on every response.

3. The data IS available at that point: `app/shared/repo.py:352-367` `get_version_by_tag` does `SELECT dv.*, t.tag_name FROM dataset_version_tags t JOIN dataset_versions dv ...`, so `ver` already carries all three columns. They are dropped in the handler, not missing from the query.

4. Contrast: `app/features/data_accelerator/repo.py:434-445` `list_versions` explicitly selects `dv.sheet_count, dv.checksum, dv.source_checksum, dv.manifest_checksum`, and `api.py:250-255` splats them straight in via `VersionInfo(**r)`. Same response_model, populated.

5. The columns are real and populated in normal flow: `app/infra/db/postgres/migrations/20260804020000_schema_hardening.sql:29` adds `sheet_count`, and `app/features/files/repo.py:98-126` writes `sheet_count`, `source_checksum`, `manifest_checksum` at processing completion (`services/processing.py:262`, `services/replace.py:130-134`).

6. No test contradicts it: `grep -rn "sheet_count" tests/` returns zero hits anywhere, so nothing pins either endpoint's value.

7. Not deliberate: no comment or docstring at the call site explains the omission, and HANDOFF.md's tag section (lines 560-561) documents promote/rollback/history only, never a reduced tag payload. The remaining eight fields are copied one-for-one, which reads as an oversight when the three hardening columns were added, not a deliberate projection.

**Fix**

In `resolve_tag` (app/features/data_accelerator/api.py:535-545) add the three missing kwargs, mirroring the existing `.get()` style:
    sheet_count=ver.get("sheet_count"),
    source_checksum=ver.get("source_checksum"),
    manifest_checksum=ver.get("manifest_checksum"),
(Do not switch to `VersionInfo(**ver)`: the row carries a datetime `created_at` and an extra `tag_name`, so the explicit build is still the right shape.)

**Test to pin it**

"test_resolve_tag_returns_the_same_version_fields_as_the_versions_list" — an integration test in tests/ (needs a real processed version so sheet_count/source_checksum/manifest_checksum are non-null): upload+process a multi-sheet dataset, tag it, then assert the GET /datasets/{id}/tags/{tag} body equals the matching entry from GET /datasets/{id}/versions field-for-field.

## [medium] Lineage node/children queries return other teams' dataset names with no team filter, leaking existence the direct routes hide with 404.

- **domain:** ?
- **where:** 

**Evidence**

The SQL in the claim is exactly as described. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/library/repo.py:544-548 — `SELECT id::text, name, domain, deprecated, created_at ... FROM datasets WHERE id = ANY(:ids)` where `ids = {dataset_id} | {e["child_id"]} | {e["parent_id"]}` (repo.py:541-543): every dataset touched by the recursive CTE is named, with no team predicate and no principal in scope (the repo function signature is `lineage_graph(dataset_id, max_depth)` only). repo.py:377-392 — the children query `JOIN datasets d ON d.id = l.dataset_id WHERE l.parent_dataset_id = :did` likewise selects `d.name` unfiltered; the parents branch (repo.py:365-376) returns the denormalized `l.parent_dataset_name` with no check either.

The route layer adds no compensating filter: app/features/library/api.py:153-162 and api.py:304-322 both call only `ensure_dataset_permission(principal, dataset_id, DATASET_READ)` on the ENTRY dataset, then hand the whole graph to the response model. LineageNode carries name/domain/deprecated straight through.

The premise is reachable today, not hypothetical. Cross-team lineage rows are constructible: app/features/relationships/api.py:95-97 lets `to_dataset_id` be a dataset in a different team as long as the caller has DATASET_READ on it (multi-team member or superuser — `principal.memberships` is a set, deps.py:142), and app/features/relationships/joins.py:344-366 publishes the join into the LEFT dataset's team while writing an `extra_lineage` row naming the right-hand (other-team) dataset via `publish_artifact_as_version` (service.py:322-331). A single-team member of the left team then gets 404 on GET /datasets/{right_id} (deps.py:135-144, documented "we never leak the existence of other teams' datasets") but sees that dataset's name/domain/deprecated in /lineage and /lineage/graph.

Deliberateness cuts the other way: app/features/relationships/api.py:188-192 says in so many words "Checking only the owning side would make a relationship a side channel for reading — or learning the existence of — another team's dataset", and HANDOFF.md:867 states "Cross-team existence hiding (404 not 403) must extend to all new endpoints." Nothing in HANDOFF.md (150-151, 499-500) or ARCHITECTURE.md (291-294) carves lineage out.

No test contradicts the claim: every lineage test (tests/test_lineage_graph.py, test_library.py:85-91, test_transformations.py:338-343, test_join_builder.py:228-230, test_pivot.py:208) is single-team single-principal; grep for "cross-team" in tests/ finds no lineage case.

**Fix**

Thread the caller's visible teams into both queries. Change `lineage_graph(dataset_id, *, max_depth, team_ids: list[str] | None)` and `get_lineage(dataset_id, *, team_ids)` (None = superuser, no filter); add `AND (:team_ids IS NULL OR d.team_id = ANY(:team_ids))` to the nodes SELECT and the children JOIN, and drop any edge whose endpoint is not in the surviving node set so the graph does not expose an anonymous-but-present neighbour. For parents, join `datasets` on `l.parent_dataset_id` and null out `parent_dataset_name`/`parent_dataset_id` when the parent's team is not visible (the denormalized label must not survive the filter). Callers pass `sorted(principal.memberships)` or None when `principal.is_superuser`.

**Test to pin it**

"test_lineage_does_not_name_a_parent_dataset_in_a_team_the_caller_cannot_read" in tests/ (integration): admin in both teams creates a cross-team relationship and publishes a join into team A; a team-A-only user gets 404 on GET /datasets/{team_b_id} and must see neither the team-B id nor its name in GET /datasets/{child}/lineage or /lineage/graph.

## [medium] GET /datasets accepts any string for validation_status/documentation and returns an empty page with total 0 instead of 422. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim's file:line citations are exact and the code does what it says.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/api.py:188-193 declares the three filters with NO enum/Literal and no pattern:
  `validation_status: str | None = Query(None, description="Filter by validation status: passed | failed | none")`
  `has_schema_drift: bool | None = Query(None, ...)`  <- correctly typed bool, as the claim concedes
  `documentation: str | None = Query(None, description="Filter by documentation completeness: full | partial | none")`
The vocabulary exists only in the description string, so FastAPI/pydantic performs no validation; anything non-empty passes through.

repo.py:232-240 appends a raw equality predicate:
  `if validation_status: clauses.append("sig.validation_status = :vstatus")`
  `if documentation: clauses.append("sig.documentation = :doc")`
The compared column is the CASE expression in discovery/repo.py::signals_lateral() (app/features/discovery/repo.py:50-80), whose domain is exactly {'passed','failed','none'} and {'full','partial','none'}. So `validation_status=Passed` or a stale `documented` chip matches nothing: COUNT(*) is 0 and the endpoint returns HTTP 200 with an empty Page and total 0. Bound parameters mean there is no injection risk — the only defect is the missing 422.

Not deliberate: the repo demonstrably knows the correct pattern elsewhere — app/features/files/api.py:767 uses `sort_order: Literal["asc", "desc"] = Query(...)`. Nothing in HANDOFF.md §18 (lines 318-327) or ARCHITECTURE.md documents a permissive-filter choice; §18 only says the filters exist and reuse the health signals.

No test contradicts the claim. tests/test_catalog_facets.py:91-101 only exercises valid values (`validation_status="passed"`, `documentation="full"/"none"`, `has_schema_drift=True`) and its `_list` helper hard-asserts status_code == 200; there is no 422 assertion anywhere in that file and no invalid-value case at all.

Secondary nit in the same lines: `if validation_status:` / `if documentation:` are truthiness checks, so `?documentation=` (empty string) is silently treated as "no filter" rather than an unmatched value.

The same unvalidated strings are relayed by the MCP tool at app/features/mcp/tools/orient.py:75-92 (`validation_status: Annotated[str | None, Field(description="One of: passed, failed, none.")]`), where a model that guesses "Passed" gets "No datasets matched." plus a misleading hint about team invisibility.

**Fix**

In app/features/data_accelerator/api.py:188-193 change the two string params to closed enums, matching the existing precedent at app/features/files/api.py:767: `validation_status: Literal["passed", "failed", "none"] | None = Query(None, ...)` and `documentation: Literal["full", "partial", "none"] | None = Query(None, ...)`. That yields FastAPI's standard 422 naming the permitted values, needs no repo change (the predicates stay as-is), and the truthiness checks in repo.py:232/238 become safe because empty string is no longer accepted. Mirror the Literals on the MCP wrapper at app/features/mcp/tools/orient.py:75-81 so the tool schema advertises the vocabulary to the model.

**Test to pin it**

"test_invalid_catalog_signal_filter_values_are_rejected_with_422" in tests/test_catalog_facets.py (integration layer, tests/) — assert GET /api/v1/datasets?validation_status=Passed and ?documentation=documented each return 422, while the existing valid-value filter assertions still return 200.

## [medium] Malformed Upload-Metadata base64 in TUS create escapes as binascii.Error/UnicodeDecodeError and becomes a generic 500 instead of a 400.

- **domain:** ?
- **where:** 

**Evidence**

app/features/files/services/tus.py:36 — `val = base64.b64decode(parts[1]).decode() if len(parts) > 1 else ""` — inside `parse_tus_metadata` (lines 28-38) there is no try/except and no `validate=`/`errors=` guard. Verified in a Python shell that `base64.b64decode('YS5jc3Z')` raises `binascii.Error: Incorrect padding` and `base64.b64decode('/w==').decode()` raises `UnicodeDecodeError`.

Call site app/features/files/api.py:391 — `metadata = parse_tus_metadata(request.headers.get("Upload-Metadata", ""))` inside `tus_create`; the whole handler (376-429+) has no try/except around it. The only thing downstream is the global catch-all app/api/errors.py:136-140 `_unhandled_exception_handler`, which logs and returns `problem_response(500, "An unexpected error occurred.", ...)`. So the client gets an opaque 500 with no hint that the header was the problem, exactly as claimed.

No pydantic model is involved — these are raw `request.headers` reads on a `Request`, so nothing validates them earlier.

No test contradicts it: grep of tests/ for `Upload-Metadata` finds only well-formed values (`tests/test_uploads_tus.py:30` via `_meta()`, `tests/test_files_misc.py:107` `"filename YS5jc3Y="`). tests/test_uploads_tus.py:142-148 covers only missing Upload-Length and unsupported extension.

Not deliberate: nothing in HANDOFF.md/ARCHITECTURE.md excuses it (ARCHITECTURE.md:638 only documents that TUS staging is local-disk-only). The repo's own convention is the opposite — app/shared/query/compile.py:94-102 `decode_cursor` docstring says "any malformation → problem+json 400 ``invalid-cursor``" and catches `(binascii.Error, ValueError, UnicodeDecodeError, AttributeError)`.

Two caveats on the claim's framing: (a) the impact line's "any non-ASCII filename encoded with a different scheme" is overstated — a correctly base64'd UTF-8 non-ASCII filename decodes fine; only genuinely malformed/non-UTF-8 headers trip it; (b) the same class of bug sits one line above at api.py:386 `total_size = int(upload_length)`, where a non-numeric Upload-Length raises ValueError → 500 as well. Severity is medium rather than high because only a misbehaving client can reach it and no data is corrupted.

**Fix**

Wrap the decode in parse_tus_metadata and raise a 400 instead of letting it escape:

    try:
        val = base64.b64decode(parts[1]).decode() if len(parts) > 1 else ""
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "Malformed Upload-Metadata header") from exc

(`HTTPException` is already imported at tus.py:12.) Optionally give the same treatment to `int(upload_length)` at app/features/files/api.py:386.

**Test to pin it**

"test_tus_create_rejects_malformed_upload_metadata_with_400" in tests/test_uploads_tus.py (API layer), POSTing /tus/ with `Upload-Metadata: filename YS5jc3Z` and with a base64 of non-UTF-8 bytes, asserting 400 problem+json rather than 500; plus a unit test of parse_tus_metadata in tests/unit/.

## [medium] GET /storage/usage is authenticated but unguarded and un-scoped, returning platform-wide byte totals to any user while its two siblings are superuser-only.

- **domain:** ?
- **where:** 

**Evidence**

The claim's factual assertions all hold in source.

app/features/files/api.py:828-831 — the handler takes no principal and no team at all:
```
@router.get("/storage/usage", response_model=StorageUsageResponse, tags=["storage"])
async def storage_usage() -> StorageUsageResponse:
    """Get storage usage breakdown by category."""
    return await get_storage_usage()
```
Its two siblings do guard: api.py:844-845 `if not principal.is_superuser: raise HTTPException(403, "Retention policy is restricted to platform administrators")` and api.py:864-865 the same for POST /storage/gc, with a docstring saying "Platform-wide housekeeping, so platform admins only — the numbers span every team."

The numbers really are platform-wide. app/features/files/services/management.py:80-114 sums `storage.list_sizes("datasets")`, then walks the entire ARTIFACT_ROOT prefix (`artifacts/{team}/{dataset}/{kind}/...`) with no team filter — every team's bytes land in samples_bytes/exports_bytes — plus every file in the shared uploads_dir(). So GET /storage/usage returns exactly the same class of cross-tenant aggregate that /storage/retention is explicitly restricted for.

Authentication (only) is enforced: app/main.py:146-153 mounts files_router under `protected = APIRouter(dependencies=[Depends(get_principal)])` with the comment "The data plane sits behind a blanket authentication guard; individual routes then enforce team-scoped RBAC." /storage/usage is one of the routes that never does the second half. get_principal (app/features/auth/deps.py:47-73) 401s on a missing/unknown X-User-Id, so it is not anonymous — matching the claim's wording "no authorization beyond authentication".

No test contradicts it: the only coverage is tests/test_files_misc.py:29-41, which calls the endpoint as `admin_id` and asserts the byte totals move after an upload; it never asserts a non-superuser is rejected or that totals are team-scoped. No docstring, comment, ARCHITECTURE.md (§Downloads & storage, lines 470-481 discuss only the list_sizes/HEAD perf fix) or HANDOFF.md (line 107, same perf note) defends the missing guard, so this is not a documented deliberate choice like the 404-not-403 rule. The endpoint is not exposed as an MCP tool, which limits blast radius.

Severity is medium rather than high: what leaks is four aggregate byte counters, not tenant data or names, and no UI contract promises team scoping.

**Fix**

Add `principal: Principal = Depends(get_principal)` to `storage_usage` and gate it the same way as its neighbours: `if not principal.is_superuser: raise HTTPException(403, "Storage usage is restricted to platform administrators")`, with a docstring noting the totals span every team. If a per-team widget is actually wanted, that is a separate endpoint that filters the ARTIFACT_ROOT scan by `artifacts/{team_id}/` and sums only datasets owned by the caller's team — do not silently rescope the platform number.

**Test to pin it**

"test_storage_usage_is_restricted_to_platform_administrators" — asserts a non-superuser member gets 403 and a superuser gets 200; belongs in tests/test_files_misc.py (integration layer, beside the existing test_storage_usage_accounts_for_data).

## [medium] GET /tus/{upload_id}/status reports status="uploading" for a fully processed upload whenever the in-memory processing_status entry is gone. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/files/api.py:590-606 — `tus_upload_status` loads only the on-disk TUS meta, then: `if not version_id or version_id not in processing_status: return UploadResponse(..., status="uploading", message=f"Upload in progress: {meta['offset']}/{meta['total_size']} bytes")`. There is no DB read at all in this branch; the only DB touch in the whole handler is absent (no `repo.get_version`).

`processing_status` is a bare module-level dict — app/features/files/services/processing.py:36-38 "In-memory status for fast polling (supplements the jobs table). processing_status: dict[str, dict[str, Any]] = {}". Nothing rehydrates it; it is only written in-process (api.py:191, api.py:550, processing.py:142/233/292).

The TUS meta file itself IS durable (services/tus.py:56-67 save/load via `uploads_dir()/{id}.meta.json`) and is NOT deleted on successful completion (api.py:547-566 sets processing_status, calls save_tus_meta, schedules the background task; only `tus_terminate` and `cleanup_stale_uploads` delete it). So after a restart the endpoint still returns 200 with the stale "uploading" answer — with offset == total_size — rather than 404, for up to TUS_UPLOAD_EXPIRY_SECONDS (7 days, constants.py:23) and only if the GC ever runs.

The sibling endpoint does exactly what the claim says: api.py:318-341, docstring "The in-memory cache is fastest, but the DB is authoritative — status survives process restarts and works across instances", with `status_map = {"ready": "complete", "failed": "error", "uploading": "processing"}` fallback from `repo.get_version`. Pinned by tests/test_ops.py:8-21 `test_upload_status_survives_restart`, which pops the cache entry and asserts status=="complete".

No test contradicts the claim: tests/test_uploads_tus.py exercises /tus/{id}/status only in-process (line 66-67 asserts "complete" while the cache is warm; line 120-121 asserts "uploading" mid-upload, which is correct there). No test wipes processing_status for the TUS path.

Not documented as deliberate: HANDOFF.md:518 scopes the ops hardening to "GET /upload/status/{id} answers from the DB when the in-memory cache is gone (restart-safe)" and says nothing about the TUS variant; ARCHITECTURE.md:638 marks "TUS staging on S3 … local-disk-only by design", which is about byte staging, not status durability.

**Fix**

In `tus_upload_status`, before returning the "uploading" default, do what `upload_status` does: when `version_id` is set but missing from `processing_status`, load `ver = await repo.get_version(version_id)` and, if present and `ver["status"] != "uploading"`, map through the same `{"ready": "complete", "failed": "error", "uploading": "processing"}` table and return path/size/row_count/error from the row. (Factor the mapping into one shared helper so the two endpoints cannot drift again.) Separately worth noting: this handler takes no `Principal` and does no `ensure_dataset_permission`, unlike its sibling — out of scope for this claim but adjacent.

**Test to pin it**

"test_tus_status_survives_restart" — integration layer, tests/test_uploads_tus.py: complete a TUS upload, pop the version_id from `processing_status`, then GET {location}/status and assert status == "complete" with row_count > 0 (mirrors tests/test_ops.py::test_upload_status_survives_restart).

## [medium] POST /datasets/{id}/sheets/{sheet}/replace is annotated `-> dict`, so its six response fields are untyped in OpenAPI.

- **domain:** ?
- **where:** 

**Evidence**

CONFIRMED part — the response type. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/api.py:628-644:

  @router.post("/datasets/{dataset_id}/sheets/{sheet_name}/replace", tags=["sheets"])
  async def replace_sheet_endpoint(...) -> dict:
      ...
      return await replace_sheet(ds, sheet_name, file, principal)

No `response_model=`, and the return annotation is bare `dict`, which FastAPI renders as an untyped object schema. The service really does return six fields — app/features/files/services/replace.py:164-171 returns dataset_id, version_id, version_number, replaced_sheet, reused_sheets, row_count — and none of them appear in the OpenAPI schema. There is no Pydantic model for this payload anywhere: grep for `reused_sheets`/`replaced_sheet` in app/features/files/schemas.py and app/shared/schemas.py returns nothing. This route is the only JSON-returning route in the file without a typed contract; api.py:114, 318, 590, 719, 796, 828, 834, 854 all carry either `response_model=` or a Pydantic return annotation (the only other untyped ones, api.py:651 and 676, are byte-stream downloads where that is correct). No test asserts the OpenAPI shape (tests/test_advanced.py:62,91 only assert runtime JSON), and no docstring/comment/HANDOFF.md/ARCHITECTURE.md text explains the omission — ARCHITECTURE.md:329 and HANDOFF.md:513 document the route as a first-class feature, so this is oversight, not design.

Also confirmed, minor: the route is absent from the module docstring inventory (api.py:7-27) — but so are POST /samples/{filename}/export, GET /storage/retention and POST /storage/gc, so the inventory is generally stale rather than this route being singled out.

REFUTED part — the `/`-in-sheet-name trap. The claim says "`/` reaches here from sheet_key normalisation". It cannot. app/shared/data_io.py:179: `key = re.sub(r"[^a-z0-9]+", "_", sheet_name.lower()).strip("_")` — every non-alphanumeric character, `/` included, is collapsed to `_`, so a sheet_key can never contain `/`. The claim itself concedes `/` is illegal in Excel sheet names. `[`, `]`, spaces and unicode in a raw sheet name are all valid path-segment content once percent-encoded by any standard HTTP client, and _find_sheet (replace.py:49) matches on the stored name/key, so there is no undocumented escaping rule beyond ordinary URL encoding.

**Fix**

Add a `SheetReplaceResponse` model to app/features/files/schemas.py with dataset_id: str, version_id: str, version_number: int, replaced_sheet: str, reused_sheets: list[str], row_count: int; set it as `response_model=` on api.py:628 and change the return annotation from `dict` to it. Optionally add the route line to the api.py:7-27 docstring inventory alongside the other three missing routes.

**Test to pin it**

"test_sheet_replace_response_is_typed_in_openapi" — fetch /openapi.json and assert the 200 schema for POST /api/v1/datasets/{dataset_id}/sheets/{sheet_name}/replace resolves to a component with version_number and reused_sheets properties. Belongs in tests/ (integration, needs the app's OpenAPI generation), alongside the existing tests/test_advanced.py replace coverage.

## [medium] An abandoned TUS upload leaves its dataset_versions row stuck at status 'uploading' forever; the 7-day sweep deletes only the disk files. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and I could not find any compensating path.

1. POST /tus/ creates a real DB row before a single byte arrives — /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/api.py:420-426: `version = await repo.create_version(ds_id, storage_type="temp", status="uploading", size_bytes=total_size, source={"type": "tus_upload", ...})`, and its id is only recorded on disk in the meta JSON (api.py:441 `"version_id": str(version["id"])`).

2. cleanup_stale_uploads is purely filesystem — /home/saketh/.../app/features/files/services/tus.py:97-113 loops meta files and calls `delete_tus_upload(upload_id, meta)` (tus.py:70-79 unlinks data + meta + lock). The module's entire import list is base64/json/os/time/pathlib/typing + fastapi.HTTPException + `uploads_dir` + constants (tus.py:1-15) — no repo, no DB session, and the function is sync `def`, so it could not await a repo call anyway. TUS_UPLOAD_EXPIRY_SECONDS = 7*24*3600 (app/shared/constants.py:23), as claimed.

3. Only the explicit client cancel closes the row: DELETE /tus/{id} does `await repo.fail_version(version_id, "Upload cancelled by client")` (api.py:583-585). An upload that is simply abandoned never reaches that path.

4. No other sweep exists. `grep -rn "uploading" app/` returns only creation sites (files/api.py:167,256,423,603; files/repo.py:59; files/services/replace.py:55; library/service.py:280) and a status map — nothing that reconciles old `uploading` rows. app/features/files/services/retention.py contains no `status` reference at all. `grep -rn cleanup_stale_uploads tests/` returns nothing, so no test asserts either behaviour.

5. The impact claim holds. GET /api/v1/datasets/{id}/versions (app/features/data_accelerator/api.py:250-256 -> repo.list_versions, data_accelerator/repo.py:430-452) selects `FROM dataset_versions dv WHERE dv.dataset_id = :did` with no status filter, so the dead row is returned with `status: "uploading"` forever, and it has permanently consumed a version_number (files/repo.py:65-72 MAX(version_number)+1). GET /tus/{id}/status begins `meta = load_tus_meta(upload_id); if meta is None: raise HTTPException(404, "Upload not found")` (api.py:588-592), so after the sweep the UI gets a 404 and has no way to explain the stuck row.

6. Not deliberate. No docstring/comment defends it, and the repo treats exactly this class as a defect: commit b69bfc0 "close and report stranded runs" ("a run/job left `running` forever, unreachable and invisible") and HANDOFF.md:465 ("previously both were orphaned forever") show the codebase's stated position is that stranded lifecycle rows are bugs to be closed. This is the same class, unfixed for TUS versions.

Only nuance vs the claim's wording: cleanup runs opportunistically on each POST /tus/ (api.py:406), not on a timer, so the 404 window depends on traffic; the stranded row itself is permanent either way.

**Fix**

Make the sweep close the DB row it is abandoning: read `version_id` out of the meta JSON before `delete_tus_upload` and call `repo.fail_version(version_id, "Upload expired without completing")` — the same call DELETE /tus/{id} already makes at api.py:585. That means making `cleanup_stale_uploads` async (it has exactly one caller, files/api.py:406, already in an async handler, so `await cleanup_stale_uploads()`), or moving the sweep into the async retention/GC job where a session is already available. Keep the per-file try/except so one bad row does not abort the sweep.

**Test to pin it**

"test_expired_tus_upload_marks_its_version_failed_not_just_deletes_files" — integration layer, tests/ (needs a real DB row): POST /tus/ with a dataset, backdate the meta file's updated_at past TUS_UPLOAD_EXPIRY_SECONDS, trigger the sweep, then assert GET /datasets/{id}/versions shows that version with status "failed" (with an error string) rather than "uploading".

## [medium] 4 of 7 declared webhook EVENT_TYPES — including transformation.completed — are accepted on subscribe but never emitted anywhere. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim checks out, and the real gap is wider than claimed.

Declared vocabulary — app/features/webhooks/schemas.py:11-19 lists 7 EVENT_TYPES including "transformation.completed", and the WebhookCreate._known_events validator (schemas.py:38-44) ACCEPTS it (it only rejects names outside that tuple), so a subscription naming it is created with 201 and never fires.

Emission sites — `grep -rn "emit(" app/` finds exactly two callers of webhooks.emit:
- app/features/quality/api.py:168-175 → "validation.failed" / "validation.passed"
- app/features/data_accelerator/api.py:459-464 → "tag.promoted"
Plus the manual /webhooks/{id}/test endpoint, which uses the event name "webhook.test" (app/features/webhooks/api.py:157,160) — a string that is NOT in EVENT_TYPES, so a test delivery does not exercise any real event either.

That means 4 of the 7 declared events are never emitted anywhere: transformation.completed, tag.rolled_back, version.ready, dataset.published. `grep -rn "transformation.completed"` has exactly one hit, schemas.py:17.

Transform completion path — app/features/transform/service.py:347-351:
    await repo.complete_run(run_id, result_summary=summary,
                            artifact_id=artifact["id"], ...)
    return summary
and `grep -rn "webhooks" app/features/transform/` returns nothing at all — the module never imports the webhooks package, so there is no dispatch on any code path, not just at that line.

Not by design. ARCHITECTURE.md:559 documents the publish flow as `└─ webhooks.emit("dataset.published")` — an emission that also does not exist in the source (`grep -rn "webhooks" app/features/library/` is empty). Docs asserting an emission that isn't there is evidence of oversight, not a deliberate reserved-vocabulary choice. HANDOFF.md:152-154 describes webhooks as "lifecycle notifications" with no note that any declared event is aspirational. No docstring or comment anywhere reserves unemitted names.

No test contradicts it: tests/test_webhooks.py and tests/unit/test_webhook_signing.py only ever use "tag.promoted" and "validation.failed" (test_webhooks.py:86,171,199,211,219). Nothing asserts transformation.completed delivers, and nothing asserts it doesn't.

The only mitigation: an empty `events` list means "all" (schemas.py:9-10, repo.matching_subscriptions), so a subscriber who filters nothing still receives the 3 events that are actually emitted. The failure is specific to a user who explicitly picks one of the 4 dead names.

**Fix**

Two coherent options; pick one rather than patching just transform.
(a) Emit them. In app/features/transform/service.py:347-351, after repo.complete_run, add a guarded `await webhooks.emit("transformation.completed", team_id=params["team_id"], dataset_id=dataset_id, data={"run_id": run_id, "artifact_id": artifact["id"], "row_count": output_rows, "step_count": len(steps)})`. emit() already promises never to raise, but this runs inside a job handler, so wrap it in try/except like quality/api.py:166-178 so a webhook problem cannot mark a successful run failed. Do the same for tag.rolled_back (data_accelerator rollback path) and dataset.published (library publish_artifact_as_version caller, which ARCHITECTURE.md:559 already claims does this).
(b) Shrink the vocabulary. Cut the 4 unemitted names from EVENT_TYPES so the validator rejects them and the UI picker cannot offer them. Cheaper, but it removes documented behaviour.
Either way, add a guard test that asserts the set of literals passed to webhooks.emit across app/ equals EVENT_TYPES, so the declaration and the emitters cannot drift again.

**Test to pin it**

"test_every_declared_event_type_is_emitted_somewhere" in tests/unit/ (a static guard: AST- or grep-walk app/ for webhooks.emit call sites and assert the collected literal event names cover EVENT_TYPES), plus "test_completing_a_transformation_delivers_transformation_completed_to_a_subscriber" in tests/ (integration: subscribe with events=["transformation.completed"], run a transform inline, assert a delivery row with that event_type appears on GET /webhooks/{id}/deliveries).

## [medium] Transform preview/validation is only reachable through a persisted definition; there is no ad-hoc compile/preview endpoint for an unsaved pipeline.

- **domain:** ?
- **where:** 

**Evidence**

The cited route is exactly as claimed. app/features/transform/api.py:105-116 — `@router.post("/datasets/{dataset_id}/transformations/{definition_id}/preview")` then `definition = await _definition_or_404(dataset_id, definition_id)`; preview_transformation (app/features/transform/service.py:184-193) reads `definition["steps"]`/`definition["version_selector"]` and never accepts a body. There is no request body model at all on that route.

Grepping every router in app/features/*/api.py for preview/validate/compile/dry returns only: transform/api.py:105, relationships/api.py:205 (`POST /joins/preview`, which DOES take an ad-hoc `JoinBuildSpec` body and "Persists nothing"), and two explorer sheet previews. So no `POST /datasets/{id}/transformations/compile|preview` sibling exists, and the sibling Wave-5 feature does support ad-hoc preview — an inconsistency, not a documented stance.

Validation is likewise save-coupled: validate_pipeline (service.py:117-125) is only called from create_transformation (service.py:136) and update_transformation (service.py:165). Both persist. create requires a name (schemas.py:21 `name: str = Field(..., min_length=1)`) unique per dataset (migration 20260809010000_transformations.sql:23 `UNIQUE (dataset_id, name)`; repo.create_definition uses ON CONFLICT DO NOTHING -> 409, service.py:141-143, asserted by tests/test_transformations.py:61).

Per-step columns: compile_pipeline (compile.py:343-372) folds `cols` step by step but returns only the FINAL `cols`; TransformPreview (schemas.py:64-74) exposes only `output_schema`. So no endpoint yields the folded column list at step N on the success path (HANDOFF.md:205-212 notes an `unknown-column` ERROR lists what is available at that point — error-only, not a picker source).

Two overstatements in the claim's impact text, neither fatal to the core finding: (a) POST with a duplicate name is a clean 409, not a 500; (b) an intermediate-step picker is not literally impossible — save-then-truncate-then-PATCH gets there, awkwardly. Conversely the claim's PATCH worry is understated in a different way: repo.update_definition (repo.py:73-93) issues a plain UPDATE with no ON CONFLICT, so a rename onto an existing name raises IntegrityError and hits app/api/errors.py:136 `_unhandled_exception_handler` (500); the 409 branch at service.py:174-176 is unreachable, and no test covers PATCH-duplicate-name.

Nothing in ROADMAP.md §19-§21 (lines 177-194, "Preview-on-sample before full run", "define -> preview -> run -> inspect -> publish"), HANDOFF.md:205-229, or any docstring states that preview is deliberately restricted to saved definitions. Not BY_DESIGN — just unbuilt.

**Fix**

Add `POST /datasets/{dataset_id}/transformations/compile` (DATASET_READ) taking the TransformationCreate body minus `name` (sheet, version_selector, steps, optional `rows`). Reuse `_resolve_target` + `validate_pipeline`/`compile_pipeline` to return `output_schema` and, when `rows` is given, the same TransformPreview sampled rows. Cheapest way to also serve a step-N picker: have compile_pipeline optionally return the per-step folded `cols` snapshots it already computes in its loop (compile.py:366-369) and surface them as `step_schemas`. Separately, make repo.update_definition tolerate the unique violation (catch IntegrityError / pre-check the name) so the existing 409 at service.py:174 is actually reachable.

**Test to pin it**

"test_a_pipeline_can_be_compiled_and_previewed_without_saving_a_definition" in tests/test_transformations.py (integration, tests/), plus "test_renaming_a_transformation_onto_an_existing_name_is_a_409_not_a_500" in the same file.

## [medium] Publishing a transformation run is unguarded and unrecorded: each POST .../runs/{id}/publish mints another identical version plus another transformed_from edge.

- **domain:** ?
- **where:** 

**Evidence**

app/features/transform/api.py:168-179 (publish_run) only checks permission and that the run belongs to the dataset; it then calls service.publish_transformation_run unconditionally. app/features/transform/service.py:419-432 calls resolve_publishable_artifact then publish_artifact_as_version — no check of prior publication. app/features/library/service.py:213-229 (resolve_publishable_artifact) validates only status=='completed', artifact exists, source version exists. app/features/library/service.py:265-271 is the sole uniqueness guard and it is inside `if mode == "new_dataset"` ("A dataset named '...' already exists in this team"); in new_version mode (line 275-276 `target = ds`) files_repo.create_version is called every time (line 279) and record_lineage appends a fresh `transformed_from` row every time (line 322-327).

The claim's second half also holds: the transformation_runs DDL (app/infra/db/postgres/migrations/20260809010000_transformations.sql:28-46) has no published_version_id/published_at column, and TransformationRunOut/Detail (app/features/transform/schemas.py:76-95) expose nothing about publication. The version-listing schema VersionInfo (app/features/data_accelerator/schemas.py:43-63) omits the `source` JSONB, so the `transformation_run_id` recorded at library/service.py:281-284 is never surfaced — there is genuinely no API to ask "has this run been published?". grep of tests/ (test_transformations.py:313-380, test_pivot.py, test_join_builder.py, test_timeline.py) finds no test asserting republish behaviour, and no repeat-publish 409 test exists to contradict the claim. No docstring, HANDOFF.md or ARCHITECTURE.md text documents republishing as intentional; HANDOFF.md:794-795 documents only the new_dataset name guard.

One caveat, which the claim itself concedes: in new_dataset mode with the default name, a second click DOES 409 on dataset_name_taken, so the duplication is real only for mode=new_version (or new_dataset with a different name).

**Fix**

Add `published_version_id UUID REFERENCES dataset_versions(id) ON DELETE SET NULL` (+ published_at) to transformation_runs (and analytics_runs), set it inside the publish path after publish_artifact_as_version returns, expose it on TransformationRunOut, and have publish_transformation_run raise 409 (or return the existing publication idempotently) when it is already set.

**Test to pin it**

"test_publishing_the_same_run_twice_does_not_create_a_second_version" in tests/test_transformations.py (integration layer — it needs the real route, version table and lineage rows).

## [medium] preview_transformation omits the "version has no data" guard that start_run and _resolve_target both apply, so a pinned data-less version yields a misleading sheet-not-in-version 404.

- **domain:** ?
- **where:** 

**Evidence**

The asymmetry in the code is real and exactly as cited.

app/features/transform/service.py:236-238 (start_run):
    ver = await resolve_version(dataset_id, **_selector_pin(definition["version_selector"]))
    if not ver.get("path"):
        raise HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")
    await _resolve_run_sheet(ver, definition["logical_sheet_id"])

app/features/transform/service.py:187-189 (preview_transformation) — same two lines minus the guard:
    ver = await resolve_version(dataset_id, **_selector_pin(definition["version_selector"]))
    sheet_row = await _resolve_run_sheet(ver, definition["logical_sheet_id"])

The third site, _resolve_target (service.py:89-92, used by create/update), also has the guard — so preview is the sole outlier of three. app/shared/datasets.py:51-52 shows the same guard is the service-wide convention.

Downstream, _resolve_run_sheet (service.py:102-114) raises 404 code="sheet-not-in-version", "The transformation's sheet is not present in version N", because get_version_sheet_rows (app/shared/datasets.py:81-106) returns [] for a version with no sheet rows and no source.sheets. So preview does produce a different code and message for the same state. (If sheet rows do exist with path NULL, it degrades further: sheet_data_path returns str(None) at datasets.py:113 and the failure surfaces as a 400 "Sheet data unreadable"/transformation-failed.)

Reachability is narrower than the claim states, but non-zero. The claim's "dataset mid-upload" framing is wrong for the default mode=current selector: resolve_version -> _get_current_version_or_404 (datasets.py:203-208) already 404s "Dataset not found" when path is NULL, so run and preview agree there and start_run's guard is unreachable for that mode. The guard only bites for an explicit pin (mode=version / mode=tag). Definitions cannot be created against a data-less pinned version (_resolve_target blocks it), and nothing ever nulls an existing path (no "SET path = NULL" anywhere). The live path is a tag: PUT /datasets/{id}/tags (app/features/data_accelerator/api.py:362-388) validates only ownership of the version, never its status or path, so a tag can be moved onto an "uploading" or "failed" version (created path-less at app/features/files/api.py:164-168, 253-257, 420-426). A transformation pinned by that tag then gets "Version has no data" from Run and "sheet is not present in version N" from Preview.

No test covers either message for transform preview: grep of tests/ for "Version has no data" / "sheet-not-in-version" hits only tests/test_saved_views.py:177. No docstring or comment defends preview's omission — the run site instead documents (service.py:239-241) why it resolves eagerly, which argues the guard was simply not mirrored.

**Fix**

Hoist the guard into a small helper and use it at all three sites. Minimal version: in preview_transformation, after service.py:188, add
    if not ver.get("path"):
        raise HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")
Better: extract `async def _pin_version_with_data(dataset_id, selector) -> dict` that does resolve_version + the path check, and call it from _resolve_target, preview_transformation, and start_run so the three cannot drift again. (Separately worth considering: set_tag accepting a path-less version is what makes the state reachable at all.)

**Test to pin it**

"Previewing a transformation whose tag points at a version with no data returns the same version-has-no-data 404 as starting a run" — belongs in tests/ (integration), alongside the transform preview/run tests, since it needs a real tag moved onto an uploading version.

## [medium] PATCH /datasets/{id}/views/{view_id} with {"description": null} is silently ignored — the old description survives and 200 is returned. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/explorer/schemas.py:58-65 — `DatasetViewUpdate` has `description: str | None = Field(default=None, max_length=2000)`; pydantic accepts an explicit `null` and it is indistinguishable from omission unless `model_fields_set`/`exclude_unset` is consulted.

app/features/explorer/service.py:151-157 — `update_view` builds the write set with:
```
    if body.name is not None:
        fields["name"] = body.name
    if body.description is not None:
        fields["description"] = body.description
```
so an explicit null never reaches `fields`. If nothing else changed, service.py:174-175 `if not fields: return DatasetViewOut(**view)` returns the unchanged row with HTTP 200 — a confident, plausible, wrong response.

The repo layer is NOT the problem: app/features/explorer/repo.py:240-247 keys on membership (`for col in ("name","description","logical_sheet_id"): if col in fields:`), so it would emit `description = NULL` correctly if the service passed it. The gate is purely the service's `is not None` test.

The rest of the codebase does this the documented way — `model_dump(exclude_unset=True)` at app/features/library/api.py:91,227, app/features/discovery/api.py:236,345, app/features/data_accelerator/api.py:233. app/features/data_accelerator/repo.py:379-388 spells out the contract: "key sent as explicit null -> present in fields with value None -> col = NULL is emitted, so the field is cleared." ARCHITECTURE.md:456-459 states the same as a service-wide rule ("PATCH merges, writing only the fields present in the body while an explicit null still clears"), so explorer's view PATCH deviates from the repo's own stated contract — this is a deviation, not a design choice.

No test contradicts the claim: tests/test_saved_views.py only asserts the happy path (line 62-65 patches `{"description": "updated"}` and asserts the new value); nothing exercises `null`. No docstring or comment anywhere anchors the `is not None` behaviour as intentional.

**Fix**

In `update_view` (app/features/explorer/service.py:153-157), key off what the client actually sent rather than off `None`:

```python
sent = body.model_fields_set
if body.name is not None:            # name is NOT NULL — null stays a no-op
    fields["name"] = body.name
if "description" in sent:            # explicit null clears
    fields["description"] = body.description
```
Keep `name` on the `is not None` guard (or reject explicit null with 422) so a `{"name": null}` body cannot drive `name = NULL` into a NOT NULL column and turn a 422 into a 500.

**Test to pin it**

"test_patch_view_with_explicit_null_description_clears_it" in tests/test_saved_views.py (integration layer — it needs the route, service and repo together to prove the null survives to the UPDATE); pair it with "test_patch_view_omitting_description_keeps_it" so the two cases are pinned apart, and "test_patch_view_with_null_name_is_rejected_not_500".

## [medium] GET/POST .../versions/{v}/profile-runs are the only collection endpoints in the service that return a bare unpaginated list instead of the Page envelope.

- **domain:** ?
- **where:** 

**Evidence**

The structural claim is literally true and is uniquely true of these two routes. `grep -rn "response_model=list\[" app/` returns exactly two hits in the whole service: app/features/explorer/api.py:183 (`response_model=list[ProfileRunOut]`, POST create_profile_runs) and app/features/explorer/api.py:201 (same, GET list_profile_runs). Every other collection is enveloped, e.g. api.py:113-114 `@router.get(".../views", response_model=Page[DatasetViewOut])` -> `return Page.of([...], total, page)` (api.py:124) with `page: PageParams = Depends(pagination)`.

The handler takes no query params at all: `async def list_profile_runs(dataset_id, version_number, principal)` (api.py:202-206) and calls `repo.list_runs_for_version(str(ver["id"]))`. The repo (app/features/explorer/repo.py:107-117) is `SELECT {_RUN_COLS} FROM profile_runs WHERE dataset_version_id = :vid ORDER BY started_at, logical_sheet_id` — no LIMIT/OFFSET, no status or algorithm_version predicate. So there is genuinely no server-side paging or filtering; nothing in a Pydantic request model rescues it because there is no request model.

HANDOFF.md:582 states the project's own convention: "Uniform API layer: `/api/v1`, `Page` envelope, problem+json errors, X-Request-Id." I found no docstring, comment, ARCHITECTURE.md or HANDOFF.md line justifying the bare list for profile-runs (HANDOFF.md:376-379 describes §8 only functionally). So this is an unjustified deviation, not a documented deliberate choice.

Two parts of the claim are overstated and should not carry weight:
1. "no way to ask for only completed runs ... mixes failed and completed rows with no count" — each item carries `status: str` and `error: str | None` (app/features/explorer/schemas.py:118, 120), and the row set is one-per-(version, sheet, algorithm_version) enforced by the upsert's `ON CONFLICT (dataset_version_id, logical_sheet_id, algorithm_version)` (repo.py:33), so client-side filtering is trivial and the array length is the count.
2. "unbounded" is loose — the array is bounded by the number of ready sheets in one version (POST is a bulk-action result, not a collection, so its bare shape is defensible on its own). The real defect is the GET listing.

tests/test_profiling_runs.py:47-62 asserts the bare-list shape (`runs = r.json(); assert len(runs) == 1`, `assert r.status_code == 200 and len(r.json()) == 1`) — it pins current behaviour rather than contradicting the claim, and in the same test the jobs list is read as `r.json()["total"]` (line 56), showing the two shapes side by side.

**Fix**

Give GET .../versions/{version_number}/profile-runs the same treatment as list_views: `response_model=Page[ProfileRunOut]`, add `page: PageParams = Depends(pagination)` plus optional `status: str | None = None` and `algorithm_version: int | None = None` query params; change repo.list_runs_for_version to accept limit/offset/filters and return `(rows, total)` via a COUNT, then `return Page.of(await service.runs_with_context(ver, rows), total, page)`. Leave POST as a bare list (it is an action result), or wrap both if strict uniformity is wanted — either way callers in app/features/mcp/tools/curate.py:727 and app/features/data_accelerator/services/diffs.py:267 must be updated together.

**Test to pin it**

"test_list_profile_runs_is_paged_and_filterable_by_status" in tests/test_profiling_runs.py (integration layer, tests/): profile a multi-sheet workbook, then assert GET .../profile-runs returns an object with total/limit/offset, that limit=1 returns one item with the full total, and that status=completed excludes a failed run.

## [medium] PATCH view returns 500 instead of 404 if the view is deleted between the route's 404 check and the repo UPDATE.

- **domain:** ?
- **where:** 

**Evidence**

The claim's code reading is exactly right on every hop.

app/features/explorer/repo.py:240-262 — `async def update_view(...) -> dict | None:` with docstring "Update the given columns; None when the view doesn't exist."; the UPDATE has `RETURNING id::text` and line 262 is `return await get_view(dataset_id, view_id) if row else None`. So None is returned when the UPDATE matches zero rows (and also if the row disappears between the UPDATE and the re-SELECT).

app/features/explorer/service.py:176-181 — only IntegrityError is caught; line 181 is unconditionally `return DatasetViewOut(**updated)`. `**None` raises `TypeError: argument of type 'NoneType' is not a mapping`. No None guard anywhere on this path, unlike the sibling create path which does guard (service.py:145-147 `if created is None: raise HTTPException(409, ...)`).

app/features/explorer/api.py:139-151 — the route fetches `view = await _get_view_or_404(...)` then calls the service; there is no second existence check. The delete route at api.py:162-164 does guard: `if not await repo.delete_view(...): raise HTTPException(404, ...)`.

app/api/errors.py:147 registers `_unhandled_exception_handler` for bare `Exception`, so the TypeError becomes a 500, not a typed problem document.

Scope check (narrows the claim): the UPDATE filters on `id = :vid AND dataset_id = :did`, the same predicate `get_view` used moments earlier (repo.py:230-236), and `get_view`'s JOIN to `dataset_sheets` (repo.py:181) can't drop the row because logical_sheet_id is only ever set from a resolved sheet. So the None return is reachable ONLY via the concurrent-delete race the claim describes — there is no single-request input that triggers it. No docstring, comment, or HANDOFF/ARCHITECTURE note claims this is deliberate, and no test in tests/ touches repo.update_view's None branch (grep for "update_view" hits only app/ code).

**Fix**

In app/features/explorer/service.py after line 177, mirror the create path: `if updated is None: raise HTTPException(404, f"View not found: {view['id']}")` before constructing DatasetViewOut.

**Test to pin it**

"test_update_view_returns_404_when_the_view_is_deleted_concurrently" in tests/unit/ — patch app.features.explorer.repo.update_view to return None and assert the PATCH route surfaces 404, not 500.

## [medium] The "Version has no data (status: ...)" 404 is a bare HTTPException, so its problem+json `code` is the generic `not_found`, with the ingest status only in English prose.

- **domain:** ?
- **where:** 

**Evidence**

The cited lines are accurate. `app/features/explorer/api.py:47-49` (`_readable_version`), `:194-196` (POST profile-runs) and `:331-333` (POST sql) all raise `HTTPException(404, f"Version has no data (status: {ver.get('status', 'unknown')})")` — plain `fastapi.HTTPException`, no `code=`. The renderer `app/api/errors.py:115-124` `_http_exception_handler` does `code=getattr(exc, "code", None)`, and `problem_response` (`errors.py:104`) falls back to `code or _code_for(status)` = `"not_found"` for 404. The repo clearly has the affordance and uses it elsewhere: `ProblemException(..., code="sheet-selection-required")` in `app/shared/datasets.py:133-138`, `code="version-too-large-for-sql"` in `app/features/explorer/service.py:382-388`, plus tests pinning `profile-required`, `unknown-column`, `sheet-not-in-version`, etc. So a genuinely-missing dataset (`app/shared/datasets.py:207` "Dataset not found") and a still-ingesting version are indistinguishable by `code`. The same bare pattern is duplicated in six more places (`app/shared/datasets.py:52`, `transform/service.py:92,238`, `relationships/joins.py:77`, `data_accelerator/services/sampling.py:748`).

The state is reachable: async upload creates the version row with `path=None, status="uploading"` (`app/features/files/repo.py:54-86`, called at `app/features/files/api.py:163-167` with `status="uploading"`), and only `complete_version` sets a path.

No test asserts this shape — grep of tests/ for "has no data" finds nothing, and the only "uploading" assertions are in test_uploads_tus.py — so nothing contradicts the claim, and no docstring/HANDOFF/ARCHITECTURE text defends the omission. It is an oversight, not a documented choice.

Where the claim overreaches: (a) `list_profile_runs` (api.py:200-211) does call `resolve_version`, which 404s for a nonexistent version (`datasets.py:68-71`); it returns 200 `[]` only when the version really exists but has no runs — the two endpoints do NOT "disagree about whether the version exists", they disagree about whether the version has data, and empty-list is defensible list semantics; (b) the UI is not actually blind: `VersionInfo.status` is exposed by GET /datasets/{id}/versions (`data_accelerator/api.py:250-255`, `schemas.py:54`) and GET /files/upload/status/{version_id} (`files/api.py:318-347`) exists specifically for polling. So it costs one extra call, it is not undiscoverable.

**Fix**

Replace the six duplicated bare raises with a shared helper in `app/shared/datasets.py`, e.g. `def _no_data(ver): raise ProblemException(404, f"Version has no data (status: {ver.get('status','unknown')})", code="version-not-ready", version_status=ver.get("status"))`, and call it from `_readable_version`, `create_profile_runs`, `sql_query`, `resolve_dataset_path`, transform/service.py:92,238, relationships/joins.py:77 and sampling.py:748. The extra `version_status` body field plus a distinct `code` lets the UI branch poll-vs-navigate-away without a second request.

**Test to pin it**

"test_preview_of_a_still_ingesting_version_returns_a_version_not_ready_code_not_generic_not_found" in tests/ (integration layer — it needs a real dataset_versions row created with status='uploading' and path NULL, i.e. an async upload, which the unit layer cannot produce); pair it with an assertion that a genuinely missing version_number still yields code 'not_found'.

## [medium] Cursors omit the sheet, so a cursor minted on one sheet is accepted verbatim on another sheet of the same version instead of 400 invalid-cursor. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The claim is factually accurate about the code on every step of the path.

1. Cursor payload has no sheet component. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/shared/query/compile.py:88-90 — `encode_cursor(version_id, hash_, offset)` -> `json.dumps({"v": version_id, "h": hash_, "o": offset})`. decode_cursor (93-104) validates only the presence/type of "v", "h", "o".

2. The match check uses only those two fields. compile.py:127-133:
```
if spec.cursor is not None:
    c = decode_cursor(spec.cursor)
    if c["h"] != h or c["v"] != version_id:
        raise ProblemException(400, ..., code="invalid-cursor")
    offset = c["o"]
```
and compile.py:158 mints the next cursor as `encode_cursor(version_id, h, offset + spec.limit)`.

3. `spec_hash` (compile.py:81-85) hashes `spec.model_dump(exclude={"cursor","limit"})` — QuerySpec carries columns/filters/search/sort only; the sheet is a path parameter, not part of the spec, so it cannot enter the hash indirectly either.

4. The sheet-scoped route passes no sheet identity down. app/features/explorer/api.py:343-359 `query_sheet(..., sheet_name, spec, ...)` -> app/features/explorer/service.py:83-97, which resolves the sheet row and then calls `execute_query(conn, "df", spec, row["schema_json"], version_id=str(ver["id"]))` — the version UUID only. The saved-view run path does the same (service.py:213-214), so a cursor from one view can be replayed on another view pinned to the same version.

Practical reach: `validate_spec` runs against the target sheet's `schema_json`, so a cursor is only replayable when the spec's referenced columns exist on the other sheet — trivially satisfied by a bare `QuerySpec()` (no columns/filters/sort) or by sheets with overlapping column names. No sheet-identity guard exists anywhere on the path.

Not contradicted by tests: tests/unit/test_query_dsl.py:372-400 only covers garbage/tamper, spec-hash mismatch, and version mismatch (`test_cursor_spec_or_version_mismatch_rejected`). No test asserts cross-sheet acceptance either way.

Not documented as deliberate: HANDOFF.md:418-420 describes the cursor as "`{version_id, spec_hash, offset}` exploiting version immutability — `invalid-cursor` on tamper/mismatch". Immutability justifies the offset being stable per (version, spec); it does not address sheet scoping, and nothing in the docstrings/comments explains omitting it.

Severity caveat: the rows returned are a genuine page of the sheet actually named in the URL at that offset — nothing cross-sheet leaks, and no cross-tenant boundary is crossed (`_readable_version` still authorizes). The defect is that the documented `invalid-cursor` guard silently fails to fire, so the caller resumes mid-sheet instead of being told the cursor is stale.

**Fix**

Bind the sheet into the cursor identity. Simplest: fold the sheet identifier into the hash — pass the resolved sheet key (prefer `row["logical_sheet_id"]`, falling back to `sheet_name`) into `execute_query` and hash it alongside the spec, e.g. `spec_hash(spec, scope=sheet_key)` mixing `scope` into the canonical JSON before sha256. Existing `c["h"] != h` check then rejects cross-sheet replays with no cursor-format change. Alternative: add an `"s"` field to the cursor payload in encode/decode and compare it in the same guard at compile.py:129.

**Test to pin it**

"test_cursor_from_one_sheet_is_rejected_on_another_sheet_of_the_same_version" in tests/unit/test_query_dsl.py (unit layer, alongside test_cursor_spec_or_version_mismatch_rejected), plus an API-level companion in tests/test_explorer.py posting a sheet A next_cursor to the sheet B query route and asserting 400 invalid-cursor.

## [medium] PATCH /webhooks/{id} runs no url-scheme or event-name validation, so it persists values POST rejects with 422. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The structural claim is exactly right, and no layer downstream re-validates.

- app/features/webhooks/schemas.py:31-44 — `WebhookCreate` carries `@field_validator("url") _http_only` ("url must be http:// or https://") and `@field_validator("events") _known_events` ("unknown event type(s): ...").
- app/features/webhooks/schemas.py:47-51 — `class WebhookUpdate(BaseModel)` declares `name`, `url: str | None = None`, `events: list[str] | None = None`, `enabled` with zero validators. It does NOT inherit from `WebhookCreate`; a grep for `WebhookUpdate|model_validator|_http_only|_known_events` across app/ shows the only webhook validators live on `WebhookCreate`.
- app/features/webhooks/api.py:112-114 — `await _manageable(...)` then `repo.update_subscription(subscription_id, body.model_dump(exclude_unset=True))`. `_manageable` is purely authz (404/403); it does not touch the body.
- app/features/webhooks/repo.py:76-93 — `update_subscription` blindly maps `name`, `url`, `enabled` into a SET clause and `json.dumps(fields["events"] or [])` into the jsonb column. No scheme check, no event-name check.
- tests/test_webhooks.py:102-105 — `test_non_http_urls_are_rejected` posts `file:///etc/passwd` to POST /webhooks only; tests/test_webhooks.py:96-100 `test_unknown_event_types_are_rejected` likewise POST-only. The two PATCH tests (tests/test_webhooks.py:85, :239) send only `enabled`/`events` with valid values. Nothing asserts the opposite of the claim.
- No docstring, comment, HANDOFF.md or ARCHITECTURE.md text justifies the asymmetry (ARCHITECTURE.md:486 just lists the route). It reads as an omission, not a documented choice.

Where the claim OVERSTATES the impact, and this matters for severity:
1. `http://169.254.169.254/latest/meta-data/` is NOT a bypass — it starts with `http://`, so `_http_only` accepts it on POST too. There is no SSRF/private-IP guard anywhere; the create path is equally open. PATCH adds nothing there.
2. `file:///etc/passwd` is persisted and returned 200, but it is never actually fetched: app/features/webhooks/service.py:122-136 posts via `httpx.AsyncClient.post(subscription["url"], ...)`, and httpx raises `UnsupportedProtocol` for a `file://` scheme; the bare `except Exception` at :132 records it as a failed delivery. So "the service then POSTs signed payloads to it" is false for the non-http scheme that PATCH uniquely lets through.

The real, confirmed defect is the inconsistent validation contract, plus a genuinely silent failure mode: `PATCH {"events": ["not.a.thing"]}` returns 200 with the bogus filter echoed back, and `repo.matching_subscriptions` (repo.py:107-122) will then match nothing forever — the subscription looks healthy in the UI and silently never fires again.

**Fix**

In app/features/webhooks/schemas.py, give `WebhookUpdate` the same two checks, skipping `None`:

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("url must be http:// or https://")
        return v

    @field_validator("events")
    @classmethod
    def _known_events(cls, v: list[str] | None) -> list[str] | None:
        unknown = [e for e in (v or []) if e not in EVENT_TYPES]
        if unknown:
            raise ValueError(f"unknown event type(s): {unknown}")
        return v

Better: hoist both into a shared mixin/module-level helper so the create and update models cannot drift again. Also add `min_length=1, max_length=2000` to `WebhookUpdate.url`, which is likewise missing.

**Test to pin it**

tests/test_webhooks.py::test_patch_rejects_non_http_urls_and_unknown_event_types_just_like_create — an integration test in tests/ (not tests/unit/), since it must exercise the route + schema; create a valid subscription, then assert PATCH with url="file:///etc/passwd" is 422 and PATCH with events=["not.a.thing"] is 422.

## [medium] The raw-SQL 413 size guard is unenforceable on legacy versions: it is skipped on the no-sheet-rows early return and computes 0 for JSONB-fallback sheets whose size_bytes is always None.

- **domain:** ?
- **where:** 

**Evidence**

app/features/explorer/service.py:374-389 `_sql_tables`:
  `ready = [r for r in sheets if r.get("status","ready")=="ready"]`
  `if not ready:  # legacy version without sheet rows: one synthetic table` -> `return {"data": str(ver["path"])}` (:378-379) — returns BEFORE the guard.
  `total = sum(r.get("size_bytes") or 0 for r in ready)` (:380), then the 413 `version-too-large-for-sql` at :381-388.
The claim's second half is worse than stated and is confirmed at the source: app/shared/datasets.py:81-106 `get_version_sheet_rows` falls back to the legacy `source` JSONB and hardcodes `"size_bytes": None` (:100) for every synthesized row. So a legacy version WITH `source.sheets` takes the non-early-return branch, `ready` is non-empty, and `total` is always exactly 0 -> guard never fires. A legacy version with no `source.sheets` at all takes the early return -> guard never runs.
The materialization is real and eager: app/shared/duck.py:42-66 `open_sandboxed` does `CREATE TABLE <key> AS SELECT * FROM read_parquet('<path>')` for every table before locking the connection, i.e. the whole parquet is pulled into the in-memory DuckDB, which is precisely what MAX_SQL_MATERIALIZE_BYTES (=512MB, service.py:60) exists to bound.
No other guard on the path: app/features/explorer/api.py:317-334 only checks permission, resolves the version and 404s when `path` is missing, then calls `service.raw_sql_query` (service.py:409-427) which calls `_sql_tables` and immediately `open_sandboxed`.
Not contradicted by tests and not covered: the only test, tests/test_explorer.py:338-344, monkeypatches MAX_SQL_MATERIALIZE_BYTES to 1 on a normally-processed version (which does have DB sheet rows with real size_bytes from processing.py:87/106) and asserts the 413 — it exercises only the branch that works.
Not documented as deliberate: HANDOFF.md:369 states the guard as an unconditional contract ("Size guard `version-too-large-for-sql` 413"); the only comment at :378 explains the synthetic table, not any decision to waive the limit. Modern write paths (files/services/processing.py, replace.py:98, library/service.py, files/api.py:286) all populate sheet size_bytes, so NULLs are a legacy-row property, not an accepted normal state.

**Fix**

In `_sql_tables`, fall back to the version-level size when sheet sizes are unusable, and run the guard on all branches: compute `total = sum(...)` over ready rows; if any ready row has `size_bytes` None, or if `ready` is empty, use `ver.get("size_bytes")` (already populated on `dataset_versions` and read elsewhere, e.g. app/features/files/api.py:338) instead. Do the 413 check once, before the early return builds `{"data": ver["path"]}`. If neither source yields a size, either stat the object via the storage layer or raise the 413/409 rather than silently materializing unbounded data.

**Test to pin it**

"test_sql_on_legacy_version_without_sheet_rows_still_enforces_the_size_guard" (plus a sibling for a legacy version whose JSONB-fallback sheets have NULL size_bytes) — integration layer, tests/test_explorer.py, alongside the existing MAX_SQL_MATERIALIZE_BYTES monkeypatch test at line 338.

## [medium] POST /relationships/suggest?sync=false always returns job_id: null, because worker.dispatch discards the created job row on the async path.

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out.

app/shared/worker.py:122-126 — `job = await jobs.create_job(...)` then `if not inline: return None`. The row (and its id) is created and thrown away; only the inline branch below reaches `job_id = str(job["id"])` (worker.py:131).

The only path that ever produces a job_id for the response is the HANDLER, not dispatch: app/features/relationships/service.py:302 `return {**result, "job_id": str(job["id"])}` in `_handle_discovery`. That handler's return value only reaches the caller when dispatch runs it inline (worker.py:140-145). Under `inline=False` the handler runs later in the worker loop and its result is stored on the job row via `_run_one` -> `jobs.complete_job` (worker.py:79-80), never returned to the request.

So at app/features/relationships/api.py:73-80, `result = await worker.dispatch(..., inline=sync) or {}` is `{}` when sync=false, and `job_id=result.get("job_id")` is None. SuggestResponse declares `job_id: str | None = None` (app/features/relationships/schemas.py:57), so the null serializes cleanly — no error, just no handle.

No test contradicts it; the tests pin it. tests/test_relationships.py:97 asserts `r.json()["job_id"]` only for the DEFAULT sync call. The async test (tests/test_relationships.py:113-127) deliberately never touches job_id — it asserts `suggested == 0` and then calls `worker.run_pending_jobs_once()` itself. tests/test_job_worker.py:95-96 asserts `result is None` for `dispatch(..., inline=False)`, i.e. the current behaviour is pinned at the worker level.

The claimed UI recourse is also real: app/features/jobs/api.py:37-47 `list_jobs` filters only by `status` and `job_type` — there is no dataset_id filter — so a UI with no id genuinely has to list /jobs and guess by job_type + timestamp among all of the team's jobs.

Not by design: HANDOFF.md:281-284 documents `inline=False` = "enqueue, worker drains" and worker.py:119-120 says the result "is ``None``", but nothing anywhere justifies exposing a `job_id` field that is populated only in the case where it is useless (sync) and null in the case it exists for (async). Compare the sibling async path, transform (app/features/transform/service.py:243-254), which creates its own run row BEFORE dispatch and hands the UI a run_id — so relationships/suggest is the odd one out, not the documented pattern.

**Fix**

In app/shared/worker.py, return the id instead of None on the async branch: replace `if not inline: return None` (line 125-126) with `if not inline: return {"job_id": str(job["id"])}`. The three callers either ignore the return (transform/service.py:251, webhooks/service.py:85) or already do `... or {}` + `.get("job_id")` (relationships/api.py:73-80), so nothing else changes. Update tests/test_job_worker.py:96 (`assert result is None`) to assert the returned job_id matches a pending jobs row. Optionally also merge the id in on the inline path so job_id is populated identically either way, instead of relying on `_handle_discovery` adding it.

**Test to pin it**

"test_async_suggest_returns_the_job_id_the_ui_polls" in tests/ (integration, tests/test_relationships.py): POST .../relationships/suggest?sync=false, assert the returned job_id is non-null, then GET /api/v1/jobs/{job_id} returns 200 with status pending, and after worker.run_pending_jobs_once() the same id reports completed with the discovery result.

## [medium] GET /api/v1/webhooks lists every subscription (including target URLs) to any team member, while GET /webhooks/{id} 403s anyone below team:manage.

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out and nothing in the schema, service, repo, tests, or docs contradicts it.

- `app/features/webhooks/api.py:85-93` — `list_webhooks` takes only `Depends(get_principal)` and calls `repo.list_subscriptions(_teams(principal), ...)`. `_teams` (api.py:29-30) is `list((principal.memberships or {}).keys())` — plain membership, no role test. `role_has` / `Permission.TEAM_MANAGE` are never referenced on this path.
- `app/features/webhooks/repo.py:43-55` — `list_subscriptions` filters only `WHERE team_id = ANY(:tids)`, so it is scoped per-tenant but not per-role.
- `app/features/webhooks/api.py:96-102` — `get_webhook` returns `WebhookOut(**await _manageable(principal, subscription_id))`, and `_manageable` at api.py:47-48 does `if not role_has(role, Permission.TEAM_MANAGE): raise HTTPException(403, "Managing webhooks requires team:manage")`. Same gate on PATCH (112), DELETE (125), deliveries (143), test (155).
- The exposure is real: `WebhookOut` (`app/features/webhooks/schemas.py`) carries `id, team_id, name, url, events, enabled, created_by, ...`. `url` — the delivery target — is in the list payload. The signing `secret` is only on `WebhookCreated`, so no secret leaks; this is target-URL and topology exposure, not credential exposure.
- Not documented as deliberate. The module docstring (api.py:1-7) says only "Managing them is an administrative act (team:manage), because a subscription sends data outside the service" — which, if anything, argues the *list* should be gated too; it offers no rationale for read-without-manage on the collection while single-read requires manage. ARCHITECTURE.md:484-487 and HANDOFF.md:152 just enumerate the routes.
- No test covers it either way. `tests/test_webhooks.py:285-293` (`test_managing_webhooks_needs_team_manage`) asserts 403 for an editor on POST and DELETE only; `test_another_teams_webhook_is_hidden` (line 296) asserts 404 cross-team on GET /{id}. There is no assertion about an editor/viewer calling GET /api/v1/webhooks — line 74 is the admin listing. So no passing test contradicts the claim, and none pins the current lenient behaviour as intended.

Minor correction to the claim's framing, not to its substance: the 403 the UI hits on the row click is not the only inconsistency — `GET /webhooks/{id}/deliveries` and `POST /webhooks/{id}/test` are gated the same way, so every drill-down from the ungated list 403s.

**Fix**

Pick one contract and apply it to both. Consistent with the module docstring ("a subscription sends data outside the service"), gate the collection: in `list_webhooks`, replace `_teams(principal)` with a manage-scoped variant, e.g. add `def _manageable_teams(principal) -> list[str]: return [t for t, r in (principal.memberships or {}).items() if role_has(r, Permission.TEAM_MANAGE)]` and pass that. (If instead reads should be open, swap `_manageable` for a membership-only `_readable` in `get_webhook` and keep `_manageable` on PATCH/DELETE/test/deliveries.) Either way, list and detail must agree so the UI never renders a row it cannot open.

**Test to pin it**

"test_listing_webhooks_needs_team_manage": an editor in DEFAULT_TEAM_ID gets an empty page (total == 0) from GET /api/v1/webhooks after an admin subscribes, and every id visible in an editor's list page is openable via GET /api/v1/webhooks/{id} without a 403. Belongs in tests/test_webhooks.py (integration layer, alongside test_managing_webhooks_needs_team_manage), since it needs a real principal with memberships and the repo.

## [medium] GET /api/v1/webhooks scopes superusers to their own memberships, so subscriptions they can create, fetch, patch and delete never appear in their list. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out and no test or doc contradicts it.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/webhooks/api.py:29-30 — `def _teams(principal): return list((principal.memberships or {}).keys())` — no is_superuser branch.
api.py:91-92 — `rows, total = await repo.list_subscriptions(_teams(principal), limit=..., offset=...)`, the only list path.
repo.list_subscriptions (app/features/webhooks/repo.py) has no "None means all" escape: both the COUNT and the SELECT are hard-wired to `WHERE team_id = ANY(:tids)`. An empty list yields `= ANY('{}')` → zero rows, total 0, HTTP 200.

Contrast with the siblings:
- app/features/jobs/api.py:45 — `team_ids = None if principal.is_superuser else principal.team_ids`.
- app/features/audit/api.py:41-44 — superuser-only and globally scoped (`audit.query(limit=..., offset=...)`, no team filter).
- app/features/discovery/api.py:34 and app/features/data_accelerator/api.py:83 — same `None if principal.is_superuser` idiom.

And within webhooks itself the single-resource path deliberately does have the branch: api.py:42-43 `if principal.is_superuser: return subscription` in `_manageable`, which gates GET/PATCH/DELETE/{id}/deliveries and /test. api.py:52-54 `_ensure_can_create` returns early for superusers, and create_webhook accepts an arbitrary `?team_id=` (api.py:66,74-75). So a superuser can create a subscription in a team they are not a member of, then read/patch/delete it by id — but list omits it. That asymmetry inside one module is what makes this an oversight rather than a policy.

principal.memberships is populated purely from real membership rows (app/features/auth/deps.py:75), so a superuser is not implicitly a member of everything; deps.py:41-43 shows `can()` short-circuits for superusers, i.e. the codebase's own convention is that superuser bypasses membership.

Nothing documents the narrower list behaviour as intentional: the docstring is only "Webhook subscriptions across your teams." (api.py:90), the module docstring (api.py:1-7) covers team-scoping and team:manage but says nothing about superusers, and ARCHITECTURE.md:484-487 / HANDOFF.md:152 just enumerate the routes. tests/test_webhooks.py has no `superuser` occurrences at all, and its list assertion (line 74) uses `admin_id`, a team member — so no passing test asserts the opposite.

**Fix**

Give the list path the same superuser branch the single-resource path already has. In app/features/webhooks/api.py, either change `_teams` to return `None` for superusers or inline `team_ids = None if principal.is_superuser else _teams(principal)` at api.py:91, and teach repo.list_subscriptions to skip the filter when team_ids is None (`WHERE (:tids IS NULL OR team_id = ANY(:tids))` on both the COUNT and the SELECT), mirroring app/shared/jobs.list_jobs.

**Test to pin it**

"a superuser lists webhook subscriptions belonging to teams they are not a member of" — integration, tests/test_webhooks.py: create a subscription via `POST /api/v1/webhooks?team_id=<other team>` as a superuser with no membership in that team, assert `GET /api/v1/webhooks/{id}` is 200 and that the same id appears in `GET /api/v1/webhooks` with total >= 1.

## [medium] CORS expose_headers lists only X-Request-Id, so cross-origin JS cannot read Content-Disposition on any of the five download responses.

- **domain:** ?
- **where:** 

**Evidence**

app/main.py:128-135 registers CORSMiddleware with `allow_origins=settings.cors_origins`, `allow_credentials=settings.cors_allow_credentials and not wildcard`, and `expose_headers=["X-Request-Id"]` — a single-entry list, no Content-Disposition, no Content-Length. app/infra/config.py:41-44: `cors_origins: list[str] = ["*"]`, `cors_allow_credentials: bool = True`, so the default path hits the wildcard branch and main.py:122-127 logs the credentials downgrade exactly as claimed. All five download responses do set the header: downloads.py:55 and :82 (`streaming_file_response` / `stored_file_response`), :134 (`workbook_xlsx_response`, `{base_name}.xlsx`), :230 and :303 (`f'attachment; filename="{dl_filename}"'` where dl_filename is built as `{stem}_v{version_number}.{fmt}` — the sheet/version/format naming the claim describes). grep for `X-Request-Id|Access-Control` across app/ returns only main.py:134 and app/api/middleware.py:24-27, so nothing else widens the exposed set. grep -rli "cors" over tests/ returns zero files: there is no CORS assertion anywhere in the suite, confirming the second half of impact_on_ui. The only doc mention of CORS is a box in an ARCHITECTURE.md diagram (line 39) — no comment, docstring, or handoff note justifying the narrow expose list, so this is an oversight rather than a documented choice (unlike the credentials downgrade immediately above, which IS deliberate and commented).

**Fix**

Extend the list in app/main.py:134 to `expose_headers=["X-Request-Id", "Content-Disposition", "Content-Length"]` (or make it a settings field, e.g. `cors_expose_headers`, defaulting to those three).

**Test to pin it**

"test_download_response_exposes_content_disposition_to_cross_origin_callers" in tests/ (integration layer) — issue a download with an `Origin:` header and assert `access-control-expose-headers` contains both `X-Request-Id` and `Content-Disposition`; pair it with a middleware-ordering guard asserting CORS headers survive on a 404 error response.

## [medium] /duplicates runs one extra `SELECT * FROM df WHERE ...` per group (up to limit=100) against a lazy DuckDB view, with no timeout.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics of the claim check out exactly.

app/features/explorer/data_quality.py:135-147 — inside `find_duplicates`, after the three bounded queries (COUNT, `duplicate_totals_sql`, `duplicate_groups_sql`), there is a per-group loop:
```
group_rows = conn.execute(duplicate_groups_sql(physical, limit)).fetchall()
for g in group_rows:
    key_values, count = list(g[:-1]), int(g[-1])
    examples = _row_dicts(conn.execute(group_examples_sql(physical, EXAMPLES_PER_GROUP), key_values), name_map)
```
`group_examples_sql` (data_quality.py:54-59) is `SELECT * FROM df WHERE <col> IS NOT DISTINCT FROM ? AND ... LIMIT 5`. So it is exactly one query per returned group.

Bound on the loop: app/features/explorer/api.py:266 and :283 both declare `limit: int = Query(default=25, ge=1, le=data_quality.MAX_DUPLICATE_GROUPS)`, and `MAX_DUPLICATE_GROUPS = 100` (data_quality.py:29). So up to 100 iterations, i.e. up to 103 DuckDB executions per request. Default is 25, so the common case is ~28.

`df` really is lazy: app/shared/data_io.py:96 `conn.execute(f"CREATE VIEW df AS SELECT * FROM {_read_expr(path)}")` for local .csv/.parquet, and `_load_s3` (data_io.py:47-65) likewise does `CREATE VIEW df AS SELECT * FROM read_parquet('s3://...')` over an httpfs connection. Only Excel/inline-JSON become materialized tables. So every one of the N example queries re-reads the object.

No timeout on this path: `find_duplicates` calls `conn.execute` directly. The watchdog the claim contrasts against is real and lives only in app/shared/duck.py:91-117 (`run_sandboxed`, `SQL_TIMEOUT_S = 30.0`, `threading.Timer(timeout_s, conn.interrupt)`), used by the /sql sandbox, not by the explorer. Grep for `watchdog|timeout|interrupt` across app/ returns no hit in features/explorer. Worse than the claim states: `find_duplicates` is `async def` and does these blocking DuckDB calls on the event loop, so a slow duplicates request stalls other requests too.

Nothing documents this as deliberate: HANDOFF.md:306-310 (§16) describes the endpoint as "capped, with example rows" but says nothing about the per-group fetch cost. No test contradicts it — tests/test_dup_missing_explorers.py:58 exercises only `limit=1`, so the loop never runs more than once in the suite.

Two qualifications that soften, but do not refute, the impact framing: (a) each example query carries `LIMIT 5`, so DuckDB can stop early once five matching rows are found — it is a full scan only in the worst case, not always; (b) the pinned DuckDB is 1.5.x (uv cache shows duckdb-1.5.3/1.5.5; requirements.txt only says `duckdb>=1.0.0`), which has the external file cache on by default, so the repeated s3 reads are largely served from memory after the first full scan rather than being 100 fresh network round-trips. The CPU cost of up to 100 re-scans and the absence of any timeout remain real.

**Fix**

Replace the loop with a single windowed query: take the top-`limit` group keys from `duplicate_groups_sql`, then run one `SELECT * FROM df QUALIFY ROW_NUMBER() OVER (PARTITION BY <cols>) <= 5` restricted to those keys (semi-join against a VALUES list, or just fetch all and bucket in Python), and group the rows by key in Python. That makes the endpoint 4 queries regardless of `limit`. Additionally, wrap the explorer's DuckDB work in `run_in_threadpool` plus the same `threading.Timer(conn.interrupt)` watchdog already used in app/shared/duck.py so the path cannot hang the event loop indefinitely.

**Test to pin it**

"test_duplicates_issues_a_constant_number_of_duckdb_queries_regardless_of_limit" — integration layer, tests/test_dup_missing_explorers.py, patching/counting `duckdb.DuckDBPyConnection.execute` while requesting `?limit=1` and `?limit=50` and asserting the query count does not grow with limit.

## [medium] GET /jobs and GET /jobs/{id} emit the same timestamp fields in two different string formats ('+00' vs '+00:00'), and neither is ISO-8601.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics of the claim are exactly as described.

app/shared/jobs.py:142-154 (list_jobs) casts in SQL: `created_at::text AS created_at, started_at::text AS started_at, completed_at::text AS completed_at`, so the driver returns Postgres' timestamptz text rendering: `2026-08-07 12:00:00.123456+00`.

app/shared/jobs.py:95-102 (get_job) is `SELECT * FROM jobs WHERE id = :id` — no cast, so `created_at` comes back as a Python `datetime` (column is `TIMESTAMPTZ`, app/infra/db/postgres/migrations/20260311001644_baseline.sql:131-133).

app/features/jobs/api.py:61-70 then stringifies with `str()`:
    def _s(key): return str(row[key]) if row.get(key) is not None else None
    ... created_at=str(row["created_at"]), started_at=_s("started_at"), completed_at=_s("completed_at")
`str(datetime)` yields `2026-08-07 12:00:00.123456+00:00`. JobOut declares these as plain `str` (api.py:32-34), so pydantic does not normalize them — whatever string arrives is what ships.

So the same job's created_at is `...+00` from the list and `...+00:00` from the detail. Both use a space rather than 'T', so neither is ISO-8601.

This is not deliberate: `::text` is the repo-wide convention for timestamp serialization (app/features/auth/repo.py:14, app/features/data_accelerator/repo.py:177/259/318/353/438, app/features/quality/repo.py:16/124-125, app/features/library/repo.py:15/131). The jobs *detail* path is the lone deviation, and only because `get_job` uses `SELECT *`. Nothing in HANDOFF.md or ARCHITECTURE.md discusses timestamp format, and there is no comment justifying the split.

No test contradicts it: tests/test_ops.py:24-45 is the only /jobs test and asserts status/job_type/progress/404-scoping only — it never touches created_at. `grep -rn "created_at" tests/ | grep -i job` returns nothing.

One part of the impact statement is overstated: since BOTH forms use a space instead of 'T', a strict Safari `Date` parser would reject both, not one — the "renders correctly on one screen, Invalid Date on the other" split is not the real failure mode. The real defect is the inconsistent contract plus the fact that neither endpoint returns ISO-8601.

**Fix**

Make `get_job` (and `get_job_for_version` / `get_jobs_for_dataset`, which have the same `SELECT *`) use the same explicit column list as `list_jobs`, ideally via a shared `_JOB_COLS` constant, and drop the `_s`/`str()` timestamp handling in app/features/jobs/api.py. Better still, standardize on `to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')` (or declare the JobOut fields as `datetime` and let pydantic emit ISO-8601) so the API is actually ISO-8601 everywhere.

**Test to pin it**

"test_job_timestamps_are_identically_formatted_in_list_and_detail" — an integration test in tests/ (extend tests/test_ops.py::test_jobs_observability): upload a file, read the job from GET /api/v1/jobs and GET /api/v1/jobs/{id}, and assert detail["created_at"] == listing_item["created_at"] and that the value parses with datetime.fromisoformat / matches an ISO-8601 regex.

## [medium] Membership mutation routes (POST/PATCH/DELETE /teams/{id}/members) return 403 to non-members where every other route in the repo returns the existence-hiding 404.

- **domain:** ?
- **where:** 

**Evidence**

The cited lines are accurate. app/features/auth/api.py:128-129 (`list_members`): `if not principal.can(team_id, Permission.TEAM_READ): raise HTTPException(404, f"Team not found: {team_id}")`. app/features/auth/api.py:141-142 (`add_member`): `if not principal.can(team_id, Permission.TEAM_MANAGE): raise HTTPException(403, "Requires team:manage")` — and `Principal.can` (app/features/auth/deps.py:38-42) returns False identically for "not a member" and "member without the permission", so the handler cannot and does not distinguish them. Same 403 at api.py:157-158 (PATCH) and 176-177 (DELETE). No team-existence lookup precedes it, so a bogus team id also yields 403 (contrast create_user at api.py:85-86, which does `repo.get_team` → 404).

This is not "writes are exempt from the contract". The repo's own canonical write-path helper does exactly what the claim says add_member should: app/features/auth/deps.py:139-143 `ensure_dataset_permission` — "Readers of a team they don't belong to get 404 (existence hidden); members lacking write/delete get a truthful 403." And app/features/webhooks/api.py:51-58 `_ensure_can_create`, a TEAM_MANAGE-gated *write*, splits it explicitly: `role is None` → 404 "Team not found: {team_id}", else lacking manage → 403, under the docstring "Cross-team requests 404 rather than 403, matching the existence-hiding contract used everywhere else." HANDOFF.md:867 states the invariant ("Cross-team existence hiding (404 not 403) must extend to all new endpoints") and HANDOFF.md:649-651 restates it as cross-team 404 / in-team 403.

No test contradicts the claim: tests/test_teams_membership.py:37-42 asserts 404 for an outsider GET (and for a nonexistent team id), while the only 403 assertions on POST /members (lines 57-64) are in-team cases — a team admin over-granting and a viewer — which stay 403 under the correct pattern. No test exercises an outsider POST/PATCH/DELETE, and tests/test_teams_membership.py:1-6 states the intended contract as "non-members can't even see that a team exists (404)".

Mitigating: the 403 is uniform for existing-but-invisible and nonexistent teams, so no existence information actually leaks; the defect is a contract divergence on one resource, not a disclosure.

**Fix**

Extract the webhooks pattern into a shared helper and use it in add_member/update_member_role/remove_member: `if not principal.is_superuser and team_id not in principal.memberships: raise HTTPException(404, f"Team not found: {team_id}")` before the existing `if not principal.can(team_id, Permission.TEAM_MANAGE): raise HTTPException(403, "Requires team:manage")`. Existing in-team 403 tests keep passing.

**Test to pin it**

"test_membership_mutations_hide_team_existence_from_outsiders" in tests/test_teams_membership.py (integration layer, tests/): an outsider POST/PATCH/DELETE on /api/v1/teams/{tid}/members gets 404, as does the same call against a random UUID, while an in-team viewer still gets 403.

## [medium] GET /search/columns builds an ILIKE pattern from the raw query, so `%` and `_` in user input act as SQL wildcards. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/discovery/repo.py:26-28 is exactly as claimed and has no ESCAPE clause and no pre-escaping of the bind value:
  WHERE (col->>'normalized_name' ILIKE '%' || :q || '%'
         OR col->>'name' ILIKE '%' || :q || '%'
         OR col->>'original_name' ILIKE '%' || :q || '%'){team_clause}
params at repo.py:30 is `{"q": q, "tids": team_ids}` — the value is bound (so the claim's "not SQL injection" caveat is right), but binding does not neutralise LIKE metacharacters; Postgres interprets `%`/`_` inside the concatenated pattern.

The full path adds no escaping. Route app/features/discovery/api.py:166-178: `q: str = Query(..., min_length=1, ...)` — the only validation is non-empty; there is no regex/pattern constraint, no strip of wildcards, and q is passed straight to repo.search_columns. No Pydantic request model is involved (it is a query param).

grep for escaping over app/ finds only DuckDB single-quote escaping (`replace("'", "''")`) and filesystem `_sanitize`; nothing that touches LIKE metacharacters. `grep -rn "ESCAPE"` in app/ returns nothing relevant.

No test contradicts the claim. tests/test_discovery.py:41 and :88 only exercise q="cusip"; tests/unit/test_mcp_harness_smoke.py:190-201 stubs the HTTP call, so it never reaches this SQL. No test asserts wildcard behaviour either way.

Not documented as deliberate: the function docstring (repo.py:16-19) only says it searches by name and runs off Postgres schemas; nothing in it, the route docstring, or the repo docs mentions wildcards or pattern syntax.

Same defect exists in app/features/data_accelerator/repo.py:221-222, 287-288, 300-301 (dataset search and suggestions), so this is a systemic pattern, not a one-off — a real fix belongs in a shared helper.

Scope limit worth noting: the team_clause is a separate AND'd predicate, so a `%` query cannot cross tenants; it returns the caller's entire visible catalog only, exactly as the claim states.

**Fix**

Escape LIKE metacharacters in the bound value before it reaches the pattern, in a shared helper, e.g. `def like_contains(q: str) -> str: return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"`, then bind the whole pattern (`col->>'name' ILIKE :q_pattern`) rather than concatenating in SQL. Postgres' default LIKE escape is backslash, so no ESCAPE clause is needed; apply the same helper to data_accelerator/repo.py:221-222, 287-288, 300-301.

**Test to pin it**

"test_column_search_treats_percent_and_underscore_as_literal_characters" in tests/test_discovery.py (integration layer — it needs real Postgres ILIKE semantics against seeded columns; assert q="%" returns zero hits and that q="a_b" does not match a column named "axb").

## [medium] DELETE column-metadata's gone-column fallback passes the raw URL segment to the repo, so a physical spelling PUT accepted 404s once the column leaves the current schema.

- **domain:** ?
- **where:** 

**Evidence**

app/features/discovery/api.py:293-297 — `except StarletteHTTPException: if require_current: raise; return sheet, column_name` returns the URL segment verbatim; the success path returns `col.get("normalized_name") or col["name"]`. Entries are always stored normalized: put_column_metadata (api.py:317-319) writes `normalized`, and the existing test asserts `r.json()["column_name"] == "business_name"` after `PUT .../columns/Business Name` (tests/test_column_metadata.py:303-306). The repo does an exact match with no normalization: repo.py:528-534 `DELETE FROM dataset_column_metadata WHERE logical_sheet_id = :lsid AND column_name = :col`. The fallback is reachable because resolve_schema_column is `app/shared/query/validate.py::_resolve`, which raises ProblemException(400, "Unknown column") (validate.py:47-50), and ProblemException subclasses StarletteHTTPException (app/api/errors.py:58). So after the column disappears, `DELETE .../columns/Business%20Name` sends col="Business Name" against the stored "business_name" -> rowcount 0 -> 404 at api.py:384-385, while `DELETE .../columns/business_name` succeeds. This contradicts the DELETE handler's own docstring promise (api.py:375-379): "The column is normalized exactly as PUT/PATCH/GET normalize it, so any spelling those accept deletes the entry they created" — so the raw fallback is an oversight, not a documented choice; the docstring at api.py:271-275 only justifies falling back at all, not the spelling split. tests/test_column_metadata.py:332-357 (test_delete_still_reaches_an_entry_whose_column_is_gone) deletes only via `f"{base}/business_name"` at line 355, exactly as the claim says, so no test contradicts it.

**Fix**

In `_resolve_dictionary_target`'s fallback, apply the pure-string normalization rule instead of returning the raw segment: `return sheet, normalize_name(column_name)` (app/features/transform/compile.py:48, `re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")` — the same rule as shared/data_io.normalize_column_names). That makes both spellings collapse to the stored key whether or not the column is still in the current schema. (Note it cannot reproduce ingest's duplicate `_2` suffixes, so leave the schema-resolved path first, as it already is.)

**Test to pin it**

"test_delete_reaches_a_gone_column_entry_under_its_physical_spelling" — integration layer, tests/test_column_metadata.py: upload v1 with header "Business Name", PUT the entry, upload v2 without that column, then assert DELETE .../columns/Business%20Name returns 204 (today: 404).

## [medium] Discovery's two sheet-metadata list endpoints fabricate the Page envelope (limit=len(items), offset=0) and accept no limit/offset at all.

- **domain:** ?
- **where:** 

**Evidence**

app/features/discovery/api.py:245-252 (`list_sheet_metadata`) and :355-364 (`list_column_metadata`) both end with the literal `return Page(items=items, total=len(items), limit=len(items), offset=0)`. Neither signature declares `page: PageParams = Depends(pagination)` — unlike `search_columns` (api.py:169,178) and `dataset_timeline` (api.py:392,402), which use `Page.of(..., total, page)`. The repo layer is unbounded: repo.py:306-313 `SELECT ... WHERE dataset_id = :did ORDER BY sheet_key` and repo.py:518-525 `SELECT ... WHERE logical_sheet_id = :lsid ORDER BY column_name` — no LIMIT/OFFSET, no cap. Since FastAPI ignores undeclared query params, `?limit=20&offset=40` is silently dropped and the full result set is returned.

The envelope contract is stated in app/api/pagination.py:1-6 ("A single Page[T] envelope keeps list responses uniform: {items, total, limit, offset}") and the request-side contract is `limit: int = Query(50, ge=1, le=200)` at pagination.py:41. So `limit=0` on an empty list is a value the service's own documented range excludes.

Two corrections to the claim, neither fatal to it:
1. "a value the shared pagination dependency forbids (ge=1)" — the `ge=1` constraint lives only on the request dependency; the response model `Page.limit` is a bare `int` (pagination.py:24), so `limit=0` serializes fine and nothing 500s. The defect is a contract lie, not a crash.
2. "a client that sends ?limit=20 gets 200 rows back with limit=200" — the echoed limit equals `len(items)`, not 200; and `?limit=20` is dropped rather than honored, which is the real point.
Also, "both list endpoints in the domain" is loose: discovery has four `Page`-returning routes, and the other two (`/search/columns`, `/datasets/{id}/timeline`) paginate correctly.

Not by design: I found no docstring, comment, HANDOFF.md or ARCHITECTURE.md text justifying it (grep for "unpaginated / not paginated / limit=len" across *.md returns nothing; HANDOFF.md:582 only claims "Uniform API layer: /api/v1, `Page` envelope"). No test contradicts the claim either — no test in tests/ asserts `limit`/`offset` for the sheet-metadata routes. It is a copied convention: the same literal appears at auth/api.py:120,132, quality/api.py:81, library/api.py:67,205, data_accelerator/api.py:78 — i.e. the same defect exists at eight sites, which makes it a systemic envelope-contract violation rather than a one-off.

**Fix**

Add `page: PageParams = Depends(pagination)` to both handlers, push `limit`/`offset` into the two repo queries (`ORDER BY ... LIMIT :limit OFFSET :offset` plus a `COUNT(*)` for `total`), and return `Page.of(items, total, page)`. If some of the eight call sites are genuinely bounded collections that should never page, at minimum stop echoing `limit=len(items)`: emit a fixed sentinel that satisfies the documented range (e.g. `limit=total or 1`, or better a declared `paginated: false` variant) so a client can never see `limit=0` or infer a page size the request did not ask for.

**Test to pin it**

"test_sheet_metadata_and_column_dictionary_lists_honour_limit_and_offset_and_never_report_limit_zero" — belongs in tests/ (integration, needs a dataset with several logical sheets and a wide documented sheet): assert `?limit=2&offset=1` returns 2 items with `limit==2, offset==1` and `total` equal to the full count, and that an empty dictionary returns `limit>=1`.

## [medium] PUT /datasets/{id}/sheet-metadata/{sheet_key} never checks the sheet exists, so any typo'd key returns 200 and creates an unlinked, undeletable record. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

The mechanics of the claim check out end to end.

app/features/discovery/api.py:209-214 (put_sheet_metadata) does exactly two things — `ensure_dataset_permission(..., DATASET_WRITE)` then `repo.upsert_sheet_metadata(dataset_id, normalize_sheet_key(sheet_key), ...)`. There is no `get_live_logical_sheet` call and no other existence gate; `SheetMetadataIn` (api.py:63-76) only validates grain/primary_key_columns/description, so Pydantic cannot reject an unknown path key either.

app/features/discovery/repo.py:212-219 inserts unconditionally and resolves the link with a scalar subquery: `(SELECT id FROM dataset_sheets WHERE dataset_id = :did AND current_sheet_key = :key AND retired_at IS NULL)`. A subquery with no rows yields NULL, not an error, and the migration column is nullable — 20260806000000_logical_sheets.sql only ADDs the column, and HANDOFF.md:400 states `logical_sheet_id` is "nullable on dataset_sheet_metadata + quality_rules". `sheet_key` itself is plain `TEXT NOT NULL` with only a UNIQUE (dataset_id, sheet_key) index and no FK (20260804050000_discovery.sql:28-39). So the INSERT succeeds and RETURNING gives a 200 body.

The asymmetry with the sibling route is real: `_resolve_dictionary_target` (api.py:277-279) calls `repo.get_live_logical_sheet` and raises `HTTPException(404, f"Sheet not found: {sheet_key}")`, and every column route (PUT/PATCH/GET/DELETE) goes through it. One path segment deeper, the same bad key 404s.

The row is then listed forever: `list_sheet_metadata` (repo.py:306-310) selects by dataset_id only, with no join to dataset_sheets, and api.py:243-252 returns whatever it gets. Grepping app/ for a delete confirms there is no `delete_sheet_metadata` repo function and no DELETE sheet-metadata route (only `delete_column_metadata`, api.py:367). PUT-with-empty-body blanks the fields but the row stays in the list.

No test contradicts it: tests/test_discovery.py:241-258 only asserts PATCH 404s for an unknown key with no record present; nothing exercises PUT with a bogus key. test_sheet_metadata_on_hidden_sheet (line 287) is not a counterexample — hidden sheets do get a dataset_sheets row (app/features/files/repo.py:191-202 inserts one per sheet at ingest), so that key resolves normally.

I found no docstring, comment, HANDOFF.md or ARCHITECTURE.md text blessing arbitrary keys. The route docstring (api.py:202) says "for a logical sheet (by sheet_key)", presupposing the sheet exists. ARCHITECTURE.md:262 discusses sheet_key state only in the rename-rewrite context.

Two parts of the claim are overstated and should not be repeated:
- It does NOT "falsify" test_sheet_metadata_patch_requires_existing_record. That test's 404 is about no metadata record existing, which is the documented PATCH contract; PATCH succeeding after a PUT is correct behaviour, not a broken assertion.
- Documentation/health stats are immune: both the facets rollup (repo.py:105-107) and dataset_health (repo.py:378-380) count sheet metadata via `JOIN dataset_sheets ls ON ls.id = m.logical_sheet_id AND ls.retired_at IS NULL`, so a NULL-linked phantom is excluded from documented_sheets. The blast radius is the sheet-metadata list itself (and rowdiff.py:353, which matches on sheet_key and would only ever hit a phantom whose key equals a real sheet's).

**Fix**

In put_sheet_metadata (api.py:209), resolve the sheet before writing: `sheet = await repo.get_live_logical_sheet(dataset_id, normalize_sheet_key(sheet_key))`; `if sheet is None: raise HTTPException(404, f"Sheet not found: {sheet_key}")` — the same guard `_resolve_dictionary_target` already applies for the column routes — then pass `sheet["current_sheet_key"]` to the upsert. If pre-existing NULL-linked rows must remain writable, allow the key through when `repo.get_sheet_metadata` already returns a record.

**Test to pin it**

"test_sheet_metadata_put_rejects_a_sheet_key_that_names_no_sheet" — integration, tests/test_discovery.py: upload a workbook, PUT /datasets/{id}/sheet-metadata/nope expecting 404 (matching the column route's "Sheet not found"), then assert GET /sheet-metadata still totals 0.

## [medium] GET /datasets/{id}/usage counts every audit row for the dataset regardless of status_code, so 403/404/422 failures inflate writes and downloads. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/discovery/repo.py:543-559 — `dataset_usage` is a single aggregate over `audit_log` with `WHERE path LIKE '%/datasets/' || :did || '%'` and no `status_code` predicate: `COUNT(*) FILTER (WHERE path LIKE '%/download') AS downloads`, `COUNT(*) FILTER (WHERE method IN ('POST','PUT','PATCH','DELETE')) AS writes`, `COUNT(*) AS total_events`. status_code is stored (app/shared/audit.py INSERT includes `status_code`) and is used elsewhere — the timeline query at repo.py:634-639 surfaces `'status_code', a.status_code` — so the omission in usage is not a missing column, it is a missing filter.

app/api/middleware.py:49-72 — AuditMiddleware runs after `call_next` and records unconditionally on the final response: `if (request.method in _MUTATING or is_download) and not any(path.endswith(s) for s in _SKIP_AUDIT_PATHS): ... status_code=response.status_code`. The only exclusions are /health, /openapi.json, /docs, /redoc. install_middleware (line 75-80) adds AuditMiddleware first, so it sits outside Starlette's ExceptionMiddleware and therefore sees the 403/404/422 response produced from an HTTPException raised in `ensure_dataset_permission` or from pydantic request validation. So a denied write or a failed download does get a row, and that row is counted.

Route path confirms nothing filters later: app/features/discovery/api.py:417-424 just does `stats = await repo.dataset_usage(dataset_id)` and splats it into `UsageResponse` (api.py:141-146: downloads, writes, total_events, last_activity_at) — no reads field, so the repo docstring's "Read/write/download counts" (repo.py:544) over-promises; only /download GETs are audited, all other reads are not, making total_events effectively downloads+writes.

Not contradicted by tests: tests/test_discovery.py:76-78 only asserts `usage["downloads"] >= 1 and usage["writes"] >= 1` — a weak lower bound that passes either way. No test asserts failed requests are excluded.

Not documented as deliberate: ARCHITECTURE.md:100-101 and :452 and HANDOFF.md:347/506 describe usage as "derived from the audit trail" but never say failures are intentionally included; there is no comment or docstring defending it (contrast the heavily documented 404-not-403 choice).

Only overstatement in the claim: MCP context.py:480-496 renders the same fields, so the inflation propagates there too — the claim understates rather than overstates.

**Fix**

Add `AND status_code &lt; 400` to the WHERE clause of `dataset_usage` (repo.py:555), so downloads/writes/total_events/last_activity_at reflect successful activity only. If failure counts are wanted for the UI, add explicit `COUNT(*) FILTER (WHERE status_code &gt;= 400) AS failed_events` and expose it on `UsageResponse` rather than folding it into the success counts. Separately, fix the repo.py:544 docstring to stop promising a read count, or rename `total_events` to `audited_events`.

**Test to pin it**

"test_dataset_usage_excludes_failed_requests" — a denied write and a 404 download against a dataset must not increase writes/downloads/total_events; belongs in tests/ (integration, needs the real middleware + audit_log), e.g. tests/test_discovery.py alongside the existing usage assertions at line 76.

## [medium] Discovery registers no GET on the single sheet-metadata or column-dictionary item paths, so GET there returns 405 despite ARCHITECTURE.md advertising GET on them.

- **domain:** ?
- **where:** 

**Evidence**

I tried to refute this and could not. Route inventory of app/features/discovery/api.py (grep of every @router decorator): line 196 PUT and 217 PATCH on `/datasets/{dataset_id}/sheet-metadata/{sheet_key}`; line 300 PUT, 325 PATCH, 367 DELETE on `/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}`. The only GETs in that family are collection routes: line 243 `GET /datasets/{dataset_id}/sheet-metadata` (list) and line 353 `GET /datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns` (list). No GET is registered on either item path, and no catch-all exists; Starlette matches the path, finds no GET in the method map, and returns 405 (app/api/errors.py:41 even carries the "405: Method Not Allowed" title).

The repo functions the claim names are real and orphaned as described. app/features/discovery/repo.py:240 `get_sheet_metadata(dataset_id, sheet_key)` and repo.py:469 `get_column_metadata(logical_sheet_id, column_name)` are each called from exactly one place in app/: repo.py:285 and repo.py:502, both under `if not sets:  # nothing to change — a no-op PATCH is not an error`. (Note the grep also hits `data_accelerator/services/datasets.py:107 get_sheet_metadata` behind `GET /datasets/{id}/sheets/{sheet_name}` — that is a different, same-named function returning schema+preview for a version sheet, not the semantic `dataset_sheet_metadata` record, so it does not rescue the claim.)

Not by design: ARCHITECTURE.md:453 explicitly advertises `GET/PUT/PATCH/DELETE  …/sheet-metadata[/{sheet_key}[/columns[/{col}]]]`, and the DELETE docstring at api.py:369-375 says "The column is normalized exactly as PUT/PATCH/GET normalize it" — both documents assume an item GET that is not implemented. HANDOFF.md:294-304 discusses PUT vs PATCH semantics at length and never says reads are list-only on purpose. No doc or comment defends the omission.

No test contradicts it: every test that reads this data goes through the list route (tests/test_discovery.py:37,170,251,299; tests/test_logical_sheets.py:123; tests/test_wave0_journeys.py:181; tests/test_column_metadata.py:264 lists `/columns`). That is exactly the list-and-filter-client-side workaround the claim predicts.

Severity is medium, not high: the list endpoints are unpaginated (`Page(items=..., limit=len(items), offset=0)`, api.py:250-253 and 361-365), so the UI can render any screen — it is an inconsistent, awkward contract plus a doc/route mismatch, not a wrong answer.

**Fix**

Add two read routes in app/features/discovery/api.py mirroring the write routes' auth and key normalization: `@router.get("/datasets/{dataset_id}/sheet-metadata/{sheet_key}", response_model=SheetMetadataOut)` calling `repo.get_sheet_metadata(dataset_id, normalize_sheet_key(sheet_key))` and 404ing on None; and `@router.get("/datasets/{dataset_id}/sheet-metadata/{sheet_key}/columns/{column_name}", response_model=ColumnMetadataOut)` calling `_resolve_dictionary_target(...)` then `repo.get_column_metadata(sheet["id"], normalized)`, 404ing on None, returning `ColumnMetadataOut(**row, sheet_key=sheet["current_sheet_key"])`. Both gate on `Permission.DATASET_READ`. Declare the column route before the `/columns` list route is irrelevant (distinct segment counts), but keep the `_resolve_dictionary_target` call so GET and DELETE agree on column identity.

**Test to pin it**

"test_get_single_column_dictionary_entry_returns_the_record_and_404s_when_absent" (and a sibling for sheet-metadata) in tests/test_column_metadata.py — integration layer (tests/), since it needs a real dataset, version schema and normalization through _resolve_dictionary_target.

## [medium] Discovery emits two timestamp encodings — ISO-8601 'T' form in health freshness evidence vs Postgres ::text space-separated form everywhere else, including inside the same health response.

- **domain:** ?
- **where:** 

**Evidence**

The claim is factually correct, and the inconsistency is worse than stated (it occurs inside a single response body).

ISO-8601 side: `app/features/discovery/health.py:212` — `"created_at": created_at.isoformat(),` (the claim cites :213, off by one; :213 is `age_seconds`). The value is a real Python `datetime`: `health.py:315` passes `current.get("created_at")` from `get_current_version()`, whose query is `SELECT dv.* FROM dataset_versions dv` (`app/shared/repo.py:77`) — no `::text` cast, so the driver returns a `datetime`, and `.isoformat()` yields `2026-08-07T12:00:00.123456+00:00`.

Postgres ::text side, exactly as cited: `app/features/discovery/repo.py:195` (`updated_at::text AS updated_at` in `_SM_COLS`), `:407` (same in `_CM_COLS`), `:553` (`MAX(occurred_at)::text AS last_activity_at`), `:652` (`occurred_at::text AS occurred_at`). These reach the wire unmodified because the response models declare them as plain `str`, not `datetime`, so pydantic performs no re-serialization: `app/features/discovery/api.py:96` `updated_at: str`, `:138` `updated_at: str`, `:146` `last_activity_at: str | None = None`, `:155` `occurred_at: str`. `timestamptz::text` renders as `2026-08-07 12:00:00.123456+00` — space separator, hour-only offset.

Stronger than the claim: the two encodings collide inside ONE response. `GET /datasets/{id}/health` (`api.py:405-414`) returns `dimensions.freshness.evidence.created_at` in ISO-T form, while `dimensions.validation.evidence.completed_at` is built at `health.py:106` from `latest_completed_run()`, whose `_RUN_COLS` (`app/features/quality/repo.py:125`) is `completed_at::text AS completed_at` — so it is the space-separated form. `HealthDimension.evidence` is `dict[str, Any]` (`health.py:44`), so nothing normalizes it.

Not by design: no docstring, comment, ARCHITECTURE.md or HANDOFF.md states a timestamp convention (grep for `iso.8601|isoformat|::text` across the repo's markdown returns only an unrelated PII-regex mention in WAVE4-6-PLAN.md:283). The `::text` cast is genuinely service-wide (e.g. `app/features/library/repo.py:15,131,151,403`), confirming health's `.isoformat()` is the outlier.

No test contradicts it: `tests/test_health.py:95-96` asserts only `dims["freshness"]["status"]` and `evidence["refresh_frequency"]`; `tests/unit/test_health_evaluators.py:124-143` asserts only statuses. No test anywhere pins the serialized shape of `occurred_at`, `updated_at`, `last_activity_at` or `evidence.created_at`.

Downgraded from critical/high: no value is wrong, and Python's `datetime.fromisoformat` accepts both forms on 3.11+. The Safari `new Date()` NaN detail in `impact_on_ui` is a plausible but unverified consequence, not something the source proves.

**Fix**

Make health match the service-wide `::text` convention rather than the reverse, since `::text` is what every other repo emits. In `app/features/discovery/health.py:212`, drop the re-encoding and echo the source form — capture the raw value before the `datetime.fromisoformat` coercion at `health.py:204-205` and put that raw string in `evidence["created_at"]`, using `.isoformat()` only when the input was already a `datetime`. Better still, normalize at one seam: give `HealthDimension` a field serializer (or a small `_ts()` helper used by both `evaluate_freshness` and `evaluate_validation`) that renders every timestamp identically, and while there fix `health.py:106`'s `str(run.get("completed_at"))`, which stringifies `None` into the literal `"None"`.

**Test to pin it**

"test_health_evidence_timestamps_use_the_same_encoding_as_usage_and_timeline" in tests/ (integration layer — it needs a real dataset with a ready version, a completed validation run, and timeline activity so it can assert that `dimensions.freshness.evidence.created_at`, `dimensions.validation.evidence.completed_at`, and `GET /datasets/{id}/usage`'s `last_activity_at` all match one single timestamp regex).

## [medium] Unfiltered list_artifacts drops the service's Page.total and can never emit a "more exist" hint, so a truncated page reads as the whole store. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every factual assertion in the claim checks out against the source.

1. `fetch = limit` on the unfiltered path — /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/artifacts.py:51-53:
   `filtering = bool(dataset_id or kind)` / `fetch = 1000 if filtering else limit` / `page = await ctx.client.get("/samples", limit=fetch, offset=offset)`.

2. `clipped` is dead on that path — artifacts.py:55-68. With no filters both predicates at :64-65 are vacuously true, so `rows` is exactly `page_items(page)`, and the service caps the page at `limit` (app/features/files/api.py:719-724, `limit: int = Query(100, ge=1, le=1000)`; the repo call at :735-736 passes it straight to `list_visible_artifacts(..., limit=limit, offset=offset)`). Therefore `len(rows) <= limit` and `clipped = len(rows) > limit` at :67 can never be True unfiltered — so the "More matches exist — raise limit or use offset." string at :82 is unreachable on the unfiltered path.

3. The real total is present and discarded — app/features/files/api.py:748 returns `Page(items=entries, total=total, limit=limit, offset=offset)` where `total` comes from `library_repo.list_visible_artifacts` (:735), i.e. the full visible artifact count, not the page length. `Page.total: int` is declared at app/api/pagination.py:19-25. artifacts.py:81 nonetheless passes a hardcoded `None`: `render.count_note(len(rows), None, noun="artifacts")`, and render.count_note (app/features/mcp/render.py:68-73) returns the bare `f"{shown} {noun} shown."` when total is None. So with 5000 artifacts and limit=50 the output is exactly "50 artifacts shown." with no total and no offset hint — the claim's predicted rendering.

4. It is not deliberate and it is inconsistent with its own siblings. Nothing in the docstring, the :47-50 comment (which only explains the 1000-row filtered scan), HANDOFF.md or ARCHITECTURE.md mentions the total; `grep -rni list_artifacts *.md` returns nothing. Meanwhile the neighbouring tools do pass it: app/features/mcp/tools/orient.py:112 `render.count_note(len(rows), page.get("total"), noun="datasets")`, orient.py:279, context.py:764, curate.py:853 — all `page.get("total")` off the same Page envelope. read_artifact in this very file (artifacts.py:135-139, 152) both passes a total and emits an explicit `offset=` hint.

5. No test contradicts it. tests/unit/test_mcp_artifacts.py covers the filtered clipping path only — test_more_matches_than_the_limit_are_announced_rather_than_dropped (:239-253) and test_a_full_page_of_matches_is_not_announced_as_having_more (:256-266) both call `list_artifacts(kind="query_output", limit=2)`. No test exercises an unfiltered call against a store larger than `limit`, so nothing pins the current behaviour as correct.

The one nuance the claim does not state: passing `page["total"]` blindly would be wrong on the *filtered* path, where the service's total counts unfiltered artifacts. That makes the fix conditional, not unconditional — but it does not rescue the unfiltered path, which is what the claim is about.

**Fix**

In app/features/mcp/tools/artifacts.py, keep the total only when it is meaningful, i.e. when not filtering:

    total = None if filtering else page.get("total")
    more = clipped or (
        isinstance(total, int) and total > offset + len(rows)
    )
    ...
    render.count_note(len(rows), total, noun="artifacts"),
    (f"More artifacts exist — call again with offset={offset + len(rows)}." if more else ""),

count_note already suppresses the "of N" when total <= shown, so an exact-fit page still renders "N artifacts shown." and the existing filtered-path tests keep passing.

**Test to pin it**

tests/unit/test_mcp_artifacts.py (unit layer): "test_an_unfiltered_page_reports_the_store_total_and_how_to_reach_the_rest" — stub GET /samples with 50 entries and total=5000, call list_artifacts(limit=50), assert "50 of 5000 artifacts shown." and an "offset=50" hint appear; plus a complement "test_an_unfiltered_page_that_is_the_whole_store_is_not_announced_as_having_more" with total=3, limit=50.

## [medium] list_artifacts forwards `offset` to the unfiltered /samples scan while `limit` clips the locally filtered matches, so filtered paging repeats or skips rows and reports no total.

- **domain:** ?
- **where:** 

**Evidence**

The claim's three factual assertions all hold in the source.

1. `offset` goes to the raw scan, not the matches — `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/artifacts.py:51-53`:
```
filtering = bool(dataset_id or kind)
fetch = 1000 if filtering else limit
page = await ctx.client.get("/samples", limit=fetch, offset=offset)
```
`offset` is forwarded unconditionally, including when `filtering` is true.

2. The filter is applied in process and `limit` clips the *matches* — artifacts.py:55-68 (`if (dataset_id is None or item.get("dataset_id") == dataset_id) and (kind is None or item.get("file_type") == kind)`, then `clipped = len(rows) > limit; rows = rows[:limit]`). So the two paging knobs act on two different sequences: `offset` indexes the raw artifact stream, `limit` indexes the filtered stream.

3. No total — artifacts.py:81 `render.count_note(len(rows), None, noun="artifacts")`, and `count_note` in app/features/mcp/render.py:68-73 returns a bare `"N artifacts shown."` when `total is None`. The `Page` from the route does carry `total` (app/features/files/api.py:748 `return Page(items=entries, total=total, ...)`), so it is available and deliberately dropped.

Server-side filtering genuinely does not exist, so the local filter itself is justified: `GET /samples` (app/features/files/api.py:719-724) takes only `limit`/`offset`.

Nothing makes this by design. There is a comment at artifacts.py:47-50 explaining the wide scan, but it says nothing about `offset`. The only documentation of `offset` semantics is the empty-match branch's advice (artifacts.py:72-77, "Scanned the 1000 most recent artifacts... retry with offset"), where forwarding to the raw scan is coherent — but the non-empty branch's advice at artifacts.py:82, "More matches exist — raise limit or use offset.", promises paging over matches, which the code does not do.

No test contradicts the claim, and none pins the filtered+offset case. tests/unit/test_mcp_artifacts.py:126-137 asserts offset is forwarded only in the *unfiltered* case ("asks for exactly the page the caller wanted"); tests/unit/test_mcp_artifacts.py:145-155 pins `limit == 1000` for the filtered scan without asserting anything about offset; the "more matches exist" test (tests/unit/test_mcp_artifacts.py:238-251) asserts the string is emitted but never exercises the follow-up call it advises.

Concrete failure: 300 artifacts, of which 25 are kind=pivot_output scattered through them. `list_artifacts(kind="pivot_output", limit=10)` scans raw rows 0-999, matches 25, shows 10, says "More matches exist — raise limit or use offset." The model follows that and calls `offset=10`, which moves the *raw* window by 10, so the scan still contains matches 1..25 minus whichever of the first 10 raw rows were matches — the reply repeats most of page 1 rather than advancing. With a dense mix the same call can instead jump past matches. With no total, neither the model nor a UI can tell which happened.

**Fix**

Keep `offset` off the wire when filtering: request `/samples` with `limit=1000, offset=0`, build the full `matches` list, then slice `matches[offset : offset + limit]`. Report the real denominator — `render.count_note(len(rows), len(matches))` in the filtered case and `render.count_note(len(rows), page.get("total"))` in the unfiltered case — and change the trailing advice at artifacts.py:82 to name the concrete next offset (`offset={offset + len(rows)}`), the way read_artifact already does at artifacts.py:136-139. The saturated-scan message (artifacts.py:72-77) then needs its own wording, since `offset` no longer moves the scan window; a scan of >1000 artifacts should either loop the fetch or say plainly that the window is capped.

**Test to pin it**

"test_paging_a_filtered_listing_advances_through_the_matches_not_the_raw_artifacts" in tests/unit/test_mcp_artifacts.py (unit layer, driven through FakeAnalyticsClient): script a /samples page whose matches are interleaved with non-matches, call with limit=2, offset=2, and assert the second page shows matches 3 and 4 (and that the wire params are limit=1000, offset=0). A companion, "test_a_filtered_listing_reports_how_many_artifacts_matched", asserts "2 of 5 artifacts shown."

## [medium] The 60k-char response ceiling is applied per-tool and query_rows/aggregate/pivot/list_artifacts and the orient tools skip it, so their responses are unbounded.

- **domain:** ?
- **where:** 

**Evidence**

The factual core of the claim holds. `MAX_RESPONSE_CHARS = 60_000` is declared in /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/look.py:38-39 with the docstring "Ceiling on a single response. A wide SELECT can otherwise emit tens of MB." — but it is only applied at look.py:190-204 (run_sql). `query_rows` in the same module ends at look.py:124-129 with a bare `return render.join(...)`, no clamp, and its `limit` is `Field(..., ge=1, le=1000)` (look.py:104) with `columns` optional ("Omit for all", look.py:91-94).

A repo-wide grep confirms the split exactly as claimed: clamp() is called only in look.py:190 (run_sql), artifacts.py:141 (read_artifact), context.py:349/399/644/761/847/872 (get_lineage, get_activity, list_relationships, list_saved_objects), curate.py:703/752/850 (run_quality_check, manage_tags), pipeline.py:255/400/418/557/654 (manage_relationships, join_datasets, transform_data). The cited unclamped sites are real: compute.py:208-218 (aggregate, limit le=1000 at :160), compute.py:280-291 (pivot, limit le=1000 at :255), artifacts.py:79-86 (list_artifacts), and every return in orient.py (describe_dataset, get_data_dictionary, search_datasets, search_columns, get_dataset_health).

Nothing further down the path bounds it: the service's own request model caps only row count (app/features/explorer/schemas.py:87 `limit: int | None = Field(default=None, ge=1, le=1000)`), never response bytes; render.scalar caps a *cell* at 80 chars (render.py:12) but nothing caps column count; and there is no size/limit logic at all in app/features/mcp/server.py or asgi.py. So 1000 rows x 200 columns x ~81 chars is genuinely emitted whole.

No doc or comment defends the omission: grep for "clamp"/"60_000"/"MAX_RESPONSE" across all .md files returns nothing, and no test asserts query_rows is deliberately unclamped. The opposite is documented — tests/unit/test_mcp_look.py:592-611 (`test_an_enormous_result_is_cut_at_the_response_ceiling_with_a_way_forward`) states the rationale for run_sql: "A wide SELECT over 500 rows can emit megabytes, which blows the caller's context window before it can read anything." query_rows is the same shape of output with twice the row ceiling.

One correction to the claim's framing: query_rows does not silently drop data. It never truncates, and it does emit `render.count_note(len(rows), payload.get("total"))` plus a `next_cursor` note (look.py:62-66, 127), so row-level completeness *is* signalled. The defect is an unbounded response / context blow-up and an inconsistent contract across one tool surface — not a wrong answer. read_artifact (limit le=500) clamps while query_rows (limit le=1000) does not, which is the clearest sign this is an oversight rather than a decision.

**Fix**

Move the ceiling out of the individual tools: wrap it into the `guard` decorator in app/features/mcp/tools/_common.py so every tool response passes through `clamp(result, MAX_RESPONSE_CHARS)` with a generic hint, and keep the existing per-call `clamp(...)` sites only where a tool-specific hint is worth it (they become no-ops once already under budget). Promote MAX_RESPONSE_CHARS to _common.py and drop the duplicate literals in look.py, context.py, artifacts.py, curate.py and pipeline.py. Separately, for query_rows the hint should name its own parameters ("project fewer `columns`, or lower `limit`").

**Test to pin it**

"test_query_rows_on_a_wide_sheet_is_cut_at_the_response_ceiling_with_a_way_forward" in tests/unit/ (tests/unit/test_mcp_look.py, mirroring the existing run_sql case at line 592), plus a table-driven "test_every_registered_tool_response_is_bounded_by_the_response_ceiling" in tests/unit/ that stubs an oversized payload for each tool so a newly added tool cannot reintroduce the gap.

## [medium] list_saved_objects sorts a silently truncated 1000-item window, so with >1000 saved objects of one kind the ordering is wrong with no disclosure. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Half the claim holds, half is factually wrong.

TRUE — silent truncation with no disclosure. app/features/mcp/tools/context.py:142-147: `if not chunk or len(chunk) < PAGE_SIZE or len(items) >= cap: break` ... `return items[:cap], total`. `_fetch_all` returns no exhausted flag, and its caller at :852 (`items, total = await _fetch_all(...)`) then sorts the possibly-truncated list at :864 `items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=(order == "desc"))` and windows at :865. Contrast `_scan` at :159-184, which returns `exhausted` and whose callers say so (:522-526 "filtered locally over the {scanned} most recent of {all_events} events", artifacts.py:73 "Scanned the {scanned} most recent artifacts"). The module docstring at :24-31 does state the norm: "Scanning a bounded window and reporting the window is honest". Nothing in tests/ exercises the `_fetch_all` cap (grep for SCAN_CAP/_fetch_all hits only `_scan` tests), and no comment/HANDOFF text defends the silence, so it is an oversight, not a documented choice. `created_at` exists on all five OutSchemas (quality/schemas.py:87, explorer:79, transform:51, library:58/149/186), so the sort really does reorder relative to the server order.

FALSE — the count_note. :875 passes `total if isinstance(total, int) else len(items)`, and `total` comes from the server Page. Every one of these routes returns a true global total: quality/api.py:75-81 `Page(items=items, total=len(items), ...)` over `repo.list_rules` (all rows), and library/transform/explorer all return `Page[...]` with a real count. So with 1500 rules the note reads "50 of 1500 rules shown" — accurate, not "confidently wrong".

FALSE — the direction of the sort error. The claim says asc returns "the oldest of the newest 1000". Server order is not newest-first: quality/repo.py:54 `ORDER BY created_at` (ascending), transform/repo.py:55 `ORDER BY t.name`, explorer/repo.py:217 `ORDER BY v.name`, library/repo.py:44 and :434 `ORDER BY name`. So the retained window is the oldest 1000 (rules) or the alphabetically first 1000 (views/analytics/charts/transformations) — for the name-ordered kinds both asc AND desc can be wrong, which is a different and slightly broader error than claimed.

Also overstated: "a saved-objects browser silently loses everything past the first 1000". limit is capped at 200 and offset at 200 (:800-801), so the tool can never address past index 399 regardless of the cap; the cap is not the binding reachability limit. The residual real defect is ordering only. One case the claim missed but which is the same root cause: :838, rule lookup by object_id scans only the first 1000 rules and returns "No quality rule with id ..." for a rule that exists.

**Fix**

Make `_fetch_all` return the exhausted signal like `_scan` does — e.g. `return items[:cap], total, len(items) <= cap` — and in list_saved_objects append a note when not exhausted: "Sorted over the first {cap} of {total} {segment} the service returned; ordering beyond that window is not reflected." Same flag should gate the rule-by-id lookup at :838 so a miss says "not found in the first 1000 rules scanned" rather than a flat not-found.

**Test to pin it**

"test_list_saved_objects_says_so_when_the_sort_ran_over_a_truncated_window" in tests/unit/test_mcp_context.py, using the fake client to serve 1500 views and asserting the output both reports the true total and warns that only the first 1000 were sorted.

## [medium] list_artifacts applies `offset` to the raw /samples scan but tells a clipped filtered listing to "use offset", so paging repeats the same matches. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

app/features/mcp/tools/artifacts.py:51-53 — `filtering = bool(dataset_id or kind); fetch = 1000 if filtering else limit; page = await ctx.client.get("/samples", limit=fetch, offset=offset)`. The offset goes straight to the service unmodified (client.py does no rewriting; `page_items` at _common.py:241 just returns `page["items"]`), so it skips RAW artifact rows, not matches. Filtering then happens in-process at lines 55-66, and clipping at 67-68 (`clipped = len(rows) > limit; rows = rows[:limit]`). Line 82 emits "More matches exist — raise limit or use offset." for that clipped case.

The two offset semantics are inconsistent within the same function: the empty-result branch (lines 72-77, "Scanned the {scanned} most recent artifacts … retry with offset") correctly treats offset as a raw scan-window move, and the unfiltered path (fetch = limit) makes offset a true page offset. Only the clipped-filtered note describes offset as if it paged the filtered list.

Failure is real, not just cosmetic: with 5 matches scattered anywhere inside the first 1000 raw rows and limit=2, the first call returns matches #1-2 and prints the note; offset=2 re-scans from raw row 2 and, if those two skipped raw rows were non-matching, the filtered list is unchanged and the tool returns matches #1-2 again — a "next page" that is byte-identical to the first. If exactly one skipped row matched, the page overlaps by one. It only appears to work in the single case where every scanned row matches.

No test covers offset in the clipped case. tests/unit/test_mcp_artifacts.py:239-253 asserts the note string but uses a fixture where all 5 rows match, so it cannot catch this; :128-137 pins offset pass-through only for the UNFILTERED call ("params == {limit: 5, offset: 10}"); :296-312 pins the empty-result "retry with offset" wording. Nothing in the module comments (lines 47-50 explain only the 1000-row widest-scan choice), HANDOFF.md or ARCHITECTURE.md documents offset as intentionally raw-in-a-filtered-listing, so this is not a documented deliberate trade-off.

**Fix**

Smallest correct fix: make the clipped note stop advising offset when filtering, since offset is a scan-window control there — e.g. `"More matches exist in the scanned window — raise limit." if clipped else ""` (keep the offset advice only for the unfiltered path, where offset really is a page offset). The larger fix, if filtered paging is wanted, is to apply offset to the filtered list instead: fetch with `offset=0` when `filtering`, then `rows = rows[offset:offset + limit]` with `clipped = len(rows_all) > offset + limit`.

**Test to pin it**

"test_a_clipped_filtered_listing_does_not_advise_an_offset_that_re_lists_the_same_matches" in tests/unit/ (tests/unit/test_mcp_artifacts.py, alongside test_more_matches_than_the_limit_are_announced_rather_than_dropped): serve a page where matches are interleaved with non-matching rows, call with kind=... and limit=2, then call again with offset=2 and assert the second result shares no filename with the first (or, for the minimal fix, assert the clipped note does not contain "use offset" when a filter is set).

## [medium] read_sample_data raises bare HTTPException 400s for unknown projection/sort columns, discarding the `available` set it just computed, so MCP read_artifact errors carry no column list.

- **domain:** ?
- **where:** 

**Evidence**

The cited code is exactly as claimed. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/services/downloads.py:346-350 — `available = {r[0] for r in desc}` / `missing = [...]` / `raise HTTPException(400, f"Columns not found: {missing}")`; and :366-369 — `available = {r[0] for r in desc}` / `if sort_by not in available: raise HTTPException(400, f"Sort column not found: {sort_by}")`. `available` is discarded in both. Note the very next branch in the same function (:370-374) DOES use `ProblemException(..., code="invalid-sort-order", available=list(SORT_ORDERS))`, so the richer convention was in the author's hand three lines away.

Call path checked end to end: MCP tool `read_artifact` (app/features/mcp/tools/artifacts.py:98-124) → `ctx.client.get(f"/samples/{filename}/data", ...)` → route `read_sample` (app/features/files/api.py:758-792) → `read_sample_data`. Nothing between them enriches the error. `columns` is a free-form comma-joined string on the route (api.py:764, split at :783), so pydantic cannot reject an unknown name — the handler-level check really is the only gate. `sort_by` is a bare `str | None` Query, same story.

Client/render path: client.py:_problem_from (120-125) sets `code = body.get("code") or f"http-{status}"`; FastAPI's HTTPException body is `{"detail": ...}` mapped by the app's handler to code "bad_request" (tests/unit/test_mcp_artifacts.py:617 uses exactly `problem(400, ..., "bad_request")`). In _common.explain, `if exc.status == 400 and code == "bad_request": return exc.detail` (app/features/mcp/tools/_common.py:209-210) — so the model gets the detail string only. The `unknown-column` branch it misses is at _common.py:108-115, which renders `"... Available columns: {available}. Call describe_dataset to see the full schema."` via `ProblemError.available_columns` (client.py:50-54, reads `extra["available"]`).

No deliberate rationale anywhere: read_sample_data's docstring (downloads.py:319-330) only explains `key` and the sort_order defensive check; _common.py's module docstring and `require_sort_order` (lines 1-15, 41-68) document why the client-side sort mirror was dropped, not why this error is thin. No existing test asserts the 400 body for these two cases at the HTTP layer.

Two overstatements in the claim, neither fatal: (a) "everywhere else in this service" is false — the same bare-400 pattern exists at downloads.py:183 (download_dataset) and data_accelerator/services/profiling.py:212; the ProblemException convention holds on the schema-driven query paths (shared/query/validate.py:49, transform/expr.py:200, relationships/service.py:85). (b) tests/unit/test_mcp_artifacts.py:608-624 and :645-655 currently assert the thin message verbatim (`assert message == "Columns not found: ['revenu']"`), but they were written by the same session that filed this risk and only pin observed behaviour plus the "no (code: ...) suffix" rule — they do not justify the omission.

**Fix**

In downloads.py replace both raises with the repo's convention: `raise ProblemException(400, f"Columns not found: {missing}", code="unknown-column", columns=missing, available=sorted(available))` at :350 and `raise ProblemException(400, f"Sort column not found: {sort_by}", code="unknown-column", column=sort_by, available=sorted(available))` at :369 (ProblemException is already imported/used at :371). One caveat the fix must handle: _common.explain's unknown-column branch appends "Call describe_dataset to see the full schema", which is wrong advice for an artifact (artifacts have no dataset schema to describe) — either soften that sentence or emit a distinct code (e.g. "unknown-artifact-column") with its own explain branch. Update tests/unit/test_mcp_artifacts.py:624 and :655, which assert the current verbatim string.

**Test to pin it**

"test_an_unknown_projection_column_on_an_artifact_lists_the_columns_that_do_exist" in tests/ (integration, against GET /api/v1/samples/{filename}/data so the whole route→service→problem-handler path is exercised), plus updating the two MCP renderer cases in tests/unit/test_mcp_artifacts.py to assert "Available columns:" appears.

## [medium] get_data_dictionary renders column description and allowed_values through render.table, cutting both at 79 chars + "…"

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/render.py:12 `MAX_CELL = 80`; :51 `out += [" | ".join(scalar(row.get(col)) for col in columns) for row in rows]` — table cells always use the default limit; :25-27 `if limit is not None and len(text) > limit: text = text[: limit - 1] + "…"`. render.fields (:36) is the only escape hatch (`scalar(v, limit=None)`), and its docstring says so: "Not truncated: these carry prose ... where the tail is usually the actionable part."

orient.py:220-231 builds column_rows including `"allowed_values": ", ".join(...)` and `"description": c.get("description")`, then orient.py:242 `render.section(f"Columns — {sheet_key}", render.table(column_rows))` — so both prose fields go through the 80-char cell path.

The 2000-char ceiling is real: app/features/discovery/api.py:108 `description: str | None = Field(default=None, max_length=2000)`; `allowed_values: list[Any] | None` (:116) has no length bound at all, so a 30-value enum is definitely cut.

The asymmetry the claim points at is explicit in the source: orient.py:296-297 "Rendered as lines rather than a table: the summary is the actionable part and must not be truncated to fit a column," and tests/unit/test_mcp_orient.py:404 `test_a_long_health_summary_is_never_truncated` pins exactly that for health while no test covers the dictionary. Nothing in tests/unit/test_mcp_orient.py:237-360 (the get_data_dictionary block) asserts a long description survives; test_a_closed_set_of_numeric_codes_is_still_rendered only uses `[10, 20, 30]`. No comment, docstring, HANDOFF.md or ARCHITECTURE.md entry defends truncating the dictionary — grep for MAX_CELL / "cell limit" in the .md files returns nothing.

One overstatement in the claim: truncation is NOT completely unsignposted — scalar appends "…", so the output does show a marker. The loss is still unrecoverable (no "call X for the full text" affordance and no untruncated route surfaced by any MCP tool), and for allowed_values the cut lands mid-value, so the visible tail of the enumeration is a fragment that reads like a value.

**Fix**

In get_data_dictionary, keep the short/enumerable fields in render.table (column, business_name, semantic_type, unit, sensitivity) and emit the prose fields per column outside the table, e.g. a render.fields / bullet block per documented column for description and the full allowed_values list — mirroring what get_dataset_health already does at orient.py:296-301. Alternatively give render.table an optional `limits: dict[str, int | None]` so named columns can opt out of MAX_CELL.

**Test to pin it**

"test_a_long_column_description_and_a_wide_allowed_values_set_are_never_truncated" in tests/unit/test_mcp_orient.py (unit layer, alongside test_a_long_health_summary_is_never_truncated): stub the columns route with a >80-char description and a 30-element allowed_values list and assert both appear whole in the output.

## [medium] GET /datasets types validation_status and documentation as bare str, so a mistyped facet returns an empty 200 that the MCP tool reports as a tenancy problem. · **SILENT WRONG ANSWER**

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and nothing on the path rejects a bad facet value.

1. Route accepts any string. app/features/data_accelerator/api.py:188-193:
   `validation_status: str | None = Query(None, description="Filter by validation status: passed | failed | none")` and `documentation: str | None = Query(None, description="Filter by documentation completeness: full | partial | none")`. Both are bare `str | None` — the allowed vocabulary lives only in the description text. By contrast `has_schema_drift` on the very next line IS typed (`bool | None`) and would 422 on garbage, which shows the inconsistency is not a global stylistic choice. Elsewhere in the same feature the repo does use enums: schemas.py:13 `Classification = Literal[...]`, :921 and :1019 `sort_order: Literal["asc","desc"]`.

2. Repo applies it as raw SQL equality with no membership check. repo.py:232-240:
   `if validation_status: clauses.append("sig.validation_status = :vstatus"); params["vstatus"] = validation_status` and the identical shape for `documentation`. `signals_lateral()` (app/features/discovery/repo.py:50-89) only ever emits 'passed' | 'failed' | 'none' and 'full' | 'partial' | 'none', so `validation_status="pass"` matches zero rows in both the COUNT and the row query. No error, HTTP 200, `items: []`, `total: 0`.

3. The tool has one empty-result message and it blames tenancy. app/features/mcp/tools/orient.py:96-97:
   `if not items: return "No datasets matched. Note that datasets owned by teams you are not a member of are invisible rather than forbidden."` — reached identically for a genuine no-match, a cross-team dataset, and a mistyped facet. The tool's own parameter descriptions (orient.py:76, :80) advertise the vocabulary but nothing enforces it, and `@guard` (tools/_common.py:82-92) only fires on `ProblemError`, so with a 200 there is literally no error to translate — the claim's "cannot be fixed in the tool layer alone" is right.

4. No test contradicts it. tests/test_catalog_facets.py only exercises valid values (:97 `validation_status="passed"`); there is no assertion of a 422 or of any behaviour for an unrecognised value anywhere under tests/. tests/unit/test_mcp_harness_smoke.py:110-111 asserts the misleading string is returned, i.e. it pins the current wording rather than refuting the complaint.

5. It is not documented as deliberate — the documented principle is the opposite. app/features/mcp/tools/_common.py:4-11: "Request *validation* deliberately does not live here any more … the service used to coerce a malformed filter into an empty group and an unrecognised sort order into its default — both of which return confidently wrong data. The service now rejects those inputs, so the mirrors were deleted rather than left to drift." Same file :50-53 records that `GET /samples/{filename}/data` was migrated from bare `str` to `Literal["asc","desc"]` for exactly this reason. HANDOFF.md:318-327 (§18 Catalog facets) describes the filters and the fixed three-value vocabularies but never claims unknown values should silently return empty. So these two query params are a straggler from a conversion the repo already decided on, not a defended design.

**Fix**

Type the two query params with the vocabulary that signals_lateral() already fixes, so FastAPI 422s on anything else and `_common.explain`'s existing 422 branch renders the allowed values to the model:

  validation_status: Literal["passed", "failed", "none"] | None = Query(None, ...)
  documentation: Literal["full", "partial", "none"] | None = Query(None, ...)

in app/features/data_accelerator/api.py:188 and :192. Ideally hoist the two vocabularies into named aliases next to `Classification` in data_accelerator/schemas.py:13 and have discovery/repo.py's facet buckets reference the same aliases so catalog and filter can't drift. No change needed in repo.py or orient.py — `@guard` already turns the 422 into actionable text (tools/_common.py:195-207). Optionally also split orient.py:97 so the tenancy note is not the only explanation offered.

**Test to pin it**

"test_unknown_facet_value_is_rejected_rather_than_returning_an_empty_page" in tests/test_catalog_facets.py (integration layer — it must drive the real route to exercise FastAPI query validation): assert GET /datasets?validation_status=pass returns 422, and likewise ?documentation=complete, while the valid values still return 200. A companion unit test "test_search_datasets_surfaces_the_service_rejection_for_a_mistyped_facet" in tests/unit/test_mcp_orient.py can can a 422 problem from the harness and assert the ToolError names the allowed values instead of the team-membership note.

## [medium] run_sql clamps the joined output tail-first, so the artifact handle and row counts are dropped exactly when the truncation hint says to read the artifact.

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate on every point I could check.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/look.py:190-204 — `run_sql` builds the response as `clamp(render.join(fields, render.table(shown, ...), " ".join(notes)), MAX_RESPONSE_CHARS, hint="Project fewer columns, or lower max_rows, then read the artifact.")`. The notes string is the LAST argument to `join`.

/home/saketh/.../app/features/mcp/render.py:64-65 — `def join(*parts): return "\n\n".join(part for part in parts if part and part.strip())` — plain concatenation in argument order, so notes land at the tail.

/home/saketh/.../app/features/mcp/tools/_common.py:71-79 — `clamp` is head-keeping: `kept = text[:budget]` then appends `[response truncated at {budget:,} characters. {hint}]`. Anything past 60,000 chars is discarded, i.e. the tail, i.e. exactly the notes.

The notes that get dropped are constructed at look.py:178-188: the 10,000-row cap warning, `f"Showing {len(shown)} of {len(rows)} returned rows."`, and `f"Artifact: {payload['result_file']} (read with read_artifact)."` — the last of which carries a comment at look.py:185-186 stating "Always surface the handle: read_artifact needs it, and it is the only way to chain a result that is not fully shown." The code does not honour its own comment when the table overflows, so this is intent-contradicted, not by design.

Reachability is real: `max_rows` allows up to 500 (look.py:158-161) and `render.table` only caps each cell at 80 chars (render.py:12, 15-27) with no cap on column count or total size, so 500 rows x ~12 wide columns exceeds 60,000 chars easily.

No test contradicts the claim; the only relevant test agrees with it. /home/saketh/.../tests/unit/test_mcp_look.py:592-611 asserts only that the truncation marker, the "lower max_rows" hint text, and the length bound hold; its docstring says "Known gap, reported rather than pinned: the notes are appended after the table, so the artifact handle is the first thing the truncation removes, exactly when the hint tells the model to go read it." Nothing in HANDOFF/ARCHITECTURE defends the ordering.

Not a silent wrong answer: the truncation is explicitly announced, so the caller knows the table is cut — it just cannot follow the remedy it is handed, and it also loses the "Showing N of M" count and the 10,000-row cap warning.

**Fix**

Clamp only the volume-bearing part and keep the notes outside the budget. In look.py:190-204, hoist the note string into a variable and do:

    note_text = " ".join(n for n in notes if n)
    body = clamp(render.join(fields, render.table(shown, payload.get("columns"))), MAX_RESPONSE_CHARS, hint="Project fewer columns, or lower max_rows, then read the artifact.")
    return render.join(body, note_text)

(Alternatively pass note_text as the second argument to join, before the table — but appending after the clamp is better, since the fields block is also useful and the notes are short and bounded.) The existing test's `len(out) < MAX_RESPONSE_CHARS + 500` bound still holds, since the notes are a few hundred chars at most.

**Test to pin it**

"test_the_artifact_handle_survives_the_response_ceiling" in tests/unit/test_mcp_look.py (unit layer, alongside test_an_enormous_result_is_cut_at_the_response_ceiling_with_a_way_forward): same 500-row x 12-wide fixture with a result_file set, assert both "[response truncated at 60,000 characters." and "Artifact: " plus the result_file name appear in the output, and that "Showing 500 of" survives.

## [medium] MCP pivot renders grand totals as bare `alias: value` lines with no section heading, so a whole-dataset total reads as covering only the rows shown.

- **domain:** ?
- **where:** 

**Evidence**

The claim is accurate line for line; I tried to refute it three ways and could not.

1. The code really is asymmetric. app/features/mcp/tools/compute.py:195-200 (aggregate):
   `totals_block = render.section("Totals (over all groups, not just the rows shown)", render.fields(list(totals.items())))`
   preceded by an explicit comment at 188-192 explaining WHY the label exists: "The old label here said 'summed across the groups shown', which was wrong on both counts."
   compute.py:289 (pivot) renders the same field with no section at all:
   `render.fields(list((payload.get("totals") or {}).items())),`
   render.section is what emits `## <title>` (app/features/mcp/render.py:58-59); render.fields emits bare `key: value` lines (render.py:30-36) — the exact same helper used at compute.py:281-287 for `rows_scanned` / `rows` / `pivot_columns`, and render.join separates parts with a blank line (render.py:62-63). So the rendered output is precisely the block quoted in the claim: an unqualified `total: 999` that is typographically identical to the metadata header block.

2. The semantics really are the same as aggregate's, so the missing label is materially misleading, not cosmetic. app/features/data_accelerator/services/pivot.py:252-260 computes `totals` via `_aggregate_into(conn, "_pivot_grand", request, [], available, source)` — grouping list `[]`, i.e. re-aggregated over the FULL filtered source, unconditionally, independent of `limit`/truncation (truncation is applied earlier at pivot.py:241-250). schemas.py:1032-1033 documents it as "Grand totals per value alias". aggregation.py:263 says the identical thing for aggregate and even points at pivot: "the shape pivot uses for its grand totals (`_aggregate_into(..., [])`)". So on a truncated pivot the unlabelled number genuinely does not describe the rows shown.

3. Nothing documents this as deliberate, and no test contradicts the claim. The only heading string in the repo is aggregate's (grep for "Totals (over all groups" hits only compute.py:198 and tests/unit/test_mcp_compute.py:554). The pivot totals test, tests/unit/test_mcp_compute.py:794-807 `test_pivot_grand_totals_are_reported_when_the_service_computed_them`, asserts only `"total: 999" in out` — its docstring argues totals must be present ("Dropping it forces a model to add the visible cells, which is wrong the moment the table is capped"), i.e. it agrees with the claim's rationale and simply never checked the labelling. HANDOFF.md:334-336 documents pivot totals as re-aggregated at the coarser grain but says nothing about the MCP rendering.

One caveat on the claim's flourish: "mistake for a data row" is weak — render.table always emits a pipe-delimited header row (render.py:39-51), so a `total: 999` line is not shaped like a data row. The core defect (unlabelled, wrongly-attributable scope) stands.

Adjacent gap noticed while reading, outside the claim: pivot never renders `payload["column_totals"]` at all, so `include_column_totals=True` silently produces no visible per-column totals in the MCP output.

**Fix**

In app/features/mcp/tools/compute.py, mirror aggregate: build `totals_block = render.section("Totals (over all groups, not just the rows shown)", render.fields(list(totals.items())))` when `payload.get("totals")` is non-empty, and pass `totals_block` to render.join at line 289 instead of the bare render.fields call. (Optionally also render `column_totals` under its own heading.)

**Test to pin it**

"test_pivot_grand_totals_are_labelled_as_covering_every_filtered_row_not_the_rows_shown" in tests/unit/test_mcp_compute.py (unit layer), asserting the `## Totals (over all groups, not just the rows shown)` heading precedes `total: 999`.

## [medium] MCP tools interpolate raw sheet/column names into REST paths unencoded, so a name containing '#' truncates the URL and the call 404s.

- **domain:** ?
- **where:** 

**Evidence**

The mechanism is real. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/_common.py:233-238 builds `f"{base}/sheets/{sheet}/{suffix}"` with no quoting; same pattern at compute.py:64 (`f"columns/{column}"`), compute.py:405, orient.py:218, curate.py:394, artifacts.py:117. client.py:_request passes that string straight to httpx (`self._http.request(method, path, ...)`), and httpx does not percent-encode path-structural characters. Verified against the repo's own httpx 0.28.1 (venv/bin/python, URL build only — no tests run):
  '/datasets/d/versions/1/sheets/Sheet #1/query' -> raw_path b'/api/v1/datasets/d/versions/1/sheets/Sheet%20'   (everything from '#' onward, including the '/query' segment, is dropped as a fragment)
  '/datasets/d/versions/1/sheets/Q1/Q2/query'    -> raw_path unchanged (the '/' becomes a path separator)
The name really is a supported input: look.py:86 documents `sheet` as "Sheet name or key", orient.py:142 (describe_dataset) prints `("sheet", sheet.get("name"))` — the raw workbook name — and app/shared/datasets.py:150-158 `_find_sheet` matches "by exact sheet name first, then by normalized sheet_key". A sheet literally named "Sheet #1" is legal in Excel, so query_rows/profile_column/check_quality on it hit an unroutable URL and surface the generic 404 text from _common.py:188-193.

But two-thirds of the claim's specifics are wrong:
- "a column named 'rate %'" — false. httpx encodes a stray '%' to %25 and a space to %20; '/d/columns/rate %' -> b'/d/columns/rate%20%'. '&' and spaces are likewise fine. Only '#' (fragment) and '/' (separator), and '?' (query), actually break.
- "a sheet literally named 'Q1/Q2'" — not reachable through normal ingest. Excel forbids / \ ? * [ ] : in sheet titles, and sheet names in this service come only from the workbook (app/shared/data_io.py:422-447) or the synthetic constant "data" (HANDOFF.md:548). It takes a hand-forged xlsx.
- artifacts.py:117 is a non-issue: the filenames callers pass are service-generated `f"agg_{uuid.uuid4().hex}.parquet"` / `sample_…` / `pivot_…` (aggregation.py:420, pivot.py:281, sampling.py:504,564).
Also no silent wrong data: the truncated path either matches no route (404) or resolves a column that then fails resolve_schema_column; the metadata write path re-normalizes the segment server-side anyway (discovery/api.py:277, 292-297), so nothing is written under a mangled name. Nothing in HANDOFF.md/ARCHITECTURE.md documents this as deliberate, and no test in tests/ exercises a special-character sheet name.

**Fix**

Percent-encode every non-numeric, non-UUID segment at the point of interpolation: in `sheet_path` use `urllib.parse.quote(sheet, safe="")`, and do the same for `column` (compute.py:64), `sheet_key`/`column_name` (orient.py:218, curate.py:394) and `filename` (artifacts.py:117). A one-line helper (`def seg(v: str) -> str: return quote(str(v), safe="")`) in _common.py used at all five sites keeps it from drifting.

**Test to pin it**

"test_query_rows_reaches_a_sheet_whose_name_contains_a_hash" — tests/unit/ (the MCP tool tests already stub the client via tests/unit/mcp_harness.py, so assert on the path the tool asks for, e.g. that the recorded request path is .../sheets/Sheet%20%231/query and not a truncated one).

## [medium] PATCH /datasets/{id}/rules/{rule_id} re-validates nothing, so it can replace a foreign_key/accepted_values rule's parameters with {} — a shape create rejects.

- **domain:** ?
- **where:** 

**Evidence**

Every cited line checks out, and the whole path (tool -> route -> pydantic -> repo) really is unguarded.

1) Create-time invariants exist only on RuleCreate. app/features/quality/schemas.py:46-58 `_check_selectors` raises for `foreign_key requires parameters.ref_sheet and parameters.ref_column` (:53-55) and `accepted_values requires parameters.values (non-empty list)` (:56-57). RuleUpdate (schemas.py:61-70) is a flat all-optional model with `parameters: dict[str, Any] | None = None` and no `model_validator` at all — I read the class in full; there is nothing after line 70 but `RuleOut`.

2) The route adds nothing. app/features/quality/api.py:84-97 `update_rule` does permission check -> `_resolve_sheet_selector(...)` -> `repo.update_rule`. No rule_type lookup, no shape check.

3) The repo blindly overwrites. app/features/quality/repo.py:74-92: `_MUTABLE` includes `parameters`, and `sets.append("parameters = CAST(:parameters AS jsonb)"); params["parameters"] = json.dumps(v or {})` — a full replace, not a merge, exactly as the tool's description claims (curate.py:485-487 "On update this REPLACES the whole parameters object").

4) The MCP tool's update branch really only checks non-emptiness. curate.py:560-575 builds the body then `body = {k: v for k, v in body.items() if v is not None}` — `parameters={}` is not None, so it survives the filter, the body is non-empty, the `ToolError` at :571-575 does not fire, and the PATCH goes out. None of the create-branch guards (:526-543) are reachable from `action='update'`.

5) The stated consequence is real. engine.py:_evaluate foreign_key path does `ref_sheet = _find_sheet(sheets, params.get("ref_sheet"))` -> `_result(rule, "error", message="Referenced sheet 'None' not found")` (engine.py:153-156); accepted_values with `values or []` builds `col NOT IN ()` (engine.py:189-193), which throws and is caught by the blanket `except Exception` at engine.py:108-110 into status='error'. repo.complete_run counts status 'error' at severity 'error' into `error_failures` (repo.py:146-149), which gates tag promotion (data_accelerator/api.py:442-448) — so the breakage does surface later, exactly as curate.py:690-695 describes.

Nothing contradicts it: no test in tests/ patches a rule's parameters (tests/test_quality.py:122 only patches `{"enabled": False}`), and no docstring, comment, HANDOFF.md or ARCHITECTURE.md entry claims update-time laxity is intentional — the only relevant HANDOFF line (411-412) says the opposite, that "rule create/update pins selectors to live logical sheets".

The one point I'd downgrade from the claim's framing: this is not a silent wrong answer. The next validation run reports status='error' with a message and blocks promotion, so the damage is loud — just deferred, and attributed to the rule rather than to the update that broke it.

**Fix**

Factor the RuleCreate invariants into a module-level `def check_rule_shape(rule_type, sheet_selector, column_selector, parameters) -> None` in app/features/quality/schemas.py and call it from both `RuleCreate._check_selectors` and `api.update_rule`. In `update_rule`, fetch the existing row first (`repo.get_rule(dataset_id, rule_id)` already exists and is used at api.py:77-ish/repo.py), 404 if absent, merge `body.model_dump(exclude_unset=True)` over it, and 400 via HTTPException if the merged shape fails the check. That fixes the MCP tool for free without it needing an extra GET to learn rule_type — the server knows it.

**Test to pin it**

"test_patching_a_foreign_key_rules_parameters_to_empty_is_rejected_with_400" — belongs in tests/test_quality.py (the API/integration layer), since the guard has to live server-side where rule_type is known; a companion unit test "test_check_rule_shape_rejects_accepted_values_with_no_values" fits tests/unit/.

## [low] Statuses 460 (tus checksum mismatch) and 507 (insufficient storage) are missing from _STATUS_TITLES, so their problem bodies render title "Error"/code "error".

- **domain:** ?
- **where:** 

**Evidence**

The factual core of the claim holds, but its stated impact is overstated.

- /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/api/errors.py:36-51 — `_STATUS_TITLES` is commented "Human titles + machine codes for the statuses this service actually returns." and lists 400,401,403,404,405,409,413,415,422,423,429,500,502,503. No 460, no 507.
- errors.py:54-55 `_code_for` → `_STATUS_TITLES.get(status, "Error").lower().replace(" ", "_")` = "error"; errors.py:100 `"title": title or _STATUS_TITLES.get(status, "Error")` = "Error".
- /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/api.py:532-536 raises a bare `HTTPException(460, "Checksum mismatch — corrupted data, PATCH rejected. Retry from same offset.")` — no `code=`, so `_http_exception_handler` (errors.py:121 `code=getattr(exc, "code", None)`) passes None and the fallback applies.
- /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/services/tus.py:88-94 raises a bare `HTTPException(507, f"Insufficient disk space. Free: ...")` — same fallback.
- These are the ONLY two statuses raised anywhere in app/ that are absent from the map: `grep -oE "(HTTPException|ProblemException)\(\s*[0-9]{3}"` over app/ yields 404, 400, 409, 403, 500, 401, 423, 415, 413 (all mapped) plus the single 460 and single 507. So this is an oversight against the map's own stated scope, not a deliberate catch-all.
- No test contradicts or asserts this: tests/test_uploads_tus.py:100 asserts only `r.status_code == 460  # TUS checksum-mismatch`; nothing asserts the `code`/`title` for either status. No HANDOFF.md/ARCHITECTURE.md note documents an intentional omission.

Where the claim overreaches: the problem body still carries `"status": 460` / `"status": 507` (errors.py:102) alongside the HTTP status line, and `detail` is the specific human message ("Checksum mismatch — ... Retry from same offset."), not a generic string. The tus checksum extension is defined in terms of the 460 *status*, which is exactly what a tus client branches on and what the repo's own test uses. So "a tus client cannot distinguish 'retry this chunk' from a generic failure" is false — it can, by status; what it cannot do is branch on `code`, which is a consistency defect in the envelope rather than a loss of information.

**Fix**

Add two entries to `_STATUS_TITLES` in app/api/errors.py:36-51 — `460: "Checksum Mismatch"` and `507: "Insufficient Storage"` — yielding codes `checksum_mismatch` and `insufficient_storage`. (Alternative, equally correct: pass explicit `code=` at the two raise sites via `ProblemException`, e.g. `code="checksum-mismatch"` in app/features/files/api.py:533 and `code="insufficient-storage"` in app/features/files/services/tus.py:91; note the rest of the codebase's custom codes use hyphens, e.g. "sheet-selection-required", while map-derived codes use underscores — pick whichever matches the intended contract.)

**Test to pin it**

"test_tus_checksum_mismatch_returns_specific_problem_code" (and a sibling for 507) asserting `r.json()["code"] != "error"` and the specific title — belongs in tests/test_uploads_tus.py alongside the existing 460 assertion at line 100; a pure mapping assertion over `_STATUS_TITLES` covering every status literal raised in app/ belongs in tests/unit/.

## [low] run_sql's sql-error hint lists the dataset's CURRENT-version sheet keys but labels them "in this version" even when an older version was pinned.

- **domain:** ?
- **where:** 

**Evidence**

The claim is factually accurate on every link of the path.

1. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/look.py:27 — `_table_hint` calls `ctx.client.get(f"/datasets/{dataset_id}/sheets")` with no version in the path and no query param, and look.py:34 renders `f"Queryable table names in this version: {', '.join(names)}. "`.

2. app/features/data_accelerator/api.py:570-575 — the only sheet-listing route is `@router.get("/datasets/{dataset_id}/sheets")` → `get_dataset_sheets(dataset_id)`; it takes no version argument. There is no versioned sheets-list route (grep for `versions/{version_number}/...` finds `confirm-rename`, `/sql`, `/sheets/{sheet_name}/missing`, `/sheets/{sheet}/query`, but no version-scoped sheet list).

3. app/features/data_accelerator/services/datasets.py:100 — `get_dataset_sheets` starts with `ver = await _get_current_version_or_404(dataset_id)`, i.e. always the CURRENT version, and returns that version's sheet rows.

4. look.py:163-172 — `resolved = await resolve_version(ctx, dataset_id, version)`; `_common.py:215-226` `resolve_version` returns the caller's `version` verbatim when supplied ("Pass `version` explicitly to pin an older one"). The SQL then goes to `/datasets/{id}/versions/{resolved}/sql`, and app/features/explorer/api.py:317-340 `sql_query` resolves exactly that version and runs against its sheets. So the query ran on `resolved`, while the hint lists sheets of `current_version`.

5. No test contradicts it. tests/unit/test_mcp_look.py:619-670 pin the hint text ("Queryable table names in this version: orders_2026, returns.") but the fake client stubs a single unversioned `/datasets/ds-1/sheets`, so no test exercises pinned-old-version vs current-version divergence.

6. Not documented as deliberate: no mention of the hint in any .md. The opposite — the sibling tool is careful about exactly this distinction: app/features/mcp/tools/orient.py:127 fetches the same unversioned endpoint and labels the block `render.section("Sheets (current version)", ...)` (orient.py:165), and describe_dataset's tool description says "Full schema of a dataset's current version". look.py's "in this version" is the outlier wording.

Scope of the harm is narrow: it only fires inside a `ToolError` on the `sql-error` branch (look.py:171), so the user never gets a wrong data answer — the model gets a wrong follow-up hint and can burn another failed query.

**Fix**

There is no version-scoped sheet-list endpoint to call, so reword rather than re-route: change look.py:33-36 to "Queryable table names in the dataset's current version: ..." and, when `resolved` differs from the current version, say so — e.g. pass `resolved` into `_table_hint` and append "you queried version {resolved}; these names come from the current version and may differ." (Routing properly would need a new `GET /datasets/{id}/versions/{n}/sheets`.)

**Test to pin it**

"test_the_table_hint_does_not_claim_current_version_sheet_names_belong_to_a_pinned_older_version" in tests/unit/test_mcp_look.py (unit layer — the fake client can serve /datasets/ds-1/sheets with names that differ from what version 2 contained, and assert the hint text does not assert them as that version's).

## [low] clamp()'s .rstrip() binds to the fully concatenated f-string, which always ends in ']', so the stray space before ']' is never removed.

- **domain:** ?
- **where:** 

**Evidence**

app/features/mcp/tools/_common.py:76-79 reads:

```
    return (
        f"{kept}\n\n[response truncated at {budget:,} characters. "
        f"{hint}]".rstrip()
    )
```

Adjacent string literals are concatenated at parse time into a single expression, so `.rstrip()` applies to the whole joined string, which always ends in `]` — there is nothing to strip. Verified with the interpreter: both `(f"... characters. " f"{hint}]").rstrip()` and the source's exact form produce `'xxxxxxxxxx\n\n[response truncated at 10 characters. ]'` for `clamp("x"*20, 10)`. The `.rstrip()` is therefore dead code, and the dangling space before `]` it was written to remove survives.

The hintless path is real in production, not just in tests: app/features/mcp/tools/look.py:190, app/features/mcp/tools/artifacts.py:141, app/features/mcp/tools/context.py:349/399/872, app/features/mcp/tools/curate.py:703/752, app/features/mcp/tools/pipeline.py:255/400/418/557/654 all call `clamp(...)` with no `hint=`, so those responses end with `characters. ]`.

No test contradicts the claim. tests/unit/test_mcp_common.py:699-732 asserts only `"[response truncated at 100 characters." in out` and, for the no-hint case, that no borrowed advice leaks (`"Lower" not in out`); the hint case at line 725 asserts `out.endswith("...offset`.]")`, which is unaffected. Other suites (test_mcp_look.py:609, test_mcp_artifacts.py:581, etc.) use the same substring form. Nothing pins the trailing space either way.

No docstring, comment, or handoff note defends the space — the docstring at line 72 is just "Hard ceiling on a single tool response.", and the `.rstrip()` itself is evidence of intent to remove it. So this is an ineffective fix, not a deliberate choice.

**Fix**

Build the marker so the strip lands on the hint slot, e.g. `marker = f"[response truncated at {budget:,} characters. {hint}".rstrip() + "]"` then `return f"{kept}\n\n{marker}"`. Equivalent: `" ".join(filter(None, (f"response truncated at {budget:,} characters.", hint)))` wrapped in brackets.

**Test to pin it**

"test_a_truncation_marker_with_no_hint_has_no_dangling_space_before_the_bracket" in tests/unit/test_mcp_common.py (unit layer), asserting `clamp("z"*500, 100).endswith("characters.]")`.

## [low] run_sql's table-name hint lists the dataset's CURRENT version's sheet keys while claiming they are the pinned version's tables.

- **domain:** ?
- **where:** 

**Evidence**

The code path is exactly as claimed.

app/features/mcp/tools/look.py:24-36 — `_table_hint(ctx, dataset_id)` takes no version and calls `ctx.client.get(f"/datasets/{dataset_id}/sheets")`, then emits "Queryable table names in this version: ...". It is invoked at look.py:172 inside run_sql's `sql-error` branch, after run_sql executed against `resolved` (look.py:163-167, `POST /datasets/{id}/versions/{resolved}/sql`), and `resolved` is whatever the caller pinned (`_common.py:221-222: if version is not None: return version`).

The unversioned route really is current-version-only: app/features/data_accelerator/api.py:570-576 `list_sheets` -> `get_dataset_sheets` (app/features/data_accelerator/services/datasets.py:93-104) whose first line is `ver = await _get_current_version_or_404(dataset_id)`; that resolves via app/shared/datasets.py:203-208 -> app/shared/repo.py:70-83, which joins `datasets.current_version_id`. So the sheet rows come from the dataset's current version, never from `resolved`.

The divergence is materially possible, not theoretical: ARCHITECTURE.md:241 states "`dataset_version_sheets.sheet_key` is the *physical* name in a given version", i.e. keys can differ version to version, and there is no version-scoped sheets-listing endpoint at all (only `…/versions/{n}/sheets/{name}/…` in app/features/explorer/api.py) — so the hint literally cannot be built for a pinned version today.

Nothing contradicts it and nothing documents it as deliberate: the three hint tests in tests/unit/test_mcp_look.py:619-670 all stub `/datasets/ds-1/sheets` and never exercise a pinned-version mismatch (the first even lets the version default via `/datasets/ds-1/versions`). `describe_dataset` (app/features/mcp/tools/orient.py:115-127) uses the same unversioned call but its description explicitly says "current version", so that one is by design; `_table_hint`'s "in this version" wording is the part that is wrong.

Scope check that caps severity: this only fires on the `sql-error` branch, the real engine message is still returned first (look.py:172), and it is an error enrichment — no query result is silently wrong. The failure is a misleading correction that can send the model to a table name absent from the pinned version.

**Fix**

Pass the resolved version into `_table_hint(ctx, dataset_id, resolved)`. Since no version-scoped sheets listing exists, either (a) resolve the current/newest-ready version and, when it differs from `resolved`, reword to "Table names in the dataset's current version (you queried version {resolved}, whose sheets may differ): ...", or (b) suppress the hint entirely when `resolved` is not the current version and tell the model to call describe_dataset. Minimum viable change is the wording plus the version comparison; the unconditional "in this version" phrasing must go.

**Test to pin it**

"test_the_table_hint_does_not_claim_a_pinned_versions_tables_from_the_current_version" in tests/unit/test_mcp_look.py (tests/unit/) — stub `POST /datasets/ds-1/versions/2/sql` with a sql-error, stub `/datasets/ds-1/sheets` with sheet keys that only exist in the current version, call run_sql(version=2), and assert the message does not assert those names as "in this version".

## [low] The "No recorded activity at all" fallback in the MCP context tool is dead code: usage counts are always ints, so 0 renders instead.

- **domain:** ?
- **where:** 

**Evidence**

Every link in the chain checks out.

1. app/features/mcp/render.py:36 — `lines = [f"{k}: {scalar(v, limit=None)}" for k, v in pairs if v not in (None, "", [], {})]`. The membership test uses `==`, and `0 == None/""/[]/{}` is all False, so integer 0 is KEPT. (Note it would also keep `False`, and would drop `0.0`? no — 0.0 also compares unequal to all four. So any numeric zero survives.)

2. app/features/mcp/tools/context.py:487-495 — `render.fields([... ("downloads", usage.get("downloads")), ("writes", ...), ("total_events", ...), ("last_activity_at", _ts(...))]) or "No recorded activity at all — no reads, no writes."`. The `or` only fires when fields() returns "".

3. app/features/discovery/api.py:417-424 — the route is declared `response_model=UsageResponse` and returns `UsageResponse(dataset_id=dataset_id, **stats)`; app/features/discovery/api.py:143-145 types `downloads: int`, `writes: int`, `total_events: int` as required non-nullable, so the serialized JSON always contains all three keys.

4. app/features/discovery/repo.py:543-559 — `SELECT COUNT(*) FILTER (...) AS downloads, COUNT(*) FILTER (...) AS writes, COUNT(*) AS total_events, MAX(occurred_at)::text ...` with `.mappings().one()`. An ungrouped aggregate over zero matching audit rows still returns one row with 0/0/0 (only `last_activity_at` is NULL, and that one IS dropped by fields()).

5. app/features/mcp/client.py:55-71 — `AnalyticsClient.get` goes over an in-process ASGI transport to that very route, so the tool sees the pydantic-serialized body, not a raw repo dict. No path bypasses the response_model.

6. No test contradicts: the only reference is tests/unit/test_mcp_context.py:145, which stubs non-zero counts (`"downloads": 4, "writes": 11, "total_events": 40`). Nothing in HANDOFF/ARCHITECTURE or a comment defends the fallback as deliberately defensive.

So on a never-touched dataset the Usage section renders "downloads: 0 / writes: 0 / total_events: 0" and the authored prose can never appear. The numbers are correct, so nothing is silently wrong — it is a dead branch and a missed affordance for the agent reading the section.

**Fix**

Make the emptiness test explicit at the call site instead of relying on fields() dropping zeros, e.g. in context.py compute `has_activity = bool(usage.get("total_events"))` and use `render.fields([...]) if has_activity else "No recorded activity at all — no reads, no writes."`. Do not change render.fields to drop 0 — zeros are meaningful in other sections (row counts, error counts).

**Test to pin it**

"test_context_usage_section_says_no_recorded_activity_when_all_counts_are_zero" in tests/unit/test_mcp_context.py (unit layer — the existing stubbed-client harness at tests/unit/test_mcp_context.py:145 already covers this tool with a fake usage payload).


# BY_DESIGN

## [medium] /joins/execute authorizes with DATASET_READ while persisting a definition, job, run and artifact — inconsistent with siblings, but explicitly specified that way.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts in the claim check out. app/features/relationships/api.py:224-225 calls `_authorized_relationship(spec.relationship_id, principal, Permission.DATASET_READ)`, and `_authorized_relationship` (api.py:187-202) uses that permission for the OWNING dataset and hard-codes `Permission.DATASET_READ` for the far side. `joins.execute_join` (app/features/relationships/joins.py:256-336) really does persist: `_join_definition(...)` -> `library_repo.create_definition` (joins.py:240), `jobs.create_job` (265), `library_repo.create_run` (270), `_persist_table` (278) and `library_repo.create_artifact` (310). Siblings do require write: app/features/library/api.py:116 `run_definition` -> DATASET_WRITE; app/features/transform/api.py:132 `run_transformation` -> DATASET_WRITE, while the non-persisting `preview_transformation` (transform/api.py:114) uses READ. library/api.py:265-267 states the convention in a docstring: "Read-only: no run row and no artifact, unlike executing the definition."

But this is a documented, deliberate choice, not an oversight. WAVE4-6-PLAN.md (§23 section, lines ~211-217) specifies the endpoint exactly as built: "`POST /joins/execute` (persist `join_output` artifact ...)" and then "RBAC: READ both sides for preview/execute; WRITE target for publish." ARCHITECTURE.md:436-440 and HANDOFF.md:242-250 give the rationale for the safety model: the gate that makes cross-dataset joining exposable is the `confirmed` relationship plus independent permission checks on both dataset ids; the actual mutation of a dataset (publishing a new dataset/version + lineage) is what carries DATASET_WRITE. HANDOFF.md:248-250 also notes "ONE reused definition per (relationship, how) so repeated joins don't litter the library", which bounds the definition-littering half of the claimed impact (joins.py:228-239 dedupes by name).

Existing tests confirm the intended RBAC shape rather than contradicting it: tests/test_join_builder.py:262-265 asserts a cross-team caller gets 404 on execute; :279-284 asserts a viewer with only the left side gets 404 on preview; :288-298 asserts a viewer gets 403 on publish. The claim is correct that no test pins viewer-vs-editor on /joins/execute specifically.

So: the described behaviour is real, but it matches the written specification and the publish/execute split it defines. The claim's framing ("every sibling requires WRITE, therefore this is wrong") ignores that the plan doc calls out this endpoint's RBAC explicitly.

## [medium] Version/tag/sheet lists return the full collection in a Page envelope with limit=len(items) — deliberate, and `total` is accurate, not a fake count.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics are as described, but the harmful part of the claim ("a UI that trusts `total` ... is reading len(items)") is not a defect, because these endpoints return every row — so len(items) IS the true total.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/api.py:76-78:
  def _collection(items: list) -> Page:
      """Wrap a small, fully-materialised sub-collection in the Page envelope."""
      return Page(items=items, total=len(items), limit=len(items), offset=0)
The docstring is an explicit statement of intent: these are sub-collections deliberately materialised whole, not a forgotten `pagination` dependency. The same module imports `PageParams, pagination` (api.py:60) and uses them on the top-level list endpoints, so the omission on these three is a choice, not an oversight.

Call paths confirm no hidden truncation:
- api.py:254-255 list_dataset_versions -> repo.py:430-452 `list_versions`: `... FROM dataset_versions dv WHERE dv.dataset_id = :did ORDER BY dv.version_number DESC` — no LIMIT, so all rows come back and total is exact.
- api.py:358-359 list_tags -> repo `list_tags_for_dataset` (tags per dataset are a handful; whole-version tags are an invariant of the model).
- api.py:574-575 list_sheets -> `get_dataset_sheets` (bounded by sheets in one workbook).
- repo.py:313-331 (search_datasets version fetch): `WHERE dv.dataset_id = ANY(:dids) ORDER BY dv.dataset_id, dv.version_number DESC` — accurate: no LIMIT. But the dataset query immediately above (repo.py:293-306) IS bounded (`LIMIT :limit OFFSET :offset`, limit<=200 via `pagination`, app/api/pagination.py:39-44), so the fan-out is bounded-datasets x versions, not "every version of every dataset".

An existing test asserts the current contract rather than contradicting it: tests/test_transformations.py:347 `assert versions["total"] == 1`.

So: no wrong number, no crash, no missing data — the response is complete and `total` is correct. What is real is (a) an inconsistent envelope (limit echoes len(items), and is 0 for an empty list, which will divide-by-zero a naive page-count computation) and (b) unbounded response growth for a dataset with a long version history, since versions are immutable and accumulate. That is a scalability/contract wart on a deliberately documented design, not a correctness bug.

## [medium] Pivot sort_by is deliberately restricted to row dimensions; measure-column sorting returns an explicit, documented, test-pinned 400.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical fact in the claim is accurate, but the restriction is a documented, deliberate contract, not a defect.

1. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/services/pivot.py:232-236 is exactly as cited:
   `sort_name = request.sort_by or None` / `if sort_name is not None and sort_name not in row_names: raise HTTPException(400, f"sort_by must be a row dimension, got: {sort_name}. Row dimensions: {row_names}")`
   and the widened cell columns are indeed built at :200-214 (`MAX(CASE WHEN {qcol} IS NOT DISTINCT FROM ? ...) AS {cell}`) and the row-total columns at :229 (`cell_names += [f"total_{a}" for a in aliases]`).

2. It is declared in the request schema, not just enforced in the handler: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/schemas.py:918-919 —
   `sort_by: str | None = Field(default=None, description="Row-dimension output name to sort by (default: all row dims asc)")`.
   The API contract the UI reads never advertises measure sorting.

3. An existing test pins the 400 as intended behaviour, in the "invalid pivot requests" block alongside the other deliberate guards (unknown agg fn, pivot dim == row dim, pct display without a columns dim): /home/saketh/Projects/playground/work/demo/apps/analytics-service/tests/test_pivot.py:166-170 asserts `sort_by: "amount_sum"` -> 400 with "row dimension".

4. Behaviour is fail-loud, not silently wrong: the request is rejected with a 400 that enumerates the valid row dimensions. Nothing returns a wrongly-ordered or wrongly-truncated result. Contrast /aggregate, which explicitly *does* allow sorting by an agg alias (aggregation.py:214-231, `valid_sort = {group names} | set(agg_aliases)`), and whose comment records that the silent-drop of an unknown sort_by was the bug that got fixed. So the codebase knows how to allow measure sorting and chose a narrower contract for pivot.

5. The claim's impact framing is overstated: a UI cannot "fetch the whole result and sort client-side" as a workaround silently degrading — it gets a 400. And the row cap is `min(request.limit or MAX_AGGREGATION_ROWS, MAX_AGGREGATION_ROWS)` with MAX_AGGREGATION_ROWS = 100_000 (constants.py:27), so the truncation interaction is far less acute than described. For the no-pivot-dim case the client can use /aggregate with sort_by=alias instead.

This is a real product-capability gap (ranking rows by a measure in a widened pivot is not expressible server-side), but it is deliberate, documented in the schema, explicitly error-messaged, and locked by a test — BY_DESIGN, a feature request rather than a bug.

## [medium] Preview does sample the source before the pipeline and hardcodes approximate=true, but sample-before-run is the documented, advertised contract at every layer.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts of the claim check out, but the framing as a defect does not.

1) Sampling really is on the src CTE, before every step — app/features/transform/compile.py:359-363:
   `src = f"SELECT * FROM {source}"` / `if sample_rows is not None: src += f" USING SAMPLE {int(sample_rows)} ROWS"` / `ctes = [("src", src)]`, then each step chains off `relation = "src"`. So yes, sort/limit/filter/deduplicate see the sample.

2) This is explicitly the documented purpose, not an oversight. compile.py:345-350 docstring: "*sample_rows*, when set, makes the head of the chain a ``USING SAMPLE`` — the preview path, **which never touches the full file**." The bounded scan is the point: preview is an in-request, unbounded-file-safe dry run.

3) It is disclosed at every layer the UI touches:
   - schemas.py:13-15 (the query-param description the OpenAPI doc shows): "Rows to sample from the source before applying the pipeline — the preview is approximate by construction and never scans the whole sheet".
   - schemas.py:63 TransformPreview docstring: "A sampled dry run — no job, no artifact, nothing persisted."
   - schemas.py:69-70 field description on `approximate`: "Rows come from USING SAMPLE, not the whole sheet".
   - api.py:104-116 route docstring: "Dry-run the pipeline over a sample."
   - app/features/mcp/tools/pipeline.py:569-573 renders an explicit caller-facing warning: "These rows come from a SAMPLE of the source, so they are approximate — row counts and rare values here do not describe the whole sheet. Use action='run' for the real thing."
   - ROADMAP.md:183 §19: "Preview-on-sample before full run".

4) An existing test asserts exactly the claimed behaviour as correct, not as a bug: tests/unit/test_transform_steps.py:298 `test_preview_sampling_bounds_the_scan` asserts `run(conn, [], sample_rows=2)` returns 2 rows; tests/test_transformations.py:119 asserts `preview["approximate"] is True`.

5) The one residual sub-point that is factually true and undocumented — `approximate=True` is hardcoded in service.py:209 even when the sheet has fewer rows than `rows`, so a preview that is in fact exact is labelled approximate — is a conservative over-report. It cannot mislead a caller into trusting a sampled result; it only under-promises. Non-determinism across two previews (DuckDB reservoir sampling with no REPEATABLE seed) is real and is the honest cost of "never scans the whole sheet"; the API tells callers to use action='run' when they need the real result.

## [medium] No cross-definition run-list endpoint exists and timeline rows carry no run_id, but /jobs already gives a team-scoped "what is running" list.

- **domain:** ?
- **where:** 

**Evidence**

Route inventory (grep of every @router.get/post containing "run" across app/features/) confirms the only transformation-run reads are per-definition or per-id: app/features/transform/api.py:138 `GET /datasets/{dataset_id}/transformations/{definition_id}/runs` and api.py:153 `GET /datasets/{dataset_id}/transformations/runs/{run_id}`. There is no /datasets/{id}/transformation-runs and no team-level equivalent. The repo layer matches: app/features/transform/repo.py:193 `list_runs(definition_id, ...)` filters solely on `definition_id` — no dataset- or team-keyed variant exists. So part 1 of the claim is factually true.

Part 2 is also true: app/features/discovery/repo.py:603-615 emits the transformation_run branch as `jsonb_build_object('status', 'mode', 'transformation', 'version_number', 'sheet', 'row_count')` — no `t.id`, no `definition_id`. But this is the uniform, deliberate shape of §13, not a transform-specific omission: none of the sibling branches carry a row id either (version_created repo.py:567-571, tag_* :574-579, validation_run :582-587, profile_run :593-596). HANDOFF.md:344-347 documents the timeline as "one UNION ALL ... merging versions, tag history, validation runs, profile runs, lineage ... Offset paging; no new tables", i.e. a merged display stream, and HANDOFF.md:743-745 explicitly instructs that "any new run/history table (e.g. Wave-4 transformation_runs) should add a branch there" — which is exactly what was done. (Minor doc gap only: the TimelineEvent event_type description at app/features/discovery/api.py:152-154 never lists `transformation_run`.)

The load-bearing impact claim — that "what is running right now" forces an N+1 fan-out over definitions — is refuted. app/features/transform/service.py:243-254 creates the run row and then ALWAYS calls `worker.dispatch("transform", ..., team_id=..., inline=sync)`, so every run (sync and async alike) leaves a jobs row. app/features/jobs/api.py:37-48 exposes `GET /jobs?status=&job_type=` — team-scoped for non-superusers (`team_ids = None if principal.is_superuser else principal.team_ids`), paginated, newest-first, returning `dataset_id`/`dataset_version_id`/`progress`/`status`; app/shared/jobs.py:119-155 backs it with a single ORDER BY created_at DESC query. Its module docstring (jobs/api.py:1-6) says this is "where a UI polls progress". So `GET /jobs?job_type=transform&status=running` is a one-call, cross-definition, cross-dataset running-work list. What genuinely does not exist is (a) a dataset_id filter on /jobs and (b) any run_id on either the job row (JobOut omits `parameters`, and the run_id lives only in `parameters`, service.py:247-249; the result summary at service.py:337-346 has no run_id) or the timeline row — so a UI can see that a transform is running but cannot deep-link from that row to `GET .../transformations/runs/{run_id}` without first walking the definition's /runs list.

## [medium] Platform endpoints (auth/teams/jobs/audit/webhooks) raise bare HTTPException, so every error carries only the coarse status-derived code from _code_for().

- **domain:** ?
- **where:** 

**Evidence**

The claim's facts check out, but the behaviour is the documented opt-in design, not a defect.

Facts confirmed:
- `app/api/errors.py:104` — `"code": code or _code_for(status)`; `_code_for` (errors.py:54-55) lowercases the status title, so 409 -> "conflict", 403 -> "forbidden".
- `grep -rn "ProblemException" app/` returns hits only under data_accelerator, files, library, discovery, explorer, transform, quality-ish paths and `app/shared/masking.py:176` (`code="sensitive-data-restricted"`). Zero hits in `app/features/auth/`, `app/features/webhooks/`, `app/features/jobs/`, `app/features/audit/`.
- Every raise in those four features is a bare `HTTPException`: `app/features/auth/api.py:44,56,84,86,88,129,142,146,158,162,177,180,190`; `app/features/auth/deps.py:69,73,101,109,137,143,144,156`; `app/features/webhooks/api.py:41,46,48,57,59,81,116,127`; `app/features/jobs/api.py:59`; `app/features/audit/api.py:43`.
- The two 409s the claim names are real and both render as `"conflict"`: `auth/api.py:190` "Cannot remove or demote the last owner of a team" (via `_guard_last_owner`) and `webhooks/api.py:81` "A webhook named '...' already exists". Likewise the two 403s: `deps.py:101` "You are not a member of the requested team" and `auth/api.py:44` "Cannot grant role '...' above your own" — and those two really can both come out of the *same* member-grant endpoint (`_ensure_grantable` is called from the members routes that also sit behind `get_principal`).
- No test contradicts it: `grep '"code"' tests/` shows custom-code assertions only for data-plane codes; the only platform assertion is `tests/test_api.py:25` `body["code"] == "unauthorized"` — i.e. a test that pins the status-derived fallback as the expected contract.

Why BY_DESIGN rather than a bug:
- `ProblemException`'s own docstring (errors.py:61) says "Raise **where a machine-readable error contract matters**" — custom codes are explicitly opt-in, and `_code_for` exists precisely to give everything else a valid code.
- The module docstring (errors.py:15-16) states the contract that is actually met: "Consumers can branch on the machine-readable `code` and always find a human-readable `detail`." Every platform response does carry a `code`; it is coarse, not absent.
- ARCHITECTURE.md:128-131 lists the fine-grained codes and they are all data-plane (`sheet-selection-required`, `unknown-column`, `relationship-not-confirmed`, `ambiguous-diff-key`, `select-only`); nothing promises per-cause codes on the platform surface.
- The claim also overstates the UI impact for its own examples: the last-owner 409 and the duplicate-webhook-name 409 are on entirely different routes (`DELETE/PUT /teams/{id}/members/...` vs `POST /webhooks`), so a client already knows which one it called. The one genuinely ambiguous case is 403-vs-403 on the member-grant route, which is an ergonomics gap, not a wrong answer.

No incorrect data, no 500 where a typed error belongs, no cross-domain inconsistency in envelope shape — just a coarser-than-ideal code on the platform routes.

## [low] POST /validate requires dataset:write, which is correct: the run creates job, run, object-store and artifact rows and emits a webhook.

- **domain:** ?
- **where:** 

**Evidence**

Claim's citations are literally accurate but its characterization is wrong.

1. `app/features/quality/api.py:123` does read `ds = await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_WRITE)`, and `permissions.py:49-51` does exclude DATASET_WRITE from `_VIEWER`. So a viewer gets 403 on POST /validate. That part is real.

2. The claim's premise — "the operation only produces a report" — is false. `validate_version` (api.py:118-183) mutates a lot of persistent state: `jobs.create_job(...)` + `jobs.start_job(...)` (api.py:132-136), `repo.create_run(...)` (api.py:137), the engine writes failing-row parquet files into the object store under `ArtifactLayout("validation_failures", ...)` (api.py:141-145), `_register_failure_artifacts` then creates library artifact ownership rows via `library_repo.create_artifact(...)` (api.py:242-248), `repo.complete_run` / `jobs.complete_job` (api.py:149-155), and finally a webhook `validation.failed`/`validation.passed` is emitted to team subscribers (api.py:169-176). That is a write by any definition, not a read.

3. It is deliberate and documented. The module docstring at api.py:3-6 states the rule up front: "Routes live under the dataset tree so RBAC follows the standard pattern: reads need dataset:read, mutations dataset:write... Validation runs synchronously ... with a jobs row as the execution record." The write check is the standard pattern, applied consistently with create/update/delete_rule (api.py:64, 90, 105), while the three genuine reads use DATASET_READ (api.py:78, 196, 211).

4. A test explicitly documents the intended contract: `tests/test_quality.py:137-151`, `async def test_quality_rbac(...)` with docstring "Viewers can read rules/results but not mutate or validate; outsiders 404." The one sub-claim that survives is narrow: that test exercises an *outsider* (asserts 404 at test_quality.py:150-151), and `tests/test_api.py:132-144` pins in-team viewer 403 only for PATCH/DELETE on the dataset, not for /validate. So no test asserts the in-team-viewer 403 on /validate specifically — a test-coverage gap, not a defect, and the documented intent is unambiguous.

**Test to pin it**

Optional coverage only (not a bug fix): "an in-team viewer gets 403 from POST /datasets/{id}/versions/{n}/validate while still reading rules and past runs" belongs in tests/test_quality.py alongside test_quality_rbac.

## [low] GET /datasets/{id}/rules returns the full list in a Page envelope with limit=len(items) — a repo-wide idiom for bounded child collections, not a defect.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is accurate: `app/features/quality/api.py:81` is `return Page(items=items, total=len(items), limit=len(items), offset=0)`, the handler takes no `pagination` dependency (`api.py:74-81`), and `repo.list_rules` (`app/features/quality/repo.py:49-57`) issues an unbounded `SELECT ... WHERE dataset_id = :did ORDER BY created_at` with no LIMIT. `list_validations` (`api.py:188-200`) does use `page: PageParams = Depends(pagination)` + `Page.of`, so the two siblings differ.

But this is not a quality-domain slip, it is the codebase's uniform idiom for small, dataset- or user-scoped child collections. The identical line appears in 8 places: `app/features/data_accelerator/api.py:78`, `app/features/discovery/api.py:252` and `:364`, `app/features/auth/api.py:120` (list_my_teams) and `:132` (list_members), `app/features/library/api.py:67` and `:205`, plus quality `:81`. `Page` (`app/api/pagination.py:20-30`) is a plain pydantic model with `limit: int` and no `ge` constraint, so `limit=0` is a legal value of the envelope; the `ge=1 le=200` bound lives only on the `pagination()` *query* dependency (`pagination.py:40-46`), which these routes deliberately do not use. `tests/test_quality.py:55-56` asserts exactly this contract (`listing["total"] == 7` with no limit/offset params) and passes.

The impact half of the claim is largely wrong. Because `limit == len(items) == total` and `offset == 0`, the invariant `offset + limit >= total` always holds, so a pager computing `pageCount = ceil(total/limit)` gets 1 for the 300-rule dataset and correctly renders a single complete page — the response does signal that it holds everything. The only genuine wart is the empty state, where `total=0, limit=0` makes `ceil(0/0)` NaN in JS; that is a cosmetic edge, not a wrong answer. Extra `limit`/`offset` query params being ignored is standard FastAPI behaviour shared by every unpaginated route in the service, not specific to rules.

No cap on rules per dataset exists (grep for MAX_RULES/max_rules finds nothing), so the collection is unbounded in principle — that is a scaling note, not the claimed defect. ROADMAP.md:285 explicitly discusses the `Page[T]` offset/limit envelope as the known, intentional pagination primitive.

## [low] Validate's 400/409 do carry only generic status-derived codes, but that is the documented default and status alone already disambiguates them on this route.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts hold. app/features/quality/api.py:126 `raise HTTPException(409, f"Version {version_number} is not ready (status: {ver['status']})")` and api.py:130 `raise HTTPException(400, "Dataset has no enabled quality rules")` are plain HTTPExceptions; app/api/errors.py:121 does `code=getattr(exc, "code", None)` and errors.py:104 falls back to `code or _code_for(status)` → "conflict"/"bad_request". app/features/data_accelerator/api.py:437-450 does raise ProblemException with code="validation-required"/"validation-failed", and tests/test_quality.py:199 really is `assert r.status_code == 400 and "no enabled" in r.json()["detail"]` (contrast tests/test_quality.py:105 which asserts `r.json()["code"] == "validation-required"`).

What the claim gets wrong is that this is a defect and that the UI is forced to parse prose.

1) The generic fallback is the deliberate, documented design, not an oversight. app/api/errors.py:12-16 shows the envelope with `"code": "not_found"` as the intended generic value and says "Consumers can branch on the machine-readable `code`"; ProblemException's own docstring (errors.py:61) says to raise it "where a machine-readable error contract matters", i.e. custom slugs are the opt-in exception. That is the repo-wide ratio: 226 `HTTPException(` raises vs 66 `code="` sites. ARCHITECTURE.md:128-131 lists the custom slugs as examples ("sheet-selection-required, unknown-column, …"), and its literal promise — every error is problem+json carrying a code — is kept here.

2) The impact claim is false. On POST /datasets/{id}/versions/{n}/validate the only pre-try failures are ensure_dataset_permission (403/404, app/features/auth/deps.py:135-144), resolve_version (404, app/shared/datasets.py:63-69), the 409 at api.py:126 and the 400 at api.py:130; everything inside the try is converted to 500 at api.py:159. So on this route 400 uniquely means "no enabled rules" and 409 uniquely means "version not ready" — a UI branches on the status code and needs no substring match. The service's own consumers already do exactly that: app/features/mcp/tools/curate.py:618 documents "not ready; 400 if the dataset has no enabled rules". The test's string match is belt-and-braces on the prose, not the only available discriminator.

3) The promotion gate is not "the very same workflow" — it lives in a different feature (data_accelerator tag promotion) where two distinct 409s (validation-required vs validation-failed) share one status and therefore genuinely need codes to be told apart. That is precisely the criterion in ProblemException's docstring, and it is absent here.

## [low] scope_type is deliberately derived from rule_type and echoed in every RuleOut, so ignoring a client-sent copy is documented design, not a silent failure.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics the claim describes are real, but its characterisation of them as a defect does not survive the source.

1. scope_type is derived on purpose, and it is not a field a client is invited to send.
- app/features/quality/schemas.py:14-24 — `# Which scope each rule type belongs to (drives request validation)` above `_RULE_SCOPES`, a total map from every one of the eight `RuleType` literals to its scope.
- schemas.py:42-44 — `@property def scope_type(self) -> str: return _RULE_SCOPES[self.rule_type]`. Scope is a pure function of `rule_type`; there is nothing for a client to contribute and no way for the two to disagree in storage.
- schemas.py:62 — `RuleUpdate` docstring: "Only provided fields change (rule_type/scope are fixed)." The immutability of scope is stated, not accidental.
- api.py:67 — `{**body.model_dump(), "scope_type": body.scope_type}` deliberately stamps the derived value over whatever `model_dump()` produced.
- The published contract is the OpenAPI schema generated from `RuleCreate`, which does not contain `scope_type`. A UI built from that schema never sends it. That three existing tests hand-write it (tests/test_logical_sheets.py:86, tests/test_wave0_journeys.py:125,131) is test noise — and in every case the value they send equals the derived value, so nothing is being papered over.

2. The impact statement — "successful 200/201 responses with no diagnostic", "the user believes the edit was saved" — is factually wrong about the code. Both mutations return the full stored resource, not an empty ack:
- api.py:72 `return RuleOut(**row)` on POST, and `RuleOut` (schemas.py:78) includes `scope_type`. A client that sent a conflicting `scope_type` gets the real one back in the 201 body.
- api.py:97 `return RuleOut(**row)` on PATCH, fed by `repo.update_rule` -> `get_rule` (repo.py:76-78), i.e. the current row. A PATCH of `{"ruleType": "unique"}` returns the rule with every field at its true value. The response *is* the diagnostic; the API never reports a state it does not hold.

3. The claim mis-attributes the PATCH cause. `RuleUpdate` (schemas.py:61-70) drops `ruleType` at the pydantic layer, so `body.model_dump(exclude_unset=True)` is already `{}` before `repo.update_rule` runs. The `_MUTABLE` filter (repo.py:71-72) contains every field `RuleUpdate` can produce plus `logical_sheet_id`, which `_resolve_sheet_selector` injects — so it never actually removes anything reachable from the API. It is defence-in-depth, not the mechanism.

4. `extra="ignore"` here is the framework-wide posture, not a quality-feature lapse: `grep -rn "model_config\|ConfigDict" app/features/*/schemas.py` returns nothing across the entire feature surface. And the repo has explicitly reasoned about this exact failure mode and drawn a scoped line — app/features/mcp/strict_args.py:1-14 calls a silently dropped argument "the worst failure mode a tool surface has ... worse than an error, because nothing signals it", and installs `UnknownArgumentGuard` for MCP specifically, where the caller gets opaque text back and cannot compare. On REST, where every mutation echoes the full resource, that argument does not apply. A deliberate, documented, asymmetric choice.

The only genuinely silent case in this area is a well-formed `{"rule_type": ...}` on PATCH, which is ignored — but that is the documented invariant at schemas.py:62 (scope is a function of rule_type, so a mutable rule_type would strand the stored scope), and the 200 body still returns the unchanged `rule_type`.

## [low] Same problem code `relationship-not-confirmed` really is 409 on /joins and 400 on /sample/coordinated, but both statuses are specified up front in the design plan and are contextually defensible.

- **domain:** ?
- **where:** 

**Evidence**

The factual half of the claim is accurate. app/features/relationships/joins.py:88-97 `require_confirmed()` raises `ProblemException(409, ..., code="relationship-not-confirmed", current_status=...)`; app/features/data_accelerator/services/sampling.py:667-675 in `_resolve_link_relationship()` raises `ProblemException(400, ..., code="relationship-not-confirmed", current_status=...)`. Both are the only two occurrences in app/ (grep). Both are pinned: tests/test_join_builder.py:124-125 asserts 409 + code, tests/test_coordinated_sampling.py:331-332 asserts 400 + code.

But the divergence is deliberate and pre-specified, not an accident. WAVE4-6-PLAN.md:216 (§23 join builder): "Guard: execute/publish require relationship `status='confirmed'` -> else 409 `relationship-not-confirmed`." WAVE4-6-PLAN.md:225 (§24 coordinated sampling): "validate confirmed + endpoints belong to the version (400 `relationship-not-confirmed`/`relationship-endpoint-mismatch`)." ARCHITECTURE.md:438-439 repeats the 409 for joins.

The two statuses also fit their contexts. In /joins/execute the relationship IS the request's subject and its state conflicts with the action -> 409. In /sample/coordinated the relationship_id is one field inside a `related[].link` element of a larger body, sitting next to sibling 400 validations in the same function -- `relationship-endpoint-mismatch` (sampling.py:688-692), `cannot-subsample-parent`, `No foreign_key quality rule links...` (sampling.py:638-651) -- so it is a malformed-link-spec error -> 400.

Finally, the documented contract for clients is the code, not the status: ARCHITECTURE.md:128-131 says all errors are problem+json with a machine-readable `code` "(`sheet-selection-required`, `unknown-column`, `relationship-not-confirmed`, ...) so a UI can branch on the code rather than parse prose." The code is identical across both routes, so the documented branching key is consistent; the claim's "a frontend that branches on `code` gets an inconsistent severity" is the weakest part -- `code` is stable and both responses are 4xx client errors with `current_status` in the body, and neither returns a wrong answer.

## [low] SeedResponse.created counts created-or-refreshed edges, matching the documented idempotent-refresh contract; only the field name is imprecise.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts of the claim check out but the "defect" framing does not.

app/features/relationships/api.py:55-57 does `created = await service.seed_from_fk_rules(...)` / `SeedResponse(created=len(created), relationships=[...])`, and service.py:125-137 appends the return of `repo.upsert_relationship(...)` for every matching FK rule regardless of insert-vs-update. repo.py:41-70 uses `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING id::text`, so the row comes back in both cases and the count is "edges seeded (created or refreshed)", never "rows newly inserted". So yes, re-seeding an unchanged dataset returns created=N.

But that meaning is the deliberate one, and it is documented at both ends:
- api.py:49-53 docstring: "Idempotent: re-seeding refreshes evidence but never overrides a status a human already set." The endpoint advertises that it is a refresh, not an insert-only operation.
- repo.py:33-39: "Insert or refresh a suggestion for a directed (sheet, column) pair. Idempotent by design — discovery can be re-run at will."
- The one in-repo consumer already renders it under the correct label: app/features/mcp/tools/pipeline.py:280 `render.fields([("created_or_refreshed", payload.get("created"))])`, with the accompanying text "Seeding is idempotent: re-running refreshes the evidence but never overrides a status someone has already set." The author clearly knew the field means created-or-refreshed and named it accordingly at the presentation layer.

Also note `created` is exactly `len(relationships)` in the same response body — it carries no information the client does not already have, and the `relationships` array it counts is itself accurate (those really are the N edges the seed produced/refreshed, each with its true `status`, e.g. `rejected` per tests/test_relationships.py:74-86). Nothing downstream branches on `created`; grep for `SeedResponse`/`relationships/seed` finds only api.py, schemas.py, ARCHITECTURE.md:426 and the MCP renderer.

tests/test_relationships.py:62-71 does assert only `total == 1` on the listing after two seeds, so the claim is right that the second response body is unasserted — but there is nothing wrong there to pin. The residual issue is a field NAME in schemas.py:47-51 (`created: int`, no description) that reads as insert-count; distinguishing real inserts would require an `xmax = 0` trick in the RETURNING clause, which no code or doc asks for.

## [low] measure()'s output_columns arg is genuinely unused, but column_collisions is contractually "names present on both sides", not a per-projection warning.

- **domain:** ?
- **where:** 

**Evidence**

Both mechanical observations are literally true, but the defect framing is not.

1) Dead parameter — confirmed but inert. app/features/relationships/joins.py:136-169: `def measure(conn, left, right, how, output_columns) -> JoinWarnings:` and nowhere in the body (139-169) is `output_columns` referenced. The other params are used (`how` feeds `probes.expansion_sql(...)` at :148). Both call sites pass `names` (joins.py:218, joins.py:275). So it is an unused parameter — a lint-level nit, not behaviour.

2) Collisions from full column lists — confirmed, and that IS the specified contract. probes.py:212-221 `column_collisions(left_columns, right_columns, left_key, right_key)` docstring: "Non-key column names present on both sides. These are the names a join has to disambiguate; surfacing them up front is the difference between a guided join and a surprising one." schemas.py:101-102 states the same contract to the UI: `column_collisions: list[str] = Field(default_factory=list, description="Non-key column names present on both sides")`. WAVE4-6-PLAN.md:207 spells out the same design: "(5) collisions = non-key names on both sides." It is a property of the two inputs, deliberately, not of the projection.

3) The claimed UI impact does not exist. Nothing anywhere treats a collision as an error or a gate. build_join_sql (joins.py:109-133) unconditionally resolves every collision by prefixing the right side's copy with `right.sheet_row["sheet_key"]` (:115-116), and drops the right key entirely (:113). There is no 4xx, no block, no "resolve this first" state — grep for `column_collisions` finds only the probe, the schema field, the assignment, and two read-only render sites. The MCP surface confirms it is advisory: pipeline.py:150 prints it as one line among left_rows/right_rows/etc., and pipeline.py:175-180 emits a *note*, not a warning to clear: "Colliding column names (...) are kept from both sides — the right side's copy is prefixed with its sheet key." There is no mechanism by which a user "already resolved" a collision via select_columns, because collisions were never the user's to resolve; the builder always resolves them.

4) select_columns is applied strictly after aliasing (joins.py:119-127: unknown-name check against the aliased `names`, then filter `parts`/`names`), so the produced data and `output_columns` are correct under projection. No wrong row, no wrong column name, no wrong count.

The only residue is that the advisory sentence at pipeline.py:175-180 can read as stale if a user projects one side away — cosmetic prose, and even then the field itself remains true to its documented meaning.

## [low] POST /relationships/suggest deliberately returns the dataset's whole suggested backlog (capped at 100) alongside the run's counters; the only real rough edge is the silent 100 cap.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts of the claim are correct but the framing is wrong on every point that matters.

1) The scope is documented and deliberate, not accidental. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/pipeline.py:197-200 describes this exact endpoint to its consumers: "'suggest' probes the data statistically for undeclared relationships and records what it finds as suggestions (it also returns every suggestion already on the dataset, so it doubles as the way to get a relationship_id)". Returning the full pending backlog is the stated contract, not a leak of "other" edges.

2) The array is not mislabelled. app/features/relationships/schemas.py:53-61 — SuggestResponse has `pairs_examined`, `suggested`, and `relationships`; nothing claims `relationships` is this run's output. It mirrors SeedResponse (schemas.py:46-51, `created` + flat `relationships` list), so the "every other list in the service returns a Page envelope" assertion in impact_on_ui is false — the sibling action endpoint has the same flat shape, and these are action results, not list endpoints.

3) "no way to page" is false. app/features/relationships/api.py:119-132 GET /datasets/{dataset_id}/relationships takes `status` (pattern "^(suggested|confirmed|rejected)$") and PageParams and returns `Page.of(..., total, page)` via the same repo.list_relationships, which computes a real COUNT(*) (repo.py:84-101). A UI that needs all pending suggestions with a total pages that endpoint; the suggest response is a convenience payload.

4) A test already exercises and accepts the divergence between counter and array: tests/test_relationships.py:120-129 posts `suggest?sync=false`, asserts `suggested == 0` ("nothing has run yet"), then runs the worker and reads the paged GET for the real listing. The counters are run stats; the listing is the backlog.

5) Where the counter and array can legitimately differ, the data is still truthful: seeded fk_rule edges land as status 'suggested' (tests/test_relationships.py:56) and discovery is idempotent by design (repo.upsert_relationship docstring, repo.py:33-38), so `suggested` counts qualifying pairs this run while `relationships` is the pending queue. Both numbers are correct for what they measure.

The one residue of substance: service.MAX_CANDIDATE_PAIRS = 400 (app/features/relationships/service.py:47), so a single run can in principle persist more than 100 suggestions, and api.py:77-78 hard-codes limit=100/offset=0 and discards the total (`rows, _ =`). That is a silent truncation of a convenience payload with a fully paged alternative one GET away — cosmetic, not a wrong answer.

## [low] Three join errors carry only status-derived codes, but that is the repo's documented default and one of the three is unreachable.

- **domain:** ?
- **where:** 

**Evidence**

The three lines exist verbatim: joins.py:76-77 `raise HTTPException(404, f"Version has no data (status: {ver.get('status','unknown')})")`, joins.py:179 `raise HTTPException(409, "A dataset behind this relationship no longer exists")`, api.py:251 `raise HTTPException(409, "This run did not record the relationship it used")`. app/api/errors.py:104 does fall back to `_code_for(status)` -> "not_found"/"conflict". That much is accurate; everything the claim builds on top of it is not.

1) Not an inconsistency — it is the house convention. errors.py:60-65 states ProblemException is for "where a machine-readable error contract matters"; ARCHITECTURE.md:128-131 says errors carry a code "(`sheet-selection-required`, `unknown-column`, `relationship-not-confirmed`, ...) so a UI can branch on the code", i.e. slugs are added selectively, and the status-derived code is the documented default. The exact "Version has no data (status: ...)" 404 is a shared, codeless pattern in seven places — app/shared/datasets.py:52, app/features/explorer/api.py:49,196,333, app/features/transform/service.py:92,238, app/features/data_accelerator/services/sampling.py:748 — so joins.py:77 is copying the service-wide idiom, not deviating from its siblings. Likewise the codeless 409 "no longer exists" appears at library/service.py:225,228 and library/api.py:276,287.

2) joins.py:179 is unreachable through the API. `_open_sides` is called only from `preview_join`/`execute_join` (grep: no other callers), and both routes go through `_authorized_relationship` (api.py:194-202), which calls `ensure_dataset_permission` on `dataset_id` and, when different, on `to_dataset_id`; deps.py:135-137 already raises 404 "Dataset not found" if `get_dataset` returns None. So by the time `_open_sides` re-fetches both datasets they are both known to exist — it is defensive dead code, not an "actionable failure mode" the wizard needs to distinguish.

3) The claim's own contrast is undercut: sibling slugs in these same handlers (relationship-not-confirmed, unknown-column, relationship-endpoint-mismatch, sheet-not-in-version) are exactly the branchable cases, and they ARE tested (tests/test_join_builder.py:125,195; tests/test_coordinated_sampling.py:332,353; tests/test_saved_views.py:177). Absence of tests for three degenerate/unreachable states is a coverage observation, not a defect.

The only residual nit is api.py:249-251: a join run that is still running or failed has no `result_summary`, so it gets the 409 "did not record the relationship it used" rather than a status-shaped message — mildly imprecise prose on a correct status code.

## [low] Relationship listing is scoped to the owning (`from`) dataset by explicit design; there is no inbound-edge query, but none is promised.

- **domain:** ?
- **where:** 

**Evidence**

The claim's facts check out, but the framing as a defect does not.

Facts confirmed:
- `app/features/relationships/repo.py:87` — `where = "r.dataset_id = :did" + ...`; the count and the SELECT both use it. Nothing filters on `to_dataset_id`.
- `repo.py:131` `find_confirmed_between(from_logical_sheet_id, to_logical_sheet_id)` does take both sheet ids, and grep across `app/` shows it has no caller at all (the only cross-module import, `app/features/data_accelerator/services/sampling.py:661-663`, calls `get_relationship`), so it is certainly not exposed over HTTP.
- The only HTTP list is `app/features/relationships/api.py:119-132`, which passes just `dataset_id`.

Why this is deliberate, not a bug:
- The migration states the model outright, `app/infra/db/postgres/migrations/20260810000000_relationships.sql:4-7`: "Both endpoints carry their own dataset id... `dataset_id` is the OWNING side and is what scopes RBAC."
- Every mutation is ownership-scoped to match: `_relationship_or_404` rejects when `row["dataset_id"] != dataset_id` (api.py:37-41), and `delete_relationship` has `WHERE id = :id AND dataset_id = :did` (repo.py:125). A dataset listing edges it does not own would be listing rows it cannot confirm, reject or delete.
- The endpoint does not over-promise: the docstring is "Relationships owned by this dataset, most confident first" (api.py:128), the repo docstring is "Edges owned by a dataset" (repo.py:86), and the MCP tool declares the parameter as "Dataset UUID that owns the edges (the 'from' side)" (`app/features/mcp/tools/context.py:665-667`). No caller is told it is getting a full lineage view.
- An inbound listing would be a cross-tenant side channel, which the module explicitly guards against: api.py:3-6 ("a relationship would be a side channel for learning that another team's dataset exists") and `_authorized_relationship` api.py:189-193. `test_a_cross_dataset_relationship_needs_access_to_both_sides` and `test_another_teams_relationships_are_invisible` (tests/test_relationships.py:227-268) pin that property. Serving inbound edges from the target dataset would hand a target-side reader the existence and sheet/column names of a source dataset they have no permission on.

The impact statement is also overstated. Discovery is intra-dataset only — `_candidate_pairs` iterates the sheets of a single version (`service.py:145-160`) — and FK seeding sets `to_dataset_id=dataset_id` (`service.py:129`). So every auto-created edge has `dataset_id == to_dataset_id` and IS returned by the list endpoint (tests/test_relationships.py:59 asserts exactly this). Only hand-declared cross-dataset edges are one-directional, and those are precisely the ones with the RBAC hazard.

What is genuinely true is narrower than "no way to answer impact": there is no inbound/reverse relationship query in the HTTP API. That is a missing feature for a hypothetical impact-analysis screen, not code doing the wrong thing. The `ix_dataset_relationships_to_dataset` index (migration line 41-42) means such a query could be added cheaply if the RBAC question were answered.

## [low] Definition `params` really are stored unvalidated, but that is a documented, tested design choice and the "dry run endpoint doesn't exist" part is false.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts check out. `app/features/library/schemas.py:34` declares `params: dict[str, Any] = Field(default_factory=dict, ...)` on `DefinitionCreate` (and `dict[str, Any] | None` on `DefinitionUpdate`, schemas.py:45) — no `kind`-aware validator, no model_validator anywhere in that file. `app/features/library/api.py:48-53` passes `body.model_dump(...)` straight to `repo.create_definition`; the only checks are RBAC and a 409 on duplicate name. Binding to the real request model happens only in `service.py:58-98` (`build_definition_request`), called from `execute_definition` (service.py:123) and `compute_definition` (service.py:360). And `grep -rn "validate|dry_run|dry-run" app/features/library/` returns only `_validate_chart_source` — there is no `/analytics/validate` route.

But the claim frames this as a defect, and the repo says otherwise in two places.

1. It is deliberate and documented. `schemas.py:36` describes params as "Body of the underlying /sample, /aggregate, or /profile request (minus dataset/version/sheet, which come from the definition)" — i.e. a deliberate passthrough envelope. `service.py:59-78` is a long docstring explaining exactly WHY: "Definitions are stored as free-form JSON, so a definition saved under an older, looser schema can hold a value the current model rejects." Late binding is the point: it lets a stored definition outlive a schema change instead of being unreadable, and the machinery exists to turn that into an actionable typed error, not a 500. That is the opposite of "stale-params machinery exists because this is a known failure mode [of missing validation]" — it exists because storage and schema are versioned independently.

2. A passing test asserts the claimed behaviour as the intended contract. `tests/test_library.py:240` — `test_saved_definition_with_stale_params_is_a_400_not_a_500` — POSTs a definition with `sort_order: "ASC"` and asserts `r.status_code == 201` (line 260), then asserts the run is a 400 `application/problem+json` with `code == "invalid-definition"` and `errors == [{"param": "sort_order", "reason": "Input should be 'asc' or 'desc'", "value": "ASC"}]` (lines 263-270), then asserts `hist["total"] == 0` (no run row for something that never ran), then PATCHes the params and runs it successfully. `tests/test_library.py:296` does the same for the chart-render path. The 201-then-400 sequence the claim calls a bug is the assertion.

3. The impact paragraph is factually wrong on its key point. "The obvious UI fix (bind the params in a dry run) needs an endpoint that does not exist." It does exist. `service.py:50-55` maps each kind to `SampleRequest / AggregateRequest / PivotRequest / ProfileRequest`, and `app/features/data_accelerator/api.py:589, 614, 621, 633` expose `POST /sample`, `/profile`, `/pivot`, `/aggregate` taking those exact models. A definition-builder form posts `{dataset_id, sheet, **params}` to the matching endpoint and gets a 422 naming the bad field before saving — the same binding `build_definition_request` performs. The failure is also not silent: the 400 carries `param`, `reason` and the rejected `value`.

What survives is a real but minor ergonomic gap: no cheap validate-only route (the operation endpoints execute and register artifacts), so pre-save validation costs a computation. That is a feature request, not a defect.

## [low] Run requires DATASET_WRITE because it creates a job, a run row and an artifact; render is persist=False and writes nothing.

- **domain:** ?
- **where:** 

**Evidence**

The permission split is real but it is the repo's documented, uniformly applied rule: endpoints that write durable state require DATASET_WRITE, endpoints that compute-and-return require DATASET_READ.

- app/features/library/api.py:116 `run_definition` -> `execute_definition`, which at app/features/library/service.py:125-181 calls `jobs.create_job(...)`, `jobs.start_job(...)`, `repo.create_run(...)`, writes a parquet via `ArtifactLayout(...)` with the default `persist=True`, and calls `repo.create_artifact(...)`. That endpoint creates a job row, an analytics_runs row, a stored blob and an artifact row — all mutations of the dataset's control plane. Its output is also the only thing `POST .../runs/{run_id}/publish` (api.py:146, also DATASET_WRITE) can turn into a new dataset/version.
- app/features/library/api.py:267 `render_chart` -> `compute_definition`, whose docstring at service.py:341-347 states the intent explicitly: "``execute_definition`` is the durable path: it opens a job, writes an analytics_runs row, and registers an artifact. Rendering a chart is a read — doing that on every render would fill the run history with noise — so this recomputes the same operation and returns the rows directly." service.py:363-368 forces `persist=False` precisely so nothing is written.
- The render handler's own docstring (api.py:265) says "Read-only: no run row and no artifact, unlike executing the definition."
- ARCHITECTURE.md:396-398 and HANDOFF.md:139-143 record the same decision ("Charts own no query logic; render re-runs the referenced view/definition. Rendering is `persist=False`").
- tests/test_charts.py:200 `test_rendering_persists_nothing` asserts run history stays at total == 0 after a render — a passing test pinning the very property that justifies DATASET_READ.
- The rule is consistent elsewhere, not special-cased for charts: app/features/explorer/api.py:176 `run_view` (execute a saved view) is DATASET_READ, api.py:328 ad-hoc sandboxed SQL is DATASET_READ, while create/update/delete view (api.py:108,148,162) are DATASET_WRITE.

Two of the claim's premises are also inaccurate. "compute_definition and execute_definition compute the same result from the same data" is not quite true: compute_definition (service.py:354-356) rejects any kind outside pivot/aggregate/sample with a 400, so `profile` definitions are runnable but not renderable, and compute_definition never resolves/records a version row (`resolve_version` at service.py:115 is on the run path only). And the DATASET_READ comment at app/features/auth/permissions.py:36 reading "list/get/versions/tags/sheets/download/analytics" sits next to DATASET_WRITE's "upload, create version, patch..." — "analytics" there covers reading analytics definitions, which api.py:64/76/132 do gate at DATASET_READ; it is not a statement that executing-and-recording a definition is a read.

So: a viewer really does get charts but a 403 on Run, and that is exactly the intended contract — Run appends to run history and produces a publishable artifact, which is an editor action; render mutates nothing.

## [low] PATCH with an explicit `version_selector: null` does store `{}` — but `{}` is the documented canonical encoding of "current", not corruption.

- **domain:** ?
- **where:** 

**Evidence**

The mechanism the claim describes is real. `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/library/api.py:91-94`: `fields = body.model_dump(exclude_unset=True)` then `if "version_selector" in fields and body.version_selector is not None:` — with an explicit null the key is present with value `None`, the model_dump branch is skipped, and `None` reaches the repo. `/home/saketh/.../app/features/library/repo.py:63-71`: `allowed` contains `version_selector`, `updates` is non-empty (the key is present), and `params[k] = json.dumps(v or {})` writes `{}`. `DefinitionUpdate.version_selector: VersionSelector | None = None` (schemas.py:43) so pydantic does NOT reject the null, and `DefinitionOut.version_selector: dict[str, Any]` (schemas.py:54) happily serialises `{}`.

Where the claim breaks is the characterisation of `{}` as corruption. ARCHITECTURE.md:268-271 defines the contract explicitly: "Saved artefacts (views, analytics definitions, transformations) don't pin a version id. They store a `version_selector` JSONB — `{}` (current), `{"version_number": N}`, or `{"tag": "production"}` — resolved **at run time**." So `{}` IS the documented representation of "current"; a mode key is not part of the wire contract a UI may assume, and any client reading `version_selector.mode` unconditionally is already violating the documented shape. `app/features/library/service.py:101-108` `_selector_pin` implements exactly that contract (`selector or {"mode": "current"}` → `{}` → current), which is why the claim itself concedes "nothing breaks server-side".

PATCH semantics are likewise documented as deliberate: ARCHITECTURE.md:457-462 — "`PATCH` merges, writing only the fields present in the body while an explicit `null` still clears. `PATCH` therefore needs `exclude_unset=True` plus a partial `UPDATE` built column by column". Clearing `version_selector` to the default (current) and `params` to `{}` on an explicit null is that documented clear-on-null rule, not an accident. Omitting the field entirely leaves both untouched (the key never enters `fields`).

No test asserts the opposite; grep of tests/ for `version_selector` shows no PATCH-with-null case at all (tests/test_library.py:108-109 patches a real selector and asserts `mode == "tag"`).

The one genuine wart, not what the claim alleges: the sibling features preserve instead of clearing — app/features/explorer/service.py:159-160 `selector = (... if body.version_selector is not None else view["version_selector"])` and app/features/transform/service.py:155-157 do the same. So library is inconsistent with views/transformations on explicit null (clear vs. leave alone). That is a cross-feature contract inconsistency, not a silent wrong answer.

## [low] The ownerless-artifact fallback in `_output_layout` is a documented, deliberate branch, and the claim's stated trigger ("no home team") cannot occur — users.team_id is NOT NULL.

- **domain:** ?
- **where:** 

**Evidence**

The code mechanics the claim describes are real. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/api.py:126-131:

    try:
        team_id = pick_active_team(principal, None)
    except HTTPException:  # no unambiguous team -> ownerless (superuser-only)
        team_id = None

and app/features/files/api.py:704-716 does 404 a non-superuser on a row with neither dataset_id nor team_id, and app/features/library/repo.py:169-171 filters `team_id = ANY(:tids)` for non-superusers ("including the ownerless rows, which are superuser-only by design").

But the claim's premise is factually wrong on two counts.

1. "a user who belongs to two teams and has no home team" is impossible. app/infra/db/postgres/migrations/20260311001644_baseline.sql:21 — `team_id UUID NOT NULL REFERENCES teams(id)` — and Principal.home_team_id is exactly `user.get("team_id")` (app/features/auth/deps.py:81). Every user always has a home team. app/features/auth/deps.py:103-108 returns that home team whenever it is in memberships, and app/features/auth/api.py:90-96 creates the user with `team_id=team_id` and immediately `upsert_member(team_id, user["id"], VIEWER)`, so the home team is a membership by construction. Adding a user to a second team (POST /teams/{id}/members) does not touch users.team_id, so the multi-team user the claim describes still resolves cleanly to their home team. The 400 branch is only reachable after an admin removes the user from their *home* team while leaving them in >=2 others (or in 0) — a degraded state the claim never mentions.

2. The branch is deliberate and documented, not an oversight: the inline comment at api.py:128 names the outcome ("ownerless (superuser-only)"), the /samples docstring at files/api.py:694-696 states "A row with no owner at all (inline-data or superuser file_path sources) is superuser-only", repo.py:167 repeats "superuser-only by design", and sampling.py:557-561 says "Callers with no dataset context pass an ownerless layout explicitly". tests/test_artifact_storage.py:114 (`test_ownerless_output_uses_the_shared_prefix`) pins the shared-prefix behaviour. The alternative — raising the 400 — would hard-fail /sample and /aggregate for a caller whose analytics request is otherwise perfectly valid, and those routes take no X-Team-Id header for the caller to disambiguate with.

Impact is also overstated: SampleResponse (schemas.py:608-620) returns `preview` and `data` inline, so the caller still gets their result; only the optional download filename is unreachable, and only in the removed-from-home-team state.

## [low] compute/split/merge `into` that names an existing column replaces it in place and keeps the physical label — documented, and matching by normalized name mirrors the DSL's own resolver.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics the claim describes are real, but they are the documented contract, not an accident.

app/features/transform/compile.py:326-340 — the function's own docstring states the intent: "Emit a step that writes one derived column, replacing it in place when *into* names an existing column and appending it otherwise." Line 329-330 matches `c["name"] == into or c.get("normalized_name") == into`; line 332-333 emits `replace={existing["name"]: emitter}` so `_emit_select` (compile.py:113-115) emits `<expr> AS "<existing physical name>"`, and line 334 keeps `name`/`normalized_name` and changes only `dtype`.

That "replace" semantic is declared on the public models, so it reaches the UI through OpenAPI: app/features/transform/steps.py:133 `"""Add (or replace) a column from a typed expression — ROADMAP §20."""` and steps.py:119 `into: ColumnName = Field(..., description="Output column (may be the source column)")`.

The normalized-name arm — the part the claim treats as the defect — is consistent with how every column reference in this DSL is addressed. app/features/transform/expr.py:187-198, `resolve_column`, tries `normalized_name` FIRST and only then the physical name ("Resolve a column reference against *schema* — normalized name first. Same precedence ... as the query DSL's resolver"). So `into="revenue"` on a sheet whose physical column is "Revenue" IS the canonical way to name that existing column; had `_emit_projection` appended instead, the result would be two columns sharing normalized name "revenue" and every downstream `resolve_column("revenue")` would silently bind to the OLD one — strictly worse and genuinely silent. Retaining the physical label is also coherent: `rename` is the step that changes labels (compile.py:152-167), and it is the one that must reject duplicates because it does not carry replace semantics.

Where the claim is right but not damning: nothing in tests/unit/test_transform_steps.py exercises the replace arm — every `into=` there is a fresh name ("doubled" l.196, "month" l.271, "who" l.277, "flag" l.305), and tests/test_transformations.py uses only new names ("amount_with_tax", "band", "x", "double_amount"). That is a coverage gap on a documented branch, not a wrong answer. No pydantic constraint bears on it: `ColumnName` is just `Annotated[str, Field(min_length=1, max_length=200)]` (steps.py:30).

**Test to pin it**

Optional coverage, not a fix: "test_compute_into_an_existing_columns_normalized_name_replaces_it_in_place_and_keeps_the_physical_label" in tests/unit/test_transform_steps.py, pinning that `ComputeStep(into="full_name")` against physical "Full Name" yields one column still named "Full Name" with the new dtype.

## [low] POST profile-runs really does return 200 while POST views returns 201, but it is an idempotent upsert, not a create, so the codes are not inconsistent.

- **domain:** ?
- **where:** 

**Evidence**

The literal code observation is accurate. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/explorer/api.py:100-101 has `@router.post("/datasets/{dataset_id}/views", response_model=DatasetViewOut, status_code=201, ...)`; api.py:182-183 has `@router.post(".../profile-runs", response_model=list[ProfileRunOut], ...)` with no status_code, so FastAPI defaults to 200.

But "the same kind of operation" is false. profile-runs is an idempotent upsert, not a resource creation:
- api.py:189-190 docstring: "Profile every ready sheet of a version; persist one run per sheet with deterministic insights (idempotent per algorithm version)."
- service.py:301-306 `profile_version` docstring: "Idempotent per (version, sheet, algorithm_version): re-profiling resets and replaces the previous run."
- service.py:327 calls `repo.upsert_run(...)`, not an insert.
- api.py:191-192 requires only `Permission.DATASET_READ`, whereas create_view (api.py:108-109) requires `DATASET_WRITE`. It is modelled as a read/refresh compute action.
- It returns a list of the current representations, not one newly created object with a Location-worthy id.

tests/test_profiling_runs.py:72-82 (`test_reprofile_is_idempotent`) asserts `first[0]["id"] == second[0]["id"]  # same (version, sheet, algo) row` and then `len(r.json()) == 1  # reset, not duplicated`. A second POST creates nothing, so 201 would be wrong on the repeat call. tests/test_profiling_runs.py:47 pins `r.status_code == 200`, and ~12 other tests across tests/test_health.py, tests/test_wave1_journeys.py, tests/test_timeline.py, tests/test_transformations.py rely on 200.

The repo's actual convention is consistent, not "201 everywhere with one exception": only 8 of 42 `@router.post` handlers in app/features/*/api.py use status_code=201 (auth/api.py:68 users, webhooks/api.py:62, transform/api.py:42, explorer/api.py:101, relationships/api.py:87, quality/api.py:59, library/api.py:42 and :182). Every one returns a single freshly created named resource; none is an idempotent upsert. The remaining 34 POSTs (query, run_view at api.py:167-168, etc.) return 200 as action endpoints. profile-runs follows the majority rule correctly.

Nothing here can produce a wrong answer or block a screen; a client branching on "201 means created" would in fact be misled by 201 on the second, non-creating POST.

## [low] Preview takes only `limit`, but the next_cursor it returns is fully consumable by the sibling POST /query — preview is a documented LIMIT-n convenience, not a paging route.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is accurate but its conclusion ("advertises a next_cursor that ... cannot [be] consume[d]", "the field is misleading") is false.

1. Signature, as claimed. app/features/explorer/api.py:53-77 — `preview_version` / `preview_sheet` take only `limit: int = Query(default=100, ge=1, le=1000)` and build `QuerySpec(limit=limit)`; response_model is the shared `QueryPage`.

2. The cursor IS consumable. app/shared/query/compile.py:80-84: `spec_hash` = "sha256 over the canonical JSON of the spec minus cursor/limit" — `payload = spec.model_dump(mode="json", exclude={"cursor", "limit"})`. Preview's spec is all-defaults, so the hash of a preview cursor equals the hash of a default `QuerySpec` body posted to `POST .../versions/{n}/query` (api.py:80-90) or `.../sheets/{s}/query` (api.py:343-359) — the same resource, one level of the same path. compile.py:126-131 only rejects when `c["h"] != h or c["v"] != version_id`. Neither holds. And limit being excluded is deliberate: tests/unit/test_query_dsl.py:364-370 `test_cursor_limit_change_does_not_invalidate` — "limit is excluded from the spec hash — clients may resize pages mid-scan."

3. Emitting next_cursor from preview is asserted by a passing test, not an accident. tests/test_explorer.py:38-46 `test_preview_single_sheet_version` asserts `body["next_cursor"]` with the comment "# more rows exist".

4. The split is documented design. ROADMAP.md:373-376 spells out the two routes: "`GET .../preview?limit=` ... `SELECT * LIMIT n`" vs "`POST .../query` — body `QuerySpec`; validate → compile → execute". ARCHITECTURE.md:339-342 lists them as a pair. HANDOFF.md:356 "§6 Explorer. `GET .../preview` + `POST .../query` (body `QuerySpec` → `QueryPage`)". The repo's own MCP client implements exactly the handoff the claim calls impossible: app/features/mcp/tools/look.py:62-65 — "More rows available — pass cursor='{payload['next_cursor']}' to query_rows with the same spec to continue."

So: one shared `QueryPage` envelope (schemas.py:205-215) across preview and query; preview is the zero-body first-page convenience; page 2 is the same cursor on the sibling POST. The next_cursor is honest — it just isn't redeemed on the GET.

## [low] Viewers can run SQL/profile under dataset:read — that is the documented "analytics is a read capability" model, and the claim's unbounded-growth rationale is factually wrong.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts are right but the framing is not.

1) Deliberate, repo-wide, and documented. app/features/auth/permissions.py:36 defines the capability as `DATASET_READ = "dataset:read"  # list/get/versions/tags/sheets/download/analytics` — analytics is explicitly part of read. This is not an explorer slip: every analytics producer in the accelerator authorizes the same way — data_accelerator/api.py:110 `_authorize_source` -> `ensure_dataset_permission(..., Permission.DATASET_READ)`, used by /sample (592), /sample/coordinated (605), /pivot (625), /aggregate (636), each followed by `_register_output_artifacts`. explorer/api.py:328-339 (sql) and :191-197 (profile-runs) are consistent with that model, not deviations from it. ARCHITECTURE.md:571 lists "`_output_layout` / `_register_output_artifacts` | data_accelerator/api.py | sample, pivot, aggregate, sql, coordinated" as one family. Where the repo wants write authority it says so in the same file: create_view/update_view/delete_view use `Permission.DATASET_WRITE` (explorer/api.py:109, 149, 162).

2) "Unbounded and undeduped" is contradicted by the documented retention plane. ARCHITECTURE.md:154 "### 4b. Derived artifacts — the disposable plane" and 4d Retention: "`artifacts.expires_at` is stamped **at write time from the policy then in force**" with a table giving `export`, `query_output` = **7 days** ("Pure scratch"), reclaimed by `artifact_gc` (expired rows, then orphan blobs past 24h). That is enforced in code, not just prose: library/repo.py:119-127 `create_artifact` imports `expires_at(artifact_type, ...)` and persists it; repo.py:570 sweeps `WHERE expires_at IS NOT NULL AND expires_at <= now()`. Per-call parquet files are the intended shape of a scratch kind, not a leak. (The one real gap is separately documented already: ARCHITECTURE.md:637 "Schedule `artifact_gc` | — | Job + admin trigger exist; nothing fires it on a timer".)

3) The profile-runs half of the claim is simply false. It says a viewer "creates a job row, run rows and insight rows". Runs and insights are idempotent, not accumulating: service.py:301-306 "Idempotent per (version, sheet, algorithm_version): re-profiling resets and replaces the previous run", implemented at repo.py:27-46 as `INSERT INTO profile_runs ... ON CONFLICT (dataset_version_id, logical_sheet_id, algorithm_version) DO UPDATE SET status='running', profile=NULL ...` followed by `DELETE FROM profile_insights WHERE profile_run_id = :rid`. N calls produce exactly one run row and one insight set per (version, sheet). Only the `jobs` row (service.py:317) is per-call, which the claim never isolates.

4) The UI concern is answered by the same matrix the claim invokes: a UI driven by ROLE_PERMISSIONS sees viewers holding `dataset:read`, whose documented scope includes analytics, and both endpoint docstrings state the persistence up front ("results are row-capped and persisted as a `query_output` artifact", explorer/api.py:325-327; "persist one run per sheet", :189-190). No test asserts 403 for a viewer on /sql or /profile-runs; the only viewer tests in tests/test_explorer.py:210,349 and tests/test_profiling_runs.py:188 are cross-team 404 probes.

## [low] View name-collision 409s use the generic `conflict` code, matching every other 409 in the service; the claimed `name: None` message is unreachable.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical fact is correct but the framing is not. app/features/explorer/service.py:145-147 raises `HTTPException(409, f"A view named '{body.name}' already exists on this dataset")` and :176-180 does the same on IntegrityError. app/api/errors.py:115-124 renders any StarletteHTTPException through `problem_response`, and with no `code` attribute it falls back to `_code_for(409)` = "conflict" (errors.py:54-55, :104). So yes, no slug and no structured `name` field.

But "unlike every other error in this domain" is false as a characterisation of the convention. Every 409 in the service is a plain HTTPException with the generic code: app/features/library/service.py:220,222,225,228,271 ("A dataset named '{target_name}' already exists in this team"), app/features/transform/service.py:143 and :175 (byte-for-byte the same create/update pair, including `fields.get('name')`), app/features/relationships/service.py:324. The slug-carrying ProblemExceptions the claim lists are 400/404 semantic errors where the UI must branch on *which kind* of error it is; a 409 on a create/PATCH of a named resource has exactly one cause, so the status alone is the discriminator. HANDOFF.md:567 states the policy as "`ProblemException` (api/errors.py) carries custom codes + extra fields" — used "where a machine-readable error contract matters" (errors.py:61).

The concrete impact claim is refuted outright. The only unique constraint on the table is `UNIQUE (dataset_id, name)` (app/infra/db/postgres/migrations/20260807010000_profiling.sql:51); the other constraints are FKs to datasets/dataset_sheets/users, all with ON DELETE CASCADE/SET NULL. repo.update_view (app/features/explorer/repo.py:240-261) only sets name/description/logical_sheet_id/version_selector/query. A unique violation therefore requires `name` to be in `fields`, in which case `fields.get('name')` is the offending name, never None. There is no realistic input for which the message renders "A view named 'None'".

tests/test_saved_views.py:53-55 pins the contract that is actually promised: duplicate POST -> 409, no assertion on `code`.

## [low] TEAM_DELETE is an unused enum member; team delete/rename endpoints were never in the documented API surface, so this is unbuilt scope, not a defect.

- **domain:** ?
- **where:** 

**Evidence**

The claim's raw facts check out, but its framing as a defect does not.

Facts confirmed:
- /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/auth/permissions.py:46 `TEAM_DELETE = "team:delete"  # delete the team / transfer ownership` and :56 `_OWNER: set[Permission] = _ADMIN | {Permission.TEAM_DELETE}`.
- Repo-wide grep for `TEAM_DELETE|team:delete` across .py/.md/.json returns exactly those two lines. Nothing consumes it.
- The whole teams surface is app/features/auth/api.py:29 `teams_router = APIRouter(prefix="/teams")` with only: :104 POST "", :115 GET "", :123 GET "/{team_id}/members", :135 POST "/{team_id}/members", :151 PATCH "/{team_id}/members/{user_id}", :170 DELETE "/{team_id}/members/{user_id}". No DELETE /teams/{id}, no PATCH /teams/{id}.
- app/features/auth/repo.py has create_team/get_team/upsert_member/remove_member/list_team_members — no delete_team and no rename.
- app/main.py:143-161 registers only auth_router and teams_router for team routes; there is no separate admin router that could hold a delete.
- create_team (api.py:104-112) takes only `principal: Principal = Depends(get_principal)`, so yes, any authenticated caller can create a team.

Why this is BY_DESIGN rather than a bug: ARCHITECTURE.md:304-306 enumerates the public team API and lists exactly the six implemented routes — `GET /teams`, `POST /teams`, `GET/POST /teams/{id}/members`, `PATCH/DELETE /teams/{id}/members/{user_id}`. Team deletion/rename is not in the documented surface, and ROADMAP.md contains no team-CRUD item (its only team mentions are cross-team 404 hiding). HANDOFF.md:578-580 likewise describes the RBAC matrix and member-management rules with no team-lifecycle claim.

The claim's impact_on_ui is overstated: "the admin console has an owner-only capability it can never surface" presupposes a capability that was never specified or shipped. Nothing returns a wrong answer, nothing 500s, no screen is blocked from doing what the API promises. What actually exists is one dead enum member whose trailing comment ("delete the team / transfer ownership") reads as a placeholder for future scope. That is cosmetic dead code plus a product gap, not a correctness defect.

## [low] Discovery's plain 404s all carry code "not_found" — but that is the documented status-derived default of the problem+json envelope, pinned by tests, with slugs added only where a machine contract is needed.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is accurate. `app/api/errors.py:104` sets `"code": code or _code_for(status)` and `_code_for` (errors.py:54-55) lowercases the status title, so every bare `HTTPException(404, ...)` renders `code: "not_found"`. That covers `raise HTTPException(404, "Dataset is not in your favorites")` (discovery/api.py:193), `f"No metadata recorded for sheet: {sheet_key}"` (api.py:239), `f"Sheet not found: {sheet_key}"` (api.py:279), and `f"No dictionary entry for column: {column_name}"` (api.py:349 and api.py:385), while `ProblemException(..., code="sheet-not-in-version", version_number=...)` (api.py:288-290) and `code="unknown-column", column=..., available=...` (shared/query/validate.py:47-49) do carry slugs.

But that is the stated contract, not an oversight. The module docstring at errors.py:1-17 documents the envelope explicitly — "Consumers can branch on the machine-readable `code`" — with the worked example body showing `"detail": "Dataset not found: abc", "code": "not_found"`. `ProblemException`'s docstring (errors.py:61-66) says to raise it "where a machine-readable error contract matters", i.e. slugs are a deliberate opt-in for cases carrying structured extras (`sheets=[...]`, `available=[...]`, `version_number=...`), not a capability the domain forgot to apply.

The behaviour is pinned by passing tests that assert exactly the shape the claim calls a defect: `tests/test_discovery.py:249` `assert r.json()["code"] == "not_found"` for PATCH sheet-metadata with no record, and `tests/test_column_metadata.py:208` the same for PATCH column metadata. These are not incidental — the surrounding test names are `test_sheet_metadata_patch_requires_existing_record` / `test_column_metadata_patch_requires_existing_entry`.

Two of the claim's five cases also fail on their own terms. (1) "Dataset not found" is deliberately overloaded: `app/features/auth/deps.py:137-144` returns 404 both when the dataset is absent and when a non-member lacks access — "404 is returned for datasets in teams the caller can't see, so we never leak the existence of other teams' datasets". Giving that case a distinguishing slug would undo a documented security property; the test at tests/test_discovery.py:255 even comments "Unknown sheet key: same 404, no existence leak." (2) The claim's contrast with "unknown-column" is misleading: that error is a **400**, not a 404 (tests/test_column_metadata.py:219 `assert r.status_code == 400`), so it is already separable from every 404 by status alone without any slug.

The residual gap is narrow — on one PATCH the client cannot tell "sheet has no metadata row yet" from "sheet key does not exist" — and both recoveries collapse to re-reading `GET /datasets/{id}/sheet-metadata`, which the UI can do unconditionally. No wrong data is returned; the correct status and a human-readable `detail` are always present.

## [low] The catalog signals are deliberately re-expressed in SQL, documented as such, and pinned to the health evaluators by an explicit equality test.

- **domain:** ?
- **where:** 

**Evidence**

The claim's "what" (two implementations) is factually true, but every claimed consequence fails against the source.

1. Documentation is not "duplicated with drift risk" — it is a clause-for-clause transcription, and I diffed it. health.py:235-244: `sheets_full = total_sheets > 0 and documented_sheets >= total_sheets`; `columns_full = column_coverage is None or column_coverage >= DOC_COLUMN_COVERAGE`; full = desc AND domain AND sheets_full AND columns_full; none = not desc AND not domain AND documented_sheets == 0 AND documented_columns == 0. repo.py:73-79: `WHEN x.has_desc AND x.has_dom AND x.ts > 0 AND x.ds >= x.ts AND (x.tc = 0 OR x.dc::float / NULLIF(x.tc, 0) >= {DOC_COLUMN_COVERAGE}) THEN 'full' WHEN NOT x.has_desc AND NOT x.has_dom AND x.ds = 0 AND x.dc = 0 THEN 'none' ELSE 'partial'`. The four counters ts/ds/dc/tc (repo.py:103-118) are the same subqueries, retired_at filter included, as `documentation_stats` (repo.py:375-394). There is no semantic gap to diverge into.

2. The equality is pinned, not hoped for. tests/test_catalog_facets.py:147 `test_catalog_documentation_matches_health` builds five datasets across the threshold matrix, reads `/datasets/{id}/health` -> evidence.bucket and the catalog list `documentation`, and asserts `catalog_bucket == health_bucket` (line 177) plus the expected labels including the 0.5 coverage boundary (lines 179-181). Its docstring states exactly why it exists: "The facet SQL re-expresses evaluate_documentation in SQL rather than calling it, so this pins the two implementations together". Four more tests (lines 57, 68, 91, 103, 111) pin the SQL side and tests/unit/test_health_evaluators.py pins the Python side, so "the only guard" is also inaccurate.

3. The duplication is a documented, deliberate architectural fact, not an oversight. repo.py:60-64: "Every signal is derived from the SAME sources the §17 health dimensions use ... so the catalog and /datasets/{id}/health can never disagree." HANDOFF.md:751-758: "catalog signals and the §17 health dimensions MUST come from one source — signals_lateral() computes the three catalog buckets from the same tables the evaluate_* health functions read, and reuses DOC_COLUMN_COVERAGE from health.py (imported lazily to dodge the health<->repo cycle) ... test_catalog_facets.py double-checks catalog == health." The reason it is SQL is structural: the same lateral must serve *filtering* (data_accelerator/repo.py:232-240 `sig.validation_status = :vstatus` etc.) in both the COUNT and the row query (lines 243-246, 264) — a Python evaluator cannot filter/paginate in the database.

4. "Validation can disagree": the two expose different vocabularies on purpose. SQL yields passed/failed/none with "error-level failures => failed" (repo.py:55-56, 83-89); evaluate_validation (health.py:96) yields attention/warning/ok, where warning-level-only failures are WARNING but still catalog-"passed". Both read the same row via the same `ORDER BY started_at DESC LIMIT 1` on completed runs for the current version (quality/repo.py:263-266). That is a documented mapping, not a contradiction.

5. "Unbounded": `facets` (repo.py:146-150) does scan all team-scoped datasets, but a facet *count* is by definition an aggregate over the whole catalog; there is no correct bounded version. The list endpoint applies the same lateral before LIMIT because it filters on it. No wrong answer either way.

The only genuine divergence I could find is one the claim does not make: the SQL drops NULL fingerprints (`WHERE s.schema_fingerprint IS NOT NULL`, repo.py:98) and then tests `COUNT(DISTINCT fp) > 1`, while evaluate_schema_stability (health.py:70-71) compares adjacent rows and skips a pair if either side is NULL. A sequence A, NULL, B on one logical sheet would give catalog has_schema_drift=true but health schema_stability=ok. Every writer of dataset_version_sheets sets a fingerprint (files/services/processing.py:90,109; files/services/replace.py:101; library/service.py:307), so this is a theoretical hole, not the claimed defect.

## [low] POST /joins/execute really does gate on DATASET_READ while writing a join_output artifact and a run row — but that split is the documented design.

- **domain:** ?
- **where:** 

**Evidence**

The factual half of the claim is accurate. app/features/relationships/api.py:216-232 — `execute_join` calls `_authorized_relationship(spec.relationship_id, principal, Permission.DATASET_READ)` (same permission as `/joins/preview` at :204-213) and then `joins.execute_join(...)`, which materialises a join_output artifact and a run row. The sibling at api.py:234-259 does `ensure_dataset_permission(target_id, Permission.DATASET_WRITE)` for publish. pipeline.py:351-357 does say "action='execute' CHANGES STATE".

But this is an explicit, written design decision, not an oversight. WAVE4-6-PLAN.md:216 states verbatim: "RBAC: READ both sides for preview/execute; WRITE target for publish." ARCHITECTURE.md:430-441 describes the same three endpoints and says the safety property for joins is the confirmed-relationship gate plus independent permission checks on both dataset ids ("Only a `confirmed` relationship may drive a join (409 relationship-not-confirmed). That constraint is what makes cross-dataset joining safe to expose"), not a write check. `_authorized_relationship`'s docstring (api.py:186-192) documents that the second side is deliberately only checked for READ, because the concern being defended against is a cross-team read side channel.

The coherent reading is: execute produces a scratch derived artifact under the caller's own run, visible only to their team; nothing in the durable dataset plane changes until publish, which is correctly WRITE-gated on the target dataset. tests/test_join_builder.py:251-266 asserts the cross-team property (preview, execute and publish are all 404 for an outsider); there is no test asserting a viewer is refused execute, consistent with READ being intended.

The MCP-side `guard` (app/features/mcp/tools/_common.py:82) is an error-rendering decorator, not an RBAC layer, so the MCP description wording carries no enforcement contract that the API contradicts.

## [low] The 80-char cell cap and pipe-to-slash escaping in render.scalar are a documented, tested token-economy choice of the model-facing MCP surface, not a UI data path.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics are accurately described but the framing is wrong.

1. The truncation and pipe rewrite exist exactly as claimed — /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/render.py:24-26 `text = text.replace("\n", " ").replace("|", "/")` then `text = text[: limit - 1] + "…"`, applied per cell at render.py:51. read_artifact (app/features/mcp/tools/artifacts.py:151 `render.table(rows, columns)`) and query_rows (app/features/mcp/tools/look.py:122) do route real data rows through it.

2. It is deliberate and documented at the module level. render.py:1-6: "Every tool returns text rather than JSON. For the same content a pipe-delimited table costs roughly half the tokens of pretty-printed JSON, and the point of this tool surface is to spend as few tokens as possible describing data." The `|` -> `/` rewrite is not corruption, it is the escaping that makes a pipe-delimited format parseable at all; the same line strips newlines for the same reason.

3. The author explicitly reasoned about where truncation is and is not acceptable, and opted out where it would hurt. render.py:31-35 on `fields`: "Not truncated: these carry prose like health summaries, where the tail is usually the actionable part." That opt-out is pinned by a test: tests/unit/test_mcp_orient.py:404 `test_a_long_health_summary_is_never_truncated` — "This read-out is rendered as lines precisely so it escapes the 80-character cell limit that applies to every table in this layer", asserting `"…" not in out`.

4. Multiple other tests treat the 80-char cap as a known, designed-around property rather than a defect: tests/unit/test_mcp_context.py:309 (timestamps trimmed to the second "in a table cell capped at 80"), :518 (audit paths elided so the verb survives the cell), :584 `test_a_lineage_event_points_at_get_lineage_for_the_untruncated_ids` — "Cells are capped at 80 characters, so a `published_to` row's child dataset id can arrive cut in half... say where the whole id lives instead." So the codebase already recognises and remediates the one real hazard (retyping a truncated identifier).

5. The "impact_on_ui" premise contradicts the documented architecture. ARCHITECTURE.md:497-499 says /api/v1/mcp is "Not a REST route and deliberately absent from the OpenAPI document — it is JSON-RPC", and ARCHITECTURE.md:509-511 says the tools call the service's own REST routes in-process. MCP is a model-facing surface; the UI is expected to use REST. "A UI has to bypass MCP and call the REST route" is the intended design, not a defect.

6. The claim's "no note anywhere that a value was altered" is also weak on its own terms: a truncated cell ends in U+2026, which is the marker, and the response-level `clamp` (app/features/mcp/tools/_common.py:77) separately announces "[response truncated at 60,000 characters." The claim's only genuinely unsignalled loss is `|` -> `/`, which is format escaping.

No test asserts data cells are returned verbatim; several assert the opposite convention. Nothing here is a silent wrong answer — the values are visibly elided, in a surface whose stated purpose is token-minimal text for a model.

## [low] explain() has no 409 status branch, but the code+detail fallback is a deliberate, test-pinned contract and the claim's "two rescued, two not" inconsistency is false.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is true: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/_common.py:176-212 branches on 401/403/404/422 and 400+bad_request only, so any 409 whose `code` is not one of the ten handled codes hits `return f"{exc.detail} (code: {code})"` (_common.py:212). And 409s do reach it: app/features/relationships/joins.py:89-97 `require_confirmed` raises ProblemException(409, "This relationship has not been confirmed — only confirmed relationships can drive a join", code="relationship-not-confirmed"); app/features/library/service.py:270-271 and app/features/quality/api.py:71 raise plain HTTPException(409, ...) which app/api/errors.py:54-55,104 stamps as code="conflict".

But the framing is wrong on two counts.

(1) The fallback is deliberate and pinned by a test that asserts the exact string the claim calls a defect. tests/unit/test_mcp_common.py:483-489: "The fallback's job is to not swallow the one machine-readable thing the response carried... `assert out == "This dataset is locked by a running job (code: dataset-locked)"`" — and that test's fixture is itself a 409. tests/unit/test_mcp_pipeline.py:991-1013 pins the very case the claim names as the worst one: `test_executing_on_an_unconfirmed_edge_surfaces_the_service_refusal` posts the 409 relationship-not-confirmed problem and asserts "has not been confirmed", "only confirmed relationships can drive a join" and "relationship-not-confirmed" all survive. A passing test asserts the current output is the intended contract. Same for publish_result's collision: tests/unit/test_mcp_pipeline.py:1841-1856 asserts only that the service detail naming the taken name survives.

(2) "Two of those are individually rescued" is factually false. Of the four 409s listed, exactly one has a hand-written rescue — run_quality_check's non-ready version at curate.py:653-657. The other cited rescue, curate.py:913-919, is `manage_tags` ROLLBACK ("Rollback replays the tag's own history..."), which is not one of the four. The local handlers exist precisely where the service detail is ambiguous on its own, which the tests state: tests/unit/test_mcp_curate.py:862 "A 409 is about version STATUS, not about the rules. Without the pointer..." and :1324 "The 409 alone reads like the gate rejecting the rollback". That is a rule (add local text only when the detail under-determines the fix), not an inconsistency.

(3) The specific UI grievance — "no instruction to call manage_relationships action='confirm'" — is refuted. That instruction is delivered proactively where the unconfirmed edge is produced: app/features/mcp/tools/pipeline.py:267-271 "Nothing here can drive a join yet. Confirm the ones that are real with action='confirm'..." (pinned at tests/unit/test_mcp_pipeline.py:424-436), app/features/mcp/tools/context.py:757-759, and join_datasets' own description at pipeline.py:354-356 states the precondition. The 409 detail itself names the precondition verbatim and the code slug is `relationship-not-confirmed`, not an opaque `conflict`.

## [low] describe_dataset does cap versions at 10 without a count note, but the visible monotonic version numbers make the omission self-disclosing, so no wrong answer follows.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical half of the claim is accurate. app/features/mcp/tools/orient.py:152-169 builds the version rows from `page_items(versions_page)[:10]` and renders `render.section("Versions (newest first)", render.table(versions))` with no `render.count_note(...)`, unlike whoami (orient.py:33-36, "…and N more") and search_datasets (orient.py:110-113, count_note). GET /datasets/{id}/versions is unpaginated — app/features/data_accelerator/api.py:250-255 returns `_collection(...)`, and _collection (api.py:76-78) sets `total=len(items)` — so for 40 versions the tool really does hold total=40 and discard it.

The harm half is refuted. Version numbers auto-increment from 1: app/features/files/repo.py:65-72, `SELECT COALESCE(MAX(version_number), 0)` then `ver_num = prev + 1`, and repo.list_versions orders `ORDER BY dv.version_number DESC` (app/features/data_accelerator/repo.py:448). The rendered table therefore reads "40, 39, … 31" in its first column. The oldest row cannot "read as the dataset's first version" — it is literally labelled 31, with 1..30 conspicuously absent. The information the count note would add (that there are older versions) is already on the face of the output.

The cap itself is deliberate and already pinned with a rationale by the repo's own test, tests/unit/test_mcp_orient.py:203-217: "Versions are immutable, so a busy dataset accumulates them forever and an uncapped table would dominate the response to a question about *columns*. The cap is only safe while the order is newest-first…" — and the test asserts exactly ["15"…"6"] for a 15-version fixture.

The specific scenario cited, "which version introduced this column", is not answerable from describe_dataset at any list length: the sheets/columns block (orient.py:127, 131-150) comes from GET /datasets/{id}/sheets, which is current-version-only, and the version table carries no column data. That question routes to the diff endpoint (app/features/data_accelerator/api.py:267-270), not here.

Residual: a one-token consistency gap (count_note is used in the two sibling tools and not here). Worth adding for uniformity; it is not a silent-wrong-answer defect.

## [low] check_quality fetches 10 duplicate groups but renders only pre-cap totals, so the example rows are computed and discarded — wasted work, not a wrong answer.

- **domain:** ?
- **where:** 

**Evidence**

The mechanics of the claim check out, but the "bug" framing does not.

Mechanics confirmed:
- /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/compute.py:333-337 does pass `limit=10` to the duplicates route, and the render block at compute.py:373-380 uses only `columns`, `exact`, `group_count`, `duplicate_rows` — never `groups` or `truncated`.
- The totals are genuinely independent of `limit`: app/features/explorer/data_quality.py:46-51 `duplicate_totals_sql` — docstring "(group_count, duplicate_rows) over ALL duplicate groups, uncapped" — and it is called at data_quality.py:133-134 with no limit argument. `limit` only reaches `duplicate_groups_sql` at line 136, and each returned group then triggers a separate `group_examples_sql(..., EXAMPLES_PER_GROUP)` query (data_quality.py:140-143, EXAMPLES_PER_GROUP = 5).
- So the real waste per call is up to 10 extra DuckDB `SELECT * ... LIMIT 5` queries against an already-loaded in-memory relation, plus <=50 rows serialised over the in-process ASGI transport (app/features/mcp/client.py: "no socket, no TLS and no network hop") and discarded. That is small next to `load_data(sheet_data_path(...))` (data_quality.py:130), which the request pays for regardless.
- `limit=0` is indeed impossible: app/features/explorer/api.py:266,282 declare `limit: int = Query(default=25, ge=1, le=MAX_DUPLICATE_GROUPS)`.

Why this is not a defect:
- Output is byte-identical whichever limit is used; no value the tool renders changes. Nothing is wrong, slow in any user-visible way, or misleading.
- The bound is a deliberate, tested decision. tests/unit/test_mcp_compute.py:871-886 asserts exactly `params == {"limit": 10}` with the rationale "both list reads are bounded at the request. An uncapped duplicates or validations fetch would put an arbitrary amount of data into the model's context for a summary that shows counts only." The intent was to cap below the route default of 25, and it does. The claim reads a chosen conservative cap as an oversight.
- The tool description at compute.py:300-301 ("reports counts without naming the offending rows") is consistent with rendering counts only; it does not imply the fetch value is a mistake.

At most this is a micro-optimisation (limit=1 would save ~9 tiny queries); changing it would require editing a passing test that encodes the current value on purpose.

## [low] The lowercase/exact split is a documented rule — locally-consumed dispatch verbs are normalized, service-forwarded params mirror the service's Literal exactly.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical facts are accurate: `_require` at app/features/mcp/tools/pipeline.py:80 does `normalized = str(action).strip().lower()`, while :385 `if how not in {"inner", "left"}` and :726 `if mode not in {"new_dataset", "new_version"}` are exact. But the claim's conclusion — "a model cannot learn one rule from this surface" — is contradicted by a rule the repo states and tests.

The rule is: a parameter that is *forwarded to the service* is validated exactly as the service types it; a parameter this process *consumes locally* is normalized. `how` and `mode` are forwarded verbatim (pipeline.py:387 `spec = {... "how": how ...}`, :731 `body = {"mode": mode, "name": name}`) into models typed `how: Literal["inner","left"]` (app/features/relationships/schemas.py:72) and `mode: Literal["new_dataset","new_version"]` (app/features/library/schemas.py:83, app/features/transform/schemas.py:99). `action`/`source` never leave the process — the return of `_require` is used only for branching (`if verb == "preview"`, `if kind == "join"` picking the path); no request body carries them. So lowercasing them cannot create a second contract, whereas lowercasing `how` would.

That rationale is written down verbatim in app/features/mcp/tools/_common.py:55-59: "Deliberately does *not* lowercase. The aggregate and pivot request models are ``Literal["asc","desc"]``, so ``"ASC"`` is a 422 there; accepting it here would give one parameter name two contracts across one tool surface, and quietly rewriting a caller's input is the same class of behaviour this guard exists to prevent." _common.py:4-11 states the same principle for the whole module.

Two existing tests assert the strict behaviour *with the uppercase case as an explicit parametrization*, i.e. the opposite of "should be resolved the same way in both places":
- tests/unit/test_mcp_pipeline.py:1016 `@pytest.mark.parametrize("how", ["outer", "INNER", "cross", ""])`, docstring: "Note the check is exact: 'INNER' is rejected rather than quietly rewritten, because a tool that rewrites its inputs is the behaviour this surface avoids."
- tests/unit/test_mcp_pipeline.py:1823 `@pytest.mark.parametrize("mode", ["overwrite", "replace", "NEW_DATASET"])`, docstring: "The check is exact, so an uppercase mode is refused rather than rewritten."

app/features/mcp/tools/context.py:719 (`status not in {"suggested","confirmed","rejected"}`, also forwarded) follows the same rule, so the pattern is consistent across the codebase, not a two-site accident. Both paths raise ToolError before any client call (`assert client.calls == []`), so nothing is silently wrong.

## [low] MCP does not require row_count_min's "min", matching the REST contract; the rule then means "at least 1 row", which is a real check, not a no-op.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is accurate but its characterization is not.

1. No MCP-level check exists. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/curate.py:526-543 guards only accepted_values, foreign_key, range and regex_match; `params = parameters or {}` (curate.py:515) and the body is POSTed with `"parameters": params` (curate.py:551). True.

2. The REST layer deliberately accepts it, and tests lock that in. app/features/quality/schemas.py:46-58 `_check_selectors` validates selectors plus only foreign_key and accepted_values parameters — row_count_min, range and regex_match parameters are unvalidated at the API. Three existing integration tests POST exactly the claimed payload and assert success:
   tests/test_quality.py:144 `{"name": "min-rows", "rule_type": "row_count_min", "sheet_selector": "orders"}` … `assert r.status_code == 201`
   same at tests/test_quality.py:202 and :216.
   So the claim's "expected: a refusal" would make the MCP tool contradict a contract the suite asserts. (MCP is stricter than REST for range/regex for a stated reason — curate.py:539-541 "otherwise the rule errors at validation time" — which is exactly what does NOT happen for row_count_min.)

3. "Enforces nothing" is false. app/features/quality/engine.py:132-142: `minimum = int(params.get("min", 1))` … `if count >= minimum: passed` else `failed` with `message=f"Sheet has {count} rows, minimum is {minimum}"`. A sheet with 0 rows FAILS. The rule degrades to a non-empty-sheet check, which is a meaningful gate, not a vacuous pass. The engine also never errors on the missing key, so nothing is "silently wrong" — the run message states the minimum it used.

4. Minor real weakness (not the claimed one): render.fields (app/features/mcp/render.py:36) skips values in `(None, "", [], {})`, so the confirmation's "Now stored" block omits `parameters` entirely when it is `{}` — the caller is not shown that no minimum was recorded. That is a small echo/UX gap, not a wrong result.

## [low] relationship-not-confirmed takes explain()'s deliberate generic fallback, like ~29 of the service's ~40 problem codes; detail + machine code both survive.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical part of the claim is true but trivial: `explain()` (app/features/mcp/tools/_common.py:102-212) has no `relationship-not-confirmed` branch, so it returns `f"{exc.detail} (code: {code})"` at :212. Everything the claim builds on top of that is wrong.

1. "Every other translated code in explain() names the next step" is false. `sheet-not-in-version` -> "That sheet does not exist in this version of the dataset." (_common.py:173-174) names no next step. `operator-type-mismatch` (:140-145) names no tool or action. `sheet-selection-required` (:106-109) only appends sheet names. The 401/403/404 branches (:176-193) name no next step either.

2. The "translated codes" are a small minority, not the norm. Grepping `code="..."` across app/ yields ~40 distinct codes (ambiguous-diff-key, invalid-relationship-transition, sensitive-data-restricted, diff-key-required, ...); explain() branches on 11 of them. The generic line at :212 IS the designed path for the rest, and it preserves both the human detail and the machine code. ARCHITECTURE.md:127-131 documents exactly that contract: "All errors are application/problem+json with a machine-readable `code` ... so a UI can branch on the code rather than parse prose."

3. The dropped "actionable" content is already present twice over. The service's own detail is "This relationship has not been confirmed — only confirmed relationships can drive a join" (app/features/relationships/joins.py:92-97), which states the precondition. And `manage_relationships`' tool description already tells the model the next step before it ever errors: "only a CONFIRMED relationship may drive a join, so a suggested edge must be confirmed first" (pipeline.py:194-197), plus "Confirm the ones that are real with action='confirm'" (:267-269) and "call action='confirm' with this relationship_id first" (:311-313).

4. The only genuinely absent datum is `current_status` (suggested vs rejected), and the claim overstates its stakes: per app/features/relationships/service.py:314-318 `rejected -> confirmed` is a legal transition, so "confirm it" is the correct next step in BOTH cases — the guidance would not differ.

5. The existing test asserts the current behaviour deliberately, not the claim's: tests/unit/test_mcp_pipeline.py:991-1013, docstring "The detail must survive the translation intact, because it is the only text that names the precondition", asserting detail and code both present.

No wrong data, no wrong status code, no missing information the caller cannot act on — this is an enhancement request phrased as a defect.

## [low] list_relationships does short-circuit on relationship_id before validating status, but that mirrors the surface's house pattern and cannot mislead the caller.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical fact in the claim is correct. app/features/mcp/tools/context.py:684 `if relationship_id:` returns the detail render at :690-715, and the status vocabulary check only runs afterwards at :717-722 (`if status is not None: ... raise ToolError(f"status must be suggested, confirmed or rejected, got {status!r}.")`). So `list_relationships(dataset_id=..., relationship_id="rel-1", status="verified")` returns the edge with no error.

But the framing as a defect does not survive the source:

1. It is the house pattern, not a one-off slip. The sibling tool in the same file does exactly the same thing: `list_saved_objects` validates `kind` first because the detail branch *needs* it (context.py:805-813, including the deliberate `raise ToolError("object_id needs kind — ...")` for the argument that genuinely is required), then returns the single-object detail at :832-847, and only calls `require_sort_order(sort_order, ...)` at :850 — after the branch. `list_saved_objects(kind="view", object_id="v1", sort_order="sideways")` is ignored identically. Args are validated in the branch that uses them; args meaningless to the detail branch are not.

2. The parameter's own description declares the branch semantics: context.py:679 `Field(description="Show one edge in full, with its evidence, instead of the list.")`, and the tool description at :659 says "Pass relationship_id for the evidence ... behind one edge." "Instead of the list" is precisely the statement that list-shaping arguments do not apply.

3. The appeal to UnknownArgumentGuard is a category error. app/features/mcp/strict_args.py:28-30 explicitly scopes it: "**Types, required-ness, or values.** Still the SDK's arg model. This guard only answers 'is this key declared at all'." `status` is a declared key with a bad *value* — outside the guard's stated remit by design. The harm the guard exists to stop is stated at strict_args.py:11-14 / :100-102: "the answer you got back would be to a different question than the one you asked". Here the answer is exactly the question `relationship_id` asked.

4. There is no misreadable output. The detail render prints `("status", edge.get("status"))` at context.py:699 and then states the consequence in words at :711-714 ("This edge is confirmed, so it can drive a join." / "This edge is 'suggested', so it cannot drive a join"), pinned by tests/unit/test_mcp_context.py:1103-1124. A caller who imagined they had filtered to confirmed edges is told the edge's actual status on the same screen.

The list-path validation the claim contrasts against is itself already pinned: tests/unit/test_mcp_context.py:930 `await tools["list_relationships"](dataset_id="ds-1", status="verified")` inside a raises block, and :942 for case/whitespace normalisation — both list-mode, consistent with the branch-scoped design.


# REFUTED

## [low] Claim's mechanism is real (no ORDER BY tiebreaker) but its premise is false: sequential awaited POSTs cannot share a microsecond started_at.

- **domain:** ?
- **where:** 

**Evidence**

The only literally-true part of the claim is the SQL text. app/features/quality/repo.py:216-221: `ORDER BY started_at DESC LIMIT :limit OFFSET :offset` — no tiebreaker. Everything the claim builds on that is false.

1. started_at cannot tie on this path. app/infra/db/postgres/migrations/20260804030000_quality.sql:58 declares `started_at TIMESTAMPTZ NOT NULL DEFAULT now()` — microsecond resolution, and `now()` is transaction_timestamp(). repo.py:128-140 `create_run` opens its own `async_session_factory()` session, INSERTs, and commits; it is not sharing a transaction with anything else, so two runs would have to BEGIN inside the same microsecond to collide.

2. The runs are not "created back to back" in any sense that could collide. app/features/quality/api.py:118-159 does the whole thing synchronously inside the request: create_job -> start_job -> create_run (line 137) -> read every sheet schema from the version (line 140) -> evaluate every rule (144) -> register failure artifacts (148) -> complete_run (149) -> complete_job. Only after all of that does the response return.

3. So the cited test is not a latent flake. tests/test_quality.py:219-223 is `for _ in range(3): await client.post(.../validate)` — each POST is awaited to completion before the next is issued, so run N's entire validation pipeline (including its complete_run commit) has finished before run N+1's create_run transaction even starts. The gap is milliseconds of workbook I/O and rule evaluation, not microseconds. The assertion at line 228 (`== list(reversed(run_ids))`) is a genuine guarantee on this code path, not luck.

The residual is a theoretical microsecond tie between two genuinely concurrent POSTs on separate connections, where the two runs started simultaneously and there is no factually "newer" one to get wrong. That is display-order ambiguity between indistinguishable rows, not a wrong answer.

## [low] Join execute returns `sample_file`, which is the codebase's canonical artifact handle (GET /samples/{filename}), so the output is reachable without any name reverse-engineering.

- **domain:** ?
- **where:** 

**Evidence**

The only literally-true part is the schema shape: `JoinExecuteResponse` (app/features/relationships/schemas.py:114-122) carries `run_id, sample_file, row_count, warnings, output_columns, relationship` and no `definition_id`/`artifact_id`, and app/features/library/api.py has no `GET .../analytics/runs/{run_id}` (only :124 list_runs and :139 publish). Everything the claim builds on top of that is false.

(1) "cannot link to the artifact without reverse-engineering an internal naming scheme" — false. `sample_file` IS the artifact handle everywhere in this service. app/features/files/api.py:751 `GET /samples/{filename}` (download), :758 `GET /samples/{filename}/data` (paged/filtered/sorted rows), :796 `POST /samples/{filename}/export`, each gated by `_authorize_sample_access(principal, filename)`. app/features/data_accelerator/schemas.py:616 documents this contract verbatim: "Saved sample filename — fetch via GET /api/v1/samples/{filename}". The MCP wrapper says the same thing (app/features/mcp/tools/pipeline.py:435: "The full result is the artifact named by sample_file — read it with read_artifact"). A raw `artifact_id` is not the addressing scheme here; the filename is.

(2) "cannot poll the run, show its status" — false, there is nothing to poll. `execute_join` (app/features/relationships/joins.py:254-340) is fully synchronous: it materializes the table, writes the blob, calls `library_repo.complete_run(run["id"], result_summary=summary, artifact_id=artifact["id"])` and `jobs.complete_job(...)` before returning. Any failure raises out of the request (the guarded blocks call `fail_run`/`fail_job` then re-raise). By the time the UI holds `run_id`, the run is terminal-completed by construction.

(3) "the definition id is only obtainable by ... matching the internal name convention `join:{relationship_id[:8]}:{how}`" — false. `library_repo.list_definitions` (app/features/library/repo.py:40-47) is an unfiltered `SELECT ... WHERE dataset_id = :did`, so the join definition is returned by `GET /datasets/{ds}/analytics`, and `DefinitionOut` (app/features/library/schemas.py:47-59) exposes `kind` and `params`. joins.py:242-248 stores `"kind": "join"` and `"params": {"relationship_id": ..., "how": ...}`. Matching `kind == "join" and params.relationship_id == rel_id` is a structured, documented lookup — not the name convention.

(4) Deliberate surface: ARCHITECTURE.md:429-431 declares the join surface as exactly `POST /joins/preview`, `POST /joins/execute`, `POST /joins/{run_id}/publish`. HANDOFF.md:249 explains the one-definition-per-(relationship, how) reuse, matching the docstring at joins.py:229-233.

Residual, and it is cosmetic: `analytics` runs have no single-run GET while `transformations` do (app/features/transform/api.py:153), and echoing `definition_id`/`artifact_id` in the execute response would save one list call. That is an ergonomic asymmetry, not "a run_id it cannot resolve to anything".

## [low] GET /datasets/{id} does not read the whole parquet (lazy DuckDB view) and the leaked file_path is useless to non-superusers.

- **domain:** ?
- **where:** 

**Evidence**

Both halves of the claim fail against the source.

(1) "loads the entire version parquet on every request" is false. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/data_accelerator/services/datasets.py:74-77 calls `load_data(file_path=...)` then `extract_metadata`. `load_data` (app/shared/data_io.py:69-95) documents and implements exactly the opposite: "CSV and Parquet files are loaded as views (lazy — DuckDB reads only what queries touch; ``s3://`` URIs stream via httpfs)" — it executes `CREATE VIEW df AS SELECT * FROM read_parquet(...)`, materializing a table only for .xlsx/.xls and inline JSON. Ingest converts uploads to canonical parquet (app/features/files/services/processing.py:234 `file_path=parquet_path`, `complete_version(path=parquet_path...)`), so the stored version path is parquet, not Excel. `extract_metadata` (app/shared/data_io.py:532-541) runs only `SELECT COUNT(*) FROM df` (parquet footer metadata in DuckDB), `DESCRIBE df` (schema metadata), and `SELECT * FROM df LIMIT 5` (one row group). No full-file scan; cost does not grow with dataset size the way the claim asserts. s3:// paths take `_load_s3`, which is also a view (data_io.py:60-66).

(2) The privilege framing is false. The route (app/features/data_accelerator/api.py:210-215) enforces `ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)` before returning the response, so `file_path` goes only to callers already authorized to read that dataset's data. And the `file_path` analytics source is hard-gated: app/features/data_accelerator/api.py:105-108 — `if getattr(request, "file_path", None) and not principal.is_superuser: raise HTTPException(403, "file_path sources are restricted to platform administrators")`, with the docstring explaining why ("reads straight from the server filesystem with no team scoping"). A passing test asserts exactly this: tests/test_api.py:165-173 `test_file_path_source_requires_superuser` posts `{"file_path": "/etc/hostname", ...}` to /api/v1/sample as a non-superuser and asserts 403. Knowing the path therefore buys a reader nothing; the claim's "precisely the identifier the RBAC design tries to keep out of the request surface" is backwards — RBAC blocks the *input*, and blocking it is what makes the *output* harmless.

What remains true is only the narrow fact that `DatasetMetadataResponse.file_path` (app/features/data_accelerator/schemas.py:276-277) surfaces an internal storage path to authorized dataset readers — cosmetic internal-layout disclosure, not the escalation or performance defect described.

## [low] Name collision is real but DuckDB silently dedupes to `total_amt_1` — no DuckDB error, no 500, and an include_row_totals test does exist.

- **domain:** ?
- **where:** 

**Evidence**

The claim has two load-bearing assertions and both are false.

(1) "fails with an unhandled DuckDB error surfaced as a 500". DuckDB does not reject duplicate output column names in CREATE TABLE AS; it de-duplicates by suffixing. Reproduced with the repo's own interpreter (/home/saketh/Projects/playground/work/demo/apps/analytics-service/venv/bin/python, duckdb 1.5.3) using the exact statement shapes from pivot.py:207-214 and :225-228 — widening on a pivot dim whose values include the literal `total`, then LEFT JOIN of `_pivot_row_totals` producing another `total_s`:
  DESCRIBE => [region, total_s, total_m, x_s, x_m, total_s_1, total_m_1]
  rows      => ('E',5,1,None,None,5,1), ('W',None,None,3,2,3,2)
No exception is raised, values are all correct, and the row-total columns simply land as `total_s_1`/`total_m_1`. Same for the "pivot value equals a row-dimension name" variant (`SELECT 1 AS x, 2 AS x` => x, x_1).

Consequently `result_cols = [r[0] for r in conn.execute("DESCRIBE _pivot_out")...]` (pivot.py:277) and the returned `data` dicts (pivot.py:289-291) both report the *actual* de-duplicated names, so the response stays self-consistent. The internal `cell_names` list (pivot.py:229) is only used for the `column_totals` membership check at pivot.py:273, which is unaffected.

(2) "There is no include_row_totals test at all." False. /home/saketh/Projects/playground/work/demo/apps/analytics-service/tests/test_pivot.py:79-93, `test_totals_reaggregate_non_additive_mean`, posts `"include_row_totals": True, "include_column_totals": True` and asserts `eu["total_avg_amt"] == 150.0`. The path is exercised.

What remains true: the two namespaces (`_cell_name` at pivot.py:54-57 and `f"total_{a}"` at pivot.py:229) can produce the same string. The residual effect is a cosmetic/labelling one — a client that hardcodes `row["total_amt"]` instead of reading `columns` would read the pivot cell for the value "total" instead of the row total. That is a narrow, self-disclosing naming wart, not the 500 the claim describes, and it requires multiple value specs plus a categorical literally equal to "total".

## [low] Bare-400 -> code "bad_request" is the codebase-wide norm for detail-only client errors, not an inconsistency unique to `include`.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical half of the claim is true but trivial: `app/features/data_accelerator/api.py:258-264` raises `HTTPException(400, f"Unknown include section(s): ...")`, and `app/api/errors.py:104` (`"code": code or _code_for(status)`) plus `_code_for` (errors.py:54-55) renders that as `"bad_request"`.

The load-bearing half of the claim — "every other machine-readable rejection in this domain carries a slug" — is factually false. Bare, slugless `HTTPException(400, ...)` is the dominant pattern in this exact domain: `api.py:377` and `api.py:410` ("Provide either version_id or version_number"), `services/aggregation.py:331` ("join requires a dataset_id source"), `:343`/`:345` ("Join column not found on ..."), `services/sampling.py:239,263,268,276,284,293` ("unknown method '{method}'", "stratify_column required", ...), `:359`, `:837`, `:840`, `services/methods.py:35,145,323,379`. The slugged errors (`profile-required` diffs.py:268, `sheet-selection-required` rowdiff.py:338, `unknown-column` rowdiff.py:227, `invalid-sort-order` aggregation.py:229, `rename-not-candidate` sheet_identity.py:78, `ambiguous-diff-key` rowdiff.py:258) are the deliberate minority — the ones that carry *extra body fields* the client needs to recover (`sheets=[...]`, `available=[...]`, `missing=[...]`). `ProblemException.__doc__` (errors.py:59-66) states the rule: "Raise where a machine-readable error contract matters". An unknown `include` section carries no recovery payload; the only valid value is the literal `profile` documented in the `Query(description=...)`.

The generic bucket is an explicit, consumed contract, not a fallthrough: `app/features/mcp/tools/_common.py:209` — `if exc.status == 400 and code == "bad_request": return exc.detail` — the MCP layer deliberately treats `bad_request` as "the detail is the whole message".

No test contradicts or supports a slug here: `tests/test_profiling_runs.py:181-183` asserts only `status_code == 400` for `params={"include": "nonsense"}`, deliberately not pinning a `code`.

Impact is also overstated: `include` is a static client-side literal (the only accepted value is `profile`), so a UI never branches on this at runtime — it is a developer typo caught in dev, not a user-recoverable state.

## [low] compared_columns is physical, but posting those names back is accepted, so the claimed 400 round-trip failure does not occur.

- **domain:** ?
- **where:** 

**Evidence**

The claim's factual half is true but inert; its stated consequence is false.

1) compared_columns really is physical. app/features/data_accelerator/services/rowdiff.py:218-219 builds `left_cols`/`right_cols` from `c["name"]` (the PHYSICAL parquet name — app/shared/data_io.py:305 sets `"name": physical[i]`, `"normalized_name": normalized[i]`), and :232 `common = sorted((left_cols & right_cols) - set(phys_keys))` is returned verbatim at :309 as `"compared_columns": common`. `"key": keys` at :309 is the caller's / metadata names, unmapped. So yes, the two fields can be in different namespaces.

2) The claimed failure — "posts those names back as columns ... may get a 400 unknown-column" — is refuted by `_physical_key` itself (app/features/data_accelerator/services/sampling.py:573-580):

    for c in sheet_row.get("schema_json") or []:
        if c.get("normalized_name") == selector:
            return c["name"]
    return selector

It is a normalized→physical mapper with a PASS-THROUGH fallback. rowdiff.py:234 `requested = [_physical_key(left_row, c) for c in compare_columns]` therefore leaves an already-physical name (`"Name.1"`, `"Unnamed: 1"`, `"Total Amount"`) unchanged, and :235 `unknown = [c for c in requested if c not in common]` finds it in `common` — which is the same physical set the response emitted. The round trip succeeds. A 400 would need the pathological coincidence of column A's physical name being exactly column B's normalized name (physical `"Name.1"` normalizes to `"name_2"`, blanks to `"column_N"` per data_io.py:253-274 — no collision in the very cases the claim cites).

3) Everything else in the response is physical too, so compared_columns is consistent, not the outlier: `added_sample`/`removed_sample` are `SELECT s.*` (rowdiff.py:161, 268-269), `changed_sample.column_name` and `column_changes[].column` are emitted from the physical `common` list (rowdiff.py:110, 132), and masking is deliberately converted to physical for exactly this reason (app/shared/masking.py:139-156, "The dictionary keys on normalized names; result rows carry physical ones").

4) Existing tests exercise the round trip's shape (tests/test_row_diff.py:62, 160-166 `{"columns": ["name"]}` → `compared_columns == ["name"]`); they use snake_case headers so they neither confirm nor contradict the mangled-header case, but nothing in the code path can produce the asserted 400.

Residual (not the claim as written, and not UI-blocking): `key` echoes the caller's namespace while the sample rows are keyed physically, so a UI reading key values out of `added_sample` via `body["key"]` would miss on a mangled header. Same mixture at rowdiff.py:222-228, where `missing`/`key` are caller names and `available` is physical. That is a cosmetic inconsistency, not the 400 the claim asserts.

## [low] GET /samples does use its own 100/1000 bounds, but so do sibling list endpoints, and the exposed key leaks only the caller's own team/dataset ids.

- **domain:** ?
- **where:** 

**Evidence**

The numeric facts check out but both inferences drawn from them are false.

1) "rather than the shared `pagination` dependency used by every other list endpoint" is factually wrong. `grep -rn "limit: int = Query" app/` returns five hand-rolled paginators, not one:
- app/features/files/api.py:722 `limit: int = Query(100, ge=1, le=1000)` (samples listing)
- app/features/files/api.py:763 `limit: int = Query(100, ge=1, le=10000, ...)` (read_sample rows)
- app/features/explorer/api.py:58 and :72 `limit: int = Query(default=100, ge=1, le=1000)` — identical bounds to /samples
- app/features/explorer/api.py:266, :282 `limit: int = Query(default=25, ge=1, le=data_quality.MAX_DUPLICATE_GROUPS)`
So /samples is not an outlier against a uniform 50/200 house style; row-oriented/data listings deliberately carry larger bounds while the 15 `Depends(pagination)` call sites cover the control-plane collections. All of them return the same `Page[T]` envelope (app/api/pagination.py:19-25, api.py:748), which is what a shared UI paginator actually binds to — and the envelope echoes the effective `limit` back.

2) The "hands every team member the internal bucket layout" claim collapses on inspection. The listing is filtered at app/features/files/api.py:732-736 to `teams = [t for t in principal.team_ids if principal.can(t, Permission.DATASET_READ)]`, and app/features/library/repo.py:169 turns that into `WHERE team_id = ANY(:tids)`. Keys are `artifacts/{team}/{dataset}/{kind}/{filename}` — i.e. every segment is a team the caller is already a member of and a dataset id that the same response already returns in the clear (`dataset_id=r["dataset_id"]`, api.py:743). No new identifier is disclosed.

3) The key is not a capability either. Download is by filename and re-authorizes independently: `_authorize_sample_access` (api.py:690-716) looks up the artifact row and calls `ensure_dataset_permission(...)`/`principal.can(...)`, raising 404 otherwise — and its docstring states "A blob with no row is therefore unreachable by everyone, superusers included". Possessing or guessing a storage key buys nothing.

4) tests/test_artifact_storage.py:150-160 already asserts the cross-team listing is empty and the download is 404; :162-172 asserts pagination honours limit=2 and echoes it in the envelope.

## [low] Claim says these raises emit no machine-readable code; the global problem+json handler always emits one, and the two branches differ by status AND code.

- **domain:** ?
- **where:** 

**Evidence**

The claim's central assertion — "plain HTTPExceptions with no machine-readable `code` or structured payload" — is false about the code.

1. Every `HTTPException` in this service is rendered through one handler that synthesizes a code. `/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/api/errors.py:115-124`:
```
async def _http_exception_handler(request, exc):
    return problem_response(exc.status_code, str(detail), request.url.path,
        code=getattr(exc, "code", None), ...)
```
and `problem_response` at errors.py:104 fills the gap: `"code": code or _code_for(status)`, with `_code_for` (errors.py:54-55) mapping 404 -> `not_found`, 400 -> `bad_request`. The module docstring (errors.py:3-16) states this explicitly: "Every error the API returns — raised ``HTTPException``, request-validation failure, or an unhandled exception — is rendered as one consistent shape ... Consumers can branch on the machine-readable ``code``". `install_error_handlers` registers it on `StarletteHTTPException`, so bare `HTTPException` is covered, and the media type is `application/problem+json`.

2. The impact claim — "The UI has to string-match `detail` to tell these apart" — is wrong for exactly the pair it names. `Sheet not found` is `404 / code=not_found` (app/shared/datasets.py:128, not :124 as cited; :124 is the `get_version_sheet_rows` call) while `sheet-selection-required` is `400 / code=sheet-selection-required` (datasets.py:133-138). Different status and different code; no prose parsing needed.

3. Tests assert the opposite of the claim. tests/test_wave0_journeys.py:359-365 asserts on every error surface, including the plain-404 RBAC ones at :399: `assert body.get("code")  # machine-dispatchable`. tests/test_column_metadata.py:208 and tests/test_discovery.py:249 assert `r.json()["code"] == "not_found"` on plain-HTTPException 404s.

4. "No recovery data" overstates it: `GET /datasets/{dataset_id}/sheets` exists (app/features/data_accelerator/api.py:570) as the first-class way to repopulate a stale picker, and sibling sheet-lookup sites already inline the list in `detail` (app/features/files/services/replace.py:52, data_accelerator/services/diffs.py:198).

What is actually true, and all that survives: these five sites use the generic status-derived code rather than a specific one, so a client cannot distinguish "sheet not found" from "dataset not found" (both `not_found`) without reading `detail`, and `Columns not found` / `Sort column not found` in downloads.py:183/350/369 do not use the repo's existing richer `unknown-column` + `available` contract that aggregation.py:222, shared/query/validate.py:49 and transform/expr.py:200 use. That is a granularity inconsistency, not the absence of a machine-readable contract the claim describes. ARCHITECTURE.md:129-131 documents the envelope requirement, and the envelope is satisfied.

## [low] The inline-JSON upload path is NOT uncapped — Starlette caps non-file form parts at 1MB — and `sync` as a query param is a repo-wide documented convention.

- **domain:** ?
- **where:** 

**Evidence**

The load-bearing part of the claim ("The inline-JSON branch also enforces no size cap at all ... a large `data` field is fully materialised in memory ... memory-exhaustion vector") is false.

`data` is a multipart FORM field, not a file: `data: str | None = Form(default=None)` (app/features/files/api.py:118), and the docstring documents the usage as `curl -F 'data=[{"a":1},{"a":2}]' /files/upload` (api.py:132). Starlette's multipart parser hard-caps every non-file part at 1MB before the handler is ever reached — venv/lib/python3.13/site-packages/starlette/formparsers.py:128 `max_part_size = 1024 * 1024  # 1MB` and :162-165:
    if self._current_part.file is None:
        if len(self._current_part.data) + len(message_bytes) > self.max_part_size:
            raise MultiPartException(f"Part exceeded maximum size of {int(self.max_part_size / 1024)}KB.")
(starlette 1.0.0 is what's installed). A MultiPartException surfaces as a 400. So the inline path is bounded at ~1MB — three orders of magnitude *below* `max_upload_bytes` (app/infra/config.py:73: `1024 * 1024 * 1024  # 1GB cap for the simple upload path`). Applying the 1GB cap to the inline branch would be a no-op; there is nothing to exhaust and nothing a 413 would add over the 400 that already fires.

The two remaining sub-claims are factually accurate but not defects:

1. `sync: bool = Query(default=True)` (api.py:123) alongside Form fields. This is the repo's uniform convention for sync/async toggles, not a one-off: `datasets/{ds}/transformations/{did}/run?sync=false` (tests/test_transformations.py:171, 376), `datasets/{ds}/relationships/suggest?sync=false` (tests/test_relationships.py:122), and `/api/v1/upload` is exercised with `params={"sync": "false"}` in tests/test_e2e_journeys.py:198. It is explicitly documented in the handler docstring under a "Query params:" heading (api.py:135-137). Deliberate.

2. 200 vs 201. Real but cosmetic. The 201 at api.py:446-449 is mandated by the TUS protocol (creation response must be 201 + Location), so it is not a free style choice. The 200 on `/upload` is pinned by existing passing tests (tests/test_files_misc.py:46, :83 `assert r.status_code == 200`). `/upload` is also not purely creational — it can append a version to an existing `dataset_id` (api.py:154-155, 244-245) and, in async mode, returns a poll handle rather than a finished resource. Other creating routes do use 201 (auth/api.py:68, library/api.py:42, etc.), so the inconsistency exists, but it is a status-code style nit with no correctness or UI-implementability consequence.

## [low] Run `status` and the (output_profile, source_drift) pair already discriminate every case the claim says is conflated except size-skip vs. profiler-exception.

- **domain:** ?
- **where:** 

**Evidence**

The claim asserts three indistinguishable meanings for `output_profile: null` and a second conflation on `source_drift: null`. Two of the three, and the source_drift conflation, are false on the actual source.

1) "run has not finished" is discriminated by `status`. app/features/transform/schemas.py:81 `status: str` is on `TransformationRunOut`, which `TransformationRunDetail` extends (schemas.py:91-95), and repo.get_run selects `r.status` and `r.error` (app/features/transform/repo.py:211-222). `output_profile`/`source_drift` are only ever written by `repo.complete_run` (repo.py:147-164), and failure goes through `_handle_transform` -> `repo.fail_run(run_id, str(exc))` (service.py:404-409). So a UI sees status=running (spinner), status=failed + `error` (retry), status=completed (terminal). No ambiguity with "not finished".

2) The `source_drift` conflation is false. Read `_profile_output` (service.py:354-397): if the output profile is missing (size skip line 363-365, or profiler exception 366-372) it returns `(None, None)`; the ONLY path that returns a non-null profile with a null drift is the source-profiling exception at line 386-391 `return out_profile, None`. So (profile != null, drift == null) unambiguously means "source profiling failed", and (null, null) means output profiling never happened. The pair is itself the discriminator.

3) The one residual ambiguity is real but narrow: on a completed run, (null, null) does not distinguish `size_bytes > MAX_AUTO_PROFILE_BYTES` (service.py:66 = 256 MB, skip at 363-365) from `run_profiling` raising (370-372). That single case is documented as deliberate: the function docstring at service.py:357-362 says "Best-effort by design: profiling is a convenience on top of a run that has already succeeded, so an oversized output is skipped and any failure is logged rather than failing the transformation." HANDOFF.md:219 records the same §21 intent. tests/test_transformations.py:264-310 asserts the populated shape, including `source_drift["source"] == "profile_run"` vs `ad_hoc`, i.e. the payload does carry provenance discriminators where they exist.

The MCP consumer (app/features/mcp/tools/pipeline.py:615-616) treats both as optional dicts, consistent with best-effort. The claim's stated impact — "cannot choose between ... and a spinner", "will show an empty state on every large output" — overstates a one-bit gap into a three-way blind spot.

## [low] The claimed strand mechanism does not exist: start_run already fails the run row when jobs.create_job blows up, in async mode too.

- **domain:** ?
- **where:** 

**Evidence**

The claim's load-bearing sentence is "An async run whose jobs.create_job fails leaves a `running` row with job_id NULL and no job row in existence, so neither the worker loop nor any operator tool will ever touch it again." That is false about the current code.

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/transform/service.py:250-280 wraps the dispatch:
  250  try:
  251      await worker.dispatch("transform", params=params, ..., inline=sync)
  255  except StarletteHTTPException as exc:
  261      await repo.fail_run(run["id"], str(exc.detail))
  262      raise
  263  except Exception as exc:
  279      await repo.fail_run(run["id"], str(exc))
  280      raise _pipeline_error(exc)
The comment at :270-272 names the exact scenario the claim asserts is unhandled ("Worse in async mode, where the failure can be ``jobs.create_job`` itself"). The catch is unconditional and covers both sync=True and sync=False; there is no branch that skips fail_run for async.

repo.fail_run (app/features/transform/repo.py:169-192) is guarded `WHERE id = :id AND status = 'running'`, and its docstring explains the belt-and-braces contract.

An existing test asserts the opposite of the claim, parametrized over exactly the async case:
tests/unit/test_transform_run_bookkeeping.py:90-106
  @pytest.mark.parametrize("sync", [True, False], ids=["sync", "async"])
  async def test_a_dispatch_failure_closes_the_run_row(...)
      ... assert [c[0] for c in rig.calls] == ["fail_run"]
with a docstring at :94-97 stating "In async mode it is unambiguously permanent: the exception came from ``jobs.create_job``" — i.e. the test exists precisely to pin that fail_run does fire there.

What IS true is only the narrow, secondary observation: scripts/audit_stranded_analytics_runs.py:147-172 scans `FROM analytics_runs` only (and its close SQL at :259-271 targets analytics_runs/jobs), so there is no equivalent operator script for transformation_runs. The test docstring at tests/unit/test_transform_run_bookkeeping.py:13-15 already records that as a known gap. But that is a missing ops convenience for a residual case (hard process death between create_run and the handler) that no audit script in this repo covers for any table — not the code defect the claim describes. The claim's own impact_on_ui concedes the fail_run coverage and then retreats to "process death", which is a different, much narrower assertion than what `what` states.

Also note the script's rationale for being analytics-only is explicit (docstring :33-39): an analytics run has no worker.dispatch and lives entirely inside one HTTP request, so age alone is a sound discriminator. A transformation run legitimately stays `running` while queued for the worker loop (asserted at tests/unit/test_transform_run_bookkeeping.py:139-149), so the same age heuristic would not transfer unchanged — the omission is defensible, not an oversight.

## [low] The 51-step body is indeed a 422, but that 422 does carry a stable `code` and a machine-readable error entry, so the stated UI impact is false.

- **domain:** ?
- **where:** 

**Evidence**

The one true part: `steps: list[TransformStep] = Field(default_factory=list, max_length=MAX_PIPELINE_STEPS, ...)` (app/features/transform/schemas.py:28-30) and the same bound on TransformationUpdate (schemas.py:38) do fire before the handler, and every caller of compile_pipeline gets already-bounded steps — service.py:124 (create/update, via validated body), service.py:192 (preview, `parse_steps(definition["steps"])`), service.py:304 (run, same). No other route or MCP tool feeds compile_pipeline raw steps: app/features/mcp/tools/pipeline.py:495 POSTs to `/datasets/{id}/transformations`, i.e. back through the same pydantic model. So compile.py:352-356 is defence-in-depth on a pure function, not a live HTTP branch.

Everything the claim builds on top of that is wrong.

1. "The 422 body has no `code` slug" — false. app/api/errors.py:104 in `problem_response` sets `"code": code or _code_for(status)`, and `_validation_exception_handler` (errors.py:127-133) goes through that exact function. The 422 body is `{"type","title","status":422,"detail":"Request validation failed","instance",...,"code":"unprocessable_entity","errors":[...]}`. The module docstring (errors.py:1-17) states this as the contract: "Every error the API returns — raised HTTPException, request-validation failure, or an unhandled exception — is rendered as one consistent shape ... Validation errors add an `errors` array."

2. "the step builder cannot show the limit without pattern-matching pydantic's message" — false. The entry is `{"loc":["body","steps"],"type":"too_long","ctx":{"max_length":50,...}}`; `jsonable_encoder(exc.errors())` (errors.py:132) preserves loc/type/ctx. Branching on `loc == ["body","steps"] and type == "too_long"` is exactly as machine-readable as a `code` slug.

3. "Every other pipeline-shaped rejection in this feature is a 400 with a stable code; this one alone is not" — false framing. Every *shape/size* bound in the feature is a 422 by design: `name` max_length=255 (schemas.py:21), `rows` ge=1/le=MAX_PREVIEW_ROWS (api.py:109). The 400+code family is for *semantic* failures the compiler can only find against the sheet schema (unknown-column, unknown-operator, sheet-selection-required). The split is deliberate and documented at app/features/mcp/tools/_common.py:196-199: "Request-shape rejections (a Literal violated, a filter operand of the wrong arity) arrive as a pydantic error array. Unrendered it reads 'Request validation failed', which tells a model nothing" — and the code right below it (_common.py:200-207) renders loc+msg per entry, so even the MCP caller gets "steps: List should have at most 50 items after validation, not 51".

4. tests/unit/test_transform_steps.py:160-163 asserts on `compile_pipeline(...)` directly, a pure-function unit test of the compiler's own invariant. It never claims an HTTP status, so it is not "asserting a 400 no caller can see" in any misleading sense.

## [low] The three joined columns really are not serialized, but they exist for authorization and publish lineage, not to describe the run, and the claimed UI impact is false.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical half is true and trivial: `TransformationRunOut`/`TransformationRunDetail` (app/features/transform/schemas.py:76-95) declare no `dataset_id`, `logical_sheet_id` or `sheet_key`, and pydantic v2 ignores undeclared kwargs, so `TransformationRunDetail(**run)` at app/features/transform/api.py:135 and :163 drops them. Everything the claim builds on top of that is wrong.

1. The premise "joined precisely so the run is self-describing" is contradicted by the code's own docstring. app/features/transform/repo.py:209: `"""A run joined to its definition — carries dataset_id for authorization."""` The join exists for the server. It is consumed server-side in exactly the two places you would expect: the tenant check at api.py:161 (`if not run or run["dataset_id"] != dataset_id`) and api.py:175, and the lineage edge at app/features/transform/service.py:429 (`relation="transformed_from", parent_sheet_key=run.get("sheet_key")`). Nothing in the join is orphaned; it is not "gone out of its way" for a client.

2. The impact is factually impossible as written. The claim says the UI gets "no transformation name, no sheet name, no dataset name" — but `get_run` selects no name of any kind. There is no `d.name`, no `ds.name`. Passing all three dropped fields through would still yield no transformation name and no dataset name, so the claim's own remedy ("a second GET /transformations/{definition_id} to resolve the name") is required either way. The field pass-through saves zero round trips for the stated scenario.

3. `dataset_id` cannot be missing from the caller. The route is `@router.get("/datasets/{dataset_id}/transformations/runs/{run_id}")` (api.py:153) and the POST is `/datasets/{dataset_id}/transformations/{definition_id}/run` (api.py:119). The caller supplies `dataset_id` — and `definition_id` on the POST — in the path. A "deep link" that reaches this endpoint already holds them. Echoing `dataset_id` back would be pure redundancy.

4. The shape is a consistent, deliberate contract, not an oversight. The sibling analytics feature's run DTO, `AnalyticsRunOut` (app/features/library/schemas.py:62-73), has the identical field list — id/definition_id/dataset_version_id/job_id/status/result_summary/artifact_id/triggered_by/timestamps/error — and likewise no dataset_id. Run DTOs in this service are definition-scoped by design; dataset identity lives in the path. Where the service does want to hand back dataset identity it says so explicitly: `PublishResponse` (library/schemas.py:89-94) declares `dataset_id` and `dataset_name`.

No test asserts these keys are present on a run response, so nothing is broken; and `sheet_key` — the one genuinely non-path, human-readable datum among the three — is already served on the definition itself via `TransformationOut.sheet_key` (transform/schemas.py:45).

Residue: a UI wanting the sheet name for a still-`running` run must read it from the definition rather than the run. That is a one-field convenience gap in a deliberately definition-scoped DTO, not the "run 3f2a… with no context" defect described.

## [low] PATCH can clear a description (send "") and can revert the pin (version_selector {"mode":"current"}); only null-vs-omitted is conflated.

- **domain:** ?
- **where:** 

**Evidence**

app/features/transform/schemas.py:35 — `description: str | None = None` with NO min_length and no strip/validator. So an empty textarea's natural payload `{"description": ""}` validates.

app/features/transform/service.py:152-153 — `if body.description is not None: fields["description"] = body.description`. `"" is not None` is True, so `""` is written; app/features/transform/repo.py:79-82 puts it straight into `SET description = :description`. The description IS cleared (to empty string, which TransformationOut at schemas.py:47 returns as `""` — an empty textarea). The claim's headline "PATCH cannot clear a description" is false; empty string is not a "sentinel", it is what an emptied textarea sends. Only the literal `null` payload is a no-op.

The version_selector half of the claim is flatly wrong. app/features/library/schemas.py:12-25 — `VersionSelector.mode` defaults to `"current"`. Sending `{"version_selector": {}}` or `{"version_selector": {"mode": "current"}}` yields a non-None body field, so service.py:155-157 takes `selector = body.version_selector.model_dump(exclude_none=True)` = `{"mode": "current"}`, service.py:159-168 sets `retargeting = True` and persists `fields["version_selector"]`. "Revert to the default current-version pin" therefore has an exact representation in the update body.

The `sheet` half is also wrong in premise: a definition always has a `logical_sheet_id` (repo.update_definition writes it as a required column); there is no "unset sheet" state to reach, so `min_length=1` blocks nothing meaningful.

Residual true fact, and it is only that: unlike the sheet/column-metadata PATCH, which the repo documents at HANDOFF.md:302-304 and ARCHITECTURE.md:456-459 as using `exclude_unset=True` so "an explicit `null` still clears", the transform PATCH does not use `exclude_unset`, so `description: null` is silently a no-op. The same `is not None` gate exists at app/features/explorer/service.py:156, i.e. it is the consistent convention for definition-style resources rather than a one-off slip. The response body echoes the stored (unchanged) description, so the client sees the real state immediately — nothing is silently wrong.

## [low] The catch-all does re-wrap HTTPExceptions as 500, but the claim's cited 400s are unreachable/raised outside the try and the 500 body is fully populated, not debug-redacted.

- **domain:** ?
- **where:** 

**Evidence**

The mechanical half of the claim is real: app/features/explorer/service.py:352-356 is `except Exception as e: ... raise HTTPException(500, f"Profiling run failed: {e}")`, and HTTPException/ProblemException are subclasses of Exception. Everything the claim builds on top of that is false.

1) The 400s cited are not in the try. The sheet-selection / "profiling-unsupported" ProblemException lives in `_profilable_sheets` (service.py:293-296), which is awaited at service.py:308 — BEFORE `try:` at 324. It propagates intact with its `code="profiling-unsupported"`. The other cited 400, `sheet-selection-required`, is raised by `resolve_version_sheet_row` only when `sheet is None`; profile_version always passes an explicit sheet (`sheet=sheet_row["sheet_name"]`, service.py:334), so it is unreachable on this path. I found no ProblemException reachable inside the try at all — so no `extra` fields and no custom `code` are actually lost.

2) The impact_on_ui is factually wrong about the response. The wrapper raises an `HTTPException`, so it is handled by `_http_exception_handler` (app/api/errors.py:115-124), not `_unhandled_exception_handler`. The client therefore always gets `detail: "Profiling run failed: File not found: ..."` and `code: "internal_server_error"` (errors.py:104 `code or _code_for(status)`). The debug-only redaction at errors.py:139 (`detail = ... if settings.debug else "An unexpected error occurred."`) applies only to *un*handled exceptions and never fires here. So "no code slug" and "message only in debug builds" are both untrue.

3) The only typed errors genuinely reachable inside the try come from `load_data`: HTTPException 404 "File not found" (app/shared/data_io.py:86-87), 404 "File not found or unreadable" for s3 (data_io.py:64), and 400 "Unsupported file format" (data_io.py:100-104). All three describe a version whose registered sheet artifact is missing or corrupt — a server-side data-integrity fault on a POST the user cannot correct by changing input. Relabelling those as 500 is defensible, not a lost corrective action.

4) The catch-all is deliberate and load-bearing: it is what fails the run and the job (`repo.fail_run`, `jobs.fail_job`, service.py:353-355), documented in app/features/explorer/repo.py:81-94, and tests/test_completed_run_demotion.py:112-131 exercises this exact except-block and asserts `r.status_code == 500` with the comment "The 500 is correct here (the response genuinely cannot be built)".

## [low] The teams endpoints are not outliers: eight list endpoints across the service use the same deliberate full-materialisation Page envelope.

- **domain:** ?
- **where:** 

**Evidence**

The claim's load-bearing premise — "Every other list endpoint in the service takes Depends(pagination) with ge=1/le=200 bounds" — is false. `grep -rn "limit=len(" app/` returns eight sites, not two:

- app/features/data_accelerator/api.py:78
- app/features/auth/api.py:120, :132 (the cited lines)
- app/features/library/api.py:67, :205
- app/features/quality/api.py:81
- app/features/discovery/api.py:252, :364

The pattern is documented as deliberate. app/features/data_accelerator/api.py:76-78 factors it into a named helper with an explanatory docstring:

    def _collection(items: list) -> Page:
        """Wrap a small, fully-materialised sub-collection in the Page envelope."""
        return Page(items=items, total=len(items), limit=len(items), offset=0)

i.e. the service's convention is: bounded sub-collections (a user's memberships, a team's members, a dataset's saved definitions, its sheet metadata) are returned whole inside the uniform envelope, while unbounded collections (audit events, jobs, datasets, webhook deliveries, explorer rows) take `Depends(pagination)` — app/api/pagination.py:39-45 with `Query(50, ge=1, le=200)`. The teams routes follow the first rule; they are not an inconsistency with "every other list endpoint".

The specific harms also don't hold up:
- "500-member team returns 500 rows... infinite scroll never terminates": the response reports `total == len(items)` and `offset == 0`, so a client sees the complete set in one shot; there is no next page to fail to fetch. `list_members` (auth/api.py:128-129) additionally requires `Permission.TEAM_READ`, i.e. the caller is a member.
- "empty result returns limit: 0 ... reachable via DELETE member": for `/teams/{id}/members` this is unreachable — the TEAM_READ guard means the caller is a member, so `items` is non-empty; and `_guard_last_owner` (auth/api.py:186-190) blocks removing the last owner. For `GET /teams`, a memberless user yields `{items: [], total: 0, limit: 0, offset: 0}`, but that is identical to what library/quality/discovery return for any empty sub-collection, so it is an envelope-wide convention, not a teams defect — and with `total == 0` a client has nothing to page through (`Math.ceil(0/0)` is NaN, not Infinity).

Existing tests (tests/test_teams_membership.py:22, :31) read only `["items"]` and assert content, consistent with the whole-collection contract.

## [low] Row-diff already validates metadata-derived key columns against both versions' schemas and returns 400 unknown-column with `available`; no unvalidated selector reaches DuckDB.

- **domain:** ?
- **where:** 

**Evidence**

The claim's premise is true but its load-bearing consequence is false.

True part: `SheetMetadataIn.primary_key_columns` (app/features/discovery/api.py:73-74) is a plain `list[str] | None` and `put_sheet_metadata`/`patch_sheet_metadata` (api.py:196-240) go straight to `repo.upsert_sheet_metadata`/`update_sheet_metadata` with no schema check, unlike the column-dictionary routes which resolve through `_resolve_dictionary_target` (api.py:255-297, 317, 343).

False part — "it reaches DuckDB as an identifier ... unhandled DuckDB exception rendered as a generic 500". The claim cites rowdiff.py:345-360 (`_resolve_keys`) and stops there. The very next lines in the caller do exactly the validation the claim says is missing, on ALL keys regardless of whether they came from metadata or from the explicit `key` field:

app/features/data_accelerator/services/rowdiff.py:221-228
    phys_keys = [_physical_key(left_row, k) for k in keys]
    missing = [k for k, p in zip(keys, phys_keys)
               if p not in left_cols or p not in right_cols]
    if missing:
        raise ProblemException(
            400, f"Key column(s) not present in both versions: {missing}",
            code="unknown-column", columns=missing,
            available=sorted(left_cols & right_cols))

`left_cols`/`right_cols` are built from `schema_json` at rowdiff.py:218-219, before any SQL is built or any DuckDB view is created (first `conn.execute` is at line 248). So a bogus declared PK yields precisely the problem+json the claim says is absent — same code (`unknown-column`) and same `available` list as the column-dictionary route it is contrasted with.

`_physical_key` (app/features/data_accelerator/services/sampling.py:573-580) does return the selector unchanged, as claimed — but that is the input to the `missing` check, not a path to DuckDB.

tests/test_row_diff.py:139-144 pins this behaviour (`test_an_unknown_key_column_is_rejected`: 400, code `unknown-column`, `"id" in available`). It exercises the explicit-key path, but the check at rowdiff.py:221 sits after `_resolve_keys` and is common to both sources, so the metadata-derived case is the same code.

Residual, much smaller: an unverifiable PK can be stored and does count toward documentation signals (app/features/discovery/repo.py:109, 382). That is arguably deliberate — sheet metadata is per logical sheet and outlives any single immutable version, and the column route's own docstring calls its schema check "early feedback only, same philosophy as saved-view create" (api.py:266-270), i.e. leniency about current-version schema is the house style, not an oversight. Nothing here produces a 500 or a silent wrong answer.

## [low] Timeline ORDER BY really has no tiebreaker, but both same-transaction tie sources the claim cites do not exist in the code.

- **domain:** ?
- **where:** 

**Evidence**

The one true part: `apps/analytics-service/app/features/discovery/repo.py:654` is `ORDER BY occurred_at DESC` with no secondary key, and `tests/test_timeline.py:83` only asserts `first["items"] != second["items"]` plus `stamps == sorted(stamps, reverse=True)` (line 57), neither of which pins tie order. But the claim's load-bearing premise — "Events written in the same transaction share a timestamp" — is false for both examples it gives.

(1) "version_created and its audit row". Every timeline timestamp column is `TIMESTAMPTZ NOT NULL DEFAULT now()` (e.g. `20260311001644_baseline.sql:52` dataset_versions.created_at, `20260803010000_hardening.sql:17` audit_log.occurred_at), so `now()` = transaction start. The audit row is NOT written in the version's transaction: `app/shared/audit.py:39-68` opens its own `async_session_factory()` and commits separately ("Insert one audit row. Best-effort: never raises into the request path"), and `files/repo.py:54` `create_version` likewise opens and commits its own session. Two different transactions, microsecond-resolution `now()` — they do not share a timestamp.

(2) "two tag writes in one request". `data_accelerator/repo.py:90-129` `set_tag` inserts exactly ONE `dataset_tag_history` row per request via a single `_record_tag_history` call; `delete_tag` (repo.py:133-159) likewise inserts one. There is no request that writes two tag-history rows.

I looked for any other systematic same-transaction tie and found none: `create_version` is one version per transaction; `library/repo.py:330` `record_lineage` opens its own session per row, so even a two-parent join publish (`library/service.py:322-331`) commits the two lineage rows in separate transactions.

The "COUNT and page evaluated independently" point is inert — COUNT(*) is order-insensitive, so it cannot contribute to duplicate/dropped rows.

What is left is a latent robustness gap, not the described bug: ties would require two independent transactions to begin in the same microsecond. The house pattern for the identical shape does add a tiebreaker (`app/shared/audit.py:92`: `ORDER BY occurred_at DESC, id DESC`), which the UNION ALL cannot reuse because it projects no id column.

## [low] The claimed kwarg collision is unconstructable: `code` and `headers` are keyword-only named params on ProblemException, so they never land in `extra`.

- **domain:** ?
- **where:** 

**Evidence**

The claim's premise — "an exception carrying an extra field named `code` or `headers`" — cannot exist.

app/api/errors.py:80-84:
    def __init__(self, status_code: int, detail: str, *, code: str | None = None,
                 headers: dict[str, str] | None = None, **extra: object):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extra = extra

`code` and `headers` are declared keyword-only parameters, so Python binds them to the named params before `**extra` collects anything. `ProblemException(401, "x", code="y", headers={...})` and even `ProblemException(401, "x", **{"code": "y"})` both bind to the named slots. There is no way to get `"code"` or `"headers"` into `self.extra`, so `problem_response(..., code=..., headers=..., **exc.extra)` at identity.py:135-140 cannot receive duplicate values for either.

This exact bug already existed and was deliberately fixed. The class docstring at app/api/errors.py:68-77 describes it verbatim: "a `headers=` kwarg that fell through to it would have been serialized into the JSON document while `_http_exception_handler` read `exc.headers` (`None`, from the base) and passed it positionally — `problem_response(..., headers=None, headers={...})`, a `TypeError` and a 500 in place of the response that was asked for."

Tests assert the opposite of the claim:
- tests/unit/test_problem_exception_headers.py:51-52 — `assert "headers" not in exc.extra` / `assert exc.extra == {"realm": "analytics"}`
- tests/unit/test_problem_exception_headers.py:55 `test_the_handler_renders_it_instead_of_raising_typeerror` — "The actual regression: this call used to raise, not return a response." It now asserts a 401 with a WWW-Authenticate header and a problem+json body.
- The claim asserts tests/unit/test_mcp_identity_errors.py "exercises extras but not a colliding name" — but that file also has `test_response_headers_survive_too` (line 110), covering the MCP middleware path specifically.

The only other way `getattr(exc, "extra", {})` could yield a colliding key is a different StarletteHTTPException subclass defining `.extra`. Grepping the app package, ProblemException (app/api/errors.py:84) is the only one; `ProblemError` (app/features/mcp/client.py:40) subclasses plain `Exception`, not StarletteHTTPException, so the `except StarletteHTTPException` clause never sees it. The one dynamic construction site, app/features/transform/expr.py:207 `_mismatch`, passes `code=` explicitly, which binds to the named param.

(Aside: even a hypothetical extra key `"title"` would not TypeError — `problem_response` also declares `title` as a named keyword-only param, so it would bind, not duplicate.)

## [low] `all_events` cannot be None on this path: the timeline route is response_model=Page[TimelineEvent] with a required int `total`.

- **domain:** ?
- **where:** 

**Evidence**

The interpolation is real but its input cannot be None on this call path.

1. Source of the value. app/features/mcp/tools/context.py:150-183 `_scan` loops at least once (`while scanned < cap` with scanned=0) and each iteration does `total = page.get("total") if isinstance(page, dict) else None`. So `total` is None only if the timeline response is not a dict, or is a dict without a `total` key.

2. What the endpoint actually returns. app/features/discovery/api.py:388-402:
   `@router.get("/datasets/{dataset_id}/timeline", response_model=Page[TimelineEvent], ...)` ... `return Page.of([TimelineEvent(**r) for r in rows], total, page)`.
   app/api/pagination.py:19-29: `class Page(BaseModel, Generic[T])` with `total: int` — a required, non-optional int. FastAPI serializes through this response_model, so the JSON body always carries an integer `total`. There is no 204/empty-body path (app/features/mcp/client.py:106-108 returns None only for 204 or empty content; a 200 Page response is neither), and non-2xx responses raise ProblemError at client.py:109 rather than returning a dict.

3. The message is scoped further. context.py:511-521 only emits "The timeline holds {all_events} events in total." inside `if not events:` — and the adjacent comment ("The endpoint's `total` counts every event type... reporting it would overstate") shows the None-ing of `total` for count_note is deliberate, and is a *different* variable from `all_events`.

4. Tests assert the opposite of the claim. tests/unit/test_mcp_context.py:687-688 `assert "The timeline holds 5000 events in total." in out`, and :670-671 the filtered-total test. No test produces a page lacking `total`.

The claim's premise ("None for any response that is not a Page") is a restatement of the defensive `isinstance` guard, not an observable behaviour: the only way to reach it is to delete the response_model from a documented route, which is a hypothetical, not a defect in this code.

## [low] Tool names and required-parameter splits are pinned by the unit suite; only the "full 27-name set in one assertion" part of the claim is true.

- **domain:** ?
- **where:** 

**Evidence**

The claim has three parts; two are false.

1) "nothing asserts any tool's required-vs-optional parameter split" — false. /home/saketh/Projects/playground/work/demo/apps/analytics-service/tests/unit/test_mcp_look.py:715 asserts exactly that, off the published schema: `assert published["run_sql"]["required"] == ["dataset_id", "sql"]` (built at :706 from `{t.name: t.input_schema for t in await tools.server.list_tools()}`). Neighbouring assertions pin published bounds too (:709-714: query_rows.limit 1..1000, run_sql.max_rows 1..500, sql minLength 1), and tests/unit/test_mcp_pipeline.py:1884-1913 and tests/unit/test_mcp_compute.py:841 pin published property schemas/descriptions for transform_data and others.

2) "Renaming a tool ... the suite stays green as long as the count is unchanged" — false. tests/unit/mcp_harness.py:232-262 `register_tools` returns a plain `dict` subclass keyed by the server's own registered names (`{name: tool.fn for name, tool in server._tool_manager._tools.items()}`), and every tool unit test indexes it by literal name (e.g. tests/unit/test_mcp_compute.py:284 `await tools["profile_column"](...)`, test_mcp_look.py:131+ `tools["query_rows"]`). A rename produces KeyError across dozens of tests, not a green suite. tests/unit/test_mcp_harness_smoke.py:224-231 additionally asserts an exact name set for the orient module (`assert set(tools) == {"whoami", "search_datasets", "describe_dataset", "get_data_dictionary", "search_columns", "get_dataset_health"}`) with a docstring saying "a renamed tool fails here rather than silently going untested".

3) "Promoting an optional parameter to required ... suite stays green" — also false: tests call the closures directly with only the arguments they need (tests/test_mcp_endpoint.py:482 `query_rows` with just `dataset_id`; test_mcp_look.py passim), so a newly-required parameter raises TypeError / arg-model errors in those tests.

Only the narrow first clause survives: tests/test_mcp_endpoint.py:104-108 does pin the count (27) plus four sample names rather than the whole 27-name set, and there is no single test that asserts the complete published name set or the required list for all 27 tools. That is a partial coverage gap, not the described "contract untested / suite silently green" defect.

## [low] filter_expr is not unguarded: the service runs sanitize_filter_expr on it, and the MCP path's rejection behaviour is explicitly tested.

- **domain:** ?
- **where:** 

**Evidence**

The claim's core assertion — "forwards it into a DuckDB WHERE clause" with no gate, "undocumented ... and untested from [MCP]" — is false on all three counts.

1) There IS a server-side gate on this exact path. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/files/services/downloads.py:356-359 (inside read_sample_data, the function the route at api.py:790 calls):
    where = ""
    if filter_expr:
        sanitize_filter_expr(filter_expr)
        where = f" WHERE {filter_expr}"
/home/saketh/.../app/shared/utils/sql.py:34-47 defines that gate: rejects any ";" (400 "Filter expression must not contain semicolons") and any of DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|UNION (400 "contains disallowed SQL keywords"). So both tools do route through a gate; they are different gates, not "gate vs no gate".

2) It is tested from the MCP side. tests/unit/test_mcp_artifacts.py:627-640, `test_a_rejected_filter_expression_says_which_rule_it_broke`, calls read_artifact with filter_expr="amount > 1; DROP TABLE t" and asserts the caller sees "must not contain semicolons". Its docstring states the contract outright: "``filter_expr`` is raw SQL and the sanitizer rejects semicolons and DDL keywords."

3) The no-client-side-mirroring choice is deliberate and documented in the very lines the claim cites — artifacts.py:121-124: "Passed straight through: ... the service rejects anything else with a 422 that ``explain`` renders. Mirroring that check here would only risk drifting from it." MCP is a thin transport over the same authenticated REST endpoint (GET /samples/{filename}/data, api.py:757-793, itself authorized by _authorize_sample_access at api.py:704-716); it exposes no capability a REST/UI caller of the same endpoint does not already have, so there is no MCP-specific safety story to document.

Residual, and NOT what the claim asserts: sanitize_filter_expr is a keyword denylist rather than a parser gate, and the connection used by read_sample_data comes from app/shared/data_io.py:82 `duckdb.connect()` without the `SET enable_external_access = false; SET lock_configuration = true` hardening that app/shared/duck.py:61-62 applies to the run_sql sandbox. A scalar subquery (e.g. `amount > (SELECT ... FROM read_csv('/etc/passwd'))`) contains no denylisted keyword. That is a separate, narrower hypothesis about the sanitizer's strength — the filed claim ("no client-side restriction", "untested", "a UI cannot know") is refuted as written.

## [low] The three routes really do ignore limit/offset, but _fetch_all is correct today and the claimed duplicate loop needs a route that honours limit while still ignoring offset.

- **domain:** ?
- **where:** 

**Evidence**

The only factual half of the claim checks out: `app/features/quality/api.py:76-81` (`list_rules`), `app/features/library/api.py:59-67` (`list_definitions`) and `app/features/library/api.py:197-205` (`list_charts`) take no `PageParams` and return `Page(items=items, total=len(items), limit=len(items), offset=0)` over a repo that selects every row (`app/features/quality/repo.py:49-57`, `app/features/library/repo.py:40-47`, `430-437`). The other two kinds do page: `app/features/explorer/api.py:115-124` and `app/features/transform/api.py:59-67` both `Depends(pagination)` and return `Page.of(rows, total, page)`.

Everything else in the claim is wrong or hypothetical:

1. "five full table scans for a counts table" — only three. The counts loop at `app/features/mcp/tools/context.py:817-819` sends `limit=1`, which views and transformations honour, so those two probes really are O(1). And the counts path reads only `page.get("total")`, which is exact for all five kinds, so no output is wrong.

2. "paging correctness for those kinds depends on the `total` break firing" — for any collection under 200 items the first break fires instead: `context.py:142` `if not chunk or len(chunk) < PAGE_SIZE or len(items) >= cap: break`. The `total` break at :144 only matters at >=200 items, where it is also correct (`total == len(chunk) == len(items)`).

3. The predicted future failure does not follow from "a routine pagination fix". `_fetch_all` advances `offset += len(chunk)` (`context.py:146`). A route that starts honouring `limit` via the standard `Depends(pagination)` + `Page.of` pattern (exactly what explorer/transform already do) honours `offset` too, and `_fetch_all` pages it correctly with no duplicates. Duplicates require a half-fix that honours `limit` but still ignores `offset` — a shape that exists nowhere in this repo.

4. The accommodation is deliberate and documented in the function's own docstring, `context.py:128-133`: "Half the saved-object list routes ignore limit/offset entirely and return the whole set in one response; the other half page properly. Collecting everything here makes the two behave identically for the caller."

Tests exist for this path: `tests/unit/test_mcp_context.py:1152-1162` asserts one GET per segment with `params == {"limit": 1}`. Its docstring aspiration ("must not download five full lists") is only partly met against the real routes, but the assertion it makes — one request per kind, correct counts — holds.

## [low] GET/DELETE on the mounted MCP route are fully specified by the SDK (401/406/405/idle SSE), not unspecified; only the test coverage is absent.

- **domain:** ?
- **where:** 

**Evidence**

The claim has two parts. The coverage part is true; the substantive part ("unspecified response", "determines whether a browser-based MCP client can connect at all") is false.

Route declaration is deliberate and documented. /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/asgi.py:96-103 appends `Route(mounted.path, endpoint=mounted.asgi_app, methods=["GET","POST","DELETE"], name="mcp")`, and ARCHITECTURE.md:496 documents the surface as `POST/GET/DELETE  /api/v1/mcp        streamable HTTP, stateless, JSON responses`. The method list simply mirrors the three methods the SDK transport implements.

The response for each method is deterministic, not unspecified. Every request first passes MCPIdentityASGIMiddleware (app/features/mcp/identity.py:110-152), which runs `get_principal` regardless of method, so an unauthenticated GET or DELETE gets a problem+json 401 before the session manager sees it. Then, in venv/.../mcp/server/streamable_http.py:479-486 the transport dispatches by method:
- DELETE, line 784-793: `if not self.mcp_session_id:` -> `_create_error_response("Method Not Allowed: Session termination not supported", HTTPStatus.METHOD_NOT_ALLOWED)`. In stateless mode the manager constructs the transport with `mcp_session_id=None` (streamable_http_manager.py:201-206), so DELETE is always a clean 405 — exactly what the streamable-HTTP spec prescribes for a server that has no sessions.
- GET, line 687-706: without `Accept: text/event-stream` it is a 406 ("Not Acceptable: Client must accept text/event-stream"); with it, an `EventSourceResponse` whose headers are sent immediately and which then idles with no events. That is a normal idle SSE stream, not a hang-before-headers, and the spec makes the standalone GET stream optional — clients connect by POSTing `initialize`, which tests/test_mcp_endpoint.py:92-108 exercises and asserts 200 with 27 tools. So no client is prevented from connecting.

Also false is "the SDK's stateless session manager has no session to open or terminate" implying undefined behaviour: streamable_http_manager.py:195-249 creates a fresh transport per request and calls `http_transport.terminate()` afterwards, and the no-session DELETE path is explicitly coded to answer 405.

The only accurate residue: grep of tests/ shows the sole MCP HTTP driver is `http.post(MCP_URL, ...)` (tests/test_mcp_endpoint.py:71) and no test issues GET or DELETE against /api/v1/mcp. That is a coverage gap on already-specified third-party behaviour, not a defect in this repo's code.

## [low] get_data_dictionary does not hold the list of valid sheet_keys when the columns route 404s; /sheet-metadata returns only sheets with recorded metadata.

- **domain:** ?
- **where:** 

**Evidence**

The claim's load-bearing premise — "At that moment the tool is holding the list of valid sheet_keys and throws it away" — is factually wrong.

1) The first call is GET /datasets/{id}/sheet-metadata (orient.py:193), which is backed by `repo.list_sheet_metadata` (app/features/discovery/repo.py:306-313): `SELECT ... FROM dataset_sheet_metadata WHERE dataset_id = :did`. That is the table of *recorded semantic metadata rows*, not the dataset's logical sheets. A dataset with no dictionary returns zero rows — the tool itself handles that case at orient.py:205-208 ("No sheet-level metadata ... has been recorded for this dataset"), and tests/unit/test_mcp_orient.py:286 pins `page([])`. So at the 404 moment the tool may be holding an empty list, or a strict subset of the live sheets. It cannot honestly say "the keys that do exist".

2) The comparator standard the claim invokes cuts the other way. In app/features/mcp/tools/_common.py:106-117, `explain` renders `exc.sheets` for `sheet-selection-required` and `exc.available_columns` for `unknown-column` — both are structured fields the *service* puts on the problem document. The discovery 404 here is a bare `raise HTTPException(404, f"Sheet not found: {sheet_key}")` (app/features/discovery/api.py:279) with no such field, so `explain` falls through to the generic 404 branch (_common.py:188-193). The MCP layer's convention is to render what the service supplies, not to fabricate a list from an unrelated route.

3) The behaviour is deliberate and already pinned with a rationale, not an oversight. tests/unit/test_mcp_orient.py:355-371, `test_a_sheet_key_that_does_not_exist_is_reported_with_the_key`, docstring: "a wrong one is nearly always a transcription error it can fix itself — but only if the rejected key is echoed back. The 404 caveat matters here too: this route 404s for a sheet in someone else's dataset the same way it does for a misspelling." The echoed key plus the existence-hiding caveat is the intended contract (consistent with the repo's documented 404-hides-existence property).

The only true residue is that the error could optionally be enriched, as look.py:24-36 `_table_hint` does — but note that helper deliberately fetches the authoritative `/datasets/{id}/sheets`, precisely because the metadata route is not a sheet inventory. That is a UX enhancement request, not a defect, and the claim's own author concedes "I pinned the current message rather than the desired one."

## [low] Pivot omits the inline "read it with read_artifact" hint, but the server instructions already state that rule globally.

- **domain:** ?
- **where:** 

**Evidence**

The textual difference is real but the claim's substance ("the model is handed a filename and left to guess which of the 27 tools consumes it") is false.

app/features/mcp/tools/compute.py:183-186 (aggregate): `notes.append(f"Full result saved as artifact '{payload['result_file']}' — read it with read_artifact.")`
app/features/mcp/tools/compute.py:278-279 (pivot): `notes.append(f"Full result saved as artifact '{payload['result_file']}'.")`

But the server-level MCP instructions — attached to every session via `MCPServer(..., instructions=INSTRUCTIONS)` at app/features/mcp/server.py:82-93 — already state the general rule, at server.py:64-66:
"Results too large for a response are saved as artifacts — feed the returned filename to read_artifact with columns, a filter and paging, rather than asking for everything at once."

Reinforced elsewhere in the same always-loaded context: `read_artifact`'s own filename parameter is described as "Artifact filename, e.g. the result_file returned by run_sql." (app/features/mcp/tools/artifacts.py:101), and pivot's own tool description says it "Returns a bounded table plus a handle to the full output" (compute.py:224-226). Note that run_sql itself — the tool the global instruction and read_artifact's schema both name explicitly — is in the same boat; only aggregate (compute.py:185) and query_rows (look.py:188) repeat the tool name inline. So pivot is not a lone outlier against a consistent convention; the per-tool restatement is the exception, not the rule.

No behavioural difference: both branches fire off the same `payload.get("result_file")` key, both return the same filename, and the artifact is retrievable identically. Nothing is wrong, missing or misleading in the pivot output — it is a one-clause wording nicety that duplicates guidance already in the system instructions.

## [low] The versions list endpoint is not paginated at all, so resolve_version already sees every version — there is no page to fall off.

- **domain:** ?
- **where:** 

**Evidence**

The claim's premise ("fetched with no `limit`, so it sees only the service's default page") is false: `GET /datasets/{id}/versions` has no pagination.

app/features/data_accelerator/api.py:250-255:
```
@router.get("/datasets/{dataset_id}/versions", response_model=Page[VersionInfo], tags=["versions"])
async def list_dataset_versions(dataset_id: str, principal: Principal = Depends(get_principal)) -> Page[VersionInfo]:
    """List all versions for a dataset, newest first, including tags."""
    await ensure_dataset_permission(principal, dataset_id, Permission.DATASET_READ)
    rows = await repo.list_versions(dataset_id)
    return _collection([VersionInfo(**r) for r in rows])
```
There is no `page: Pagination = Depends(...)` parameter here — contrast api.py:177 and :199 (dataset search/list) and :520 (tag history), which DO take `page.limit`/`page.offset`. The route accepts no limit/offset query params, so passing one from the MCP client would be ignored anyway.

app/features/data_accelerator/repo.py:430-452 (`list_versions`): a single SELECT `... WHERE dv.dataset_id = :did ORDER BY dv.version_number DESC` — no LIMIT, no OFFSET, returns every row.

app/features/data_accelerator/api.py:76-78:
```
def _collection(items: list) -> Page:
    """Wrap a small, fully-materialised sub-collection in the Page envelope."""
    return Page(items=items, total=len(items), limit=len(items), offset=0)
```
The Page envelope is cosmetic — `limit` is set to the full item count, i.e. "everything, wrapped to look like a page".

Call path checked end to end: app/features/mcp/tools/_common.py:223 -> app/features/mcp/client.py:70/92 (in-process ASGI httpx to the same FastAPI app) -> api.py:250 -> repo.py:430. Every version, newest-first, reaches the loop at _common.py:224-226, so an older ready version behind many non-ready newer ones IS found and returned. The failure scenario in the claim cannot occur.

(The claim itself concedes it is "Not reachable in a unit test" — the reason is that the behaviour does not exist, not that the fixture is hard to build.)

## [low] query_rows never reports a sheet that differs from the one used; on the auto path the service guarantees exactly one ready sheet, so the omitted line is cosmetic.

- **domain:** ?
- **where:** 

**Evidence**

The claim has two halves; the load-bearing half is false.

1) "renders the sheet the CALLER named, not the one the service resolved" implies divergence. There is none. app/features/mcp/tools/_common.py:233-238 `sheet_path()` routes to `.../sheets/{sheet}/query` whenever `sheet` is truthy, and to `.../versions/{v}/query` otherwise. So when the header prints `sheet: X` (look.py:125), X is literally the path segment the service was asked to use — app/features/explorer/api.py:343 passes `sheet_name` straight to `service.query_sheet`, and app/shared/datasets.py:125-129 does `_find_sheet(...)` or 404s. The header can never name a different sheet than the one queried.

2) On the auto-resolving route the service cannot be reporting some hidden choice, because it never makes one: app/shared/datasets.py:117-139 — "Never silently picks a sheet: multi-sheet versions require an explicit name and answer with the machine-readable `sheet-selection-required` problem" — `ready = [r for r in sheets if ...]; if len(ready) > 1: raise ProblemException(400, code="sheet-selection-required", sheets=[...])`. The un-scoped route therefore only ever succeeds when the version has exactly one ready sheet (or is a legacy sheet-row-less version, synthesized as "data" at explorer/service.py:72-79). There is no ambiguity for the model to be misled about.

3) The service does not return the resolved sheet at all: app/shared/query/schemas.py:206-216 `QueryPage` carries only items/next_cursor/total/masked_columns. So the MCP tool has nothing to echo without an extra round trip; the "expected" behaviour in the claim is not available from the response.

4) The behaviour is deliberate and documented at the pin site, tests/unit/test_mcp_look.py:144-154: "A single-sheet dataset must be readable without the caller first learning the sheet's name... inventing a name like \"Sheet1\" here would 404" and "# Nothing claims a sheet name that was never established."

Residual true fact: when `sheet` is omitted the output has no sheet line (render.fields at app/features/mcp/render.py:36 drops None). That is an omission of a value the response never contained, on a path where exactly one sheet exists — cosmetic, not a wrong answer.

## [low] Claimed ".." rendering at _common.py:206 is unreachable: the 422+errors branch has exactly one producer, a fixed literal with no trailing period.

- **domain:** ?
- **where:** 

**Evidence**

The `f"{exc.detail}. "` at app/features/mcp/tools/_common.py:206 is real, but the failure mode the claim posits ("any route-supplied 422 detail ending in a period") cannot occur, and the punctuation is deliberate and test-pinned.

1. The branch is gated twice: `if exc.status == 422` AND `if problems:` — `problems` is only non-empty when the problem body carries an `errors` array (_common.py:200 `exc.extra.get("errors")`).
2. The only place in the whole app that puts `errors` into a problem body at status 422 is app/api/errors.py:127-133, `_validation_exception_handler`, whose detail is the hard-coded literal `"Request validation failed"` (errors.py:130) — no trailing period. `grep -rn "errors=" app/` returns exactly two producers: errors.py:132 and app/features/library/service.py:97, and the latter raises `ProblemException(400, ...)` (service.py:93), so it never reaches the 422 branch. Grepping every `raise ProblemException(` in app/ for a 422 status returns nothing — no route supplies a 422 detail at all.
3. The period is intentional, not accidental: tests/unit/test_mcp_common.py:395 asserts `out.startswith("Request validation failed. ")`, and tests/unit/test_mcp_common.py:449-454 pins the complementary case (no usable error array → bare `"Request validation failed"` with no dangling ". "), with the docstring explaining why: 'a dangling "Request validation failed. " with nothing after it looks like the tool lost the explanation it was given.'

The claim itself concedes it is "Harmless for the only producer today". The remaining scenario is hypothetical, unreachable in the current code, and would at worst render ".." — cosmetic, never a wrong answer.

## [low] The fabricated 'None.None' relationship row is unreachable: the join responses are response_model-validated with a required relationship field.

- **domain:** ?
- **where:** 

**Evidence**

The helper's mechanics are as described — /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/pipeline.py:97-109 builds `"from": f"{r.get('from_sheet')}.{r.get('from_column')}"` and `"cross_dataset": r.get("to_dataset_id") != r.get("dataset_id")`, so an empty dict would render "None.None"/False. But nothing can hand it an empty dict. app/features/relationships/schemas.py:114-122 declares `class JoinExecuteResponse` with `relationship: RelationshipOut` as a required field, and app/features/relationships/api.py:216 wires it: `@router.post("/joins/execute", response_model=JoinExecuteResponse, tags=["joins"])`. FastAPI's response_model therefore guarantees `payload["relationship"]` is a full RelationshipOut before the MCP tool ever sees it, so the `or {}` at pipeline.py:430 (and the preview path at :402, whose response is validated the same way) is defensive dead code, not a live defect. The claim itself concedes "this cannot fire today"; there is no input, state, or call path that produces a wrong rendered row. The only real-world source of a "None." prefix is RelationshipOut.from_sheet being legitimately `str | None = None` for single-sheet versions (schemas.py:16, :20), which is a rendering nicety unrelated to the claimed empty-dict fabrication. No behavioural bug exists to fix.

## [low] Claimed empty "Available columns: ." is unreachable — all eight unknown-column raise sites populate `available`, and that branch already ends with an actionable describe_dataset hint.

- **domain:** ?
- **where:** 

**Evidence**

The code text is described accurately, but the claimed defect is unreachable and the asymmetry is justified, so this is not a bug.

1. Every raise site of `unknown-column` in the service passes a populated `available`, not just `validate.py::_resolve`. I enumerated all of them:
- app/shared/query/validate.py:49 `code="unknown-column", column=name, available=_available(schema_json)`
- app/features/transform/expr.py:200-202 `available=[c.get("normalized_name") or c["name"] for c in sorted(schema, ...)]`
- app/features/relationships/service.py:85-87 `available=[...schema_json...]`
- app/features/relationships/joins.py:124 `code="unknown-column", available=names` (`names` is the join's output column list, never empty at that point)
- app/features/data_accelerator/services/aggregation.py:222 `available=valid_sort`
- app/features/data_accelerator/services/rowdiff.py:227,239 `available=sorted(left_cols & right_cols)` / `available=common`
- app/features/explorer/{service.py:238,data_quality.py:121} go through `resolve_schema_column`, i.e. validate.py again.
So there is no "second raise site" that omits the key — the claim's hypothetical is the only failure mode, and it requires a future code change to exist.

2. The transport preserves the key: client.py:119 `extra = {k: v for k, v in body.items() if k not in _PROBLEM_KEYS}`, and client.py:49-52 `available_columns` returns `extra["available"]` when it is a list. Nothing strips it.

3. The asymmetry with the sheet branch is defensible, not an oversight. _common.py:108-109 has no follow-up action to offer ("Available sheets: unknown." is the whole recovery hint), whereas _common.py:114-117 always appends "Call describe_dataset to see the full schema." — the unknown-column message stays actionable even with an empty list, which is exactly why only the sheet branch needs the `or "unknown"` sentinel. tests/unit/test_mcp_common.py:126-133 documents the sheet-branch reasoning ("an empty list looks like 'there are no sheets', which is a different and untrue statement"); no equivalent dead end exists for columns.

The claim itself concedes "Latent today" — it describes a defensive-coding preference about a hypothetical future caller, not behaviour the service can produce.

## [low] seed does lack clamp(), but it is far from the only unclamped unbounded table on this tool surface, and no wrong data results.

- **domain:** ?
- **where:** 

**Evidence**

The one true fact: /home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/pipeline.py:280-288 returns a bare `render.join(... render.table(_relationship_rows(rows)) ...)` with no clamp, and the route feeding it is unbounded — app/features/relationships/api.py:44-56 returns `relationships=[RelationshipOut(**r) for r in created]` from service.seed_from_fk_rules (app/features/relationships/service.py:94-138), which iterates every enabled foreign_key rule from quality_repo.list_rules, and app/features/quality/repo.py:49-57 has no LIMIT.

But the claim's distinguishing premise is false on two counts.

(1) "a tool surface whose other seven rendering paths all cap at 60,000" — pipeline.py contains exactly FIVE clamp() calls (suggest, join preview, join execute, transform preview, transform inspect); declare, confirm/reject, transform create, transform run and publish_result all return bare render.join too. Across the wider surface, `grep -c "clamp("` gives: compute.py 0, orient.py 0, look.py 1, curate.py 3, artifacts.py 1, context.py 6. Two entire tool modules never clamp.

(2) "the only rendering path with an UNBOUNDED table" — it isn't even the worst one. compute.py:215 renders `render.table(rows, payload.get("columns"))` for `aggregate` with `limit` allowed up to 1000 (compute.py:160, ge=1 le=1000) and no clamp; compute.py:428/453/454 render schema-diff tables for compare_versions with no clamp; orient.py:242 renders the full column dictionary for a sheet with no clamp. Any of these blows past 60,000 chars more easily than seed's rows, which are seven short fields each (render.scalar caps every cell at MAX_CELL=80, render.py:11) — roughly 100-150 chars per row, so "a few hundred FK rules" lands around 30-45k, i.e. under the budget the claim says is being missed.

Nothing in the repo documents 60,000 as a policy: `grep -rn "clamp|60_000|60,000" --include=*.md` returns nothing. And clamp() (app/features/mcp/tools/_common.py:71-79) only truncates text and appends a hint — its absence produces a long response, never a wrong one.

## [low] _require is a presence check, not a range check; version=0 is rejected by the MCP arg model (ge=1) and, even if forced, by a 404 from the REST layer.

- **domain:** ?
- **where:** 

**Evidence**

/home/saketh/Projects/playground/work/demo/apps/analytics-service/app/features/mcp/tools/curate.py:69-72 — `def _require(value, *, param, because): if value in (None, ""): raise ToolError(...)`. Its docstring-free but its call sites make its contract plain: lines 333, 370, 507, 508, 559 all pass strings (sheet_key, column_name, name, rule_type, rule_id). It is a "you left this parameter unset" check, not a value-range check, so accepting 0 is not a defect — 0 IS a supplied value.

The claim's own escape hatch is the decisive part, and it is correct: curate.py:808-817 declares `version: Annotated[int | None, Field(..., ge=1)] = None` on the `@server.tool`-registered `manage_tags` (registration at curate.py:770, closure at 799). The MCP layer validates against that model, so `version=0` never reaches curate.py:862.

The claim's "would reach the PUT with version_number=0" tail is also harmless if the model layer is bypassed by calling the closure directly. curate.py:863-866 PUTs `{"tag_name": tag, "version_number": version}`; the route at app/features/data_accelerator/api.py:363-377 does `ver_row = await repo.get_version_by_number(dataset_id, body.version_number)` and at :374 `raise HTTPException(404, f"Version {body.version_number} not found for dataset {dataset_id}")`. So version 0 produces a clean 404, which `guard` (app/features/mcp/tools/_common.py:82-92) converts into a ToolError. There is no path — production or test-harness — that yields a wrong answer, a corrupted tag, or a 500.

Net: the claim is factually right that `0 not in (None, "")`, but wrong that this constitutes a thin or defective guard. Two independent layers (pydantic ge=1, then a 404 on version lookup) already own the value range, and _require was never the layer that owned it.

## [low] The MCP run header is unconditional, but a failed sync run cannot return 2xx — the service returns 400 transformation-failed, so the tool raises instead of rendering "complete".

- **domain:** ?
- **where:** 

**Evidence**

The claim's premise — a 2xx run response carrying `status: "failed"` — is unreachable on the path the MCP tool uses.

1. The tool always runs synchronously: `app/features/mcp/tools/pipeline.py:582-584` posts `/datasets/{id}/transformations/{def}/run` with `sync=True` (the route's own default is also `sync: bool = Query(default=True)`, `app/features/transform/api.py:122-123`).

2. With sync=True the handler runs in-request and every failure re-raises as an HTTP problem, never as a 2xx body. `app/shared/worker.py:140-144`: `result = await handler(job)` … `except Exception: await jobs.fail_job(...); raise`. `app/features/transform/service.py:263-281`: `except Exception as exc: await repo.fail_run(run["id"], str(exc)); raise _pipeline_error(exc)` — and only the non-exception path reaches `return await repo.get_run(run["id"])`. The sole place a 2xx run body is produced is after `repo.complete_run(...)` (`service.py:347`), which sets `status = 'completed'` (`app/features/transform/repo.py:153`). `fail_run` (repo.py:187-188) can only be reached on a path that also raises.

3. An existing integration test asserts exactly this: `tests/test_transformations.py:187-202` `test_a_failing_pipeline_records_the_error_on_the_run` — `assert r.status_code == 400` and `r.json()["code"] == "transformation-failed"`, with the run row later showing `status: failed`. The failure DOES live in the HTTP status, contrary to the docstring of the unit test that motivated the risk (`tests/unit/test_mcp_pipeline.py:1452-1455`: "the failure lives on the run row, not in the HTTP status, so `guard` never sees it").

4. On a 400 the MCP client raises: `app/features/mcp/client.py:105-109` `if response.is_success: … ; raise _problem_from(response)` → ProblemError → ToolError. The string "Transformation run complete. Created:" (pipeline.py:587) is never reached for a failed run.

So `test_a_failed_run_surfaces_the_error_the_service_recorded` exercises a state only its own fake client can produce. The unconditional header is a latent style wart (defence in depth if async runs were ever surfaced here), not a live misleading-output defect; note also that `render.fields` (app/features/mcp/render.py:34) drops None values, so no `sample_file:`/`artifact_id:` field is ever fabricated. The genuinely-failed-run view is `action='inspect'` (GET, pipeline.py:611), which renders `status` and `error` with no success header or publish advice.

