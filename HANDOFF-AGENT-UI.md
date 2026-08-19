# Agent surface — state & brief for the next session

**Written 2026-08-19, end of the session that shipped it.** Everything below is
committed, tested, and was running live when the session closed.

## What exists now (verified)

**Backend (apps/workflow-engine, suite 146 green):**
- The four agent-run lifecycle bugs from the 2026-08-18 review are fixed
  test-first (`tests/services/test_agent_run_service.py`): double-cancel can no
  longer skip `_finalize`; terminal writes are atomic-conditional
  (`finalize_run_if_active`, first terminal write wins); tool executors get a
  session *factory*, never a closed session; the no-live-task cancel path
  releases the session.
- HANDOFF-SDK-COMPLETION.md is **done** (status note at its top): subprocess
  sandbox (`src/engine/workflow_sdk_sandbox.py`), `SDK_TYPES` derived from the
  registry (add-a-node drill passes), and the `"sdk"` tool source resolves a
  THREE-tool kit (`src/services/workflow_sdk_tool_service.py`):
  `build_workflow` / `list_workflows` / `run_workflow` — build → discover →
  execute → **chain**, proven end-to-end against real Postgres in
  `tests/services/test_workflow_sdk_agent_tool.py`.
- `POST /api/workflow-sdk/execute` + `GET /api/workflow-sdk/reference`
  (`src/routes/workflow_sdk.py`) — the human door onto the same sandbox core.
- Migration `20260819150000_sdk_tool_source.sql` widens the
  `agent_tool_bindings.source` CHECK to include `'sdk'`. **The Pydantic
  `ToolSource` Literal (schemas/agent.py) and this CHECK must change
  together** — Pydantic accepting a value the DB rejects is a 500, not a 422.
- One-shot eval: 10/12 (was 7/12) after constructor-misuse/import teaching
  errors; `scripts/sdk_eval_harness.py --print-prompt/--grade` lets any model
  runner reuse identical prompts + mechanical grading.

**Studio UI (apps/workflow-studio, tsc 0, browser tests green):**
- `/build` — Script → Workflow: CodeMirror pane, Run (sandboxed, validated even
  if the script forgets), graph via `WorkflowSVG` (new `showLabels` prop),
  per-node `line N` provenance chips, Save → `/editor?workflowId=`.
- `/agents` — fleet rail (role verb derived; sdk toolkit ⇒ `builds`),
  create-agent form (binds the sdk toolkit), trigger form, polling runs table,
  run-detail panel with the `after_seq` event stream (live and replay are one
  view). A failed run shows its error verbatim.
- `sdkApi` / `agentsApi` in `src/shared/lib/api.ts`; routes registered per the
  house 5-step convention; `tests/agent-surface.spec.ts` (4 tests) +
  `shell.spec.ts` (13) green.

## Environment (the part a fresh session must know)

- **Engine Postgres is on host 5433 now** — `workflow-engine-postgres-1` was
  recreated with the same `workflow-engine_pgdata` volume publishing
  `5433:5432`; `apps/workflow-engine/.env` (untracked!) sets
  `WORKFLOW_DB_PORT=5433`, and `.env.example` documents it. analytics-pg keeps
  5432; **both run together** — the old swap dance is obsolete. If the engine
  container is ever recreated from the old compose file it will grab 5432
  again; recreate with `-p 5433:5432`.
- To run everything: `docker start analytics-pg workflow-engine-postgres-1`,
  engine API `cd apps/workflow-engine && venv/bin/python -m uvicorn
  src.main:app --port 8000`, analytics API on 8001 as usual, studio
  `cd apps/workflow-studio && npx vite --port 5174 --strictPort`.
- Browser tests: `npx playwright test agent-surface.spec.ts` (engine API must
  be up; the spec's beforeAll says so loudly).

## Evaluation update (2026-08-19) — read EVALUATION-AGENT-PLATFORM.md first

The honest evaluation ran. Verdict: **good demo of real plumbing, not yet a
useful product — and not safe to show without fixes.** The list below stands but
is reordered by what the evaluation proved. New blockers it surfaced, above
everything previously listed:

- **SECURITY (Tier 0, undeployable until fixed).** The SDK "sandbox" leaks the
  engine's `.env` (DB password, the key that decrypts every tenant's credentials,
  a live LLM key) to an **unauthenticated** caller — *proven live* via an
  attribute-chain escape past the curated `__builtins__`. There is **no auth or
  team scoping anywhere** (cross-tenant read/write is the default). The Code node
  is in-process RCE with the full un-scrubbed env. Masking exists only on the
  analytics HTTP seam, so raw `code`/`neo4jQuery`/Postgres tools leak at exactly
  the tool call VISION markets as protected. → real sandbox isolation + auth +
  govern the data plane, not individual tools.
- **THE ONE DEMO ARTIFACT FAILS WHEN RUN.** `demo-weekly-export` run model-free
  → `failed` (HttpRequest 404 + Postgres auth). The "£0.02/year, runs forever"
  workflow has never succeeded. Reseed one that works before any demo.
- **`requires_approval` is a live, settable NO-OP** (stored, serialized, never
  enforced). Approvals below are not just missing UI — the flag actively lies.
- **Orphaned agent runs are never reaped** → run + session stuck forever on a
  crash (2 zombie `queued` runs prove it). The StaleReaper covers only workflow
  executions.
- **Litter is on-screen:** 19 of 20 agents are junk, ~22 of 31 runs failed.
  Purge before showing (confirm the delete list first).
- **Role-derivation drift is live:** the seeded agent is bound to `build_workflow`
  but reports `role:"asks"` — role is stored at write, never derived at read.
- **Correct-the-record:** empty states ARE handled in the built `/agents` page —
  the "never drawn" claim is stale, stop repeating it. `/build` is genuinely
  deterministic and works with no model. The run-detail evidence trail is good.

Deliverables from the session: `EVALUATION-AGENT-PLATFORM.md` (ranked findings +
Now/Next/Later), `DEMO-SCRIPT.md` (1-page, funded + unfunded tracks),
`screenshots/eval/*`.

## Feature fixes (2026-08-19, later) — create→run now works in the UI

The two feature breaks the evaluation surfaced are fixed and validated (engine
suite 146 green, studio tsc 0, 5 agent-surface browser tests green + a live UI
create→run reaching success):

- **Model selector.** The create-agent form hardcoded `claude-sonnet-5` (dead
  credit) and had no way to change it. New `GET /api/models`
  (`src/routes/models.py`) returns a catalog flagged by which provider key is
  configured; the studio create form (`AgentsPage.tsx`) now has a model
  `<select>` (`modelsApi` in `api.ts`) that preselects the backend default
  (`gemini-3.6-flash`, the one with credit). Seed agent model also updated.
  `gemini-3.6-flash` added to `GEMINI_MODELS`.
- **False-success fixed.** `agent_runtime.py::_to_outcome` now returns
  `status=failed` when a turn's tool calls ALL errored (no-tools Q&A and
  mixed-outcome turns stay `success`). Verified against the pre-fix all-errored
  case.

Still open from the sweep (not breaks): no agent-edit UI (can't rename/re-model
an existing agent), and the "open in editor" links use `<a href>` full reloads
(fine on the dev server; would need SPA fallback on a static host).

## What is NOT done (the honest list, in the order I'd do it)

1. **LLM credit.** The Anthropic key is valid but out of credit — a triggered
   run 202s then fails with an auth error (shown honestly in the run panel).
   The moment credit exists, `/agents` is the live loop with zero code changes.
   Then: run the real end-to-end (agent builds → runs → chains workflows) and
   re-run the SDK eval through the actual agent loop instead of one-shot.
2. **Design-fidelity pass on /build** (the SDK trio in
   `design-prototypes/terminal-agent-sdk*.html`, screenshots in
   `screenshots/agent-designs/`): reference/evidence right panel, per-line
   done/pending/raised states, cost table, publish-checkpoint-styled confirm.
   The data for all of it already comes back from `/api/workflow-sdk/execute`.
   Also fix the stale guard text in `terminal-agent-sdk.html` lines ~312-314 —
   it still claims in-process execution; the subprocess sandbox landed.
3. **Approvals** — `agent_approvals` table exists with zero code. Needed for
   the checkpoint screens (`requires_approval` on bindings is decoration until
   then). Design: `terminal-agent-checkpoint.html`.
4. **Run trace screen** per `terminal-agent-run.html` — the current run panel
   is a compact version; the full-page trace (iteration separators, tool-call
   cards, sub-agent lanes) renders the same 9 event types we already persist.
5. **SSE for agent runs** — polling works and renders identically; the
   recorder's `subscribe()`/`END_OF_STREAM` exist unused if SSE is wanted.
6. **Tools binder / connectors screens** — blocked on connector discovery
   having real data (zero connectors registered today).
7. **Schedule / promote (the flywheel)** — tables with zero code; post-Tier-0.
8. Deliberately skipped: `.pyi` stub generation (SDK-DESIGN §3) — the
   derivation work prevents drift; stubs only serve humans.

## Shell-agent live test (2026-08-19, after commit)

A Claude Code Sonnet subagent played the platform agent as a black box — HTTP
only, no source access: read `/api/workflow-sdk/reference`, built
`shellagent-demo-normalize` and `-report` (0 failed builds), ran both, and
chained run 2 from run 1's real output ({count: 2, names: [ALPHA, BETA]}).
Both executions `success`; workflows kept in the DB for inspection. Findings
worth fixing:
1. **Code node's runtime item shape is undocumented** — `items` is
   `[{json: {body: ...}}]`, so the agent's first transform silently no-op'd
   (pass-through, no error). Document the shape in `signature_reference()`'s
   Code entry, or normalize it.
2. **The agent debugged by persisting 12 probe workflows** — empirical and
   effective, but litter. The toolkit should advertise a dry execution path
   (POST /api/workflows/run-adhoc exists and was never surfaced to it).
3. The workflow-Code-node runtime sandbox lacks builtins agents reach for
   (`dir`, `globals`, `Exception` at that layer) — fine as policy, but the
   error could teach like the SDK's do.

## Where the full context lives

Memory: `agent-run-service-fixes`, `agent-sdk-review-fixes`,
`single-5432-port-conflict` (resolved). Docs: `HANDOFF-SDK-COMPLETION.md`
(status note), `HANDOFF-AGENT-PLATFORM.md`, `SDK-DESIGN.md`. Design digest:
the 20 prototypes were fully analyzed 2026-08-19 — Tier-0 slice is
home/session/run/checkpoint/ask; the SDK trio is the best-backed flow.
