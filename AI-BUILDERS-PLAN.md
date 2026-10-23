# Plan: simpler AI app builder and AI workflow builder

2026-10-22. This is guidance for the agent doing the work. It builds on `docs/history/SIMPLIFICATION-PLAN.md` sections 4–6 and replaces them where they disagree. Do the cuts in `CUT-GUIDE.md` first.

> **These guides were written on a different branch.** Your code may differ: file names, line numbers, sizes and test
> counts are pointers, not exact targets. Find the same thing in your copy by name and adapt. The prompts and the ideas
> stay the same.

## The idea in one paragraph

Both builders today are pipelines of roles: plan, clarify, spec, generate, repair, check, fix, and each hand-off is a place to break. Current models are strong enough to do the whole job in **one call**, provided they get the full context up front and their output is checked by running it. So each builder becomes:

> **context in → one model call → deterministic clean-up → run it → at most 2 fix calls → show it**

**The two builders stay completely separate.** Apps and workflows are different products, with different data and different environments. Each builder gets its own module, prompts, clean-up, checks, chat UI, store and evals, and neither imports from the other. They follow the same *recipe*, but they share no code: a change to one can never break the other.

## What the model gets, and what it writes

| | App builder | Workflow builder |
|---|---|---|
| **Context** | the request; the schemas of the tables and views the app may use; the kit's full export list with props and a usage line each; the current files when editing | the request; the node catalog (name, inputs, outputs, one example each); the table schemas; the current workflow JSON when editing |
| **Output** | 1–3 files, path-annotated fences (` ```tsx file="App.tsx" `), full contents, `App.tsx` default-exports | one workflow JSON object, validated against a schema (structured output) |
| **Checked by** | bundling with esbuild and rendering in the iframe | the existing workflow checker, plus a dry run where the nodes allow it |
| **Shown in** | the live preview | the canvas, as a proposal the user accepts (the existing proposal, history and undo stay) |

**Lean on the model, not on our machinery:**
- **No plan step and no question round.** If the request is vague, the model picks sensible defaults and states them in one line at the top of its reply. The user corrects it in the next message.
- **No retrieval, grounding or "understanding" step.** The whole kit list, node catalog and table schemas fit in the context. Send all of it every time.
- **Reasoning happens inside the same call** (extended thinking, or a short thinking block). There's no separate planner call.
- **No hand-written repair heuristics for things the model gets right on its own.** Keep only the cheap deterministic fixes in the next list.

**Keep deterministic clean-up, because it's cheap and v0 shows it's worth a lot** (64.7% → 93.9% error-free builds; see `docs/cleanup/v0-research.md`). Before bundling:
- add missing kit imports by name;
- map unknown icon names to the nearest allowed one;
- replace imports from outside the kit and React with a clear stub;
- make sure `App.tsx` has a default export;
- strip stray markdown.

For workflows: fill node defaults, fix edge references by node name, and drop unknown fields.

## How apps get data (this is what makes the app builder simple)

This follows the product direction in the mockups (https://claude.ai/artifact/FYZnd9vnojrtLdzBS3MhNT): **Data** is the foundation, and **Apps** and **Automations** sit on top of it as separate things.

- **The app builder no longer generates a backend per app.** Generated pages read and write tables and views through two kit hooks, `useTable('invoices')` and `useView('open_ap_by_vendor')`, which call the existing data-table API.
- **If the tables an app needs don't exist yet,** the same call also returns a small `tables` JSON block: names, columns and types, a few sample rows. Code creates them through the data-table API before the page renders. This is not a second model call.
- **Simple rules stay in the page.** For example, "over $5k needs approval" shows an Approve button and sets a status. The app builder never calls the workflow builder. If someone wants a rule that runs on its own (emails, schedules), they build it separately as an automation.
- **The backend builder stays,** behind **Advanced**, for the rare app that needs custom server code. It's no longer part of the default build.

## Each builder: one chat, one store, one screen

The rules below apply to each builder separately. They don't share components or stores.

- **One chat per builder.** It shows the messages as they actually happened, a streaming "writing App.tsx…" state, the result, and on failure the error text with a **Fix** button.
- **One store per app or per workflow,** holding only messages and build status. It's persisted as-is, so nothing is rebuilt from saved prompts and nothing leaks between apps or between workflows.
- **No hidden auto-senders.** Every model call comes from a message the user sent, or from the at-most-2 automatic fixes, which the chat shows.
- **App builder:** its own chat on the left, the live preview on the right. One screen, from the first message through every later edit.
- **Workflow builder:** its own AI panel beside the canvas, slimmed down to a plain chat.

## Edits

- Send the current files (or the current workflow JSON) in full, then the request.
- The model returns only the files it changed, in full; it never writes "rest unchanged". For workflows it returns the full JSON.
- Everything it didn't touch is kept exactly as it was.

## The engine side: two small modules, same recipe

Write two independent modules, for example `services/app_ai_build.py` and `services/workflow_ai_build.py`. Neither imports the other. Each is roughly:

```
build(request, context, current=None) -> Result
  output  = call_model(prompt(request, context, current), stream=True)   # one call
  draft   = cleanup(parse(output))                                       # deterministic
  errors  = check(draft)       # app: bundle + render · workflow: workflow checker
  for _ in range(2):                                                     # at most 2 fixes
      if not errors: break
      draft  = cleanup(parse(call_model(fix_prompt(draft, errors))))
      errors = check(draft)
  return Result(draft, errors)                                           # errors shown with a Fix button
```

The loop is about 15 lines, so copy it into each module rather than sharing it. Each builder keeps the freedom to change its own flow later.

**Start the app builder from what already works.** The one-shot path in `oneshot_builder.py` wrote a whole small app in 2.5 minutes with one call, against about 50 calls and 27 minutes for the role-split pipeline. Lift its prompt and parsing into `app_ai_build.py` and leave the rest behind.

**Start the workflow builder from the prompt and node-catalog parts of `ai_builder.py`,** plus `workflow_checker`.

## What this retires

Switch these off first. Delete them only after the new path passes the evals below. Before deleting anything, map importers, since some names may have moved since this was written.

**App builder, engine:**
- the agent loop (`_run_agent_loop` in `app_builder_ai_service.py`) and `app_scaffold.py`;
- the plan and clarify step: `project_plan.py`, `plan_clarify`, `plan_checklist`, `plan_compact`;
- `build_coordinator.py`, the multi-runner checks in `builder_gates.py`, and `app_autotest`;
- per-app module generation as part of the default build (`module_generator` stays for Advanced).

**App builder, studio:**
- BuildStudio as a second edit screen;
- the plan page and plan flow (`features/plan`), `planFlowStore`, and the restore-chat logic.

**Workflow builder, engine:** `ai_spec`, `ai_repairs`, `ai_grounding`, the clarify and rounds logic in `ai_builder.py`, the Understand step, and `fuzz` and `drift`. Keep `workflow_checker`; it becomes the check.

**Workflow builder, studio:** the spec, questions, rounds and understanding UI in `features/ai-builder`. Keep the proposal accept/undo flow.

**Rough size:** the AI parts of both builders are about 40k lines today (from `CODEBASE-MAP.md`):
- engine about 20k: app builder core 10.0k, plan 2.6k, AI workflow builder 6.9k;
- studio about 20k: app-builder chat, preview and sandbox 9.1k, plan 4.5k, ai-builder 7.8k.

The target is about 8–10k for the two together. That includes the preview and sandbox, which stay, plus each builder's own module and chat. Treat this as an estimate; measure as you go.

## How to tell it works: evals before deleting anything

- **20 app prompts and 20 workflow prompts, bank-style.** Examples: "track vendor invoices, over $5k needs approval", "branch cash count sheet, flag differences over $50", "every Monday email managers their open KYC reviews". Keep them in a file in the repo.
- **For each prompt, record:**
  - did the first build pass its check;
  - how many fix calls it needed;
  - total time;
  - and, by hand for a sample, does the result do what was asked.
- **Run the old path and the new path on the same prompts.** Switch the default only when the new path is at least as good on first-build success and faster.
- **Keep the set and rerun it after every prompt or clean-up change.** A change that lowers first-build success gets reverted.

## Order

**Owner's order (2026-10-23): cut and prune (`CUT-GUIDE.md`) → app builder (`ONESHOT-BUILDER-PORTING-GUIDE.md`) →
workflow builder (`handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md`).** The workflow track below is already built on
the owner's machine as `workflow_oneshot.py` and ships as patches in that folder; follow its guide, not the steps below.
The app-builder steps below are replaced by the porting guide. The two tracks touch almost no common files.

**Workflow builder track**
1. Build `workflow_ai_build.py` beside the old path, behind a flag, and run the workflow evals. It's the lower-risk track: the editor works and the checker exists.
2. Slim its AI panel down to a plain chat with its own store.
3. Switch the default once the evals pass, then delete the retired workflow-builder code.

**App builder track**
1. Add the kit hooks `useTable` and `useView` over the existing data-table API, with a small hand-written test app that uses them.
2. Build `app_ai_build.py` beside the old path, writing pages against those hooks plus the `tables` block. Put it behind a flag and run the app evals.
3. Give it one chat and one store, and remove BuildStudio as a second screen.
4. Switch the default once the evals pass, then delete the retired app-builder code, one change per area.

**Gates after each step:** the same as `docs/history/SIMPLIFICATION-PLAN.md` section 8 (tsc 0 errors, vitest, lint ≤ 93, design check, build, engine unit tests, and the browser journeys). Add one rule: **the eval numbers don't go down.**

## Out of scope

- Security fixes (deferred by the owner).
- The workflow editor, runner and clone: don't change them, apart from the AI panel.
- The visual editor (dropped).
- New features beyond what's listed here. Add them later, one at a time.
