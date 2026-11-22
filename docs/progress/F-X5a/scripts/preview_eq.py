"""Edit preview summaries on credit-default for several op lists, printed as JSON (run from a code tree's backend/)."""
import json, os, sys
sys.path.insert(0, os.getcwd())
from app import config
from app.datasets import DatasetStore
from app.db import Database
s = config.load_settings(); st = DatasetStore(s, Database(s.db_path))
cases = {
 "cells": [{"op": "set_cells", "cells": [{"row_id": 3, "column": "income_usd", "value": 1.5}, {"row_id": 9, "column": "region", "value": "zz"}]}],
 "rename_then_set": [{"op": "rename_column", "column": "region", "to": "area"}, {"op": "set_cells", "cells": [{"row_id": 4, "column": "area", "value": "q"}]}],
 "set_then_rename": [{"op": "set_cells", "cells": [{"row_id": 4, "column": "region", "value": "q"}]}, {"op": "rename_column", "column": "region", "to": "area"}],
 "cast": [{"op": "cast_column", "column": "credit_score", "to": "text"}],
 "replace": [{"op": "replace_values", "column": "employment", "find": "salaried", "replace": "employed"}],
 "fill": [{"op": "fill_missing", "column": "months_employed", "strategy": "median"}],
 "rows": [{"op": "delete_rows", "row_ids": [1, 2, 3]}, {"op": "add_rows", "rows": [{"applicant_id": "Z1", "age": 30, "defaulted": "no"}]}],
 "addcol": [{"op": "add_column", "name": "ratio", "formula": "loan_amount_usd / income_usd"}],
}
out = {}
for k, ops in cases.items():
    try:
        r = st.edit_version("credit-default", ops, by="x5", reason="x5", preview=True)
        out[k] = {x: r.get(x) for x in ("matched", "changed", "added", "removed", "columns_before", "columns_after")} | {"warnings": [w.get("code") for w in r["checks"]["warnings"]]}
    except Exception as e:  # noqa
        out[k] = f"{type(e).__name__}: {str(e)[:120]}"
print(json.dumps(out, sort_keys=True))
