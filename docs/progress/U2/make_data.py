"""U2 walk files, from the credit-book generator (P12):
- book.xlsx: sheets Applications (3,000 rows, with applicant_name and ssn so PII tags are suggested) and Payments;
- bureau.csv: the bureau table (customer_id is its key);
- bureau_delta.csv: a change set for bureau with an _op column (U / D / I): 20 updates, 5 deletes, 10 inserts;
- batch/{applications,bureau,payments}.csv: applications 3000–3499 without the label, their bureau rows and payments.
Usage: make_data.py <out dir>"""
import datetime as dt
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.samples_p12 import generate_credit_book  # noqa: E402

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
t, _ = generate_credit_book(n_apps=3500, seed=7)
apps, bureau, pay = t["applications"].copy(), t["bureau"].copy(), t["payments"].copy()
first = ["Ada", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hana", "Ivo", "June"]
last = ["Moss", "Reyes", "Okafor", "Lind", "Sato", "Varga", "Nkosi", "Berg"]
apps.insert(1, "applicant_name", [f"{first[i % 10]} {last[(i // 10) % 8]}" for i in range(len(apps))])
apps.insert(2, "ssn", [f"{100 + i % 800:03d}-{10 + i % 89:02d}-{1000 + i:04d}" for i in range(len(apps))])
train, later = apps.iloc[:3000], apps.iloc[3000:]

wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Applications"
ws.append(list(train.columns))
for r in train.itertuples(index=False):
    row = [x.item() if hasattr(x, "item") else x for x in r]
    row[list(train.columns).index("applied_on")] = dt.date.fromisoformat(row[list(train.columns).index("applied_on")])
    ws.append(row)
ws = wb.create_sheet("Payments")
tp = pay[pay["app_id"].isin(train["app_id"])]
ws.append(list(tp.columns))
for r in tp.itertuples(index=False):
    ws.append([x.item() if hasattr(x, "item") else x for x in r])
wb.save(out / "book.xlsx")
bureau.to_csv(out / "bureau.csv", index=False)

# the delta: 20 updates (score +15), 5 deletes, 10 new customers
upd = bureau.iloc[10:30].copy(); upd["score"] = upd["score"] + 15; upd.insert(0, "_op", "U")
dele = bureau.iloc[40:45].copy(); dele.insert(0, "_op", "D")
new = bureau.iloc[:10].copy(); new["customer_id"] = [f"C9{i:05d}" for i in range(10)]; new.insert(0, "_op", "I")
import pandas as pd  # noqa: E402
pd.concat([upd, dele, new]).to_csv(out / "bureau_delta.csv", index=False)

b = out / "batch"; b.mkdir(exist_ok=True)
la = later.drop(columns=["defaulted"])
la.to_csv(b / "applications.csv", index=False)
bureau[bureau["customer_id"].isin(la["customer_id"])].to_csv(b / "bureau.csv", index=False)
pay[pay["app_id"].isin(la["app_id"])].to_csv(b / "payments.csv", index=False)
print({"applications": len(train), "payments": len(tp), "bureau": len(bureau), "batch": len(la)})
