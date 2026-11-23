# test-builder: AI browser test builder (prototype)

This is a standalone first slice of the test-automation feature for a bank QA team. An agent explores a web app
with Playwright and Chromium and turns a plain-language goal into a readable Playwright Test spec. The spec then
runs with full evidence: trace, HAR, video, JUnit and a hashed `evidence.json`. Safety policy is enforced by the
builder, never by trusting the model.

It is not integrated with the workflow engine or the studio yet.

## Quickstart

Requires Node 23.6 or later. TypeScript runs natively, with no build step.

```bash
cd apps/test-builder
npm install                      # @playwright/test 1.62.1 uses the cached chromium-1234 build, so nothing is downloaded

# 1. Start the demo target app (http://127.0.0.1:4477). Default login: teller01 / Sup3r-Secret-Pa55!
node demo-app/server.ts &

# 2. Credentials come only from env. Names are mapped in targets/demo.json.
export TB_DEMO_USER=teller01 TB_DEMO_PASSWORD='Sup3r-Secret-Pa55!'

# 3. Explore with the scripted planner (no LLM needed). This writes tests/generated/<slug>.spec.ts and a session log.
node src/cli.ts explore --target targets/demo.json --script goals/login-accounts.json
node src/cli.ts explore --target targets/demo.json --script goals/add-payee.json
node src/cli.ts explore --target targets/demo.json --script goals/invalid-payee.json
node src/cli.ts explore --target targets/demo.json --script goals/dangerous-transfer.json   # shows policy blocks

# 4. Explore with the LLM planner (OpenAI-compatible or Gemini).
export TB_LLM_PROVIDER=gemini TB_LLM_MODEL=gemini-3.8-flash TB_LLM_API_KEY=...   # TB_LLM_BASE_URL is optional
node src/cli.ts explore --target targets/demo.json --goal "Log in and verify the accounts table shows 3 accounts"

# 5. Run with evidence, or apply the determinism gate (3/3 passes with no AI).
node src/cli.ts run tests/generated/login-shows-accounts.spec.ts
node src/cli.ts run tests                       # a whole directory
node src/cli.ts verify tests/generated/add-payee-success.spec.ts

# 6. API mode: generate a spec from OpenAPI.
node src/cli.ts explore-api --openapi demo-app/openapi.json   # or --openapi http://127.0.0.1:4477/openapi.json
node src/cli.ts verify tests/generated/api-demo-bank-api.spec.ts

npx tsc --noEmit
```

Exit codes:

| Command | Exit codes |
| --- | --- |
| `explore` | 0 when the goal is done; 2 otherwise. A failed exploration writes `<slug>.draft.ts`, not a runnable spec. |
| `run` | 0 when everything passes; 1 otherwise. |
| `verify` | 0 when ACCEPTED (N/N); 1 when REJECTED. |

## Layout

```
demo-app/server.ts          demo bank: login, accounts table + search, add-payee form with validation,
                            Transfer/Delete/"Move money" buttons, off-allowlist link, JSON API, /api/_audit
demo-app/public/*           HTML/JS for the demo (accessible roles and labels throughout)
demo-app/openapi.json       OpenAPI 3 description of the demo API
targets/demo.json           target config: baseUrl, allowedHosts, forbiddenActions, forbiddenRequests, credentials, budget
goals/*.json                scripted-planner step lists (role + name + action)
src/cli.ts                  explore | run | verify | explore-api
src/config.ts               target config loading; regex and request-rule compilation; credential env resolution
src/actions.ts              closed action vocabulary, strict validation, ElementRef -> Locator and -> source code
src/explore.ts              agent loop: snapshot -> planner -> policy -> execute -> record; session log
src/planners/scripted.ts    replays intended steps against the live page (no LLM)
src/planners/llm.ts         OpenAI-compatible or Gemini planner; strict-JSON single action per turn
src/policy.ts               network policy (route abort) and action policy (computed role/name check)
src/secrets.ts              scrubbing for the planner, leak scan (incl. zip entries), artifact masking
src/codegen.ts              recorded actions -> readable Playwright Test (getByRole/getByLabel/getByText, test.step)
src/fixtures.ts             run-time fixture: same network policy on page and API request, per-test HAR, secret()
src/run.ts                  spawns @playwright/test, masks, scans, writes evidence.json
src/apiExplore.ts           OpenAPI -> request-based spec (status + response shape; valid and invalid POST)
playwright.config.ts        trace on, video on, screenshot on failure, JUnit + list; output in runs/<ts>/
tests/generated/            generated specs (*.spec.ts runnable, *.draft.ts from failed explorations)
tests/policy/               hand-written self-test proving the run-time policy fixture
runs/                       all output (gitignored): explore sessions, run dirs, verify dirs
```

## Architecture

### Planner

`explore` runs a loop. Step 0 is always `goto /`. On each later step, the builder:

1. takes `page.locator('body').ariaSnapshot()`;
2. scrubs credential values out of the snapshot, replacing them with `{{key}}`;
3. asks the planner for one action;
4. validates the action against a closed vocabulary: `goto`, `click`, `fill`, `select`, `press`, `expectVisible`,
   `expectText`, `expectCount` and `done`;
5. executes the action;
6. records the outcome (`ok`, `error` or `blocked`), which is returned to the planner as history and `lastError`.

Elements are addressed only by accessible handles: `{role, name}`, `{label}` or `{text}`, with an optional `within`
scope. A handle must resolve to exactly one element, or the step fails as ambiguous.

There are two planners:

- **Scripted** (`goals/*.json`): replays intended steps. It retries a failing step up to 3 times and never retries a
  step that policy blocked.
- **LLM** (`TB_LLM_PROVIDER=openai|gemini`, `TB_LLM_MODEL`, `TB_LLM_API_KEY`, `TB_LLM_BASE_URL`): runs at
  temperature 0 and returns one JSON object per turn. If the reply is invalid, it is re-asked once with the
  validation error. The model sees placeholders such as `{{password}}`, never the values.

Only `ok` steps become the spec. Blocked and errored steps are excluded, and blocked steps are listed in the spec
header.

### Policy layer

The builder enforces policy and does not trust the planner.

- **Action policy.** Before every click, and before Enter/Space on a control, the builder reads the element's
  computed role and accessible name from the browser. It uses the element's own aria snapshot, so a planner cannot
  avoid the check by misnaming the element. It also checks the element's inner text against `forbiddenActions`.
  A match blocks the action, logs it, and returns it to the planner as an error.
- **Network policy.** `context.route('**/*')` aborts two kinds of request:
  - any host not in `allowedHosts`;
  - any request matching `forbiddenRequests`, such as `POST /api/transfer*` or `DELETE /api/*`.

  This catches dangerous actions with innocent names. In the demo, "Move money" posts to `/api/transfer/quick`.
  A step that triggered an aborted request is marked `blocked`.
- **Budget.** `maxSteps` and `maxSeconds` apply, the latter enforced mid-step as well. Exploration also stops after
  6 consecutive errors.
- **Run time.** `src/fixtures.ts` installs the same network policy on every browser context. It also wraps the
  API `request` fixture, so `request.post('/api/transfer')` rejects before anything is sent.
- **API mode.** `explore-api` skips operations that the policy forbids and records them as skipped.

### Runner

`run` spawns `@playwright/test` with `playwright.config.ts`:

- trace `on`, video `on`, and a screenshot on failure;
- a HAR per test, recorded by the fixture;
- JUnit and list reporters;
- workers 1, retries 0;
- output in `runs/<timestamp>-run/`.

`verify` runs the spec N times (default 3) as separate fresh processes, with `TB_LLM_*` stripped from the child
environment, and accepts the spec only on N/N passes. The result goes to `verify.json`.

### Evidence and secret hygiene

After every run, the runner processes the artifacts in four steps:

1. **Scan.** It scans every text-readable artifact for each secret credential: the HAR, JUnit, logs, error context,
   and every text entry inside each `trace.zip`. It checks the raw value and its URL-encoded, JSON-escaped and
   base64 forms.
2. **Mask.** Playwright records `fill()` values, DOM input values and request bodies verbatim in traces and HARs,
   so the first scan always finds the password. The runner masks those occurrences in place, rewriting zip entries
   as needed.
3. **Re-scan.** It scans again; the post-mask count must be 0. As a final check it scans `evidence.json` itself,
   and the run fails loudly if anything remains.
4. **Write evidence.** It writes `evidence.json` with:
   - the result and exit code;
   - JUnit counts;
   - start and end times;
   - the target, plus the config's SHA-256;
   - each spec with its SHA-256;
   - tooling versions: Playwright, browser build, Node and platform;
   - the before and after leak reports and the list of masked files;
   - the SHA-256 and size of every artifact.

Explore output is scrubbed before it is written: session logs, snapshots and specs contain placeholders only.

## Known limitations

See `BLOCKERS.md` for the full stress-test against a complicated bank target, with per-case outcomes, security
bypass findings, and blockers grouped by where they must be fixed. Current state:

- Masking rewrites trace zips after the run; the masked trace is now re-opened in the real trace viewer
  (`src/traceCheck.ts`, wired into `run` evidence as `maskedTraceChecks`, and `tb check-trace`), and resource
  files are renamed to their new SHA-1 so integrity holds.
- Snapshots are taken after a settle step (`src/settle.ts`) that waits for in-flight XHR/fetch, URL stability,
  DOM quiescence and `aria-busy`. `TB_SETTLE=legacy` restores the old `networkidle`-only behaviour.
- `forbiddenActions` matches on name. Write patterns carefully: `\bpay\b`, not `pay`, which would also block
  "Add payee".
- `explore-api` handles bearer-token auth (`api.tokenEndpoint`), path params (`api.pathParams`),
  GET/POST/PUT/PATCH/DELETE, `nullable` shapes and a no-auth 401 negative. It still sends only JSON bodies
  (not CSV/multipart), does not chain calls (path IDs can go stale), and assumes the first documented 4xx is a
  validation error.
- **Policy enforcement is author-time best-effort, not a hard control.** `context.route` does not see
  WebSocket traffic or 3xx redirect hops, so a WS mutation channel or a redirect to a forbidden path bypasses
  it (demonstrated in `BLOCKERS.md` §5). A real forward proxy is required to close this.
- Exploration cannot author into a **cross-origin iframe** or a **closed shadow root** (not in the a11y
  snapshot); use a hand-written `frameLocator`/handle spec. Mutating specs must be `tb approve`d to run.
- Generated assertions are only as good as the planner's `expect*` steps. A spec with no assertions gets a
  warning in its header.
