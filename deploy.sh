#!/usr/bin/env bash
# deploy.sh — Full deployment for icd10-gapanalysis-demo
#
# Git is the single source of truth for all files.
# databricks.yml is the single source of truth for all config variables.
#
# Two-phase deployment:
#   Phase 1 — Bootstrap
#   1. Read variable defaults from databricks.yml
#   2. Derive workspace file path from bundle configuration
#   3. setup_resources.py  — resolve / create SQL warehouse + KA + VS endpoints
#   4. Generate app.yaml   — written to app/app.yaml (synced to workspace by Step 5)
#   5. File sync           — databricks sync --full (files only, no job creation)
#   6. Deploy app          — create if needed, wait for ACTIVE, then apps deploy
#
#   Phase 2 — Finalise
#   7. Read app SP + grant ALL permissions:
#        Infrastructure: warehouse · KA · VS endpoint · Genie Space
#        Unity Catalog:  schema · tables · volume · SELECT/MODIFY grants  (setup_schema.py)
#   8. DAB deploy          — creates jobs WITH app SP permissions from workflows.yml
#
# Job names: [dev <username>] <data_setup_job_name | ai_setup_job_name>
#
# Usage:
#   ./deploy.sh [--profile <profile>] [--catalog <name>] [--schema <name>]
#               [--warehouse-name <name>] [--ka-display-name <name>]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_START=$(date +%s)

# ── Logging helpers ───────────────────────────────────────────────────────────
_log()     { echo "  $*"; }
_ok()      { echo "  ✔ $*"; }
_section() { echo ""; echo "──────────────────────────────────────────────────"; echo "  $*"; echo "──────────────────────────────────────────────────"; }
_elapsed() { echo "  ⏱  $(( $(date +%s) - DEPLOY_START ))s elapsed"; }

# ── Step 1: Read all variable defaults from databricks.yml ───────────────────
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   ICD-10 Gap Analysis Demo — Deploying...        ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
_log "Reading config from databricks.yml..."
_py=$(mktemp /tmp/yaml_parser_XXXXXX.py)
cat > "$_py" << 'PYEOF'
import re, sys

with open("databricks.yml") as f:
    content = f.read()

current_var = None
for line in content.splitlines():
    m = re.match(r'^  (\w+):', line)
    if m:
        current_var = m.group(1)
    if current_var:
        m2 = re.match(r'^\s+default:\s*["\']?(.*?)["\']?\s*$', line)
        if m2:
            val = m2.group(1).replace('"', '\\"')
            print('%s="%s"' % (current_var.upper(), val))
            current_var = None
PYEOF
_yaml_vars=$(python3 "$_py")
rm -f "$_py"
eval "$_yaml_vars"

PROFILE="DEFAULT"

# ── Command-line overrides ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --profile)           PROFILE="$2";           shift 2 ;;
    --catalog)           CATALOG="$2";           shift 2 ;;
    --schema)            SCHEMA="$2";            shift 2 ;;
    --warehouse-name)    WAREHOUSE_NAME="$2";    shift 2 ;;
    --ka-display-name)   KA_DISPLAY_NAME="$2";   shift 2 ;;
    --genie-space-name)  GENIE_SPACE_NAME="$2";  shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Step 2: Derive workspace file path from bundle configuration ──────────────
_log "Resolving workspace path from bundle..."
WORKSPACE_FILE_PATH=$(
  cd "$SCRIPT_DIR" && \
  databricks bundle validate --profile="$PROFILE" --output=json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('workspace',{}).get('file_path',''))"
)
if [ -z "$WORKSPACE_FILE_PATH" ]; then
  echo "ERROR: Could not resolve workspace.file_path from databricks.yml."
  echo "  Ensure you are authenticated and workspace.host is set in databricks.yml."
  exit 1
fi
APP_SOURCE_PATH="${WORKSPACE_FILE_PATH}/app"
_ok "Workspace path resolved"

echo ""
echo "  Profile:       $PROFILE"
echo "  Catalog:       $CATALOG.$SCHEMA"
echo "  Warehouse:     $WAREHOUSE_NAME"
echo "  KA:            $KA_DISPLAY_NAME"
echo "  VS endpoint:   $VS_ENDPOINT_NAME"
echo "  App:           $APP_NAME"
echo "  App source:    $APP_SOURCE_PATH"
echo ""

# ════════════════════════════════════════════════════
_section "Phase 1 — Bootstrap"
# ════════════════════════════════════════════════════

# ── Step 3: Resolve SQL warehouse, KA endpoint, VS endpoint ──────────────────
echo ""
echo "▶ Step 3/8 — Resolving infrastructure (warehouse · KA · VS endpoint)"
_resource_vars=$(python3 "${SCRIPT_DIR}/setup_resources.py" \
    --profile           "$PROFILE" \
    --catalog           "$CATALOG" \
    --schema            "$SCHEMA" \
    --warehouse-name    "$WAREHOUSE_NAME" \
    --ka-display-name   "$KA_DISPLAY_NAME" \
    --vs-endpoint-name  "$VS_ENDPOINT_NAME" \
    --genie-space-name  "$GENIE_SPACE_NAME")
eval "$_resource_vars"

echo ""
_ok "Warehouse ID:   $WAREHOUSE_ID"
_ok "KA endpoint:    $KA_ENDPOINT_NAME"
_ok "VS endpoint:    $VS_ENDPOINT_NAME"
_ok "Genie Space:    $GENIE_SPACE_ID"
_elapsed
echo ""

# ── Step 4: Generate app.yaml ─────────────────────────────────────────────────
echo "▶ Step 4/8 — Generating app.yaml"
mkdir -p "${SCRIPT_DIR}/app"
cat > "${SCRIPT_DIR}/app/app.yaml" << APPCFG
command: ["python", "app.py"]

# Generated by deploy.sh — do not edit manually.
# Source of truth: databricks.yml variables.

env:
  - name: UC_CATALOG
    value: "${CATALOG}"
  - name: UC_SCHEMA
    value: "${SCHEMA}"
  - name: DATABRICKS_WAREHOUSE_ID
    value: "${WAREHOUSE_ID}"
  - name: FMAPI_ENDPOINT
    value: "${FMAPI_ENDPOINT}"
  - name: KA_ENDPOINT_NAME
    value: "${KA_ENDPOINT_NAME}"
  - name: KA_NAME
    value: "${KA_NAME}"
  - name: DATA_SETUP_JOB_NAME
    value: "${DATA_SETUP_JOB_NAME}"
  - name: AI_SETUP_JOB_NAME
    value: "${AI_SETUP_JOB_NAME}"
  - name: VS_ENDPOINT_NAME
    value: "${VS_ENDPOINT_NAME}"
  - name: GENIE_SPACE_ID
    value: "${GENIE_SPACE_ID}"

resources:
  - name: sql-warehouse
    sql_warehouse:
      id: "${WAREHOUSE_ID}"
      permission: "CAN_USE"
APPCFG
_ok "app.yaml written with resolved values"
echo ""

# ── Step 5: File sync — push all files to workspace (no job creation) ────────
echo "▶ Step 5/8 — Syncing all files to workspace"
_log "Pushing app/, setup/, resources/, deploy.sh → $WORKSPACE_FILE_PATH"
databricks sync --full --profile="$PROFILE" \
  --exclude "data/**" --exclude "*.md" --exclude ".gitignore" --exclude "local.yml" \
  "$SCRIPT_DIR" "$WORKSPACE_FILE_PATH"
_ok "All files synced to workspace"
_elapsed
echo ""

# ── Step 6: Create (if needed) + deploy the app ───────────────────────────────
echo "▶ Step 6/8 — Deploying Databricks App: $APP_NAME"
if ! databricks apps get "$APP_NAME" --profile="$PROFILE" --output=json > /dev/null 2>&1; then
  _log "App does not exist — creating (this may take ~2 min)..."
  databricks apps create "$APP_NAME" --profile="$PROFILE"
  _ok "App created and compute ACTIVE"
else
  APP_COMPUTE_STATE=$(databricks apps get "$APP_NAME" --profile="$PROFILE" --output=json \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("compute_status",{}).get("state",""))')
  _log "App exists — compute state: $APP_COMPUTE_STATE"
  if [ "$APP_COMPUTE_STATE" = "STOPPED" ]; then
    _log "Starting app..."
    databricks apps start "$APP_NAME" --profile="$PROFILE"
    _ok "App started"
  elif [ "$APP_COMPUTE_STATE" != "ACTIVE" ]; then
    _log "Waiting for app compute to become ACTIVE..."
    for i in $(seq 1 24); do
      sleep 10
      APP_COMPUTE_STATE=$(databricks apps get "$APP_NAME" --profile="$PROFILE" --output=json \
        | python3 -c 'import sys,json; print(json.load(sys.stdin).get("compute_status",{}).get("state",""))')
      _log "[${i}0s] compute state: $APP_COMPUTE_STATE"
      [ "$APP_COMPUTE_STATE" = "ACTIVE" ] && break
    done
    if [ "$APP_COMPUTE_STATE" != "ACTIVE" ]; then
      echo "ERROR: App compute did not reach ACTIVE within 240s"
      exit 1
    fi
    _ok "App compute ACTIVE"
  fi
fi

_log "Deploying source code snapshot..."
databricks apps deploy "$APP_NAME" \
  --source-code-path "$APP_SOURCE_PATH" \
  --mode SNAPSHOT \
  --profile="$PROFILE" \
  --output=json 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  ✔ Deployment {d[\"deployment_id\"][:12]}... — {d[\"status\"][\"state\"]}')" \
  || databricks apps deploy "$APP_NAME" \
       --source-code-path "$APP_SOURCE_PATH" \
       --mode SNAPSHOT \
       --profile="$PROFILE"
_elapsed
echo ""

# ════════════════════════════════════════════════════
_section "Phase 2 — Finalise"
# ════════════════════════════════════════════════════

# ── Step 7: Read app SP + grant permissions ───────────────────────────────────
echo ""
echo "▶ Step 7/8 — Granting permissions to app service principal"
_log "Reading app service principal..."
APP_SP_CLIENT_ID=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["service_principal_client_id"])'
)
APP_SP_NAME=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["service_principal_name"])'
)
_ok "App SP: $APP_SP_NAME"

_log "Granting warehouse CAN_USE..."
databricks permissions update warehouses "$WAREHOUSE_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_USE\"}]}" \
  > /dev/null
_ok "Warehouse     → CAN_USE"

_log "Granting KA serving endpoint CAN_QUERY..."
KA_ENDPOINT_ID=$(
  databricks serving-endpoints get "$KA_ENDPOINT_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])'
)
databricks permissions update serving-endpoints "$KA_ENDPOINT_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_QUERY\"}]}" \
  > /dev/null
_ok "KA endpoint   → CAN_QUERY ($KA_ENDPOINT_NAME)"

_log "Granting KA resource CAN_QUERY..."
KA_ID="${KA_NAME#knowledge-assistants/}"
databricks permissions update knowledge-assistants "$KA_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_QUERY\"}]}" \
  > /dev/null
_ok "KA resource   → CAN_QUERY"

_log "Granting VS endpoint CAN_USE..."
VS_ENDPOINT_ID=$(
  databricks api get "/api/2.0/vector-search/endpoints/${VS_ENDPOINT_NAME}" \
    --profile="$PROFILE" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))"
)
databricks api patch "/api/2.0/permissions/vector-search-endpoints/${VS_ENDPOINT_ID}" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_USE\"}]}" \
  > /dev/null
_ok "VS endpoint   → CAN_USE ($VS_ENDPOINT_NAME)"

_log "Granting Genie Space CAN_RUN..."
databricks permissions update genie "$GENIE_SPACE_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_RUN\"}]}" \
  > /dev/null
_ok "Genie Space   → CAN_RUN ($GENIE_SPACE_ID)"

# ── Unity Catalog: create schema + tables + grant UC permissions ──────────────
_log "Creating schema, tables and granting Unity Catalog permissions..."
python3 "${SCRIPT_DIR}/setup_schema.py" \
    --profile      "$PROFILE" \
    --catalog      "$CATALOG" \
    --schema       "$SCHEMA" \
    --warehouse-id "$WAREHOUSE_ID" \
    --app-sp-id    "$APP_SP_CLIENT_ID"
_ok "Schema, tables and UC grants complete"

_log "Job permissions will be set by DAB deploy in Step 8"
_elapsed
echo ""

# ── Step 8: DAB deploy — create jobs with app SP permissions ─────────────────
echo "▶ Step 8/8 — DAB deploy (create Databricks Jobs with app SP permissions)"
_log "Deploying bundle resources: Job 1 (Data Setup) + Job 2 (KA Setup)..."
DATABRICKS_TF_EXEC_PATH=$(which terraform) DATABRICKS_TF_VERSION=1.15.2 \
databricks bundle deploy \
  --var="catalog=$CATALOG" \
  --var="schema=$SCHEMA" \
  --var="fmapi_endpoint=$FMAPI_ENDPOINT" \
  --var="ka_endpoint_name=$KA_ENDPOINT_NAME" \
  --var="ka_name=$KA_NAME" \
  --var="data_setup_job_name=$DATA_SETUP_JOB_NAME" \
  --var="ai_setup_job_name=$AI_SETUP_JOB_NAME" \
  --var="app_service_principal=$APP_SP_CLIENT_ID" \
  --var="vs_endpoint_name=$VS_ENDPOINT_NAME" \
  --var="genie_space_id=$GENIE_SPACE_ID" \
  --profile="$PROFILE"
_ok "Jobs created with app SP permissions"
_elapsed
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
BUNDLE_TARGET="dev"
CURRENT_USER_SHORT=$(databricks current-user me --profile="$PROFILE" --output=json \
  | python3 -c 'import sys,json; u=json.load(sys.stdin).get("userName",""); print(u.split("@")[0].replace(".","_"))')
TOTAL_TIME=$(( $(date +%s) - DEPLOY_START ))

echo "╔══════════════════════════════════════════════════╗"
echo "║   Deployment Complete  ✔                         ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "  App URL:     $(databricks apps get "$APP_NAME" --profile="$PROFILE" --output=json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("url","(check Databricks Apps UI)"))' 2>/dev/null || echo '(check Databricks Apps UI)')"
echo "  App:         $APP_NAME"
echo "  Warehouse:   $WAREHOUSE_NAME ($WAREHOUSE_ID)"
echo "  KA endpoint: $KA_ENDPOINT_NAME"
echo "  VS endpoint: $VS_ENDPOINT_NAME"
echo "  Job 1:       [$BUNDLE_TARGET $CURRENT_USER_SHORT] $DATA_SETUP_JOB_NAME"
echo "  Job 2:       [$BUNDLE_TARGET $CURRENT_USER_SHORT] $AI_SETUP_JOB_NAME"
echo ""
echo "  Total time:  ${TOTAL_TIME}s"
echo ""
echo "  Next steps:"
echo "  1. Open the app URL → navigate to Setup"
echo "  2. Run Job 1 (Data Setup)"
echo "  3. Run Job 2 (KA Setup) after Job 1 completes"
echo ""
