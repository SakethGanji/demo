# Handoff — finish the studio UI + E2E (sequentially)

> Historical handoff, superseded by [UI-PROGRESS.md](UI-PROGRESS.md), [E2E.md](E2E.md), and [DEMO-READINESS.md](DEMO-READINESS.md). The unfinished-test and type-error statements below describe the earlier state.

Written 2026-10-15 ~07:50. Branch `revert-to-5684ba5`, **local commits only — never push**
(history contains live Google API keys). Do not look at other branches/tags.

## Read first (in this order)
1. `PLAN.md` — product plan (projects; UI + backend connected automatically; nodes as functions).
2. `UI-DESIGN.md` **v2.1 (approved)** — THE spec. Mockups: `design-mockups/`, `screenshots/ui-design/`.
3. `UI-PROGRESS.md` — slice status, dev stack, deviations. **Trust `git log` over it if they disagree.**
4. `AUDIT-BACKEND-FIXES.md` — audit of backend fixes; items 2–5 still open (see below).
5. `BACKEND-DESIGN.md` (§3 contract, §13 behaviour changes), `BACKEND-PROGRESS.md` (engine test how-to).
6. `UI-DESIGN-REVIEW.md` — review history (81 findings + re-reviews, all resolved in v2.1).

## Owner's direction (do not drift)
Few pages: Projects home → **Project page does most things** (secrets, backend generate/edit/save/test,
routes, data, apps, runs) → Workflows (existing canvas, secondary) → App builder.
Generate a Python backend **by prompt** using SDK functions (`studio.nodes.*`, `studio.db`, `studio.secret`,
`@api.get/post`); saving creates routes. "Create UI from this backend" opens the app builder with the
backend preselected; published apps call the project's Python backend. Modern, using OUR components/tokens.
**Everything tested end to end with Playwright, UI → backend.** After every step, a separate agent
audits for shortcuts (skipped/weakened tests, hardcoded passes, features that don't hit the backend).

## Done (committed)
- Backend phases 1–9 + single baseline migration; backend defect fixes (engine suite was 168 green).
- Studio slices 0, 1, 2, 4, 5+6a (`123f6d9`, `74bc7ad`, `2afac7c`, `f2d8ba3`, `68f8c78`).
- Engine: fake LLM test-only provider (`6b29ebe`), run-draft (`190f208`), audit #1 read-only broker (`b96b7df`).
- E2E harness + specs 01–05, 07 (`ffd28e3`).
- tsc errors 53 → 44; vitest 23.

## Remaining — do these IN ORDER, one at a time, each verified before the next
1. **Reconcile the working tree.** Sub-agents of the previous build agent left uncommitted work
   (settings/release sheets, workflows list, engine changes e.g. `src/routes/projects.py`,
   `tests/integration/test_ui_run_draft.py`). Run engine suite + `npx tsc -b` + vitest; finish or revert
   each half-done file; commit coherent pieces.
2. **Engine: `POST /api/projects/{p}/modules/generate`** per UI-DESIGN §10.1 (SSE, repair loop,
   read_only self-test, declared tables, secret-value scrub), test-first with the fake LLM.
3. **Audit items 2–5** (`AUDIT-BACKEND-FIXES.md`): release pinning for published-app workflow runs + cron;
   behavioural app-token tests (rotation on password change / private; refresh-on-401); team-secret
   redaction in python-route traces; remaining minors fixed or documented in BACKEND-DESIGN §13.
   Spec decision already made: apps with draft source but no saved version ARE versioned into releases;
   only apps with no source fail the check — make UI-DESIGN §3.8 say so.
4. **Finish UI slices 6b (settings + release sheets) and 7 (workflows list, responsive, dark mode).**
5. **E2E: write remaining specs (06, 08–46) and get all 46 green** on the isolated stack
   (`workflows_test_e2e_*` DB, engine with explicit env not `.env`, own ports). Assertions check backend
   state; console errors and unexpected 4xx/5xx fail tests. Document in `E2E.md`.
6. **Screenshots** of every main screen from the isolated stack after the flagship scenario
   → `screenshots/ui-built/` (light + dark for project page and backend editor).
7. **Final independent audit** (separate agent, read-only, no sub-agents): shortcuts, weakened/skipped
   tests, UI actions not reaching backend, live data touched, console errors ignored. Fix → re-audit until clean.
8. Report to the owner: what works, test counts (engine, vitest, E2E table), tsc count, commits, deviations.

## Ground rules learned the hard way
- **Sub-agents' completion results go to the main session, not to the agent that spawned them.**
  Don't let agents spawn sub-agents; run work sequentially or have the main session relay.
- Never run two pytest processes against the same DB; engine suite uses isolated `workflows_test_*` DBs.
- Never touch the live engine (:8000) / studio (:5173) data or the `workflows` DB from tests.
  Live DB holds a "Payments Ops" example project (orders table, `orders_api` module).
- A dev stack may be running: engine on 127.0.0.1:8710 (`workflows_test_e2e_devui`), Vite on :5190.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; `git add` only
  your files (never `screenshots/`, `test-results/`, `apps/analytics-service/web/`, `.coverage`).
- No LLM key on this machine: AI features are tested with the gated fake LLM only; say so in reports.
- Owner's screenshots page (artifact): https://claude.ai/artifact/MHUTFXcWikgVdhVZK7cZ4Z — update it with
  final screenshots (Artifact publish with that `url`; images embedded as data URIs).
