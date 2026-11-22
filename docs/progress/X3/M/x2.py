import sys; sys.path.insert(0, "."); from x3 import *
from openpyxl import Workbook
import io
wb = Workbook(); ws = wb.active; ws.title = "收入"; ws.append(["a", "b"]); [ws.append([i, i]) for i in range(3)]
ws = wb.create_sheet("支出"); ws.append(["a", "b"]); [ws.append([i, i]) for i in range(3)]
b = io.BytesIO(); wb.save(b)
up, err = upload([("cjk.xlsx", b.getvalue())], name="x3 cjk")
s, c = check(up); print("two pure-CJK sheet names: check", s, c.get("detail") if isinstance(c, dict) and "detail" in c else [(x["sheet"], x["table"]) for x in c.get("sheets", [])])
up, err = upload([("cjk.xlsx", b.getvalue())], name="x3 cjk", plan={"sheets": {"收入": {"table": "income"}}})
print("  plan to rename a sheet:", err)
