# One-call backend builder (tested 2026-10-22)

One request → one model call → the platform checks it → at most 2 fix calls.
No plan step, no questions, no agent loop. Python stdlib only.

```
python build.py "Track vendor invoices; over $5,000 needs a manager to approve"
python eval.py 8          # the 8 bank cases, in parallel
```

Model: set `ANTHROPIC_API_KEY` and `LLM_MODEL` to use the API. Without a key it
uses the local `claude` CLI (`LLM_MODEL` defaults to `sonnet` there).

## The files

| File | What it is |
|---|---|
| `PROMPT.md` | The whole prompt: output format, the `db` API, test helpers, one worked example. |
| `kit.py` | The platform. Creates tables, validates every write, enforces roles, keeps the audit trail, runs every test on a fresh copy of the sample data. |
| `build.py` | Model call → parse → check → fix (at most 2). Writes `out/<slug>/schema.json`, `actions.py`, `reply.md`, `checks.json`. |
| `eval.py` | 8 typical bank requests, run in parallel, with a summary. |

The model writes two files:
- `schema.json`: tables, columns with types and choices, sample rows, saved queries;
- `actions.py`: `@action` functions plus `test_*` functions, each with a one-sentence plain-English docstring that becomes the line in the Checks panel.

## Results

| Run | First call passes | After fixes | Median time | Max time |
|---|---|---|---|---|
| 1. First contract | 0/8 | 8/8 | 66 s | 118 s |
| 2. Contract clarified | 7/8 | 8/8 | 42 s | 58 s |
| 3. Booleans fixed | **8/8** | 8/8 | **39 s** | 58 s |

Each app comes out at about 150 lines, with 8–16 tests in plain English. A hand review of the loan-exception case found correct limits (including the exact boundary), separation of duties, required reasons, no double decisions and requester emails.

### Harder cases (`python eval2.py`)

| Set | First call passes | Times |
|---|---|---|
| Related tables, 4 apps with 3–4 tables each (vendors → invoices → payments, loans → covenants → tests → waivers, audits → findings → action items, branches → tellers → counts) | 2/4, then **4/4** after fixing a platform bug | 45–70 s |
| Edits to existing apps: a due date with a default, fraud category with a 1-day SLA, an extra security-approval step, a second-analyst sign-off | **4/4** | 34–65 s |

Related-table apps have 14–19 tests each and enforce things like "a finding can't close while its action items are open", "a payment can't exceed the balance" and "only that branch's manager can sign off".

Every edit kept all existing actions and tests (13/13, 12/12, 9/9, 10/10), added 4–10 new tests, and passed **"existing data still fits"**. That check means the rows people already entered can move to the new schema: nothing removed, renamed or retyped, and new required columns have defaults.

Platform additions for these cases:
- `ref` columns, checked on every write, with delete refused while other rows still point at the row;
- column `default`s;
- the existing-data check for edits (`existing_data_fits` in `kit.py`);
- the edit prompt in `build.py`.

The one failure was ours again: the test helper `t.run(name, ...)` clashed with action inputs called `name`. It's now positional-only.

## E2E step 1: the app runs as a server (done)

```
uv run --no-project --with fastapi --with uvicorn python server.py fixture/vendor-payments --port 8100
python api_test.py fixture/vendor-payments      # starts its own server, 31 HTTP checks
```

Generated apps (`out/`, `out2/`, `apps/`) are not kept in git; every script rebuilds what it needs. Two things are kept
on purpose:
- `fixture/vendor-payments/`: one known-good app (the vendor-payments edit: `schema.json`, `actions.py`, `App.tsx`,
  `journey.json`, and the hand-checked direct-editing test `crud.json`). It tests the runtime without any model call.
  `api_test.py` part B is written for it.
- `ui/example-journey.json`: a real passing journey. It is the `{{EXAMPLE}}` in the journey prompt.

`server.py` is written once and is the same for every app; nothing in it is generated. It provides:
- **Data kept in a file:** `APP_DIR/app.db`. Sample rows go in only the first time, and new columns from edits are added in place, with their defaults.
- **Generic CRUD for every table:** list (filter, search, sort, page), open a row with its change history, add, edit and delete.
- **Rules on generic CRUD:** only roles in the table's `edit_roles` may add, edit or delete; `locked` columns (status, approvals, amounts) change only through actions; a row other rows point to can't be deleted.
- **Actions** at `POST /api/actions/{name}`: roles are enforced (403), refusals return 400 with the message, and each call is one transaction, so a refused action writes nothing.
- **Saved queries and the outbox.**
- **Identity:** `X-User` and `X-Role` headers, a stand-in for the studio login.

`api_test.py` drives the app only over HTTP, the way the studio would, and restarts the server partway through. The full vendor journey passes all 31 checks:
- a member can't add vendors, but a manager can;
- locked status can't be edited directly;
- a $12,400 invoice needs approval, and paying before approval is refused;
- a member can't approve, and nobody can approve their own invoice;
- a partial payment leaves the invoice partially paid, and an overpayment is refused and writes nothing;
- after the restart the data and history are still there;
- paying the rest marks it paid, and the submitter is emailed;
- a vendor with invoices can't be deleted.

## The lesson: the failures were the platform's, not the model's

All the failures in run 1 came from ambiguities in the contract we gave the model, not from bad logic. Each fix was one line in `kit.py` or `PROMPT.md`:

- **Saved queries.** The model invented parameters (`:vendor`) that nothing supplies. Now queries get only `:me` and `:today`, and the platform fills them in.
- **Identity.** It was name in one place and email in another. Now people are always identified by email (lowercased), and `as_user` in tests is an email.
- **Negative money.** `money` refused negative values, but differences and breaks are signed. That rule is gone.
- **Booleans.** They came back as 0/1. They're now returned as real booleans.

**When a first call fails, fix the contract, not the prompt wording, and don't add a repair step.** Rerun `eval.py` after every change, and keep any change only if the first-call pass rate doesn't drop.

## What's deliberately missing (add one at a time, only if needed)

- The page/UI layer: `useTable`, `useView` and `useAction` over these actions come next.
- Real storage: swap sqlite `:memory:` for Postgres behind the same `DB` methods.
- Sandboxing `exec` and a real email sender. The current import allow-list is a speed bump, not security.
- Generic CRUD (list, filter, view, edit and delete any row) is the platform's job, not the model's: write it once over `DB`, with the same validation, roles and audit trail.
- The tests are written by the same model. Keep reviewing a sample by hand.

## E2E step 2: the page (UI)

    python ui_build.py APP_DIR "original request" [--port 8130]

- One model call writes `App.tsx`. The prompt is `ui/UI_PROMPT.md`, plus the real `ui/kit.tsx` source (minus the CSS), `ui/example-App.tsx`, the live `/api/app` and 3 sample rows per table.
- The page may import only `react` and `./kit`. Data comes through `useTable` / `useQuery` / `useRow`; every change goes through `<ActionButton action=…>`. That button auto-builds the form, hides itself from roles that can't run the action, refreshes everything and shows errors. `<RecordTable>` handles direct editing for tables with `edit_roles`.
- `ui/check.mjs`:
  - type-checks the page against the kit;
  - bundles it with esbuild;
  - loads it in real Chromium against the running server, once per role (the same X-User/X-Role headers), and clicks every tab.
- It fails on: console errors, page errors, any `/api` 4xx/5xx, a crash, an empty page, or a table column key that isn't in its rows.
- At most 2 fix calls.

| app | first call | time | lines |
|---|---|---|---|
| vendor-payments | pass | 28s | 155 |
| branch-cash | pass | 25s | 122 |
| loan-covenants | pass | 28s | 151 |
| audit-program | pass | 27s | 193 |

`/api/app` lists each saved query's real columns. Without that, a page can show a column a query never returns; my own hand-written example did exactly that, and the checker caught it.

## E2E step 3: click through the app in the browser

    python journey_build.py APP_DIR "original request"   # model writes journey.json, robot runs it

- `ui/journey.mjs` is the robot. It runs short JSON steps in Chromium on fresh data, as each role:
  - `as`, `tab`, `click` (optionally within a row), `fill`, `submit`, `close`;
  - checks: `see`, `error`, `shows`, `no_button`;
  - `api` checks the stored values over HTTP.
- It stops at the first failure and says what was on screen instead. It was proven to fail on 7 planted wrong expectations.
- `ui/JOURNEY_PROMPT.md` is one model call. It sees the request, `/api/app`, `actions.py`, the sample rows and `App.tsx`, and writes the journey: each role's main job end to end, refusals, hidden buttons, and a data check after every change.
- There is no automatic fix loop. A model fixing its own failing test can hide a real bug, so a failure goes to a person or the builder to judge.

| app | steps | roles | data checks | refusals | hidden buttons | result |
|---|---|---|---|---|---|---|
| vendor-payments (hand-written) | 53 | 2 | 8 | 2 | 4 | pass |
| branch-cash (model) | 82 | 4 | 13 | 6 | 3 | pass |
| loan-covenants (model) | 74 | 3 | 16 | 3 | 7 | pass |
| audit-program (model) | 74 | 2 | 11 | 4 | 9 | pass |

Each model journey took about 30s to write. The first run failed on 3 of the 3 model-written journeys; the causes were:

- A real kit bug, found by the branch-cash journey: an action input `new_branch_id` showed a number box, not a branch dropdown. The kit only recognised `branch_id`. Fixed by matching qualified names and plurals.
- Robot gaps, now fixed:
  - the tab "Waivers" also matched "Waivers to decide";
  - `close` failed when the button had no form to open.
- None were model mistakes in the app.

## Edits, end to end (`edit_e2e.py`)

An edit uses the same three calls as a new app:
1. The backend call: add-only, migrate, the app's own tests.
2. The page call: gets the current `App.tsx` plus the change, and keeps everything else.
3. The old journey reruns as a regression check.
4. The journey call writes a new journey.

| change | backend | page | old journey | new journey |
|---|---|---|---|---|
| vendor-payments: payment method ACH/wire/check, required | 1st call, 43s | 1st call, 31s | stops at "record payment" (correct: method now required) | 89/89 |
| audit-program: one extension per action item, auditor approves/declines | 1st call, 72s | 1st call, 44s | 74/74 (nothing broke) | 82/82 |

Journey mistakes found while doing this, all fixed in `ui/JOURNEY_PROMPT.md`:
- it guessed ids of new rows;
- it assumed values the app assigns (who becomes the lead);
- my prompt misdescribed when a form opens: only when a required input is missing.

`journey_build.py` now makes one judge call on failure. The model must say "Test wrong: …" (corrected journey, rerun) or "App bug: …" (stop for a person). In the final run all 5 journeys passed first time, so the judge wasn't needed.
