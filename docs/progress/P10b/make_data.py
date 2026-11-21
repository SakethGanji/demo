"""P10b verification data: accounts.csv (v1) and v2.csv (the next extract: 300 incomes corrected, 200 accounts closed,
150 opened), written to the current directory. Deterministic. N rows (default 200,000) as argv[1]."""
import csv
import random
import sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
rng = random.Random(10)
rows = []
for i in range(N):
    income = round(rng.uniform(15000, 150000), 2)
    seg = rng.choice(["retail", "sme", "corp"])
    p = 0.7 if income < 40000 else 0.35 if seg == "sme" else 0.12
    rows.append([f"A{i:07d}", income, rng.randint(18, 80), seg, rng.randint(0, 30), rng.choice(["north", "south", "east", "west"]),
                 1 if rng.random() < p else 0])
head = ["acct", "income", "age", "segment", "tenure", "region", "y"]
with open("accounts.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(head); w.writerows(rows)
v2 = [list(r) for r in rows]
for i in rng.sample(range(N), 300):
    v2[i][1] = round(v2[i][1] * 1.1, 2)
closed = set(rng.sample(range(N), 200))
v2 = [r for i, r in enumerate(v2) if i not in closed]
for j in range(150):
    v2.append([f"B{j:07d}", round(rng.uniform(15000, 150000), 2), rng.randint(18, 80), "retail", 0, "north", rng.randint(0, 1)])
with open("v2.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(head); w.writerows(v2)
print(N, len(v2))
