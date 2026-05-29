# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step — Care Gap Vector Search Index
# MAGIC Creates a Delta Sync Vector Search index on `care_gap_rules` so the app can
# MAGIC retrieve the most semantically relevant rules for each patient's clinical note
# MAGIC without sending all rules to the AI model.
# MAGIC
# MAGIC **What this does:**
# MAGIC 1. Adds an `embedding_text` column with clinically rich text per rule
# MAGIC 2. Creates a Delta Sync VS index on `rag_pdf_vs_endpoint`
# MAGIC 3. Triggers initial sync (20 rules — completes in < 1 min)
# MAGIC 4. Grants app SP CAN_QUERY on the VS endpoint
# MAGIC 5. Records completion in `bootstrap_status`

# COMMAND ----------

from databricks.sdk import WorkspaceClient
import time

dbutils.widgets.text("catalog",          "my_catalog")
dbutils.widgets.text("schema",           "icd10_care_gap")
dbutils.widgets.text("app_sp_id",        "")
dbutils.widgets.text("vs_endpoint_name", "rag_pdf_vs_endpoint")

CATALOG      = dbutils.widgets.get("catalog")
SCHEMA       = dbutils.widgets.get("schema")
APP_SP_ID    = dbutils.widgets.get("app_sp_id")
VS_ENDPOINT  = dbutils.widgets.get("vs_endpoint_name").strip() or "rag_pdf_vs_endpoint"
VS_INDEX     = f"{CATALOG}.{SCHEMA}.care_gap_rules_vs_index"
EMBED_MODEL  = "databricks-gte-large-en"

print(f"Catalog:    {CATALOG}.{SCHEMA}")
print(f"VS index:   {VS_INDEX}")
print(f"Endpoint:   {VS_ENDPOINT}")

# COMMAND ----------

# Condition codes → full medical names for richer embeddings
CONDITION_NAMES = {
    "T2DM":         "Type 2 Diabetes Mellitus",
    "HTN":          "Hypertension",
    "POST-MI":      "Post-Myocardial Infarction Cardiac Care",
    "AFIB":         "Atrial Fibrillation",
    "BREAST_CANCER":"Breast Cancer Oncology",
    "COPD":         "Chronic Obstructive Pulmonary Disease",
    "CKD":          "Chronic Kidney Disease",
    "DEPRESSION":   "Depression and Mental Health",
}

# Add embedding_text column (safe — catches "already exists" silently)
try:
    spark.sql(
        f"ALTER TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules ADD COLUMN embedding_text STRING"
    )
    print("Column embedding_text added")
except Exception as e:
    if "already exists" in str(e).lower() or "COLUMN_ALREADY_EXISTS" in str(e):
        print("Column embedding_text already exists — skipping")
    else:
        raise

# COMMAND ----------

# Build clinically rich embedding text for each rule and upsert
rows = spark.sql(
    f"SELECT rule_id, condition, gap_name, check_description, priority, guideline "
    f"FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules"
).collect()

updates = []
for r in rows:
    full_cond = CONDITION_NAMES.get(r.condition, r.condition)
    text = (
        f"{r.gap_name} for {full_cond}. "
        f"{r.check_description}. "
        f"Guideline: {r.guideline}. "
        f"Priority: {r.priority}."
    )
    updates.append((r.rule_id, text))
    print(f"  {r.rule_id}: {text[:80]}...")

from pyspark.sql.types import StructType, StructField, StringType
schema_df = StructType([
    StructField("rule_id",        StringType()),
    StructField("embedding_text", StringType()),
])
df = spark.createDataFrame(updates, schema_df)
df.createOrReplaceTempView("_rule_embeddings")

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.care_gap_rules AS t
    USING _rule_embeddings AS s ON t.rule_id = s.rule_id
    WHEN MATCHED THEN UPDATE SET t.embedding_text = s.embedding_text
""")
print(f"\nembedding_text populated for {len(updates)} rules")

# COMMAND ----------

# Enable Change Data Feed on care_gap_rules — required for Delta Sync VS index
spark.sql(f"""
    ALTER TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules
    SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
""")
print("Change Data Feed enabled on care_gap_rules")

w = WorkspaceClient()

# Use raw REST API for index operations — avoids SDK version differences
def _vs_get(index_name):
    return w.api_client.do("GET", f"/api/2.0/vector-search/indexes/{index_name}")

def _vs_sync(index_name):
    return w.api_client.do("POST", f"/api/2.0/vector-search/indexes/{index_name}/sync")

def _vs_create(name, endpoint, primary_key, source_table, embed_col, embed_model):
    return w.api_client.do("POST", "/api/2.0/vector-search/indexes", body={
        "name":         name,
        "endpoint_name": endpoint,
        "primary_key":  primary_key,
        "index_type":   "DELTA_SYNC",
        "delta_sync_index_spec": {
            "source_table": source_table,
            "embedding_source_columns": [{
                "name":                           embed_col,
                "embedding_model_endpoint_name":  embed_model,
            }],
            "pipeline_type": "TRIGGERED",
        },
    })

# Create or sync
try:
    _vs_get(VS_INDEX)
    print(f"Index already exists — triggering sync to pick up latest rules")
    _vs_sync(VS_INDEX)
    print("Sync triggered")
except Exception as e:
    if "NOT_FOUND" in str(e) or "404" in str(e) or "does not exist" in str(e).lower() or "RESOURCE_DOES_NOT_EXIST" in str(e):
        print(f"Creating VS index: {VS_INDEX}")
        _vs_create(VS_INDEX, VS_ENDPOINT, "rule_id",
                   f"{CATALOG}.{SCHEMA}.care_gap_rules",
                   "embedding_text", EMBED_MODEL)
        print("Index created — initial sync started")
    else:
        raise

# COMMAND ----------

# Wait for sync to complete (20 rules sync in < 60s)
print("Waiting for sync to complete...")
for attempt in range(20):
    time.sleep(6)
    try:
        idx    = w.vector_search_indexes.get_index(index_name=VS_INDEX)
        status = (idx.status.detailed_state or "").upper()
        print(f"  [{attempt*6}s] status: {status}")
        if "READY" in status or "ONLINE" in status:
            print("Sync complete — index is ready")
            break
    except Exception as e:
        print(f"  [{attempt*6}s] checking... ({e})")
else:
    print("WARNING: Sync still running — index will be ready shortly")

# COMMAND ----------

# Grant app SP CAN_QUERY on VS endpoint
if APP_SP_ID:
    try:
        w.api_client.do(
            "PATCH",
            f"/api/2.0/permissions/vector-search-endpoints/{VS_ENDPOINT}",
            body={"access_control_list": [{
                "service_principal_name": APP_SP_ID,
                "permission_level": "CAN_QUERY",
            }]},
        )
        print(f"Granted CAN_QUERY on {VS_ENDPOINT} to {APP_SP_ID}")
    except Exception as e:
        print(f"WARNING: Could not grant VS permissions: {e}")
else:
    print("app_sp_id not set — skipping VS permissions")

# COMMAND ----------

# Record in bootstrap_status
spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'care_gap_vs_index' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET
        status = 'COMPLETED', updated_at = current_timestamp(),
        details = 'VS index {VS_INDEX} on {VS_ENDPOINT} using {EMBED_MODEL}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('care_gap_vs_index', 'COMPLETED', current_timestamp(),
                'VS index {VS_INDEX} on {VS_ENDPOINT} using {EMBED_MODEL}')
""")
print(f"bootstrap_status: care_gap_vs_index → COMPLETED")
print(f"\nVS index ready: {VS_INDEX}")

