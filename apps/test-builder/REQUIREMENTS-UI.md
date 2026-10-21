# UI test automation: requirements catalog and coverage check

Written from the point of view of a QA lead at a large regulated bank. Hundreds of testers, existing Selenium/Java and Tosca
suites, Jira + Xray/ALM, Jenkins or GitLab CI. MFA/SSO is out of scope (handled above this layer).

Coverage was checked by reading code on 2026-10-19. Nothing was started and no code was edited. One cheap check was run:
`npx tsc --noEmit` in `apps/test-builder` exits 0, and `node src/cli.ts` prints the seven-command usage listed below.
`apps/test-builder` is still being extended by another agent (`tb login` and `auth.storageState` appeared while this was
being written), so the (a) column is a snapshot. I did not run the bank-app specs, because that app belongs to the other agent.

How to read the ratings:

- **Covered**: it would satisfy a bank pilot as is.
- **Partial**: something real exists but a pilot would hit a wall.
- **Missing**: nothing usable exists.
- **(a)** is `apps/test-builder` (TB). **(b)** is Command Studio (CS), meaning `apps/workflow-engine/src` plus the studio.
- For CS, "Covered" means the platform already does the thing for a team building a testing app on it. It does not mean a testing app exists.
- File references are relative to `apps/test-builder/` for TB and `apps/workflow-engine/src/` for CS.

Priorities are for a **bank pilot**: Must (the pilot fails without it), Should (the pilot is weak without it), Could (later).

Commands that exist today (`src/cli.ts` USAGE): `explore`, `run`, `verify`, `explore-api`, `check-trace`, `approve`, `login`.

---

## 1. Authoring

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-01 | **Record and playback.** A tester clicks through the app and gets a spec. *Example: a manual tester records "open a savings account" in 5 minutes.* | Should | **Missing.** There is no recorder. `explore` is goal-driven (`explore.ts`), and `playwright codegen` is not wired in. | **Missing.** No browser anywhere (the sandbox is `--unshare-all`, `sandbox/bwrap.py`). |
| UI-02 | **AI authoring from a plain-language goal**, with human review before it counts. *Example: "log in as a teller and verify the accounts table shows 3 accounts".* | Should | **Covered.** `explore.ts` plus `planners/llm.ts`: closed action vocabulary, strict validation, `.draft.ts` on failure, spec only on `done`. | **Partial.** The AI builder (`services/ai_builder.py`) generates workflows, not UI tests. `ai_policy.py` is reusable for gating the model. |
| UI-03 | **Author from an existing manual test case.** Steps and expected results are ingested (Xray/ALM text or CSV) and become a spec linked to the case key. *Example: import 400 manual regression cases, generate drafts.* | Must | **Partial.** The `--goal` string or a scripted-steps JSON works, but there is no ingest of structured steps and expected results, and no case key is carried into the spec (`cli.ts explore`). | **Missing.** Nothing ingests test cases. `DataTable` plus a Loop could hold them, but no generator consumes them. |
| UI-04 | **Readable, editable output** that engineers can change in an IDE. Regeneration must not destroy edits. *Example: a developer adds a custom wait.* | Must | **Partial.** Output is plain Playwright TS with `test.step` groups (`codegen.ts`), so editing works. But `explore` overwrites `<slug>.spec.ts` with no merge or guard (`explore.ts`). | **Missing.** The AI builder edits workflow graphs, not test code. |
| UI-05 | **Reuse: page objects, shared flows, composable steps.** *Example: "login" and "open account" defined once and used in 800 tests.* | Must | **Missing.** `codegen.ts` emits a flat linear script. Every generated spec repeats login (see `tests/generated/bank-02-*.spec.ts`). | **Partial.** Sub-workflows (`services/subworkflow_service.py`) and code modules give reuse for workflows, not for UI steps. |
| UI-06 | **Data-driven tests.** One flow, N data rows from a table or CSV, with a result per row. *Example: 50 payment-amount boundary values.* | Must | **Missing.** `codegen.ts` has no parameterisation; `secret()` only injects credentials. | **Partial.** `DataTable` (`routes/tables.py`) plus the Loop node can iterate rows. There is nothing to drive a browser from them. |
| UI-07 | **Parameterised environments.** The same suite runs against DEV/SIT/UAT with different URLs, users and data. *Example: `--env uat`.* | Must | **Partial.** `targets/*.json` per target with `baseUrl`, `allowedHosts` and credential env names. Specs use relative `goto`, and `TB_TARGET` is honoured (`fixtures.ts`, `run.ts`). There is no environment registry or matrix, and policy rules must be copied per file. | **Partial.** Projects have named environments with a kind and per-env variables (`services/project_service.py`, `VariableModel`). Not tied to a UI target. |
| UI-08 | **Test-case metadata.** ID, title, tags, owner, linked requirement/Jira key, suite membership. *Example: `@XRAY-PAY-1234 @regression @payments`.* | Must | **Missing.** A spec has only a title and a slug. There are no tags or keys, and the header comment is not machine-read. | **Partial.** Workflow tags and folders exist (`TagModel`, `FolderModel`). There is no test-case entity. |
| UI-09 | **Meaningful assertions** beyond "page loaded": values, cells, URLs, attributes, regex, negative checks. *Example: the balance after a transfer equals the old balance minus the amount.* | Should | **Partial.** Only `expectVisible`, `expectText` (substring) and `expectCount` (`actions.ts`). A spec with no assertions gets a warning header (`codegen.ts`). | **Missing.** `engine/assertions.py` asserts on workflow outputs, not on DOM. |
| UI-10 | **Quality gate before a generated test is trusted.** *Example: it must pass N times with no AI before it enters the pack.* | Should | **Covered.** `tb verify` runs N/N fresh processes with `TB_LLM_*` stripped (`cli.ts verify`). | **Missing.** No such gate for UI tests. |

## 2. Hard UI

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-11 | **Data grids.** Sort, filter, paginate, virtualised rows, select a row by its content, row-level kebab menus, assert cell values. *Example: find the row for payee "ACME" and click its kebab menu.* | Must | **Partial.** The role vocabulary includes table/row/cell/columnheader, `within` scoping and `expectCount`; `bank-02` filters and sorts. There is no "row where cell = X" helper, and no pagination or virtual-scroll handling. | **Missing.** Browser only. |
| UI-12 | **iframes, including cross-origin** (card-entry and 3-D Secure widgets). *Example: fill a card form inside a payment-provider iframe.* | Must | **Missing.** `ElementRef` has no frame scope (`actions.ts toLocator`), and the bank app's card iframe has no goal in `goals/bank/`. Hand-written specs could use `frameLocator`. | **Missing.** |
| UI-13 | **Shadow DOM** (open and closed web components). *Example: a bank-branded `<currency-converter>` component.* | Should | **Partial.** Playwright role/label locators pierce open roots, and `goals/bank/04` plus `tests/generated/bank-04-shadow-converter.spec.ts` exist. I did not see it run, and closed roots are unreachable. | **Missing.** |
| UI-14 | **File upload.** *Example: upload a 5,000-row bulk-payment CSV and check the validation errors.* | Must | **Partial.** `upload` action to `setInputFiles(filePath())` (`actions.ts`, `codegen.ts`, `fixtures.ts`). No committed spec exercises it. Files must be static fixtures; generated-per-run files are not supported. | **Missing.** |
| UI-15 | **Download assertions** (CSV, XLSX, PDF statements). *Example: the downloaded statement contains the IBAN and 12 lines.* | Must | **Partial.** `expectDownload` and `download` assert text in CSV/PDF (`downloads.ts`). The PDF extractor is a regex over FlateDecode streams, so fonts with ToUnicode/CID encoding will fail. There is no XLSX support. | **Missing.** |
| UI-16 | **Multi-tab and popups** (partner portal, print preview, OAuth consent). *Example: click "Open partner portal" and assert on the new window.* | Should | **Missing.** There is one page, and no popup action or `context.waitForEvent('page')`. The allowlist would abort the partner host anyway (`policy.ts`). | **Missing.** |
| UI-17 | **Multi-user maker-checker in one test.** Two roles, two contexts, interleaved. *Example: the teller creates a payment and a supervisor approves it in a second session.* | Must | **Missing.** `explore` has one context and one page. `bank.json` has four credential sets, but no action switches actor. A second `browser.newContext()` in hand-written code would **not** get the network policy, because `fixtures.ts` guards only the built-in `context`. | **Partial.** The Approval node and `human_tasks` model maker-checker for workflows (`nodes/flow/approval.py`). Not for a browser flow. |
| UI-18 | **i18n/locale.** Run in EN and DE, check date, number and currency formats, long strings, RTL. *Example: DE locale shows `1.234,56 EUR`.* | Should | **Missing.** Locators hard-code English accessible names, `playwright.config.ts` sets no locale or timezone, and there is no locale matrix. | **Missing.** |
| UI-19 | **Accessibility checks** (WCAG, axe). *Example: every page in the payment journey has 0 serious violations.* | Should | **Missing.** There is no axe integration. | **Missing.** |
| UI-20 | **Visual regression** with baselines, masks for dynamic regions and an approve-new-baseline flow. *Example: the statement page layout did not shift.* | Should | **Missing.** Screenshots are only taken on failure (`playwright.config.ts`); there is no `toHaveScreenshot` flow. | **Missing.** |
| UI-21 | **Async and dynamic UI.** SPA settling, skeleton loaders, websocket updates, session-timeout modals. *Example: wait for the live rate to tick, then assert.* | Must | **Partial.** `settle.ts` (in-flight requests, DOM-quiet, `aria-busy`) is used by `explore`. Generated specs rely only on Playwright auto-wait and `waitFor`. Websocket/timer behaviour has no helper. | **Missing.** |
| UI-22 | **Reusable authenticated session**, so 3,000 tests do not each log in. *Example: log in once per role per run.* | Must | **Covered.** `tb login` saves a `storageState`, and `auth.storageState` is applied in `fixtures.ts`. This is new and untested at scale. Per-role states and expiry refresh are not handled. | **Missing.** |
| UI-23 | **Dialogs, date pickers, autocompletes, drag/drop.** Native alerts, calendar widgets. *Example: pick 29 Feb in the booking calendar.* | Should | **Partial.** Only click/fill/select/press/hover exist. There is no dialog handler, no widget helpers and no drag. | **Missing.** |
| UI-24 | **Legacy and non-web UI** (Citrix-hosted, thick client, canvas, frames-based intranet). *Example: a mainframe emulator in the browser.* | Could | **Missing.** Browser DOM only. | **Missing.** |

## 3. Execution

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-25 | **Parallel runs and sharding.** *Example: 3,000 tests in 40 shards across 20 agents.* | Must | **Missing.** `workers: 1`, `fullyParallel: false` (`playwright.config.ts`). `run.ts` spawns one process. The CLI exposes no `--workers` or `--shard`, and per-shard evidence is not merged. | **Missing.** The engine is one process (known), and the sandbox cannot run a browser. |
| UI-26 | **Cross-browser and device matrix** (Chrome, Edge, Firefox, Safari/WebKit, mobile viewports). *Example: the nightly pack runs on Chrome and Edge, a smoke pack on iPhone viewport.* | Must | **Missing.** There is a single `chromium` project (`playwright.config.ts`). | **Missing.** |
| UI-27 | **Schedules.** *Example: smoke every 30 minutes, full regression nightly at 01:00.* | Must | **Missing.** There is no scheduler; it relies on external cron or CI. | **Partial.** `Cron` node plus `engine/cron_scheduler.py` fire workflows on a schedule (and smoke configs). They have nothing to execute a UI run. |
| UI-28 | **CI trigger with an exit code** and machine-readable result. *Example: a Jenkins stage `node src/cli.ts run tests` fails the build.* | Must | **Covered.** Documented exit codes: `run` 0/1, `verify` 0/1, `explore` 0/2 (`cli.ts`), plus JUnit and `evidence.json`. | **Partial.** Webhook trigger (`routes/webhooks.py`, project API keys in `services/api_key_service.py`) with a Respond node can return a verdict. There is no CI plugin or status-callback contract. |
| UI-29 | **Retries and flaky handling.** Rerun failed only, quarantine, flake-rate tracking. *Example: a test failing 1 in 20 is auto-quarantined and a ticket opened.* | Must | **Missing.** `retries: 0` is hard-coded. `verify` 3/3 is a pre-admission gate only, with no history or quarantine. | **Partial.** Node-level `retry_on_fail` and an execution `/retry` route (`workflow_runner.py`, `routes/executions.py`). Wrong granularity, no flake statistics. |
| UI-30 | **Test data setup and teardown** per test or per suite (API/DB preconditions, cleanup). *Example: create a fresh customer through the API, delete it after.* | Must | **Partial.** `hooks.resetUrl` is POSTed once before the whole run (`run.ts applyHooks`), and it needs an app-side reset endpoint. There are no per-test or teardown hooks, though the guarded `request` fixture lets hand-written specs call APIs. | **Partial.** The HTTP, Postgres and DataTable nodes are exactly the primitives for seeding and cleaning. Not wired into any test lifecycle. |
| UI-31 | **Test data management.** Unique-per-run data so parallel tests do not collide, reservation of shared accounts, synthetic or masked data. *Example: lease account ACC-1001 to one test at a time.* | Must | **Missing.** Specs use fixed data (e.g. `pathParams` in `targets/bank.json`). | **Partial.** `DataTableModel`/`DataTableRowModel` store datasets. There is no lease or lock, and no masking here (the masking lives in analytics-service). |
| UI-32 | **Environment management.** Which build is deployed, is the environment up, test windows, who has it booked. *Example: skip the run and say "UAT down" instead of failing 3,000 tests.* | Must | **Partial.** Target configs only. No health pre-flight, no build detection, no booking. | **Partial.** Project environments (kind, variables, release envs) plus HTTP/Poll nodes could build a pre-flight. Nothing is built. |
| UI-33 | **Selective execution** by tag, suite, requirement, changed area, or "failed last night". *Example: run only `@payments` smoke on every merge.* | Must | **Missing.** `run` accepts only files or a directory (`run.ts expandSpecs`); no `--grep` or tag filter is passed through. | **Missing.** |
| UI-34 | **Runner pool, queueing, capacity limits and external grids** (Selenium Grid, BrowserStack, Sauce). *Example: 20 teams share 30 browser agents fairly.* | Should | **Missing.** Local process only. | **Missing.** There is no worker pool or queue (single process). |
| UI-35 | **Timeouts and cancel** of a running suite. *Example: stop a run that is hammering UAT.* | Should | **Missing.** Kill the process. Per-test timeout is 30 s fixed. | **Partial.** Executions can be cancelled (`routes/executions.py` `/cancel`), but only workflows. |

## 4. Results

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-36 | **Evidence per test:** trace, video, screenshots, network log, console. *Example: an auditor opens the trace for a payment test from March.* | Must | **Covered.** Trace on, video on, screenshot on failure (`playwright.config.ts`), HAR per test (`fixtures.ts`), `check-trace` proves masked traces still open (`traceCheck.ts`). | **Missing.** There is no artifact store (known). Node outputs are JSON in Postgres (`NodeOutputModel`). |
| UI-37 | **Tamper-evident, retained evidence.** Hashes, signing, WORM/immutable storage, 7-year retention. *Example: prove a March run's artifacts are unchanged.* | Must | **Partial.** `evidence.json` lists sha256 for every artifact plus the spec and config hashes (`run.ts`). It is unsigned and on local disk; the manifest can be regenerated by anyone, and there is no retention policy or WORM store. | **Missing.** `engine/retention_cleaner.py` only deletes old executions. |
| UI-38 | **Results dashboard.** History per test, pass-rate trends, drill-down, compare runs. *Example: "payments pack pass rate for the last 30 days".* | Must | **Missing.** `summarize()` prints to the console; results are directories under `runs/` (37 so far, none indexed). | **Partial.** Executions list and detail (`routes/executions.py`) and `services/scorecard.py` are generic run history, with no concept of a UI test result. |
| UI-39 | **Failure triage.** Reason, evidence at failure, classification (product bug / environment / script), defect creation. *Example: one click turns a failure into a Jira defect with the trace attached.* | Must | **Partial.** Screenshot, trace and error context are captured. There is no classification, no dedup, no defect link. | **Missing** for UI tests. |
| UI-40 | **JUnit/xUnit XML** for CI and Xray import. *Example: Jenkins JUnit publisher shows the trend.* | Must | **Covered.** `junit.xml` is written (`playwright.config.ts`) and summarised in `evidence.json`. Test names carry no Xray key, so mapping by name is fragile. | **Missing.** Nothing emits or ingests JUnit. |
| UI-41 | **Audit trail.** Who ran what, when, against which environment and build, which spec version. *Example: an auditor asks who ran the UAT payment pack on 3 March.* | Must | **Partial.** `evidence.json` has timestamps, spec and config hashes, and tool versions. It has **no user identity and no build under test** (no CI build number or git SHA). | **Missing.** `services/authz.py` is a no-op, and `X-User-Id` is recorded but "never used for authorization" (`core/identity.py`). Typed actor labels are unverified (`core/actor.py`). |
| UI-42 | **Test versioning and history.** Diff, rollback, which version ran. *Example: roll back a test edited last week.* | Must | **Partial.** Specs are files, so git gives versions, and approvals are keyed to content hash. Nothing in the tool manages or records this. | **Partial.** `WorkflowVersionModel` and releases (`services/release_service.py`) version workflows and apps, not specs. |
| UI-43 | **Approval of tests** before they gate anything (review, promote draft to active). *Example: only reviewed tests join the release-gating pack.* | Must | **Partial.** `tb approve` records a spec hash in `approvals.json` (`approvals.ts`). Today it means "allowed to run mutating requests", not "reviewed", and it records no reviewer. | **Partial.** Approval node, `human_tasks` and email notify (`services/approval_notify.py`) are the right primitives for a review workflow. Actors are unverified. |
| UI-44 | **Secrets never in evidence.** Passwords, tokens and API keys are masked in traces, HARs, logs, video-adjacent files. *Example: the trace shows `********`, not the password.* | Must | **Covered.** Scan, mask, re-scan with raw, URL, JSON and base64 forms, including zip entries; a failed re-scan throws; masked traces are re-opened (`secrets.ts`, `run.ts`). It covers only credentials declared in the target config. | **Partial.** Broker redaction and secret fingerprinting (`broker/policy.py`, `services/secret_service.py`). Not artifact-aware. |
| UI-45 | **PII in evidence.** Customer names, account numbers and card numbers in screenshots, videos and HAR bodies are detected, restricted and purged on request. *Example: purge evidence for a data subject within 30 days.* | Must | **Missing.** Only declared credentials are scanned. Video and screenshots cannot be masked, and there is no purge. | **Partial.** `engine/ai_policy.py` `scrub_pii` has a regex PII scrubber (email, phone, SSN, Luhn cards, IBAN, accounts) that could be reused. The retention cleaner exists. Neither is applied to evidence. |

## 5. Maintenance

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-46 | **Locator healing.** When the UI changes, the test repairs itself or proposes a fix. *Example: the button "Pay" is renamed "Submit payment" and a patch is proposed.* | Should | **Missing.** Specs use only role/label/text (no CSS or XPath), which is more stable by design. But a failing step just fails, and there is no re-explore-to-patch loop. | **Missing.** |
| UI-47 | **Impact analysis of UI change.** Which tests touch which screens or components, and what changed between builds. *Example: "this release changed the payee form: 14 tests affected".* | Should | **Missing.** `explore` stores per-step aria snapshots (`runs/explore-*/snapshots/`), but nothing indexes or diffs them. | **Missing** for UI. `services/drift.py` (API contracts) and `WorkflowRefModel` (dependency graph) are the analogous building blocks. |
| UI-48 | **Test health reporting.** Stale, never-failing, always-flaky and ownerless tests; ownership notices. *Example: monthly list of tests no one has touched in 12 months.* | Could | **Missing.** | **Missing.** |

## 6. Coexistence with existing tooling

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-49 | **Orchestrate existing Selenium/Java (and Tosca) suites** under the same trigger, evidence and reporting. *Example: one nightly pipeline runs 6,000 Selenium tests plus 200 new Playwright ones.* | Must | **Missing.** `run.ts` can only spawn `@playwright/test`. | **Partial.** HTTP plus Poll nodes, with credentials and the egress allowlist, can trigger and poll a Jenkins job (`broker/policy.py`, `nodes/flow/poll.py`). It can run no suite itself and ingests no results. |
| UI-50 | **Migration assist.** Convert Selenium/Tosca/UFT scripts to Playwright or the builder's format. *Example: AI converts a Java page object to TS.* | Could | **Missing.** | **Missing.** `services/n8n_import.py` is an n8n workflow importer only. |
| UI-51 | **Jira/Xray (or ALM) sync.** Pull test cases and plans, push execution results with evidence, link defects. *Example: results appear on the Xray Test Execution `PAY-5500`.* | Must | **Missing.** | **Partial.** HTTP node, OpenAPI import (`services/openapi_import.py`), credentials and the allowlist could build it. No Jira/Xray node exists. |
| UI-52 | **Git as the source of truth.** Specs in a repo, branch plus PR review, CODEOWNERS, generated changes as a PR. *Example: the builder opens a PR with 5 new specs.* | Must | **Partial.** Output is plain files in `tests/generated/`, so git works by hand. The tool makes no commits or PRs. | **Missing.** Workflows live in Postgres. Template bundle export (`services/template_bundle.py`) is file-based but not git-oriented. |
| UI-53 | **IDE and local parity.** An engineer can run and debug the same spec locally. *Example: run in VS Code with the trace viewer.* | Must | **Covered.** Specs are ordinary Playwright Test files over `src/fixtures.ts`, with `--headed` and `check-trace`; `npx tsc --noEmit` exits 0. The Node >= 23.6 requirement may clash with managed laptops. | **Missing.** Not applicable to a workflow runtime. |
| UI-54 | **CI plugins and pipeline templates.** Jenkinsfile and GitLab CI snippets, a container image with browsers, JUnit publish. *Example: copy-paste stage.* | Must | **Missing.** No Dockerfile, Jenkinsfile or `.gitlab-ci.yml` under `apps/` (checked with `find`). | **Missing.** Only a webhook. |
| UI-55 | **Notifications.** Teams/Slack/email on failure or on a nightly summary. *Example: "payments pack failed, 3 tests, link".* | Should | **Missing.** | **Covered** as a platform capability: `nodes/integrations/send_email.py`, the HTTP node, `services/approval_notify.py`. It needs a result feed to trigger it. |

## 7. Governance

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-56 | **RBAC: who can author, run and approve, on which environment.** *Example: only the release manager's group may run the UAT pack; testers cannot touch prod.* | Must | **Missing.** The CLI has no identity. | **Missing.** `authorize()` always returns True (`services/authz.py`). User/Team/Member tables exist (`db/models.py`) but are not enforced. |
| UI-57 | **Production safety.** Hard blocks on mutation, read-only synthetic users, host allowlist. *Example: nothing can POST a payment, even by accident.* | Must | **Partial, and the strongest area.** Network route abort on the page and the API `request` (`policy.ts`, `fixtures.ts`), forbidden actions by computed role and name, and an allowlist that approval never relaxes (`approvals.ts`). Gaps: rules are per target file, not tied to an environment kind; there is no "this is prod, refuse" check; name patterns are regex-fragile; and extra contexts bypass the policy. | **Partial.** Per-project egress allowlist and secret-to-host binding (`broker/policy.py`) apply to workflows only. |
| UI-58 | **Segregation of duties.** Author, approver and executor differ, and approvals are attributable. *Example: the author of a payment test cannot approve it.* | Must | **Missing.** `approvals.json` has no identity. Anyone with file access, including the author, can run `tb approve`. | **Missing.** No verified identity, so it cannot be enforced. The Approval node records a typed name (`core/actor.py`). |
| UI-59 | **Credential management.** Vault integration (CyberArk/HashiCorp), no secrets in code, per-environment, rotation. *Example: Jenkins injects UAT teller credentials from CyberArk.* | Must | **Partial.** Credentials come only from env vars (`config.ts credentialValues`), so a CI credential store injecting env works. No vault client, no rotation. | **Partial.** Encrypted variables, secret-to-host binding, fingerprints (`services/secret_service.py`, `variable_crypto.py`). No external vault. |
| UI-60 | **AI governance for model risk.** What page content leaves the bank, which model, a prompt log, AI-authored tests flagged and human-approved, no AI at run time. *Example: an on-prem model only; every prompt retained.* | Must | **Partial.** The base URL is configurable, so an on-prem model works. Credentials are scrubbed from snapshots, and `verify` strips `TB_LLM_*`. **Page content (names, balances) is sent unscrubbed**, truncated at 12k chars, with no prompt log (`planners/llm.ts`, `explore.ts`). Specs do not record that they are AI-authored beyond a header comment. | **Partial.** Model allowlist and PII/secret scrubbing for prompts exist (`engine/ai_policy.py`). Not applied to this flow. |
| UI-61 | **Change-control linkage.** Each run is tied to a release or build ID and change ticket, as CAB evidence. *Example: "build 4.12.3, CHG0012345, 2,940/2,950 passed".* | Must | **Missing.** `evidence.json` has no build, commit or ticket field. | **Missing.** Studio releases pin Studio artifacts (`ReleaseModel`), not the system under test. |

## 8. Scale

| ID | Requirement and example | Pri | (a) test-builder | (b) Command Studio |
|---|---|---|---|---|
| UI-62 | **Thousands of tests in a nightly window.** *Example: 3,000 tests complete in under 4 hours.* | Should | **Missing.** Sequential, one worker, and per-test video and trace always on. Even 13 tiny tests produce 224 KB per run, and `runs/` is 22 MB already. | **Missing.** |
| UI-63 | **Evidence cost and retention tiers.** Full artifacts on failure, thin on pass, expiry by policy. *Example: keep traces for 90 days, hashes for 7 years.* | Must | **Missing.** `trace: 'on'` and `video: 'on'` are unconditional, and there is no pruning (`playwright.config.ts`). | **Missing.** No artifact store. |
| UI-64 | **Multi-team and multi-tenant.** Hundreds of testers in many teams, with separate targets, suites, quotas and visibility. *Example: Payments and Lending each own their pack.* | Must | **Missing.** One package, one `approvals.json`, one `targets/` directory. | **Partial.** Teams, projects and folders exist in the data model (`db/models.py`), but nothing is enforced (authz no-op). |
| UI-65 | **LLM cost control.** Budget per authoring session, and AI used at authoring time only. *Example: cap each generation at 40 steps.* | Should | **Partial.** `budget.maxSteps` / `maxSeconds` per target (`config.ts`), temperature 0, and no AI at run time. No token or money accounting. | **Partial.** `services/api_usage.py` and the LLM provider layer exist. No budget enforcement was verified. |

---

## Summary

Counts below come from the tables above (65 requirements: 45 Must, 17 Should, 3 Could).

"% Must covered" uses two cuts. **Strict** means Covered only. **Generous** counts a Partial as half.

| Priority | Total | (a) TB Covered | (a) TB Partial | (a) TB Missing | (b) CS Covered | (b) CS Partial | (b) CS Missing |
|---|---|---|---|---|---|---|---|
| Must | 45 | 6 | 18 | 21 | 0 | 22 | 23 |
| Should | 17 | 2 | 4 | 11 | 1 | 3 | 13 |
| Could | 3 | 0 | 0 | 3 | 0 | 0 | 3 |

- **test-builder: 6 of 45 Musts Covered (13%), 18 Partial.** Generous cut: (6 + 9) / 45 = **33%**.
- **Command Studio: 0 of 45 Musts Covered (0%), 22 Partial.** Generous cut: 11 / 45 = **24%**. Every "Partial" is a general primitive, such as a node, a table or a schedule, and not a UI-testing feature.
- Together, test-builder plus Command Studio, counting each Must once at its better rating, are Covered on 6 and Partial on 30 (of 45). The two overlap very little: test-builder owns the browser, and Command Studio owns scheduling, notification, approvals and data.

### Top 10 gaps, ranked by pilot impact

| # | Gap | Reqs | Kind |
|---|---|---|---|
| 1 | **Identity, RBAC and segregation of duties.** The `authz` seam is a no-op, there is no verified actor, and `tb approve` has no reviewer. Nothing in a bank can go live without it, and it also blocks a trustworthy audit trail. | UI-41, 43, 56, 58, 64 | **Platform building block** |
| 2 | **No artifact store, evidence retention or WORM.** Evidence lives in local `runs/` directories, and Studio cannot hold a trace or a video. Without it nothing is auditable at scale, and cost control (UI-63) has nowhere to act. | UI-36, 37, 45, 63 | **Platform building block** |
| 3 | **No execution fleet.** There is a single process and sandbox that cannot run a browser. test-builder runs `workers: 1` with no sharding. A runner pool (Studio queue + remote browser agents) is needed before any schedule means anything. | UI-25, 26, 27, 34, 62 | **Platform** (queue, worker pool, remote executors), **testing-specific** (browser matrix, sharding) |
| 4 | **No Jira/Xray/ALM integration and no test-case entity.** Without a case key, results cannot be traced to requirements, and manual cases cannot be imported. | UI-03, 08, 51, 39 | **Testing-specific** (HTTP/OpenAPI/credentials are the platform base) |
| 5 | **Cannot run or report on the existing Selenium/Tosca estate.** The bank's thousands of tests sit outside any shared view or trigger. This is the make-or-break coexistence gap. | UI-49, 54, 40, 38 | **Testing-specific** (a "run external job and ingest JUnit" runner) |
| 6 | **No CI integration beyond an exit code.** No image with browsers, no pipeline templates, no build ID or change-ticket in evidence, no selective run. | UI-54, 61, 33, 28 | **Testing-specific** |
| 7 | **Hard-UI authoring gaps that real bank apps hit daily.** Cross-origin iframes (card forms), multi-user maker-checker, popups, locale. The bank-app demo already contains each of these, but the action vocabulary has no support for them. | UI-12, 16, 17, 18, 23 | **Testing-specific** |
| 8 | **No reuse or data-driven layer.** Every spec re-codes login and flows, and there are no page objects or parameter tables. This will not scale to hundreds of testers, who would maintain thousands of near-duplicate scripts. | UI-05, 06, 31, 30 | **Testing-specific** (DataTable is the platform base) |
| 9 | **No results model.** No history, flake tracking, quarantine, retries or triage. A nightly run of thousands of tests is unusable without them. | UI-29, 38, 39, 48 | **Mixed** (run history is platform; flake and triage are testing-specific) |
| 10 | **PII and AI governance.** Page content, including customer data, is sent to the model without PII scrubbing. PII in videos and screenshots is not detected, and nothing can be purged. `ai_policy.scrub_pii` already exists in the engine and is not applied. | UI-45, 60 | **Platform** (policy, scrubbers) and **testing-specific** (applied to snapshots and evidence) |

### Platform building blocks versus testing-specific

- **Platform building blocks** (any Command Studio team would use these):
  - verified identity and RBAC (gap 1);
  - an artifact store with retention and immutability (gap 2);
  - a worker pool or queue with remote executors (gap 3);
  - external-system connectors with credentials, such as Jira/Xray, Jenkins and Teams;
  - a generic "run history and trends" view;
  - applying `ai_policy`'s PII scrubbing to any prompt.
- **Testing-specific:**
  - the Playwright runner and its browser, shard and matrix control;
  - a test-case entity with keys, tags and requirement links;
  - Xray/ALM mapping and the JUnit ingest;
  - an external-suite wrapper (Maven/Gradle, surefire XML);
  - retry, flake and quarantine logic;
  - page objects, data-driven expansion and the hard-UI action vocabulary;
  - build and change-ticket linkage in evidence.

What genuinely works today and is worth keeping:

- the policy layer (host allowlist, forbidden requests and actions, enforced by the builder and not the model);
- secret masking with a re-opened-trace check;
- the `verify` determinism gate;
- the hashed evidence manifest;
- plain-Playwright output that an engineer can read and run in an IDE.

These are strong design choices for a bank, but they cover a narrow slice of the catalog.

---

## Verdict

**Not yet. A bank QA team cannot realistically build and run its UI automation on Command Studio plus test-builder today.** It could run a credible, safe, narrow *pilot* of AI-authored Playwright tests for one application, as a *sidecar* to the existing estate.

Why not today:

1. The two products are not connected. The README says "not integrated with the workflow engine or the studio". Studio has no way to start, observe or store the output of a test-builder run.
2. Command Studio cannot be the control plane for the main-line use cases yet. It has no identity, no artifact store, no job runner for browsers, and no UI-test concept. Its useful primitives (Cron, Approval, HTTP, DataTable, email, versions) are all real, but none yet drives a test.
3. Coexistence is the weakest area. Nothing runs, wraps or reports on Selenium/Tosca, nothing syncs with Xray/Jira, and there are no CI templates or container images.
4. The generated tests are linear scripts with no reuse or data-driven layer, and the action vocabulary lacks iframes, popups and multi-user flows, which are in the bank's real apps. The bank-app demo already contains them.

**The minimum that would turn this into a "yes" for a pilot** (a single bank team, one app, non-prod, coexisting beside Jenkins/Xray/Selenium). This is the order I would build it in:

1. **A runner service plus a job contract** (platform + testing). A containerised test-builder worker that Command Studio, or Jenkins through a webhook, can invoke with `{suite, tags, env, build_id, change_ticket}`. It returns JUnit and `evidence.json`, and supports `--workers`, `--shard`, `--grep` and `retries`. This fixes gaps 3, 6 and part of 9.
2. **An artifact store with retention, plus run records in Studio.** Evidence goes in with its manifest hash, linked to the execution. Studio shows the run list, the verdict and the drill-down. This fixes gap 2 and most of the results model.
3. **Minimal real identity**: authenticated users, three roles (author, approver, runner) enforced in `authorize()`, and `approve` that requires a different person from the author. A pilot can start with a single shared secret per role, but must have verified names in the audit trail (gap 1).
4. **An Xray/Jira connector in both directions**: pull test keys, push executions with evidence links, and carry the case key in the spec. Include a "wrap an external job and ingest its JUnit" step, so the Selenium estate appears in the same dashboard even though Studio does not run it (gaps 4 and 5). That single feature makes coexistence real, because the bank keeps its tooling and gains one view.
5. **Reusable steps and data-driven expansion in the generator**, plus frame, popup and second-actor support in the action vocabulary and in the policy fixture (gaps 7 and 8). Without these the first 50 tests are fine and the next 500 are unmaintainable.
6. **Governance fixes that are cheap now and expensive later**: apply PII scrubbing to LLM snapshots, record AI-authorship and prompt logs, and set `retries`, `trace` and `video` to failure-only by default.

Honest caveat on the owner's thesis: Command Studio *can* be the generic orchestration, approval, scheduling, notification and data layer for a QA team's custom app. It cannot yet be the *execution* layer, because browsers cannot run in its sandbox. The thesis only holds if "build their own testing app on it" includes an external runner fleet it controls. That is a platform feature (remote executors) and not a testing feature, which is why it is worth building even if the testing use case changes.
