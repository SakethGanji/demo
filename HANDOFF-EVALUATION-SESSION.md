# Next session: the honest evaluation

**Written 2026-08-19, end of the session that shipped the agent surface.**
The next session BUILDS NOTHING at first. Its job is judgment: evaluate the
backend, the UI designs, and what's actually built — as a product, not a
codebase — and produce a ranked improvement plan. The question to keep asking:
**"is this a really useful product, or a good demo?"** Both answers are
useful; pretending is not.

## Read first (15 minutes, in this order)
1. `HANDOFF-AGENT-UI.md` — what exists, environment, the known not-done list.
2. Memory: `agent-run-service-fixes` (this session), `agent-platform-plan`,
   `terminal-design-direction`.
3. `screenshots/` — `ui-build-final.png`, `ui-agents-build-attempt.png`
   (what's built) vs `agent-designs/*.png` (what was designed).
4. `VISION.md` and `design-prototypes/agents-index.html` (the product thesis
   and the design set's own table of contents + its list of admitted gaps).

## The evaluation, in four passes

### Pass 1 — the experience walkthrough (do this FIRST, before reading code)
Drive the real product in a real browser the way three users would, and write
down every point of friction, confusion, or broken promise:
- **The builder**: "I want the weekly export automated." /agents → create →
  run (fails today: no credit — how does that failure FEEL?) → /build as the
  fallback → save → editor → publish → does it actually fire?
- **The auditor**: "What did this agent do last Tuesday and can I trust it?"
  runs table → run detail → events → build view → produced workflow.
  Where does the evidence trail break?
- **The skeptic**: try to break it. Empty states (zero agents, zero runs,
  first visit), long scripts, huge tasks, double-clicks, stale tabs, a run
  cancelled mid-poll. The design index itself admits failure/empty states
  were never drawn — verify how the built UI behaves there.
Use Playwright (chromium in analytics-service/web) to capture each journey as
screenshots; the seeded demo run (`scripts/seed_demo_agent_run.py`) is the
only "live" agent story until credit exists.

### Pass 2 — backend audit (subagent fan-out works well here)
- **Surface completeness**: for each UI promise and each design-prototype
  claim, is there an endpoint? (Known holes: approvals, promote, schedule,
  SSE, connector discovery, run "produced" links on the LIST endpoint,
  no GET /models.) Rank by how visibly the hole breaks the experience.
- **Correctness/security**: the sandbox boundary (what CAN a hostile script
  reach from the subprocess? env is scrubbed, but the filesystem isn't),
  masking/PII posture on the analytics seams, the Code node's undocumented
  item shape (shell-agent finding #1 — silent no-ops are a correctness bug in
  practice), tool results with no truncation contract on run_workflow chains.
- **Durability**: what happens on engine restart mid-run (execution_registry
  is in-process); the stale reaper story; session/run recovery.

### Pass 3 — design-vs-built reconciliation
The 20 prototypes were digested 2026-08-19 (Tier 0: home/session/run/
checkpoint/ask; SDK trio best-backed). For each Tier-0/1 screen: built /
partially built / unbacked — and crucially, **which un-built screens the
product actually NEEDS to be useful vs which are polish**. The built pages
deliberately simplified the cockpit language (no stat bands, no accent
budget, plain tables) — decide whether that divergence is debt to repay or
the right call; INSTRUMENT.md is the standard if it's debt.

### Pass 4 — the product verdict
Answer plainly, in writing:
1. What can a user do today that they couldn't with the workflow editor
   alone? (The honest answer today: describe→build via /build, and — once
   credit exists — delegate build+run+chain to an agent.)
2. What is the ONE missing thing that most blocks real usefulness? (Prior
   candidates: live LLM credit; approvals so writes are safe to allow;
   connector/tool breadth so agents can touch real systems.)
3. Is the demo story credible to a business audience? What would a skeptical
   VP poke at first?
4. Now / Next / Later roadmap, each item with its user-visible payoff.

## Method note
This shape of work fans out well: parallel evaluator subagents per pass
(journey-walker with Playwright, backend auditor, design reconciler) plus an
adversarial "demo-ware detector" — then synthesize yourself. Precedent:
the 2026-08-09 UI audit (8 domain agents, AUDIT-FINDINGS-2.md) and PARITY.md's
accounting style. Deliverables: `EVALUATION-AGENT-PLATFORM.md` (findings,
ranked, with evidence links/screenshots) + updated `HANDOFF-AGENT-UI.md`
not-done list + a 1-page demo script.

## Prep checklist (before evaluating)
- `docker start analytics-pg workflow-engine-postgres-1` (5432 / 5433 — they
  coexist now), engine API :8000, analytics :8001, studio :5174.
- Decide on **API credit** — with it, Pass 1 evaluates the real live loop and
  the whole evaluation is 2× more meaningful. Without it, evaluate the seeded
  replay honestly.
- Optional cleanup before screenshots: archive the `uitest-*`/`probe-*`/
  "Proof Calculator" agents and old failed runs (user approved tidying "for
  the demo" in principle but it was never executed — confirm before deleting
  run history).

## Known findings already queued (don't rediscover them)
- Code node runtime item shape undocumented → agents silently no-op (shell
  agent probe-debugged it; fix: document in signature_reference or normalize).
- No dry-execution affordance surfaced to agents → 12 probe workflows of
  litter (run-adhoc exists, unadvertised).
- Code-node sandbox errors don't teach (SDK's do — the pattern is proven to
  move the eval number: 7/12 → 10/12).
- `terminal-agent-sdk.html` lines ~312-314 still claim in-process execution;
  the subprocess sandbox landed — update the prototype text.
- Runs LIST endpoint has no "produced artifact" field, so the runs table
  can't show the Produced column the home design wants.
