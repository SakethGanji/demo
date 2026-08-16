# Analytics Service — UI Integration Guide

Everything a frontend needs to talk to this API cleanly. The reference UI in
`web/` is a working implementation of everything below.

## Base & shape

- All endpoints are under **`/api/v1`**. The OpenAPI spec is at **`/openapi.json`**
  (107+ paths); a generated TypeScript type surface is committed at
  `web/src/api/schema.d.ts` (regenerate with
  `npx openapi-typescript openapi.json -o web/src/api/schema.d.ts`).
- Requests/responses are JSON. Uploads are `multipart/form-data`. Downloads
  stream bytes.

## Authentication (POC header identity)

The service authenticates by **headers**, not a token:

- `X-User-Id: <uuid>` — required. The seeded System superuser is
  `00000000-0000-0000-0000-000000000001`.
- `X-Team-Id: <uuid>` — optional; defaults to the user's team. Set it to act
  within a specific team.

The reference client (`web/src/api/client.ts`) attaches these on every request
and exposes an "Act as" switcher so you can view the app as a viewer or another
team to exercise RBAC and masking.

## Pagination — two envelopes

1. **Offset lists** (`Page<T>`): `{ items, total, limit, offset }`. `limit` is
   always ≥ 1 (safe for `ceil(total/limit)`). Pass `?limit=&offset=`.
2. **Cursor pages** (`QueryPage`): `{ items, next_cursor, total, masked_columns }`
   — used by `/preview` and `/query`. Follow `next_cursor` (opaque) until it's
   `null`. A cursor is bound to the version + query spec; replaying it against a
   different one returns `400 invalid-cursor`. Paging is a **total order**, so it
   never skips or duplicates rows even when the sort key has ties.

## Errors — always problem+json

Every 4xx/5xx is `application/problem+json`:
`{ type, title, status, detail, instance, code, ...extra }`. **Branch on `code`**,
not on `detail` prose. Common codes:

| code | meaning | attach to |
|---|---|---|
| `unknown-column` | a named column doesn't exist (carries `available: [...]`) | the field/picker |
| `operator-type-mismatch` | e.g. a string op on a numeric column | the filter row |
| `invalid-filter-value` | a numeric filter op got a non-numeric value | the value input |
| `invalid-query` | the engine rejected the query at execution (e.g. a bad regex) | the filter/query editor |
| `duplicate-alias` | two aggregations share one output name (ambiguous totals) | the alias input |
| `invalid-cursor` | cursor doesn't match this version/spec | reset paging |
| `invalid-rule-shape` | quality rule config is invalid | the rule form |
| `sheet-selection-required` | a multi-sheet op needs an explicit sheet | show the sheet picker |
| `sensitive-data-restricted` | caller lacks elevated access to sensitive data | show masked/blocked state |
| `run-not-completed` / `run-has-no-artifact` / `run-artifact-missing` / `run-source-version-missing` | publish refusals, distinct | the publish dialog |
| `dataset-name-taken` | publish target name collides | the name field |
| `relationship-has-dependents` | delete blocked by a saved join (carries `attached`) | confirm dialog |
| `invalid-step` | a transform step config can't run (bad regex/date format) | the step |
| `select-only` / `invalid-sql` | the SQL tool is read-only / query failed | the SQL editor |

Note: HTTP-status default codes use underscores (`not_found`, `conflict`,
`unprocessable_entity`); domain codes use hyphens (`unknown-column`). Handle both.

## Column masking (real control, not display)

A column marked sensitive in the data dictionary comes back **masked** (`***`, or
shape-preserving like `a***@***.com`) to any caller without
`dataset:read_sensitive`. `QueryPage.masked_columns` / response `masked_columns`
names them. The raw file and derived artifacts are also gated: `/download`,
`/samples/{f}` and `/samples/{f}/data` return `403 sensitive-data-restricted`
for a non-elevated caller on a sensitive-declaring dataset. Render masked cells
distinctly; don't assume you can re-derive raw values.

## Standing invariants (design, not bugs)

- **Cross-tenant reads return 404, never 403** — existence is hidden on purpose.
  A "not found" may mean "exists but not yours". Don't surface "access denied".
- **Versions are immutable.** New data = a new version. Tags point at a whole
  version; promote/rollback move a tag. Tag names are used as URL path segments,
  so `/`, `\` and whitespace are rejected at creation (422).
- **Multi-sheet workbooks require an explicit sheet** for per-sheet ops; the API
  refuses to guess (`sheet-selection-required`).
- **Sheet names resolve across a confirmed rename** — pass the current name.
- **Control plane vs data plane**: Postgres holds identity/config/counts;
  row-shaped data (query/aggregate/pivot/diff/export outputs) lives in the object
  store as `/samples/{filename}` artifacts.
- **Reads vs writes**: read routes need `DATASET_READ`; anything that writes
  (including `/joins/execute`) needs `DATASET_WRITE`.

## The core flows (each is exercised by a `web/` tab)

1. **Upload** → `POST /upload` (multipart `file`, `?sync=true`, optional
   `include_sheets`). Returns `{ dataset_id, version_id, row_count, ... }`.
2. **Catalog** → `GET /datasets?q=&documentation=&limit=` (`Page<DatasetInfo>`).
3. **Explore** → `GET /datasets/{id}/versions`, `.../versions/{n}/sheets` (has
   `columns` + `preview`), then `POST .../versions/{n}/sheets/{sheet}/query`
   (`QuerySpec`: `columns?`, `filters`, `sort`, `search?`, `cursor?`, `limit?`).
   Column stats: `GET .../versions/{n}/sheets/{sheet}/columns/{col}`.
4. **Quality** → `GET/POST /datasets/{id}/rules`, `POST .../versions/{n}/validate`,
   `GET .../validations` / `/validations/{run_id}`.
5. **Versions/diff** → `GET .../versions/{a}/diff/{b}` (workbook),
   `.../sheets/{s}/diff/{b}` (schema), `POST .../sheets/{s}/row-diff/{b}` (rows,
   needs a unique key or 409). Tags: `PUT .../tags`, `POST .../tags/{t}/promote`.
6. **Analytics** → `POST /aggregate`, `POST /pivot`; save as
   `POST /datasets/{id}/analytics`, run, and `POST .../runs/{run_id}/publish`.
   Charts: `POST /datasets/{id}/charts/{chart}/render`.
7. **Relationships** → `GET/POST /datasets/{id}/relationships`, `/seed`,
   `/suggest`, `/{rid}/confirm|reject`; joins `POST /joins/preview` (forecast) →
   `/joins/execute` (write) → `/joins/{run_id}/publish`. An unreviewed edge
   can't be joined.
8. **Transform** → `POST .../transformations/compile` (validate, no write) →
   `POST .../transformations` (save) → `/preview` (dry run) → `/run` → publish.
9. **Library** → saved views (`/views`, `/views/{id}/run`), SQL console
   (`POST .../versions/{n}/sql`, read-only), lineage (`GET .../lineage/graph`).

## Known minor inconsistencies (harmless; documented so you're not surprised)

- Most `DELETE`s return `204` with no body; `DELETE /datasets/{id}` and
  `DELETE .../tags/{t}` return `200` with `{ success, message }`. A generic
  delete handler should tolerate both.
- Six data_accelerator POST responses carry a vestigial always-`true` `success`
  field; other POSTs don't. Rely on HTTP status, not this field.
- Timestamps: most entities emit Postgres `::text` (`2026-08-09 06:37:45+00`);
  `JobOut.*` emits ISO-8601. `new Date(...)` parses both.
