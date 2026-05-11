# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 4 — Create Knowledge Assistant
# MAGIC Creates the ICD-10 Knowledge Assistant agent via the Databricks SDK,
# MAGIC attaches the UC Volume as a knowledge source, and triggers the initial PDF sync.
# MAGIC
# MAGIC **Notes:**
# MAGIC - KA sync is asynchronous — may take 30–60 min for large PDF volumes
# MAGIC - Only the creator principal can trigger re-sync
# MAGIC - The agent endpoint URL is written to `app_config.bootstrap_status`
# MAGIC - The Databricks App reads this URL at startup to call the KA endpoint

# COMMAND ----------

import json
import time

dbutils.widgets.text("catalog", "icd10_gap_demo")
CATALOG = dbutils.widgets.get("catalog")

KA_DISPLAY_NAME = "ICD-10 Clinical Reference Assistant"
VOLUME_PATH = f"/Volumes/{CATALOG}/icd10_reference/pdfs"

# COMMAND ----------

# Idempotency: check if KA already created
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.app_config.bootstrap_status
    WHERE step = 'create_knowledge_assistant' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    details = json.loads(existing[0]["details"])
    endpoint_name = details.get("endpoint_name", "")
    print(f"Knowledge Assistant already created — endpoint: {endpoint_name}")
    print("To recreate, delete the row from app_config.bootstrap_status and re-run.")
    dbutils.notebook.exit(f"SKIPPED — KA already exists: {endpoint_name}")

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.knowledgeassistants import (
    KnowledgeAssistant,
    KnowledgeSource,
    FilesSpec,
)

w = WorkspaceClient()

# Verify PDFs are present in the volume
try:
    volume_files = dbutils.fs.ls(f"dbfs:{VOLUME_PATH}".replace("/Volumes", "/Volumes"))
except Exception:
    volume_files = []

# Use SDK to list files in the Volume
import os
pdf_count = 0
try:
    pdf_count = len([f for f in os.listdir(VOLUME_PATH) if f.lower().endswith(".pdf")])
except Exception as e:
    print(f"Warning: could not count PDFs in volume: {e}")

print(f"PDFs in volume: {pdf_count}")
if pdf_count == 0:
    print("WARNING: No PDFs found in the volume. KA will be created but knowledge source will be empty.")
    print("Upload PDFs to the volume before re-triggering sync.")

# COMMAND ----------

# Create Knowledge Assistant
print(f"Creating Knowledge Assistant: {KA_DISPLAY_NAME}")

ka = w.knowledge_assistants.create_knowledge_assistant(
    knowledge_assistant=KnowledgeAssistant(
        display_name=KA_DISPLAY_NAME,
        description=(
            "Answers ICD-10 coding questions based on uploaded ICD-10 reference PDFs. "
            "Returns relevant codes with citations from source documents."
        ),
        instructions=(
            "Return relevant ICD-10 codes with citations from the reference documents. "
            "For each code, include: the code itself, the full code description, and the "
            "specific excerpt from the source document that supports it. "
            "Rank results by relevance to the clinical text provided. "
            "If a code cannot be confidently matched to the uploaded documents, "
            "state that explicitly rather than guessing. "
            "Do not return codes not directly supported by the uploaded reference material."
        ),
    )
)

ka_name = ka.name
print(f"Knowledge Assistant created: {ka_name}")

# COMMAND ----------

# Attach UC Volume as knowledge source
print(f"Attaching knowledge source: {VOLUME_PATH}")

source = w.knowledge_assistants.create_knowledge_source(
    parent=ka_name,
    knowledge_source=KnowledgeSource(
        display_name="ICD-10 Reference PDFs",
        source_type="files",
        files=FilesSpec(path=VOLUME_PATH),
    ),
)

print(f"Knowledge source attached: {source.name}")
print("PDF sync has been triggered. This runs asynchronously and may take 30–60 minutes.")
print("The app will display an 'indexing in progress' banner on Tab 1 until sync completes.")

# COMMAND ----------

# Retrieve the agent endpoint URL created automatically by the KA
# The KA creates a serving endpoint with the same name as the KA resource
endpoint_name = ka_name.split("/")[-1] if "/" in ka_name else ka_name

# Confirm endpoint exists
max_wait = 120
poll_interval = 10
elapsed = 0
endpoint_ready = False

print(f"Waiting for endpoint '{endpoint_name}' to become available...")
while elapsed < max_wait:
    try:
        ep = w.serving_endpoints.get(name=endpoint_name)
        if ep.state and ep.state.ready:
            endpoint_ready = True
            break
        print(f"  Endpoint state: {ep.state} — waiting...")
    except Exception as e:
        print(f"  Endpoint not yet visible: {e}")
    time.sleep(poll_interval)
    elapsed += poll_interval

if not endpoint_ready:
    print(f"Endpoint not ready after {max_wait}s — continuing anyway. Check Serving UI.")

# COMMAND ----------

# Store endpoint details in bootstrap_status
details = json.dumps({
    "ka_name": ka_name,
    "endpoint_name": endpoint_name,
    "volume_path": VOLUME_PATH,
    "pdf_count": pdf_count,
    "sync_started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
})

spark.sql(f"""
    MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
    USING (SELECT 'create_knowledge_assistant' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{details}'
    WHEN NOT MATCHED THEN INSERT VALUES ('create_knowledge_assistant', 'COMPLETED',
        current_timestamp(), '{details}')
""")

print(f"Step 4 complete — KA endpoint: {endpoint_name}")
print("Reminder: KA PDF sync continues in the background. Tab 1 will show a progress banner until sync completes.")

