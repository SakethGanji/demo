#!/usr/bin/env python3
"""Seed the two demo datasets the reference UI is built around.

Idempotent-ish: it always creates NEW datasets, so run it against a fresh
database (see `--help` for the reset recipe) rather than repeatedly.

    # fresh demo database
    docker exec analytics-pg psql -U accelerator -d postgres -q \
        -c "DROP DATABASE IF EXISTS accelerator WITH (FORCE);" \
        -c "CREATE DATABASE accelerator OWNER accelerator;"
    venv/bin/python -m app.infra.db.postgres.migrate apply
    # start the API, then:
    venv/bin/python scripts/seed_demo.py

What it creates:
  * "Regional Orders.csv" — 120 rows; `email` marked confidential so the
    masking behaviour is visible by switching seats in the UI; a not-null
    quality rule on `amount`; a documented `amount` column.
  * "CRM.xlsx" — a two-sheet workbook (Customers + Orders) with a foreign-key
    quality rule, so Relationships/joins have something real to discover.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys

import httpx
from openpyxl import Workbook

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REGIONS = ["EU", "US", "APAC", "LATAM"]
STATUSES = ["paid", "pending", "refunded"]


def _upload(client: httpx.Client, name: str, content: bytes, mime: str) -> dict:
    r = client.post("/upload", files={"file": (name, content, mime)}, params={"sync": "true"})
    r.raise_for_status()
    return r.json()


def seed_orders(client: httpx.Client) -> str:
    rows = ["order_id,region,customer_id,email,amount,status,order_date"]
    rnd = random.Random(7)  # deterministic demo data
    for i in range(1, 121):
        rows.append(
            f"{1000 + i},{REGIONS[i % 4]},{100 + (i % 20)},user{i}@example.com,"
            f"{round(rnd.uniform(5, 900), 2)},{STATUSES[i % 3]},2026-0{1 + i % 6}-{1 + i % 27:02d}"
        )
    body = _upload(client, "Regional Orders.csv", "\n".join(rows).encode(), "text/csv")
    ds = body["dataset_id"]

    # `email` sensitive → the UI masks it for any seat without elevated access.
    client.put(
        f"/datasets/{ds}/sheet-metadata/data/columns/email",
        json={"business_name": "Customer email", "semantic_type": "email",
              "sensitivity": "confidential"},
    ).raise_for_status()
    client.put(
        f"/datasets/{ds}/sheet-metadata/data/columns/amount",
        json={"business_name": "Order amount", "unit": "USD",
              "description": "Gross order value"},
    ).raise_for_status()
    client.post(
        f"/datasets/{ds}/rules",
        json={"name": "amount-present", "rule_type": "not_null",
              "sheet_selector": "data", "column_selector": "amount"},
    ).raise_for_status()
    print(f"  Regional Orders.csv  {ds}  ({body.get('row_count')} rows)")
    return ds


def seed_crm(client: httpx.Client) -> str:
    rnd = random.Random(11)
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.append(["customer_id", "name", "tier", "country"])
    for i in range(1, 41):
        ws.append([i, f"Customer {i}", ["Gold", "Silver", "Bronze"][i % 3], REGIONS[i % 4]])

    orders = wb.create_sheet("Orders")
    orders.append(["order_id", "customer_id", "amount", "status"])
    # customer_id runs past 40 on purpose, so the join forecast has real
    # unmatched rows to report.
    for i in range(1, 151):
        orders.append([5000 + i, 1 + (i % 45), round(rnd.uniform(10, 500), 2), STATUSES[i % 3]])

    buf = io.BytesIO()
    wb.save(buf)
    body = _upload(client, "CRM.xlsx", buf.getvalue(), XLSX_MIME)
    ds = body["dataset_id"]
    client.post(
        f"/datasets/{ds}/rules",
        json={"name": "orders-customer-fk", "rule_type": "foreign_key",
              "sheet_selector": "Orders", "column_selector": "customer_id",
              "parameters": {"ref_sheet": "Customers", "ref_column": "customer_id"}},
    ).raise_for_status()
    print(f"  CRM.xlsx             {ds}  ({body.get('row_count')} rows)")
    return ds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8001/api/v1")
    ap.add_argument("--user", default="00000000-0000-0000-0000-000000000001",
                    help="X-User-Id to seed as (default: the seeded System superuser)")
    args = ap.parse_args()

    with httpx.Client(base_url=args.api, headers={"X-User-Id": args.user}, timeout=120) as client:
        try:
            client.get("/datasets", params={"limit": 1}).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"API not reachable at {args.api}: {exc}", file=sys.stderr)
            return 1
        print("seeding demo datasets:")
        orders = seed_orders(client)
        crm = seed_crm(client)

    print(json.dumps({"orders": orders, "crm": crm}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
