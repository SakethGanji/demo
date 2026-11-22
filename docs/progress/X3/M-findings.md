# X3 lane M: deltas, multi-table, Excel, and garbage input on the upload/key/relation routes

**Setup:** head `55c491a`; a scratch venv from `requirements.lock`; SQLite at
`ENSEMBLE_DATA_DIR=<scratch>/M/data ENSEMBLE_ADMINS=Ada uvicorn app.main:app --port 8731`.

**Method:** the scripts are in `M/` and are plain HTTP calls in the UI's order: `x3.py` holds the
upload → PATCH plan → check → commit helper. Each script's raw output sits next to it as `*.out`.

## Major

### M-1: A delta keyed on a wider key silently invalidates the declared key and drops the n:1 relation

**Repro:** `m1.py` then `m2.py`.
- Starting point: apps → customers on `cust_id`, a relation `app_cust` (n:1), and `customers` key `cust_id`.
- A delta on `customers` with plan `key: [cust_id, region]` inserts `C001/N`. `C001/S` already exists.

**Observed:**
- Check: 200, `warnings []`. Commit: 201, mode `append`.
- In the new version:
  - `customers` keys: `[cust_id, region]` valid, and `[cust_id]` **valid: false** (2 duplicates);
  - `GET …/relations` → `relations: []`.
- The only trace is a manifest op: `{op: relation_dropped, name: app_cust, why: relation_to_not_unique}`. The check and the commit answer say nothing.
- The broken version became the default.
- The next delta on `customers` with no plan key picks up the new first key, `[cust_id, region]`, without saying so.

**Why it matters:** the plan says the n:1 side must be a valid key. The relation the join builder suggests is gone, and a
join pinned on the older version is unaffected. A fresh join on this version is the fan-out case in lane A.

**Expected:** a check warning (or a block needing acknowledgement) when a delta invalidates a confirmed key or drops a relation.

### M-2: Delta values are cast to the parent's types and lose precision silently

**Repro:** `d2.py` ("float amt into BIGINT column") and `d3.py`.

**Observed:**
- `amt` is BIGINT. A delta with `1.5`, `2.7` → stored `2`, `3`, with no block and no warning.
- A delta with `104.4`, `105.4` for rows holding 104 and 105 → `updated_unchanged: 2`, mode `carry`. The commit answers 200 with the parent version: the change is swallowed.
- `abc` in the same column is blocked (`declared_type_mismatch`). Only lossy numeric casts slip through.
- A plain upload with a declared `types: {amt: BIGINT}` and values `0.5 … 4.5` passes the check the same way (`d4.out`). This is the upload's TRY_CAST and predates P13. A delta inherits the types without the user declaring anything, which makes it worse.

**Expected:** a fractional value in an integer column is a `declared_type_mismatch` (a lossless cast check).

### M-3: Ambiguous dates in a delta are cast silently, and inconsistently

**Repro:** `d5.py` and `d6.py`. Column `d` is DATE.

**Observed:**
- A delta with `02/03/2024, 02/04/2024` → stored 2024-03-02 and 2024-04-02 (day first).
- A delta with `02/03/2024, 02/13/2024` → stored 2024-02-03 and 2024-02-13 (month first).
- The same text gives two dates depending on the other rows of the file. No warning either way.
- The same values in a plain upload stay text with `date_ambiguous`, as §11 decides ("ambiguous dates as text").

**Expected:** an ambiguous date column in a delta blocks or needs `formats`.

### M-4: A one-row delta is refused

**Repro:** `d1.py`. Any delta file with a single data row answers
`400 too_few_rows: "loans.csv: This file has fewer than two data rows."`.

The cases hit include an update of one account, one delete, and an update of a missing key. Correcting one record is the
most common delta. The only workaround is padding the file, for example with a delete of a key that does not exist
(counted as `missing`, with a warning).

**Expected:** the two-row floor does not apply to `action: delta` (or `append`).

## Minor

### m-1: Headers that differ only in case give a 500 at check

**Repro:** `c1.py`. A CSV with header `a,A,b`, or a sheet with the same header, → `POST /uploads/{id}/check` **500**:
`BinderException: Duplicate named argument "A" in function call to "struct_pack"` (`upload._check_file`, line 565).
Exact duplicates get a clean 400 `duplicate_header`.

### m-2: A single file sent to a multi-table parent with plan `{table: X}` gives a 500 when X is new

**Repro:** `c2.py`. `branches.csv` on a 3-table parent with `PATCH {table: branches}` → check **500**:
`FrameError: … has no table 'branches'` (`_Parent.__init__`, `upload.py:755`).

`{tables: {branches: {action: new}}}` works and adds the table. A plan `table` naming a missing table should be a 400, or
mean `new`.

### m-3: `GET /datasets/{a}/diff/{b}` gives a 500 for an unknown or malformed `b`

**Repro:** `fz.py`. Values of `b` that give a 500:
- `nope`, `ds_00000000`, `%2e%2e`, `ds_' OR 1=1--` and `ds_\0` → `FrameError: Bad dataset id …`;
- a 300-character id → `OSError: File name too long`.

The `a` side is checked by the `require()` dependency. `b` reaches `mask.for_request` → `frames.path_of` unchecked.
Nothing leaks; it is a 500 instead of a 404.

### m-4: A non-string `by` gives a 500

**Repro:** `fz.py`. `PUT …/tables/customers/key {columns: [cust_id], by: 7}` and `POST /uploads/{id}/commit {…, by: 7}` →
**500** `AttributeError: 'int' object has no attribute 'strip'`.

### m-5: A parent data column named `_op` is consumed as the delta's operation column

**Repro:** `d5.py` (A). The table has a data column `_op`. A delta `k,_op,v,target / 1,D,100,1 / 2,D,200,0` → rows 1 and 2
are **deleted**, with no warning that the table already has a column of that name.

`op_column` in the plan avoids it, but nothing points the user there. A plain upload accepts `_op` as a column name.

### m-6: Key text traps are not flagged

**Repro:** `d2.py`.
- `acct` is text with leading zeros. A delta keyed `3, 4` → two **new** accounts next to `00003` and `00004`.
- Keys with stray spaces (`" 00021"`, `"00022 "`) → two new accounts next to `00021` and `00022`.

The counts show it (`inserted 2, updated 0`), but no warning says that inserted keys equal existing ones after trimming or
removing leading zeros. A numeric key with `007` against an int parent matches correctly, because it is cast.

### m-7: Excel imports cells as empty without saying so, and mangles non-ASCII sheet names

**Repro:** `mkxl.py`, `x1.py` (`x1d.out`) and `x2.py`.
- **Silent empties:** formula cells with no cached value, from workbooks saved by scripts, come in empty, and so do the
  non-top-left cells of a merged range. There is no warning for either.
- **Sheet names:** non-ASCII names are mangled (`Données €` → `donn_es`, `表 1 😀` → `t_1`). Two pure-CJK sheet names
  (`收入`, `支出`) both become `data` → 400 `duplicate_table`, and the app offers no rename: the user must edit the workbook.
- **Unknown plan keys:** `PATCH {sheets: …}` (an unknown key) is accepted and ignored. `patch_plan` doesn't refuse unknown
  top-level keys, so a typo such as `tabels` is silently dropped.

### m-8: `POST …/edit` ignores `X-Ensemble-Actor`

**Repro:** `m3.py`'s first run. `POST …/edit {table, reason, ops}` with the header and no body `by` → 400 "'by' is required".
Keys, relations and uploads take the header.

## What held

**Deltas:**
- Update of a missing key → insert with `delta_update_missing`. Insert of an existing key → update with `delta_insert_existing`.
- Delete of a missing key → `missing` and carry (the parent returned, 200).
- Delete and insert of one key in one file, and two ops on one key → 400 `delta_duplicate_keys` listing the key.
- NULL key in the delta → 400 `delta_key_null`. A plan key with a NULL or duplicate in the parent → 400 `key_invalid`.
- `_op` values: lower case and spaces are accepted; an unknown op → 400 `delta_op_unknown`.
- Composite-key delta: update, delete and insert are correct, and the other tables are carried with the same sha.
- A partial-column update keeps the parent's other values. A partial insert gets NULLs, with a warning.
- A true no-op delta → `carry`, and the commit returns the parent (200), deduped.
- An int key with zero-padded delta keys matches. The key column missing from the delta → 400 `delta_key_missing`.

**Carry over many versions (`m3.py`):** checked against an independent Python model after every step:
- 20 insert-only deltas (an append part each) with two edits (set_cells and delete_rows) between them;
- every version: rows equal to the model, unique `__row_id`s, verify ok;
- 21 parts → the next rewrite compacts to 1 part (§5.6), and an edit also rewrites to 1 part.

**Multi-table:**
- Drop a table, then re-add it: new ids from the root counter (20…, not 0…), no stale relation, the others carried.
- A key change on `customers` (`score`) adds a key; the relation on `cust_id` is kept.

**Excel:**
- Serial-number cells with a date format → DATE; plain serials stay BIGINT. Times → text. Percentages → DOUBLE.
- Leading zeros are kept as text.
- A 1,048,575-row sheet: check 7.1 s, commit 1.3 s, API peak RSS 0.9 GB, last rows correct.
- A 10k-op delta on that table (3,334 updates, 1,000 deletes, 3,000 inserts): check 3.6 s, commit 2.7 s, peak 0.88 GB, verify ok.

**Fuzz (`fz.py`):** 522 requests. The 11 5xx are all listed above (m-3, m-4, and m-2 three times). Everything else is
400, 404 or 409. That includes:
- garbage files: a broken xlsx or xls, NUL bytes, an empty file, a 10 MB cell, ragged rows;
- path-traversal and quote ids, and 300-character table names;
- every bad plan shape tried.
