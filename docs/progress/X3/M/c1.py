import sys; sys.path.insert(0, "."); from x3 import *
for name, csv in [("case headers a,A", "a,A,b\n1,10,1\n2,20,2\n3,30,3\n"),
                  ("empty header", "a,,b\n1,10,1\n2,20,2\n3,30,3\n"),
                  ("header __row_id", "__row_id,b\n1,1\n2,2\n3,3\n"),
                  ("header _op on a plain upload", "k,_op\n1,U\n2,D\n3,I\n")]:
    up, err = upload([("t.csv", csv.encode())], name="x3 " + name)
    s, c = check(up); print(f"{name}: check {s}", (c.get("detail") or c.get("blocks") or [t["schema"] for t in c.get("tables", [])]) if isinstance(c, dict) else c)
