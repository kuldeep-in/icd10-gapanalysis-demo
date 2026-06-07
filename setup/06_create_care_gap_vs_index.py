# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step — Care Gap Vector Search Index
# MAGIC Creates (or updates) a Delta Sync Vector Search index on `care_gap_rules`.
# MAGIC
# MAGIC **Logic:**
# MAGIC - If index already exists AND all rules already have `embedding_text` → trigger sync only
# MAGIC - If any rules are missing `embedding_text` OR index doesn't exist → rebuild embeddings first
# MAGIC - Uses raw REST API for all VS operations (avoids SDK version attribute differences)

# COMMAND ----------

from databricks.sdk import WorkspaceClient
import time

dbutils.widgets.text("catalog",          "my_catalog")
dbutils.widgets.text("schema",           "icd10_care_gap")
dbutils.widgets.text("app_sp_id",        "")
dbutils.widgets.text("vs_endpoint_name", "rag_pdf_vs_endpoint")

CATALOG     = dbutils.widgets.get("catalog")
SCHEMA      = dbutils.widgets.get("schema")
APP_SP_ID   = dbutils.widgets.get("app_sp_id")
VS_ENDPOINT = dbutils.widgets.get("vs_endpoint_name").strip() or "rag_pdf_vs_endpoint"
VS_INDEX    = f"{CATALOG}.{SCHEMA}.care_gap_rules_vs_index"
EMBED_MODEL = "databricks-gte-large-en"

print(f"Catalog:    {CATALOG}.{SCHEMA}")
print(f"VS index:   {VS_INDEX}")
print(f"Endpoint:   {VS_ENDPOINT}")

w = WorkspaceClient()

# ---------------------------------------------------------------------------
# Raw REST helpers — avoids SDK version differences
# ---------------------------------------------------------------------------
def _vs_get(index_name):
    return w.api_client.do("GET", f"/api/2.0/vector-search/indexes/{index_name}")

def _vs_sync(index_name):
    return w.api_client.do("POST", f"/api/2.0/vector-search/indexes/{index_name}/sync")

def _vs_create(name, endpoint, primary_key, source_table, embed_col, embed_model):
    return w.api_client.do("POST", "/api/2.0/vector-search/indexes", body={
        "name":          name,
        "endpoint_name": endpoint,
        "primary_key":   primary_key,
        "index_type":    "DELTA_SYNC",
        "delta_sync_index_spec": {
            "source_table": source_table,
            "embedding_source_columns": [{
                "name":                          embed_col,
                "embedding_model_endpoint_name": embed_model,
            }],
            "pipeline_type": "TRIGGERED",
        },
    })

def _vs_status(index_name):
    """Return the index detailed_state string, or '' if not available."""
    try:
        data = _vs_get(index_name)
        return (data.get("status", {}).get("detailed_state") or "").upper()
    except Exception:
        return ""

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 1: Determine what needs to be done
# ---------------------------------------------------------------------------

# Check index existence using _vs_get directly — NOT _vs_status which swallows all
# exceptions and would incorrectly set index_exists=True when index is not found.
index_exists = False
index_ready  = False
try:
    data = _vs_get(VS_INDEX)
    index_exists = True
    raw_status   = (data.get("status", {}).get("detailed_state") or "").upper()
    index_ready  = "READY" in raw_status or "ONLINE" in raw_status
    print(f"VS index found — status: {raw_status or 'unknown'}")
except Exception as e:
    if any(x in str(e) for x in ("NOT_FOUND", "404", "does not exist", "RESOURCE_DOES_NOT_EXIST")):
        print("VS index does not exist — will create")
    else:
        raise

# Check if any rules are missing embedding_text
missing_embeddings = spark.sql(f"""
    SELECT COUNT(*) as cnt
    FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules
    WHERE embedding_text IS NULL OR embedding_text = ''
""").collect()[0]["cnt"]

total_rules = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules"
).collect()[0]["cnt"]

print(f"Rules: {total_rules} total, {missing_embeddings} missing embedding_text")

needs_embedding_rebuild = missing_embeddings > 0
print(f"Action: {'rebuild embeddings + sync' if needs_embedding_rebuild else 'sync only (embeddings up to date)'}")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 2: Rebuild embeddings (only if needed)
# ---------------------------------------------------------------------------
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

if needs_embedding_rebuild:
    # embedding_text column is created by setup_schema.py at deploy time — always present
    # Build embedding text only for rules missing it
    rows = spark.sql(f"""
        SELECT rule_id, condition, gap_name, check_description, priority, guideline
        FROM `{CATALOG}`.`{SCHEMA}`.care_gap_rules
        WHERE embedding_text IS NULL OR embedding_text = ''
    """).collect()

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
    print(f"embedding_text populated for {len(updates)} rules")

    # Enable Change Data Feed (required for Delta Sync)
    spark.sql(f"""
        ALTER TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules
        SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
    """)
    print("Change Data Feed enabled on care_gap_rules")
else:
    print("All rules have embedding_text — skipping rebuild")
    # Still ensure CDF is enabled
    spark.sql(f"""
        ALTER TABLE `{CATALOG}`.`{SCHEMA}`.care_gap_rules
        SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
    """)

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 3: Create index or trigger sync
# ---------------------------------------------------------------------------
if not index_exists:
    print(f"Creating VS index: {VS_INDEX}")
    _vs_create(VS_INDEX, VS_ENDPOINT, "rule_id",
               f"{CATALOG}.{SCHEMA}.care_gap_rules",
               "embedding_text", EMBED_MODEL)
    print("Index created — initial sync started automatically")
else:
    print("Triggering sync to pick up any rule changes...")
    _vs_sync(VS_INDEX)
    print("Sync triggered")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 4: Wait for sync to complete — use raw API (no SDK attribute issues)
# ---------------------------------------------------------------------------
print("Waiting for sync to complete...")
synced = False
for attempt in range(25):
    time.sleep(6)
    try:
        current_status = _vs_status(VS_INDEX)
        elapsed = (attempt + 1) * 6
        print(f"  [{elapsed}s] status: {current_status or 'checking...'}")
        if "READY" in current_status or "ONLINE" in current_status:
            print("✔ Sync complete — index is ready")
            synced = True
            break
        elif "FAIL" in current_status:
            print(f"✗ Index sync failed: {current_status}")
            break
    except Exception as e:
        print(f"  [{(attempt+1)*6}s] waiting... ({e})")

if not synced:
    print("WARNING: Sync still running — index will be ready shortly")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 5: Grant app SP CAN_QUERY on VS endpoint
# ---------------------------------------------------------------------------
if APP_SP_ID:
    try:
        # Look up the VS endpoint ID (required for permissions API)
        ep_data = w.api_client.do("GET", f"/api/2.0/vector-search/endpoints/{VS_ENDPOINT}")
        ep_id   = ep_data.get("id", "")
        if ep_id:
            w.api_client.do(
                "PATCH",
                f"/api/2.0/permissions/vector-search-endpoints/{ep_id}",
                body={"access_control_list": [{
                    "service_principal_name": APP_SP_ID,
                    "permission_level": "CAN_QUERY",
                }]},
            )
            print(f"✔ Granted CAN_QUERY on {VS_ENDPOINT} to {APP_SP_ID}")
        else:
            print(f"WARNING: Could not resolve VS endpoint ID for {VS_ENDPOINT}")
    except Exception as e:
        print(f"WARNING: Could not grant VS permissions: {e}")
else:
    print("app_sp_id not set — skipping VS permissions")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Step 6: Record completion
# ---------------------------------------------------------------------------
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
