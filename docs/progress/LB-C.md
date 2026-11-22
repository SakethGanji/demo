# LB-C: labeling UI (lane U: UL1 + UL2 of docs/plans/labeling.md)

The screens for label layers, built against LB-A's core and LB-B's workflows as they landed. The look follows U4: every
card uses the DataKit (`CardHead`, `Badge`, `CopyId`, `ChangeBar`-style bars, `CoverageBar`, `Callout`, `Flash`,
`ConfirmButton`, `Field`, `Steps`, `Why`, `Stats`). **No backend change.**

## What was built

| Screen | Files | What it does |
|---|---|---|
| **Dataset › Labels tab** | `features/labels/LabelsTab.tsx`, `models/data/DataView.tsx` (tab strip, `?dtab=labels`) | Layers (kind, table, schema version, carry-over bar, state); **New layer** (name, kind, labelled column, classes → schema s1). Per layer: **carry-over status** against any version (live / stale / conflict / orphaned / unlabelled as a `Stats` strip, a five-segment bar in ChangeBar's form, per column, sources; a warning callout when labels went stale); schema and guidelines (classes with their keys, markdown guidelines); **tasks** (progress bar, guests, Label / Review / Quality); **snapshots** (policy, accept LLM ≥ τ*, counts, state badge, sha chips, Verify, Approve with four eyes, **Materialise to version** as a `ConfirmButton` with a reason and what it does); **correction proposals** (cells, four-eyes approve / reject). |
| **Task setup** | `TaskSetup.tsx` | `Steps`: schema (kind of answer, column, classes with keys 1–9, context columns) → guidelines (markdown; a new schema version when they or the classes change) → queue (strategy, n, votes per row, review) → people and gold (annotators and reviewers; someone with no team role joins as a **guest**; gold / blind share, pause threshold; QA gold lines `row = class`; **calibration gold** drawn from the version). Creates schema → task → members → gold → fill. |
| **Labelling screen** | `LabelScreen.tsx` | Keyboard-first: `1–9` class, `Enter` submit (or confirm the pre-label), `S` skip, `F` flag with a note, `←/→` (↑/↓) between rows, `?` guidelines. The row with **PII columns masked** (mask band + 🔒), the guidelines panel, pre-label chip (never on gold / blind), session count and pace, **QA** (gold graded blind, with the Wilson interval), pause callout. Task bar: progress, **κ** with its reading, disputed. |
| **Review / adjudication** | same | disputed / gold failures / stale / flagged; votes side by side (gold beside them); adjudicate by button or hotkey; "you voted on this item" lock (four eyes, relaxed under POC). |
| **Quality** | same | κ (Fleiss or Cohen), done / backlog / disputed, anchoring; per annotator items, per hour, gold accuracy bar with `k/n` and 95 % Wilson CI, agreement with the majority, paused state; pairwise κ chips. |
| **LLM labeling** | `LlmPanel.tsx` | Destination badge (**off** by default with the API's reason; **fake** first when allowed, with why); "Turn on (fake)" sets the team policy with a monthly cap; prompt versions (columns **or a DuckDB SQL query** with a query preview); **What is sent**: columns sent, PII-tagged ones flagged ⚠ when the team has no PII guard (LB-B's user decision), excluded when `pii_guard` is on (with a steward "review as no PII" action for unreviewed tags); budget, estimate, team monthly cap; preview (20 rows), **calibration against gold** (accuracy, Wilson CI bar, passed / not, τ* with coverage and certified accuracy, per class, thresholds table), runs (spend vs cap, cache, redactions), then **Accept ≥ τ\*** (the run's snapshot policy → snapshot) or **Send the rest to people** (`llm_low_conf` fill). |
| **Training** | `Training.tsx`, `models/data/TrainOn.tsx`, `TablesCard.tsx`, `models/RunPage.tsx`, `registry/components/ModelCardView.tsx`, `shared/api/tables.ts` | The run form gains "labels: none \| snapshot …" (`labels: {snapshot_id, holdout: original, stale: exclude}`); the run page and the model card show a **Labels** section: snapshot, its sha, the overlay sha (re-verified through `/label-runs/{id}/verify`), "applied to training rows only (n rows)", "holdout: original labels, untouched". |

Client: `shared/api/labels.ts` (every route of labeling.md §6.2 plus LB-B's aliases; tolerant of both shapes where
LB-B changed them mid-build). Greying: every action button asks `/authz/check` (`label.layer_manage`, `label.task_manage`,
`label.annotate`, `label.review`, `label.snapshot`, `label.snapshot_approve`, `data.version`, `label.proposal_decide`,
`label.llm_run`, `team.settings`, `data.tag_pii`) and says why under or on it; four-eyes greying respects `usePoc()`.
`Badge` tones gained the labeling words (live, stale, conflict, orphaned, draft, paused, fake, off, …) in `DataKit.tsx`.

## Verification

1. **`npm run build`** → ✓ (tsc clean; only the existing chunk-size notice).
2. **Walk** (`LB-C/walk.cjs`), fresh SQLite API on the merged head `0083022` + this lane's commits (`ENSEMBLE_POC_MODE=1`,
   new data dir), Vite :5190, **1366 × 900, deviceScaleFactor 2**, acting as Ana, dataset "Support tickets (sample)":

   | Step | Result |
   |---|---|
   | 1. New layer `category` (6 classes) → create a task (4 steps, Ana annotator + reviewer, Gus, 3 QA gold, 1 000 calibration gold) | ✓ "40 items queued (random) from v1; 3 memberships, 3 QA gold, 1000 calibration gold" |
   | 2. Label 5 rows by keyboard (number key + Enter, then ←) | ✓ 5 `human` events; quality page |
   | 3. LLM: off → turn on (fake, cap $20) → prompt v1 → SQL input shown → preview 20 → calibrate → run 60 rows → send the rest to people | ✓ calibration 83.8 % (95 % CI 81.4–86.0 %) on 1 000 gold, **passed**, τ* 0.80; run succeeded, 52 accepted ≥ τ*, 8 pre-labels → "8 low-confidence rows sent to people" |
   | 4. Take a snapshot | ✓ `lsn_…` through event 65, Verify → hash verified |
   | 5. `set_cells` on one labelled row's `subject` → v2; status against v2 | ✓ live 4, **stale 1**, unlabelled 19 995 |
   | extra: `POST /runs` with that snapshot pinned | ✓ succeeded; run page Labels: 4 rows applied to training, holdout untouched, overlay re-verified |

   **0 failed steps, 0 console errors**; API log **0 × 4xx, 0 × 5xx, 0 tracebacks** on the final run. Call log:
   `LB-C/calls.txt`.
3. Screenshots (`docs/progress/LB-C/`): `01`/`02` task setup, `03` task list, `04`/`05` labelling, `06` quality,
   `07` LLM off, `08` calibration, `08b` SQL input, `08c` what is sent, `09` LLM page, `10` run report, `11` snapshots,
   `12` carry-over after the edit, `13` the Labels tab, `14` run page Labels.

## Notes

- **Gus was not a guest** on the walk: everyone has a role in the default team under POC, so the API reports `guest: false`.
  The UI shows what the API says (the task list counts guests from `members[].guest`).
- Live counts only human labels in the status: LLM labels from `fake` are `simulated` and the status's default policy
  excludes them; the snapshot taken with Accept uses the run's own policy (`exclude_simulated: false` for fake).
- Calibration needs ≥ 200 calibration rows and ≥ 20 per class: the form's default draw is 1 000 rows (the rarest
  class of the sample is ~3 %).
- Not built: rules UI, label import, unmask request flow beyond its status, F13/F14 run-panel banners, snapshot diff.
- The scratch APIs, data dirs and Vite were in the scratchpad.
