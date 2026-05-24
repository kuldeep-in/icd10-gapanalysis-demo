# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 3 — Load ICD-10 PDFs into Unity Catalog Volume
# MAGIC Copies PDF files from `data/icd10_pdfs/` in the Git repo into the
# MAGIC `icd10_reference_pdfs` UC Volume.
# MAGIC
# MAGIC Runs on serverless — uses the Workspace REST API to list and download files
# MAGIC (no FUSE mount needed for reading), and Python `open()` for writing to the
# MAGIC UC Volume (Unity Catalog volumes have full FUSE support on serverless).
# MAGIC
# MAGIC **Knowledge Assistant constraints:**
# MAGIC - Files larger than 50 MB are skipped by KA automatically
# MAGIC - Files beginning with `_` or `.` are skipped by KA automatically

# COMMAND ----------

import os
import base64
import requests

dbutils.widgets.text("catalog", "my_catalog")
dbutils.widgets.text("schema",  "icd10_care_gap")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA  = dbutils.widgets.get("schema")

VOLUME_PATH    = f"/Volumes/{CATALOG}/{SCHEMA}/icd10_reference_pdfs"
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# COMMAND ----------

# Resolve workspace path to data/icd10_pdfs/ relative to this notebook.
ctx           = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
api_token     = ctx.apiToken().get()
api_url       = ctx.apiUrl().get()
notebook_path = ctx.notebookPath().get()

if notebook_path.startswith("/Workspace"):
    notebook_path = notebook_path[len("/Workspace"):]

repo_root  = os.path.dirname(os.path.dirname(notebook_path))
ws_pdf_dir = repo_root + "/data/icd10_pdfs"

print(f"PDF source (workspace) : {ws_pdf_dir}")
print(f"Destination (volume)   : {VOLUME_PATH}")

# COMMAND ----------

# List PDFs in workspace directory via REST API
list_resp = requests.get(
    f"{api_url}/api/2.0/workspace/list",
    headers={"Authorization": f"Bearer {api_token}"},
    params={"path": ws_pdf_dir},
    timeout=30,
)

if list_resp.status_code == 404:
    dir_exists = False
    ws_objects = []
elif list_resp.status_code == 200:
    dir_exists = True
    ws_objects = list_resp.json().get("objects", [])
else:
    dir_exists = False
    ws_objects = []
    print(f"WARNING: workspace list returned HTTP {list_resp.status_code}: {list_resp.text}")

if not dir_exists:
    print(f"WARNING: {ws_pdf_dir} does not exist or is empty.")
    print("Commit ICD-10 PDF files to data/icd10_pdfs/ in the repo before running this step.")
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
        USING (SELECT 'load_icd10_pdfs' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET status = 'WARNING', updated_at = current_timestamp(),
            details = 'data/icd10_pdfs/ directory not found'
        WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
            VALUES ('load_icd10_pdfs', 'WARNING', current_timestamp(),
                    'data/icd10_pdfs/ directory not found')
    """)
    dbutils.notebook.exit("WARNING: No PDF directory found")

# Filter to PDF files only
pdf_objects = [
    obj for obj in ws_objects
    if obj.get("object_type") == "FILE" and obj["path"].lower().endswith(".pdf")
]
print(f"Found {len(pdf_objects)} PDF file(s)")

# COMMAND ----------

copied       = 0
skipped_size = []
skipped_name = []

for obj in pdf_objects:
    name = obj["path"].split("/")[-1]
    size = obj.get("size", 0)

    if name.startswith("_") or name.startswith("."):
        skipped_name.append(name)
        print(f"  SKIP (name): {name}")
        continue

    size_mb = size / (1024 * 1024)
    if size > MAX_SIZE_BYTES:
        skipped_size.append(f"{name} ({size_mb:.1f} MB)")
        print(f"  SKIP (size): {name} — {size_mb:.1f} MB exceeds 50 MB limit")
        continue

    # Download from workspace via export API (returns base64-encoded content)
    dl_resp = requests.get(
        f"{api_url}/api/2.0/workspace/export",
        headers={"Authorization": f"Bearer {api_token}"},
        params={"path": obj["path"], "format": "AUTO"},
        timeout=120,
    )
    if dl_resp.status_code != 200:
        print(f"  ERROR downloading {name}: HTTP {dl_resp.status_code}")
        continue

    pdf_bytes = base64.b64decode(dl_resp.json()["content"])

    # Write to UC Volume — open() with /Volumes/ works on serverless (UC volumes have FUSE)
    vol_path = f"{VOLUME_PATH}/{name}"
    with open(vol_path, "wb") as fh:
        fh.write(pdf_bytes)
    print(f"  COPIED: {name} ({size_mb:.2f} MB)")
    copied += 1

print(f"\nSummary: {copied} copied, {len(skipped_size)} skipped (size), {len(skipped_name)} skipped (name)")
if skipped_size:
    print(f"Files exceeding 50 MB: {skipped_size}")

# COMMAND ----------

if copied == 0 and not skipped_size:
    status  = "WARNING"
    details = "No PDFs were copied — check data/icd10_pdfs/ in the repo"
else:
    status  = "COMPLETED"
    details = f"{copied} PDFs loaded to {SCHEMA}.icd10_reference_pdfs volume"
    if skipped_size:
        details += f"; {len(skipped_size)} skipped (over 50MB)"

# Escape single quotes for safe SQL interpolation
safe_details = details.replace("'", "''")

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'load_icd10_pdfs' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = '{status}', updated_at = current_timestamp(),
        details = '{safe_details}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('load_icd10_pdfs', '{status}', current_timestamp(), '{safe_details}')
""")

print("Step 3 complete")

