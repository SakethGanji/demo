# Handoff — integrating the Instrument design into Command Studio

**Written 2026-08-16.** The design phase is done. This document is the brief for the
build phase: taking 21 finished HTML screens and making them real React inside
`apps/workflow-studio`.

Read this first, then `design-prototypes/INSTRUMENT.md` (the visual spec), then open
`design-prototypes/terminal.html` in a browser. The prototypes are the source of truth —
they are more specific than any prose here.

---

# 0. Where the tree stands

- Branch **`clean-main`**, pushed to `github.com/SakethGanji/demo`. It is a **single root
  commit** — the prior history contained two live Google API keys in
  `apps/workflow-engine` (3 commits, reachable from `main`), so it was dropped rather
  than published. The old history survives locally on `feat/analytics-mcp-consolidation`.
- **Those two keys should still be rotated.** They were never pushed, but they existed in
  a repo that may have been cloned. `AIzaSy…JaE` and `AIzaSy…zVE`, both Google.
- Verified at the root commit: `tsc -b` clean · **41/41** studio browser tests passing ·
  all 21 prototypes render at 1440×1024 with zero console errors and zero network requests.
- Running locally: `analytics-pg` + `analytics-minio` (Docker), analytics API on **:8001**,
  studio dev server on **:5174**.

---

# 1. Scope — what may and may not be touched

This was decided explicitly. Respect it.

| Area | Rule |
|---|---|
| `/editor` (workflow editor) | **Theming only.** No structural or component edits. |
| `/builder` (app builder) | **Theming only.** No structural or component edits. |
| `/` (landing/home) | **Theming only** for now. It needs design work, but that is a separate, later task. |
| `/projects` | **Theming only.** |
| `/data`, `/catalog` | **Fair game.** These are the datasets surface and the focus of the work. |
| New routes | **Preferred.** New capabilities land as new routes rather than as edits to existing pages. |

"Theming only" means: those pages pick up new token values from `index.css` and may need
minor fixes where a hardcoded colour bypasses a token. It does **not** mean redesigning
them, changing their layout, or refactoring their components.

The deliberate consequence: a global token change will visually alter the editor and
builder. That is expected and wanted. What is not wanted is touching their code.

---

# 2. What exists already (don't rebuild it)

`apps/workflow-studio` already ships a working datasets surface, built earlier in this
session and covered by tests:

- **`/data`** — a persistent data grid with **seven swappable lenses** (Overview, Quality,
  Versions, Analytics, Relations, Transform, Library), a dataset rail, an upload dialog,
  and a studio-wide identity/seat switcher.
- **`/catalog`** — the dataset catalog with search, server-side filters and sorting.
- **`src/shared/lib/analyticsClient.ts`** — a typed client with a real `AnalyticsApiError`
  carrying the problem+json `code`. Branch on `code`, never on prose.
- **`src/shared/lib/identity.ts`** — studio-wide identity (`X-User-Id`), the seam the whole
  RBAC/masking story hangs off.
- **`tests/`** — **41 real-browser tests** against the live API. `npm run test:ui`.
  Read `tests/README.md` before adding any: never hardcode a pass, verify writes by
  re-reading, seed your own fixtures, scope selectors, console errors fail the test.

The lenses are functional but shallow compared to the prototypes. The build work is
mostly *deepening* them and adding the capabilities that have no React at all.

---

# 3. The design system

`design-prototypes/INSTRUMENT.md` is the spec. Its eight rules matter more than its token
values, because the rules are what keep it from decaying back into a generic dark theme:

1. **The accent is a MEANING, not a count.** Cyan marks *scope and liveness* — "what am I
   looking at, and is it current?" That lands at 3–4 uses per screen **regardless of
   density**. Repeated active state must never take the accent; it becomes a texture.
   This was proven the hard way on the densest screen.
2. The **primary action is near-white**, not the accent — it wins on value, preserving budget.
3. **Delete ~90% of borders**; group by elevation and space. Rules return only at a
   horizontal zone boundary, in a heterogeneous table, or where stacked prose wraps.
4. **Numbers are the hero** — large figures over recessive eyebrows, tabular-nums.
5. **Sentence-case headings.** Table headers are lowercase mono (identifiers, not shouting).
6. **Status = shape + hue + word**, never hue alone. Five shapes for five states.
7. **Severity is typographic, not coloured** — it is a property, not a state. This frees the
   reserved palette for actual status.
8. **Cards earn a surface, not a border**, and only for heterogeneous ranked units.
   **Never nest a container inside a container** — that was the loudest generated-looking tell.

Plus, learned across 10 independent restyles: **magnitude uses the neutral ramp, identity
uses categorical hue.** Most single-series charts need no categorical colour at all.

### Token integration

`apps/workflow-studio/src/index.css` currently holds the old shadcn token set plus
`--viz-1..8`. The Instrument ramp needs to land there. Two viable approaches:

- **Alias layer (what the prototypes did):** keep the existing token names and re-point them
  at the graphite ramp. Lowest risk — every existing component inherits the new language
  without edits, which is exactly what "theming only" for the untouchable pages requires.
- **Parallel set:** add Instrument tokens under new names and adopt them per component.
  Safer for the editor/builder, but leaves the app looking like two products.

**Recommend the alias layer.** It is the only approach that themes `/editor` and `/builder`
without touching their code.

One real fix already landed in `index.css`: `--viz-6` was byte-identical in light and dark
despite a comment claiming otherwise, and collided with the reserved `--viz-good` at 1° of
hue. It is now `#545a00` light / `#747c04` dark. **A light mode for Instrument does not
exist yet** and must be *selected* from the ramps, not derived by inversion.

---

# 4. The 21 prototypes, and what each is for

Sixteen of these are the datasets surface. Five are the rest of Command Studio.

| Prototype | Maps to | Status in React |
|---|---|---|
| `terminal.html` | `/data` cockpit | exists, shallow |
| `terminal-cmdk.html` | ⌘K command palette | **none** |
| `terminal-query.html` | query + 36-operator filter builder | **none** |
| `terminal-analytics.html` | Analytics lens | exists, shallow |
| `terminal-aggregate.html` | full aggregate builder | **none** |
| `terminal-pivot.html` | pivot + SQL console | **none** |
| `terminal-quality.html` | Quality lens + insights | exists, shallow |
| `terminal-column.html` | column deep-dive + correlations | **none** |
| `terminal-versions.html` | Versions lens, diff, lineage | exists, shallow |
| `terminal-relations.html` | Relations lens, joins | exists, shallow |
| `terminal-transform.html` | Transform lens | exists, shallow |
| `terminal-sampling.html` | sampling studio | **none** |
| `terminal-library.html` | Library lens + catalog | exists, shallow |
| `terminal-admin.html` | admin/governance console | **none** |
| `terminal-runs.html` | runs/jobs monitor | **none** |
| `terminal-ingest.html` | upload incl. TUS resumable | dialog only |
| `terminal-adaptive.html` | arbitrary-data behaviour (R1–R11) | **none** |
| `terminal-shapes.html` | the shape-conformance bench (diagnostic) | n/a |
| `terminal-workflows.html` | `/projects` | exists — **theming only** |
| `terminal-editor.html` | `/editor` | exists — **theming only** |
| `terminal-appbuilder.html` | `/builder` | exists — **theming only** |

---

# 5. Build order

**Phase 1 — foundation.** Nothing good happens before this.
1. Land the Instrument tokens in `index.css` via the alias layer. Verify `/editor`,
   `/builder`, `/`, `/projects` still render sanely — that is the whole theming-only contract.
2. Build the **shared `ui/` primitives that do not exist**: Table (there is not a single
   `<table>` in 27k LOC and a dataset app is made of tables), Tabs, Card, Popover, Checkbox,
   command palette, and chart primitives. These serve the entire studio, not just datasets.
3. Decide the **app shell** (the long-deferred B4 Q1). Every prototype assumes one.

**Phase 2 — deepen the existing lenses** against their prototypes, in this order:
Quality → Versions → Transform → Relations → Library. Each has a working shell already.

**Phase 3 — the capabilities with no React**: query/filter builder first (it is the most-used
control in the product and it does not exist), then aggregate, column deep-dive, pivot/SQL,
sampling, ingest/TUS, runs, admin.

**Phase 4 — R1–R11**, the shape-adaptation rules. This is what makes the product keep its
"upload any tabular data" promise. See §6.

---

# 6. R1–R11 — the thing that matters most and is easiest to skip

`terminal-shapes.html` is a diagnostic bench: it runs the cockpit against 7 pathological
dataset shapes and found **24 baked-in shape assumptions, 9 of which break outright.**
`terminal-adaptive.html` implements the fixes. The rules are documented in both files.

The worst finding, and the reason this is not cosmetic: **`sum()` on a mixed-type column
silently drops the unparsed rows and prints a confident total.** That is the
silent-wrong-answer class the backend spent an entire audit eliminating, reintroduced at
the presentation layer. R11 (coverage denominators on every statistic) fixes it.

Others worth knowing: the grid crushes rather than scrolls above ~30 columns; a
100%-distinct column currently gets *no profile card at all* (a column with no card reads
as a column with no problem); zero rows produces `width:NaN%`; duplicate column names are
indistinguishable after truncation.

---

# 7. Backend facts that will bite you

All verified against `apps/analytics-service/openapi.json` (107 paths, 219 schemas).

- **Paging is cursor-based**, not offset. `QuerySpec` returns `next_cursor`. A numbered
  pager cannot be built. `total` *is* returned, so "1–18 of 41,908" is honest; page jumps
  are not. (Six prototypes had this wrong and were corrected.)
- **The filter DSL has 36 operators**, not 40. `istartswith`, `this_month` and `outlier`
  do not exist.
- **204 responses carry `content-type: application/json`.** Check status before
  content-type or every DELETE lies. Most DELETEs are 204, but `DELETE /datasets/{id}`
  and `…/tags/{t}` return **200 with a body**.
- **`GET /datasets/{id}` returns the dataset's DATA preview, not catalog metadata.** There
  is no single-dataset metadata endpoint; resolve name/version from the catalog list.
- **Cross-tenant reads return 404, never 403.** Never surface "access denied" for another
  team's resource.
- **Masking is driven only by column-level `sensitivity`** in the data dictionary, which is
  **human-declared — nothing is auto-detected.** Dataset-level `classification` is a
  catalog label that enforces **nothing**. Do not conflate them; the prototypes deliberately
  render them as two visually distinct things.
- **Masking masks a viewer *and* an editor.** Only admin/owner/superuser see raw.
- **Filtering or sorting on a masked column is refused** (`400 sensitive-column-not-filterable`)
  because a steerable COUNT is a binary search. Disable those controls client-side.
- **The only quality gate in the entire system is tag promotion.** Publish and upload are
  ungated; warnings never block.
- **Profiling is not automatic on upload.** A fresh dataset has no profile, so several health
  dimensions read `unknown` until someone runs one. Ship an explicit affordance.
- **There is no scheduler/cron in the service.** Nothing schedules `artifact_gc`.
- **Only 4 of 8 job types have worker handlers**; the rest run in-request and leave a job row
  as an execution record. `?sync=` defaults to `true` where it exists.
- **AI/LLM features were built and deliberately removed** from analytics-service. Do not
  reintroduce an AI lens on datasets. (The editor's and builder's LLM features are
  workflow-engine's and are legitimate.)
- Errors are `application/problem+json` — **branch on `code`**, never prose. Domain codes are
  hyphenated (`unknown-column`); HTTP defaults are underscored (`not_found`).

---

# 8. Studio conventions

- Copy `src/features/projects` as the feature template: `use*s.ts` = queries,
  `use*Actions.ts` = mutations + `invalidateQueries` + toast. **Do not copy `app-builder`.**
- No `api/` folders, no `index.ts` barrels — neither exists anywhere.
- `@/` alias across features; relative within a feature. `cn()` from `@/shared/lib/utils`.
- TS is strict with `noUnusedLocals`, `noUnusedParameters` (an unused import breaks the
  build), `verbatimModuleSyntax` (type imports must be `import type`), `erasableSyntaxOnly`
  (no enums).
- Base font size is **13px**, and `html, body, #root { overflow: hidden }` — a data-heavy
  page **must own its own scroll container** or content is silently clipped.
- Base UI, not Radix. There is **no `asChild`** — polymorphism is via a `render` prop.
- Style is inconsistent and there is no Prettier config: `ui/` uses double quotes and no
  semicolons; `features/` uses single quotes with semicolons. Match the neighbouring file.

---

# 9. Known-open

1. **R8 and R9 are claimed but not drawn** — the one-column and zero-row dataset states are
   named in `terminal-adaptive.html`'s register without being rendered.
2. **No light mode** for Instrument. Must be selected from the ramps, not inverted.
3. **No landing page design.** Deferred deliberately; the home page needs work but was
   ruled out of scope for now.
4. **Redundant prototypes** worth deleting: `terminal-refined-a.html` and
   `terminal-quality-instrument.html` are superseded (their language now lives in the whole
   set). `terminal-refined-b/c.html`, `executive.html`, `workbench.html` are the rejected
   direction explorations.
5. **The MCP server (27 tools) has no UI.** Arguably fine — it is an agent interface — but an
   admin view of which tools are exposed (writes yes, deletes never) would be reasonable.

---

# 10. Kickoff prompt for the next session

> We're integrating a finished design into `apps/workflow-studio`. **Read
> `HANDOFF-INSTRUMENT-INTEGRATION.md` at the repo root first**, then
> `design-prototypes/INSTRUMENT.md`, then open `design-prototypes/terminal.html` and
> `terminal-adaptive.html` in a browser — the 21 prototypes are the design source of truth
> and they are more specific than any prose.
>
> **Scope constraint, and it is firm:** do not modify `/editor`, `/builder`, `/` or
> `/projects` beyond theming. They pick up new token values and that is all — no layout
> changes, no component refactors. `/data` and `/catalog` are the focus and are fair game.
> New capabilities land as new routes.
>
> Start with Phase 1: land the Instrument tokens in `index.css` via an alias layer (so the
> untouchable pages re-theme without code edits), then build the missing shared `ui/`
> primitives — Table first, since there isn't a single `<table>` in 27k LOC and a dataset app
> is made of tables. Verify `npm run test:ui` stays green (41 tests, needs the API on :8001
> and Docker infra up) and `npx tsc -b` stays clean throughout.
>
> Backend gotchas are in §7 and they will cost you real time if skipped — especially:
> paging is cursor-based not offset, masking is driven only by human-declared column
> `sensitivity` (dataset `classification` enforces nothing), and cross-tenant reads are 404
> not 403.
