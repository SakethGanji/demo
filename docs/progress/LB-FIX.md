# LB-FIX: fixes for LB-X's findings (C1, M1, M2, m2, m3)

**Setup:** SQLite, Python 3.12 venv, `ENSEMBLE_POC_MODE=1`, fake LLM, with the same environment as LB-X. Everything was verified
through the API with LB-X's chain (`LB-X/chain.py`, unchanged) plus a yes/no variant (`LB-FIX/chain_bool.py`).

## Fixes
- **C1. Approval 500.** The submodule `app/labels/materialise.py` is renamed to `materialise_snapshot.py`, so importing it can no
  longer bind over the package function `labels.materialise()`.
  - `decide` is now safe on failure. Any failure after the status flips (append, snapshot or materialise) puts the
    proposal back to `pending`, with `approval failed: …` in its comment. It also retracts the label events that the
    approval appended and writes the audit event `label_proposal_approval_failed`. A `LabelError` returns its 4xx with
    `proposal_status: pending`.
  - The approve/reject UPDATE runs only while the proposal is `pending`, so a concurrent second decide gets 409.
- **M1. Boolean target.**
  - `append_events` now reads the base column's lake type. A value for a BOOLEAN or integer or number column must cast to that
    type: true/false, yes/no, 1/0 for a boolean. Otherwise it gets 400 `value_type`.
  - Free values are normalised to the type. Class values keep their schema spelling, because the class list is the vocabulary
    that gold and votes compare against.
  - Status compares in the cell's type (`values.typed_equal`), and the overlay/materialise `_cast` maps yes/no to True/False.
    The old `_cast` wrote "yes" as False.
- **M2. 500 → 400.** Validation helpers (`as_int`, `as_share`, `as_num`, `as_list`, `as_row_id`) are in `lbb_common`. They are
  applied to:
  - snapshot `policy` and `policy.accept` (must be objects);
  - task create (`columns`, `votes_per_row`, `gold_share`, `blind_share`, `min_gold_accuracy`, `lease_seconds`,
    `min_gold_items`);
  - gold (`items`, `row_id`, `n`);
  - fill (`n`, `row_ids`);
  - `next` `n`;
  - answers and adjudicate (`items` shape, `row_id`, `ms`);
  - sql/preview `limit`;
  - LLM runs, preview and query preview (`k`, `n`, `limit`, `max_usd`, `row_ids`, `version_id`, `days`,
    `few_shot.per_class`);
  - low-confidence `n`;
  - `context_columns` on events.

  A proposal now validates its cells and appends its events before the proposal row is inserted, so a refused proposal
  leaves nothing pending.
- **m2. LLM ctx.** LLM events carry `ctx_columns` = the prompt's input columns that are columns of the labelled table.
  For a SQL input those are the query's output columns. Joined or aggregated query columns have no cell in that table, so they
  cannot be hashed there; they are recorded as `provenance.query_columns` next to `query_sha256`.
  - `rowhash.hashes` now treats an explicit empty context as empty. Before, an empty list fell back to all columns.
  - The `/events` route keeps `[]` meaning "all columns".
- **m3.** An absorbed label whose context changed now reports `stale`. Otherwise the precedence is unchanged:
  base_changed before stale.

## Verify
| Check | Result |
|---|---|
| C1: rev approves → `applied`, new version | PASS (both runs) |
| C1: a failing approval (409 `nothing_to_apply`) → the proposal is `pending` with the error | PASS (`chain_bool.py`) |
| M1: yes/no target inferred BOOLEAN, status on v3 | absorbed 51, base_changed 0 (LB-X: 0 / 13) |
| M2: 46 bad inputs | 0 × 5xx in both runs (LB-X: 9 × 500) |
| m2: LLM `_ctx` | `[age, income, purpose]`; LB-X had `[age, app_id, income, purpose, region]` |
| Everything else | unchanged: string run 67/72 PASS |

The remaining FAIL lines are all chain artefacts:
- 2 × row 8 and "absorbed = rows_changed": LB-X's own test-design errors.
- "after a failed approve … pending" now reads `applied` because the approval succeeds.
- "new version has the proposed values": the chain reads `__row_id`, but `/datasets/{v}/rows` returns `_row_id`.
- In the boolean run, the holdout `y_true` check compares `"False"` with `"no"`.
- In the boolean run, `accept` → 409 `not_calibrated` is the fake LLM's run-to-run variance (LB-X m4).

**Not covered:** Postgres, enforce mode. `tests/test_concurrency.py::test_api_answers_while_shap_runs` fails both with
and without this change, so the failure predates it.
