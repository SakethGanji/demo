# Next session — the plan

**Written 2026-08-19, end of the "make features work" session.**
This session evaluated the agent platform, got the live loop working on a real
model (Gemini), and closed the feature breaks that stopped a user creating and
running an agent. State below, then a ranked plan.

## Where things stand (all committed on `clean-main`, tests green)

**Working now, verified live:**
- The full agent loop runs on `gemini-3.6-flash`: describe → build → discover →
  run → chain, through a real model, in HTTP and the studio UI.
- Create an agent in the UI (model selector, defaults to the available model) →
  it runs. Edit an existing agent's name/model inline → a dead `claude-sonnet-5`
  agent becomes runnable.
- `/build` (script → graph → Save → open in editor) round-trips end to end.
- Runs persist correctly (workflow JSONB, run rows, event stream, referential
  integrity, durable across restarts — validated against Postgres directly).

**Fixes that got us here (this session):**
- SDK tool calling-convention (`build/list/run_workflow` now run through the
  real runtime), Gemini 3 `thought_signature` replay, `GET /api/models`, the
  studio model selector + inline agent edit, false-success guard, seed on gemini.

**Test baseline to protect:** engine `pytest` 146 green; studio `tsc -b` 0;
browser `agent-surface.spec` 6 green, `shell.spec` 15 green.

**Environment:** `docker start analytics-pg workflow-engine-postgres-1`
(5432 / 5433); engine `:8000`, analytics `:8001`, studio `:5174`. The paid
Gemini key is in the gitignored `apps/workflow-engine/.env`; only Gemini has
credit (Claude models appear in the picker but 402; OpenAI has no key).

## The plan, ranked by user-visible payoff

### Now — make the demo credible (small, high impact)
1. ~~**Reseed a demo workflow that actually runs.**~~ **DONE 2026-08-19.**
   `demo-weekly-export` is now Code-node based (synthesize export → branch →
   per-region loader), runs to success model-free; seed narration updated.
2. ~~**Purge the litter.**~~ **DONE 2026-08-19.** Deleted the 19 junk
   `uitest-*`/`probe-*`/`Proof Calculator`/`runtime-proof` agents (deleting an
   agent also cleared its runs); roster is now just `Demo Workflow Author` and
   `gemini-live-eval`, both on `gemini-3.6-flash`, both verified to run live.
   Also added agent delete + model-heal-on-reseed. **Still open:** the runs
   table keeps ~24 historical failed runs + 2 zombie `queued` runs (Aug 17,
   never finalized — the durability gap, no delete-run endpoint). Clearing those
   needs a delete-run/reaper capability (see the agent-run reaper below).

### Next — the features that make it more than a build tool
3. **The "Ask" surface** (VISION's lead job, no UI today). A question-over-data
   agent view — ask in English, see the answer with its working, masked columns
   still masked. This is the most business-legible demo ("break down net revenue
   by segment") and the biggest single feature gap. Needs a non-`build_workflow`
   toolkit (data sample/profile/aggregate tools already exist in the engine).
4. **Approvals / checkpoint UX.** `requires_approval` is inert and the
   `agent_approvals` table has no code. Wire the pause-for-approval path + the
   checkpoint screen (`design-prototypes/terminal-agent-checkpoint.html`) so an
   agent can be allowed to do writes with a human gate. Also lights up the
   `waiting` status, which is declared-but-never-set today.
5. **Schedule (the flywheel).** "Do this every Monday" — turn a produced
   workflow into a scheduled job (the cron substrate exists; the
   session→schedule bridge and a schedule form do not). *Payoff: the compile
   payoff becomes real, not a slide.*

### Later — breadth + fidelity
6. **Connectors UI** (the "open tool surface" differentiator) — endpoints exist,
   zero connectors registered; needs real MCP/OpenAPI data to be worth drawing.
7. **Promote workflow → tool** — the compounding moat; `promoted_tools` is
   schema-only and its resolver points at modules that don't exist.
8. **Full run-trace screen** + **design fidelity** on `/build`/`/agents` (dark
   cockpit tokens, status-as-shape, reference+cost panel) per INSTRUMENT.md.
9. **Minor:** convert the `<a href>` "open in editor" links to router navigation
   (full-page reload today; fine on dev, would 404 on a static host without SPA
   fallback); add agent delete/archive to the UI.

## The one thing to say out loud
Governance was deprioritized for these feature sessions, and that's fine for a
demo — but it is the real blocker for any shared/production deployment. The
sandbox escape (`.env` exfiltration) and the total absence of auth/tenancy are
documented in `EVALUATION-AGENT-PLATFORM.md` (S1–S4). A *paid* key now sits in
the file that hole reads. When features are ready to show to anyone outside a
trusted machine, that work has to come first. Don't let it get lost.
