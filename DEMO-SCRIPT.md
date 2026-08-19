# Demo script — the agent platform (1 page)

**State it assumes:** the NOW fixes from `EVALUATION-AGENT-PLATFORM.md` are done
(junk purged, one demo workflow that actually succeeds, role drift fixed). If the
API key is **funded**, run Track A. If not, run Track B and *say so* — do not
pretend. Never click "Run" on a workflow you haven't confirmed succeeds today.

Services: `docker start analytics-pg workflow-engine-postgres-1`; engine :8000,
analytics :8001, studio :5174. Demo the studio at `http://localhost:5174`.

---

## The story (2 minutes): "AI is the author, not the runtime"

> "Building a workflow by hand means you already know the steps. The discovery —
> what tables exist, what the column is really called — is half the work. An
> agent does that discovery *as it builds*. And when it's done, the thing it
> produces runs forever with **no model in the loop** — pennies a year, same
> result every time, auditable like any scheduled job."

---

## Track A — API key funded (the real loop)

1. **`/agents` → pick the demo agent → type a task:** *"Build a workflow that
   fetches the weekly export and loads it into Postgres per region."* Click Run.
2. **Watch the run detail stream** (right panel): `thinking` → `tool_call` (the
   agent writes the SDK script) → `tool_result` (the workflow persists) →
   `response`. Point at the **produced-workflow graph** and **"open in editor →"**.
   *"That's the evidence trail — every figure carries its denominator, every
   step is inspectable."*
3. **Open in editor → Run the produced workflow.** It succeeds. *"No model was
   involved in that run. That's the compile: the expensive, unpredictable thing
   ran once; the cheap, predictable thing runs forever."*
4. **The auditor question:** back to the runs table, open last week's run.
   *"What did it do, can I trust it?"* — show the same trail on a past run.

## Track B — no credit (honest fallback)

1. Open the **seeded demo run** on `/agents`. Walk the same evidence trail
   (thinking → tool_call → tool_result → response → produced graph). Say plainly:
   *"This run was seeded — the machinery is real, the narration is scripted,
   because the live model isn't funded in this environment."*
2. **Open the produced workflow in the editor and Run it** (must be the reseeded,
   working one). *"This is the payoff and it's real: the agent's output runs
   deterministically, no model, same result."*
3. **`/build`:** paste a plain node-DSL script, Run. Show the graph build with
   no model, the per-line provenance, Save → editor. *"This is the author path
   without the LLM — fully deterministic today."*
4. Show a **failed run's** honest error panel. *"When it breaks, it says why —
   we draw the refusal, we don't design past it."*

---

## What a skeptical VP will poke — and the honest answer

| They ask | Say |
|---|---|
| "Show it running unattended, like the slide." | *"Scheduling is next — the cron substrate exists, the agent→schedule bridge doesn't yet."* Don't fake it. |
| "Is my data masked at the tool call?" | *"On the analytics tool path, yes. Making that the only path — so raw DB/code tools can't bypass it — is the top of our Next list."* (Do not claim it's enforced everywhere; it isn't.) |
| "Multi-tenant? Can team B see team A?" | *"Not yet — auth and team scoping are the gating item before any shared deployment. Today it runs single-tenant on a trusted host."* |
| "What happens if it crashes mid-run?" | *"Run recovery is on the durability list — today an orphaned run needs a manual cancel."* Be honest; it's a known hole. |

---

## Do NOT, on stage

- Do not trigger a live run if the key is unfunded (raw billing error appears).
- Do not run any workflow you haven't confirmed succeeds *that morning*.
- Do not scroll the raw agents/runs lists if the junk purge hasn't happened.
- Do not claim approvals, promote, schedule, or cross-tenant governance work —
  they are schema-only or inert today (`requires_approval` is a stored no-op).
