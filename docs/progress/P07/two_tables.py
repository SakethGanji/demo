"""P07 verification seed (not a test): the next version of a dataset with a second table, written through the
VersionWriter like P12's multi-table upload will be. `python two_tables.py <parent_version_id>` with ENSEMBLE_DATA_DIR
set to the API's data dir. The `accounts` table is carried (zero bytes); `bureau` (acct, bureau_score) is new."""
import sys

from app import config
from app.datasets import DatasetStore
from app.db import Database
from app.lake.store import open_store
from app.lake.writer import VersionWriter

parent = sys.argv[1]
settings = config.load_settings()
db = Database(settings.db_path)
lake = open_store(settings)
with VersionWriter(db, lake, op="seed", by="Ada", parent_id=parent, reason="P07: a second table (bureau)") as w:
    w.carry("accounts")
    w.write("bureau", "SELECT __row_id, acct, (300 + (hash(acct) % 550))::BIGINT AS bureau_score FROM " + w._rel("accounts"))
    out = w.commit(make_default=False, target="y", note="P07 seed: accounts carried + bureau")
DatasetStore(settings, db).finish_lake_version(out["version_id"], target="y", by="Ada")
print(out["version_id"], [(t["table_name"], t["change"]) for t in out["tables"]])
