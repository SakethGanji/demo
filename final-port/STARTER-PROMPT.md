# Starter prompt

Copy the `final-port/` folder into the root of the **backend repo**. Then open an agent session in the backend repo and paste
the prompt below. Fill in the path to the UI repo first.

---

```text
You are finishing a port across two repos:

- BACKEND repo (you are in it): the workflow engine (FastAPI, Python). Its root is what the guides call apps/workflow-engine/.
- UI repo at <PATH-TO-UI-REPO>: the workflow studio (React, Vite). Its root is what the guides call apps/workflow-studio/.

They are separate repos and deploy separately. Do not merge them, add one as a submodule of the other, or make either
import files from the other. They talk only over the HTTP API, as they do today.

Everything you need is in final-port/ at the backend root. Do this:

1. Read final-port/START-HERE.md fully. It states the goals (simpler process, simpler code, better performance through
   one-shot builders) and lists 5 sessions. Then read final-port/AI-BUILDERS-PLAN.md and final-port/CODEBASE-MAP.md.
2. Work ONE session at a time, in order (1-cut, 2-app-builder, 3-workflow-builder, 4-workflow-hardening,
   5-studio-workbench). Read that session's guide end to end before changing code.
3. The guides were written in a monorepo on a different branch. Paths, line numbers, sizes and test counts are pointers.
   Find the same thing in your copy by name and adapt. The prompts and the ideas stay the same.
   Path mapping:
     apps/workflow-engine/<x>  ->  <x> in the backend repo
     apps/workflow-studio/<x>  ->  <x> in the UI repo
4. The patches in final-port/3-workflow-builder/ use monorepo paths. Strip the prefix with -p3:
     patches 1 and 2 are backend-only:  git apply -p3 final-port/3-workflow-builder/1-engine-fixes.patch  (then 2-ai-chat.patch)
     patch 3 is UI-only:                 cd <PATH-TO-UI-REPO> && git apply -p3 <backend>/final-port/3-workflow-builder/3-read-view.patch
   Run `git apply --check -p3` first (check patch 2 only after patch 1 is applied). Patch 3 also needs
   `npm install @dagrejs/dagre@^1.1.4` in the UI repo. If a hunk does not apply to your code, apply the rest and make that
   change by hand, following the intent in the guide.
5. Some backend tooling expects the UI repo next to it (for example the app bundler looks for the tailwind CLI at
   ../workflow-studio/node_modules). Keep the two checkouts side by side with the UI folder named workflow-studio, or
   point that path at the UI repo through config. Don't copy UI files into the backend.
6. The rule for every session: simple and fast. When something fails, fix the prompt or the platform. Do not add a
   stage, a planner or a question round.
7. Finish each session green before the next: backend tests, UI typecheck/lint/tests, and the checks the guide lists.
   Never weaken or skip a test to get green. Commit each session separately in each repo it touched.
8. At the end of each session, tell me what changed in each repo, what you could not do, and anything in the guide that
   turned out to be wrong for this code.

Start with session 1 now.
```

---

Notes:

- Session 5 (the Studio Workbench UI) does not depend on 1–4. It can run in its own session in the UI repo at the same
  time. Point that session at `final-port/5-studio-workbench/` and `mockup.html`.
- One session per agent run keeps the context small. Start a fresh run for the next session with the same prompt and
  change the last line to "Start with session N now."
