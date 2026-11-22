"""X5/x2: summary of a dry-run plan or GET /admin/lake/migrate answer. usage: plansum.py <json> [state.json]"""
import json, sys
d = json.load(open(sys.argv[1])); st = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {}
name = {v: k for k, v in st.items() if isinstance(v, str) and v.startswith("ds_")}
print({k: v for k, v in d.items() if k not in ("plan", "versions")})
for v in d.get("plan") or d.get("versions") or []:
    if v.get("action", v.get("status")) not in ("migrate", "migrated") or v.get("version_id") in name:
        print(f"  {v['version_id']:<14} {name.get(v['version_id'], ''):<4} {v.get('action', v.get('status')):<9} {v.get('reason')}: {str(v.get('message') or '')[:220]}")
