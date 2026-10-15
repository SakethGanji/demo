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
```

Prerequisites: Postgres on `localhost:5433` (user/password `workflow`), the engine venv
(`apps/workflow-engine/venv`), `esbuild` on `PATH`, `/usr/bin/bwrap`, and the pinned Playwright
Chromium (`npx playwright install chromium`; `/usr/bin/chromium` is used with a warning if missing).

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
             05-builder  06-publish-release-settings  07-workflows-responsive
```

Results: `e2e/.stack/results.json` (JSON reporter) and traces for failures in `e2e/.stack/test-output/`.
