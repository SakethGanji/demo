# Handoff — from a proven analytics service to one UI

**Written 2026-08-10.** Two things live in this document:

1. **Part A — the record.** What the long 2026-08-08/09 session actually did to
   `apps/analytics-service`, so nobody has to re-derive it.
2. **Part B — the brief.** The next piece of work: a **datasets/analytics page
   inside `apps/workflow-studio`**, built on that app's design system. Part B is
   deliberately *analysis and open questions*, not an approved plan — the ask was
   for the next session to analyse and plan this **with** the user.

---

# Part A — the record

## A0. Where the tree stands

- Branch `feat/analytics-mcp-consolidation`, **41 commits ahead of `main`**,
  working tree clean, **nothing pushed**.
- Backend suite **2,692 tests / 0 failures**. UI regression suite **11/11** on a
  freshly seeded DB. Accessibility checks **14/14**. `tsc --noEmit` on the web
  app: clean.
- Running locally: `analytics-pg` and `analytics-minio` (Docker), the API on
  **:8001**, the reference UI dev server on **:5173**.

## A1. What the session was asked to do

Prove the service was ready for a UI to be built against it — not by reasoning
about the code, but by driving every flow the way a UI would, fixing whatever
that turned up, and leaving behind tests so the same defects can't come back.

## A2. What it did

**Two audit rounds, ~14 parallel agents, each on an isolated worker DB**
(`scripts/test_worker.sh w1..w8` — never run two default-env pytests at once).
Round one drove all eight domains over HTTP; round two drove the *built UI* in a
real browser, roughly 700 hands-on interactions, plus dedicated audits of
accessibility, masking depth, upload paths, and concurrency/volume.

**~60 verified defects fixed**, each with a failing-first test. The class that
mattered most was *silent wrong answers* — confident, wrong output a UI cannot
detect: version `size_bytes` counting only the canonical sheet, cursor paging
losing rows on tied sort keys, aggregate totals ignoring filters, a duplicate
alias producing a wrong total, diffs that couldn't span a confirmed rename,
profiling dropping empty histogram bins, pivot row totals mixing units.

**Six PII leak paths closed.** A masking-depth audit found six routes returning
**raw sensitive values to seats the policy masks** — and the policy masks *a
viewer and an editor*; only admin/owner/superuser see raw. Each bypassed masking
by never passing `principal` into the service. The widest was
`POST /datasets/{id}/transformations/compile` **with `rows`**: any viewer could
author an arbitrary pipeline over any readable dataset and dump PII. All six now
call `ensure_raw_access` (the same gate `/aggregate`, `/pivot`, `/sql` and
`/download` use), except the profile paths, which redact via `redact_profile()`.
Gating rather than masking was the right call for transform/join because a
pipeline can rename or derive a column, so masking the output by source column
name is unsound. Full detail: `apps/analytics-service/AUDIT-FINDINGS-2.md` §
"SECURITY".

**Two structural bugs** worth naming: a TUS replay could overwrite an immutable
`ready` version (fixed with a `completed` flag + 409 on post-completion PATCH),
and version-number allocation raced at ~50% under concurrency (fixed by
allocating inside the INSERT with bounded retry).

**A reference UI** at `apps/analytics-service/web/` — ~6.4k LOC of hand-written
React 19 + Vite 8 + react-router, on a typed client generated from `openapi.json`.
Catalog · Upload · Storage · Admin, plus a dataset workspace with eight tabs
(Overview, Explore, Quality, Versions, Analytics, Relationships, Transform,
Library), dataset management, and an "Act as" identity switcher for seeing RBAC
refusals and column masking from another seat.

**The first durable UI regression suite** — `web/tests/`, `npm run test:ui`, 11
browser-driven tests, one per defect that actually happened. Its rules are in
`web/tests/README.md` and they were learned the hard way here: *never hardcode a
pass* (an early check asserted a literal `true` and proved nothing — I flagged
that myself when the claim was challenged); *verify writes by re-reading*, not by
a toast; *create your own fixtures* (two tests silently depended on a viewer seat
left behind by earlier work and failed on a clean DB); *scope your selectors*.

**A clean demo.** The DB had drifted to 74 datasets / 410 teams / 8,607 audit
rows; it was reset to 2 / 1 / 6, with `scripts/seed_demo.py` committed so anyone
can reproduce it.

**`apps/analytics-mcp` deleted** (commit `65f9bde`) after verifying all 28 MCP
tools live only in `analytics-service/app/features/mcp/tools/` and the shim held
zero tool definitions. One thing was lost on purpose and should be remembered:
**the stdio transport was never ported and cannot be** — Claude Desktop/Code
launch MCP servers as stdio subprocesses, and the service mounts MCP over HTTP
only, at `/api/v1/mcp`. Restore instructions are in `HANDOFF.md`/`ARCHITECTURE.md`.

## A3. The single worst bug, because it generalises

Every `DELETE` in the UI reported failure while succeeding. The client parsed by
`content-type` before checking status — but the server stamps
`content-type: application/json` on a 204, so `res.json()` threw on the empty
body. Ten call sites, all lying. `web/src/api/client.ts` now checks status first.
The lesson that outlived the bug: **a toast is not evidence a write happened.**

## A4. Six of my "fixes" were wrong and got reverted

Each time, a pinned test said so and I backed out rather than overriding it.
Worth reading before "fixing" any of them again:

- An **empty upload is a legitimate empty export**, not an error.
- Discovery's `suggested` counts pairs *derived*, including refreshes of
  `evidence.statistical` — that is real work, not over-reporting.
- A **cancelled TUS upload keeps its dataset**: the documented retry is to
  re-upload onto the same id, and the failed version keeps its number.
- Library **multi-target publish** is deliberate.
- Aggregate `group_count` means *the returned page's* group count.

## A5. Known-open, deliberately

Listed rather than hidden; full list in `AUDIT-FINDINGS-2.md`:

- ~10 write controls are still offered to seats that can't use them. They refuse
  correctly and change nothing; the dataset header's `read-only` badge is the
  pattern to extend. **This one should be fixed properly in the new UI rather
  than ported.**
- Publish isn't idempotent under two *parallel* requests (needs a dedupe key, not
  a blanket guard). Double-submit guards are `disabled={busy}` state. No
  optimistic concurrency on dataset metadata (last write wins, silently).
- Upload's destination picker lists only the first 200 datasets.
- `useCanWrite()` means "can write *somewhere*", not "can write *here*".
- Explore's column drawer renders `top values` as `[object Object]`.

## A6. Invariants the new UI must not break

These are properties of the platform, not preferences:

- **Cross-tenant reads return 404, not 403.** Hiding existence is deliberate —
  never surface "access denied" for another team's resource.
- **Masking masks a viewer *and* an editor.** Only admin/owner/superuser see raw.
- **Control plane vs data plane.** Postgres holds identity, config, counts and
  pointers — *never* dataset cell values. Row-shaped data lives in the object
  store, registered in `artifacts`; an unregistered blob is unreachable.
- **Versions are immutable**; tags apply to a whole version; multi-tab workbooks
  **require explicit sheet selection**.
- MCP policy: **writes yes, deletes never**, and no masking-bypass layer.
- `.env` holds a **live LLM API key** — tests must patch `call_llm`.

## A7. Where to read more

| Document | What it holds |
|---|---|
| `apps/analytics-service/HANDOFF-UI-READY.md` | How to run it; what the UI covers; caveats |
| `apps/analytics-service/AUDIT-FINDINGS-2.md` | Every finding, per domain, incl. refutations |
| `apps/analytics-service/UI-INTEGRATION-GUIDE.md` | **The contract for any front end** |
| `apps/analytics-service/DEFECTS.md`, `JOURNEY-FINDINGS.md` | The earlier sweep |
| `apps/analytics-service/web/README.md`, `web/tests/README.md` | The UI and its suite |
| `apps/analytics-service/openapi.json` | Frozen spec; the typed client is generated from it |

---

# Part B — the brief: a datasets page inside `workflow-studio`

## B0. The goal, in the user's words

> "There will be one UI… I want a page for this datasets application, where you
> can kinda do anything. Right now you have upload and a couple things — we can
> really condense all of that into one page. I've set up a really beautiful
> shared [design] system in that repo; I want to make sure that's carried over.
> Keep the apps separated for now — port, and slowly connect things together.
> Focus first on building the UI, taking the UI elements."

Context: the end goal is an internal tool for a **top-four bank** — build
workflows, build datasets, and eventually make the dataset builder a
**first-class consumer of custom workflows/agents**. That last part is a reason
to put datasets *inside* the studio rather than beside it, but it is not this
piece of work.

**Scope of the next session: analyse and plan this *with the user*, then build
UI.** No backend work. No merging of services.

## B1. What `apps/workflow-studio` actually is

Measured, not assumed. 117 files, **27,166 LOC** — `features/` 19.3k (71%),
`shared/` 5.5k, `app/` 1.2k, a stray `src/components/` 1.1k.

| | `analytics-service/web` (the reference UI) | `workflow-studio` (the target) |
|---|---|---|
| React / Vite | 19 / 8 | **18 / 7** |
| Router | react-router 7 | **TanStack Router, code-based** (no file routing, no codegen) |
| Server state | hand-rolled `useAsync` | **TanStack Query v5** (`staleTime` 30s, `retry` 1) |
| Client state | component state | **zustand 4** (7 stores; `immer` is installed but **imported by zero files**) |
| Styling | hand-written CSS + custom tokens | **Tailwind 4 CSS-first** (no config file) + shadcn `base-nova` + **Base UI** |
| Toasts | custom `ToastProvider` | **sonner**, already mounted in `__root.tsx` |
| Types | `openapi-typescript` (13.3k lines, generated) | **hand-written and hand-synced** (`shared/lib/backendTypes.ts`) |
| Auth | `X-User-Id` / `X-Team-Id` | **none at all** — `team_id: 'default'` is hardcoded |

**The blunt consequence: almost none of the reference UI's presentation layer
ports.** Different router, different data layer, different styling paradigm.
What ports is the *knowledge* — the flow choreography, the endpoint sequences,
the error handling, and the list of things that bite. Plan to **re-implement the
screens on the studio's primitives, using the reference UI as an executable
spec**, not to copy files across.

### The design system, concretely

`src/index.css` (400 lines): full light + `.dark` token sets — core shadcn
colors, `--surface/--success/--warning`, `--chart-1..5`, 8 sidebar tokens, 8
shadow steps, Inter / JetBrains Mono, `--radius: 0.5rem`, plus node-kind and
sticky-note token families for the canvas.

Three things about it that will catch you out:

1. **`@theme inline` does not map everything.** `--surface`, `--success`,
   `--warning`, `--destructive-foreground` and all node/sticky tokens have **no
   Tailwind utility** — the codebase consumes them as arbitrary values
   (`bg-[var(--surface)]/80`). Only the core colors, `chart-1..5`, sidebar, fonts,
   radius and shadows are mapped.
2. **Base font size is 13px**, not 16px.
3. **`html, body, #root { overflow: hidden }`** — the app is a fixed-viewport
   shell. **A data-heavy page must own its own scroll container**, or content
   simply gets clipped.

`src/shared/components/ui/` has **23 primitives**, all Base UI (not Radix),
every one tagged `data-slot`. Only 4 use `cva`: `button` (6 variants × 8 sizes),
`badge`, `button-group`, `input-group`. Base UI uses a `render` prop for
polymorphism — there is **no `asChild`**.

### What does not exist yet, and a datasets page needs

This is the real cost of the port, and it is worth being precise about it:

| Needed | Status in studio |
|---|---|
| **Table** | **Does not exist.** There is not a single `<table>` or `role="table"` in 27k LOC. "Lists" are flex divs (`WorkflowListRow.tsx`). A dataset app is *made of* tables. |
| DataTable / `@tanstack/react-table` | Not installed |
| **Tabs** | Only `editor-tabs.tsx` — a presentational button strip you drive with your own state |
| **Pagination** | Hand-rolled inline once, in `routes/projects.tsx` |
| Popover | Absent (only `hover-card`, hover-triggered, and `dropdown-menu`) |
| Checkbox / Switch / Radio / Label | Absent as components (native inputs get `accent-color` only) |
| Card | No `ui/card.tsx` — a repeated ad-hoc class string, `floatingPanel` |
| **Charts** | **Nothing.** No recharts, no d3, no `ui/chart.tsx` |
| Form / validation | No `react-hook-form`, no `zod`; forms are hand-rolled |
| Sidebar | 8 sidebar *tokens* exist; **no sidebar component** |
| Also absent | Accordion, Avatar, Progress, Alert, Slider, Toggle, Breadcrumb, Combobox, DatePicker, Menubar, Kbd |
| Toast | ✅ sonner, wired and used at 66 call sites |

Building Table / Tabs / Pagination / Popover / Checkbox / Card as **shared**
`ui/` primitives (via `shadcn add`, which will pull matching `base-nova`/Base UI
variants) is the highest-leverage first move: they serve the whole studio, not
just datasets, which is exactly the "one UI" goal.

### The chart palette is measured, and it fails

The studio's `--chart-1..5` were run through the dataviz validator rather than
eyeballed. **Both modes FAIL as a categorical palette:**

- **Light** — `--chart-5 #c4841d` ↔ `--chart-4 #f24822`: ΔE **1.3** under
  deuteranopia (effectively the same color), and **13.9** for *normal* vision,
  below the 15 hard floor. Contrast WARN on chart-1 and chart-3 (2.74, 2.83).
- **Dark** — `--chart-2 #a78bfa` ↔ `--chart-1 #18a0fb`: ΔE **2.3** protan, **13.5**
  normal. Three of five fall outside the lightness band.

They are also only **five** slots; analytics needs eight-plus, assigned in fixed
order and never cycled. So charting needs a decision (see B4), and whatever is
chosen must be **validated with `scripts/validate_palette.js`, not judged by
eye**. The reference UI's `web/src/components/charts.tsx` already carries a
validated palette, a `MAX_CATEGORIES = 60` cap and a full table fallback — that
logic is worth porting even though its rendering is not.

### The app shell — the biggest open question

**`__root.tsx` renders no nav, no sidebar, no topbar.** It is `ThemeProvider →
ErrorBoundary → <main> → <Outlet />` plus the sonner `Toaster`, and it picks one
of two `<main>` shells by matching the route. The four routes (`/`, `/editor`,
`/builder`, `/projects`) are **full-screen islands** that each draw their own
chrome.

So there is currently **no "one UI" to add a page to** — there is a set of
independent screens. Making datasets a peer of workflows means either accepting
that pattern or introducing a real shell. That is a product decision, not a
technical one (see B4, Q1).

Adding a route is otherwise trivial and needs no codegen:

1. `src/app/routes/<name>.tsx` exporting both `createRoute({ getParentRoute: () => rootRoute, path: 'data', … })` and the page component (co-locating both is the established pattern).
2. Register it in `src/app/routes/index.ts` — import + append to `rootRoute.addChildren([...])`. Required for `<Link to="/data">` to typecheck (the `declare module Register` block).
3. Optionally add a `matchRoute` check in `__root.tsx` to get the full-screen shell; otherwise you land in the fallback branch — where `#root`'s `overflow: hidden` still applies.

### Conventions to follow

- **Copy `src/features/projects`** (1,078 LOC) as the feature template: clean
  read/write hook split (`use*s.ts` = `useQuery`, `use*Actions.ts` = imperative +
  `invalidateQueries` + toast), no stores, no structural defects. Use
  `workflow-editor` (13.6k LOC) as the reference for how folders scale. **Do not
  copy `app-builder`** — it has both `stores.ts` and `stores/`, a loose
  `panels.tsx` at the feature root, and a 2,003-line `ApiTesterPanel.tsx`.
- No `api/` folders, no `index.ts` barrels — neither exists anywhere here.
- `@/` alias across features; relative within a feature. `cn()` from
  `@/shared/lib/utils`.
- TS is strict with **`noUnusedLocals`, `noUnusedParameters`** (an unused import
  breaks the build) and **`verbatimModuleSyntax`** (type imports must be
  `import type`). `erasableSyntaxOnly` — no enums.
- Style is genuinely inconsistent and there is no Prettier config: `ui/` uses
  double quotes and no semicolons; `features/` uses single quotes with
  semicolons. Match the neighbouring file.

### Two facts to know before you start

- **`npx tsc -b` on workflow-studio is ALREADY RED** — ~8 pre-existing errors in
  `shared/components/ai-elements/prompt-input.tsx` (Base UI event-type
  incompatibilities, `openDelay`/`closeDelay` gone from PreviewCard) and one
  unused-React import in `ui/scroll-area.tsx`. This is **not** caused by anything
  in this session; the tree is untouched. Don't chase it as your own regression —
  but note you cannot use "build is green" as your check until it's fixed, and
  fixing it is probably a worthwhile 30 minutes up front.
- **`apps/workflow-engine/.env` contains live-looking API keys** (Anthropic,
  Gemini, and an encryption key), as does analytics-service's. Worth a look at
  whether those are gitignored before this repo goes anywhere near a bank.

## B2. What to port, flow by flow

The reference UI is ~6.4k LOC hand-written across 4 top-level pages and 8
dataset tabs. Every flow below is already proven end-to-end and pinned by tests;
`UI-INTEGRATION-GUIDE.md` §"The core flows" has the exact endpoint sequences.

| Reference screen | LOC | Endpoints | Notes for the port |
|---|---:|---|---|
| Catalog | 122 | `GET /datasets?q=&documentation=` | Becomes the dataset rail / picker |
| Upload | 113 | `POST /upload` (multipart, `?sync=true`, `include_sheets`) | Condense into a drop target, not a page. TUS is the resumable path |
| Explore | 292 | `versions` → `sheets` → `POST …/query` (`QuerySpec`, cursor) | The table-heavy one; needs the Table primitive first |
| Quality | 441 | `/rules`, `…/validate`, `/validations` | |
| Versions | 606 | workbook / schema / row diff; `PUT …/tags`, `promote` | Row diff needs a unique key or 409s |
| Analytics | 634 | `POST /aggregate`, `/pivot`, `…/charts/{c}/render` | Needs the charting decision |
| Relationships | 473 | `/seed`, `/suggest`, confirm/reject, join preview→execute→publish | An unreviewed edge can't be joined |
| Transform | 674 | `compile` → save → `preview` → `run` → publish | |
| Library | 628 | saved views, read-only SQL console, lineage graph | |
| Admin | 673 | teams/members, webhooks, audit, storage | Probably *not* part of "one page" |

**Gotchas that cost real time here** — each is a defect that actually happened:

- **`GET /datasets/{id}` returns the dataset's DATA preview, not catalog
  metadata.** There is no single-dataset metadata endpoint. The reference UI
  resolves name/version from the catalog list. Adding a proper metadata endpoint
  is a small, worthwhile backend change.
- **204 responses carry `content-type: application/json`.** Check status before
  content-type or every DELETE lies. Also: most DELETEs are 204, but
  `DELETE /datasets/{id}` and `…/tags/{t}` return **200 with a body**.
- **Probe existence before rendering a dataset page**, or an unknown/foreign id
  renders a phantom page (cross-tenant is 404 by design).
- A dataset can exist with **no readable version** (cancelled TUS retry) — render
  an explanatory banner, not a broken tab.
- Timestamps are Postgres `::text` except `JobOut` (ISO-8601); `new Date()` eats
  both. Six data_accelerator POSTs carry a vestigial always-true `success` —
  trust the status code.

## B3. Wiring the studio to analytics-service

Better news than expected — **the seam is already designed for this**.
`src/shared/lib/config.ts` declares a `Backends` interface with
`// analytics: string;` commented out, ready to uncomment:

```ts
interface Backends { workflow: string; /* analytics: string; */ }
const ENVIRONMENTS = { localhost: { workflow: 'http://localhost:8000' } };
```

And analytics-service is already prepared to be called cross-origin: CORS
defaults to `["*"]` (`ACCELERATOR_CORS_ORIGINS`), and `CORS_EXPOSE_HEADERS`
deliberately exposes `Content-Disposition`, `X-Request-Id` and the full TUS set
(`Location`, `Upload-Offset`, …) — without which resumable upload breaks
entirely. Someone already thought about a browser client on another origin.

Three things do **not** carry over from the studio's existing client:

1. **`apiFetch` is not good enough for analytics.** It flattens errors to
   `new Error(errorData.error || 'HTTP {status}')` — status, body and the
   problem+json **`code`** are all lost. Analytics errors are problem+json with a
   machine-readable `code` that the UI is supposed to act on. The reference
   `web/src/api/client.ts` has the right shape (`ApiError` carrying `code`, plus
   `fieldErrorText()` folding FastAPI `errors[]` into the detail) — port that.
   It also `.json()`s unconditionally, which breaks on 204.
2. **Identity.** The studio sends no auth headers at all; analytics **requires**
   `X-User-Id` (optionally `X-Team-Id`) and its whole RBAC/masking story hangs off
   it. Something must own identity — see Q3.
3. **Types.** The frozen `apps/analytics-service/openapi.json` was diffed against
   the live app during this handoff: **107 paths, 219 schemas, identical**. So
   `npx openapi-typescript ../analytics-service/openapi.json -o src/shared/lib/analyticsSchema.d.ts`
   gives a correct typed client immediately. The studio's hand-written types are
   already drifting (there's a comment in `useVariablesApi.ts` apologising for a
   backend schema quirk); don't extend that pattern to 107 more endpoints.

**Local dev has two port collisions** to resolve on day one:

- Both dev servers want **:5173** (analytics `web` is running there now).
- `workflow-engine/docker-compose.yml` maps Postgres to **:5432**, which
  `analytics-pg` already holds.

## B4. Open questions — settle these with the user before building

These are the forks where guessing wrong means rework. **Q1 is the big one.**

**Q1 — Does the studio get a real app shell, or is datasets a fifth island?**
Today every route draws its own chrome and `__root` renders no nav. "One UI"
implies a persistent shell (the `--sidebar-*` tokens exist and are unused, which
hints that was the intent). Options: (a) add a global sidebar shell and retrofit
the existing routes — most faithful to "one UI", touches existing screens;
(b) ship `/data` as another self-contained island now, extract a shell later —
lowest risk, defers the real question; (c) shell only around the non-canvas
routes. **Recommendation: (b) to start, with the page built shell-agnostic, and
(a) as an explicit, separately-agreed follow-up** — but this is the user's call
and it should be asked directly.

**Q2 — What does "one page where you can do anything" actually mean?**
The reference UI is 4 pages + 8 tabs. Candidate: a single `/data` route with a
dataset rail on the left, a header with dataset actions, and a workspace that
switches between lenses (Explore / Quality / Versions / Analytics /
Relationships / Transform / Library) — with **upload as a drop target and modal
rather than a page**, and **Admin explicitly out of scope** for the one page.
Needs the user's read on how much can collapse before it becomes a worse
experience than tabs.

**Q3 — Where does identity live?** Analytics needs `X-User-Id`; workflow-engine
has no auth and hardcodes `team_id: 'default'`. A studio-wide identity provider
(with the reference UI's "Act as" seat switcher for demos) is the honest answer,
and it is also the thing that eventually unifies the two services. Cheap
alternative: a datasets-only identity store. **This is the second seam, and the
one that matters for a bank.**

**Q4 — Direct browser → analytics-service, or proxy via workflow-engine?**
Direct is ready today (CORS + exposed headers) and keeps the apps separate as
asked. Proxying would centralise auth later but adds a hop and work in a service
we were told not to touch. **Recommendation: direct, via the `analytics` entry in
`config.ts`.**

**Q5 — Charting.** The tokens fail validation and there are only five. Options:
(a) extend to a validated 8-slot categorical scale added to `index.css` — best
for "one UI", and the reference palette in `web/src/components/charts.tsx` is
already validated; (b) install a chart library (none today) and theme it;
(c) port the reference's hand-rolled SVG charts onto Tailwind. Whatever is
chosen, **run the validator**.

**Q6 — What happens to `apps/analytics-service/web`?** Recommend keeping it
alive as the executable spec and the home of the 11-test regression suite until
the studio page reaches parity, then retiring it in one deliberate commit. The
suite's selectors are tied to its DOM, so those tests do **not** port for free —
the studio page will need its own, and the *rules* in `web/tests/README.md` are
what should carry over.

## B5. Suggested shape for the next session

1. Read this file, then `UI-INTEGRATION-GUIDE.md`, then click through the running
   reference UI (`:5173`) — it is the spec, and it is faster than reading 6.4k LOC.
2. **Ask Q1–Q6.** Do not start building until Q1 and Q2 are settled.
3. Then, in order: fix the red `tsc -b`; add the missing `ui/` primitives
   (Table first) as *shared* assets; generate the analytics typed client + a
   proper `ApiError`; decide identity; build the page one lens at a time,
   starting with Catalog + Explore because they prove the whole stack.

**Do not** merge the services, change analytics-service's contracts, or "fix"
anything in A4 without reading the pinned test first.

## B6. Kickoff prompt for the next session

Paste this verbatim to start:

---

> We're building toward **one UI** for an internal tool at a top-four bank: build
> workflows *and* build datasets, in `apps/workflow-studio`. This session is the
> first step — a **datasets/analytics page inside workflow-studio**, built on that
> app's shared design system.
>
> **Read `HANDOFF-UI-CONSOLIDATION.md` at the repo root first.** Part A is the
> record of the analytics-service work; Part B is a grounded analysis of
> workflow-studio (its stack, its design system, what's missing, what's already
> broken) and six open questions. Then skim
> `apps/analytics-service/UI-INTEGRATION-GUIDE.md` and click through the running
> reference UI on :5173 — it's the spec, and it's faster than reading its 6.4k LOC.
>
> **Before writing any component, ask me B4's six questions** — especially Q1 (does
> the studio get a real app shell, or is datasets another full-screen island?) and
> Q2 (what does "one page where you can do anything" actually collapse to?). Give me
> your recommendation on each, don't just list options. I want us to plan this
> together.
>
> Then build the UI, in this order: fix the already-red `tsc -b`; add the missing
> `ui/` primitives as **shared** assets (Table first — there isn't one anywhere in
> 27k LOC); generate a typed analytics client from
> `apps/analytics-service/openapi.json` with a real `ApiError` carrying problem+json
> `code`; settle identity (`X-User-Id`); then build lens by lens, Catalog + Explore
> first because they prove the whole stack.
>
> Constraints: **keep analytics-service and workflow-engine as separate services** —
> port UI and connect slowly. Don't change analytics-service's API contracts. Don't
> "fix" anything listed in Part A §A4 without reading the pinned test first. Carry
> over the studio's design system rather than importing the reference UI's CSS.
>
> Services: analytics API on :8001, reference UI on :5173 (frees the port for
> studio), `analytics-pg` + `analytics-minio` in Docker. Branch
> `feat/analytics-mcp-consolidation`, 42 commits, nothing pushed.

---
