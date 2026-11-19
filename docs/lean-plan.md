# Lean refactor plan

This refactor makes the code simpler and leaner, with more reuse. The rule is strict: **no change to business logic or behaviour**. Every number, message, JSON key order and page stays identical.

Three Opus 5.5 reviewers (A: backend engine and statistics; B: the rest of the backend; C: the UI) measured the code and proposed the items below. Sonnet 5.5 sessions carry it out, **one batch at a time** on `motion-components`.

**Measured baseline.** UI: 17,683 TS/TSX + 1,446 CSS lines. Backend: 25,663 lines of `.py` in `app` and `ensemble_models`. Expected removal is about 1,050 lines, around 2.5%. The code is already dense. The real gain is that each shared piece exists once: one stats module, one job helper, one 404 helper, one fetch hook, one formatter set.

## Rules (speed mode: the user asked to make the changes quickly; one full test at the end)

Two Sonnet sessions run in parallel. **Backend** does L1 → L2 → L3 → L4. **UI** does L5 → L6. They touch different files.

1. Start with `git pull --ff-only origin motion-components`, then `make setup`.
2. **No per-item smoke runs and no screenshots.** After each batch, run only quick checks:
   - backend: `cd backend && .venv/bin/python -c "import app.main"` and `.venv/bin/python -m pyflakes app ensemble_models` (if available);
   - UI: `npm run build`.
3. The behaviour rule still holds:
   - **No change to logic, numbers, messages, JSON key order or DOM.**
   - If an item doesn't fit cleanly, skip it.
   - Keep the public names, every `data-testid`, and the route order.
   - **Never edit `backend/app/samples*.py`.**
4. Commit per batch. Before each push, run `git pull --rebase origin motion-components`, because the other session pushes too. Push only to `motion-components`.
5. When all your batches are done, run one smoke pass over the sections your changes touch:
   - backend: `make smoke` (all sections);
   - UI: `SMOKE_ONLY=core make smoke` plus `npx playwright test e2e/mock.spec.ts e2e/pages.spec.ts --trace off`.
   Fix anything you broke.
6. Report to the orchestrator with `send_message`, `session_id` `session_018upNdRigdGd9A2nGzFSQXq`. Include the head SHA, lines before → after, the smoke result, and anything skipped.

## Batches, in order

### L1 · backend: stats, 404 helpers, dead code (A batch 1 + B batch 1)

- **A1 · `backend/app/stats.py`.**
  - `wilson(k, n, z=1.959964, empty=None)`. Keep each call site's z and n=0 return:
    - doctor: `(1.96, empty=(0.0,1.0))`
    - quantile: `empty=(None,None)`
    - forecast: `empty=(0.0,1.0)`
    - monitoring: `(Z, empty=(0.0,1.0))`
    - annotations: the default
  - `delong_parts` and `delong`, moved from shift.py and monitoring.py.
  - Midranks via `scipy.stats.rankdata` in place of `shift._midrank`.
  - Verify with a scratch harness that loads the old functions with `git show HEAD:<file>` and checks old == new exactly on 10k random cases, including edge cases. Don't commit the harness.
- **A2 · `holdout_mask(splits, n)`** in `app/splits.py`, for the 9 copies of the boolean mask.
- **A3 + B1.1 · dead code.**
  - From A: `active_learning_api._running`, `pool.sleep_job`, unused imports and variables (worker `now`, api.py:211 `app`, shift `feats`, registry/api `STAGES`, build `json`, monitoring_api `io`), the shift_api `_base_body` wrapper.
  - From B, verified unused by grep: forecast `_nan_none` and the `_center_col` `cols` parameter, anomaly `rescore_threshold` and `Model.transform`, vela `_fmt`/`_f`/`now()` (import it from engine.store) and the `_overfit_patch` `of` parameter, gym `json_ok` and `"math"` in `__all__`, the gym BACKEND_DIR fallback, the export distill expression, autopilot `_closes_more`/`now()` and its no-ops, drift `BANDS`, monitoring `LEVEL`, monitoring_api `pos`, scoring `info()` copy, tracking `RunTag`/`mv`, main `DatasetSummary`/re-import/`path`, reset `sys.exit`, cost `scorer_for`, the findings `[:0]` comprehension.
- **A6 + B1.2 · one 404 helper module, `app/http.py`.**
  - `run_or_404(request, id, msg=None)` and `dataset_or_404(request, id)`, with byte-identical messages. Kind-specific texts ("No forecast run with id …") are passed as `msg`.
  - Use them in place of:
    - `engine/api._get_run`;
    - the `_run`/`_rec` helpers in learning_curve_api, shift_api, active_learning_api, annotations_api and slices_api (slices keeps its status and kind checks);
    - the inline copies in vela, gym, registry, autopilot and importer;
    - the main.py dataset routes and the forecast/anomaly `_ds`.
  - Check first that `_store(request)` is the same object as `request.app.state.runs`.
  - Also add `main._detail(rec)`.
- **B1.3 · `registry/api.py`.** `_ready_version`, `_reason_args_or_400`, `_batch`; `warm_version` returns early for forecast/anomaly; one `import json`. Leave `edit_card` alone.
- **B1.4 · `main.py`.** One tuple of (module, 409 message) for learning_curve, active_learning, annotations and shift. It drives shutdown, the admin_reset checks and include_router, and the router order must stay the same. Verify that the `/openapi.json` path list is identical before and after.
- **B1.5 · `ensemble_models`.** One `_register(*classes)` in `__init__`, and a private `_binary_scorer` in cost.py. Score functions stay module-level so they still pickle.
- **Smoke:** core, samples, import, forecast, anomaly, quantile, annotators, shift, slices, active, doctor, monitor, cost, monotone, scorecard, targets, splits, reset.

### L2 · backend: job plumbing (A batch 2, items 4–5)

- **`backend/app/engine/jobs.py`:**
  - `alive(pid, module)`
  - `kill_group(pid, sig)`
  - `watch_arg()`
  - `spawn(module, args, log_path, extra_env=None)`, which keeps the same env, cwd, append-mode log, `stderr=STDOUT` and `start_new_session`
  - `watch_parent(ppid[, anchor])`
  - `dev_reload()`, moved here from runner and re-imported there
- **Apply it** to runner, learning_curve(_api), shift(_api), active_learning(_api) and annotations_api. annotations keeps its shutdown semantics.
- **`jsonsafe.read_json`/`write_json`:** same bytes as before (indent=1, `jsonable`), with an atomic tmp file and `os.replace`. Keep the public `read_state`/`write_state` names. Point `annotations_api`'s `an._write` at `write_json`.
- **Do not build a generic JobSlot class.**
- **Do not fix the start()/wait() race** that A reported. It is pre-existing; write it up in the report instead.
- **Smoke:** core, learning, active, annotators, shift, slices, doctor, reset.
- **Manual checks:**
  - `kill -9` the API during a learning curve, restart it → the curve reads "failed" with its stopped message;
  - the same for a run;
  - under `make dev`, touch a backend file during a curve → the curve survives.

### L3 · backend: registry consolidation (B batch 2)

- **2.1 · `build.py`.** Dispatch first, then `_ctx`, `_mlflow_step`, `_finish`, one hash helper. Import `_pct` from fastpath, `holdout_prediction_frame` from worker, and use `fastpath.predict_fn`. `file_hash` and `dir_hash` share one chunk reader.
- **2.2.** `_score_val` with a module-level `one_row_table`. Move `fastpath.deps` and reuse it.
- **2.3 · `store.py`.** Private `_get`, `_update`, `_merge`, with the public wrappers kept.
- **2.4 · `scoring.py`.**
  - `_finish_batch`/`_fail_batch` and `_hist`.
  - Reuse `reasons._numeric_raw`/`_coerce`. Do NOT merge `scoring._coerce` at line 24; it behaves differently.
  - `holdout_predictions_path`.
  - Use drift's `_f`.
- **2.5 · `monitoring_api`.** Share the version and batch loaders through `app/http.py` (with message parameters), plus `_actor` and `_iso_date`.
- **2.6.**
  - tracking `_mv_out`;
  - `tracking.ensure_logged`, also used by `engine/worker.py:733` and `803` (keep its events);
  - card.py KINDS/engine-name tables, with the strings verbatim;
  - `drift_brief`.
- **Smoke:** core, import, forecast, anomaly, monotone, scorecard, reasons, monitor, latency. Also diff the core model card Markdown byte for byte.

### L4 · backend: run creation, forecast, anomaly, vela, autopilot, datasets, models (B batch 3)

- **3.1.** `alloc_run` + `queue_run` in engine/api.py for the 4 run-creation copies (keep the emit kwargs order), plus `display_name(ds)`.
- **3.2.** `_stash_upload` and `_sub_csv` for forecast/anomaly. Keep forecast's catch-all-to-400 as a flag. Report the unchecked `MAX_UPLOAD_BYTES` bug, don't fix it.
- **3.3 · forecast.** `load_for_predict`, `_write_csv`, `_ensemble(cfg)`, the holdout slice computed once, `_floats`. Do not merge the per-item alignment loops.
- **3.4 · anomaly.** Declare `aux` once, compute `_prep_frame` once, add `_expected_path`, `_scores(rec)` and `_audit`.
- **3.5 · vela.**
  - A shared `REG` table, kept in order.
  - `_drop_more` and one out-of-time twin picker.
  - The doctor reuses `twin_se`.
  - One `split_twin` prescription builder, with the strings verbatim.
  - wording imports `NUM` from llm.
- **3.6 · autopilot.**
  - `_new_dataset_version` (keep the key order).
  - One decide() tail.
  - `engine.bulk_answer`.
  - inbox reuses `_editable`.
  - `Counter` for the kind counts.
  - A shared refit setup.
  - `audit._item`, with the key order kept.
  - Keep `_json_default`.
- **3.7 · datasets.** `_write_frame`, `_record`, `sidecar`-based lineage, and a `dataset_errors()` context manager. No global exception handler.
- **3.8 · ensemble_models.**
  - scorecard: a cached `_bins_by_name`, `FeatureBins.iv_parts`, one pass, `sc` computed once.
  - monotone: `_with_spec`.
  - text: `cv < 2`, inline `_text_frame`, drop the unused timing.
  - **Skip** the optional `_LightSklearnModel` base class.
- **Smoke:** core, forecast, anomaly, import, autopilot, doctor, targets, splits, reset, samples, cost, monotone, scorecard.

### L5 · UI: atoms, formatters, dead code (C batch A)

- **A1.** Delete `body.ts` `pretty` and the 5 dead CSS rules. Move `.an-adv summary` to components.css.
- **A2.** `fix`, `pct`, `pctTrim`, `sig4` in `shared/lib/format.ts` replace the local copies at the sites C listed. Do NOT merge the formatters that print differently. Check every call site for NaN.
- **A3.** `useHashParam(name)` for Forecast, Anomaly and Import.
- **A4.** `<Loading>`, `<ErrorCard>` and `<HeroCell>` in `shared/components/Status.tsx`, used only where they render the identical DOM. Diff `.explain-hero` `outerHTML` before and after.
- **Verify:** build; f19 screenshots, all 26, plus a new Import shot; `npx playwright test e2e/mock.spec.ts e2e/pages.spec.ts --trace off` for the mock pages; smoke core, forecast and anomaly.

### L6 · UI: one fetch/poll hook (C batch B)

- **B1.** `useFetch<T>(load|null, deps, {pollMs, while, onData})` → `{data, setData, err, setErr, reload}`, with C's exact semantics.
  - Migrate the 6 tick pollers first, then the simple loads.
  - Do NOT migrate:
    - `mode.tsx`
    - the custom-backoff pollers (BatchDrift:32, LiveAutopilot:71, ResultsStep:37, QuickTrainPage:89)
    - the MonitoringPanel await loop
    - the debounced sites (LiveVela:56, ThresholdPanel:47)
    - the clocks
  - Skip any site that doesn't fit.
- **B2.** The interval pollers move onto the hook (`pollMs` without `while` re-arms after an error too).
- **B3.** `<ApiOnly>` gate and the Forecast/Anomaly loading and error returns.
- **Verify:** build; screenshots of the pages C listed, plus Batch & Drift and the Explain Fairness and Importance tabs; the mock e2e; smoke core.

### L7 · final (orchestrator)

- **Tests:**
  - the full `make smoke` (every section);
  - `make test`;
  - `npm run build`;
  - f19 screenshots at 1366 and 1920, compared against the pre-refactor set in `docs/progress/F19/`.
- **Line counts:** report them before → after for the UI and the backend. Write `docs/progress/lean.md`.

## Deliberately not done (from the reviews)

- **No OpenAPI codegen.** Only 5 of 136 routes declare a response model, so it saves nothing.
- **No generic job class, where-list, Advanced or Card components.** They save little and cost readability.
- **No unification of the t-table, bootstrap or BH.** It would change published numbers.
- **No sklearn or AutoGluon metric swaps.** Their edge cases differ.
- **No changes to the latency-measuring code or the serve cache.** The numbers would change.
- **No sample edits** (fingerprint).
- **Pre-existing bugs found, reported but not fixed here:**
  - the learning-curve and shift start()/wait() race;
  - forecast and anomaly uploads read `MAX_UPLOAD_BYTES + 1` but never check the length.
