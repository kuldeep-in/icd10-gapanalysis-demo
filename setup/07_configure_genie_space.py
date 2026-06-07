# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step — Configure Genie Space
# MAGIC Registers 4 Delta tables to the Genie Space and grants CAN_RUN to the app SP.
# MAGIC
# MAGIC Tables registered via PATCH /api/2.0/genie/spaces/{id} with serialized_space
# MAGIC containing data_sources.tables (sorted by identifier — API requirement).

# COMMAND ----------

import json
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog",        "my_catalog")
dbutils.widgets.text("schema",         "icd10_care_gap")
dbutils.widgets.text("genie_space_id", "")
dbutils.widgets.text("app_sp_id",      "")

CATALOG        = dbutils.widgets.get("catalog")
SCHEMA         = dbutils.widgets.get("schema")
GENIE_SPACE_ID = dbutils.widgets.get("genie_space_id").strip()
APP_SP_ID      = dbutils.widgets.get("app_sp_id").strip()

if not GENIE_SPACE_ID:
    raise ValueError("genie_space_id widget is empty — deploy.sh must pass the Genie Space ID")

print(f"Catalog:         {CATALOG}.{SCHEMA}")
print(f"Genie Space ID:  {GENIE_SPACE_ID}")

w = WorkspaceClient()

# COMMAND ----------

TABLES = sorted([
    f"{CATALOG}.{SCHEMA}.care_gap_findings",
    f"{CATALOG}.{SCHEMA}.care_gap_rules",
    f"{CATALOG}.{SCHEMA}.icd10_analysis_results",
    f"{CATALOG}.{SCHEMA}.patient_records",
])

print("Registering tables to Genie Space...")
try:
    w.api_client.do(
        "PATCH",
        f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}",
        body={
            "serialized_space": json.dumps({
                "version": 2,
                "data_sources": {
                    "tables": [{"identifier": t} for t in TABLES]
                },
            })
        },
    )
    print(f"  ✔ Registered {len(TABLES)} tables:")
    for t in TABLES:
        print(f"    {t}")
except Exception as e:
    print(f"  ✗ Table registration failed: {e}")
    print("  Tables can also be added via: AI/BI → Genie → Settings → Data → Add tables")

# COMMAND ----------

# Grant app SP CAN_USE on the Genie Space
if APP_SP_ID:
    try:
        w.api_client.do(
            "PATCH",
            f"/api/2.0/permissions/genie/{GENIE_SPACE_ID}",
            body={"access_control_list": [{
                "service_principal_name": APP_SP_ID,
                "permission_level": "CAN_RUN",
            }]},
        )
        print(f"✔ Granted CAN_RUN on Genie Space to {APP_SP_ID}")
    except Exception as e:
        print(f"WARNING: Could not grant Genie Space permissions: {e}")
else:
    print("app_sp_id not set — skipping Genie Space permission grant")

# COMMAND ----------

# Record in bootstrap_status
spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'genie_configured' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET
        status = 'COMPLETED', updated_at = current_timestamp(),
        details = 'Genie Space {GENIE_SPACE_ID} — tables: {", ".join(TABLES)}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('genie_configured', 'COMPLETED', current_timestamp(),
                'Genie Space {GENIE_SPACE_ID} — tables: {", ".join(TABLES)}')
""")
print("bootstrap_status: genie_configured → COMPLETED")
print(f"\nGenie Space ready: {GENIE_SPACE_ID}")
