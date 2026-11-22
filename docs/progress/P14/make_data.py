"""P14 data: bureau.csv, the credit-book sample's bureau table as its own file (a P12-style upload of another dataset,
which a join on credit-book then pins by version). Usage: make_data.py <out dir>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from app.samples_p12 import generate_credit_book  # noqa: E402

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
tables, _ = generate_credit_book()
tables["bureau"].to_csv(out / "bureau.csv", index=False)
print({k: len(v) for k, v in tables.items()})
