"""X5/x2: summary of roundtrip.py's JSON. usage: rtsum.py <roundtrip.json>"""
import json, sys
d = json.load(open(sys.argv[1])); V = [v for v in d["versions"] if v["status"] == "migrated"]
print("versions read before:", len(d["versions"]), "| migrated:", len(V))
for k in ("legacy_equal", "dtypes_equal", "na_kinds_equal", "text_equal", "table_values_equal"):
    bad = [v["version"] for v in V if not v.get(k)]
    print(f"  {k}: {len(V) - len(bad)}/{len(V)}", bad or "")
print("  leading-zero cells equal:", sum(v["leading_zero_cells"][0] == v["leading_zero_cells"][1] for v in V), "/", len(V),
      "| na cells equal:", sum(v["na_cells"][0] == v["na_cells"][1] for v in V), "| empty str equal:", sum(v["empty_str_cells"][0] == v["empty_str_cells"][1] for v in V))
print("  ids:", {k: sum(1 for v in V if str(v["ids"]).startswith(k)) for k in ("root_rows", "0..n-1", "other")})
for v in d["versions"]:
    if v["status"] != "migrated": print("  not migrated:", v["version"], v["status"], v.get("reason"))
for r in d["runs"]:
    print("  run", r["run"], r["version"], {k: v for k, v in r.items() if k.startswith(("parquet", "view", "pred", "note"))})
