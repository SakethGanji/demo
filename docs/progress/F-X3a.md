# F-X3a: X3's delta and upload findings (M1–M4, upload/diff minors)

**Plan:** `docs/plans/data-platform-final.md` §5.6 (deltas), §11 ("ambiguous dates as text", acknowledgements), X3's
findings (`docs/progress/X3.md`, `X3/M-findings.md`, `X3/A-findings.md` A-m3).

**Base:** `motion-components` @ `4839e75` (X3 done). F-X3b, X4 and U3 ran in parallel.

## Result

| # | X3 finding | Before (base `4839e75`) | After |
|---|---|---|---|
| **M1** | A delta keyed on a wider key invalidates the declared key and drops the n:1 relation silently | check 200 `warnings []`, commit 201, relation gone, next delta defaults to `[cust_id, region]` | check warns **`key_invalidated`** and **`relation_dropped`** (`needs_ack: true`, counts, why); commit → **400 `acknowledge_required`** naming the missing codes until `acknowledge: ["key_invalidated", "relation_dropped"]`; the manifest op says `acknowledged: true`; the next delta without a key → **400 `delta_key_required`** naming the invalid primary key (`invalid_key`) |
| **M2** | Delta / declared-type values cast with rounding; a real change counted as no-op | `1.5` → `2`; `104.4` vs 104 → `updated_unchanged`, carry, 200 | **block `lossy_cast`** with the count and examples per column, at the text level (CSV/Excel into BIGINT, or a date-time into DATE) and at the type level (a Parquet DOUBLE into DECIMAL(10,2), INTEGER, …). Nothing is applied. `104.0` (lossless) still carries |
| **M3** | Ambiguous dates in a delta cast inconsistently | `02/03/2024` → 03-02 or 02-03 depending on the other rows | kept as text with **`date_ambiguous`** (as a plain upload, §11), so the delta **blocks `date_ambiguous`** and asks for `tables.<t>.formats`; with `formats` it is read in that order, the same in every file |
| **M4** | One-row delta refused | 400 `too_few_rows` | accepted (update, delete-by-key, append). The two-row floor applies only to a **new dataset**; a zero-row file is still 400 |
| m1 | `a,A` headers → 500 | 500 `struct_pack` | 400 `duplicate_header` (case-insensitive; CSV, sheets, Parquet) |
| m2 | plan `{table: X}`, X not in the parent → 500 | 500 `FrameError` | 400 `table_not_in_parent` (lists the tables; says how to add X as new) |
| m3 | diff against a bad id → 500 | 500 | 404 `version_not_found` |
| m4 | non-string `by` → 500 | 500 | 400 (`bad_by` on the key / commit / delta routes; `_who` refuses a non-text `by` everywhere it is used) |
| m6 (A-m3) | Postgres: numeric `version_id` in `POST …/joins` / `…/validate`; `%00` in a dataset path | 500 (`text = smallint`; NUL) | 400 `bad_id` on both engines |
| m7 / m14 | Excel: silent empties, mangled sheet names, unknown plan keys | no warning; `Données €` → `donn_es`; `收入`, `支出` → both `data` (400); `PATCH {sheets}` ignored | warnings **`excel_formula_no_value`** (count) and **`excel_merged_cells`** (ranges, hidden cells) per sheet; accents folded (`donnees`), a name with no A–Z / 0–9 → `sheet_<n>`, `sheet_renamed` warning; `PATCH` with an unknown field → 400 `bad_plan` |
| m5 (M lane) / m12 | a data column `_op` consumed as operations | rows deleted, no warning | block `delta_op_column_ambiguous` unless `tables.<t>.op_column` is given |
| m6 (M lane) / m13 | key text traps (`3` vs `00003`) | new rows, no warning | warning `delta_key_near_match` (count, examples) |

The fuzz script `X3/M/fz.py` (522 requests): **11 5xx → 0** on SQLite, **23 5xx → 0** on Postgres 16.

## What was changed

| File | Change |
|---|---|
| `lake/upload.py` | **M4:** `_check_file` / `_check_parquet` take `min_rows` (2 for a new dataset, 1 with a parent). **M2:** an 8th per-column aggregate in the typing scan counts lossy casts (`_lossy_pred`: a decimal into BIGINT, a date-time into DATE); a declared or parent type with any → block `lossy_cast`; for the type level, `Delta.lossy()` on a delta and `lossy_columns` on an append's typed file. **M3:** a parent-typed (soft) DATE column with slash dates and no day above 12 and no plan format → VARCHAR + `date_ambiguous`; `_delta_check` / the append check turn that into a `date_ambiguous` block. **M1:** `_parent_tables` also returns every key (kind, validity) and the relations; `_integrity_warnings` re-checks the parent's valid keys and its relations on the tables as the upload leaves them (multi-table check, and `_next_integrity` on the one-file P06 path); `_need_integrity_acks` → 400 `acknowledge_required` at commit; `_guard_keys` re-checks the written tables; a delta's plan `key` is no longer declared as a primary key by the generic P12 key loop; `_write_delta` saves the applied key as `primary` only when the table has no primary key (else `unique`); `_delta_check` defaults to a valid primary key and refuses (400 `delta_key_required`) when the primary key is invalid. **Minors:** case-insensitive duplicate headers; `_Parent` 400 `table_not_in_parent`; `PLAN_KEYS` (unknown → 400); `delta_op_column_ambiguous`; Excel notes into `warnings` and `sheets[].notes` / `renamed`. |
| `dataset_versions.py` | `Delta.lossy()`, `lossy_columns()` (a cast that changes or cannot hold a value, per column, with examples); `Delta._near_keys()` → `delta_key_near_match`. |
| `lake/writer.py` | `relations_acknowledged`: when an upload sets it, `_carry_relations` refuses (400 `acknowledge_required`) to drop a relation that was not acknowledged, and marks an acknowledged drop in the op log. Other writers are unchanged. |
| `lake/excel.py` | `sheet_notes()` (one streaming pass per sheet part: merged ranges by regex; the XML is parsed only when a formula is present), `note_warnings()`, `sheet_table_name()` (NFKD fold; `sheet_<n>` fallback). |
| `catalog/datasets_api.py` | diff: `_known_versions` → 404 before any read; `_body_by` (non-text `by` → 400) on commit, key, relation routes. |
| `catalog/api.py` | `_who` refuses a non-text `by` (one line, shared). |
| `authz/deps.py`, `main.py` | NUL in a path → 400 `bad_id` (the `acting_as` middleware covers routes without an authz dependency: manifest, verify, `GET /uploads/{u}`); a non-text or NUL id from the body/query → 400 before the scope lookup. |
| `catalog/joins_api.py` (**F-X3b's file**, minimal) | `_vid()`: a non-text `version_id` → 400 `bad_id` (two call sites). |

## Verification

**Stack:** a scratch venv from `requirements.lock` (Python 3.13, duckdb 2.0.1, pyarrow 24.0.0); Postgres 16 `initdb`
into `/var/tmp/fx3apg` (port 5446, removed afterwards). Four APIs: base (`git worktree` at `4839e75`) and fix, each on
SQLite and on Postgres (`ENSEMBLE_DB_URL`), fresh data dirs.

**1. Reproduced at the base, with X3's own scripts** (`X3/M/*.py`, run unchanged against each API; outputs in
`F-X3a/x3_base_sqlite/`, `x3_base_postgres/`, and the fix in `x3_fix_sqlite/`, `x3_fix_postgres/`):

```
m2.out  (M1) base: check 200, warnings [], commit 201, relations []
             fix:  warnings [key_invalidated, relation_dropped], commit 400 acknowledge_required
d3.out  (M2) base: float 104.4 → mode carry, commit 200 (swallowed)       fix: block lossy_cast amt n 2 ['104.4', '105.4']
d2.out  (M2) base: float 1.5, 2.7 → commit 201 (stored 2, 3)             fix: block lossy_cast amt n 2 ['1.5', '2.7']
d4.out  (M2) base: declared BIGINT 0.5… → check blocks []                fix: block lossy_cast n 5
d6.out  (M3) base: 02/03+02/04 → 2024-03-02; 02/03+02/13 → 2024-02-03   fix: first → block date_ambiguous; second (settled) → 2024-02-03
d5.out  (M3) base: 03/02/2024 → DATE silently                            fix: block date_ambiguous
d1.out  (M4) base: every one-row delta 400 too_few_rows                  fix: update 201, near-key warning, no-op 200 (same version), …
d5.out  (m12) base: _op data column → 2 rows deleted                     fix: block delta_op_column_ambiguous
c1.out  (m1) base: a,A → 500                                             fix: 400 duplicate_header
c2.out  (m2) base: {table: branches} → 500                               fix: 400 table_not_in_parent
x1/x2   (m14) base: no warnings, donn_es, CJK → 400 duplicate_table      fix: excel_formula_no_value 10, excel_merged_cells, donnees, sheet_1/sheet_2
fz.out  base: 11 5xx (SQLite), 23 5xx (Postgres)                         fix: 0 5xx on both
```

The fixed outputs of these scripts are identical on SQLite and Postgres once ids are normalised.

**2. The paths X3's scripts do not reach** (`F-X3a/fx3a.py`; `fx3a_sqlite.txt` = `fx3a_postgres.txt` after id
normalisation):

```
## M1: a delta keyed on a wider key
check 200 blocks []
  warning key_invalidated customers ['cust_id'] needs_ack True | not_unique
  warning relation_dropped customers app_cust needs_ack True | relation_to_not_unique
commit, no acknowledge → 400 acknowledge_required ['key_invalidated', 'relation_dropped']
commit, key_invalidated only → 400 acknowledge_required ['relation_dropped']
commit, both → 201
  keys [('customers', [(['cust_id'], 'primary', False), (['cust_id', 'region'], 'unique', True)])]
  relations []
  manifest ops [{'acknowledged': True, 'name': 'app_cust', 'op': 'relation_dropped', 'why': 'relation_to_not_unique'}]
next delta, no key → 400 delta_key_required ['cust_id']
next delta, key named → 200 [['cust_id', 'region']] []
harmless wider-key delta: check 200 []   commit 201
  keys [('customers', [(['cust_id'], 'primary', True), (['cust_id', 'region'], 'unique', True)])]
  next delta, no key → key [['cust_id']] from ['parent']
one-file rewrite with a repeated cust_id: check 200 [('key_invalidated', True), ('relation_dropped', True)]
  commit no ack → 400 acknowledge_required   commit with ack → 201
## M2: lossy casts
delta 104.4 into BIGINT → blocks [('lossy_cast', 'amt', 2, ['104.4', '105.4'])]
delta 104.0 (lossless) → 200 [] ['carry']
plain upload, declared BIGINT, 0.5 … → blocks [('lossy_cast', 3, ['0.5', '1.5', '2.5'])]
plain upload, declared DATE, a time of day → blocks [('lossy_cast', 1, ['2024-01-02 10:30'])]
parquet delta 1.234 into DECIMAL(10,2) → 200 [('lossy_cast', [{'column': 'price', 'from': 'DOUBLE', 'to': 'DECIMAL(10,2)', 'n': 1, 'examples': [1.234]}])]
## M3: ambiguous dates with a format
formats None → 200 ['date_ambiguous'] ['date_ambiguous']
formats %m/%d/%Y → commit 201 [(1, '2024-02-03'), (2, '2024-02-04')]
formats %d/%m/%Y → commit 201 [(1, '2024-03-02'), (2, '2024-04-02')]
## M4: one-row files
one-row delta (update) → 200 [1]   commit 201
one-row delta (delete, key only) → 200 [1]
one-row append → 200 []
zero-row delta → 400 too_few_rows
one-row NEW dataset → 400 too_few_rows
## minors
case-only duplicate headers → 400 duplicate_header
plan {table: X}, X not in the parent → 400 table_not_in_parent
diff → nope / ds_00000000 / 300 chars → 404 version_not_found
key, by: 7 → 400 bad_by
PATCH {tabels} → 400 bad_plan
```

**3. Postgres m6** (`F-X3a/pg_m6.py`; `pg_m6_base_postgres.txt` vs `pg_m6_postgres.txt`):

```
POST joins version_id 5 / {'a': 1}      base 500 → fix 400 bad_id
validate version_id 5 / 1.5             base 500 → fix 400 bad_id      ('ds_nope' → 404 on both)
GET /datasets/ds_%00, /datasets/V%00    base 500 → fix 400 bad_id
GET /datasets/V/diff/ds_%00             base 500 → fix 400 bad_id
GET /datasets/ds_%00/manifest, /verify, /uploads/ds_%00 (no authz dependency)   fix 400 (middleware)
```

**4. Regression: P13's and P12's chains** (`P13/chain.sh`, `P12/chain.sh`, run unchanged on all four APIs; outputs
in `F-X3a/chains/`, `bs`/`fs` = base/fix SQLite, `bp`/`fp` = base/fix Postgres):
- **P13:** base and fix are identical on SQLite and on Postgres, and the fix is identical across the two engines (ids
  normalised): the same counts, table shas, diff counts, holdout identity, append part, refusals and verify.
- **P12:** identical except the intended text of `header_only.xlsx`'s 400 (`too_few_rows` now says "a new dataset needs at
  least two"). Data shas, keys, relations, carried tables and the runs' table shas are the same.
  - The manifest sha of v1 and the split ids differ between *any* two runs (P12's own report has a third value).
  - The payments run's `score_test` varies run to run at base too (0.596 / 0.601 / 0.602): AutoGluon noise.
- **Tests:** `tests/test_upload.py` has the same 4 failures before and after (P05's CSV-store assertions, noted in
  P12/P13). None are new.
- **Logs:** the fixed APIs logged no traceback over all of the above (the base APIs logged 39 and 76: the 500s).

## Deviations and notes for dependants

- **Files outside my list:**
  - `catalog/joins_api.py` is F-X3b's. The change is minimal: `_vid()`, called at two sites.
  - `authz/deps.py`, `catalog/api.py` and `main.py` are shared. Each change is a few lines of input guards.
- **Acknowledgement codes** (`acknowledge` on `POST /uploads/{u}/commit`):
  - `key_invalidated` and `relation_dropped` are acknowledged **by code**. P06's `holdout_identity_loss` token does not
    cover them.
  - A relation lost because the plan drops one of its tables is listed as `relation_dropped` with `needs_ack: false`,
    because the drop is the person's own action.
- **Key kinds after a delta:**
  - The key a delta is applied by is recorded as `primary` only when the table has no primary key. Otherwise it is
    recorded as `unique`.
  - A table whose primary key became invalid (an acknowledged commit) needs the key named in every later delta's plan,
    or a valid key declared (`PUT …/tables/{t}/key`).
  - P06's one-file rewrite still saves its match key as `primary` (§5.3). That is unchanged.
- **Lossy casts are refused, never flagged-and-applied.** The way out is to fix the values or change the table's type
  first (a rewrite).
- **Ambiguous dates:**
  - The rule applies to DATE columns typed by the **parent** (delta, append).
  - On a plain upload, an explicitly declared `types: {d: DATE}` without `formats` still reads with the sniffed order,
    as before (§11's documented "declare it" path).
  - Slash dates in a TIMESTAMP column are not covered: the scan counts slash evidence for DATE and text only.
- **Excel table names:**
  - Accents are folded: `Données` → `donnees` (before: `donn_es`).
  - A workbook imported earlier under the mangled name, then re-uploaded, gets a different table name. The check shows
    `sheet_renamed`, and the plan's `table_exists` / `table_not_in_parent` blocks catch the mismatch.
- **Not fixed** (outside the upload and diff paths, or not asked for):
  - X3 m17: `POST …/edit` ignores `X-Ensemble-Actor`.
  - The edit path and the key/relation PUTs carry relations through the writer without `relations_acknowledged`, so a
    relation an edit invalidates is still only an op-log entry.
  - `shares.py`, `certify.py` and `contracts.py` still call `.strip()` on a non-text `by` (500). That is the G lane's area.
- **U3 / UI:**
  - Show `key_invalidated` and `relation_dropped` (`needs_ack`) in the check, and send them in `acknowledge`.
  - Show the `lossy_cast` (`columns[]` with examples), `date_ambiguous` (ask for `tables.<t>.formats`),
    `delta_op_column_ambiguous`, `delta_key_near_match`, `excel_*` and `sheet_renamed` messages.
  - `PATCH /uploads/{u}` now refuses unknown fields: the UI sends only plan fields today.
