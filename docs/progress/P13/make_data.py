"""P13 test files: a 2-table dataset (loans + branches) and deltas against loans. Usage: make_data.py <out dir>"""
import csv, random, sys
from datetime import date, timedelta
from pathlib import Path

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
rnd = random.Random(13)
N = 5000
branches = [f"{i:03d}" for i in range(1, 21)]


def loan(i, rnd):
    return {"acct": f"A{i:06d}", "branch": rnd.choice(branches), "balance": round(rnd.uniform(500, 90000), 2),
            "rate": round(rnd.uniform(2, 19), 2), "opened": str(date(2019, 1, 1) + timedelta(days=rnd.randrange(1800))),
            "segment": rnd.choice(["retail", "sme", "premium"]), "defaulted": int(rnd.random() < 0.12)}


COLS = ["acct", "branch", "balance", "rate", "opened", "segment", "defaulted"]
loans = [loan(i, rnd) for i in range(1, N + 1)]


def write(name, rows, cols):
    with open(out / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


write("loans.csv", loans, COLS)
write("branches.csv", [{"branch": b, "region": rnd.choice(["north", "south", "east", "west"]), "staff": rnd.randint(3, 40)}
                       for b in branches], ["branch", "region", "staff"])

# delta 1: 40 updates (balance and rate change), 25 deletes, 60 inserts, 3 deletes of missing keys, 2 updates of missing
# keys (→ inserts), lower-case / word ops to show the mapping, rows in a mixed order
D = ["_op"] + COLS
ids = rnd.sample(range(1, N + 1), 65)
upd, dele = ids[:40], ids[40:]
rows = []
for i in upd:
    r = dict(loans[i - 1]); r["balance"] = round(r["balance"] * 1.1 + 1, 2); r["rate"] = round(r["rate"] + 0.5, 2)
    rows.append({"_op": rnd.choice(["U", "u", "update"]), **r})
for i in dele:
    rows.append({"_op": rnd.choice(["D", "delete"]), "acct": f"A{i:06d}"})
for i in range(N + 1, N + 61):
    rows.append({"_op": "I", **loan(i, rnd)})
for i in (900001, 900002, 900003):
    rows.append({"_op": "D", "acct": f"A{i:06d}"})
for i in (900011, 900012):
    rows.append({"_op": "U", **loan(i, rnd)})
rnd.shuffle(rows)
write("delta1.csv", rows, D)

# a duplicate-key delta: 3 keys twice
dup = [{"_op": "U", **loans[i]} for i in (10, 20, 30, 40)] + [{"_op": "D", "acct": loans[i]["acct"]} for i in (10, 20, 30)]
write("delta_dup.csv", dup, D)

# insert-only: 300 new accounts
write("delta_inserts.csv", [{"_op": "I", **loan(i, rnd)} for i in range(N + 1001, N + 1301)], D)

# an unknown op value, and a new column
write("delta_badop.csv", [{"_op": "X", **loans[5]}, {"_op": "U", **loans[6]}], D)
write("delta_newcol.csv", [{"_op": "U", **loans[7], "score": 700}, {"_op": "U", **loans[8], "score": 650}], D + ["score"])

# a delta for the branches table (the other table of the version): 1 update, 1 insert
write("branches_delta.csv", [{"_op": "U", "branch": "005", "region": "central", "staff": 12},
                             {"_op": "I", "branch": "021", "region": "north", "staff": 4}], ["_op", "branch", "region", "staff"])
print("written", sorted(p.name for p in out.iterdir()))
print("expected delta1: inserted 62 (60 I + 2 U of missing keys), updated 40, deleted 25, missing 3")
