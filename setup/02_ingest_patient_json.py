# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 2 — Ingest Patient Records from JSON
# MAGIC Reads `/data/patient_records.json` from the Git repo and loads 25 synthetic
# MAGIC patient records into `clinical_data.patient_records`. Idempotent.

# COMMAND ----------

import os
import json

dbutils.widgets.text("catalog", "icd10_gap_demo")
CATALOG = dbutils.widgets.get("catalog")

# COMMAND ----------

# Idempotency check
existing = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.clinical_data.patient_records"
).collect()[0]["cnt"]

if existing >= 25:
    print(f"Table already has {existing} records — skipping ingestion")
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
        USING (SELECT 'ingest_patient_data' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
            details = 'Skipped — {existing} records already present'
        WHEN NOT MATCHED THEN INSERT VALUES ('ingest_patient_data', 'COMPLETED', current_timestamp(),
            'Skipped — {existing} records already present')
    """)
    dbutils.notebook.exit("SKIPPED")

# COMMAND ----------

# Resolve path to patient_records.json relative to this notebook
# Notebook is at: <repo_root>/setup/02_ingest_patient_json
# JSON file is at: <repo_root>/data/patient_records.json
notebook_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()

# Ensure /Workspace prefix for filesystem access
if not notebook_path.startswith("/Workspace"):
    notebook_path = f"/Workspace{notebook_path}"

repo_root = os.path.dirname(os.path.dirname(notebook_path))   # go up from setup/
json_path = os.path.join(repo_root, "data", "patient_records.json")

print(f"Reading from: {json_path}")

with open(json_path, "r") as f:
    records = json.load(f)

print(f"Loaded {len(records)} records from JSON")

# COMMAND ----------

# Build DataFrame with exact schema order
from pyspark.sql import Row
from pyspark.sql.types import StructType, StructField, StringType

schema = StructType([
    StructField("patient_id",       StringType(), True),
    StructField("mrn",              StringType(), True),
    StructField("dob",              StringType(), True),
    StructField("gender",           StringType(), True),
    StructField("message_datetime", StringType(), True),
    StructField("clinicalrecord",   StringType(), True),
])

rows = [
    Row(
        patient_id=r["patient_id"],
        mrn=r["mrn"],
        dob=r["dob"],
        gender=r["gender"],
        message_datetime=r["message_datetime"],
        clinicalrecord=r["clinicalrecord"],
    )
    for r in records
]

df = spark.createDataFrame(rows, schema=schema)
df.write.mode("overwrite").saveAsTable(f"`{CATALOG}`.clinical_data.patient_records")

final_count = spark.sql(f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.clinical_data.patient_records").collect()[0]["cnt"]
print(f"Ingested {final_count} records into {CATALOG}.clinical_data.patient_records")

# COMMAND ----------

spark.sql(f"""
    MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
    USING (SELECT 'ingest_patient_data' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{final_count} records ingested from patient_records.json'
    WHEN NOT MATCHED THEN INSERT VALUES ('ingest_patient_data', 'COMPLETED', current_timestamp(),
        '{final_count} records ingested from patient_records.json')
""")

print("Step 2 complete")

