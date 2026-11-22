"""Excel edge-case workbooks (openpyxl)."""
import datetime as dt, sys
from openpyxl import Workbook
out = sys.argv[1]
wb = Workbook(); ws = wb.active; ws.title = "Données €"
ws.append(["id", "name", "amount", "opened", "formula", "pct"])
for i in range(1, 11):
    ws.append([f"{i:04d}", f"n{i}", 10.5 * i, dt.date(2024, 1, i), f"=C{i+1}*2", 0.1 * i])
ws.merge_cells("B3:B5")  # vertical merge: B4, B5 become empty
for r in range(2, 12):
    ws.cell(r, 4).number_format = "yyyy-mm-dd"
    ws.cell(r, 6).number_format = "0%"
ws2 = wb.create_sheet("表 1 😀")
ws2.append(["Group", None, "x"]); ws2.merge_cells("A1:B1")  # merged header: second header cell empty
ws2.append(["k", "v", "x"])
for i in range(5): ws2.append([f"k{i}", i, i * 2])
ws3 = wb.create_sheet("serials")
ws3.append(["id", "d_serial_num", "d_fmt", "mixed", "t"])
for i in range(1, 8):
    c = ws3.cell(i + 1, 3, 45000 + i); c.number_format = "dd/mm/yyyy"
    ws3.cell(i + 1, 1, i); ws3.cell(i + 1, 2, 45000 + i)
    ws3.cell(i + 1, 4, dt.date(2024, 2, i) if i % 2 else "n/a")
    ws3.cell(i + 1, 5, dt.time(9, i, 0))
ws4 = wb.create_sheet("sheet 1"); ws4.append(["a", "b"]); [ws4.append([i, i]) for i in range(3)]
# (Sheet_1 collides with "sheet 1": 400 duplicate_table, see x1.out first run)
ws6 = wb.create_sheet("blank header"); ws6.append(["a", "", "", "b"]); [ws6.append([i, i * 10, None, i]) for i in range(3)]
wb.save(out)
