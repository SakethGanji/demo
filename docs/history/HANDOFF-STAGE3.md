# Handoff: Stage 3, tests inside the editor (start here)

Written 2026-10-17, after Stages 1 and 2 were committed (`bf332d0`, `a83a218` on branch `revert-to-5684ba5`, not pushed). This replaces the Stage 3 section of `PLAN-WORKFLOW-EDITOR.md`, which was written before these decisions. All paths are relative to `demo/`.

## 1. How we work (same as Stages 1 and 2)
- **Roles.** The lead session (Opus) plans, writes the contracts, launches Sonnet helper agents, reviews every diff, runs every gate itself and reports to the owner. Helper agents build. **Testers are separate agents from builders.** They write tests from the contract, not from the code, and they never "fix" a test to match a bug.
- **Order.**
  1. Freeze the contracts in `CONTRACTS-STAGE3A.md`.
  2. Run the waves (builders in parallel on separate files, then testers).
  3. The lead validates each agent's report: re-run its checks, read the key code, look at screenshots.
  4. Run the gates, then report.
- **Tests required for every feature:**
  - engine unit tests;
  - integration tests over HTTP on real Postgres;
  - one API journey test that walks the demo story;
  - Playwright through the real UI on the real stack, with no mocked API.
- **Limits.** At most 3 agents at once, with separate file ownership. Only one agent runs e2e. Agents never touch git.
- **Owner rules:**
  - Features only: no login, roles or audit.
  - Keep the approved look: `src/shared/components/ui/*`, tokens in `src/index.css`, `npm run design:check`.
  - **Report in chat as short ordered lists**, because he doesn't read documents. Plans go in chat; repo files are the backup.
  - **Don't commit until he says "commit".**
  - When a decision isn't covered, follow n8n and say so in the reply.
- **Lessons from Stages 1 and 2:**
  - The runner rebuilds `NodeDefinition` by hand at 2 places in `engine/workflow_runner.py`, plus the credential site. Every new node field must be copied there, and also in `repositories/workflow_repository.py`'s definition dumps.
  - Bug-hunting e2e agents find real product bugs. Fix them with a separate fix agent, and remove the test workarounds, so the tests prove the fix.
  - Run the **full** e2e suite at the end. Timing bugs only show under full-suite load.
  - Look at screenshots yourself. Agents miss cramped or overlapping UI.
  - The checker leans lenient. False alarms are worse than misses.

## 2. Environment and baselines
- **Postgres** runs in Docker as `workflow-engine-postgres-1` on `:5433`.
- **Dev DB** is `workflows_studio_dev`.
- **`apps/workflow-engine/.env.local`** holds a live Gemini key. **Never print it, log it, or copy it.**
- **Engine:**
  - Full suite: `cd apps/workflow-engine && venv/bin/python -m pytest -q`.
  - Unit tests: `tests/unit -q`.
- **Studio** (from `apps/workflow-studio`): `npm run test:unit`, `npx tsc -b`, `npm run lint` and `npm run design:check`.
- **E2E** (from `apps/workflow-studio`):
  - Run it as `flock /tmp/e2e.lock npx playwright test -c e2e [file]`.
  - Kill leftover `uvicorn --app-dir` and `vite --strictPort` processes **by PID**. Never use `pkill -f`.
  - The stack seeds Refund triage, a fake API server (`tests/fake_refund_api.py`) and a failed run.
- **Baselines at `a83a218`:**

| Check | Baseline |
|---|---|
| Engine suite | 906 passed, 1 skipped, 2 xfailed |
| Studio unit tests | 155 |
| `tsc` | clean |
| Lint | 96 problems (don't go above) |
| `design:check` | clean |
| E2E | 129 passing (specs 01–15) |

## 3. What exists to build on
- **Stage 1** (`CONTRACTS-STAGE1.md`):
  - runner `stop_at`, `pending_inputs` and `pre_populated_states`;
  - `services/partial_run.py` (test one node, run-to, run-from);
  - pinned data;
  - the `disabled` and `on_error` node fields;
  - version history and restore;
  - the ⌘K palette and the `editorActionsStore` action registry.
- **Stage 2** (`CONTRACTS-STAGE2.md`):
  - `engine/shapes.py` (Shape = a JSON Schema subset);
  - `engine/contracts.py` `node_contract(node_def, inputs, *, table_columns=None)`;
  - `contract()` on every node;
  - the learned-contract learn and approve endpoints;
  - `services/workflow_checker.py` `check_workflow(definition, lookups)` (WF001–WF009), `services/check_lookups.py` and `POST /workflows/{id}/check`;
  - the n8n import;
  - in the studio: `lib/shapes.ts`, `stores/checkStore.ts`, `hooks/useLiveCheck.ts`, the Problems tab and autocomplete from shapes.
- **Fixtures:**
  - `tests/fixtures/workflows/refund_triage.json` and `marketplace_*.json`;
  - 10 real n8n templates in `tests/fixtures/n8n/`;
  - checker snapshots in `tests/fixtures/checker_snapshots/`.

## 4. The agreed design (owner approved these ideas in chat on 2026-10-17)

### Principle: spec-first and test-first. No real runs needed.
You build and test a workflow without ever calling a real system. The data for outside systems comes from what they *promise* (their spec or schema), not from calling them. Recording a real run is an optional extra.

### Every node has three things, defined in a space in the editor
1. **Types: the expected input and output.**
   - Field names, types and rules, such as `amount: number ≥ 0` and `status ∈ {approve, deny}`.
   - These come from Stage 2 contracts and from API and database specs. The user can tighten them.
2. **Data categories.** For each input field, a standard set:
   - normal;
   - boundary (e.g. 500/501, 0, max length);
   - missing or empty;
   - wrong type;
   - unusual (very large, special characters, unicode, duplicates);
   - outside-system replies: success, each documented error, timeout and empty.
3. **Test cases.** Concrete examples, each tagged with a category and an expected result.
   - The editor shows a **coverage grid of node × category** so that gaps are obvious.

### Mocks (fake replies from outside systems), mostly automatic
- **API catalog.** Register an API once by its **OpenAPI spec**, by pasting it, uploading it or giving a URL.
  - The HTTP node then picks "API → endpoint" instead of a raw URL.
  - It gets its contract, autocomplete and generated fake replies instantly, including the documented errors (400/404/500).
- **Databases.** Fake rows come from the schema, by reading the table definition only. That covers Postgres and our data tables.
- **No spec available.** Paste one example reply, which becomes the contract and the mock.
- **One default mock per outside step,** shared by all tests. A test overrides it only when it needs a different reply.
- **Optional smart mocks:** rules like "if amount > 1000, reply deny" or "echo order_id".
- **Optional "Save as test" from a real or past run.**
- **Strict offline mode.** A test that reaches an outside step with no mock fails on purpose, with one-click fixes: "Generate from spec/contract" and "Use last real reply".
- **Request checks.** A test can check the request a node *would* send, such as the URL and body built from expressions.

### Checking results
- Tests check exact values only for what our own logic decides.
- For dynamic values (ids, timestamps, prices) they check flexibly: exists, type, range, one-of, pattern, and "matches the contract".
- Every test run also checks every node's output against its contract automatically.

### How test quality is measured
This is the point of the stage: it makes AI generation good later.
- **Branch coverage:** which If/Switch branches and error paths no test takes.
- **Planted-bugs score (mutation testing):**
  - The system makes small deliberate bugs in copies of the workflow: flip `>` to `>=`, change a Set value, swap branches, drop a node, break an expression.
  - It reports how many the tests catch, e.g. "19/20 planted bugs caught".
- **Repeatable results:** time, randomness and mocks are frozen per test, so 20 runs give identical results.
- **The test bar:** "14/14 passing · 1 gap · 19/20 planted bugs caught", with a pass/fail badge on every node.
- **Locked tests:** approved tests are locked.
- **Releases** are blocked when an approved test fails.

### Three test layers (the industry "test pyramid")
1. **Many mock tests:** offline and in milliseconds. They run on every edit and catch mistakes in our own logic.
2. **Drift alerts on real runs:** every real run compares the outside system's actual reply with its contract or spec. On a mismatch they flag "Risk API reply changed" and mark the related tests stale.
3. **A few opt-in live smoke tests:** these call the real or sandbox API before a release or on a schedule, and catch broken URLs, secrets and connections.

### How AI helps (Stage 3B)
- Draft test cases for empty grid cells. The user approves or rejects each one.
- Generate variations within the declared types to hunt for inputs that break a node (fuzz or property-based testing).
- Mock outside systems from specs and run the workflow offline to see whether it works.
- **Never weaken tests:** AI may only add draft tests or change the workflow, never an approved test.
- Uses the existing Gemini setup behind a swappable model adapter.
- **All automated tests use the fake LLM** (`engine/fake_llm.py`).

### AI-ready foundations, built now so Stage 6 just plugs in
1. **One toolkit.** Every editor action is one engine function: add node, set parameter, connect, check, run tests, score. The UI and the AI use the same functions.
2. **One scorecard:** `score(workflow) → {checker errors, tests passing, branch coverage, planted-bugs score, gaps}`. It's used by the test bar, releases and (later) the AI loop.
3. **Structured feedback.** Every finding and failure is data (node, field, expected, actual), not prose.
4. **A node catalog** generated from the code: each node type's parameters, contract, test recipe and an example. The AI prompts use it, so they never go stale.

### Test recipes per node type
These ship with the product; we write each one once.

| Node type | Tests check | Suggested cases |
|---|---|---|
| Logic (Set, Filter, Merge, ItemLists, Aggregate) | input → exact output | normal, missing field, empty list |
| Branching (If, Switch) | which branch each item takes | one per branch, plus boundaries read from the condition |
| Outside systems (HTTP, Postgres, MongoDB, table writes, email) | the request it would send, plus how later nodes handle the reply | success, each documented error, timeout |
| Code | input → output | normal, empty, bad value |
| Triggers | the payload is the test input | a sample from the contract |
| **AI/LLM** | **out of scope: Stage 7 (AI step evals)** | none |

### Out of scope for Stage 3
- AI/LLM node tests (Stage 7).
- The AI build loop and Keep/Undo sandbox (Stage 6).
- Durable runs (Stage 4).

## 5. The split: two parts, each with its own commit and report

### 3A: the testing core (about 3 weeks)
1. **Test case model** stored in the workflow definition (`tests: []`), versioned and restored with the workflow. Start from `DESIGN-WORKFLOWS-BACKEND.md` §5.1 and add `category` and `locked`.
2. **Types and data categories per node**, plus the coverage grid.
3. **Test runner in test mode:**
   - mocks via the pinned-data skip path;
   - mocked errors raised inside `_process_job`, so retries and `on_error` behave as in production;
   - strict offline mode, where an unmocked outside step fails with `TEST_UNMOCKED_EFFECT`;
   - frozen time and seed;
   - request capture;
   - automatic contract checks.
4. **Assertion evaluator:** `eq, neq, gt, gte, lt, lte, contains, matches, exists, not_exists, count, type, one_of, range, status, shape`.
5. **API catalog:** OpenAPI import, the HTTP node "API → endpoint" picker, and contracts plus default mocks from the spec (including errors). Database mocks come from the schema.
6. **Recipes per node type,** plus "suggest tests" from recipes and conditions (boundaries).
7. **Optional "Save as test"** from a real or past run.
8. **Scorecard:** branch coverage, the planted-bugs score (mutation operators listed in §4), and repeatability (20-run check).
9. **Studio:**
   - a Tests tab and the test editor (input, mocks, expectations, category);
   - the coverage grid;
   - node badges and the test bar;
   - one-click fixes for missing mocks;
   - the API catalog screen and the endpoint picker.
10. **Locked approved tests,** and release/publish blocked when an approved test fails.
11. **AI-ready foundations:** the toolkit functions, the scorecard function, structured results and the node catalog generator.
12. **Demo:**
    1. On Refund triage, with **no real runs**, register the Risk API by a small OpenAPI spec.
    2. Write the business cases.
    3. Accept the suggested boundaries and error cases.
    4. See the grid and "N/N passing · planted bugs X/Y".
    5. Break the If condition and watch the right test fail, pointing at the right node.
    6. Publish is blocked; fix it, and publish passes.

### 3B: AI and live layers (about 1.5–2 weeks)
1. **AI drafts test cases** for empty grid cells (drafts only; the user approves).
2. **AI variations / fuzzing** within the declared types, which reports any input that breaks a node.
3. **Model adapter**, with the fake LLM in every automated test.
4. **Drift alerts** on real runs, with stale-test marking.
5. **Opt-in live smoke tests,** run before a release or on a schedule.
6. **Demo:** the AI fills the grid gaps, a variation finds a real bug, and a drift alert fires when the fake API's reply changes shape.

## 6. Done when (each part)
1. **New tests pass:** unit, integration, an API journey and Playwright specs `16-…` (and `17-…` for 3B).
2. **Full engine suite:** 0 failures and the same 2 xfails.
3. **Studio checks:** unit tests, `tsc`, lint ≤ 96 and `design:check`.
4. **The full e2e suite passes.**
5. **The demo works,** and the lead has looked at screenshots of every UI change.
6. **Report in chat:**
   - what was built;
   - test counts before and after;
   - bugs found and fixed;
   - decisions and known gaps.

   Then wait for "commit".

## 7. First steps for the next session
1. Read this file, then `CONTRACTS-STAGE2.md`, `DESIGN-WORKFLOWS-BACKEND.md` §5, `engine/workflow_runner.py` (the pinned-data skip path and `_process_job`), `services/partial_run.py` and `engine/contracts.py`.
2. Record the baselines (§2).
3. Write `CONTRACTS-STAGE3A.md` and `PLAN-STAGE3A-EXECUTION.md`. Tell the owner the plan in chat as a short ordered list.
4. **Wave 1, in parallel:**
   - engine test model, runner test mode and assertions;
   - API catalog and OpenAPI mocks;
   - the studio Tests tab, editor and grid shell against the contract.
5. **Wave 2:**
   - recipes and suggestions;
   - scorecard and mutation testing;
   - studio wiring.
6. **Testers:**
   - backend integration and the API journey;
   - Playwright spec 16.
7. **Gates, demo and report.**
