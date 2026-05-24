# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 5 — Configure AI Gateway Route
# MAGIC Configures the care gap model endpoint. By default uses the built-in
# MAGIC `databricks-claude-sonnet-4-6` Foundation Model API (no external key needed).
# MAGIC Optionally creates a Databricks AI Gateway route backed by an external Anthropic key.
# MAGIC
# MAGIC **Prerequisites (Anthropic mode only):**
# MAGIC - A Databricks secret scope must exist with key `anthropic-api-key`.
# MAGIC   Pass the scope name via the `secret_scope` widget (default: `care-gap-demo`).
# MAGIC   OR leave `model_provider` as `databricks` to use the Foundation Model API (recommended).
# MAGIC
# MAGIC To create the secret scope:
# MAGIC   `databricks secrets create-scope <scope-name>`
# MAGIC   `databricks secrets put-secret <scope-name> anthropic-api-key --string-value <key>`

# COMMAND ----------

import json

dbutils.widgets.text("catalog",         "my_catalog")
dbutils.widgets.text("schema",          "icd10_care_gap")
dbutils.widgets.text("ai_gateway_route","care-gap-advisor")
dbutils.widgets.text("model_provider",  "anthropic")
dbutils.widgets.text("secret_scope",    "care-gap-demo")

CATALOG        = dbutils.widgets.get("catalog")
SCHEMA         = dbutils.widgets.get("schema")
ROUTE_NAME     = dbutils.widgets.get("ai_gateway_route")
MODEL_PROVIDER = dbutils.widgets.get("model_provider")
SECRET_SCOPE   = dbutils.widgets.get("secret_scope")

# COMMAND ----------

# Idempotency check
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.`{SCHEMA}`.bootstrap_status
    WHERE step = 'configure_ai_gateway' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    print("AI Gateway route already configured — skipping")
    dbutils.notebook.exit("SKIPPED")

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import (
    EndpointCoreConfigInput,
    ServedEntityInput,
    ExternalModel,
    ExternalModelProvider,
    AnthropicConfig,
    AiGatewayConfig,
    AiGatewayUsageTrackingConfig,
    AiGatewayInferenceTableConfig,
)

w = WorkspaceClient()

try:
    w.serving_endpoints.get(name=ROUTE_NAME)
    print(f"Endpoint '{ROUTE_NAME}' already exists — skipping creation")
    endpoint_exists = True
except Exception:
    endpoint_exists = False

# COMMAND ----------

FMAPI_FALLBACK = "databricks-claude-sonnet-4-6"

if not endpoint_exists:
    if MODEL_PROVIDER == "anthropic":
        try:
            api_key = dbutils.secrets.get(scope=SECRET_SCOPE, key="anthropic-api-key")
        except Exception:
            print(f"WARNING: Secret '{SECRET_SCOPE}/anthropic-api-key' not found.")
            print(f"Falling back to Foundation Model API endpoint: {FMAPI_FALLBACK}")
            MODEL_PROVIDER = "fmapi_fallback"

    if MODEL_PROVIDER == "anthropic":
        served_entity = ServedEntityInput(
            external_model=ExternalModel(
                name="claude-3-5-sonnet-20241022",
                provider=ExternalModelProvider.ANTHROPIC,
                task="llm/v1/chat",
                anthropic_config=AnthropicConfig(
                    anthropic_api_key=dbutils.secrets.get(scope=SECRET_SCOPE, key="anthropic-api-key"),
                ),
            )
        )
        model_label = "claude-3-5-sonnet-20241022 (Anthropic)"

        print(f"Creating AI Gateway route '{ROUTE_NAME}' with model: {model_label}")
        endpoint = w.serving_endpoints.create_and_wait(
            name=ROUTE_NAME,
            config=EndpointCoreConfigInput(name=ROUTE_NAME, served_entities=[served_entity]),
            ai_gateway=AiGatewayConfig(
                usage_tracking_config=AiGatewayUsageTrackingConfig(enabled=True),
                inference_table_config=AiGatewayInferenceTableConfig(
                    enabled=True,
                    catalog_name=CATALOG,
                    schema_name=SCHEMA,
                    table_name_prefix="care_gap_inference",
                ),
            ),
        )
        print(f"AI Gateway route created: {endpoint.name}")

    else:
        # No Anthropic key — use the pre-deployed Foundation Model API endpoint directly.
        # databricks-claude-sonnet-4-6 is always available in this workspace and supports llm/v1/chat.
        ROUTE_NAME   = FMAPI_FALLBACK
        model_label  = f"{FMAPI_FALLBACK} (Foundation Model API)"
        endpoint_exists = True
        print(f"Using existing Foundation Model API endpoint: {ROUTE_NAME}")

else:
    model_label = "pre-existing"

# COMMAND ----------

details = json.dumps({
    "route_name": ROUTE_NAME,
    "model":      model_label,
    "provider":   MODEL_PROVIDER,
})

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'configure_ai_gateway' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{details}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('configure_ai_gateway', 'COMPLETED', current_timestamp(), '{details}')
""")

print(f"Step 5 complete — AI Gateway route '{ROUTE_NAME}' ready")

