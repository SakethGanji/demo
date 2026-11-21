"""DLA4 #2's shape: 1 000 accounts with a plain id `acct` "A00000…" (no id-like name), then 15 corrected incomes."""
import csv, random
random.seed(7)
rows = [{"acct": f"A{i:05d}", "income": round(random.uniform(20000, 150000), 2), "age": random.randint(18, 80),
         "segment": random.choice(["retail", "sme", "private"]), "utilisation": round(random.random(), 4),
         "y": int(random.random() < 0.25)} for i in range(1000)]
w = csv.DictWriter(open("v1.csv", "w", newline=""), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
fix = sorted(random.sample(range(1000), 15))
for i in fix:
    rows[i]["income"] = round(rows[i]["income"] * 1.1 + 1, 2)
w = csv.DictWriter(open("v2.csv", "w", newline=""), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
open("fixed.txt", "w").write(",".join(f"A{i:05d}" for i in fix))
