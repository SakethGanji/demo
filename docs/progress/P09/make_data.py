"""P09 verification data (DLA4 #3's shape): 900 accounts (acct "A00000…", a 0/1 target driven by income and segment),
written to accounts.csv in the current directory. 20 % holdout = 180 rows. Deterministic."""
import csv
import random

rng = random.Random(9)
with open("accounts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["acct", "income", "age", "segment", "tenure", "y"])
    for i in range(900):
        income = round(rng.uniform(15000, 150000), 2)
        seg = rng.choice(["retail", "sme", "corp"])
        tenure = rng.randint(0, 30)
        p = 0.7 if income < 40000 else 0.35 if seg == "sme" else 0.12
        w.writerow([f"A{i:05d}", income, rng.randint(18, 80), seg, tenure, 1 if rng.random() < p else 0])
