# Demo readiness

Scope: harden the existing database → generated backend → generated UI → publish flow.
Files and analytics expansion remain deferred. The historical `HANDOFF-UI-FINISH*.md`
documents describe earlier states; use `UI-PROGRESS.md` and this document for current work.

## Architecture and supported deployment

Keep Studio, engine, and analytics separate. Keep the shared node registry, generated Python
SDK, policy broker, sandbox, project/environment references, and release snapshots.

The engine currently supports one worker. Tokens, cancellation, generation locks, and
schedulers use process-local state. Run a controlled demo on an isolated, trusted network.
Management authentication and user/project authorization are not implemented; an actor
header is audit metadata, not an authorization mechanism. Do not expose management APIs
as a bank-wide service. Apply migrations explicitly before startup: startup only checks DB
connectivity. Keep encryption/signing configuration stable across restarts.

## Hardening

- The preview fetch bridge permits selected project routes and explicitly bound workflow
  runs only. It rejects management endpoints, other projects, external destinations, and
  redirects; forwarded requests omit browser credentials. API-created apps must explicitly
  bind routes in `definition.studio.route_ids` (the UI route picker manages this binding). The iframe CSP blocks direct
  fetch/XHR/WebSocket requests and form submission.
- React and Tailwind preview runtimes are lockfile-pinned local dependencies, embedded into
  the iframe. Preview utility generation uses Tailwind 4.1.17; publication retains the existing
  Tailwind 3 compiler. Custom styles should be checked in both preview and published output.
- Outdated bundle completions and tokens belonging to another app are discarded. Changing
  apps remounts the preview. Runtime startup has a timeout and Retry.
- The Create UI dialog waits for its initial route selection before exposing the picker or
  allowing submission, avoiding selection changes during initialization.
- Release app publication uses the release transaction; it never commits individual app
  pointers. Bundle failure rolls back publication and returns `release_publish_failed`,
  leaving the previous active release in place.
- Sandbox stdout and stderr are drained continuously, retaining at most 64 KiB each.

## Verification

The branch retains the isolated stack instructions in `E2E.md` and real Gemini/Postgres
walkthrough in `apps/workflow-engine/tests/live/README.md`. Run only one pytest or Playwright
process at a time. Provider credentials must be loaded as environment variables from the
ignored configuration, never copied into code, screenshots, reports, or commits.

Fresh checks on 2026-10-16:

- Engine: 307 passed, no skips, five existing Pydantic warnings.
- Studio unit tests: 34 passed; TypeScript and production build pass. Existing large-chunk
  warning remains.
- Full isolated Playwright suite: 48 passed in one run, including the new preview-boundary
  regression. Report: `screenshots/demo-verification/2026-10-16/playwright-full.json`.
- Real Gemini/Postgres browser walkthrough: passed fresh generation, refinement, Create UI,
  UI-saved/rotated credentials, live data refresh, and publishing. Report:
  `screenshots/demo-verification/2026-10-16/postgres-live.json`.
- Screenshot capture suite: passed; all 28 light/dark PNGs refreshed in
  `screenshots/ui-built/`.

The final retained banking app was also replayed against a fresh real Postgres database:
filters, pagination, chart value differences, joined details, accounts/cases, publication,
updated data, and database-error recovery passed. This reused the previously generated
backend/UI and made no new AI generation calls. The replay fixture now explicitly binds its
six routes; an older draft was rejected by the unchanged chart assertion before the final
retained source passed. Report: `screenshots/demo-verification/2026-10-16/banking-replay.json`.

## Demo walkthrough

1. Save the synthetic database credentials through Secrets and reload to confirm persistence.
2. Generate a backend against that database; test actual responses.
3. Refine the backend with one meaningful change.
4. Create its UI, verify displayed values, filters, and loading/error states.
5. Publish; update database data and show the change in the app.

Use synthetic data. Keep the existing banking example as a prepared fallback; distinguish
replayed source from fresh generation. Neither a successful demo nor the current automated
suite establishes enterprise authorization, multi-worker correctness, S3 support, or load capacity.

## Additional app ideas (2026-10-16)

Three new projects passed fresh real-Gemini backend and UI generation, browser preview,
publication, saved-secret checks, and independent real-Postgres assertions:

- Storefront: actual orders and stock changes in preview and published UI; invalid quantities
  and unavailable stock rejected; two simultaneous requests for the last stock produce one
  success and one conflict, without overselling. No payment processing is involved.
- Support desk: filtering, persisted ticket resolution, invalid status rejection, and missing
  ticket handling. Published UI retains the preview's changes.
- Expense dashboard: grouped totals, department filters, Refresh after a direct database insert,
  and published views of the updated data.

No product-code changes were needed for these cases. The opt-in runner is
`apps/workflow-engine/tests/live/run_app_ideas.py`; instructions are in the adjacent README.
Evidence: `screenshots/app-ideas/report.json` plus three preview and three published screenshots.
This verifies these specific journeys; it does not establish arbitrary app correctness or
production commerce readiness.

## Banking showcase redesign (2026-10-16)

The retained banking UI was redesigned around the existing real API contracts. It adds light/dark
themes, volume and settlement visualizations, regional portfolio exposure, priority queue,
account search/sort, exception severity/search, CSV page export, and a keyboard-accessible drawer.
Missing summary values render as unavailable instead of invented zeros; detail requests discard
stale results. Existing server filters, pagination, joins, refresh and error recovery are preserved.

Exact retained source passed the complete preview/published banking journey against fresh real
Postgres. Additional checks passed for theme contrast, chart keyboard focus, account search/sort,
case filtering, CSV download, drawer focus/Escape, and no horizontal overflow at 1024px and 390px.
The phone fix establishes the scroll container as the containing block for hidden table labels;
it does not hide overflow at the page level.

This pass used the retained AI-generated backend and manually designed UI, with no new model calls.
Repeat with `BANKING_EXAMPLE=1 BANKING_DESIGN_PASS=1` and `run_banking_app.py`; no Gemini key is needed.
Local screenshots and JSON report are archived under `screenshots/banking-redesign/`.
