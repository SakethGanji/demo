"""P07 verification data: 1 000 accounts (acct "A00000…", zip with leading zeros, an integer column with missing cells,
a date, a 0/1 target that depends on the month opened) written to accounts.csv in the current directory. Deterministic."""
import csv
import random

rng = random.Random(7)
with open("accounts.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["acct", "zip", "income", "age", "segment", "opened", "note", "y"])
    for i in range(1000):
        age = "" if i % 17 == 0 else str(rng.randint(18, 80))
        income = round(rng.uniform(15000, 150000), 2)
        month = rng.randint(1, 12)  # the signal is the month the account was opened (a date part Feature Lab can find)
        y = 1 if rng.random() < (0.8 if month in (11, 12, 1) else 0.1) else 0
        w.writerow([f"A{i:05d}", f"{rng.randint(0, 99999):05d}", income, age, rng.choice(["retail", "sme", "corp"]),
                    f"20{rng.randint(10, 24)}-{month:02d}-{rng.randint(1, 28):02d}", f"n{i}", y])
