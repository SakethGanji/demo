"""X3/B data: the credit-book sample as three CSVs, a delta on applications (_op I/U/D) and a delta on bureau.
Usage: make_data.py <out dir>"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend"))
from app.samples_p12 import generate_credit_book  # noqa: E402

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
t, _ = generate_credit_book(n_apps=3000, seed=7)
for k, v in t.items():
    v.to_csv(out / f"{k}.csv", index=False)
a = t["applications"]
upd = a.iloc[:50].copy(); upd["income"] = upd["income"] + 1000; upd["_op"] = "U"
dele = a.iloc[50:70].copy(); dele["_op"] = "D"
ins = a.iloc[:30].copy(); ins["app_id"] = [f"A9{i:06d}" for i in range(30)]; ins["_op"] = "I"
import pandas as pd  # noqa: E402
pd.concat([upd, dele, ins]).to_csv(out / "apps_delta.csv", index=False)
b = t["bureau"].iloc[:40].copy(); b["score"] = b["score"] - 5
b.to_csv(out / "bureau_delta.csv", index=False)
print({k: len(v) for k, v in t.items()})
