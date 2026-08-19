# Evaluation — the agent platform, as a product

**Run 2026-08-19.** Four-pass evaluation per `HANDOFF-EVALUATION-SESSION.md`:
journey walkthrough (real browser), backend surface audit, backend
security/durability audit, design-vs-built reconciliation. Method: three
parallel evaluator subagents + a demo-ware detector, every load-bearing claim
re-verified live by hand against the running engine (:8000) and studio (:5174).

> ## ⚡ Live-loop update (2026-08-19, later — paid Gemini key provided)
> A paid Gemini key was supplied and I ran the **real** agent loop for the first
> time. Two bugs blocked it; I fixed both, 146 tests stay green:
> 1. **The flagship SDK toolkit never worked through a real model.** The runtime
>    calls tool executors as `executor(input_data, context)`, but
>    `build_workflow`/`list_workflows`/`run_workflow` were written with spread
>    signatures (`execute(script, name, …)`), so the args-dict landed in `script`
>    and the `ExecutionContext` in `name` → `'ExecutionContext' has no attribute
>    'strip'`. The "proven e2e" test swapped in a stand-in runtime using the
>    wrong (kwarg) convention, so it never caught this.
>    *Fixed:* the three executors now accept both conventions
>    (`workflow_sdk_tool_service.py`).
> 2. **Gemini 3 tool-calls broke on turn 2.** The google-genai adapter dropped the
>    `thought_signature` on each functionCall; Gemini 3.6 400s on the follow-up
>    turn without it. *Fixed:* signature is captured on parse, round-tripped
>    base64 through the assistant message, and replayed when rebuilding history
>    (`llm_provider.py`).
>
> **Result — the whole thesis now works live, end to end:** a real model
> (`gemini-3.6-flash`) authored a workflow from plain English, saved it,
> discovered it via `list_workflows`, and executed it via `run_workflow` reading
> the real output back — build → discover → execute → **chain**, all through the
> model (studio screenshot: `screenshots/eval/LIVE-gemini-run-ui.png`). The
> produced workflow then **runs model-free** with correct output. This retires
> the two most damaging Tier-1 findings below (P2 "un-demoable live", P3 "no
> model has ever authored a workflow") — they are now demonstrably false.
>
> **Still true / newly found:**
> - **False-success is a real latent bug.** Before fix #1, a run where *every*
>   tool call errored still finalized as `status:success` (the runtime swallows
>   tool exceptions into `{"error": …}` at `ai_agent.py:2742`, and the loop
>   terminating counts as success). A run that produced nothing should not be
>   `success` — worth a correctness fix.
> - The engine's Gemini model registry is stale (`gemini-2.5-*`); only
>   `gemini-3.6-flash` is reachable with this key. `GET /models` still absent, and
>   the studio create-agent form still hardcodes `claude-sonnet-5` (H3) — I had to
>   create the Gemini agent via the API, not the UI.
> - **P1 stands:** the *old* `demo-weekly-export` seed still fails when run. But
>   `gemini-live-eval` is now a genuinely working demo agent whose outputs run.
> - Anthropic (`claude-sonnet-5`) is still out of credit; the Gemini key does not
>   change that. The security findings (S1–S4) are unchanged and now more urgent —
>   a *paid* key sits in the `.env` that S1 exfiltrates unauthenticated.

> ## 🗄️ Persistence validation (2026-08-19 — direct against engine Postgres)
> Verified the store by querying Postgres directly (schema `workflow-app`, db
> `workflows`, host 5433), not just the API:
> - **Agent-authored workflow is stored correctly.** `gemini-live-final`'s
>   `draft_definition` JSONB holds the real graph (Start→Code, connection
>   `Start.main→Code.main`, Code body preserved verbatim). **API `definition`
>   deep-equals the DB `draft_definition`** (nodes + connections, full match).
> - **Run + event stream intact.** The success run row carries the right snapshot
>   (`gemini-3.6-flash`, sdk/`build_workflow`), real tokens (8327/166), and its
>   stored `event_count` matches the actual `agent_run_events` rows (5=5), in seq
>   order (tool_call→tool_result→tool_call→tool_result→response). Chain run's
>   `run_workflow` result persisted the real output `[{"json":{"greeting":"hi"}}]`.
> - **Referential integrity clean:** 0 orphan runs (no session), 0 orphan events
>   (no run). **Durability:** all runs from before my 3 engine restarts survive
>   (Postgres-backed). **Executions:** 2 `success` model-free runs of the
>   agent-authored workflow are persisted; the old `demo-weekly-export` shows
>   `failed` executions (P1).
> - **Gaps noted:** workflows persist with `created_by = NULL` (no auth/attribution
>   — S2); the **false-success bug is DB-confirmed** — the pre-fix run stored
>   `status=success` with 5/5 tool_results `is_error=true`.

> **The question the handoff asked: "really useful product, or a good demo?"**
> **Answer: a *good demo of real plumbing*, not yet a useful product — and not
> yet a safe or honest demo without three fixes.** The build→persist→run→chain
> machinery is genuinely built and tested at the function level. But every claim
> that depends on a model actually running is stubbed or seeded; the one artifact
> the demo produces fails when you run it; and the governance thesis the whole
> product is sold on is not just un-enforced — it is contradicted by a live,
> unauthenticated secret leak I proved by hand.

---

## What actually works (credit where due — verified, not assumed)

- **`/build` is real and deterministic.** Script → graph → Save runs through the
  sandbox with **no model in the loop**. I ran the example script through
  `POST /api/workflow-sdk/execute`: `ok:true`, 7 nodes, persisted with a
  `workflow_id`. `persist` defaults to `false`, so a dry Run does *not* litter
  the DB. This is the one honest piece of the VISION "AI is author, not runtime"
  thesis that you can show today.
- **The auditor's evidence trail is genuinely good** — on the seeded run. The
  run-detail panel shows the full chain: `agent:thinking` → `agent:tool_call`
  (with the verbatim script) → `agent:tool_result` (the persisted workflow JSON)
  → `agent:response`, plus a **produced-workflow graph with "open in editor →"**.
  Screenshot: `screenshots/eval/A3-run-detail-seeded.png`.
- **Failures surface honestly.** A run that dies before its first model call
  shows the verbatim `BadRequestError … credit balance is too low` and an honest
  "No events — a run that fails before its first model call leaves none."
  (`screenshots/eval/A5-run-detail-failed.png`.) `/build` shows `NameError` /
  `SyntaxError` in a bottom strip, no crash (`eval/B3`, `eval/C1`).
- **Empty states ARE handled.** "No agents yet", "No runs yet", the no-events
  line (`AgentsPage.tsx:266-270,375-381,490-494`). **The design-index/handoff
  claim that empty states "were never drawn" is STALE for the built page — do
  not repeat it.**
- **`run_workflow` chain output is truncation-bounded** at 20k chars
  (`workflow_sdk_tool_service.py:314-333`) — the feared unbounded-context hole
  does not exist. The agent-run finalize/cancel races are handled
  (`agent_run_service.py:53-72`). The `409 session-busy` contract is
  prototype-faithful to the letter (`agent_runs.py:60-68`).

---

## Ranked findings

Severity = damage if shipped/shown. **[LIVE]** = I reproduced it by hand today.

### Tier 0 — security: the governance thesis is contradicted, not merely absent

- **S1 · CRITICAL [LIVE] · Unauthenticated sandbox escape leaks the engine's
  `.env`.** The SDK "sandbox" scrubs the child's *environment* but runs `exec`
  with `cwd=<app root>`, no fs/namespace isolation, and a curated `__builtins__`
  that a classic attribute-chain gadget defeats. I POSTed (no auth header) to
  `/api/workflow-sdk/execute`:
  `[c for c in ().__class__.__base__.__subclasses__() if c.__name__=='catch_warnings'][0]()._module.__builtins__['open']('.env').read()`
  and got the file back through the `results` channel — DB password, the
  `WORKFLOW_ENCRYPTION_KEY` that decrypts **every tenant's stored credentials**,
  and a live LLM key. Env-scrubbing is theatre while the secret file sits in the
  cwd. `workflow_sdk_sandbox.py:109-130`, `workflow_sdk.py:734-781`.
  *Fix:* real isolation (nsjail/firejail/seccomp, non-repo tmpdir cwd, dropped
  network) + move `.env` out of the app root.
- **S2 · CRITICAL · No authentication or team scoping anywhere.** `src/main.py`
  wires only CORS. `curl /api/agents` → 200 unauthenticated. `list_runs/get_run/
  list_events` take no `team_id` (`agent_run_service.py:215-260`); `run_workflow`
  executes *any* workflow by id and its own docstring says "find its own earlier
  work (or anyone's)". Cross-tenant read/write is the default. "RBAC on the tool
  path" is un-enforced *everywhere*, not just the tool path.
- **S3 · CRITICAL · The Code node is in-process RCE with the FULL un-scrubbed
  environment.** `nodes/data/code.py:110-177` exec's user/agent code in a thread
  in the main process; `io` is injected, so `io.open('/proc/self/environ')` reads
  every *decrypted* secret the live process holds — strictly worse than S1.
  Reachable unauthenticated via `run_workflow` / `run-adhoc`. (Naive `import`/
  `open` *are* blocked — I confirmed — but that is not the boundary; the object
  graph is.)
- **S4 · HIGH · Masking exists only on the analytics HTTP seam.** The wired agent
  builtins `code`/`httpRequest`/`neo4jQuery`/`mongoQuery` + any Postgres workflow
  node read raw, unmasked data (`agent_tool_resolver.py:112-131`). The platform
  leaks at exactly the tool call VISION markets as its differentiator. Masking
  must be a property of the data plane tools *must* traverse, not of individual
  tool implementations.

### Tier 1 — the demo is not yet honest to show

- **P1 · CRITICAL-for-demo [LIVE] · The one seeded workflow FAILS when run.**
  VISION's centrepiece is "runs every Monday, no AI, ~£0.02/year, same result
  forever." I ran `demo-weekly-export` model-free (`POST /api/workflows/{id}/run`):
  `status:"failed"` — `HttpRequest`→404 from example.com, `Load US`→Postgres
  connection failure. The "£0.02/year that runs forever" **has never once
  succeeded.** If anyone clicks the produced workflow's "open in editor → Run"
  on stage, the flywheel story breaks visibly.
- **P2 · CRITICAL-for-demo [LIVE] · No LLM credit → the primary journey is
  dead.** A triggered run 202s then fails in ~1s with the raw Anthropic billing
  error. The entire "AI is the author" thesis is un-demoable live. (Fund the key
  and `/agents` becomes the live loop with zero code change — verified path.)
- **P3 · HIGH · The only agent "success" is a seeded fixture.**
  `scripts/seed_demo_agent_run.py` runs real sandbox/persist/recorder code, but
  the `thinking`/`response` narration is canned string literals and **no model
  ever ran**. Of 6 "success" runs in the DB, 2 are byte-identical re-seeds and 4
  are Aug-17 "compute 17*23"/"reply DONE" proof harnesses on other models. There
  is zero record of a model authoring a workflow through the real loop.
- **P4 · HIGH [LIVE] · The fleet and runs screens are a graveyard.**
  `GET /api/agents`: **20 agents, 19 junk** (ten `Proof Calculator …`, six
  `uitest-agentsdk-*`, `probe-sdk-agent`, two `runtime-proof`). `/api/agent-runs`:
  **~22 failed, 2 stuck `queued` since Aug 17, 6 success.** Both render unfiltered
  in the UI (`screenshots/eval/A1-agents-list.png`). A VP scrolling either list
  sees mostly red. Tidying was approved in principle, never executed.

### Tier 2 — governance decoration (settable lies)

- **G1 · HIGH · `requires_approval` is a live, settable no-op.** `PUT
  /api/agents/{id}/tools` accepts and stores it; the runtime never checks it; the
  only consumer is a serializer (`agent_run_service.py:459`). The `agent_approvals`
  table even models the prototype's exact `once|run|always`+deny scopes. Present
  and inert is worse than absent — it reads as a safety control and isn't one.
- **G2 · HIGH · The `promoted` tool source resolves to modules that don't
  exist** (`agent_tool_resolver.py:47-49` → files that aren't there). A promoted
  binding is silently skipped. The entire promote/flywheel — VISION's compounding
  moat — is schema-only at every layer.
- **G3 · MED [LIVE] · Role-derivation drift, visible now.** The seeded "Demo
  Workflow Author" shows `role:"asks"` while bound to `build_workflow`; an
  identically-bound test agent shows `builds`. Role is stored at write, never
  derived at read, despite a code comment warning it would drift. It did.
- **G4 · MED · `waiting` is a ghost status** — declared, counted active, polled
  for by the UI, never set by any code path. It is load-bearing for asks /
  checkpoints / approvals — three of the twenty prototypes.

### Tier 3 — durability

- **D1 · HIGH [LIVE] · Orphaned agent runs are never reaped.** `execution_registry`
  is an in-process dict; the `StaleReaper` only sweeps *workflow* `ExecutionModel`
  rows in `running` after 4h (`stale_reaper.py:51-56`) — nothing covers agent
  runs, and there is no startup recovery. A crash mid-run leaves the run
  `running`/`queued` forever **and its session `409 busy` forever.** The 2 zombie
  `queued` runs from Aug 17 (0 events, 2 days old) are the proof.
- **D2 · MED · Workflow executions hang up to 4h+ after a hard crash** — no
  heartbeat, no startup reconciliation, only the 4h threshold.

### Tier 4 — surface holes that break a design promise

- **H1 · HIGH · Runs LIST has no "produced" field** (verified: list items carry
  no `workflow_id`/artifact). The home design's "Produced" column can't be
  rendered from the list; the auditor must open each run.
- **H2 · MED · 500-event cliff:** the studio derives "produced workflow" by
  parsing events client-side with a single `limit=500` fetch, no paging
  (`api.ts:578`). A long run that persists past seq 500 shows success with no
  artifact.
- **H3 · HIGH · No `GET /models`** (`/api/models`, `/api/agents/models` → 404).
  The create-agent model picker has no backing; model id is a hardcoded,
  unvalidated free string.
- **H4 · MED · No SSE for agent runs** — poll-only; SSE exists for workflow
  executions only. Renders identically, so this is polish, not a broken promise.
- **H5 · MED · The "open tool surface" differentiator is unexercised.** Connector
  CRUD/discover/test endpoints are fully built but `GET /api/connectors` → `[]`,
  no studio UI, no agent bound to a connector. VISION calls this *the* thing that
  beats workflows-alone; today it is zero.
- **H6 · MED · The front door opens on the old editor.** `landing.tsx` CTAs →
  `/editor` and `/projects` (the DAG editor VISION says is "not the front door").
  The agent surface is reachable only via the nav rail.
- **H7 · MED · The "Ask" job — VISION's lead — has no UI.** `/agents` is
  build-only; the create form hardcodes the `build_workflow` toolkit
  (`AgentsPage.tsx:230`). The most business-legible demo ("break down net revenue
  by segment") can't be shown.
- **H8 · LOW · The tool catalogue omits the flagship tool.**
  `GET /api/agents/tools/available` returns 14 builtins; `build_workflow` — the
  tool the studio binds to every agent — is undiscoverable through discovery.

---

## Pass-4 verdict — the four questions, answered plainly

**1. What can a user do today they couldn't with the workflow editor alone?**
Describe a workflow in the node-DSL and get it built + graphed + saved
deterministically via `/build`, with no model. That's the whole delta that
actually works today. The agent-authored path (describe in English → agent
builds) is real in code but dead without credit.

**2. The ONE missing thing that most blocks real usefulness?** It splits by
audience, and honesty requires saying both:
- *For a real product:* **a genuine security boundary + auth/tenancy** (S1–S4).
  Until the sandbox is isolated and calls are team-scoped, this can only run on a
  single trusted box — it is not the governed multi-tenant platform VISION sells.
- *For a credible demo:* **LLM credit + one workflow that actually runs** (P1,
  P2). With those two, the live loop and the compile story both become showable.

**3. Is the demo credible to a business audience?** Not yet. A skeptical VP
clicks Run and gets a billing error (P2) or opens the produced workflow and it
fails (P1), then scrolls a list of 19 junk agents and 22 red runs (P4). The
first thing they'd poke — "show me it running unattended, like the slide says" —
is exactly VISION's own kill-criterion, and it's impossible today (no scheduler,
no working artifact).

**4. Now / Next / Later.**

**NOW — make the demo honest and safe to show (days):**
- Fund the API key → `/agents` is the live loop, zero code change.
- Reseed one demo workflow whose nodes *succeed* (a reachable endpoint + valid
  Postgres creds), and run it once to prove "no model, same result."
- Purge the 19 junk agents + failed/zombie runs (user approved in principle —
  confirm the specific delete list first).
- Fix G3 role drift (derive at read, or reseed the demo agent).
- *Payoff:* the demo stops contradicting its own slides on stage.

**NEXT — make it a product, not a demo (the real work):**
- Real sandbox isolation for BOTH the SDK subprocess and the Code node; move
  `.env` out of the app root (S1, S3).
- Auth + `team_id` threaded through every agent/run/workflow read and the two
  workflow tools (S2).
- Wire approvals: `agent_approvals` + enforce `requires_approval` + the checkpoint
  UI — flip G1 from a lie to a gate. This is what makes writes *safe to allow*.
- Route data-touching tools through the governed masking seam, or refuse raw
  `code`/`neo4jQuery`/direct-DB builtins to agents (S4).
- Agent-run reaper + startup recovery (D1).
- *Payoff:* the governance thesis becomes true instead of aspirational; writes
  become safe; multi-tenant.

**LATER — the flywheel + fidelity:**
- Promote (wire the resolver to real modules) + schedule (`derive_workflow` +
  form) + the Ask surface (H7) — the compounding VISION sells.
- Produced field on the runs list + column (H1), 500-event paging (H2),
  `GET /models` (H3), connector UI (H5), SSE if wanted (H4).
- Design-fidelity: dark-cockpit token parity on `/build` + `/agents` (they read
  as a bolt-on today), status-as-shape not colour-only, `/build` reference+cost
  panel, inline per-line errors — see the reconciliation section below.

---

## Design-vs-built (Pass 3, condensed)

Only `/build` and `/agents` are built; the other ~14 agent screens are unbacked.
The deliberate simplification (plain tables, no cockpit language) was the **right
call for shipping the loop** — INSTRUMENT says a restyle is not a redesign, so
paint can come last without rework. But two things are debt, not deferral:
1. **Capabilities, not paint:** checkpoint/approval and Ask are *unbacked
   capabilities* (G1, G4, H7) — they are what block agents from safely touching
   real systems, and they outrank every cosmetic item.
2. **Coherence:** `/build` and `/agents` are light-themed while the rest of the
   studio adopted the dark cockpit; status is encoded as colour-only text (the
   auditor's most-scanned column), violating the instrument's own shape rules.

Restore order (payoff): dark-token parity → home stat band + waiting-on-you dock
→ status-as-shape → drop border-soup → `/build` reference+cost panel → inline
per-line errors → Sessions/Artifacts tabs + Produced column (needs H1 closed).

---

## Evidence index

- Journey screenshots: `screenshots/eval/A1..A5` (agents/run-detail),
  `B1..B3` (build happy path), `C1..C2` (garbage input / home).
- Live probes reproduced by hand this session: sandbox `.env` exfil (S1),
  demo-weekly-export failing run (P1), credit-error run (P2), litter counts (P4),
  role drift (G3), zombie queued runs + reaper scope (D1), `/build` deterministic
  build (works).
- Full backend surface table, security proof, and design reconciliation:
  captured in this session's evaluator outputs; key file:line cites inlined above.
