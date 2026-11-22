"""P15 batch files: applications rows 3000–3499 of the credit-book generator (no label) with their bureau rows and
payments (the batch frame must equal those training-frame rows), plus a bureau file with one column dropped.
Usage: make_batch.py <out dir>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.samples_p12 import generate_credit_book  # noqa: E402

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
t, _ = generate_credit_book()
apps = t["applications"].iloc[3000:3500].drop(columns=["defaulted"])
bureau = t["bureau"][t["bureau"]["customer_id"].isin(apps["customer_id"])]
pay = t["payments"][t["payments"]["app_id"].isin(apps["app_id"])]
apps.to_csv(out / "applications.csv", index=False)
bureau.to_csv(out / "bureau.csv", index=False)
pay.to_csv(out / "payments.csv", index=False)
(out / "bad").mkdir(exist_ok=True)
bureau.drop(columns=["n_open_accounts"]).to_csv(out / "bad" / "bureau.csv", index=False)
t["applications"].iloc[3000:3500].drop(columns=["defaulted"]).to_csv(out / "single.csv", index=False)
print({"applications": len(apps), "bureau": len(bureau), "payments": len(pay)}, list(apps.columns), list(bureau.columns), list(pay.columns))
