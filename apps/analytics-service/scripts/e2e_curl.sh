#!/usr/bin/env bash
# End-to-end smoke of the analytics service over REAL HTTP with curl,
# sequenced the way a UI would drive it: admin provisions a team and users,
# an editor uploads and explores data, a steward gates quality, an analyst
# profiles/pivots/exports, and the permission surface is probed from every
# seat (in-team viewer 403s, outsider 404s).
#
# Usage:
#   BASE=http://localhost:9009 scripts/e2e_curl.sh
# Requirements: a running server (uvicorn app.main:app), curl, and the
# service venv (for building a test workbook + JSON assertions).
# Exit code 0 = every check passed. Creates throwaway data in the target DB.

set -u
cd "$(dirname "$0")/.."

BASE="${BASE:-http://localhost:9009}"
API="$BASE/api/v1"
PY="${PY:-venv/bin/python}"
ADMIN="00000000-0000-0000-0000-000000000001"   # seeded System superuser
RAND="$($PY -c 'import uuid; print(uuid.uuid4().hex[:8])')"
TMP="$(mktemp -d /tmp/accel-e2e.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
CODE=""; BODY=""

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

hit() {  # hit METHOD URL USER [JSON_BODY] [EXTRA_CURL_ARGS...]
  local method="$1" url="$2" user="$3" data="${4:-}"; shift 3; [ $# -gt 0 ] && shift
  local args=(-s -o "$TMP/body" -w '%{http_code}' -X "$method" "$url" -H "X-User-Id: $user")
  [ -n "$data" ] && args+=(-H 'Content-Type: application/json' -d "$data")
  args+=("$@")
  CODE=$(curl "${args[@]}")
  BODY=$(cat "$TMP/body")
}

check() {  # check NAME WANT_CODE [PYTHON_ASSERT over json `d`]
  local name="$1" want="$2" expr="${3:-}"
  if [ "$CODE" != "$want" ]; then
    FAIL=$((FAIL+1)); echo "FAIL  $name — HTTP $CODE (want $want): $(echo "$BODY" | head -c 200)"
    return 1
  fi
  if [ -n "$expr" ]; then
    if ! printf '%s' "$BODY" | $PY -c "
import sys, json
d = json.load(sys.stdin)
assert $expr, d
" 2>"$TMP/err"; then
      FAIL=$((FAIL+1)); echo "FAIL  $name — assert \`$expr\`: $(echo "$BODY" | head -c 200)"
      return 1
    fi
  fi
  PASS=$((PASS+1)); echo "PASS  $name"
}

jget() {  # jget '["key"]' — extract from the last BODY
  printf '%s' "$BODY" | $PY -c "import sys, json; print(json.load(sys.stdin)$1)"
}

# ---------------------------------------------------------------------------
# A. Identity + provisioning (the admin console flow)
# ---------------------------------------------------------------------------

hit GET "$API/auth/me" "$ADMIN"
check "admin whoami" 200 'd["user"]["is_superuser"] is True'

hit POST "$API/teams" "$ADMIN" "{\"name\": \"e2e-$RAND\"}"
check "create team" 201 'd["id"]'
TEAM=$(jget '["id"]')

hit POST "$API/auth/users" "$ADMIN" "{\"email\": \"editor-$RAND@bank.com\", \"name\": \"Editor\", \"team_id\": \"$TEAM\"}"
check "create editor user" 201
EDITOR=$(jget '["id"]')
hit PATCH "$API/teams/$TEAM/members/$EDITOR" "$ADMIN" '{"role": "editor"}'
check "grant editor role" 200 'd["role"] == "editor"'

hit POST "$API/auth/users" "$ADMIN" "{\"email\": \"viewer-$RAND@bank.com\", \"name\": \"Viewer\", \"team_id\": \"$TEAM\"}"
check "create viewer user" 201
VIEWER=$(jget '["id"]')

hit POST "$API/teams" "$ADMIN" "{\"name\": \"other-$RAND\"}"
check "create other team" 201
OTHER_TEAM=$(jget '["id"]')
hit POST "$API/auth/users" "$ADMIN" "{\"email\": \"outsider-$RAND@bank.com\", \"name\": \"Outsider\", \"team_id\": \"$OTHER_TEAM\"}"
check "create outsider user" 201
OUTSIDER=$(jget '["id"]')

# ---------------------------------------------------------------------------
# B. Uploads (multipart CSV + real Excel workbook, like the UI's file picker)
# ---------------------------------------------------------------------------

cat > "$TMP/orders.csv" <<'CSV'
region,quarter,amount
EU,Q1,100
EU,Q2,200
US,Q1,50
US,Q2,150
APAC,Q1,25
CSV
CODE=$(curl -s -o "$TMP/body" -w '%{http_code}' -X POST "$API/upload" \
  -H "X-User-Id: $EDITOR" -H "X-Team-Id: $TEAM" \
  -F "file=@$TMP/orders.csv;type=text/csv")
BODY=$(cat "$TMP/body")
check "multipart CSV upload" 200 'd["status"] == "complete"'
DS=$(jget '["dataset_id"]')

$PY - "$TMP/book.xlsx" <<'PYEOF'
import sys
from openpyxl import Workbook
wb = Workbook()
ws = wb.active; ws.title = "Revenue"
ws.append(["Amount", "Region"])
for r in ([100, "EU"], [200, "US"], [300, "APAC"]): ws.append(r)
costs = wb.create_sheet("Expenses")
costs.append(["Item", "Cost"]); costs.append(["rent", 50]); costs.append(["power", 20])
wb.save(sys.argv[1])
PYEOF
CODE=$(curl -s -o "$TMP/body" -w '%{http_code}' -X POST "$API/upload" \
  -H "X-User-Id: $EDITOR" -H "X-Team-Id: $TEAM" \
  -F "file=@$TMP/book.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
BODY=$(cat "$TMP/body")
check "workbook XLSX upload" 200 'd["status"] == "complete"'
WB=$(jget '["dataset_id"]')

hit GET "$API/datasets/$WB/sheets" "$EDITOR"
check "workbook sheets captured" 200 '{s["name"] for s in d["items"]} == {"Revenue", "Expenses"} and all(s["schema_fingerprint"] for s in d["items"])'

# ---------------------------------------------------------------------------
# C. Explorer (browse → query → column deep-dive), incl. the sheet contract
# ---------------------------------------------------------------------------

hit GET "$API/datasets/$DS/versions/1/preview?limit=3" "$EDITOR"
check "preview" 200 'len(d["items"]) == 3 and d["total"] == 5'

hit POST "$API/datasets/$DS/versions/1/query" "$EDITOR" \
  '{"filters": {"conditions": [{"column": "amount", "op": "gt", "value": 60}]}, "sort": [{"column": "amount", "direction": "desc"}]}'
check "structured query" 200 '[r["amount"] for r in d["items"]] == [200, 150, 100] and d["total"] == 3'

hit POST "$API/datasets/$WB/versions/1/query" "$EDITOR" '{}'
check "multi-sheet needs sheet (problem+json)" 400 'd["code"] == "sheet-selection-required" and set(d["sheets"]) == {"Revenue", "Expenses"}'

hit POST "$API/datasets/$WB/versions/1/sheets/Expenses/query" "$EDITOR" '{}'
check "sheet-scoped query" 200 'd["total"] == 2'

hit GET "$API/datasets/$DS/versions/1/columns/amount" "$EDITOR"
check "column explorer" 200 'd["dtype"] == "numeric" and d["is_candidate_key"] is True and d["histogram"]'

hit GET "$API/datasets/$DS/versions/1/columns/nope" "$EDITOR"
check "unknown column is machine-readable" 400 'd["code"] == "unknown-column" and "amount" in d["available"]'

# ---------------------------------------------------------------------------
# D. Quality gate + tags (the steward flow)
# ---------------------------------------------------------------------------

hit POST "$API/datasets/$DS/rules" "$EDITOR" \
  '{"name": "amount-not-null", "rule_type": "not_null", "sheet_selector": "data", "column_selector": "amount"}'
check "create quality rule" 201
hit POST "$API/datasets/$DS/versions/1/validate" "$EDITOR"
check "validation run" 200 'd["status"] == "completed" and d["error_failures"] == 0'
hit POST "$API/datasets/$DS/tags/production/promote" "$EDITOR" '{"version_number": 1, "reason": "e2e go-live"}'
check "promote to production" 200 'd["to_version_number"] == 1'

# ---------------------------------------------------------------------------
# E. New version → profile both → drift review (the release-review flow)
# ---------------------------------------------------------------------------

CODE=$(curl -s -o "$TMP/body" -w '%{http_code}' -X POST "$API/upload" \
  -H "X-User-Id: $EDITOR" -H "X-Team-Id: $TEAM" \
  --data-urlencode "dataset_id=$DS" \
  --data-urlencode 'data=[{"region":"EU","quarter":"Q1","amount":100},{"region":"LATAM","quarter":"Q1","amount":null},{"region":"LATAM","quarter":"Q2","amount":null},{"region":"US","quarter":"Q2","amount":500}]')
BODY=$(cat "$TMP/body")
check "upload v2 (inline)" 200 'd["status"] == "complete"'

hit POST "$API/datasets/$DS/versions/1/profile-runs" "$EDITOR"
check "profile v1" 200 'd[0]["status"] == "completed"'
hit POST "$API/datasets/$DS/versions/2/profile-runs" "$EDITOR"
check "profile v2 finds regressions" 200 '{"null-rate-spike", "new-categories"} <= {i["rule"] for r in d for i in r["insights"]}'

hit GET "$API/datasets/$DS/versions/1/sheets/data/diff/2?include=profile" "$EDITOR"
check "diff with profile drift" 200 'next(c for c in d["profile_drift"]["columns"] if c["column"] == "amount")["null_percent_delta"] == 50.0'

# ---------------------------------------------------------------------------
# F. Reuse & analysis: view, pivot, export, sandboxed SQL, publish, chart
# ---------------------------------------------------------------------------

hit POST "$API/datasets/$DS/views" "$EDITOR" \
  '{"name": "big-amounts", "sheet": "data", "query": {"filters": {"conditions": [{"column": "amount", "op": "gte", "value": 100}]}}}'
check "save view" 201
VIEW=$(jget '["id"]')
hit POST "$API/datasets/$DS/views/$VIEW/run" "$EDITOR" '{}'
check "run view follows current" 200 'd["version_number"] == 2 and d["result"]["total"] == 2'

hit POST "$API/pivot" "$EDITOR" \
  "{\"dataset_id\": \"$DS\", \"version_number\": 1, \"rows\": [\"region\"], \"columns\": \"quarter\", \"values\": [{\"column\": \"amount\", \"function\": \"sum\", \"alias\": \"amt\"}], \"include_row_totals\": true}"
check "pivot" 200 'd["columns"] == ["region", "Q1", "Q2", "total_amt"] and d["totals"] == {"amt": 525}'
PIVOT_FILE=$(jget '["result_file"]')

hit POST "$API/samples/$PIVOT_FILE/export?format=csv" "$EDITOR"
check "export pivot to CSV" 200 'd["export_file"].endswith(".csv")'
EXPORT_FILE=$(jget '["export_file"]')
CODE=$(curl -s -o "$TMP/body" -w '%{http_code}' "$API/samples/$EXPORT_FILE" -H "X-User-Id: $EDITOR")
BODY=$(cat "$TMP/body")
[ "$CODE" = "200" ] && head -1 "$TMP/body" | grep -q "region,Q1,Q2,total_amt" \
  && { PASS=$((PASS+1)); echo "PASS  download CSV export"; } \
  || { FAIL=$((FAIL+1)); echo "FAIL  download CSV export — HTTP $CODE: $(head -c 120 "$TMP/body")"; }

hit POST "$API/datasets/$DS/versions/1/sql" "$EDITOR" \
  '{"sql": "SELECT region, SUM(amount) AS total FROM data GROUP BY region ORDER BY total DESC"}'
check "sandboxed SQL" 200 'd["items"][0] == {"region": "EU", "total": 300.0}'
hit POST "$API/datasets/$DS/versions/1/sql" "$EDITOR" \
  "{\"sql\": \"COPY data TO '/tmp/exfil.parquet' (FORMAT PARQUET)\"}"
check "SQL gate rejects non-SELECT" 400 'd["code"] == "select-only"'
hit POST "$API/datasets/$DS/versions/1/sql" "$EDITOR" \
  "{\"sql\": \"SELECT * FROM '/etc/passwd'\"}"
check "SQL file read blocked + sanitized" 400 'd["code"] == "sql-error" and "/etc/passwd" not in d["detail"]'

hit POST "$API/datasets/$DS/analytics" "$EDITOR" \
  '{"name": "quarterly", "kind": "pivot", "params": {"rows": ["region"], "columns": "quarter", "values": [{"column": "amount", "function": "sum"}]}}'
check "save pivot definition" 201
DEF=$(jget '["id"]')
hit POST "$API/datasets/$DS/analytics/$DEF/run" "$EDITOR"
check "run pivot definition" 200 'd["status"] == "completed" and d["artifact_id"]'
RUN=$(jget '["id"]')
hit POST "$API/datasets/$DS/analytics/runs/$RUN/publish" "$EDITOR" \
  "{\"mode\": \"new_dataset\", \"name\": \"e2e-pub-$RAND\"}"
check "publish pivot as dataset" 200 'd["version_number"] == 1'
CHILD=$(jget '["dataset_id"]')
hit GET "$API/datasets/$CHILD/lineage" "$EDITOR"
check "lineage says pivoted_from" 200 'd["parents"][0]["relation"] == "pivoted_from"'

hit POST "$API/datasets/$DS/charts" "$EDITOR" \
  "{\"name\": \"big-amounts-table\", \"chart_type\": \"table\", \"view_id\": \"$VIEW\"}"
check "save chart over view" 201
CHART=$(jget '["id"]')

hit GET "$API/datasets/$DS/timeline?limit=100" "$EDITOR"
check "timeline merges history" 200 '{"version_created", "tag_promote", "validation_run", "profile_run", "published_to", "audit"} <= {e["event_type"] for e in d["items"]}'

# ---------------------------------------------------------------------------
# G. Docs & health (Wave 3): data dictionary, duplicates/missing
# ---------------------------------------------------------------------------

hit GET "$API/datasets/$DS/versions/2/duplicates?columns=region" "$EDITOR"
check "duplicate groups (subset)" 200 'd["group_count"] == 1 and d["groups"][0]["key"] == {"region": "LATAM"} and d["groups"][0]["count"] == 2'
hit GET "$API/datasets/$DS/versions/2/missing" "$EDITOR"
check "missing report is profile-backed" 200 'd["source"] == "profile_run" and d["columns"][0]["column"] == "amount" and d["columns"][0]["null_count"] == 2'
hit GET "$API/datasets/$WB/versions/1/duplicates" "$EDITOR"
check "duplicates need sheet on workbook" 400 'd["code"] == "sheet-selection-required"'

hit PUT "$API/datasets/$DS/sheet-metadata/data/columns/amount" "$EDITOR" \
  '{"business_name": "Order amount", "unit": "USD", "sensitivity": "internal"}'
check "set column dictionary entry" 200 'd["column_name"] == "amount" and d["sheet_key"] == "data"'
hit GET "$API/datasets/$DS/sheet-metadata/data/columns" "$EDITOR"
check "list column dictionary" 200 'd["total"] == 1 and d["items"][0]["business_name"] == "Order amount"'
hit PUT "$API/datasets/$DS/sheet-metadata/data/columns/nope" "$EDITOR" '{"business_name": "x"}'
check "dictionary rejects unknown column" 400 'd["code"] == "unknown-column" and "amount" in d["available"]'

hit GET "$API/datasets/$DS/health" "$EDITOR"
check "health read-model" 200 'd["current_version_number"] == 2 and d["dimensions"]["drift"]["status"] == "warning" and d["dimensions"]["validation"]["status"] == "unknown" and d["dimensions"]["documentation"]["evidence"]["bucket"] == "partial" and d["dimensions"]["missing_data"]["evidence"]["worst"]["column"] == "amount"'

# §18 — the same signals surfaced as catalog facets + list filters.
hit GET "$API/datasets/facets" "$EDITOR"
check "catalog facets expose signals" 200 '{"validation_status", "has_schema_drift", "documentation"} <= set(d) and sum(d["documentation"].values()) >= 1 and "false" in d["has_schema_drift"]'
hit GET "$API/datasets?documentation=partial&limit=200" "$EDITOR"
check "filter datasets by documentation, rows carry signals" 200 "all(i['documentation'] == 'partial' for i in d['items']) and next(i for i in d['items'] if i['id'] == '$DS') == dict(next(i for i in d['items'] if i['id'] == '$DS'), documentation='partial', validation_status='none', has_schema_drift=True)"

# ---------------------------------------------------------------------------
# H. Transformations (Wave 4): build → preview → run → profile → publish
# ---------------------------------------------------------------------------

hit POST "$API/datasets/$DS/transformations" "$EDITOR" \
  '{"name": "tidy", "sheet": "data", "steps": [
      {"type": "case_normalize", "columns": ["region"], "mode": "lower"},
      {"type": "filter", "where": {"conditions": [{"column": "amount", "op": "is_not_null"}]}},
      {"type": "compute", "into": "amount_usd", "expression":
        {"op": "round", "digits": 2, "value":
          {"op": "arith", "fn": "mul",
           "left": {"op": "col", "name": "amount"},
           "right": {"op": "lit", "value": 1.1}}}},
      {"type": "sort", "by": [{"column": "amount", "direction": "desc"}]}]}'
check "create transformation" 201 'd["sheet_key"] == "data" and len(d["steps"]) == 4'
TRANSFORM=$(jget '["id"]')

hit POST "$API/datasets/$DS/transformations" "$EDITOR" \
  '{"name": "broken", "sheet": "data", "steps": [{"type": "select", "columns": ["ghost"]}]}'
check "pipeline is validated before it is saved" 400 'd["code"] == "unknown-column" and "amount" in d["available"]'

hit POST "$API/datasets/$DS/transformations/$TRANSFORM/preview?rows=50" "$EDITOR"
check "preview is a dry run" 200 'd["approximate"] is True and [c["name"] for c in d["output_schema"]][-1] == "amount_usd" and all(r["region"] == r["region"].lower() for r in d["rows"])'

hit POST "$API/datasets/$DS/transformations/$TRANSFORM/run" "$EDITOR"
check "run materializes the output" 200 'd["status"] == "completed" and d["result_summary"]["source_row_count"] == 4 and d["result_summary"]["row_count"] == 2 and d["artifact_id"]'
TRUN=$(jget '["id"]')
TRANSFORM_FILE=$(jget '["result_summary"]["sample_file"]')

hit GET "$API/samples/$TRANSFORM_FILE/data" "$EDITOR"
check "transform output is downloadable" 200 'len(d["data"] if isinstance(d, dict) else d) == 2'

hit GET "$API/datasets/$DS/transformations/runs/$TRUN" "$EDITOR"
check "run carries auto-profile + drift" 200 'd["output_profile"]["row_count"] == 2 and "amount_usd" in d["source_drift"]["added_columns"] and d["source_drift"]["row_count_delta"] == -2'

hit POST "$API/datasets/$DS/transformations/runs/$TRUN/publish" "$EDITOR" \
  "{\"mode\": \"new_dataset\", \"name\": \"tidied-$RAND\"}"
check "publish as a new dataset" 200 'd["mode"] == "new_dataset" and d["version_number"] == 1'
TIDIED=$(jget '["dataset_id"]')

hit GET "$API/datasets/$TIDIED/lineage" "$EDITOR"
check "lineage records transformed_from" 200 'd["parents"][0]["relation"] == "transformed_from"'

hit GET "$API/datasets/$DS/versions/2/preview" "$EDITOR"
check "the source version is untouched" 200 'd["total"] == 4'

hit GET "$API/datasets/$DS/timeline" "$EDITOR"
check "timeline includes the transformation run" 200 'any(e["event_type"] == "transformation_run" and e["details"]["transformation"] == "tidy" for e in d["items"])'

# ---------------------------------------------------------------------------
# I. Relationships + the guided join builder (Wave 5)
# ---------------------------------------------------------------------------

# The workbook ($WB) has Revenue(Amount, Region) and Expenses(Item, Cost);
# nothing links them, so discovery on it should stay quiet. Use a purpose-built
# CRM workbook for the relationship flow instead.
$PY - "$TMP/crm.xlsx" <<'PYEOF'
import sys
from openpyxl import Workbook
wb = Workbook()
cust = wb.active; cust.title = "Customers"
cust.append(["customer_id", "tier"])
for r in ([1, "gold"], [2, "silver"], [3, "gold"]): cust.append(r)
orders = wb.create_sheet("Orders")
orders.append(["order_id", "customer_id", "total"])
for r in ([10, 1, 100.0], [11, 2, 40.0], [12, 1, 60.0]): orders.append(r)
wb.save(sys.argv[1])
PYEOF
CODE=$(curl -s -o "$TMP/body" -w '%{http_code}' -X POST "$API/upload" \
  -H "X-User-Id: $EDITOR" -H "X-Team-Id: $TEAM" \
  -F "file=@$TMP/crm.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
BODY=$(cat "$TMP/body")
check "upload CRM workbook" 200 'd["status"] == "complete"'
CRM=$(jget '["dataset_id"]')

hit POST "$API/datasets/$CRM/relationships/suggest" "$EDITOR"
check "discovery finds the undeclared FK" 200 'd["suggested"] >= 1 and any(r["from_sheet"] == "orders" and r["to_sheet"] == "customers" and r["from_column"] == "customer_id" for r in d["relationships"])'
hit GET "$API/datasets/$CRM/relationships?status=suggested" "$EDITOR"
check "suggestion carries its evidence" 200 'd["items"][0]["evidence"]["coverage"] == 1.0 and d["items"][0]["evidence"]["target_uniqueness"] == 1.0 and d["items"][0]["method"] == "statistical"'
REL=$(jget '["items"][0]["id"]')

hit POST "$API/joins/execute" "$EDITOR" "{\"relationship_id\": \"$REL\"}"
check "unconfirmed relationships cannot join" 409 'd["code"] == "relationship-not-confirmed"'

hit POST "$API/joins/preview" "$EDITOR" "{\"relationship_id\": \"$REL\", \"how\": \"inner\"}"
check "join pre-flight warns before running" 200 'd["warnings"]["left_rows"] == 3 and d["warnings"]["left_duplicate_keys"] == 1 and d["warnings"]["right_duplicate_keys"] == 0 and d["warnings"]["many_to_many"] is False and d["warnings"]["estimated_output_rows"] == 3 and d["warnings"]["unmatched_right_pct"] == 33.33 and len(d["preview"]) == 3'

hit POST "$API/datasets/$CRM/relationships/$REL/confirm" "$EDITOR"
check "confirm the relationship" 200 'd["status"] == "confirmed"'
hit POST "$API/datasets/$CRM/relationships/$REL/confirm" "$EDITOR"
check "re-confirming is an illegal transition" 409 'd["code"] == "invalid-relationship-transition"'

hit POST "$API/joins/execute" "$EDITOR" "{\"relationship_id\": \"$REL\"}"
check "execute the join" 200 'd["row_count"] == 3 and "tier" in d["output_columns"]'
JOIN_RUN=$(jget '["run_id"]')
JOIN_FILE=$(jget '["sample_file"]')

hit GET "$API/samples/$JOIN_FILE/data" "$EDITOR"
check "join output is downloadable" 200 'len(d["data"] if isinstance(d, dict) else d) == 3'

hit POST "$API/joins/$JOIN_RUN/publish" "$EDITOR" "{\"mode\": \"new_dataset\", \"name\": \"joined-$RAND\"}"
check "publish the join" 200 'd["version_number"] == 1'
JOINED=$(jget '["dataset_id"]')
hit GET "$API/datasets/$JOINED/lineage" "$EDITOR"
check "a join records both parents" 200 '{p["relation"] for p in d["parents"]} == {"joined_from"}'

# §24 — the confirmed relationship also drives coordinated sampling.
hit POST "$API/sample/coordinated" "$EDITOR" \
  "{\"dataset_id\": \"$CRM\", \"driver_sheet\": \"Customers\", \"target_total_volume\": 3,
    \"sampling_steps\": [{\"method\": \"random\", \"sample_size\": 3}], \"seed\": 5,
    \"related\": [{\"sheet\": \"Orders\", \"relationship_id\": \"$REL\"}]}"
check "relationship-driven coordinated sampling" 200 'd["related"][0]["key_source"] == "relationship" and d["related"][0]["left_on"] == "customer_id" and d["related"][0]["referenced_count"] == d["related"][0]["sampled_count"]'

hit POST "$API/sample/coordinated" "$EDITOR" \
  "{\"dataset_id\": \"$CRM\", \"driver_sheet\": \"Customers\", \"target_total_volume\": 3,
    \"sampling_steps\": [{\"method\": \"random\", \"sample_size\": 3}], \"seed\": 5,
    \"related\": [{\"sheet\": \"Orders\", \"relationship_id\": \"$REL\",
                   \"sampling_steps\": [{\"method\": \"random\", \"sample_size\": 1}],
                   \"target_total_volume\": 1}]}"
check "sub-sampling reduces the child sheet" 200 'd["related"][0]["referenced_count"] == 3 and d["related"][0]["sampled_count"] == 1'

# ---------------------------------------------------------------------------
# K. Review, governance, and notifications
# ---------------------------------------------------------------------------

# Row-level diff: which ROWS changed between v1 and v2, not just which columns.
hit POST "$API/datasets/$DS/versions/1/sheets/data/row-diff/2" "$EDITOR" \
  '{"key": ["region", "quarter"]}'
check "row diff classifies every row" 200 'd["added"] == 2 and d["removed"] == 3 and d["changed"] == 1 and d["unchanged"] == 1'
check "row diff names the column that moved" 200 'd["column_changes"][0]["column"] == "amount"'
DIFF_FILE=$(jget '["diff_file"]')
hit GET "$API/samples/$DIFF_FILE/data" "$EDITOR"
check "the full cell-level diff is an artifact" 200 'any(r["change_type"] == "changed" and r["column_name"] == "amount" for r in (d["data"] if isinstance(d, dict) else d))'

hit POST "$API/datasets/$DS/versions/1/sheets/data/row-diff/2" "$EDITOR" '{"key": ["region"]}'
check "a non-unique key is refused, not answered" 409 'd["code"] == "ambiguous-diff-key"'

# Charts render, rather than only storing config.
hit POST "$API/datasets/$DS/charts/$CHART/render" "$EDITOR"
check "chart renders real series" 200 'len(d["categories"]) >= 1 and d["series"] and d["x_field"]'

# Lineage as a graph: the published join traced back to both sources.
hit GET "$API/datasets/$JOINED/lineage/graph" "$EDITOR"
check "lineage graph walks the chain" 200 'len(d["nodes"]) >= 2 and all(e["relation"] == "joined_from" for e in d["edges"])'

# Declaring a column sensitive actually restricts it.
hit PUT "$API/datasets/$CRM/sheet-metadata/customers/columns/tier" "$EDITOR" \
  '{"business_name": "Customer tier", "sensitivity": "confidential"}'
check "declare a column sensitive" 200 'd["sensitivity"] == "confidential"'
hit GET "$API/datasets/$CRM/versions/1/sheets/Customers/preview" "$VIEWER"
check "viewer sees the column masked" 200 'd["masked_columns"] == ["tier"] and all(r["tier"] == "***" for r in d["items"])'
hit GET "$API/datasets/$CRM/versions/1/download" "$VIEWER"
check "and cannot bypass it by downloading" 403 'd["code"] == "sensitive-data-restricted"'
hit GET "$API/datasets/$CRM/versions/1/sheets/Customers/preview" "$ADMIN"
check "an admin still sees real values" 200 'd["masked_columns"] == [] and {r["tier"] for r in d["items"]} == {"gold", "silver"}'

# Webhooks: subscribe, ping, and confirm the attempt was signed and recorded.
# The receiver is this server's own /health, which rejects POST — a deterministic
# 405 that still proves signing, delivery, and recording end to end.
hit POST "$API/webhooks" "$ADMIN" \
  "{\"name\": \"e2e-$RAND\", \"url\": \"$BASE/health\", \"events\": [\"tag.promoted\"]}"
check "create a webhook subscription" 201 'd["secret"].startswith("whsec_") and d["events"] == ["tag.promoted"]'
HOOK=$(jget '["id"]')
hit GET "$API/webhooks/$HOOK" "$ADMIN"
check "the signing secret is never readable again" 200 '"secret" not in d'
hit POST "$API/webhooks/$HOOK/test" "$ADMIN"
check "a test ping is delivered and recorded" 200 'd["event_type"] == "webhook.test" and d["response_status"] == 405 and d["attempts"] == 1'
hit DELETE "$API/webhooks/$HOOK" "$ADMIN"
check "delete the subscription" 204

# ---------------------------------------------------------------------------
# L2. Artifact storage layout, listing, and retention
# ---------------------------------------------------------------------------

# Every derived output written above lives under artifacts/{team}/{dataset}/{kind}/.
# The listing reads Postgres, so it reports the real kind and only what the
# caller can actually open.
hit GET "$API/samples?limit=200" "$EDITOR"
check "samples listing is served from the artifacts table" 200 \
  'any(e["filename"] == "'"$PIVOT_FILE"'" and e["file_type"] == "pivot_output" and e["size_bytes"] > 0 for e in d["items"])'
check "the listing paginates" 200 'd["limit"] == 200 and d["offset"] == 0'

# The key is not derivable from a filename — it resolves through the row that
# also governs authorization, so a made-up name is a 404 rather than a probe.
hit GET "$API/samples/definitely_not_a_real_file.parquet" "$EDITOR"
check "an unknown filename 404s" 404

hit GET "$API/storage/retention" "$ADMIN"
check "retention policy: published sources are kept forever" 200 \
  'dict((r["artifact_type"], r["retention_days"]) for r in d["rules"])["published_source"] is None and dict((r["artifact_type"], r["retention_days"]) for r in d["rules"])["query_output"] < dict((r["artifact_type"], r["retention_days"]) for r in d["rules"])["sample_output"]'
hit GET "$API/storage/retention" "$EDITOR"
check "retention policy is platform-admin only" 403

hit POST "$API/storage/gc" "$ADMIN"
check "gc sweeps without touching live artifacts" 200 \
  'd["expired_deleted"] >= 0 and d["orphans_deleted"] >= 0'
hit GET "$API/samples/$PIVOT_FILE/data" "$EDITOR"
check "a live artifact survives the sweep" 200
hit POST "$API/storage/gc" "$EDITOR"
check "gc is platform-admin only" 403

hit GET "$API/storage/usage" "$EDITOR"
check "storage usage splits datasets from derived artifacts" 200 \
  'd["total_bytes"] == d["datasets_bytes"] + d["samples_bytes"] + d["exports_bytes"] + d["uploads_bytes"] and d["samples_bytes"] > 0 and d["exports_bytes"] > 0'

# ---------------------------------------------------------------------------
# L. The permission surface, from every seat
# ---------------------------------------------------------------------------

hit GET "$API/datasets/$DS/versions/1/preview" "$VIEWER"
check "in-team viewer can read" 200
hit POST "$API/datasets/$DS/views" "$VIEWER" \
  '{"name": "nope", "sheet": "data", "query": {}}'
check "viewer write is a truthful 403" 403
hit GET "$API/datasets/$DS/versions/1/preview" "$OUTSIDER"
check "outsider: dataset hidden (404)" 404
hit GET "$API/samples/$PIVOT_FILE" "$OUTSIDER"
check "outsider: artifact hidden (404)" 404
hit GET "$API/datasets/$DS/transformations" "$OUTSIDER"
check "outsider: transformations hidden (404)" 404
hit POST "$API/datasets/$DS/transformations/$TRANSFORM/run" "$VIEWER"
check "viewer cannot run a transformation (403)" 403
hit POST "$API/datasets/$DS/transformations/$TRANSFORM/preview" "$VIEWER"
check "viewer can preview (read-only)" 200
hit GET "$API/datasets/$CRM/relationships" "$OUTSIDER"
check "outsider: relationships hidden (404)" 404
hit POST "$API/joins/preview" "$OUTSIDER" "{\"relationship_id\": \"$REL\"}"
check "outsider: join hidden (404)" 404
hit POST "$API/datasets/$CRM/relationships/suggest" "$VIEWER"
check "viewer cannot run discovery (403)" 403
hit GET "$API/audit?limit=5" "$EDITOR"
check "audit is superuser-only" 403
hit GET "$API/audit?limit=5" "$ADMIN"
check "admin reads audit" 200 'any(e["path"].endswith("/sql") for e in d["items"]) or d["total"] > 0'

# ---------------------------------------------------------------------------

echo
echo "e2e: $PASS passed, $FAIL failed (base $BASE)"
[ "$FAIL" -eq 0 ]
