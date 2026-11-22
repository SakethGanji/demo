# LB-B: labeling workflows (L07 tasks/queues/QA/rules, L08 LLM labeling, L09 migration, L06b guests)

**Files (all new except two adapter hunks):** `backend/app/labels/{tasks,llm_label,pii_scan,workflows,core_bridge,lbb_common,lbb_schema}.py`,
`labels/api_tasks.py` + `labels/api_llm.py` (LB-A's empty routers, filled), and adapter lines in
`engine/annotations_api.py` (`answer`) and `engine/active_learning_api.py` (`answer`). No edits to LB-A's core modules or
schema: LB-B's extra columns are additive `ALTER TABLE … ADD COLUMN` on its own tables (`lbb_schema.py`), and there is one new
insert-only table, `label_calibrations`. Every label is written through `app.labels.append_events` (`core_bridge.py`).

**Verified** on a fresh API on SQLite (audit mode, POC off) with manual httpx chains (scratch scripts s1–s10). The
dataset is `support-tickets` (20k rows) and the F14 sample is run `lbb-f14`.

| # | Chain | Result |
|---|---|---|
| 1 | layer + schema v2 (6 classes); PII tags reviewed (`ticket_id` pii_direct); task votes 2, gold 20 %, blind 10 %, min gold 3 @ 80 %; members vik, wen (annotators), ben (reviewer); gold qa 6; fill random 20 → 40 items + 10 gold | ok. `cole next` → 403 `not_a_member` |
| 2 | `vik/wen next` → 25 items each, `ticket_id` shown as `•••`, no pre-label on gold/blind; wen answers truth; vik is wrong on half | events `source=human`, actor = principal, never a body name |
| 2b | vik's gold 3/5 = 60 % < 80 % → **paused**; `vik next` → 409 `annotator_paused`; vik resumes himself → 403; `review?kind=gold_fail` lists the misses | audited `label_annotator_paused` |
| 3 | `quality` (reviewer only; vik → 403): Cohen κ vik/wen **0.317** (20 shared), Fleiss **0.309**, gold Wilson bounds, per-annotator pace; 11 disputed | |
| 3b | wen (not reviewer) adjudicates → 403; wen made reviewer and adjudicates a row she voted on → 409 `four_eyes`; ben adjudicates 11 → disputed 0; LB-A `resolve` → 11 `adjudicated` | |
| 4 | LLM policy off (default) → 409 `llm_labeling_disabled`; destination `anthropic` → 409 `destination_not_allowed`; enabled with `fake` only | |
| 4b | prompt context `[ticket_id, subject, body, channel, customer_tier]` → sent `[subject, channel, customer_tier]`, excluded `ticket_id (pii:pii_direct)` and `body (free_text)`; preview message shows no `ticket_id` / body; **`pii_values_found: 0`** (every payload is checked for the raw values of the excluded columns) | unreviewed columns → 409 `pii_unreviewed` |
| 4c | calibration (1 441 calibration-pool rows, disjoint from few-shot and qa) on that prompt: accuracy 0.58, **not passed** → every label is `prelabel_only` (auto_accepted 0) | honest: subject alone is weak |
| 4d | budget: est > `max_usd` → 409 `over_budget`; `stop_at_cap` run → `stopped_budget`, spent ≤ max (the request that would pass the cap is not sent) | team monthly cap reserved |
| 4e | unmasked `body`: ben requests, ben decides → 409 four eyes; ana (owner) approves → prompt v7 sends `body` as free text (scanned + redacted); calibration **passed**: τ* = 0.80 picked on half A, certified on half B: coverage 0.85, acc 1.0, Wilson lo **0.994** ≥ 0.95 | bound to (prompt sha, k, mode) |
| 4f | label run 300 rows → 256 `auto_accept`, 44 pre-label only, $0.343; rerun → 295/300 cache hits (the 5 misses were rows outside the first run's set) | cache key = HMAC(layer salt) |
| 4g | fill `llm_low_conf` → wen sees pre-labels, confirms 9 (`kind=confirm`, `refers_to` the LLM event) → resolve `applied/single` 9 | |
| 5 | cole proposes `channel` corrections on 3 cells → cole decides → 409; ben approves → `edit` events → LB-A snapshot → **materialise → ds_f1fbcd44**; `GET /datasets/diff` changed 2 (one cell already had the value: `absorbed`) | |
| 6 | F14 sample (300 rows × 3) on run `lbb-f14`: `migrate?dry_run` → plan 900 events (899 simulated); migrate → 900 imported, actor `legacy:<name>`; rerun → 0 new (900 `already`); `GET /runs/{r}/annotations` before == after (byte-equal JSON); `POST …/annotations/answer` → old answer + 1 label event (adapter) | |
| 7 | rule `subject = "Charged after cancel" → Billing` → 402 rows, `rule` events; analysis: coverage 402, conflicts 0, gold accuracy 0.978 | |

Tracebacks in the API log: **0** on the fresh replay of chains 1–7 (new data dir, new F14 run) and **0** on the SQL chain 8 below. Two bugs found and fixed on the way: a
`Database()` built per call re-ran the migrations, and a write transaction stayed open across the mask code's own
connection, which caused `database is locked`.

**User decisions (2026-11-22, via the orchestrator; they override labeling.md §5.10 and review M10):**
- **Any data may go to the LLM.** There is no PII / free-text exclusion and no owner approval. The default destination
  is `llm`, the real route with llm.py's SDK settings and no server-side fallback; `fake` is kept for tests. The admin
  allowlist `ENSEMBLE_LABEL_LLM_DESTINATIONS` defaults to `llm,fake`. Each run and its audit record the columns sent,
  the row count, the query sha256 and the prompt version, never values. Budget, cache and the injection defence (values
  are JSON-quoted inside `<row>`, "data, not instructions", confidence cap) are unchanged. The old guard can be
  switched back on with `llm_labeling.pii_guard: true`. Team policy `llm_labeling.enabled` is still the on-switch
  (default off).
- **The input is a DuckDB SQL query** (`labels/llm_sql.py`).
  - The query runs on a private in-memory connection. Every table of the pinned version is a view by name, `__row_id`
    included.
  - Guard: F-X4b's `sqlsource.core.check_query` (one SELECT/WITH; no write / ATTACH / COPY / INSTALL / SET / PRAGMA;
    no read_* / glob / file functions). The tables named must be the version's views; 20 s timeout (interrupt); row
    caps of 20 / 200 / 100 000.
  - The result needs a unique `__row_id` and every id must be in the layer's table. Templates name query columns
    (`{income}`); unknown names are refused at save.
  - Saved as a prompt version (query + template + `query_sha256`). A job re-runs exactly that query on the pinned
    version, ordered by `__row_id`. Each event records `prompt_id`, `prompt_version` and `query_sha256`.

| 8 | SQL: join + aggregate (`n_same_subject`) preview 200; `read_csv('/etc/passwd')` → 400 `sql_function`; unknown table → 400 `sql_table`; non-SELECT / ATTACH → 400; no `__row_id` → 400 `no_row_id`; `llm/preview` with `call_llm` → rendered prompts (values JSON-quoted in `<row>`) + fake answers; template `{nope}` → 400 `template_unknown_columns` at save; saved prompt → run of 50 rows → events carry prompt_id, prompt_version 1, query_sha256 | ok |

**Review findings applied (labeling-review.md):**
- C3: guests are authorised by task membership. Annotate and review routes are `public()` for the team-role gate and
  check membership in every mode.
- M8: few-shot and calibration pools exclude every finished run's holdout rows.
- M9: pools are disjoint per layer; calibration is split-half and bound to (prompt, k, mode).
- M10: superseded by the user decision above. What remains: the estimate is local (no vendor call), the cache key is
  HMAC, and errors carry codes only.
- M12: annotators write only via `answers` on their own lease; quality is reviewers and managers only.
- Injection heuristic: confidence is capped at 0.5 and never auto-accepted.

**Not built / notes for the next sessions:**
- From the orchestrator's review list, still open:
  - per-class Wilson lower bounds (per-class recall is reported);
  - the post-run audit sample of auto-accepted rows;
  - spend reserved in `label_llm_spend` under the team row: today it is a process lock plus reservations of running
    runs;
  - `label_llm_batches`: batch mode is refused.
- `label_calibrations` is created by LB-B (CREATE IF NOT EXISTS) because LB-A's schema had no such table at da3293f.
- Batch mode → 400 `batch_not_built`: it needs the M11 batch ledger.
- The spend reservation is a process lock, not a team-row lock.
- A guest's 403 on `GET /datasets/{id}/rows` only bites in enforce mode, which was not run.
- `active` / `cleanlab` fills read the F14 `probs.npz` of the run, which must be computed first.
- Materialising a proposal applies the snapshot's whole column set: LB-A's `materialise` has no row filter yet.
- LLM events from `fake` carry `simulated: true`, so snapshots need `exclude_simulated: false` to apply them.
- `accept.llm.min_confidence = τ*` is the snapshot-side gate (`POST /label-llm-runs/{r}/accept` returns the policy).
- Postgres was not run, but the SQL is portable: `?` params through the DB layer, no SQLite-only functions, and a
  partial unique index.

## Routes for LB-C (all under `/api`)

**Tasks:**
- `POST/GET /label-layers/{l}/tasks`; the body may carry `schema: {spec, guidelines}`.
- `GET/PATCH /label-tasks/{t}`: GET returns `members`, `progress`, `classes`.
- `POST /label-tasks/{t}/members {principal|name, role}`, `DELETE …/members/{p}?role=`, `POST …/members/{p}/resume`.
- `POST/GET /label-tasks/{t}/gold`. POST takes `{pool, column?, items:[{row_id, value}]}` or `{pool, from_version, n, seed}`;
  GET returns `{pools, gold}`.
- `POST /label-tasks/{t}/fill {strategy: random|manual|gold|disputed|llm_low_conf|active|cleanlab, n, version_id, run_id?, row_ids?, below?}`.
- `POST /label-tasks/{t}/next?n=` → `{items:[{assignment_id, row_id, column, context, prelabel}]}`.
- `POST /label-tasks/{t}/answers {items:[{assignment_id, value | skip | flag, confirm?, note?, ms}]}` →
  `{accepted, paused, paused_reason, gold:{n, correct}}`.
- `GET /label-tasks/{t}/review?kind=disputed|gold_fail|flagged`.
- `POST /label-tasks/{t}/adjudicate`: `{row_id, column, value, note}` or `{items:[…]}`.
- `GET /label-tasks/{t}/quality` → `annotators[]` (gold_accuracy, gold_ci, per_hour, kappa, paused), `pairwise`,
  `fleiss_kappa`, `anchoring`.
- `GET /me/label-tasks`.

**LLM:**
- `POST /label-layers/{l}/sql/preview {version_id, query, limit ≤ 200}`: the query editor's run (read-only, capped).
- `POST /label-layers/{l}/llm/preview {version_id, query, prompt_template, limit ≤ 20, call_llm?, destination?, column?}`
  → `{columns, rows, prompts, answers?}`.
- To save, `POST /label-layers/{l}/prompts` also takes `{version_id, query, prompt_template}`.
- `POST/GET /label-layers/{l}/prompts {name, column?, model, template, context, send_masked?, few_shot:{per_class, seed}, unmasked_request_id?, task_id?}`.
- `GET /label-prompts/{p}`.
- `POST /label-prompts/{p}/preview {version_id, n≤20}`.
- `POST /label-prompts/{p}/calibrate {version_id, task_id?, max_usd}` (202).
- `POST /label-prompts/{p}/runs {version_id, selector:{strategy: unlabelled|any, n, seed} | {row_ids}, k, max_usd, stop_at_cap?, task_id?}` (202).
- `GET /label-layers/{l}/llm-policy`, `GET /label-layers/{l}/llm-runs`, `GET /label-llm-runs/{r}`.
- `POST /label-llm-runs/{r}/cancel`, `…/accept`, `…/send-to-task {task_id, tau?}`.
- `POST /label-layers/{l}/llm-unmasked-requests {columns, reason, days}`.
- `POST /label-llm-unmasked-requests/{id}/decide {approve, comment}`.

**Rules:** `POST /label-layers/{l}/rules {name, column, value, filter}`, `POST /label-rules/{r}/run {version_id}`,
`GET /label-layers/{l}/rules/analysis`.

**Proposals:** `POST /label-layers/{l}/proposals {version_id, cells:[{row_id, column, value}], reason}`,
`GET …/proposals`, `POST /label-proposals/{id}/decide {decision: approve|reject | approve: bool, comment}`.

**Migration:** `POST /admin/labels/migrate?run_id=&dry_run=`.
