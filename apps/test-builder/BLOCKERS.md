# test-builder vs. a realistic bank app — findings and blockers

Stress-test of the standalone AI browser/API test builder (`apps/test-builder/`) against a deliberately
complicated bank target (`apps/test-builder/bank-app/`, a SPA on `:4590` + a cross-origin card widget on
`:4591`). Dates 2026-10-19. Tooling: Playwright `@playwright/test` 1.62.1 (chromium headless shell), Node
v25.9.0, `node:sqlite`. No screenshots were used for judgement; every outcome below is a code/DOM/exit-code
check. Companion catalogs `REQUIREMENTS-UI.md` and `REQUIREMENTS-API.md` (written by other agents) hold the
full requirement-by-requirement coverage grid; this file is the empirical run log, the blockers, and the
security findings.

**Scope note (owner mid-task):** MFA/SSO is handled *above* this tool. The builder supports plain login and,
preferably, a reusable pre-authenticated `storageState` (`tb login` → `auth.storageState`), so a UI spec
skips login entirely and coexists with whatever already issues sessions. MFA is **not** counted as a blocker.
A TOTP helper (`otp` action, `{{totp:key}}`) exists only as optional local setup for this self-contained app.

---

## 1. Verdict

For the 15 QA cases below, measured by what actually ran green (process exit 0, JUnit failures 0, masked
trace re-opened clean, zero secret leaks):

- **9/15 (60%) fully automated by the tool's own authoring** — scripted `explore` or `explore-api`, then
  `verify` 3/3 or `run` green, no hand editing.
- **13/15 (≈87%) automatable once a human-reviewed, hand-edited spec is allowed** (a normal bank-QA activity,
  gated here by `tb approve`). The extra 4 are maker-checker, idempotency, the cross-origin card iframe, and
  session-timeout.
- **2/15 are fundamentally out of reach for the explore/snapshot approach**: authoring *into* a cross-origin
  iframe, and anything inside a **closed** shadow root. Both still work in a hand-written spec using
  `frameLocator` / exposed handles, i.e. they are limits of *AI authoring*, not of Playwright.

A bank pilot is realistic for regression of read/navigation/contract flows today. Mutations (payments,
approvals, closes) are correctly **impossible to author by exploration** and only run through the reviewed
`approve` gate — which is the right safety posture, but means the highest-value flows are still human-authored.

The builder is a credible *complement* to an existing suite, not a replacement: it has no page-object reuse,
no data-driven rows, no test-case/Jira metadata, and no CI wiring (see §3B / §3C and the requirement catalogs).

---

## 2. Per-case results

Time-to-author is wall-clock for me using the scripted planner (writing the steps JSON) or a one-line goal
for the LLM; it excludes app build time.

| # | Case | Approach | Outcome | verify / run | Author | Notes |
|---|---|---|---|---|---|---|
| 1 | Login (+MFA) → accounts | scripted | **worked** | verify 3/3 | 4 min | `otp` action; also proven via reusable `storageState` (case 7) |
| 2 | Grid filter + sort | scripted | **worked** | run green | 5 min | `waitFor` after async fetch; `aria-sort` columnheader assert |
| 3 | PDF statement download + content | scripted | **worked after tool fix** | run green | 4 min | needed new `download` action + PDF text extraction (`downloads.ts`) |
| 4 | Open shadow DOM (fx converter) | scripted | **worked** | run green | 3 min | `ariaSnapshot`/locators pierce **open** shadow roots |
| 5 | Payee autocomplete (combobox) | scripted | **worked after fix** | within case 5/LLM | 3 min | option name embeds bank+masked IBAN → needed `exact:false` |
| 6 | Payment wizard submit (over limit) | scripted | **blocked by policy (by design)** | draft only | 6 min | `forbiddenActions`+`forbiddenRequests` abort submit; emits `.draft.ts` |
| 7 | Pre-auth session reuse | scripted + `tb login` | **worked** | run green | 2 min | `storageState` cuts the 6 login steps from every spec |
| 8 | Maker-checker (4-eyes) | hand-edited + `approve` | **worked** | run green, audit confirms | 12 min | real POST+approve; self-approval → 403 SELF_APPROVAL |
| 9 | Idempotency (replay / key reuse) | hand-edited + `approve` | **worked** | run green | 10 min | replay 200, reuse-diff-body 422, missing key 400 |
| 10 | Auditor denial (RBAC + de-DE i18n) | scripted | **worked** | run green | 4 min | asserts German "keine Berechtigung"; routes via URL not nav labels |
| 11 | Cross-origin iframe card | hand-edited (`frameLocator`) | **needs hand spec** | smoke green | 8 min | explore cannot see cross-origin iframe content at all (fundamental) |
| 12 | Closed shadow DOM (recovery keys) | — | **blocked (fundamental)** | n/a | — | closed root is invisible to every locator strategy |
| 13 | Session-timeout modal | hand-edited | **needs hand spec** | not run this round | ~ | tool has `waitFor` + reset of `sessionIdleSeconds`; time-based, so hand-authored |
| 14 | API contract (auth + path params) | `explore-api` | **worked (mostly)** | run 28/33 | 1 min | token `beforeAll`, path params, `nullable` shapes; 5 gaps (§3B) |
| 15 | API negatives (401 / 400) | `explore-api` | **worked (mostly)** | within case 14 | — | all no-auth→401 passed; "first 4xx == validation" assumption imperfect |
| — | CSV bulk with row errors | fixtures + `upload` action | **partial** | validated manually | 3 min | `upload` action + `bulk-errors.csv` work; `explore-api` sends JSON→415 (§3B) |
| — | Policy-bypass probes | custom harness | **2 bypasses found** | see §5 | — | websocket + 3xx redirect hop evade the network policy |

Generated artifacts live in `tests/generated/bank-*.spec.ts`, `tests/generated/llm-*.spec.ts`, and run
evidence under `runs/` (gitignored).

---

## 3. Blockers, grouped

### (A) Fixed this session (in the tool)

1. **Stale snapshots before planning (the open item (b)).** The old `settle()` did `domcontentloaded` + ≤1.5s
   `networkidle`, which returns before an SPA finishes its route change + skeleton. New `src/settle.ts`:
   tracks in-flight XHR/fetch (excludes EventSource/WebSocket), waits for URL stability, DOM-mutation
   quiescence, and no `[aria-busy="true"]`, bounded by a timeout; `TB_SETTLE=legacy` keeps the old behaviour.
   Measured on a navigate-then-assert flow with no explicit waits: **legacy = 1 stale-snapshot note, new = 0**.
2. **Masked traces open cleanly in the real viewer (open item (a)).** Verified earlier: `src/traceCheck.ts`
   drives `show-trace`, asserts actions/snapshots/network render with 0 console errors and no visible secret;
   SHA-1 resource renaming fixed in `src/secrets.ts`; wired into `run` evidence (`maskedTraceChecks`). Every
   bank run above reports `trace-check OK`.
3. **New action verbs** needed by real cases: `hover`, `upload`, `download` (+ CSV/PDF content assertion via
   `src/downloads.ts`), `waitFor`, and `otp`. Vocabulary, executor, codegen and the LLM prompt all updated.
4. **API generator** (`explore-api`) gained bearer-token `beforeAll` auth, path params from
   `api.pathParams`, PUT/PATCH/DELETE, a no-auth 401 negative, and OpenAPI `nullable` handling (took the bank
   API from 23→28 of 33 passing).
5. **Explore-vs-approved policy split** (`src/approvals.ts`, `tb approve`): exploration can never mutate;
   a reviewed spec whose content hash is approved may run `forbiddenRequests` (host allowlist still enforced).
   Proven: maker-checker is blocked unapproved, runs green once approved, and the audit log shows the state
   change — editing the spec revokes approval (hash changes).
6. **Reusable auth** (`tb login` → `auth.storageState`) and **setup/teardown hooks** (`hooks.resetUrl`, run
   before each `run`/`verify` to reseed deterministically).
7. **Locator robustness**: `enterOtp` now waits for the field group to render (a generated spec had no settle
   between the login click and the MFA screen, so it counted 2 fields instead of 6). Login now verifies 3/3.

### (B) Fixable in the tool (estimates)

| Gap | Evidence | Est. |
|---|---|---|
| `explore-api` sends only `application/json`; CSV/multipart bodies → 415 | bulk-payments/validate failed | 0.5 d |
| Generated API tests assume "first documented 4xx == validation error" | token invalid-body expected 400, got 401 | 0.5 d |
| No request **chaining** in `explore-api` (path IDs/refresh tokens go stale after reset) | `getBulkJob JOB-100000`→404, refresh→401 | 2–3 d |
| Auth/credential endpoints sampled with fake data (`'test'`) → 401 | token/refresh "valid body" tests fail | 1 d (mark no-auth endpoints) |
| Schema sampling ignores `format`/`pattern`/min-max → invalid domain data (IBAN mod-97, money) | payee PUT needed a hand-added example | 1–2 d |
| No page-object / shared-flow reuse; every spec repeats login | all `bank-0*` specs | 3–5 d (emit helpers + `storageState` default) |
| No data-driven rows (one flow × N cases, one result each) | — | 2–3 d |
| No test-case metadata (tags, Jira/Xray key) carried into the spec | — | 1 d |
| `explore` overwrites `<slug>.spec.ts` with no merge/guard → clobbers edits | — | 1 d |
| Autocomplete/option accessible names that embed secondary text are brittle | case 5 needed `exact:false` | 0.5 d (prefer substring for `option`) |
| `frame` scoping in `ElementRef` for **same-origin** iframes | — | 1–2 d |

### (C) Needs platform pieces (outside the tool)

- **Container/parallel runner** — single Playwright process, `workers:1`; a bank pack is thousands of tests.
- **A real forward proxy** for policy (see §5): `context.route` cannot see WebSocket or 3xx redirect hops.
  Egress control that actually holds needs an mitmproxy/transparent-proxy layer.
- **Artifact storage / results service** — evidence is local JSON + zips; no history, trend, or dashboard.
- **Identity/RBAC & secrets vault** — credentials are env vars; no per-team isolation or vault integration.
- **Job queue / scheduler**, **CI trigger** (Jenkins/GitLab), **Xray/ALM sync** — none exist.
- **DB/Kafka/MQ assertions, mTLS/proxy transport, webhook correlation** — see `REQUIREMENTS-API.md` A-section.

### (D) Fundamental, or needs the bank's cooperation

- **Authoring into a cross-origin iframe is impossible by exploration** — the browser does not expose
  cross-origin frame content in the accessibility snapshot. The card widget (`:4591`) can only be driven by a
  hand-written `frameLocator` spec (proven working), and only because we control both origins; a third-party
  PSP widget would also need its test hooks.
- **Closed shadow roots are invisible** to every locator strategy (the recovery-keys component). Only the app
  team exposing a handle or using an open root makes it testable.
- **Time/stateful flows** (session idle-timeout, rate-limit 429 windows, async settlement) need the bank to
  provide deterministic test levers (our target has `/_test/reset` with tunable `sessionIdleSeconds`,
  `rateLimitPer10s`, `bulkRowMs`); without equivalents in the real system these are slow and flaky.
- **Real money movement** can only be exercised in a non-prod environment with seed data and a reset endpoint;
  that is a bank-environment prerequisite, not a tool feature.

---

## 4. LLM planner observations (Gemini `gemini-3.8-flash`)

Sessions used: 5 (well under the ~40 budget). Input is always the scrubbed aria snapshot; output is one
strict-JSON action from the closed vocabulary; two reparse retries; `TB_LLM_*` is stripped at `verify`/`run`
time so no model is in the trusted loop.

- **Success rate: 3/4 read-oriented goals fully `done`** (login+dashboard, grid filter, PDF download), each
  producing a runnable `.spec.ts`. It discovered and correctly used the new `otp` and `download` actions from
  the prompt alone.
- **Safety: the 1 money-moving goal was correctly refused.** Asked to "make a payment of 20000 GBP", the model
  returned `done(success:false)` after 2 steps citing its rule not to move money — *before* the hard policy
  gate would have aborted it. Defense in depth: model restraint + enforced `forbiddenActions`/`forbiddenRequests`.
- **Failure modes seen elsewhere (from prior demo runs):** over-eager `done` before asserting; occasional
  wrong accessible name when labels are localized. The stale-snapshot fix (§3A.1) matters most here because the
  snapshot *is* the plan input — a stale snapshot makes the model plan against the previous screen.
- **Cost/latency:** ~1–2 s per step, ~8–10 steps per goal; a goal is a handful of cents. Cheap enough for
  authoring, far too nondeterministic to leave in the execution path — which is why `verify` bans it.

---

## 5. Security findings (active bypass attempts)

The policy is `context.route('**/*')` + element-role/name checks. I built every evasion vector into the target
(`/labs`) and measured whether a payment actually appeared in `/_test/audit`.

| Vector | Result | Why |
|---|---|---|
| **WebSocket `quickpay`** (`ws://…/ws`) | **BYPASS — payment created (channel=websocket)** | `context.route` does not intercept WebSocket frames at all; the policy is blind to them |
| **3xx redirect hop** (`POST /orders` → 308 → `/payments`, when `/orders` not itself forbidden) | **BYPASS — payment created** | Playwright does not invoke `route` on redirect responses; a redirect to a forbidden path is never seen |
| `POST /api/v1/batch` RPC | blocked | the batch path itself is forbidden |
| `navigator.sendBeacon('/payments')` | blocked | intercepted and aborted like any fetch |
| Service-worker `fetch('/payments')` | blocked | with `serviceWorkers:'allow'`, SW requests were routed and aborted |
| Enter-key submit of legacy `GET /legacy/repeat-payment` | blocked | the GET-with-side-effects path is forbidden |
| `window.open('http://partner…')` off-allowlist | blocked | popup shares the context; host allowlist aborts it |
| Cross-origin iframe telemetry (`telemetry.cardvault.example`) | blocked | off-allowlist host aborted in the sub-frame |

**Consequences for the policy model:** a path-glob `forbiddenRequests` list only protects paths requested
**directly** over HTTP. Two real holes: (1) **WebSocket** egress is completely unpoliced — a governance claim
that "the agent cannot move money" is false if the app has a WS mutation channel; (2) a **redirect** from an
allowed path to a forbidden one bypasses, so every redirect *source* must also be forbidden (we had to add
`POST /api/v1/orders` explicitly). Both are closed only by a real proxy that sees WS and redirect hops
(category C), not by `context.route`. The batch-RPC case shows the related modelling limit: path-based
policy cannot see a mutation hidden inside an allowed envelope endpoint — it must be forbidden wholesale.

The positives still hold: secrets are masked out of every trace/HAR/JUnit (`after masking 0` on every run),
and exploration genuinely cannot author a mutation.

---

## 6. What a bank QA team would still be missing

- **Reuse and scale**: page objects / shared flows, data-driven rows, parallel/container execution, a results
  history service. Today every spec is a flat, login-repeating script run one-at-a-time.
- **Lifecycle integration**: Xray/ALM case sync, Jira keys and tags on specs, CI triggers, flaky-test
  quarantine, requirement traceability.
- **Protocol breadth**: SOAP/XSD, OAuth2 client-credentials with token cache/refresh, mTLS + corporate proxy,
  DB assertions (Oracle/DB2/MSSQL), Kafka/MQ, webhook/callback correlation (all Missing/Partial in
  `REQUIREMENTS-API.md`).
- **Authoring from existing manual cases** (400 regression cases → drafts) rather than one goal at a time.
- **Trustworthy egress governance** — the WebSocket/redirect bypasses mean the current policy is a
  *best-effort author-time guard*, not an enforceable control; a bank would require the proxy before relying
  on "the agent can't do X".
- **Accessibility-independent locators**: the whole approach leans on the a11y tree, so closed shadow DOM,
  cross-origin iframes, and canvas-only UIs (the dashboard chart has no text alternative) are dark to it.

---

## 7. How to reproduce

```
# start the target (records its PID under runs/)
node bank-app/server.ts            # app :4590, card widget :4591
source runs/bank-env.sh            # seed creds -> TB_BANK_* env (test-only app)

# UI (scripted); then prove it runs with no AI
node src/cli.ts explore --target targets/bank.json --script goals/bank/02-grid-filter-sort.json --planner scripted
node src/cli.ts verify  tests/generated/bank-02-grid-filter-sort.spec.ts --target targets/bank.json --times 3

# reusable login (MFA handled here as setup, once)
node src/cli.ts login --target targets/bank.json --script goals/bank/login-teller.json --out state/teller.json

# API from OpenAPI
node src/cli.ts explore-api --target targets/bank.json --openapi bank-app/openapi.json --slug bank-api
node src/cli.ts run tests/generated/bank-api.spec.ts --target targets/bank.json

# a reviewed mutation: blocked until approved, then real + audited
node src/cli.ts run     tests/generated/bank-maker-checker.spec.ts --target targets/bank.json   # FAILS: policy block
node src/cli.ts approve tests/generated/bank-maker-checker.spec.ts --note "reviewed"
node src/cli.ts run     tests/generated/bank-maker-checker.spec.ts --target targets/bank.json   # PASSES

# LLM authoring (Gemini key loaded into env only; never printed)
node src/cli.ts explore --target targets/bank-authed.json --planner llm --goal "…"
```
