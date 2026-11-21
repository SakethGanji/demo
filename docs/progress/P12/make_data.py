"""P12 test files: book.xlsx (sheets applications, payments, products; a hidden notes sheet; an empty sheet) and
bureau.csv, from the credit-book sample generator; payments_more.xlsx (one sheet, new payment rows for an append);
and the bad files (not a workbook, all sheets empty, a header-only sheet, two sheets that become the same table)."""
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.samples_p12 import generate_credit_book  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
t, _ = generate_credit_book(n_apps=3000, seed=7)
apps, bureau, pay = t["applications"], t["bureau"], t["payments"]
apps = apps.assign(branch=[f"{i % 120:04d}" for i in range(len(apps))])  # codes with leading zeros, stored as text
products = [("personal", "Personal loan", 0.089), ("auto", "Auto loan", 0.064), ("card", "Credit card", 0.219),
            ("overdraft", "Overdraft", 0.149)]
cut = len(pay) - 500  # the last 500 payments arrive later (the append)
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Applications"
ws.append([None])  # an empty first row: the header is the first row with a value
ws.append(list(apps.columns))
import datetime as dt  # noqa: E402
for r in apps.itertuples(index=False):
    row = list(r)
    row[6] = dt.date.fromisoformat(row[6])  # applied_on as a real Excel date
    row = [x.item() if hasattr(x, "item") else x for x in row]
    ws.append(row)
ws = wb.create_sheet("Payments")
ws.append(list(pay.columns))
for r in pay.iloc[:cut].itertuples(index=False):
    ws.append([x.item() if hasattr(x, "item") else x for x in r])
ws = wb.create_sheet("Products")
ws.append(["product", "name", "apr"])
for p in products:
    ws.append(list(p))
ws = wb.create_sheet("Notes")
ws.sheet_state = "hidden"
ws.append(["note"]); ws.append(["prepared by risk"]); ws.append(["do not share"])
wb.create_sheet("Empty")
wb.save(out / "book.xlsx")
bureau.to_csv(out / "bureau.csv", index=False)
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "payments"
ws.append(list(pay.columns))
for r in pay.iloc[cut:].itertuples(index=False):
    ws.append([x.item() if hasattr(x, "item") else x for x in r])
wb.save(out / "payments_more.xlsx")
pay.iloc[cut:].to_csv(out / "payments.csv", index=False)
# bad files
(out / "bad.xlsx").write_bytes(b"PK\x03\x04 this is not really a zip archive" + bytes(200))
(out / "fake.xlsx").write_text("a,b\n1,2\n")
wb = openpyxl.Workbook(); wb.active.title = "Sheet1"; wb.create_sheet("Sheet2"); wb.save(out / "empty.xlsx")
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "only_header"; ws.append(["a", "b", "c"]); wb.save(out / "header_only.xlsx")
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Pay ments"; ws.append(["a", "b"]); ws.append([1, 2]); ws.append([3, 4])
ws = wb.create_sheet("pay-ments"); ws.append(["a", "b"]); ws.append([1, 2]); ws.append([3, 4]); wb.save(out / "dup_sheets.xlsx")
# one CSV (the single-table paths, unchanged) and its next file: every 100th income corrected
import random  # noqa: E402
random.seed(3)
rows = [f"A{i:05d},{random.randint(0, 99999):05d},2024-0{1 + i % 9}-1{i % 9},{random.randint(20000, 90000)},{int(random.random() < 0.2)}"
        for i in range(1000)]
(out / "loans.csv").write_text("acct,zip,opened,income,y\n" + "\n".join(rows) + "\n")
rows2 = [r if i % 100 else ",".join(r.split(",")[:3] + ["99999", r.split(",")[4]]) for i, r in enumerate(rows)]
(out / "loans_v2.csv").write_text("acct,zip,opened,income,y\n" + "\n".join(rows2) + "\n")
print({p.name: p.stat().st_size for p in sorted(out.iterdir())}, "apps", len(apps), "bureau", len(bureau), "payments", cut, "+", len(pay) - cut)
