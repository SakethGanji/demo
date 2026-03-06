#!/usr/bin/env bash
# =============================================================================
# Workflow Engine API Test Suite
# Self-contained bash script that verifies every API endpoint.
# Uses curl + jq for requests and assertions.
#
# Usage:
#   bash test_api.sh               # full run with cleanup
#   CLEANUP=0 bash test_api.sh     # skip cleanup phase
#   BASE_URL=http://host:port bash test_api.sh  # custom server
# =============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
CLEANUP="${CLEANUP:-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- Colors ----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
TOTAL_COUNT=0
SERVER_PID=""

# ---- Helpers ---------------------------------------------------------------

api_call() {
  local method="$1" path="$2" body="${3:-}"
  local args=(-s -w '\n%{http_code}' -X "$method" -H 'Content-Type: application/json')
  [[ -n "$body" ]] && args+=(-d "$body")
  local response
  response=$(curl "${args[@]}" "${BASE_URL}${path}")
  HTTP_STATUS=$(echo "$response" | tail -1)
  RESPONSE_BODY=$(echo "$response" | sed '$d')
}

assert_status() {
  local test_name="$1" expected="$2"
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
  if [[ "$HTTP_STATUS" == "$expected" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} ${test_name} (${HTTP_STATUS})"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} ${test_name} — expected ${expected}, got ${HTTP_STATUS}"
    echo -e "    ${RED}Body: ${RESPONSE_BODY:0:200}${NC}"
  fi
}

assert_json() {
  local test_name="$1" jq_expr="$2" expected="$3"
  local actual
  actual=$(echo "$RESPONSE_BODY" | jq -r "$jq_expr" 2>/dev/null || echo "__JQ_ERROR__")
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
  if [[ "$actual" == "$expected" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} ${test_name}"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} ${test_name} — expected '${expected}', got '${actual}'"
  fi
}

assert_json_gte() {
  local test_name="$1" jq_expr="$2" min_val="$3"
  local actual
  actual=$(echo "$RESPONSE_BODY" | jq -r "$jq_expr" 2>/dev/null || echo "0")
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
  if [[ "$actual" -ge "$min_val" ]] 2>/dev/null; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} ${test_name} (${actual} >= ${min_val})"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} ${test_name} — expected >= ${min_val}, got '${actual}'"
  fi
}

assert_json_not_null() {
  local test_name="$1" jq_expr="$2"
  local actual
  actual=$(echo "$RESPONSE_BODY" | jq -r "$jq_expr" 2>/dev/null || echo "null")
  TOTAL_COUNT=$((TOTAL_COUNT + 1))
  if [[ "$actual" != "null" && "$actual" != "" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} ${test_name}"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} ${test_name} — got null/empty"
  fi
}

section() {
  echo ""
  echo -e "${CYAN}${BOLD}═══ $1 ═══${NC}"
}

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "\n${YELLOW}Stopping server (PID $SERVER_PID)...${NC}"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ---- Infra setup -----------------------------------------------------------

section "Infrastructure Setup"

# Detect docker compose command (test actual functionality, not just version)
DC=""
if docker compose -f "$SCRIPT_DIR/docker-compose.yml" ps &>/dev/null; then
  DC="docker compose -f $SCRIPT_DIR/docker-compose.yml"
elif docker-compose -f "$SCRIPT_DIR/docker-compose.yml" ps &>/dev/null 2>&1; then
  DC="docker-compose -f $SCRIPT_DIR/docker-compose.yml"
fi

# Start Postgres if not already running
if [[ -n "$DC" ]] && ! $DC ps postgres 2>/dev/null | grep -q "running"; then
  echo "Starting Postgres via docker compose..."
  $DC up -d postgres
  echo "Waiting for Postgres to be healthy..."
  for i in $(seq 1 30); do
    if $DC ps postgres 2>/dev/null | grep -q "healthy"; then
      break
    fi
    sleep 1
  done
  echo "Postgres is ready."
else
  echo "Postgres assumed running (no docker compose or already up)."
fi

# Start FastAPI server if not already running
if ! curl -sf "${BASE_URL}/health" >/dev/null 2>&1; then
  echo "Starting FastAPI server..."
  cd "$SCRIPT_DIR"
  python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000 &
  SERVER_PID=$!
  echo "Waiting for server (PID $SERVER_PID)..."
  for i in $(seq 1 30); do
    if curl -sf "${BASE_URL}/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  echo "Server is ready."
else
  echo "Server already running at ${BASE_URL}"
fi

# ---- Workflow JSON definitions ---------------------------------------------

# A) Simple linear: Start -> Set -> Code
read -r -d '' WF_LINEAR <<'WFJSON' || true
{
  "name": "Test Linear Workflow",
  "nodes": [
    {
      "name": "Start",
      "type": "Start",
      "parameters": {},
      "position": {"x": 0, "y": 0}
    },
    {
      "name": "Set Data",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "greeting", "value": "hello world"},
          {"name": "count", "value": 42, "type": "number"}
        ]
      },
      "position": {"x": 200, "y": 0}
    },
    {
      "name": "Transform",
      "type": "Code",
      "parameters": {
        "code": "return [{\"json\": {\"greeting\": item[\"json\"][\"greeting\"].upper(), \"doubled\": item[\"json\"][\"count\"] * 2}} for item in items]"
      },
      "position": {"x": 400, "y": 0}
    }
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Set Data"},
    {"source_node": "Set Data", "target_node": "Transform"}
  ]
}
WFJSON

# B) Branching: Start -> Set (score:85) -> If (>=70) -> true/false
read -r -d '' WF_BRANCH <<'WFJSON' || true
{
  "name": "Test Branching Workflow",
  "nodes": [
    {
      "name": "Start",
      "type": "Start",
      "parameters": {},
      "position": {"x": 0, "y": 0}
    },
    {
      "name": "Set Score",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "score", "value": 85, "type": "number"},
          {"name": "student", "value": "Alice"}
        ]
      },
      "position": {"x": 200, "y": 0}
    },
    {
      "name": "Check Score",
      "type": "If",
      "parameters": {
        "field": "score",
        "operation": "gte",
        "value": 70
      },
      "position": {"x": 400, "y": 0}
    },
    {
      "name": "Passed",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "result", "value": "passed"}
        ]
      },
      "position": {"x": 600, "y": -100}
    },
    {
      "name": "Failed",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "result", "value": "failed"}
        ]
      },
      "position": {"x": 600, "y": 100}
    }
  ],
  "connections": [
    {"source_node": "Start", "target_node": "Set Score"},
    {"source_node": "Set Score", "target_node": "Check Score"},
    {"source_node": "Check Score", "target_node": "Passed", "source_output": "true"},
    {"source_node": "Check Score", "target_node": "Failed", "source_output": "false"}
  ]
}
WFJSON

# C) Webhook: Webhook (POST, path:test-hook, responseMode:lastNode) -> Set -> RespondToWebhook
read -r -d '' WF_WEBHOOK <<'WFJSON' || true
{
  "name": "Test Webhook Workflow",
  "nodes": [
    {
      "name": "Webhook",
      "type": "Webhook",
      "parameters": {
        "method": "POST",
        "path": "test-hook",
        "responseMode": "lastNode"
      },
      "position": {"x": 0, "y": 0}
    },
    {
      "name": "Process",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "received", "value": true, "type": "boolean"},
          {"name": "message", "value": "webhook processed"}
        ]
      },
      "position": {"x": 200, "y": 0}
    },
    {
      "name": "Respond",
      "type": "RespondToWebhook",
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{$json}}"
      },
      "position": {"x": 400, "y": 0}
    }
  ],
  "connections": [
    {"source_node": "Webhook", "target_node": "Process"},
    {"source_node": "Process", "target_node": "Respond"}
  ]
}
WFJSON

# D) Single Start node (no connections)
read -r -d '' WF_SINGLE <<'WFJSON' || true
{
  "name": "Test Single Node",
  "nodes": [
    {
      "name": "Start",
      "type": "Start",
      "parameters": {},
      "position": {"x": 0, "y": 0}
    }
  ],
  "connections": []
}
WFJSON

# E) GET webhook for test 66
read -r -d '' WF_GET_WEBHOOK <<'WFJSON' || true
{
  "name": "Test GET Webhook",
  "nodes": [
    {
      "name": "Webhook",
      "type": "Webhook",
      "parameters": {
        "method": "GET",
        "path": "get-test-hook",
        "responseMode": "lastNode"
      },
      "position": {"x": 0, "y": 0}
    },
    {
      "name": "Process",
      "type": "Set",
      "parameters": {
        "mode": "manual",
        "fields": [
          {"name": "method", "value": "GET"},
          {"name": "status", "value": "ok"}
        ]
      },
      "position": {"x": 200, "y": 0}
    },
    {
      "name": "Respond",
      "type": "RespondToWebhook",
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{$json}}"
      },
      "position": {"x": 400, "y": 0}
    }
  ],
  "connections": [
    {"source_node": "Webhook", "target_node": "Process"},
    {"source_node": "Process", "target_node": "Respond"}
  ]
}
WFJSON

# =============================================================================
# Phase 0: Health
# =============================================================================
section "Phase 0: Infra + Health"

api_call GET "/"
assert_status "GET / — root" 200
assert_json "Root has status" ".status" "running"

api_call GET "/health"
assert_status "GET /health" 200
assert_json "Health is healthy" ".status" "healthy"

# =============================================================================
# Phase 1: Folders CRUD
# =============================================================================
section "Phase 1: Folders CRUD"

api_call POST "/api/folders" '{"name": "Test Parent Folder"}'
assert_status "Create parent folder" 201
PARENT_FOLDER_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call POST "/api/folders" "{\"name\": \"Test Child Folder\", \"parent_folder_id\": \"$PARENT_FOLDER_ID\"}"
assert_status "Create child folder" 201
CHILD_FOLDER_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call GET "/api/folders"
assert_status "List folders" 200
assert_json_gte "At least 2 folders" '. | length' 2

api_call GET "/api/folders/$PARENT_FOLDER_ID"
assert_status "Get parent folder" 200
assert_json "Parent folder name" ".name" "Test Parent Folder"

api_call PUT "/api/folders/$CHILD_FOLDER_ID" "{\"name\": \"Renamed Child\", \"parent_folder_id\": \"$PARENT_FOLDER_ID\"}"
assert_status "Rename child folder" 200
assert_json "Child renamed" ".name" "Renamed Child"

api_call PUT "/api/folders/$CHILD_FOLDER_ID" '{"name": "Renamed Child", "parent_folder_id": null}'
assert_status "Move child to root" 200

api_call GET "/api/folders/nonexistent-id-000"
assert_status "Get nonexistent folder → 404" 404

api_call DELETE "/api/folders/nonexistent-id-000"
assert_status "Delete nonexistent folder → 404" 404

# =============================================================================
# Phase 2: Variables CRUD
# =============================================================================
section "Phase 2: Variables CRUD"

api_call POST "/api/variables" '{"key": "API_KEY", "value": "sk-test-123", "type": "secret", "description": "Test API key"}'
assert_status "Create variable API_KEY" 201
VAR_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call POST "/api/variables" '{"key": "API_KEY", "value": "duplicate"}'
assert_status "Duplicate variable key → 409" 409

api_call GET "/api/variables"
assert_status "List variables" 200
assert_json_gte "At least 1 variable" '. | length' 1

api_call GET "/api/variables/$VAR_ID"
assert_status "Get variable by ID" 200
assert_json "Variable key is API_KEY" ".key" "API_KEY"

api_call PUT "/api/variables/$VAR_ID" '{"value": "sk-updated-456", "description": "Updated key"}'
assert_status "Update variable" 200
assert_json "Updated description" ".description" "Updated key"

api_call GET "/api/variables/99999"
assert_status "Get nonexistent variable → 404" 404

api_call DELETE "/api/variables/99999"
assert_status "Delete nonexistent variable → 404" 404

api_call POST "/api/variables" '{"key": "DB_HOST", "value": "localhost", "type": "string", "description": "Database host"}'
assert_status "Create variable DB_HOST" 201
VAR2_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

# =============================================================================
# Phase 3: Credentials CRUD
# =============================================================================
section "Phase 3: Credentials CRUD"

api_call POST "/api/credentials" '{"name": "Test Postgres", "type": "postgres", "data": {"host": "localhost", "port": 5432, "user": "test", "password": "secret"}}'
assert_status "Create credential" 201
CRED_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call GET "/api/credentials"
assert_status "List credentials" 200
assert_json_gte "At least 1 credential" '. | length' 1
# Verify data field is NOT exposed in list
CRED_HAS_DATA=$(echo "$RESPONSE_BODY" | jq '.[0] | has("data")')
TOTAL_COUNT=$((TOTAL_COUNT + 1))
if [[ "$CRED_HAS_DATA" == "false" ]]; then
  PASS_COUNT=$((PASS_COUNT + 1))
  echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} Credential list does not expose data"
else
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} Credential list exposes data field!"
fi

api_call GET "/api/credentials/$CRED_ID"
assert_status "Get credential by ID" 200
assert_json "Credential name" ".name" "Test Postgres"

api_call PUT "/api/credentials/$CRED_ID" '{"name": "Updated Postgres", "data": {"host": "db.example.com", "port": 5432}}'
assert_status "Update credential" 200
assert_json "Credential renamed" ".name" "Updated Postgres"

api_call GET "/api/credentials/nonexistent-cred-000"
assert_status "Get nonexistent credential → 404" 404

api_call DELETE "/api/credentials/nonexistent-cred-000"
assert_status "Delete nonexistent credential → 404" 404

api_call DELETE "/api/credentials/$CRED_ID"
assert_status "Delete credential" 200

# =============================================================================
# Phase 4: Nodes
# =============================================================================
section "Phase 4: Nodes"

api_call GET "/api/nodes"
assert_status "List all nodes" 200
assert_json_gte "Nodes array length > 0" '. | length' 1

api_call GET "/api/nodes?group=trigger"
assert_status "Filter nodes by group=trigger" 200

api_call GET "/api/nodes/Set"
assert_status "Get Set node schema" 200
assert_json "Node type is Set" ".type" "Set"

# =============================================================================
# Phase 5: Files
# =============================================================================
section "Phase 5: Files"

api_call GET "/api/files/browse"
assert_status "Browse home directory" 200
assert_json_not_null "Entries array exists" ".entries"

api_call GET "/api/files/browse?path=/tmp"
assert_status "Browse /tmp" 200

api_call GET "/api/files/validate?path=/etc/hosts"
assert_status "Validate /etc/hosts" 200
assert_json "Path is valid" ".valid" "true"

# =============================================================================
# Phase 6: Workflows + Execution
# =============================================================================
section "Phase 6: Workflows + Execution"

# --- Workflow 1: Simple linear ---
echo -e "  ${YELLOW}--- Workflow 1: Simple Linear ---${NC}"

api_call POST "/api/workflows" "$WF_LINEAR"
assert_status "Create linear workflow" 201
WF1_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call GET "/api/workflows"
assert_status "List workflows" 200
assert_json_gte "At least 1 workflow" '. | length' 1

api_call GET "/api/workflows/$WF1_ID"
assert_status "Get linear workflow" 200
WF1_NODE_COUNT=$(echo "$RESPONSE_BODY" | jq '.definition.nodes | length')
TOTAL_COUNT=$((TOTAL_COUNT + 1))
if [[ "$WF1_NODE_COUNT" == "3" ]]; then
  PASS_COUNT=$((PASS_COUNT + 1))
  echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} Linear workflow has 3 nodes"
else
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} Expected 3 nodes, got ${WF1_NODE_COUNT}"
fi

api_call PUT "/api/workflows/$WF1_ID" '{"name": "Renamed Linear Workflow"}'
assert_status "Rename workflow" 200
assert_json "Workflow renamed" ".name" "Renamed Linear Workflow"

api_call POST "/api/workflows/$WF1_ID/publish" '{"message": "v1 release"}'
assert_status "Publish v1" 200
VERSION_ID=$(echo "$RESPONSE_BODY" | jq -r '.version_id')
assert_json "Workflow is active" ".active" "true"

api_call GET "/api/workflows/$WF1_ID/versions"
assert_status "List versions" 200
assert_json_gte "At least 1 version" '. | length' 1

api_call GET "/api/workflows/$WF1_ID/versions/$VERSION_ID"
assert_status "Get version detail" 200
assert_json_not_null "Version has definition" ".definition"

api_call POST "/api/workflows/$WF1_ID/run"
assert_status "Run linear workflow" 200
EXEC1_ID=$(echo "$RESPONSE_BODY" | jq -r '.execution_id')
assert_json_not_null "Execution ID returned" ".execution_id"

api_call POST "/api/workflows/$WF1_ID/unpublish"
assert_status "Unpublish workflow" 200
assert_json "Workflow is inactive" ".active" "false"

# --- Workflow 2: Branching ---
echo -e "  ${YELLOW}--- Workflow 2: Branching ---${NC}"

api_call POST "/api/workflows" "$WF_BRANCH"
assert_status "Create branching workflow" 201
WF2_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call POST "/api/workflows/$WF2_ID/publish"
assert_status "Publish branching workflow" 200

api_call POST "/api/workflows/$WF2_ID/run"
assert_status "Run branching workflow" 200
EXEC2_ID=$(echo "$RESPONSE_BODY" | jq -r '.execution_id')
assert_json_not_null "Branch execution ID" ".execution_id"

# --- Workflow 3: In folder ---
echo -e "  ${YELLOW}--- Workflow 3: In Folder ---${NC}"

api_call POST "/api/workflows" "{\"name\": \"Folder Workflow\", \"nodes\": [{\"name\": \"Start\", \"type\": \"Start\", \"parameters\": {}}], \"connections\": [], \"folder_id\": \"$CHILD_FOLDER_ID\"}"
assert_status "Create workflow in folder" 201
WF3_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call GET "/api/workflows?folder_id=$CHILD_FOLDER_ID"
assert_status "List workflows in folder" 200
assert_json "Folder has 1 workflow" '. | length' "1"

# --- Workflow 4: Single node ---
echo -e "  ${YELLOW}--- Workflow 4: Single Node ---${NC}"

api_call POST "/api/workflows" "$WF_SINGLE"
assert_status "Create single-node workflow" 201
WF4_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

# --- Edge cases ---
echo -e "  ${YELLOW}--- Edge Cases ---${NC}"

api_call POST "/api/workflows" '{"name": "", "nodes": [{"name": "Start", "type": "Start", "parameters": {}}]}'
assert_status "Empty name → 422" 422

api_call GET "/api/workflows/nonexistent-wf-000"
assert_status "Get nonexistent workflow → 404" 404

api_call DELETE "/api/workflows/nonexistent-wf-000"
assert_status "Delete nonexistent workflow → 404" 404

# --- Ad-hoc + unpublished ---
echo -e "  ${YELLOW}--- Ad-hoc + Unpublished ---${NC}"

api_call POST "/api/workflows/run-adhoc" "$WF_LINEAR"
assert_status "Run ad-hoc workflow" 200
assert_json_not_null "Ad-hoc execution ID" ".execution_id"

api_call POST "/api/workflows/$WF1_ID/run"
assert_status "Run unpublished workflow" 200

# =============================================================================
# Phase 7: Executions
# =============================================================================
section "Phase 7: Executions"

api_call GET "/api/executions"
assert_status "List executions" 200
assert_json_gte "At least 2 executions" '. | length' 2

api_call GET "/api/executions?workflow_id=$WF1_ID"
assert_status "Filter executions by workflow" 200

api_call GET "/api/executions/$EXEC1_ID"
assert_status "Get execution detail" 200
assert_json_not_null "node_data is non-empty" ".node_data"

api_call GET "/api/executions/nonexistent-exec-000"
assert_status "Get nonexistent execution → 404" 404

api_call POST "/api/executions/$EXEC1_ID/cancel"
assert_status "Cancel completed execution → 400" 400

api_call POST "/api/executions/$EXEC1_ID/retry"
assert_status "Retry successful execution → 400" 400

api_call DELETE "/api/executions/$EXEC1_ID"
assert_status "Delete execution" 200

# =============================================================================
# Phase 8: Webhooks
# =============================================================================
section "Phase 8: Webhooks"

# --- Workflow 5: Webhook workflow ---
api_call POST "/api/workflows" "$WF_WEBHOOK"
assert_status "Create webhook workflow" 201
WF5_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call POST "/api/workflows/$WF5_ID/publish"
assert_status "Publish webhook workflow" 200

# Trigger by ID
api_call POST "/webhook/$WF5_ID" '{"test": "data"}'
assert_status "Trigger webhook by ID" 200

# Trigger by custom path
api_call POST "/webhook/p/test-hook" '{"test": "path-data"}'
assert_status "Trigger webhook by path" 200

# Wrong method (webhook is POST-only)
api_call GET "/webhook/$WF5_ID"
assert_status "Wrong method → 405" 405

# Nonexistent webhook
api_call POST "/webhook/nonexistent-wf-000"
assert_status "Nonexistent webhook by ID → 404" 404

api_call POST "/webhook/p/no-such-path"
assert_status "Nonexistent webhook by path → 404" 404

# --- GET webhook lifecycle ---
echo -e "  ${YELLOW}--- GET Webhook Lifecycle ---${NC}"

api_call POST "/api/workflows" "$WF_GET_WEBHOOK"
assert_status "Create GET webhook workflow" 201
WF6_ID=$(echo "$RESPONSE_BODY" | jq -r '.id')

api_call POST "/api/workflows/$WF6_ID/publish"
# Not counted as a numbered test, just setup
WF6_PUB_STATUS=$HTTP_STATUS

api_call GET "/webhook/p/get-test-hook"
assert_status "GET webhook by path" 200

# =============================================================================
# Phase 9: Streaming / SSE
# =============================================================================
section "Phase 9: Streaming / SSE"

# Ad-hoc SSE stream
SSE_BODY=$(curl -s -N --max-time 15 -X POST -H 'Content-Type: application/json' \
  -d "$WF_LINEAR" "${BASE_URL}/execution-stream/adhoc" 2>/dev/null || true)
TOTAL_COUNT=$((TOTAL_COUNT + 1))
if echo "$SSE_BODY" | grep -q "data:"; then
  PASS_COUNT=$((PASS_COUNT + 1))
  echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} SSE adhoc stream has data: lines"
else
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} SSE adhoc stream — no data: lines found"
  echo -e "    ${RED}Body: ${SSE_BODY:0:200}${NC}"
fi

# Stream saved workflow (POST with input)
SSE_BODY2=$(curl -s -N --max-time 15 -X POST -H 'Content-Type: application/json' \
  -d '{}' "${BASE_URL}/execution-stream/${WF2_ID}" 2>/dev/null || true)
TOTAL_COUNT=$((TOTAL_COUNT + 1))
if echo "$SSE_BODY2" | grep -q "data:"; then
  PASS_COUNT=$((PASS_COUNT + 1))
  echo -e "  ${GREEN}✓${NC} #${TOTAL_COUNT} SSE saved workflow stream has data: lines"
else
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo -e "  ${RED}✗${NC} #${TOTAL_COUNT} SSE saved workflow stream — no data: lines found"
  echo -e "    ${RED}Body: ${SSE_BODY2:0:200}${NC}"
fi

# =============================================================================
# Phase 10: Cleanup
# =============================================================================
section "Phase 10: Cleanup"

if [[ "$CLEANUP" == "0" ]]; then
  echo -e "  ${YELLOW}Skipping cleanup (CLEANUP=0)${NC}"
else
  # Delete executions first (some may already be deleted)
  if [[ -n "${EXEC2_ID:-}" ]]; then
    api_call DELETE "/api/executions/$EXEC2_ID"
    assert_status "Delete execution 2" 200
  fi

  # Delete remaining executions for clean state
  api_call DELETE "/api/executions"
  assert_status "Clear all executions" 200

  # Delete workflows
  for wf_id in "${WF6_ID:-}" "${WF5_ID:-}" "${WF4_ID:-}" "${WF3_ID:-}" "${WF2_ID:-}" "${WF1_ID:-}"; do
    if [[ -n "$wf_id" ]]; then
      api_call DELETE "/api/workflows/$wf_id"
      assert_status "Delete workflow $wf_id" 200
    fi
  done

  # Delete folders (child first if it still has parent)
  api_call DELETE "/api/folders/$CHILD_FOLDER_ID"
  assert_status "Delete child folder" 200

  api_call DELETE "/api/folders/$PARENT_FOLDER_ID"
  assert_status "Delete parent folder" 200

  # Delete variables
  api_call DELETE "/api/variables/$VAR_ID"
  assert_status "Delete variable API_KEY" 200

  api_call DELETE "/api/variables/$VAR2_ID"
  assert_status "Delete variable DB_HOST" 200
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${BOLD}═══════════════════════════════════════${NC}"
echo -e "${BOLD}  Test Summary${NC}"
echo -e "${BOLD}═══════════════════════════════════════${NC}"
echo -e "  Total:  ${TOTAL_COUNT}"
echo -e "  ${GREEN}Passed: ${PASS_COUNT}${NC}"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo -e "  ${RED}Failed: ${FAIL_COUNT}${NC}"
else
  echo -e "  Failed: 0"
fi
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo -e "${RED}${BOLD}SOME TESTS FAILED${NC}"
  exit 1
else
  echo -e "${GREEN}${BOLD}ALL TESTS PASSED${NC}"
  exit 0
fi
