# Defects found by the end-to-end test sweep

Found 2026-08-17 by ten agents writing browser tests against the live service.
Every one was found by a test expressing the CORRECT behaviour; that test is now
the regression guard. **11 defects: 7 fixed in the studio, 4 open in
analytics-service.**

## The most important one

### `cn()` silently deleted colour classes — FIXED
`tailwind-merge` resolves conflicts by class group. It knows Tailwind's own
scale, so `text-xs` is a size and `text-red-500` is a colour. It did NOT know
about the custom scale `@theme inline` adds (`text-micro`, `text-footnote`,
`text-body`…), so it guessed — and it guessed **colour**:

```
cn('text-muted-foreground', 'text-micro')  → 'text-micro'
cn('text-[var(--st-good)]', 'text-small')  → 'text-small'
```

Every component that set an ink token and then received a size through
`className` lost its colour. `Severity` rendered `error` and `warning` in the
same ink, so **INSTRUMENT rule 7 was dead on arrival**; `Status` lost the hue on
its shape, taking **rule 6** with it. Nothing errored — the classes were simply
gone, which is why it survived every visual review.

Fixed at the root in `src/shared/lib/utils.ts` by registering the scale with
`extendTailwindMerge`. Patching the components individually would have left the
hazard armed for the next one.

## Fixed in the studio

| # | Defect | Guard |
|---|---|---|
| 1 | `cn()` dropped colours (above) | `quality.spec.ts` → *severity levels differ in value* |
| 2 | `middleTruncate` cut UTF-16 surrogate pairs in half, rendering tofu the data never contained. `slice()` works on code units; an emoji is two. Now iterates code points, so `max` counts characters. | `variety.spec.ts` → *an emoji is not cut in half* |
| 3 | The query-token row stated `identity.label` — a string the seat switcher writes to localStorage once and never reconciles. After an admin changed a seat's role it went on claiming the old one **while the service masked per the new one**. Now reads the role from `/auth/me`. | `shell.spec.ts` → *the token row states the seat real role* |
| 4 | The cockpit strip presented the first 200 datasets as the tenant, discarding the response's `total`. Above the page cap it silently read 200. Now shows `total` and states what was loaded. | — |
| 5 | Saved pipelines never named their sheet: the client type declared `sheet`, the API returns `sheet_key`. Every row said "sheet not recorded". | `transform.spec.ts` → *a saved pipeline row names the sheet it reads* |
| 6 | The audit panel claimed "a cross-tenant read appears here as the 404 it was". Reads are **not** audited — except raw egress (`/download`, `/samples/{f}/data`), which is deliberate. Copy now says exactly that. | `admin.spec.ts` → *only raw-egress reads are audited* |
| 7 | Deleting a chart or view with its detail panel open logged a 404: the detail query stayed enabled while the coarse invalidate refetched the deleted id. Panel now closes first. | `library-relations.spec.ts` → *a chart is created, renamed, rendered… and deleted* |
| 8 | The missing-data explorer named a "worst column" on a sheet with zero nulls, implying an offender. Now has a zero state. | — |

## Fixed in analytics-service

All five were fixed with a failing test first, on isolated test workers. The
service suite went **2692 → 2789 passed**, no regressions. Two agents corrected
the brief I gave them, which is recorded below because the corrections are the
interesting part.

| # | Defect | What it actually was |
|---|---|---|
| A | Row diff 500 on a dtype change | Fixed by reconciling types through DuckDB's own `COALESCE` supertype rule, NOT a blanket text cast — a blanket cast would have made `BIGINT → DOUBLE` report every row as changed, trading a 500 for a wrong answer. The **key** column had the same latent 500, which nobody had reported. |
| B | `/profile` 500 on `1e308` | My brief guessed "overflow to ±inf". Wrong: `STDDEV_SAMP` **raises** `OutOfRangeException`. Two further overflows were found on the same input — histogram bin edges genuinely producing `Infinity`, and bucketing raising on a `-1e308..1e308` span. |
| C | 500s missing CORS headers | Starlette lifts the `Exception` handler out and gives it to `ServerErrorMiddleware` at depth 0 — **above** CORS. Fixed with a middleware added first so it runs innermost, which also restored `X-Request-Id`, security headers and the audit row that 500s were losing. |
| D | `dataset_usage` counting reads as writes | Fixed by declaring the effect **per route, once**, with a tripwire test diffing the table against the live route table in both directions. `/sql` and `/pivot` turned out to be writes (they persist artifacts); `/charts/{id}/render` is a read. |
| E | profile-runs vs `/profile` permissions | **This one found a real leak — see below.** |
| F | `/aggregate` with `std` | Found while fixing B. It was a **400 on valid data**, not a 500, because `OutOfRangeException` subclasses `duckdb.Error` which was already caught — so the whole request was refused. A second half: `SUM` overflow serialised to `null` silently, with nothing saying the number existed. |

### The leak (E)

Briefed as "investigate, do NOT reflexively bolt on a gate". Good thing:
**the sentinel escaped** — through **insights**, not the profile.

- `constant-column` → `evidence.value` was the verbatim cell
- `new-categories` → the values appeared in `evidence.added` **and in the message**

Critically, **a run created by an admin leaked identically to a viewer reading
it**, so the 403-vs-200 asymmetry was never the cause and gating `profile-runs`
would not have closed it. Two more found while proving it: `redact_profile`
never stripped `min_date`/`max_date` (a masked date-of-birth read back exactly),
and `GET /profile-runs/{run_id}` returned 400 for **every multi-sheet dataset,
for every caller including superusers**.

The recommendation was to leave `profile-runs` open and fix the read path —
gating it would have removed profiling from editors too, and left `/health`,
`/missing` and drift signals permanently "unknown" on any PII dataset.

## Reached the UI

A contract change is not integrated until it is visible:

- `ColumnProfile.unavailable_stats` — the column page now says a statistic
  *"has no finite value … withheld rather than rounded to something wrong"*
  instead of a bare em dash.
- `AggregateResponse.unavailable_measures` — an aggregate cell reads
  *"no finite value"* instead of blank, because a blank cell means "no rows
  matched", which is the opposite of what happened.
- `UsageResponse.reads` — reads are now their own counter.

`openapi.json` and the studio's generated types were regenerated from the fixed
app; `tests/analysis.spec.ts` proves the whole chain from a `1e308` value to the
words on screen.

## Previously open, now closed — kept for the record

### A. Row diff 500s when a compared column's dtype differs between versions
`rowdiff.py:279` builds the comparison SQL over common column NAMES without
reconciling dtypes → `ConversionException: Could not convert string 'high' to
INT64`. A type change is exactly what a diff is for, and the schema diff already
treats it as a first-class outcome. Adding a column is handled correctly.
Pinned by `versions.spec.ts` (`test.fail`).

### B. `POST /profile` 500s on a column containing `1e308`
Almost certainly a variance/std overflow to `±inf`, which is not JSON-encodable.
`1e308` alone is sufficient; `/query` returns the same value happily. One such
column takes out the column page, the analytics lens and the aggregate builder's
capability gates **for the whole sheet**. Pinned by `variety.spec.ts` (`test.fail`).

### C. 500 responses carry no CORS headers
A 400 comes back with `access-control-allow-origin: *`; a 500 does not. In a
browser that surfaces as `blocked by CORS policy` + `net::ERR_FAILED` instead of
a status, so `AnalyticsApiError` never receives a `status` or `code` and **every
5xx renders as a generic network failure** — defeating the "branch on `code`,
never on prose" contract. This one degrades the handling of every other error.

### D. `dataset_usage` counts the studio's own reads as writes
`repo.py:639` classifies any POST/PUT/PATCH/DELETE under `/datasets/{id}` as a
write, and the grid's row read is `POST …/query`. Opening a dataset increments
`writes` with nothing mutated. The library lens publishes that number.

### E. Observation — profile permissions disagree between two endpoints
`POST /datasets/{id}/versions/{v}/profile-runs` returns **200** for a viewer on a
dataset with a declared sensitive column, while `POST /profile` returns **403**.
`create_profile_runs` requires only `DATASET_READ` and never calls
`ensure_raw_access`. No leak — reads are redacted per-principal — but the two
surfaces tell one reader contradictory things, and a read-only seat triggers a
write that persists raw `top_values` into the profile JSONB.
