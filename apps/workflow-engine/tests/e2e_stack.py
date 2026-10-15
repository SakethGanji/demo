"""Create / drop the throwaway database of the studio's Playwright stack (UI-DESIGN §12.1 step 1).

    python tests/e2e_stack.py up            # TEST_DB_NAME=workflows_test_e2e_<8hex>; creates + migrates; prints one JSON line
    python tests/e2e_stack.py down --db NAME

Refuses any name that is not ``workflows_test_e2e_<8 hex>`` (so never the live ``workflows`` DB)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAME_RE = re.compile(r"^workflows_test_e2e_[0-9a-f]{8}$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["up", "down"])
    ap.add_argument("--db")
    args = ap.parse_args()
    name = args.db or os.environ.get("TEST_DB_NAME", "")
    if not NAME_RE.match(name):
        print(f"refusing database name {name!r}", file=sys.stderr)
        return 2
    os.environ["TEST_DB_NAME"] = name
    from tests.integration import conftest as c   # TestDatabase + connection settings (env-overridable)

    db = c.TestDatabase()
    if args.action == "up":
        db.create()
        db.migrate("apply")
        print(json.dumps({"db_name": db.name, "db_host": c.DB_HOST, "db_port": c.DB_PORT,
                          "db_user": c.DB_USER, "db_password": c.DB_PASSWORD}))
    else:
        db.drop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
