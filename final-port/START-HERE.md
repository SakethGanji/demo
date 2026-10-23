# Final port: start here

This folder is the last handoff. Everything to bring over is here, split into 5 sessions. Run them in order, one session
each, and finish each one green before starting the next.

> **These guides were written on a different branch.** Your code may differ: file names, line numbers, sizes and test
> counts are pointers, not exact targets. Find the same thing in your copy by name and adapt. The prompts and the ideas
> stay the same.

**To begin:** copy this folder into the backend repo root and paste the prompt from `STARTER-PROMPT.md` into an agent.
The backend and the UI are separate repos. `apps/workflow-engine/<x>` in these guides means `<x>` in the backend repo,
and `apps/workflow-studio/<x>` means `<x>` in the UI repo. Keep the two checkouts side by side (the engine reads
`../workflow-studio/node_modules` for its Tailwind and `tsc` checks). Don't connect their deployments.

## The goals

1. **Simplify the process.**
   - Each builder becomes **one model call**, then the result is checked by running it, with at most 2 fix calls.
   - The pipelines of roles go: plan, clarify, spec, generate, repair, check, fix.
   - There is no plan step and no question round.
2. **Simplify the code.**
   - Cut the features we dropped and the dead code (about 202k lines down to about 175k before the builders are replaced).
   - Then delete the old staged builders once the one-shot paths replace them.
3. **Better performance.** One call, with full context up front:
   - is **faster**: minutes, not tens of minutes;
   - is **more reliable**, because there are fewer hand-offs to break;
   - is **cheaper**: a handful of calls instead of about 50.

   The prototypes proved it:
   - a small app was written in one call in about 2.5 minutes, where the old pipeline took about 50 calls and 27 minutes;
   - the workflow builder built 35 of 35 hard bank workflows on the first call.

**The rule for every session: simple and fast.** When something fails, fix the prompt or the platform. Don't add a stage,
a planner or a question round.

## The 5 sessions

| # | Folder | Guide | What it does |
|---|---|---|---|
| 1 | `1-cut/` | `CUT-GUIDE.md` | **Cut and prune.** Remove the dropped features (knowledge, the hidden second app builder, interface builder, releases UI, API tester, legacy pipeline) and dead code. `evidence/` has the research behind each cut. |
| 2 | `2-app-builder/` | `ONESHOT-BUILDER-PORTING-GUIDE.md` | **One-shot app builder.** Port the prototype in `oneshot-builder/` (prompts, kit, checker, fixture) into the engine and studio, then retire the old app builder. |
| 3 | `3-workflow-builder/` | `WORKFLOW-BUILDER-GUIDE.md` | **One-shot workflow builder.** Apply the 3 patches (engine fixes, the one-call AI workflow chat, the read view), then retire the old staged chat. `oneshot_prompt.md` is the exact prompt; `workflow-oneshot-kit/` has the evals. |
| 4 | `4-workflow-hardening/` | `WORKFLOW-HARDENING-GUIDE.md` | **Harden the workflow builder.** Fix the issues the live bank runs found: unwired approval outcomes, missing deadlines, no API auth, item-0-only DataTable writes, weak test assertions. |
| 5 | `5-studio-workbench/` | `STUDIO-WORKBENCH-GUIDE.md` | **Studio Workbench UI.** The new IDE-style agent workspace: a command bar instead of chat, a live steps plan that is the saved script, a timeline you can rewind, comments to steer, changes reviewed like a diff, and Editor/Manager views. Open `mockup.html` beside it. |

Session 5 is independent of 1–4, so it can run in parallel if you want.

## Background

- `AI-BUILDERS-PLAN.md` explains why both builders become "one call, then check it by running". Sessions 2 and 3 build
  on it.
- `CODEBASE-MAP.md` maps where the code lives, with sizes per area.

Older plans and handoffs in your repo are superseded by these guides. Don't follow them.
