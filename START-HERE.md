# Start here

This page lists the documents that are current. Anything not listed here is history.

## The work, in order

| # | Guide | What it does |
|---|---|---|
| 1 | [`CUT-GUIDE.md`](CUT-GUIDE.md) | **Cut and prune.** It removes the features we dropped and the dead code. Do this first. |
| 2 | [`ONESHOT-BUILDER-PORTING-GUIDE.md`](ONESHOT-BUILDER-PORTING-GUIDE.md) | **App builder.** It replaces the staged pipeline with one call that writes the app, using the prototype in `experiments/oneshot-builder/`. |
| 3 | [`handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md`](handoff/workflow-builder/WORKFLOW-BUILDER-GUIDE.md) | **Workflow builder.** It applies the 3 patches in that folder (engine fixes, the one-call AI workflow chat, the read view), then retires the old staged chat. |
| 4 | [`handoff/studio-workbench/STUDIO-WORKBENCH-GUIDE.md`](handoff/studio-workbench/STUDIO-WORKBENCH-GUIDE.md) | **Studio Workbench.** This is the new IDE-style agent workspace: a command bar, a live steps panel, a timeline you can rewind, comments, change review, and a live browser you can take over. Open `mockup.html` next to it. |

Each guide is self-contained. Finish one, with its checks green, before starting the next.

## Background (read when a guide points you here)

- [`AI-BUILDERS-PLAN.md`](AI-BUILDERS-PLAN.md): why both builders become "one model call, then check it by running". Guides 2 and 3 build on it.
- [`CODEBASE-MAP.md`](CODEBASE-MAP.md): where the code lives, with line counts per area.

## History (don't follow these)

- `docs/history/`: every older plan, design, contract, handoff and progress log (Stages 1–8, the UI rebuild, the merge guides, and `SIMPLIFICATION-PLAN.md`). They are kept for reference only, and the guides above supersede them. Some code comments still cite these files by their old root path.
- `design/`, `design-mockups/`, `docs/cleanup/`: earlier mockups and cleanup research.
