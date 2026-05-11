# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 5 — Configure AI Gateway Route
# MAGIC Creates a Databricks AI Gateway route that proxies requests to the
# MAGIC care gap foundation model (Claude via Anthropic or DBRX Instruct).
# MAGIC
# MAGIC **Prerequisites (Anthropic only):**
# MAGIC - A Databricks secret scope must exist with key `anthropic-api-key`.
# MAGIC   Pass the scope name via the `secret_scope` widget (default: `care-gap-demo`).
# MAGIC   OR set `model_provider` to `databricks` to use DBRX Instruct (no external key needed).
# MAGIC
# MAGIC To create the secret scope:
# MAGIC   `databricks secrets create-scope <scope-name>`
# MAGIC   `databricks secrets put-secret <scope-name> anthropic-api-key --string-value <key>`

# COMMAND ----------

import json

dbutils.widgets.text("catalog", "icd10_gap_demo")
dbutils.widgets.text("ai_gateway_route", "care-gap-advisor")
dbutils.widgets.text("model_provider", "anthropic")  # "anthropic" or "databricks" (DBRX)
dbutils.widgets.text("secret_scope", "care-gap-demo")  # Databricks secret scope holding anthropic-api-key

CATALOG = dbutils.widgets.get("catalog")
ROUTE_NAME = dbutils.widgets.get("ai_gateway_route")
MODEL_PROVIDER = dbutils.widgets.get("model_provider")
SECRET_SCOPE = dbutils.widgets.get("secret_scope")

# COMMAND ----------

# Idempotency check
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.app_config.bootstrap_status
    WHERE step = 'configure_ai_gateway' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    print(f"AI Gateway route already configured — skipping")
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

# Check if route already exists
try:
    existing_ep = w.serving_endpoints.get(name=ROUTE_NAME)
    print(f"Endpoint '{ROUTE_NAME}' already exists — skipping creation")
    endpoint_exists = True
except Exception:
    endpoint_exists = False

# COMMAND ----------

if not endpoint_exists:
    if MODEL_PROVIDER == "anthropic":
        # External model via Anthropic API key stored in Databricks secrets
        try:
            api_key = dbutils.secrets.get(scope=SECRET_SCOPE, key="anthropic-api-key")
        except Exception:
            print("WARNING: Secret '{SECRET_SCOPE}/anthropic-api-key' not found.")
            print("Falling back to DBRX Instruct (internal model, no external key required).")
            MODEL_PROVIDER = "databricks"

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
    else:
        # Use Databricks Foundation Model (DBRX) — no external key needed
        served_entity = ServedEntityInput(
            entity_name="databricks-dbrx-instruct",
            entity_version="1",
            workload_size="Small",
        )
        model_label = "databricks-dbrx-instruct"

    print(f"Creating AI Gateway route '{ROUTE_NAME}' with model: {model_label}")

    endpoint = w.serving_endpoints.create_and_wait(
        name=ROUTE_NAME,
        config=EndpointCoreConfigInput(
            served_entities=[served_entity],
        ),
        ai_gateway=AiGatewayConfig(
            usage_tracking_config=AiGatewayUsageTrackingConfig(enabled=True),
            inference_table_config=AiGatewayInferenceTableConfig(
                enabled=True,
                catalog_name=CATALOG,
                schema_name="app_config",
                table_name_prefix="care_gap_inference",
            ),
        ),
    )
    print(f"AI Gateway route created: {endpoint.name}")
else:
    model_label = "pre-existing"

# COMMAND ----------

details = json.dumps({
    "route_name": ROUTE_NAME,
    "model": model_label,
    "provider": MODEL_PROVIDER,
})

spark.sql(f"""
    MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
    USING (SELECT 'configure_ai_gateway' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{details}'
    WHEN NOT MATCHED THEN INSERT VALUES ('configure_ai_gateway', 'COMPLETED',
        current_timestamp(), '{details}')
""")

print(f"Step 5 complete — AI Gateway route '{ROUTE_NAME}' ready")

