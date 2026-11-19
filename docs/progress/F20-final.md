# F20 · Final verification

Status: **partly done — stopped early by the orchestrator (relaying the user).** The second clean-clone smoke, the
`make reset` rerun and the pytest rerun were cancelled on request. The demo walkthrough was run once. Every number in
the matrix below comes from the existing progress notes and smoke outputs. None of them comes from a new run, apart
from the clean-clone smoke and the walkthrough reported here.

## What F20 did

- **Clean clone, run 1 (stopped early).** `git clone --branch motion-components … /tmp/clean` (no data, no
  node_modules) → `make setup` in **26 s** (uv cache) → `make smoke` (the full default).
  - It was stopped by request partway through the `monotone` section, at **98 passed, 0 failed**:
    core 21/21 · samples 3/3 · autopilot 8/8 · forecast 12/12 · anomaly 10/10 · import 5/5 · fresh 9/9 · latency 8/8 ·
    targets 6/6 · splits 7/7 · cost 7/7 · monotone 2/2 (so far).
  - The text one-row latency step in that run: WeightedEnsemble_L2 τ₉₅ 18.0 ms vs LightGBM 9.2 ms, Δaccuracy −0.0030,
    and it serves LightGBM (the version's τ₉₅ is 9.6 ms).
  - Run 2 (after `make reset`) was not done (cancelled). The last complete full run is F19's **171/171 in 2,509 s**.
- **pytest fast tier:** not rerun (cancelled). The last result is F19's **117 passed, 16 deselected**.
- **Explain gets its own sidebar entry** (with a new icon). Before, the Explain page highlighted Experiments (an open
  issue from F8 and F19). `npx tsc --noEmit` is clean.
- **`scripts/demo_walk.cjs` (new)** walks `docs/demo-script.md` click by click in headless Chromium at 1920×1080. It is
  a walkthrough, not a test suite.
  - It covers the prep for part 8, then parts 1–8: New Model with an out-of-time split → the random twin → Gym cost
    and monotone → cost per 1,000 and the ROC AUC twin → Monotonicity → Register → Reason Codes → Try It on the first
    adverse holdout row → Batch → Model Card → Scorecard → Learning Curve → Model Doctor → Monitoring (challenger,
    stream, alerts, retrain) → the champion's Doctor.
  - It checks that each number the script promises is on screen, within a stated tolerance, and saves a screenshot
    per stop in `docs/progress/F20/`.
  - Usage: `node scripts/demo_walk.cjs [ui_url] [out_dir]` on a fresh workbench (`make reset`, `make dev`).

WALKTHROUGH_RESULT

## Feature matrix

FEATURE_MATRIX

## Remaining limits

LIMITS

## How to run

```
make setup                      # venv (uv, < 1 min) + npm install
make dev                        # API :8000 + UI :5190 → open http://localhost:5190
make reset                      # empty workbench, seeded samples regenerated
make smoke                      # every section (≈ 42–46 min on 4 CPUs); SMOKE_ONLY=core,cost picks sections
node scripts/demo_walk.cjs      # walk docs/demo-script.md headless (numbers: scripts/demo_numbers.py)
```
