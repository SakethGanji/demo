# Stage 3B execution plan (AI and live layers)

Source: `HANDOFF-STAGE3.md` §4 "How AI helps", §4 "Three test layers", §5 3B, §6.
Base: Stage 3A committed as `decd9e4` on 2026-10-17.
Baselines: engine 1333 passed / 1 skipped / 2 xfailed; studio unit 178; lint 96; e2e 143.

## Facts found before planning
- `src/engine/llm_provider.py` and `src/engine/fake_llm.py` already exist. The model adapter wraps these, with Gemini as the real model.
- `src/engine/cron_scheduler.py` exists. Scheduled smoke tests use it.
- The 3A toolkit (`services/workflow_toolkit.py`), scorecard, suggestions (`services/test_suggest.py`), node catalog, mock_gen and api_catalog are the base for this stage.

## Order of work
1. Lead reads `llm_provider.py`, `fake_llm.py`, `cron_scheduler.py`, `test_suggest.py`, `test_runner.py` and `release_service.py`, then writes `CONTRACTS-STAGE3B.md`.
2. **Wave 1, in parallel (separate files):**
   - A, the AI layer:
     - a model adapter (`engine/test_ai.py`): Gemini or the fake LLM;
     - `POST .../tests/draft` turns empty grid cells into draft tests (`draft: true`, never locked);
     - AI may add draft tests only and never edits a locked test. The server enforces this.
   - B, variations and fuzzing:
     - `services/fuzz.py` generates inputs within the declared shapes (edge values, missing optional fields, wrong-but-allowed values, plus a seeded random sample);
     - each input runs in test mode;
     - any input that crashes a node, breaks a contract or hits an unexpected error path is reported as data (node, field, input, error);
     - a found case can be saved as a draft test.
   - C, drift and smoke tests:
     - on every real run, compare outside replies with the spec or contract and store a drift record ("Risk API reply changed");
     - tests that use that mock are marked stale;
     - opt-in live smoke tests run before a release (a release check) and on a schedule (cron), against the real or sandbox API, recording pass or fail;
     - a migration if needed.
3. **Wave 2:** D, the studio:
   - "AI fill gaps" on the grid, with draft tests shown and approve/reject;
   - a variations panel with "save as test";
   - a drift banner and stale badges;
   - smoke test settings and results;
   - keep the approved look and pass `design:check`.
4. **Testers (separate agents, from the contract):**
   - backend integration plus an API journey, all with the fake LLM;
   - Playwright spec `17-…`.
5. **Fix agents** for bugs; the lead validates with reruns and screenshots.
6. **Gates:**
   - full engine suite (0 failures, same 2 xfails);
   - studio unit tests, tsc, lint ≤ 96, design:check;
   - full e2e.
7. **Demo:** the AI fills grid gaps, a variation finds a real bug, and a drift alert fires when the fake API's reply changes shape.
8. Report in chat, then wait for "commit".

## Rules
- Default automated suites use the fake LLM. Owner asked (2026-10-17) for real Gemini testing too: opt-in live tier `tests/live/test_test_ai_gemini.py` (WORKFLOW_LIVE_GEMINI=1) plus a live browser demo, run by the lead. The `.env.local` key is never printed.
- Agents never touch git. One e2e run at a time: `flock /tmp/e2e.lock npx playwright test -c e2e [file]`.
- Kill leftover servers by PID only.
- AI/LLM node tests stay out of scope (Stage 7).
