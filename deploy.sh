#!/usr/bin/env bash
# deploy.sh — Full deployment for icd10-gapanalysis-demo
#
# databricks.yml is the single source of truth for all config variables.
# This script reads defaults from databricks.yml — no values are hardcoded here.
# Command-line flags override those defaults when provided.
# The workspace app/ path is derived automatically from databricks.yml via
# 'databricks bundle validate' — no --app-path flag required.
#
# Sequence:
#   1. Read variable defaults from databricks.yml
#   2. Derive workspace app path from bundle configuration
#   3. setup_resources.py  — resolve / create SQL warehouse + KA + VS endpoints
#   4. Generate app.yaml   — write all env vars from resolved values
#   5. Deploy app          — establishes app service principal
#   6. Grant permissions   — warehouse, KA, VS endpoints to app SP
#   7. Sync setup notebooks — pull from workspace so bundle manages them correctly
#   8. Bundle deploy        — creates Jobs with all vars from single source
#
# Usage:
#   ./deploy.sh [overrides]
#
# Overrides (all optional — defaults come from databricks.yml):
#   --profile           Databricks CLI profile
#   --catalog           Unity Catalog name
#   --schema            Schema name
#   --warehouse-id      SQL warehouse ID         (auto-created if omitted)
#   --ka-display-name   KA display name          (created if not found)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Step 1: Read all variable defaults from databricks.yml ───────────────────
# databricks.yml is the single source of truth — no defaults hardcoded here.
echo "Reading config from databricks.yml..."
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

# Deployment mechanics — not bundle variables, not in databricks.yml
PROFILE="DEFAULT"
APP_NAME="icd10-gap-advisor"

# ── Command-line overrides ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --profile)           PROFILE="$2";           shift 2 ;;
    --catalog)           CATALOG="$2";           shift 2 ;;
    --schema)            SCHEMA="$2";            shift 2 ;;
    --warehouse-id)      WAREHOUSE_ID="$2";      shift 2 ;;
    --ka-display-name)   KA_DISPLAY_NAME="$2";   shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Step 2: Derive workspace app path from bundle configuration ───────────────
# databricks bundle validate resolves workspace.file_path from databricks.yml.
# APP_SOURCE_PATH is always <workspace_root>/app — no manual flag required.
echo "Resolving workspace app path from bundle..."
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
echo "  App path: $APP_SOURCE_PATH"
echo ""
echo "═══════════════════════════════════════════════════"
echo "  ICD-10 Gap Analysis Demo — Deployment"
echo "═══════════════════════════════════════════════════"
echo "  Profile:       $PROFILE"
echo "  Catalog:       $CATALOG / $SCHEMA"
echo "  App source:    $APP_SOURCE_PATH"
echo "  KA name:       $KA_DISPLAY_NAME"
echo "  VS endpoint:   $VS_ENDPOINT_NAME"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Step 2: Resolve SQL warehouse, KA endpoint, VS endpoint ──────────────────
echo "▶ Step 3/7 — Resolving SQL warehouse, KA endpoint, and VS endpoint"
_resource_vars=$(python3 "${SCRIPT_DIR}/setup_resources.py" \
    --profile           "$PROFILE" \
    --catalog           "$CATALOG" \
    --schema            "$SCHEMA" \
    --warehouse-id      "$WAREHOUSE_ID" \
    --ka-display-name   "$KA_DISPLAY_NAME" \
    --vs-endpoint-name  "$VS_ENDPOINT_NAME")
eval "$_resource_vars"

echo "  Warehouse:    $WAREHOUSE_ID"
echo "  KA name:      $KA_DISPLAY_NAME"
echo "  KA endpoint:  $KA_ENDPOINT_NAME"
echo "  VS endpoint:  $VS_ENDPOINT_NAME"
echo "✔ Resources resolved"
echo ""

# ── Step 3: Generate app.yaml from resolved values ────────────────────────────
# All variables from databricks.yml are reflected as env vars in the app.
echo "▶ Step 4/7 — Generating app.yaml"
TEMP_APP_YAML="$(mktemp /tmp/icd10_app_yaml.XXXXXX)"

cat > "$TEMP_APP_YAML" << APPCFG
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

resources:
  - name: sql-warehouse
    sql_warehouse:
      id: "${WAREHOUSE_ID}"
      permission: "CAN_USE"
APPCFG

databricks workspace import "${APP_SOURCE_PATH}/app.yaml" \
  --file "$TEMP_APP_YAML" \
  --format AUTO \
  --overwrite \
  --profile="$PROFILE"

# Keep a local copy alongside deploy.sh so the project directory stays in sync
mkdir -p "${SCRIPT_DIR}/app"
cp "$TEMP_APP_YAML" "${SCRIPT_DIR}/app/app.yaml"

rm -f "$TEMP_APP_YAML"
echo "✔ app.yaml generated and uploaded (local copy saved to app/app.yaml)"
echo ""

# ── Step 4: Deploy the app ────────────────────────────────────────────────────
echo "▶ Step 5/7 — Deploying Databricks App"
databricks apps deploy "$APP_NAME" \
  --source-code-path "$APP_SOURCE_PATH" \
  --mode SNAPSHOT \
  --profile="$PROFILE"
echo "✔ App deployed"
echo ""

# ── Step 5: Read app SP + grant permissions ───────────────────────────────────
echo "▶ Step 6/7 — Granting permissions to app service principal"
APP_SP_CLIENT_ID=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["service_principal_client_id"])'
)
APP_SP_NAME=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["service_principal_name"])'
)
echo "  App SP: $APP_SP_NAME ($APP_SP_CLIENT_ID)"

databricks permissions update warehouses "$WAREHOUSE_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_USE\"}]}" \
  > /dev/null
echo "  ✔ Warehouse CAN_USE granted"

KA_ENDPOINT_ID=$(
  databricks serving-endpoints get "$KA_ENDPOINT_NAME" --profile="$PROFILE" -o json \
  | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])'
)
databricks permissions update serving-endpoints "$KA_ENDPOINT_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_QUERY\"}]}" \
  > /dev/null
echo "  ✔ KA serving endpoint CAN_QUERY granted ($KA_ENDPOINT_NAME)"

KA_ID="${KA_NAME#knowledge-assistants/}"
databricks permissions update knowledge-assistants "$KA_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_QUERY\"}]}" \
  > /dev/null
echo "  ✔ KA resource CAN_QUERY granted (knowledge-assistants/$KA_ID)"

VS_ENDPOINT_ID=$(
  databricks api get "/api/2.0/vector-search/endpoints/${VS_ENDPOINT_NAME}" \
    --profile="$PROFILE" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))"
)
databricks api patch "/api/2.0/permissions/vector-search-endpoints/${VS_ENDPOINT_ID}" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_USE\"}]}" \
  > /dev/null
echo "  ✔ VS endpoint CAN_USE granted ($VS_ENDPOINT_NAME)"
echo "✔ Permissions granted"
echo ""

# ── Step 7: Sync setup notebooks for bundle deploy ───────────────────────────
# Pull the 7 setup notebooks from the workspace into the local directory so
# the bundle sync includes them — prevents deletion and keeps them managed by
# the bundle. Only the setup notebooks are needed; app/, data/ are excluded.
echo "▶ Step 7/8 — Syncing setup notebooks for bundle"
mkdir -p "${SCRIPT_DIR}/setup"
NOTEBOOKS_SYNCED=0
for nb in 01_create_catalog 02_setup_care_gap_rules 02_ingest_patient_json \
           03_load_icd10_pdfs_to_volume 04_configure_knowledge_source \
           05_configure_ai_gateway 06_create_care_gap_vs_index; do
  if databricks workspace export "${WORKSPACE_FILE_PATH}/setup/${nb}" \
       --format SOURCE --file "${SCRIPT_DIR}/setup/${nb}.py" \
       --profile="$PROFILE" 2>/dev/null; then
    NOTEBOOKS_SYNCED=$((NOTEBOOKS_SYNCED + 1))
  else
    echo "  ⚠ ${nb} not found in workspace — skipping"
  fi
done
echo "✔ ${NOTEBOOKS_SYNCED} setup notebooks synced to local directory"
echo ""

# ── Step 8: Bundle deploy (Jobs) ──────────────────────────────────────────────
# All variable values flow from databricks.yml defaults + step 3 resolution.
echo "▶ Step 8/8 — Deploying bundle (Jobs)"
DATABRICKS_TF_EXEC_PATH=$(which terraform) DATABRICKS_TF_VERSION=1.15.2 \
databricks bundle deploy \
  --var="catalog=$CATALOG" \
  --var="schema=$SCHEMA" \
  --var="warehouse_id=$WAREHOUSE_ID" \
  --var="fmapi_endpoint=$FMAPI_ENDPOINT" \
  --var="ka_endpoint_name=$KA_ENDPOINT_NAME" \
  --var="ka_name=$KA_NAME" \
  --var="data_setup_job_name=$DATA_SETUP_JOB_NAME" \
  --var="ai_setup_job_name=$AI_SETUP_JOB_NAME" \
  --var="app_service_principal=$APP_SP_CLIENT_ID" \
  --var="vs_endpoint_name=$VS_ENDPOINT_NAME" \
  --profile="$PROFILE"
echo "✔ Bundle deployed"
echo ""

echo "═══════════════════════════════════════════════════"
echo "  Deployment complete!"
echo ""
echo "  App:         $APP_NAME"
echo "  Warehouse:   $WAREHOUSE_ID"
echo "  KA endpoint: $KA_ENDPOINT_NAME"
echo "  VS endpoint: $VS_ENDPOINT_NAME"
echo "  Care Gap:    databricks-claude-sonnet-4-6 (FMAPI)"
echo ""
echo "  Next steps:"
echo "  1. Open the app → run Job 1 (Data Setup)"
echo "  2. Run Job 2 (Knowledge Assistant Setup) after Job 1 completes"
echo "═══════════════════════════════════════════════════"
