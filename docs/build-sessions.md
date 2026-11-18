# Build sessions

The overnight build of [`demo-build-plan.md`](demo-build-plan.md), split into seven sessions run one after another on the `motion-components` branch. An orchestrator session starts each one, reruns the full suite after it finishes, and only then starts the next.

| # | Session | Plan steps | Proven end-to-end by |
| --- | --- | --- | --- |
| S1 | Skeleton + data | 1–2 | Upload a CSV over HTTP and get its profile back; Playwright sees "live" with the API up and mock mode with it down |
| S2 | Engine + six models | 3–5 | For each model: POST a run, read SSE to the end, replay from index 0 and get the same events; golden scores with fixed seeds |
| S3 | Race + ensembles | 6–7 | Playwright runs a five-model race, reads the leaderboard, builds a stack, and checks the verdict against the API |
| S4 | Understand + keep | 8 | Register and approve the winner; Try It returns a class, probabilities and reasons |
| S5 | Training Gym + Vela | 9 | Move a slider, apply a Vela patch, retrain, and see the change in Experiments |
| S6 | Autopilot | 10 | The inbox finds the planted label errors; answering improves the score; it runs without an API key on layer 1 |
| S7 | Hardening | §9 | Full suite three times from a clean clone; reset command; README and demo script |

## Rules for every session

1. Start with `git pull origin motion-components`, then run the full suite (`make test-full` once it exists). If it's red, fix that before starting new work.
2. Build the backend first and test it over HTTP the way the UI will call it. Then build the UI. Then add Playwright coverage.
3. Add tests for everything you build. Never skip, delete or loosen a test to get to green.
4. Make small commits and push only to `motion-components`. Open no pull requests.
5. Keep the design system (`src/shared/styles/tokens.css`, the README's rules) and keep mock mode working when the API is down. Follow the folder rules in the README.
6. Don't add Redis, Celery, Docker or Postgres. Use one Python process with SQLite, as the plan says.
7. Finish by writing `docs/progress/S<n>.md`: what works, what doesn't, the test counts, and anything the next session must know. Commit it and push.

## Regression suite (built in S1, grown in each later session)

- `backend/tests/`: pytest with httpx against the real app. It covers every endpoint in plan §7, SSE order and replay, restart recovery, golden metrics (macro-F1 pinned within ±0.005 with fixed seeds), stacking without leakage, and voting equal to the mean of its parts.
- `e2e/`: Playwright against the real API and the Vite dev server. It covers the headline flow and opens every page in both live and mock mode. Pass `--trace off`.
- `make test` runs the fast tier: pytest without the slow marks, `tsc --noEmit` and the build. `make test-full` adds the slow marks and e2e.
