"""F-X5b K3: the audit writer's own sanitiser, past the input guard. Writes one audit row whose details, comment and
actor hold a NUL and a lone surrogate (as a value from a file or a multipart field could), straight through
catalog.schema.audit on the API's database; then reads it back raw and checks the chain.
    ENSEMBLE_DB_URL=… ENSEMBLE_DATA_DIR=… python k3_writer.py"""
import json
from app import config
from app.db import Database
from app.catalog import schema as S

db = Database(config.load_settings().db_path)
with db.connect() as con:
    i = S.audit(con, "Ada\x00", "fx5b_k3_probe", {"dataset_id": "credit-default", "root_id": "credit-default",
                                                 "name": "a\x00b", "k\x00": ["\udc00", "😀"]}, comment="c\x00")
    r = con.execute("SELECT actor, comment, details FROM audit_log WHERE id = ?", (i,)).fetchone()
print("row", i, "actor", repr(r[0]), "comment", repr(r[1]))
print("details as stored", r[2])
print("has NUL / NUL escape:", "\x00" in r[2] or "\\u0000" in r[2])
