# Studio Workbench: mockup and build brief

This folder is the design reference for **Studio Workbench**, the agent workspace in Command Studio. It holds:

- `mockup.html`: a single, self-contained, clickable mockup. Open it in a browser. Click a suggestion in the command bar to start, and press `?` to reset.
- `STUDIO-WORKBENCH-GUIDE.md` (this file): how it works and how the UI should look.

The mockup is the spec for **look, layout and behaviour**. Everything in it is fake: the timers, the data and the "Harbor" bank app. Rebuild it in `apps/workflow-studio`, following `apps/workflow-studio/DESIGN-STANDARD.md`. Do not copy the mockup's code.

---

## 1. The idea

Bank ops and QA staff give Studio a goal in plain English, for example "Find wires stuck over a day and check them in Harbor". An agent then does the work in real tools:

- a live cloud browser;
- named DB connections;
- the team's workflows;
- files.

The user watches, steers and approves.

**There is no chat thread.** The feel is an IDE crossed with Devin, plus Antigravity's Agent Manager. The work is the surface, not the conversation.

Five ideas define it, and each must survive the real build:

| # | Idea | What the user sees |
|---|---|---|
| 1 | **Command bar instead of chat** | One ⌘K bar at the top: "What should Studio do?". A goal creates a task, and its work opens as editor tabs (SQL, browser, results grid, workflow run, report). Agent remarks are short toasts, never message bubbles. |
| 2 | **The plan is the script** | The right-hand **Steps** panel is a numbered plan that ticks off live: pending → running → done ✓ → needs you ⚠. Each step shows its tool icon. You edit a step inline, and "Rerun from here" appears. The finished plan *is* the saved, replayable script ("Saved as stuck-wires-check · runs with no AI"), so there is no separate "save as script" step. |
| 3 | **Timeline rewind** | A strip at the bottom with one tick per action, coloured by tool. Click or drag a tick to see the tabs exactly as they were at that point. A badge reads "Viewing 9:41:07 · step 3", with **Back to live** and **Fork from here**. |
| 4 | **Comment on anything** | Click a grid row, a browser element or a step to leave a margin comment, in the style of Google Docs. The agent answers in that thread and adds steps if needed. Comments are how you steer. |
| 5 | **Writes reviewed like a code diff** | Any change to data or workflows opens a **Change review** tab, shaped like a PR: red/green lines (`wires.W-88202 status: in_review → approved`), the affected workflow runs, the environment ("Writes · UAT"), and the reason. Buttons: **Approve & run**, **Request Maria's OK**, **Discard**. Reads never ask. |

Two features are kept from Devin and Antigravity:

- **Live browser with Take over / Hand back.**
  - Normally the agent drives, and its cursor is visible.
  - **Take over** pauses the agent, puts a primary-colour ring on the frame, shows "You're in control · agent paused", and forwards your clicks and typing.
  - **Hand back** resumes the agent.
  - This covers sign-ins and MFA.
- **Editor | Manager views.**
  - Manager is a grid of task cards.
  - Each card shows a live thumbnail, the current step, its status (Running / Needs you / Done / Scheduled), and an environment chip (UAT, or a red PRODUCTION).

## 2. Layout

On desktop, at about 1320 px and wider:

```
┌ Studio  [Editor|Manager]      [ ✦ What should Studio do?   ⌘K ]        UAT  DR ┐
├──┬──────────────┬─────────────────────────────────────────┬───────────────────┤
│▤ │ Tasks (4)    │ stuck-wires.sql │ Harbor · W-88207 │ ... │ Stuck wires check │
│▭ │ Connections  │                                         │ Agent · running   │
│⌕ │ Workflows (6)│         active tab content              │ Saved as … [8:00] │
│⏰│ Scripts (42) │                                         │ 1 ✓ Query …       │
│  │ Files        │                                         │ 2 ✓ Open Harbor … │
│  │              ├─────────────────────────────────────────┴───────────────────┤
│  │              │ Timeline ■■■■■■■■■■●              10 actions · legend       │
│  │              │ Log | Problems   (timestamped tool calls, monospace)        │
└──┴──────────────┴─────────────────────────────────────────────────────────────┘
```

- **Activity rail** (icons only): Tasks, Explorer, Search, Scheduled.
- **Explorer:** a tree of tasks (with status dots), connections (`payments-uat · read-only`, `harbor-uat · browser profile`), workflows, scripts (show 4, then "39 more"), and files.
- **Editor tabs:** these can be closed. Tab kinds: 🗄 query, 🌐 browser, 📊 results grid, ⚙ workflow run, ✎ change review, 📄 report (markdown walkthrough whose steps link back to the timeline).
- **Steps panel:** the task title, the agent state with a pulse, Pause / Stop, the "Saved as …" status line with schedule chips, then the steps.
- **On a phone (390 px):**
  - the command bar sits on top, with one tab at a time and a tab switcher;
  - Steps opens as a bottom sheet, and the timeline is a thin strip;
  - Manager becomes a stacked list;
  - there must be no horizontal scroll.
- **Light and dark (Graphite)** follow the Studio glass standard.

## 3. The reference flow (what the mockup demonstrates)

1. **Open:** Manager shows 4 tasks:
   - Stuck wires check (Running 3/5);
   - Vendor statements (Needs you, PRODUCTION);
   - Nightly tests (Done · 13/14 · 1 likely bug);
   - Morning wire report (Scheduled 8:00).
2. **Run:** in the command bar, choose "Find wires stuck over a day and check them in Harbor". The view switches to Editor and 5 steps appear. The run then goes through:
   - the SQL tab, which returns 3 wires;
   - the Harbor browser checking each wire;
   - the Results grid, with the mismatch on W-88202 highlighted ($12,800, Approved in Harbor, `in_review` in the DB);
   - `report.md`.

   The timeline fills up to 10 ticks.
3. **Rewind:** click a tick to see the past state, then Back to live. Clicking a step jumps the timeline too. Fork from here creates a new task.
4. **Edit a step:** double-click step 2 and change "over a day" to "over 2 days". **Rerun from here** reruns it, and the script becomes v2.
5. **Comment:** comment on the W-88202 row: "why does Harbor say approved?".
   - The agent replies and adds step 6, "Check W-88202 audit log in Harbor", which finds "approved by J. Patel 8:12; DB update failed (timeout)".
   - It then adds step 7, "Fix W-88202 status", marked needs-you, which opens Change review.
6. **Change review:** Request Maria's OK leads to "Waiting on Maria". A subtle demo link approves as Maria, then shows "Approved 9:42 · applied" and the escalation run tab.
7. **Schedule:** "Every morning 8:00" moves the script under Scheduled.
8. **Needs you:** open Vendor statements. The vendor portal shows its MFA page.
   - Take over: the user cursor types the code.
   - Hand back: the agent downloads 3 October statement PDFs, and Files updates.

Keep these numbers consistent everywhere: 4 tasks, 6 workflows, 42 scripts (43 after the save), Nightly tests 13/14 with 1 likely bug, and 3 wires with 1 mismatch.

## 4. Known gaps in the mockup (don't copy these)

- The seeded "Stuck wires check" card says step 3/5, but opening it replays from step 1. A real task should resume where it is.
- Only the W-88202 row gets a real reply to a comment. Everything else gets "Noted".
- The sign-in code is typed by an animation. In the real build, the user types it.
- The running card's action count shows 0.
- The "Check a wire over $10k really goes to approval" suggestion runs but is only lightly worked out.
