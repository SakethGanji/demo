"""A 1,048,575-row sheet (Excel's maximum data rows) in a write-only workbook."""
import sys, datetime as dt
from openpyxl import Workbook
wb = Workbook(write_only=True); ws = wb.create_sheet("big")
ws.append(["id", "acct", "amount", "opened", "target"])
d0 = dt.date(2020, 1, 1)
for i in range(1_048_575):
    ws.append([i, f"{i:07d}", (i % 1000) / 7, d0 + dt.timedelta(days=i % 1500), i % 2])
wb.save(sys.argv[1])
