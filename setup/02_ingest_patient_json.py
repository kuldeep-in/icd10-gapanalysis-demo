# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 2 — Ingest Patient Records from JSON
# MAGIC Reads `data/patient_records.json` from the Git repo and loads 25 synthetic
# MAGIC patient records into `patient_records`. Idempotent.
# MAGIC Runs on serverless — uses the Workspace REST API to read the file (no FUSE needed).

# COMMAND ----------

import os
import base64
import json as _json
import requests

dbutils.widgets.text("catalog", "my_catalog")
dbutils.widgets.text("schema",  "icd10_care_gap")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA  = dbutils.widgets.get("schema")

# COMMAND ----------

# Idempotency check
existing = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.`{SCHEMA}`.patient_records"
).collect()[0]["cnt"]

if existing >= 25:
    print(f"Table already has {existing} records — skipping ingestion")
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
        USING (SELECT 'ingest_patient_data' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
            details = 'Skipped — {existing} records already present'
        WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
            VALUES ('ingest_patient_data', 'COMPLETED', current_timestamp(),
                    'Skipped — {existing} records already present')
    """)
    dbutils.notebook.exit("SKIPPED")

# COMMAND ----------

# Resolve workspace path to patient_records.json relative to this notebook.
# Uses the Workspace REST API — works on serverless (no FUSE or dbfs: path needed).
ctx           = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
api_token     = ctx.apiToken().get()
api_url       = ctx.apiUrl().get()
notebook_path = ctx.notebookPath().get()  # e.g. /Users/.../setup/02_...

# Strip /Workspace prefix if present; API paths start with /Users/ or /Repos/
if notebook_path.startswith("/Workspace"):
    notebook_path = notebook_path[len("/Workspace"):]

repo_root    = os.path.dirname(os.path.dirname(notebook_path))  # setup/ → repo root
ws_json_path = repo_root + "/data/patient_records.json"

print(f"Workspace path : {ws_json_path}")

# COMMAND ----------

# Download via Workspace REST API.
# The export endpoint returns {"content": "<base64>", "file_type": "SOURCE"} — decode accordingly.
resp = requests.get(
    f"{api_url}/api/2.0/workspace/export",
    headers={"Authorization": f"Bearer {api_token}"},
    params={"path": ws_json_path, "format": "SOURCE"},
    timeout=60,
)
if resp.status_code != 200:
    raise RuntimeError(
        f"Failed to download {ws_json_path}: HTTP {resp.status_code}\n{resp.text}"
    )

raw = base64.b64decode(resp.json()["content"]).decode("utf-8").strip()
print(f"Downloaded {len(raw)} bytes from {ws_json_path}")

# patient_records.json may be a JSON array or JSON Lines (one object per line)
if raw.startswith("["):
    records = _json.loads(raw)
else:
    records = [_json.loads(line) for line in raw.splitlines() if line.strip()]

print(f"Parsed {len(records)} records")

# COMMAND ----------

df = spark.createDataFrame(records)
df.write.mode("overwrite").saveAsTable(f"`{CATALOG}`.`{SCHEMA}`.patient_records")

final_count = spark.sql(
    f"SELECT COUNT(*) as cnt FROM `{CATALOG}`.`{SCHEMA}`.patient_records"
).collect()[0]["cnt"]
print(f"Ingested {final_count} records into {CATALOG}.{SCHEMA}.patient_records")

# COMMAND ----------

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'ingest_patient_data' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{final_count} records ingested from patient_records.json'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('ingest_patient_data', 'COMPLETED', current_timestamp(),
                '{final_count} records ingested from patient_records.json')
""")

print("Step 2 complete")

