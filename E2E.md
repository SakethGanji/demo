# E2E — Playwright, UI → backend, on an isolated stack

The studio's end-to-end suite implements the scenarios of `UI-DESIGN.md` §12.4 (T1–T45 plus T27b).
Every scenario drives the real studio in Chromium against a real engine and asserts **backend state
through the API**, not just UI text.

## Run

```bash
cd apps/workflow-studio
npm run test:e2e                                   # full suite (also: npm run e2e)
npx playwright test -c e2e -g T14                  # one scenario (every scenario seeds its own state)
KEEP_E2E=1 npx playwright test -c e2e -g T14       # keep the stack (engine, Vite, DB) for debugging
npm run test:unit                                  # vitest unit tests (contrast, line diff)
npx playwright test -c e2e/capture.config.ts        # final screenshots, same isolated stack
```

Prerequisites: Postgres on `localhost:5433` (user/password `workflow`, with permission to create test databases), the engine venv
(`apps/workflow-engine/venv`), `esbuild` and Node on `PATH`, installed studio dependencies (`npm ci`), `/usr/bin/bwrap`, and the pinned Playwright
Chromium (`npx playwright install chromium`; `/usr/bin/chromium` is used with a warning if missing).
Publishing compiles Tailwind utilities using the lockfile-pinned `tailwindcss-v3` compiler; the
published page serves the resulting CSS instead of loading the Tailwind development CDN.
App-supplied Tailwind configuration is never executed. Bubblewrap must be allowed to create
user namespaces; run in the supported cloud execution context if an outer command sandbox prevents it.

## What the global setup builds (`e2e/global-setup.ts`)

| Piece | How | Guard |
|---|---|---|
| Database | `apps/workflow-engine/tests/e2e_stack.py up` creates `workflows_test_e2e_<8hex>` and applies the migrations | name must match `^workflows_test_e2e_`; the `workflows` DB is refused |
| Upstream | a Node HTTP server on `127.0.0.1:<free>`: `/v1/balance`, `/v1/leak` (echoes the Authorization value in body, header and a 500 text), `/__log` | – |
| Engine | `uvicorn src.main:app` on `127.0.0.1:<free>` with **only** an allow-listed environment (no inherited env, fresh tmp cwd, `WORKFLOW_DISABLE_DOTENV=1`, fake LLM, no provider keys) | port ≠ 8000; `GET /api/_test/health` must return `{fake_llm: true, db: <name>}`; only `proj_default` may exist |
| Studio | its own Vite on `127.0.0.1:<free ≥5180>` with `VITE_WORKFLOW_API_URL` = the engine | port ≠ 5173 |

The live `:8000` engine, the `:5173` studio and the `workflows` database are never touched.
Teardown fails the run if the fake LLM answered any call with no matching rule (`rule_id: null`),
stops both process groups and drops the DB (unless `KEEP_E2E=1`).

The fake LLM (`apps/workflow-engine/src/engine/fake_llm.py`) only activates in an explicit test
process (test mode + `workflows_test_` DB + loopback bind + a ≥32-char token + dotenv off). Its
pinned script is `e2e/fixtures/llm-script.json`; module and app fixtures live in
`e2e/fixtures/modules/*.py` and `e2e/fixtures/apps/*.tsx`.

## Policies enforced on every scenario (`e2e/fixtures/test.ts`)

- **Network**: any 4xx/5xx from the engine, the studio or a published page fails the test unless the
  step declared it with `net.expect([{method, url, status}], async () => …)`; a declared status that
  never happened also fails.
- **Console**: any `console.error`, `console.warn`, `pageerror` or `Warning:` in the studio, the
  preview iframe or a published app fails the test. The allow-list `e2e/console-allow.ts` is empty.
  (The browser's own "Failed to load resource" line for a 4xx/5xx is owned by the network policy.)
- **Layout**: `assertNoClipping` runs at the end of every UI scenario: no horizontal overflow in any
  `[data-testid$="-panel"]` (unless `data-scroll-x="allowed"`), and every visible button/tab lies
  inside its panel.
- **Independence**: `workers: 1`; every scenario seeds its own project (`e2e-<test>-<rand>`) through
  the API (`e2e/fixtures/seed.ts`). Fonts are answered locally (fallback system font).

## Files

```
apps/workflow-studio/e2e/
  playwright.config.ts  global-setup.ts  global-teardown.ts  console-allow.ts
  fixtures/  test.ts (stack, API client, policies)  seed.ts  editor.ts  layout.ts
             llm-script.json  modules/*.py  apps/*.tsx
  tests/     01-shell  02-backend-manual  03-generate  04-data-secrets-runs
             05-builder  06-publish-release-settings  07-workflows-responsive  08-release-settings
  capture.config.ts  capture.spec.ts               # screenshots/ui-built/ (not committed)
```

Results: `e2e/.stack/results.json` (JSON reporter) and traces for failures in `e2e/.stack/test-output/`.

## UI completion verification (2026-10-15)

All 46 required scenario IDs are implemented. There are 47 test cases because T45 runs at two sizes.
T29/T30 verify snapshot/promotion and rollback through the release sheet while a published browser
keeps its app token. T36 covers rename/republish, project-secret scope, one-time API keys, revocation,
and inline nonempty-project deletion errors. AI calls in this suite use the gated fake provider;
the engine, database, sandbox, published runtime and browser requests are real. Live Gemini checks
are a separate follow-up and are not claimed by these results.

Corrections to test assumptions, with the original behavioral assertions retained:

- T22 checks the nested checkbox; its test id belongs to a label with no checked-state attribute.
- T24 reads the browser's SSE stream with the existing fetch tee, since Playwright cannot reliably
  retrieve a streamed response body after completion.
- T25 uses the secret form's scoped ids, supplies its required host binding, and checks the URL
  input's value rather than its empty text content.
- T27 pauses browser timers while server time advances, so proactive refresh cannot conceal the
  expired-token → one 401 → refresh → retry path. Normal proactive refresh remains enabled.
- T7 counts persisted writes separately from `?dry_run=true` analysis requests.
- The clipping check allows controls wholly below the fold of a vertically scrolling panel;
  partial clipping and horizontal overflow still fail.

The final capture command seeds a Payments Ops project and saves the main screens, settings,
release and publish dialogs, workflow canvas, and backend response in both light and dark themes.
It uses the same strict network, console and layout fixtures as the scenario suite.

Latest passing verification by spec (full run plus the affected generation-spec rerun):

| Spec | Passed |
| --- | ---: |
| 01 shell | 4 |
| 02 backend manual | 10 |
| 03 generate | 12 |
| 04 data, secrets, runs | 5 |
| 05 builder | 5 |
| 06 publish | 5 |
| 07 workflows, responsive | 3 |
| 08 releases, settings | 3 |
| Total | 47 |

The generation-spec rerun also verifies the save/analysis race fix: saving cancels queued and
in-flight analysis and pauses it until the saved module id is installed, preventing a stale
new-module preview from reporting `409 name_taken` after a successful save.

## Live Gemini validation (2026-10-15)

Real Gemini browser testing used a separate `workflows_test_e2e_*` database, real sandbox and
real APIs, with no fake LLM or mocked application requests. Provider keys were loaded only as
environment variables from the ignored engine `.env.local`; no key is in the committed files.

- **Generate passed**, using `gemini-3.5-flash`: generated `inventory_api`, saved it, and executed
  `GET /items` successfully. Its generation self-test passed 1/1.
- **Refine passed**: added `GET /items/summary`, saved it, and executed the refined route successfully.
  Its self-tests passed 2/2; both saved route tests returned 200.
- **Create UI passed**, using `gemini-3.5-flash` with the replacement key: generated and saved
  `App.tsx`, compiled it, and rendered actual item/count/currency values from the real backend.
- **Refresh and publish passed**: after changing the backend from Blue mug/USD to Green mug/EUR,
  Refresh made two successful API calls and rendered the changed values. The published app also
  rendered Green mug, count 1, and EUR. No browser page errors were recorded.

Generate and Refine were verified in an earlier live browser run. The final Create UI run reused
that real generated/refined source in a fresh isolated database, avoiding duplicate generation
calls. It did not use mocked source or mocked APIs. Earlier attempts encountered provider quota,
high demand, and malformed tool responses; those failed attempts are not counted as passes.
An initial static-label assertion was replaced with exact rendered-value assertions and changed
backend data, so loading screens cannot satisfy the final check.

Live testing found and fixed these provider issues:

1. `WORKFLOW_APP_BUILDER_MODEL` can now override both app-builder agent tiers; unset, the existing
   Claude default remains. `WORKFLOW_MODULE_GEN_MODEL` selects the backend generation model.
2. Gemini's native model content, including opaque thought signatures, survives multi-turn tool
   calls and JSON serialization. This metadata is excluded from OpenAI requests.
3. Retry handling recognizes the google-genai SDK's numeric `code`, honors short RetryInfo quota
   windows, and surfaces quota windows longer than 60 seconds without waiting for hours.
4. Repeated errors stop after the configured limit when both agent tiers use the same model;
   switching to the same model no longer resets the error counter.

Nine focused regression cases were added; the complete engine suite passes **285 tests**.
Local live evidence is under `screenshots/live-gemini/` (not committed): `report.json`,
`03-create-ui.png`, `03b-refresh.png`, and `04-published.png`. The report includes generated source,
self-test results, successful API responses, and changed-data assertions.

For local Gemini startup, explicitly export the ignored file before starting the engine:

```bash
cd apps/workflow-engine
set -a
. ./.env.local
set +a
# The file needs GEMINI_API_KEY plus WORKFLOW_MODULE_GEN_MODEL and WORKFLOW_APP_BUILDER_MODEL.
# Choose models available to your project and with sufficient quota.
```
