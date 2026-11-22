# LBL-VERIFY: independent review of docs/plans/labeling.md

**Reviewer:** LBL-VERIFY (adversarial, not the author), 2026-11-22. **Reviewed:** labeling.md at c955608 on motion-components.
**Checked against:** backend/app at the same commit (frames.py, lake/, engine/, catalog/, authz/, jobs/, llm.py, registry/,
sqlsource/, vela/, gym/), docs/plans/data-platform-final.md, build-tracker.md and its carried notes. §12.1 of the plan (the
author's red-team pass) was not taken as evidence. Each finding below was re-derived from the code or from a worked example.

## Verdict

**Sound with changes.** The core idea is right and fits the platform: an append-only event log on the root, keyed by
`__row_id`, frozen snapshots, a pinned overlay, and a materialise path. Most hooks the plan names exist where it says
(spot-checked: `worker.py:85` has_label, `:729` record_run_input, `:771` data_gate, `schema.py:48` annotations, `:872` run_inputs,
`:971` ri_ro_u, `pg_schema.py:6` TEXT JSON, `:189` APPEND_ONLY, `policy.py:39`, `:144` POC, `:186` FOUR_EYES, `llm.py:444`
fallbacks, `certify.py:218` inputs_gate, `lake/api.py:347`, `catalog/api.py:194`, `registry/build.py:248/274`,
`dataset_edit.py:59`, `purge.ACTION`, `artifacts.SKIP_TABLES`, `stats.wilson`, `jobs/queue.RESOURCES`, `not_before`).

Three things are wrong enough to block L01a as written. The overlay will be bypassed or will break dispatch on the bounded
10M path. Stale labels can never be fixed by re-review, because consensus mixes votes made on different row contents. And
guest annotators cannot be authorised through `require()`, so the fallback is the leak D12 was meant to stop. All three are
cheap to fix now and expensive after the snapshot format and the authz rules freeze.

**Counts:** 3 critical, 16 major, 14 minor.

## Critical

### C1. The overlay is bypassed on the bounded/10M path, and the new ref type breaks dispatch
**Evidence.**
- The plan adds `Labelled(base, …)` as a new `InputRef` that `frames.resolve(cfg)` returns. 24 `isinstance(ref, (Table, JoinFrame))` /
  `parquet_ref()` checks in 8 files (vela/rows.py, vela/experiments.py, lake/mask.py, lake/frame.py, engine/serving.py,
  engine/api.py, gym/export.py, registry/build.py) would read a `Labelled` as "not Parquet". They would then fall to
  `path_of()` (FrameError) or the legacy/CSV branch.
- Readers that take labels straight from parts, not through `frames.read`, are missing from §5.5:
  - `engine/serving.read_ids` (`serving.py:169-189`, `read_part_columns` on the label) feeds `RunRows.label_ok`
    (`serving.py:276`). Per F-X5a, `RunRows` is the path the build, Model Doctor, Vela findings and the scoring reference
    take above `ENSEMBLE_DOCTOR_ROWS`.
  - `bounded_holdout` uses `read_ids` (`:353`).
  - `vela/rows.sample_table` uses `read_ids` (`vela/rows.py:111`).
  - `gym/export.build_script` (`gym/export.py:69-75`) exports a "reproducible" script that hashes and reads the base parts.
- `registry/build.py:551,838` records `data_hash = frames.input_sha256(cfg)` = base bytes only.

So on a 10M overlay run, rows labelled only by the overlay are `label_ok=False` and drop out of diagnostics and the
scoring reference. The exported script silently trains on base labels. The card's data hash claims the base and overlay
runs had identical inputs. Grep gate 3 as worded ("label-column reads outside frames.py / lake/ / labels/") does not catch
`serving.read_ids`, because it reads `[ROW_ID, label]` through `frames.read_part_columns`, a sanctioned helper.

**Fix.**
- Make the overlay a subclass, not a sibling: `LabelledTable(Table)` and `LabelledJoin(JoinFrame)`, frozen dataclasses with
  `overlay_blob`, `overlay_sha256`, `columns`, `holdout`. Every existing isinstance check and `lake_table()` keeps working
  on the base parts.
- `read_ids`, `RunRows`, `bounded_train`, `bounded_holdout`, `vela/rows`, `lake/frame.read_rows/iter_rows/read_ids_label`
  and `frames.read` apply the overlay when `ref` is labelled.
- `gym/export` ships the overlay blob and applies it in the script, or refuses with 409 `labels_export_unsupported`.
- The card records `overlay_sha256` next to `data_hash`.
- The worker records **`labels_fitted_sha256`** = sha over `(__row_id, label)` of the rows actually fitted (and of the
  sample when sampled), pinned in `run_inputs` and recomputed by run verify. That is the end-to-end proof L04a's done-when
  lacks today.
- Gate 3 becomes: any `read_ids` / `read_part_columns` / `lake_table` use with the run's label column outside `frames`,
  `lake/frame`, `engine/serving` and `labels/` fails.
- L04b's chain flips 10 labels by overlay on a table above `ENSEMBLE_DOCTOR_ROWS`. It asserts the flipped labels in the
  Doctor, the registration holdout, slices, explain, fairness, the credit report, learning curve and the export script, and
  `labels_fitted_sha256` equals the overlay's expectation.

### C2. A stale label can never be fixed by re-review (votes from different row contents are pooled)
**Evidence (worked example).**
1. Row R on v3, context X. A and B vote `Fraud`.
2. v4 edits a context column → context Y. R is stale on v4 (§5.1) and goes to the stale queue.
3. C reviews R on v4 and votes `Other`.
4. The snapshot is version-independent and resolved once (§5.3–5.4). Majority = Fraud 2 : Other 1, so the deciding events
   are A and B with hash X.
5. Overlay on v4: hash X ≠ Y → stale → excluded. Overlay on v3: Fraud.

C's re-review is outvoted by votes about different content. R stays stale on v4 until two more people agree with C, or an
adjudication happens. The "stale → review queue → live" loop that requirement 4 relies on does not close. The snapshot
format (§4.3) has one row per `(row, column)`, so fixing this after L03 means `ensemble.labelsnap/2`.

**Fix.** Resolve per **epoch**. An epoch is the set of events on `(row, column, item_key)` with the same `row_hash_ctx`
(and `base_value_hash` for corrections).
- The snapshot stores one entry per `(row, column, item_key, epoch_hash)` for the latest K epochs (default 2).
- The overlay picks the entry whose `epoch_hash` equals the version's recomputed hash. No match → stale.
- Votes never cross epochs. A re-review on new content starts a new epoch and decides it alone (with `min_votes`).
- Add `item_key` and `epoch_hash` to the Parquet now (§4.3), in format `/1`.

### C3. Guest annotators cannot be authorised; the default answer is exactly the leak D12 avoids
**Evidence.**
- `authz/policy.py:150-181`: `decide()` allows by team role, `rr`, admin, share or `anyone`. There is no membership-scoped
  grant.
- §6.1 gives `label.annotate` / `label.review` "min role viewer plus task membership (handler check)". `require()` runs
  before the handler, so a principal with no team role (D12's guest) gets 403 on `next` / `answers`.
- §14 Q3's default then says "outsourced operations staff would be added as principals in the team with viewer role". A
  viewer has `data.preview` on every team dataset (`policy.py:39`), which D12 itself names as the reason not to do this.
- `policy.py` is touched only in L01a (lane rule), but the guest mechanism is first exercised in L06b. That is too late to
  change policy.py under the lane rules.

**Fix.**
- L01a adds `Rule.task_role: 'annotator'|'reviewer'|None` and scope kind `label_task` (team via task → layer). `decide()`
  allows it when `label_task_members` has the principal, the role and no `paused_at`. It is checked before the team-role
  branch and is never widened by POC mode beyond what POC already does.
- `/authz/coverage` lists the new kind.
- §14 Q3 default: guests (no team role), never viewers.
- L01a's chain proves a guest gets 200 on `next` and 403 on `GET /datasets/{id}/rows`.

## Major

### M1. Identity loss outside the upload path: SQL refresh, labelled-column rename, a re-added row
- **SQL refresh** (`sqlsource/api.py:212` → `datasets.create_from_frame`, `datasets.py:1500`: "a refresh is a version of its
  parent with fresh row ids (unpaired)") makes every labelled row orphaned on every refresh. There is no `/check` and no
  ack: `label_identity_lost` is added only to `/uploads/{u}/check`.
- **rename_column / drop_column / cast_column on the labelled column** (`dataset_edit.OPS`): events keyed `column_name =
  'category'` stop applying after a rename to `complaint_category`. §5.1 covers renamed *context* columns only. A cast
  changes `canon()` of a context column (int `1` → double `1.0`), so every label goes stale with reason `context_changed`.
  That is deterministic, but it gives no way out except relabelling.
- **Deleted then re-added** (same business key, later version): the parent no longer has the row, so `label_identity_lost`
  doesn't count it and the new id leaves the labels orphaned.

**Fix.**
- `label_identity_lost` on every writer that assigns fresh ids: SQL refresh (`refresh` gains `acknowledge` and the count),
  and `derive()` with `row_map = -1` for previously labelled ids.
- `label_schemas.column_map` (old → new column name) is appended automatically when a version's manifest records
  `rename_column` on a layer's column. The overlay and status report `column_missing` explicitly (409 at run creation,
  never silent).
- `type_changed` is a stale reason of its own, with a bulk "re-anchor" action. A reviewer confirms a page of type-only
  stale labels, and each confirm is an event that starts a new epoch.
- Status lists orphaned rows whose declared key reappears in V, so remap can be offered.

### M2. `base_value_hash` on every event marks every materialised or absorbed label as a conflict
**Evidence.** Label L = `Fraud` on v4 where the base target is NULL. `base_value_hash` = HMAC(null). Materialise → v5 with
the cell = `Fraud`. On v5 the base hash ≠ the stored hash → `base_changed` → conflict (default) → not applied, sent to the
stale queue. The same happens after every approved proposal (§5.11), every monthly outcome import that upstream later
writes into the base, and every F13/F14 adapter materialise (§9 step 9). Each materialise floods the stale queue with
labels that are correct.

**Fix.** Add status **`absorbed`**: the current base value equals the label's value → live, a no-op in the overlay,
counted. `base_changed` only when the current base ≠ the seen base **and** ≠ the label value. Add it to the L02 chain and
to L05's chain (materialise, then status on the new version → absorbed = rows_changed, conflict 0).

### M3. Snapshot results depend on inputs the hashes do not cover (rebuild equality and incremental snapshots are wrong)
**Evidence.** Resolution reads more than events and policy:
- gold-accuracy weights (`weights: gold`), computed from the mutable `label_gold`;
- the prompt's calibration record (`label_prompts.calibration`, a mutable column on an "insert-only" table) for `τ*`;
- the admin `legacy:<name> → principal` map (§9);
- the reference run's p̂ (CROWDLAB);
- the class_map chain;
- maturity `as_of`.

§4.3 defines "same result" as equal `events_sha256` + `policy_sha256` + `values_sha256`. That holds by accident, or fails
after a re-calibration. §15.2 stage 1's incremental snapshot re-resolves only keys with new events, but a new gold answer
changes the weights of every row that annotator voted on, and maturity changes with time without any event. The weekly
"full rebuild must match" check would fail.

**Fix.**
- Manifest `inputs` = `{calibration_ids + sha, gold_version_sha, identity_map_sha, reference_run_probs_sha,
  schema_chain_sha, as_of}` and `inputs_sha256`. "Same result" = events + policy + inputs + values.
- Calibration becomes the insert-only table `label_calibrations`. Gold becomes insert-only with a `retired_at` event
  (versioned).
- Incremental snapshots are allowed only when `inputs_sha256` is unchanged and the policy has no time-dependent field;
  otherwise rebuild in full.

### M4. Immutability is claimed but not enforced on snapshots, schemas, prompts, gold
**Evidence.** §3.1 puts triggers only on `label_events`. `label_snapshots` (`blob_sha256`, `values_sha256`, `state`,
`approved_by`), `label_schemas` and `label_prompts` (both "insert-only") and `label_gold` have none.
- Approval is a column UPDATE on the snapshot row. A stray or app-level UPDATE of `blob_sha256` after approval changes
  what "approved snapshot ls_x" means. Runs are safe because they pin bytes; approvals, shares and the certified gate are
  not.
- The platform's approvals go through `approval_requests` + `policy.four_eyes` + audit (`catalog/certify.py`,
  `shares.py`).

**Fix.**
- `ls_ro_u`: content columns (`layer_id`, `through_seq`, `policy*`, `blob_sha256`, `manifest_blob`, `snapshot_sha256`,
  `values_sha256`, `inputs_sha256`, `counts`, `created_*`) are frozen. Only `state` moves, forward-only
  (draft → approved → superseded, any → purged).
- Approval is an `approval_requests` row (`action='label.snapshot_approve'`), referenced by `approval_request_id`.
- Insert-only triggers on `label_schemas`, `label_prompts`, `label_calibrations` and `label_gold`. The gold `retired_at`
  becomes the only allowed one-way UPDATE.
- On PG, all of them join `APPEND_ONLY`.

### M5. The "content hashes" are not content hashes, and the snapshot format lacks `item_key`
**Evidence.** §4.3:
- `events_sha256` covers `id ‖ seq ‖ kind ‖ source ‖ actor ‖ value`. It leaves out `row_id`, `column_name`, `item_key`,
  `refers_to`, `schema_version`, `confidence`, `is_null` vs. empty and the row hashes. A DB-level change of an event's
  `row_id` (trigger bypass by file access, or a PG superuser) keeps the hash.
- `kind ‖ source ‖ actor` has no separators (`"ab"+"c"` = `"a"+"bc"`).
- The Parquet and `values_sha256` have no `item_key`, so span, pair and list items (§15.1) cannot be stored in
  `labelsnap/1`. That contradicts §15.3 ("/2 only if hash definitions change").

**Fix.**
- `events_sha256` over every frozen column of each included event, length-prefixed or 0x1F-separated, in seq order,
  including `purged_at` (so a redaction changes it visibly).
- Add `item_key` and `epoch_hash` to the Parquet and to `values_sha256`, now, in `/1`.

### M6. Snapshot approval is not bound to what it approved; POC approvals carry into enforce
**Evidence.**
- The snapshot is version-independent (§5.4). Approval runs the contract against "the latest default version" (§8.2/5.4).
  A run on another version gets a different applied set (staleness, orphans, holdout) than the approver saw, and still
  passes `inputs_gate` with `labels_approved`.
- POC mode (`policy.py:144`, `four_eyes` → None) lets one person create and approve. The approval stays `approved` after
  POC is switched off. No platform approval records that it was made under POC (grep `poc_mode()` callers).

**Fix.**
- The approval records `approved_on_version_id`, `table_sha256` and the applied-set `values_sha` it was shown.
- `inputs_gate` passes an overlay only when the run's version `table_sha256` equals it, or the run's applied set ⊆ the
  approved one (same `epoch_hash` per row). Otherwise 409 `labels_approved_on_other_version`, with a one-click re-approval.
- Approvals carry `under_poc: true`, and `inputs_gate` refuses them when POC is off.

### M7. Between L04a and L06b the certified-inputs gate does not see overlays at all
**Evidence.** `inputs_gate` (`certify.py:218`) checks version certification only. L04a ships overlay training, and L06b
adds the overlay check. In between, a model trained on an unreviewed overlay over a certified version passes promotion.
The first slice (§15.4) stops at L04b plus part of L07, so the slice ships in that state.

**Fix.** L04a adds a fail-closed stub to `inputs_gate`: any run with `overlay_sha256` under `require_certified_inputs` →
409 `labels_not_approved` until L06b replaces it with the real check.

### M8. Holdout contamination paths the plan leaves open
1. **Few-shot from holdout.** §5.7 lets gold include holdout rows, and the `few_shot` pool is drawn from gold. Holdout
   rows and their human labels go into the prompt that labels training rows, so holdout information flows into training
   labels.
2. **`import` may grade the holdout** (seed `may_grade_holdout=1`), with unknown provenance. An imported file can be
   another model's or an LLM's output. Doctor proposals (§5.11) may be recorded as `edit` (grading 1) though they are
   cleanlab suggestions.
3. **Immature outcomes stay `0` in the holdout.** Maturity is applied by the training overlay, which never touches
   holdout rows (§5.7 rule 2). The headline `original` metric still counts 3-month-old loans as "no default", which is
   the silent bias §5.12 claims to remove.
4. "Holdout" is per split (and split_key). Queues and pools must name which.

**Fix.**
1. The `few_shot` and `calibration` pools exclude the **protected set** = the holdout rows of every split (all
   split_keys) of every version of the root; this is checked at insert.
2. `import` gets `may_grade_holdout=0` unless the import declares `provenance.human_attested` with a reason (audited).
   Doctor proposals keep `source='cleanlab'`.
3. Maturity is a **row exclusion**, not a label change. It applies to training and holdout alike, the excluded rows are
   counted on the card, and both metrics exclude them.
4. Fills and pools use the protected set.

### M9. LLM calibration is not valid as specified
- **Selection bias.** `τ*` = the smallest τ with `acc_lo ≥ target`, chosen by scanning thresholds on the same calibration
  set. The minimum over a grid is optimistic, and the Wilson bound of the chosen τ is no longer a 95 % bound.
- **Class imbalance.** "Overall accuracy ≥ 0.95" with Fraud at 2 % can pass with Fraud recall at 50 %. "≥ 20 per class"
  stratification also skews the overall accuracy unless it is reweighted to the population.
- **Gold truth anchoring.** Nothing says gold values were made without seeing the LLM pre-label. Gold taken from tasks
  with `show_prelabel=1` makes calibration measure the LLM against humans who agreed with it.
- **Disjointness is per task** (`label_gold` PK by task). Prompts are per layer, so few-shot from task A and calibration
  from task B can overlap.
- **Not bound to the run.** Calibration does not record `samples_k` / `mode` (adaptive k changes the confidence
  distribution) or the population the run labels.

**Fix.**
- Pools are per layer and disjoint per layer.
- Gold rows carry `provenance.blind = true` (made without a pre-label) or `expert`; calibration refuses others.
- Split the calibration pool in two: pick τ on one half, certify `acc_lo` on the other.
- Require a per-class lower bound (or recall) ≥ `target_class` for classes the policy marks `critical`, with population
  reweighting.
- Bind calibration to `(prompt_sha, samples_k, mode)`.
- Every label run draws a random **audit sample** of auto-accepted rows (default 1 %, ≥ 50) into a blind `qa` fill. The
  snapshot refuses `accept.llm` for that run until its audit `acc_lo ≥ target`. This catches drift between the gold pool
  and the population.

### M10. LLM PII paths
1. **Untagged columns are sent by default** (only tagged and free-text columns are excluded). Tagging coverage in a bank
   is never complete: a 15-character `customer_name` column without a tag goes out.
2. **The rationale echoes data.** With approved unmasked columns, the rationale (≤ 300 chars) can quote them. It is shown
   to annotators, who see masked context, and stored in the cache. That is a masking bypass.
3. **`count_tokens` and preview send rows** to the vendor before a run is approved. The estimate path is not mentioned in
   the PII guard.
4. **Few-shot examples** go into the system prompt. The plan does not say they pass the same column policy and redaction.
5. **The cache key** `payload_sha` is a plain sha over low-entropy row payloads, a dictionary oracle (the same argument
   §4.2 uses for HMAC).
6. **Error messages and logs** of `rows_failed` (schema validation of model output) can carry row text.

**Fix.**
1. Allowlist: every sent column must have a PII classification set by a steward (`pii_reviewed: none|…`). Unreviewed
   columns are refused with 409 `pii_unreviewed`.
2. The rationale is PII-scanned and masked per viewer, and never shown when unmasked columns were sent.
3. The estimate uses a local tokenizer approximation (chars/3.5 + a margin). `count_tokens` and preview go through the
   same guard and audit.
4. Few-shot rows pass the same policy.
5. The cache key is HMAC(layer salt).
6. Errors and logs carry codes and row ids only.

### M11. Cost caps can be overrun, and Batches are not resumable
1. **The monthly cap races.** Two jobs each check `spent + est ≤ cap` and both start.
2. **Batch mode cannot stop at `max_usd`.** Requests are submitted at once and spend is known only when results return.
3. **No persisted batch id.** `label_llm_runs` has no batch id, so a worker restart after submission re-submits and pays
   twice.
4. **A 24 h batch held in a `light` slot** starves other light jobs.

**Fix.**
- A spend ledger `label_llm_spend (team_id, run_id, reserved_usd, actual_usd)` is updated under the team row lock.
- A run reserves its worst case = rows × k × (input + `max_tokens` output) before it starts. Batch mode submits in chunks
  (≤ 10k requests), each reserved.
- `label_llm_batches (run_id, chunk, batch_id, state, submitted_at)` is written before submit returns, and polled by a
  re-enqueued job (`jobs.queue.enqueue(…, not_before=)`) that holds no slot.

### M12. Annotators can write labels on any row, bypassing queue, gold and blind
**Evidence.** §6.2 lets `label.annotate (task)` call `POST /label-layers/{l}/events` directly. An annotator can then label
arbitrary `row_id`s with no lease, no gold sprinkling and no blind check. That is targeted label poisoning: flip a
specific customer's fraud label, then outvote with a second account. Separately, §5.9 quality (per-annotator gold accuracy,
pace) is readable by anyone with `label.read` (viewer and shares). That is personnel data.

**Fix.** Direct events need `label.task_manage`. Annotators write only through `answers` against their own leased
`assignment_id`. `GET …/quality` needs `label.review` or `label.task_manage`. Shares never see it.

### M13. Purge leaves derivatives of the purged values
**Evidence.**
- The row hashes of *other* events whose context included the purged column (e.g. `ssn`) are HMACs keyed with a salt
  stored in the same DB (§4.2: "not a secret from DB readers"). For low-entropy columns that is recoverable by
  dictionary.
- The trigger allows nulling hashes only together with `value NULL`, so the redactor must either destroy unrelated
  labels or keep the derivatives.
- `label_events.provenance` of non-targeted events (an LLM rationale naming the person) is skipped by the generic scrub
  (`SKIP_TABLES`), and the label redactor touches only `row_id ∈ rows OR column ∈ columns`.
- `label_gold.value` is not in §8.4.

**Fix.**
- A second allowed UPDATE form, **hash-only redaction**: `row_hash_ctx/all → NULL`, `hash_redacted_at`, `purge_id`
  approved; value and every other column frozen. Those labels become `stale (context_changed)`, as §8.4 already accepts.
- The label redactor also redacts (full form) any event whose provenance matches a purge needle.
- Gold rows of purged rows/columns are retired with the value nulled (the one allowed gold UPDATE).
- The sweep searches all label tables.

### M14. The minimal first slice ships unpurgeable PII corrections and the gate hole
**Evidence.** §15.4: the slice = L01a–L04b + human L07, including the `cell_correction` task type. A correction to `ssn`
writes the value into an append-only table that cannot be redacted until L06a, outside the slice. M7's gate hole is also
in the slice.

**Fix.**
- Until L06a, appending to a PII-tagged column is refused (409 `pii_column_needs_purge_support`).
- The L04a gate stub (M7) is in the slice.
- XL0, a slice check, is added to the phases.

### M15. Phases: L07 and L08 are each two sessions; the slice check is missing; L01a lacks pieces later lanes need
**Evidence.**
- L07 bundles tasks, membership, 7 fill strategies, leases, gold grading and auto-pause, review/adjudicate, κ/anchoring
  quality and rules. L08 bundles prompts, the PII guard, destinations, preview, calibration, online and batch runs,
  budget, cache, self-consistency and the fake oracle. The plan itself expects both to spill.
- §15.4's XL1-lite is not in the phase list.
- The guest rule (C3), the immutability triggers (M4) and the format fields (C2, M5) must land in L01a, because the lane
  rule freezes policy.py and the DDL after it.

**Fix.** The phase list in the plan is updated (see the end of this file). 19 sessions:
- L07 → L07a (tasks, members, human fills, next/answers, leases, gold, review) and L07b (quality/anchoring, active and
  cleanlab strategies, rules).
- L08 → L08a (prompts, PII allowlist, destinations, fake, preview, online runs, cache, spend ledger) and L08b
  (calibration v2, audit sample, batch mode, self-consistency, acceptance).
- XL0 after L04b + L07a.

### M16. Gaps for a bank that the survey (§11) leaves out
1. **Fairness of labels.** Annotator and LLM accuracy and agreement split by protected-group proxies. The fairlending
   module already computes BISG-style groups: use them for picking and analysis only, never shown. LLM acceptance is
   refused when a group's `acc_lo` < target − margin. Label bias is a fair-lending exposure distinct from model bias.
2. **A label-quality statement on the model card** for model risk (SR 11-7 / SS1/23 style):
   - estimated label-noise rate with CI, from gold and IAA, per class;
   - the share of training labels by source and method;
   - the share accepted from machines;
   - the guideline version.
3. **Segregation of duties and conflict of interest.** Exclude annotators from rows they are party to (an investigator
   labelling their own fraud cases, a relationship manager labelling their book). This needs a task-level exclusion rule
   over a column (`exclude_if: {column: 'officer_id', equals: principal.attr}`).
4. **A retention schedule for label data.** Events, LLM cache, snapshots and gold under the P24 retention plan per team.
   Today labels have no retention class.
5. **Look-ahead in corrections.** A feature corrected with information learned after the decision date (income verified
   after default) leaks the future into training. Events need `known_at` for corrections on outcome layers, and
   materialising one into rows observed before `known_at` warns with `lookahead_correction`.
6. **IAA gates on approval.** The snapshot policy may require κ ≥ x (e.g. 0.6) on the applied columns before approval.

## Minor

1. **§10 storage:** 350 B/event is ~2.5× low. Four 64-char hex hashes (256 B), provenance JSON (~250 B for LLM events),
   about 6 TEXT ids, the tuple header and 5 indexes come to ≈ 700–950 B/event. 0.6M events → ~0.5 GB per layer; 10⁹
   events (stage 2) ≈ 1 TB before segments. Fix the numbers. Optionally store hashes as 32-hex (128-bit, keyed, ample).
2. **Stage-2 per-layer seq lock.** One hot layer with 1 000 annotators (~300 events/s) plus 500-event LLM batches holding
   the lock ~50–100 ms is near a single-row-lock ceiling (~200–500 commits/s). Plan group commit or block-allocated seq
   with a commit watermark that preserves the prefix contract.
3. **Stage-2 cold segments.** They need to delete events from PG, which `le_ro_d` forbids, and hash partitioning by
   `layer_id` cannot detach seq ranges. Plan `PARTITION BY HASH(layer_id)` sub-partitioned `BY RANGE(seq)`, with detach as
   the compaction path (no row DELETE).
4. **Closed CHECK lists** on extensible enums (`label_events.kind`, `label_task_types.item_shape`,
   `label_proposals.origin`, `label_llm_runs.purpose`) need SQLite table rebuilds to extend. Validate them in the
   registries instead. "Never DDL" is overstated: ranking adds `label_items`.
5. **Import has no `version_id`.** It is needed for id/key mapping and the server-side hashes.
6. **Overlay size.** The plan assumes ≤ 100k rows. Whole-table LLM labelling and maturity exclusion produce millions. Add
   an L04a gate at 2M overlay rows.
7. **Post-fit consumers** must apply the **pinned** overlay blob, never recompute it. Diagnostics report the `original`
   holdout unless the run is `corrected`, and say so.
8. **Join frames.** The labelled column must be on the grain table (400 `label_not_on_grain`). Context from joined
   tables has no masked view yet (F-PII follow-up), so in the POC the context comes only from the layer's own table.
9. **Targeted injection.** A customer can write complaint text that pushes the model away from `Fraud`.
   Policy `accept.llm.never_auto_from: ["Fraud"]`: a machine label that is not a critical class, on a row whose rules or
   prior labels say critical, is never auto-accepted.
10. **Registry rows.** Seed them from code, not a runtime API. Treat `may_grade_holdout=1` on a new source as a
    governance change (audited, admin). Say whether a new plug-in needs a restart (the L01a chain implies not, §15.1
    implies yes).
11. **Grantee layers after revoke** keep labels on the owner's data indefinitely. Revoke → freeze, then retention or
    purge per the owner's policy.
12. **LLM API specifics** (thinking modes per model, Batches vs. server-side fallbacks, `output_config.format`) were not
    verified by this review. L08a checks them against the current SDK docs before coding.
13. **Four eyes on content.** The approver should not be the author of more than 20 % of the applied decisions
    (adjudications included), not just ≠ the snapshot creator.
14. **Rationale retention.** Accepted LLM labels keep their (PII-scanned) rationale in event provenance, not only in the
    evictable cache, for model-risk audit.

## Required changes before L01a

1. C1: `LabelledTable(Table)` / `LabelledJoin(JoinFrame)` subclasses; overlay in `read_ids` / `RunRows` / bounded readers /
   `gym/export`; `labels_fitted_sha256` in `run_inputs`; gate 3 reworded.
2. C2 + M5: the epoch model, plus `item_key` and `epoch_hash` in the snapshot Parquet and hashes (`labelsnap/1`);
   `events_sha256` over every frozen column with separators.
3. C3: `Rule.task_role` and scope kind `label_task` in L01a; §14 Q3 default = guests.
4. M4: immutability triggers on snapshots, schemas, prompts, calibrations, gold; approval via `approval_requests`.
5. M13: the hash-only redaction form in the `le_ro_u` trigger.
6. M3: `label_calibrations`, versioned gold, manifest `inputs_sha256`.
7. M2: `absorbed` status in the carry-over table.
8. M7 + M14: the fail-closed overlay stub in `inputs_gate` (L04a); PII-column refusal until L06a; XL0 in the phases.
9. M12: direct events → `label.task_manage`; quality → reviewer/manager.
10. Phase list as below.

M1, M6, M8–M11 and M16 are folded into the plan text and land in their phases (L02, L06b, L04a/L08a/L08b, L10), not
necessarily before L01a.

## Confidence

| Area | In the plan as written | In the plan as amended | Notes |
|---|---|---|---|
| Correctness | 0.55 | 0.80 | Hooks verified. C1/C2/M1/M2 found by code and worked examples; the remaining risk is unseen readers (31 `resolve(cfg)` files). |
| Safety (LLM, PII, governance) | 0.50 | 0.75 | LLM API details unverified (minor 12); the vendor-residency decision is outside engineering. |
| Growth | 0.55 | 0.75 | Contracts hold once `item_key`/epoch are in `/1`. Stage-2 numbers need the lock/segment changes. |
| Phasing | 0.60 | 0.80 | 19 sessions; L04b and L08b are still the likeliest to spill. |

## Final phase list (as folded into labeling.md §13)

| # | Lane | Depends on |
|---|---|---|
| L01a | D | — |
| L01b | D | L01a |
| L02 | D | L01b |
| L03 | D | L02 |
| L04a | D2 | L03 |
| L04b | D2 | L04a |
| L07a | O | L03 (beside L04a–L04b) |
| XL0 | ⟂ | L04b, L07a |
| L05 | D | L04b |
| L06a | G | L05 |
| L06b | G | L06a |
| L07b | O | L07a |
| L08a | O | L07a, L06b |
| L08b | O | L08a |
| L09 | D | L05, L07a |
| L10 | O | L04b |
| UL1 | U | L07b |
| UL2 | U | UL1, L08b, L05 |
| XL1 | ⟂ | all |

15 build + 2 UI + 2 checks = **19 sessions ≈ 38 h** serial, plus ~15 % fix-up ≈ 44 h.
**Critical path:** L01a → L01b → L02 → L03 → L04a → L04b → L05 → L06a → L06b → L08a → L08b → UL2 → XL1 (13 sessions).
