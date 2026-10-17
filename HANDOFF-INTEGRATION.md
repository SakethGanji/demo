# Handoff: integrate and test everything (next session)

**Written 2026-10-16.** Start here, then follow `INTEGRATION-TEST-PLAN.md` (the session plan, with a kickoff prompt in §5). This supersedes the older `HANDOFF-*.md` files in this folder. All paths below are relative to `demo/` (start the session there).

## Where things stand
- **The Command Studio design is built into `apps/workflow-studio`** and committed on `revert-to-5684ba5` (one commit `0bd1707`, not pushed; integration fixes go on top as separate commits, one per area; push only when the owner says so). History of how it was built: `design/IMPLEMENTATION-PLAN.md`; the rules for feature code: `apps/workflow-studio/DESIGN-STANDARD.md`; what lives where: §7 of `design/handoff/MAPPING.md`.
- **Checks at handoff:** 51/51 Playwright scenarios (isolated stack, fake LLM), capture spec passing, `npx tsc -b` clean, 42 unit tests, `npm run design:check` clean. Lint: 117 problems vs 96 before the rollout (mostly fast-refresh advisories).
- **Look and feel the owner approved (keep it):**
  - Linear-style neutrals in both themes: dark canvas `#08090a`, panels `#0f1011`/`#18191b`; light is pure white; no navy or blue tint anywhere in surfaces.
  - Blue only for meaning (primary actions, focus, selection, the focal value in a chart).
  - Charts and line art are flat; fills use a crossed-line hatch; no gradients or glows.
  - Working pages have no stars. Projects home and the landing have a coloured SVG sparkle sky in a band at the top that fades out by ~460px, and none of it sits behind text.
  - Cards are quiet panels; frosted glass only on floating layers (menus, ⌘K, sheets, dialogs, toasts). The sticky top bar on Projects home and the landing frosts only when content scrolls under it.
  - Liquid glass (`shared/lib/liquid-glass.ts`) on the sliding thumbs, the active sidebar item and dropdown/popover edges; Chromium only, off under reduced motion; in light mode the lens stays nearly clear.

## Environment
- `apps/workflow-engine/.env.local` (gitignored) holds: the new `GEMINI_API_KEY` (the previous key is kept commented), `WORKFLOW_MODULE_GEN_MODEL` and `WORKFLOW_APP_BUILDER_MODEL` (`gemini-3.8-flash`), `WORKFLOW_ENCRYPTION_KEY` for the dev DB, and `WORKFLOW_DB_*` pointing at `workflows_studio_dev`. A live Gemini call returned OK on 2026-10-16. **Never print the key or copy it into committed files, logs, screenshots or agent prompts.**
- Postgres: Docker `workflow-engine-postgres-1` on `:5433` (`docker start workflow-engine-postgres-1`).
- Dev engine: `cd apps/workflow-engine && set -a && . ./.env.local && set +a && venv/bin/python -m uvicorn src.main:app --host 127.0.0.1 --port 8000`.
- Dev studio: `cd apps/workflow-studio && npx vite --port 5173 --host 127.0.0.1`.
- `workflows_studio_dev` already has three projects (Payments Ops, Billing sync, Support desk) and seeded workflows. The original `workflows` DB has an outdated schema; leave it alone.
- The live test scripts in `apps/workflow-engine/tests/live/` build their own allow-listed environment and their own `workflows_test_e2e_*` database, so `.env.local`'s DB settings don't leak into them.

## Decisions already made
1. Commit first: done.
2. Live Gemini calls for the Marketplace scenario: approved (budget ~40–80 calls; report the actual count).
3. Build the backend gaps: bulk row insert (CSV import uses it), response status code on the run list, and a 30-day daily run aggregate for the Overview grid.
4. From the rollout: the workflow node-details modal stays (no inspector pane); run details keep the `/runs/$executionId` sheet plus inline row preview; the refund-triage double write is untouched.

## Known issues to handle
- `apps/workflow-studio/e2e/tests/10-code-map.spec.ts:94` ("native map: drafts…") is flaky: failed twice, passed on rerun. Make it deterministic.
- Data gaps still approximated client-side until §0.3 lands: the 30-day grid and 24h numbers come from the newest ~100 runs, CSV import loops single inserts, run rows show no status code.
- Table lineage is derived by scanning module source for `db.table("…")`; workflow-backed routes aren't detected.
- Liquid glass is verified only in Chromium. Firefox and WebKit need a smoke run.
- Lint is above the pre-rollout count.

## Process lessons (from the rollout session)
- **One e2e run at a time.** The suite keeps its stack in `e2e/.stack/state.json`; parallel runs clobber each other. Wrap every run in `flock <lockfile> npx playwright test -c e2e …`.
- After an aborted run, kill orphan `uvicorn --app-dir …` and `vite --port <n> --strictPort` processes **by PID** (`pkill -f <pattern>` can match and kill your own shell) and drop leftover `workflows_test_e2e_*` DBs with `apps/workflow-engine/tests/e2e_stack.py down --db <name>`.
- Keep at most 3–4 agents running: a session rate limit stopped six agents mid-task last time. Agents never touch git.
- Take screenshots with motion **on** when checking liquid glass (reduced motion turns it off by design).
- `Roll` (rolling digits) is plain text at rest; don't assert on text mid-roll.
- Home templates must create their tables with the project (the analyser can't infer columns from a pass-through body).

## Update 2026-10-16 (end of integration session)
- **Done:** code-map flake fixed (handle bounds re-registered; 90/90 repeats); backend gaps built (status code on runs, `GET /runs/daily`, chunked CSV import; `WORKFLOW_RUN_RETENTION` env, default 100); egress allowlist now enforced on workflow HttpRequest nodes; module generator prompt states the single-`req` handler contract; lint back to 96.
- **Tests:** e2e 114 (11a–11d, 12, 13 are new), studio unit 105, engine unit 262, full engine suite 509 passed + 2 strict xfails (number-secret validation, global run retention). Fast loops: `venv/bin/python -m pytest tests/unit -q` (~2 s), `npm run test:unit`.
- **Live Gemini run:** `apps/workflow-engine/tests/live/run_marketplace.py`: 17/18 steps in the full run, s11b passed in a supplement (97 calls total across iterations). One clean full run is ~20 calls. Artifacts in `screenshots/marketplace/` (uncommitted).
- **Still open:** Firefox/WebKit smoke, visual review gallery, axe/perf pass, `/api/files/*` unauthenticated, Runs subtitle counts only the loaded page, builder API tester returned 501 against a GET-only upstream (uninvestigated).
- Business overview doc: the "Command Studio: Platform Overview" Claude doc.
