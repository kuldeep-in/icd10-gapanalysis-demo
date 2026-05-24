#!/usr/bin/env bash
# deploy.sh — Full deployment script for icd10-gapanalysis-demo
#
# Sequence:
#   1. Deploy the Databricks App  → establishes the app service principal
#   2. Read the app SP client_id  → avoids hardcoding the UUID anywhere
#   3. Deploy the bundle          → creates Jobs and grants CAN_MANAGE_RUN to the app SP
#
# Usage:
#   ./deploy.sh --app-path <workspace-path-to-app-dir> \
#               [--profile <profile>] [--catalog <name>] [--schema <name>] \
#               [--warehouse <id>] [--model-provider anthropic|databricks]
#
#   --app-path   Workspace path to the app/ directory, e.g.
#                /Workspace/Users/you@example.com/icd10-gapanalysis-demo/app
#
# Defaults: profile=DEFAULT, catalog=my_catalog, model-provider=databricks

set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────
PROFILE="DEFAULT"
CATALOG="my_catalog"
SCHEMA="icd10_care_gap"
WAREHOUSE_ID="<your-warehouse-id>"
MODEL_PROVIDER="databricks"
APP_NAME="icd10-gap-advisor"
APP_SOURCE_PATH=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --profile)        PROFILE="$2";        shift 2 ;;
    --catalog)        CATALOG="$2";        shift 2 ;;
    --schema)         SCHEMA="$2";         shift 2 ;;
    --warehouse)      WAREHOUSE_ID="$2";   shift 2 ;;
    --model-provider) MODEL_PROVIDER="$2"; shift 2 ;;
    --app-path)       APP_SOURCE_PATH="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

if [ -z "$APP_SOURCE_PATH" ]; then
  echo "ERROR: --app-path is required."
  echo "  Provide the workspace path to the app/ directory, e.g.:"
  echo "  --app-path /Workspace/Users/you@example.com/icd10-gapanalysis-demo/app"
  exit 1
fi

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ICD-10 Gap Analysis Demo — Full Deployment"
echo "═══════════════════════════════════════════════════"
echo "  Profile:    $PROFILE"
echo "  Catalog:    $CATALOG"
echo "  Schema:     $SCHEMA"
echo "  Warehouse:  $WAREHOUSE_ID"
echo "  Model:      $MODEL_PROVIDER"
echo "  App source: $APP_SOURCE_PATH"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Step 1: Deploy the app first ─────────────────────────────────────────────
echo "▶ Step 1/3 — Deploy Databricks App (establishes app service principal)"
databricks apps deploy "$APP_NAME" \
  --source-code-path "$APP_SOURCE_PATH" \
  --mode SNAPSHOT \
  --profile="$PROFILE"
echo "✔ App deployed"
echo ""

# ── Step 2: Read the app SP client_id ────────────────────────────────────────
echo "▶ Step 2/3 — Reading app service principal identity"
APP_SP_CLIENT_ID=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['service_principal_client_id'])"
)
APP_SP_NAME=$(
  databricks apps get "$APP_NAME" --profile="$PROFILE" -o json \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['service_principal_name'])"
)
echo "  App SP client_id : $APP_SP_CLIENT_ID"
echo "  App SP name      : $APP_SP_NAME"
echo "✔ Identity resolved"
echo ""

# ── Step 2b: Grant warehouse CAN_USE to app SP ───────────────────────────────
# app.yaml resources block does NOT automatically apply warehouse ACLs —
# this explicit grant is required so the app can execute SQL queries.
echo "▶ Step 2b/3 — Granting warehouse CAN_USE to app SP"
databricks permissions update warehouses "$WAREHOUSE_ID" \
  --profile="$PROFILE" \
  --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP_CLIENT_ID\", \"permission_level\": \"CAN_USE\"}]}"
echo "✔ Warehouse CAN_USE granted to $APP_SP_CLIENT_ID"
echo ""

# ── Step 3: Deploy the bundle with the app SP ─────────────────────────────────
echo "▶ Step 3/3 — Deploy bundle (creates Jobs + grants CAN_MANAGE_RUN to app SP)"
DATABRICKS_TF_EXEC_PATH=$(which terraform) \
DATABRICKS_TF_VERSION=1.15.2 \
databricks bundle deploy \
  --var="app_service_principal=$APP_SP_CLIENT_ID" \
  --var="catalog=$CATALOG" \
  --var="schema=$SCHEMA" \
  --var="warehouse_id=$WAREHOUSE_ID" \
  --var="model_provider=$MODEL_PROVIDER" \
  --profile="$PROFILE"
echo "✔ Bundle deployed"
echo ""

echo "═══════════════════════════════════════════════════"
echo "  Deployment complete!"
echo ""
echo "  Resources created:"
echo "  • App: $APP_NAME"
echo "    (source: $APP_SOURCE_PATH)"
echo "    (CAN_USE on warehouse $WAREHOUSE_ID)"
echo "  • Job 1: ICD-10 Gap Demo — Data Setup"
echo "    (CAN_MANAGE_RUN → $APP_SP_CLIENT_ID)"
echo "  • Job 2: ICD-10 Gap Demo — AI Setup"
echo "    (CAN_MANAGE_RUN → $APP_SP_CLIENT_ID)"
echo ""
echo "  Next steps:"
echo "  1. Open the app and run Job 1 (Data Setup)"
echo "  2. Run Job 2 (AI Setup) after Job 1 completes"
echo "═══════════════════════════════════════════════════"
