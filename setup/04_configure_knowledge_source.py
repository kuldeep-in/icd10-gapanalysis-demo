# Databricks notebook source


# COMMAND ----------

# MAGIC %md
# MAGIC ## Job 2 — Configure Knowledge Source
# MAGIC Attaches the `icd10_reference_pdfs` UC Volume to the Knowledge Assistant
# MAGIC and triggers document sync. Safe to re-run — fully idempotent.
# MAGIC
# MAGIC **Prerequisites:**
# MAGIC - KA must already exist (created by `setup_resources.py` during pre-deploy)
# MAGIC - `ka_endpoint_name` must be set (flows from `databricks.yml` → bundle variable)
# MAGIC - Job 1 step 3 must be complete so PDFs are in the UC Volume
# MAGIC
# MAGIC **What this does:**
# MAGIC 1. Locates the KA by its serving endpoint name via REST API
# MAGIC 2. Attaches the UC Volume as a knowledge source if not already attached
# MAGIC 3. Triggers document sync (indexing runs async, 30–60 min)
# MAGIC 4. Records completion in `bootstrap_status` so the app shows accurate step 6 status

# COMMAND ----------

import json
import time

from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog",          "my_catalog")
dbutils.widgets.text("schema",           "icd10_care_gap")
dbutils.widgets.text("ka_endpoint_name", "")

CATALOG          = dbutils.widgets.get("catalog")
SCHEMA           = dbutils.widgets.get("schema")
KA_ENDPOINT_NAME = dbutils.widgets.get("ka_endpoint_name").strip()
VOLUME_PATH      = f"/Volumes/{CATALOG}/{SCHEMA}/icd10_reference_pdfs"
KA_API_BASE      = "/api/2.1/knowledge-assistants"

if not KA_ENDPOINT_NAME:
    raise ValueError(
        "ka_endpoint_name widget is empty.\n"
        "This value must be set in databricks.yml (var.ka_endpoint_name) and "
        "flows into this job via the bundle variable. "
        "Run setup_resources.py during deploy to create the KA and capture the endpoint name."
    )

print(f"Catalog:        {CATALOG}.{SCHEMA}")
print(f"Volume path:    {VOLUME_PATH}")
print(f"KA endpoint:    {KA_ENDPOINT_NAME}")

# COMMAND ----------

# Reset KA sync cache so the app re-polls the KA API until indexing completes again.
# The app caches ka_source_sync = COMPLETED in bootstrap_status once indexing finishes.
# Deleting that row here ensures a re-run of this job triggers fresh polling.
spark.sql(f"""
    DELETE FROM `{CATALOG}`.`{SCHEMA}`.bootstrap_status
    WHERE step = 'ka_source_sync'
""")
print("ka_source_sync cache cleared — app will re-poll KA API until indexing completes")

# COMMAND ----------

# Verify PDFs are present in the volume before attaching
try:
    pdf_files = [f for f in dbutils.fs.ls(VOLUME_PATH) if f.name.lower().endswith(".pdf")]
    print(f"\nPDFs in volume: {len(pdf_files)}")
    if not pdf_files:
        print("WARNING: No PDF files found in volume — KA will be configured but knowledge base will be empty.")
        print(f"Run Job 1 step 3 (load_icd10_pdfs) to populate {VOLUME_PATH} first.")
    else:
        for f in pdf_files[:5]:
            print(f"  {f.name}")
        if len(pdf_files) > 5:
            print(f"  ... and {len(pdf_files) - 5} more")
except Exception as e:
    print(f"WARNING: Could not list volume contents: {e}")
    print("Continuing with KA configuration...")

# COMMAND ----------

w = WorkspaceClient()

# Locate the KA by its serving endpoint name via REST API (/api/2.1/knowledge-assistants).
# The Databricks SDK v0.67 does not expose a knowledge_assistants attribute,
# so we use the raw API client directly.
kas_data = w.api_client.do("GET", KA_API_BASE)
ka = None

for item in kas_data.get("knowledge_assistants", []):
    item_endpoint = item.get("endpoint_name", "") or ""
    item_name     = item.get("name", "") or ""
    # endpoint_name is the authoritative field; name has form "knowledge-assistants/{id}"
    derived_endpoint = item_name.split("/")[-1] if "/" in item_name else item_name

    if item_endpoint == KA_ENDPOINT_NAME or derived_endpoint == KA_ENDPOINT_NAME:
        ka = item
        break

if ka is None:
    raise RuntimeError(
        f"No Knowledge Assistant found with endpoint '{KA_ENDPOINT_NAME}'.\n"
        f"Run setup_resources.py (pre-deploy step) to create the KA first, "
        f"then redeploy the app with the captured endpoint name."
    )

ka_name      = ka.get("name")
ka_display   = ka.get("display_name", "")
ka_endpoint  = ka.get("endpoint_name", KA_ENDPOINT_NAME)
print(f"\nKA found: '{ka_display}' → {ka_name}")
print(f"Endpoint: {ka_endpoint}")

# COMMAND ----------

# Check whether the UC Volume is already attached as a knowledge source
src_data         = w.api_client.do("GET", f"/api/2.1/{ka_name}/knowledge-sources")
existing_sources = src_data.get("knowledge_sources", [])
volume_source    = None

for src in existing_sources:
    files = src.get("files", {}) or {}
    if files.get("path") == VOLUME_PATH:
        volume_source = src
        break

print(f"Existing knowledge sources: {len(existing_sources)}")

if volume_source:
    src_state = volume_source.get("state", "")
    ingest    = volume_source.get("ingestion_details", {})
    print(f"Volume already attached: {VOLUME_PATH}")
    print(f"Source name:  {volume_source.get('name')}")
    print(f"Source state: {src_state}")
    if ingest:
        print(f"Indexed:      {ingest.get('success_file_count', 0)}/{ingest.get('total_file_count', 0)} files, "
              f"{ingest.get('vector_count', 0)} vectors")
    action = "already_configured"
else:
    print(f"Attaching volume: {VOLUME_PATH}")
    volume_source = w.api_client.do(
        "POST", f"/api/2.1/{ka_name}/knowledge-sources",
        body={
            "display_name": "ICD-10 Reference PDFs",
            "description":  "ICD-10 coding reference PDFs used for clinical code suggestions.",
            "source_type":  "files",
            "files":        {"path": VOLUME_PATH},
        },
    )
    print(f"Volume attached — document sync started: {volume_source.get('name')}")
    print("Indexing runs asynchronously. ICD-10 Analyzer results improve over 30–60 min.")
    action = "configured"

# COMMAND ----------

# Record completion in bootstrap_status so app.py shows accurate step 6 status
source_name = (volume_source.get("name") if isinstance(volume_source, dict)
               else getattr(volume_source, "name", ""))

details = json.dumps({
    "ka_name":       ka_name,
    "endpoint_name": KA_ENDPOINT_NAME,
    "volume_path":   VOLUME_PATH,
    "source_name":   source_name,
    "action":        action,
    "configured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
})

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'ka_source_configured' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET
        status     = 'COMPLETED',
        updated_at = current_timestamp(),
        details    = '{details}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('ka_source_configured', 'COMPLETED', current_timestamp(), '{details}')
""")

print(f"\nStep complete — bootstrap_status updated: ka_source_configured → COMPLETED")
print(f"KA endpoint: {KA_ENDPOINT_NAME}")

# COMMAND ----------

# ---------------------------------------------------------------------------
# Poll for PDF sync completion — this notebook runs with admin credentials
# so the knowledge-sources API is accessible. The app SP only has CAN_QUERY
# on the KA and cannot list knowledge sources, so detection must happen here.
# ---------------------------------------------------------------------------
def _merge_sync_status(status: str, detail: str) -> None:
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
        USING (SELECT 'ka_source_sync' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET
            status = '{status}', updated_at = current_timestamp(),
            details = '{detail}'
        WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
            VALUES ('ka_source_sync', '{status}', current_timestamp(), '{detail}')
    """)

# Write IN_PROGRESS immediately so the Setup page shows sync is running
_merge_sync_status("IN_PROGRESS", "PDF indexing in progress — polling for completion")
print("bootstrap_status: ka_source_sync → IN_PROGRESS")

# COMMAND ----------

# Poll until KA source reaches UPDATED state (max 90 min)
print("\nPolling KA for PDF sync completion (runs async, typically 30–60 min)...")
MAX_POLLS = 90   # 1 poll per minute
synced    = False

for i in range(MAX_POLLS):
    time.sleep(60)
    try:
        src_data = w.api_client.do("GET", f"/api/2.1/{ka_name}/knowledge-sources")
        sources  = src_data.get("knowledge_sources", [])
        # Find our source by name or path
        source = next(
            (s for s in sources if s.get("name") == source_name
             or (s.get("files") or {}).get("path") == VOLUME_PATH),
            None
        )
        if source:
            state   = source.get("state", "")
            ingest  = source.get("ingestion_details") or {}
            success = ingest.get("success_file_count", "?")
            total   = ingest.get("total_file_count",   "?")
            vectors = ingest.get("vector_count",       "?")
            print(f"  [{i+1} min] state: {state} — {success}/{total} files, {vectors} vectors")
            if state == "UPDATED":
                detail = f"{success}/{total} files indexed · {vectors} vectors"
                _merge_sync_status("COMPLETED", detail)
                print(f"\nbootstrap_status: ka_source_sync → COMPLETED")
                synced = True
                break
            elif state == "FAILED":
                _merge_sync_status("FAILED", "PDF indexing failed — check KA in Databricks UI")
                print("bootstrap_status: ka_source_sync → FAILED")
                break
        else:
            print(f"  [{i+1} min] source not yet visible — waiting...")
    except Exception as e:
        print(f"  [{i+1} min] poll error: {e}")

if not synced:
    _merge_sync_status("WARNING",
        "PDF indexing may still be running — refresh the Setup page to check")
    print("bootstrap_status: ka_source_sync → WARNING (timed out after 90 min)")
