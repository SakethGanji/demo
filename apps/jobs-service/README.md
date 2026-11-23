# jobs-service

The heavy-work service that sits beside Command Studio. It runs browser and API test runs, AI triage of failed
cases, and goal-driven exploration. It listens on `127.0.0.1:8095` and implements section 1 of the QA-on-Studio
contract.

It reuses `apps/test-builder/src` by import rather than copying it:

| Module | What it provides |
| --- | --- |
| `actions.ts` | Step vocabulary and validation |
| `policy.ts` | `installNetworkPolicy`, `checkElement`, `checkRequest` |
| `explore.ts` | The step executor `execute` (now exported), plus the explore loop |
| `settle.ts` | Page settling |
| `secrets.ts` | Scrubbing, masking (including inside trace zips) and leak scanning |
| `planners/llm.ts` | The Gemini/OpenAI planner and its config |
| `totp.ts` | One-time codes |

`@playwright/test` is linked to test-builder's copy (`file:` dependency). Both packages then share one Playwright
instance, because Playwright refuses to load twice in a single process.

## Run

```bash
cd apps/jobs-service
npm install                                  # links @playwright/test from ../test-builder (chromium-1234 cache)
JOBS_SERVICE_TOKEN=change-me npm start       # production-style: token required
JOBS_DEV=1 npm start                         # dev: token defaults to "dev-jobs-token"
npm run dev                                  # JOBS_DEV=1 with --watch
npm run typecheck
npm test                                     # unit tests: triage evidence + pause/step gate
npm run smoke                                # end-to-end proof against bank-app (see below)
```

### Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `JOBS_SERVICE_TOKEN` | (required unless `JOBS_DEV=1`) | Bearer token for every route except `/health` |
| `JOBS_DEV` | unset | `1` allows the dev token `dev-jobs-token` |
| `JOBS_PORT` / `JOBS_HOST` | `8095` / `127.0.0.1` | Listen address |
| `JOBS_MAX_RUNNING` | `2` | Jobs running at once; the rest wait in a queue |
| `JOBS_DATA_DIR` | `./data` | Job state (`job.json`, `events.jsonl`) and artifacts (gitignored) |
| `TB_LLM_PROVIDER` / `TB_LLM_MODEL` / `TB_LLM_API_KEY` / `TB_LLM_BASE_URL` | | LLM config, the same variables test-builder uses |
| `GEMINI_API_KEY` | read from `apps/workflow-engine/.env.local` | Fallback when `TB_LLM_*` is unset (model `gemini-3.8-flash`) |
| `JOBS_LLM` | | `off` forces the heuristic triage and explore |
| `JOBS_HEADED` | | `1` shows the browser |

The LLM key is never logged or returned.

## Endpoints

| Method and path | Result |
| --- | --- |
| `GET /health` | `200 {ok}`. No auth. |
| `POST /jobs` | `201 {job_id, status:"queued"}`. Returns immediately; a body error returns `400 {error}`. |
| `GET /jobs` | The 100 most recent jobs. |
| `GET /jobs/{id}` | `{job_id, type, label, status, started_at, finished_at, progress:{done,total,current_case_id,current_step}, results:[…], output?, error?}` |
| `GET /jobs/{id}/events?after=<seq>&limit=500` | `{events:[{seq,ts,case_id,type,text,data?}], next, status}` |
| `GET /jobs/{id}/frame` | `image/jpeg` of the latest frame of the active UI case, or `204` |
| `GET /jobs/{id}/artifacts` | `{artifacts:[{name, bytes, content_type, case_id}]}` |
| `GET /jobs/{id}/artifacts/{name}` | The artifact's bytes |
| `POST /jobs/{id}/cancel` | `202 {job_id, status, cancel_requested:true}` |
| `POST /jobs/{id}/control` | `{action:"pause"\|"resume"\|"step"}` -> `200 {job_id, status, action, paused, step_credits}`; `409` when the job is finished; `400` on an unknown action; `{action:"takeover"}` (or no action) -> `501 {reserved:"takeover"}` |

Every route except `/health` needs `Authorization: Bearer <token>`. A missing or wrong token gets `401`.

**Job status** is one of `queued`, `running`, `passed`, `failed`, `error` or `cancelled`. A test-run is `passed`
only if every case passed.

**Case status** is one of `queued`, `running`, `passed`, `failed`, `blocked`, `error` or `cancelled`:

- `blocked` means policy stopped a step. That step and the rest of the case did not run.
- `results[].failed_step` is `{index, step, description}`. `index` points into the case's own `steps` or `requests`.
  `-1` means the implicit opening `goto /` failed.

**Pause / step.** `control` is honoured between steps of running UI cases (a step in flight always finishes). `pause` holds
before the next step; `step` runs exactly one more step, then holds again (it also pauses a free-running job); `resume` lets
it run on. Each action emits a `log` event whose `data.control` is `paused`, `resumed`, `step`, `waiting` or `stepping`, and
`progress.paused` reports the state. The live frame keeps updating while paused.

**Event types** are `case_start`, `case_end`, `step_start`, `step_pass`, `step_fail`, `request`, `blocked` and `log`.

- A `request` event's `data` is `{method, url, status, ms?|resource_type?}`. For UI cases it covers
  xhr/fetch/document requests.
- A `blocked` event's `data` is either a network block `{method, url, reason, rule?}` or an action block
  `{index, step, rule, role, name}`.

**The live frame** comes from a CDP `Page.startScreencast` (JPEG, at most 1280×800) on each UI case's page. When
the screencast is idle for 1.5 s, a JPEG screenshot is taken every 700 ms instead. The CDP session is kept in
`job.live`, so a future "take over" can drive it.

## Request body: test-run

```jsonc
{
  "type": "test-run",
  "label": "Release 7.3 smoke",
  "target": { "base_url": "http://127.0.0.1:4590", "allowed_hosts": ["127.0.0.1:4590", "127.0.0.1:4591"] },
  "policy": {
    "forbidden_names": ["transfer", "pay", "approve", "delete", "close account"],
    "forbidden_requests": ["POST /api/v1/payments", "DELETE /api/v1/*"]
  },
  "credentials": { "username": "teller1", "password": "…", "totp": "<base32 TOTP secret>" },
  "concurrency": 2,
  "cases": [ /* UI and API cases, below */ ]
}
```

### Target

- `allowed_hosts` defaults to the host of `base_url`. Requests to any other host are aborted, in both UI and API
  cases.

### Policy

- **`forbidden_names`** are checked against the element's real computed role and accessible name, read from the
  browser, before every click and before every Enter or Space press.
  - A plain word or phrase matches case-insensitively on word boundaries: `pay` blocks "Pay now" but not
    "Payments" or "Add payee".
  - `"/regex/flags"` is used as a regex.
  - A match blocks the step: the case becomes `blocked` and a `blocked` event is logged.
- **`forbidden_requests`** take the form `"METHOD /path/glob*"`. The method can be `*` or omitted.
  - In UI cases, a matching request is aborted.
  - In API cases, it is refused before it is sent.
- API cases use `redirect: "manual"`, so a 3xx hop to a forbidden path is never followed.

### Credentials

- `credentials` is any `{key: string}` map. It is kept in memory only and never written to `job.json`.
- Every value is masked:
  - in events, results and `evidence.json`;
  - inside every artifact, including the text entries of `trace.zip`. Resources are re-hashed so the trace still
    opens in the trace viewer.
- Reference credentials as `{{key}}`, or `{{totp:key}}` for a TOTP code.
- An API value saved under a name containing token, secret, pass or key is masked the same way.

## UI step schema

Each UI case is:

```json
{ "id": "TC-204", "kind": "ui", "title": "…", "steps": [ … ] }
```

Case ids must match `[A-Za-z0-9_.-]{1,64}`.

- **Opening the app.** A case opens `base_url` + `/` first, unless its first step is a `goto`.
- **Browser context.** Each case gets a fresh browser context, so no cookies are shared between cases.
- **Timeouts.** Each step has a 5 s timeout, and the page is settled after every action.

### Elements (`target`)

Elements are addressed only by accessible handles, never by CSS or XPath:

```jsonc
{ "role": "button", "name": "Sign in" }        // role + accessible name (exact match by default)
{ "label": "Username" }                        // form field by its label
{ "text": "Welcome back" }                     // visible text
{ "role": "option", "name": "Acme", "exact": false }
{ "role": "button", "name": "Save", "within": { "role": "dialog", "name": "Edit payee" } }
```

- **Roles:** alert, button, cell, checkbox, columnheader, combobox, dialog, form, heading, img, link, list,
  listitem, main, navigation, option, radio, region, row, rowgroup, rowheader, searchbox, status, tab, table,
  tabpanel, textbox, banner, menuitem, menu, switch, group and paragraph.
- **Matching:** a handle must match exactly one element, or the step fails as ambiguous.

### Actions

The vocabulary is test-builder's. The snake_case aliases are accepted too.

| `action` | Fields | Notes |
| --- | --- | --- |
| `goto` | `url` | Path or absolute URL. Off-allowlist URLs are blocked. |
| `click` | `target` | Policy-checked |
| `fill` | `target`, `value` | `value` may use `{{key}}` |
| `select` | `target`, `option` | `option` is the option's label |
| `press` | `key`, `target?` | Enter or Space on a control is policy-checked like a click |
| `hover` | `target` | |
| `upload` | `target`, `file` | Server-side path |
| `download` | `target`, `contains?: string[]` | Asserts text in the downloaded CSV or PDF |
| `otp` | `value` (e.g. `"{{totp:totp}}"`), `within?` | Fills single-digit code boxes |
| `waitFor` / `wait_for` | `target`, `state?: "visible" \| "hidden"` | |
| `expectVisible` / `expect_visible` | `target` | |
| `expectText` / `expect_text` | `target`, `text` | Substring match |
| `expectCount` / `expect_count` | `target`, `count` | |
| `expectUrl` / `expect_url` | `url` | Substring, or `"/regex/"`. Specific to jobs-service. |

Every step also accepts `group`, a short human title that is used as a label.

### UI artifacts

| Artifact | When it is written |
| --- | --- |
| `<case>.video.webm` | Always |
| `<case>.trace.zip` | Always. Masked; it opens with `npx playwright show-trace`. |
| `<case>.final.png` | The case passed |
| `<case>.failure.png` | The case failed |
| `<case>.blocked.png` | The case was blocked |

## API request schema

Each API case is:

```json
{ "id": "API-031", "kind": "api", "title": "…", "requests": [ … ] }
```

The requests run in order, and each case has its own variables. The variables start as a copy of `credentials`.

```jsonc
{
  "name": "create payee",                         // optional label
  "method": "POST",                               // GET POST PUT PATCH DELETE HEAD OPTIONS
  "path": "/api/v1/payees",                       // relative to base_url, or absolute; templated
  "headers": { "authorization": "Bearer {{access_token}}" },   // templated
  "body": { "name": "Acme", "iban": "GB82WEST12345698765432" }, // JSON (or a string); templated deeply
  "expect": {
    "status": 201,                                // number or array; default: any status < 400
    "json_path_equals": { "$.name": "Acme", "$.items.length": 3, "$.id": "{{payee_id}}" },
    "schema": { "type": "object", "required": ["id", "name"], "properties": { "id": { "type": "string" } } }
  },
  "save": { "payee_id": "$.id", "loc": "header:location" }
}
```

- **Templating.**
  - A string that is only `"{{var}}"` keeps the variable's type, such as a number or an object.
  - An embedded `{{var}}` is stringified.
  - `{{totp:key}}` produces a current TOTP code from credential `key`.
  - An undefined variable fails the step.
- **JSONPath-lite.** Supports `$`, `.key`, `['key']`, `[n]` and a trailing `.length`.
- **Schema (simple).** Supports `type` (string or array; `integer` counts as a `number`), `required`, `properties`,
  `items`, `enum`, `minItems` and `additionalProperties: false`.
- **Artifact.** `<case>.api.json` records every exchange, masked: method, url, request body, status, ms, response
  headers and body. Request headers are not recorded.

## Triage and explore

### Triage

Request body:

```json
{ "type": "triage", "job_id": "job_…", "case_id": "TC-102" }
```

It needs the source job to still be in the service, with the failure evidence it kept for that case. The case must
have status `failed`.

`output` has this shape:

```jsonc
{ "verdict": "app_changed",            // bug | app_changed | flake | environment | test_bug
  "confidence": 0.95,
  "summary": "…",
  "evidence": [{ "kind": "failed_step|error|page|candidates|network|…", "text": "…" }],
  "proposed_fix": { "steps_before": [ …case steps… ], "steps_after": [ …with the fix… ], "explanation": "…" },
  "source": "llm:gemini:gemini-3.8-flash",      // or "heuristic"
  "heuristic": { "verdict": "app_changed", "confidence": 0.8 } }
```

**Evidence fields (always present, deterministic, secrets masked, identical with or without an LLM):**

- `dom_diff`: `{baseline:"last_pass"|"expected_vs_actual"|"n/a", baseline_job_id?, summary, expected?, actual, changes:[{kind:"renamed"|"removed"|"added", role, before, after, similarity?}], text_removed, text_added, step_changed?}`.
  With an earlier passing run of the same case id it compares the accessible tree and visible text at the same step
  (`step_changed` says the test step itself was edited). Otherwise it compares the expected element with the nearest actual one.
- `network_diff`: `[{method, path, kind:"status_changed"|"missing"|"new"|"unexpected_status"|"status_mismatch", expected, actual}]`.
- `screenshots`: `{before, after, before_job_id}`, names of artifacts of the triage job (`<case>.before.png` from the last pass, `<case>.after.png` from the failure; `before` is null without a prior pass).

Passing UI cases leave a baseline in `data/baselines/<case>.json` for this.

**What the triage input contains.** For a UI failure, the service keeps:

- the failed step and its error;
- the URL and page title;
- the accessible names on the page that have the same role, ranked by similarity;
- the aria snapshot;
- the last 25 network requests.

**The heuristic.**

- It also runs as the pre-analysis the LLM sees.
- It is used alone when no LLM is available, or when the LLM call fails.
- Its rule for a renamed element: if a missing element has a similar candidate on the page (similarity ≥ 0.6), the
  verdict is `app_changed`, with a fix that renames the target.

### Explore

Request body:

```json
{ "type": "explore",
  "target": { "base_url": "…", "allowed_hosts": ["…"] },
  "policy": { … },
  "credentials": { … },
  "goal": "Log in and verify the accounts table" }
```

**How it runs.**

- It wraps test-builder's `explore()` and `LlmPlanner`, with the same policy enforcement and scrubbing.
- The model only ever sees `{{key}}` placeholders, never credential values.

**What it returns.**

- `output` is `{steps, notes, source, status, blocked, spec_preview}`.
- `steps` contains only the steps that succeeded, in the UI step schema above, ready to save as a case.

**Without an LLM (or when it fails, e.g. 402).** Explore falls back to a deterministic walk with `source: "heuristic"`: open the
app, assert the heading, sign in (and pass MFA with `{{totp:key}}`) when credentials are given, then follow up to 3
links/tabs/buttons whose names share words with the goal (skipping policy-forbidden names), asserting each page's heading.
Every proposed step was executed against the live page. Without a goal match it proposes smoke assertions.

## curl

```bash
T='Authorization: Bearer dev-jobs-token'; J=http://127.0.0.1:8095

curl -s $J/health
curl -s -o /dev/null -w '%{http_code}\n' $J/jobs                    # 401

curl -s -H "$T" -H 'content-type: application/json' $J/jobs -d '{
  "type":"test-run","label":"demo",
  "target":{"base_url":"http://127.0.0.1:4590","allowed_hosts":["127.0.0.1:4590","127.0.0.1:4591"]},
  "policy":{"forbidden_names":["pay","approve","delete"],"forbidden_requests":["POST /api/v1/payments"]},
  "credentials":{"username":"teller1","password":"<pw>","totp":"<base32>"},
  "cases":[
    {"id":"TC-1","kind":"ui","steps":[
      {"action":"fill","target":{"label":"Username"},"value":"{{username}}"},
      {"action":"fill","target":{"label":"Password"},"value":"{{password}}"},
      {"action":"click","target":{"role":"button","name":"Sign in"}},
      {"action":"otp","value":"{{totp:totp}}"},
      {"action":"click","target":{"role":"button","name":"Verify"}},
      {"action":"expect_text","target":{"role":"cell","name":"Everyday Checking"},"text":"Everyday Checking"}]},
    {"id":"API-1","kind":"api","requests":[
      {"method":"POST","path":"/api/v1/auth/token","body":{"username":"{{username}}","password":"{{password}}","otp":"{{totp:totp}}"},
       "expect":{"status":200},"save":{"access_token":"$.access_token"}},
      {"method":"POST","path":"/api/v1/payees","headers":{"authorization":"Bearer {{access_token}}"},
       "body":{"name":"Acme","iban":"GB82WEST12345698765432"},"expect":{"status":201},"save":{"payee_id":"$.id"}},
      {"method":"GET","path":"/api/v1/payees/{{payee_id}}","headers":{"authorization":"Bearer {{access_token}}"},
       "expect":{"status":200,"json_path_equals":{"$.name":"Acme"}}}]}]}'
# -> {"job_id":"job_20261020_…","status":"queued"}

ID=job_20261020_…
curl -s -H "$T" $J/jobs/$ID
curl -s -H "$T" "$J/jobs/$ID/events?after=0"
curl -s -H "$T" $J/jobs/$ID/frame -o frame.jpg
curl -s -H "$T" $J/jobs/$ID/artifacts
curl -s -H "$T" $J/jobs/$ID/artifacts/TC-1.video.webm -o video.webm
curl -s -H "$T" -X POST $J/jobs/$ID/cancel
curl -s -H "$T" -X POST $J/jobs/$ID/control                      # 501 {reserved:"takeover"}
curl -s -H "$T" -H 'content-type: application/json' $J/jobs -d "{\"type\":\"triage\",\"job_id\":\"$ID\",\"case_id\":\"TC-1\"}"
```

## Smoke test

`npm run smoke` drives `scripts/smoke.mjs` against bank-app and the running service. It reads the test-only seed
users from `../test-builder/bank-app/lib/db.ts`. To start bank-app:

```bash
cd ../test-builder && BANK_LATENCY=100-400 node bank-app/server.ts
```

The smoke test checks:

- **Auth.** `401` without a token or with a wrong one, and `400` on an invalid step.
- **A test run with concurrency 2:**
  - 4 UI cases: pass, renamed button, forbidden "Pay now" click, and pass with `expect_url`;
  - 4 API cases: schema, chained create → get, a `401` negative, and a forbidden POST.
- **Statuses, events and frames:** case statuses, event types and seq order, and at least 3 distinct JPEG frames.
- **Artifacts:** video and trace exist. The raw bytes and every zip entry are grepped for the password and TOTP
  secret in 4 encodings. `evidence.json` hashes are compared with the downloaded bytes.
- **Triage** returns `app_changed` with a rename fix.
- **Cancel** returns `202` and the job ends `cancelled`.
- **Control** returns `501`.

## Limits (v1)

- Job state lives in memory plus disk. After a restart, finished jobs reload, but credentials are gone. A job that
  was in flight becomes `error`.
- Policy is `context.route` plus element checks. As `apps/test-builder/BLOCKERS.md` §5 documents, WebSocket
  traffic and 3xx redirect hops in the *browser* are not seen. A real forward proxy is needed before the policy can
  be treated as a hard control.
- There is a single shared Chromium, with one context per case. Case concurrency is at most 4 per job, and
  `JOBS_MAX_RUNNING` jobs run at once.
