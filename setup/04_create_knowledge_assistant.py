# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 4 — Create Knowledge Assistant
# MAGIC Creates the ICD-10 Knowledge Assistant agent via the Databricks SDK,
# MAGIC attaches the `icd10_reference_pdfs` UC Volume as a knowledge source,
# MAGIC and triggers the initial PDF sync.
# MAGIC
# MAGIC **Notes:**
# MAGIC - KA sync is asynchronous — may take 30–60 min for large PDF volumes
# MAGIC - The agent endpoint URL is written to `bootstrap_status`
# MAGIC - The Databricks App reads this URL at startup to call the KA endpoint

# COMMAND ----------

import json
import time

dbutils.widgets.text("catalog", "my_catalog")
dbutils.widgets.text("schema",  "icd10_care_gap")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA  = dbutils.widgets.get("schema")

KA_DISPLAY_NAME = "ICD-10 Clinical Reference Assistant"
VOLUME_PATH     = f"/Volumes/{CATALOG}/{SCHEMA}/icd10_reference_pdfs"

# COMMAND ----------

# Idempotency check
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.`{SCHEMA}`.bootstrap_status
    WHERE step = 'create_knowledge_assistant' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    details       = json.loads(existing[0]["details"])
    endpoint_name = details.get("endpoint_name", "")
    print(f"Knowledge Assistant already created — endpoint: {endpoint_name}")
    dbutils.notebook.exit(f"SKIPPED — KA already exists: {endpoint_name}")

# COMMAND ----------

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.knowledgeassistants import (
    KnowledgeAssistant,
    KnowledgeSource,
    FilesSpec,
)

w = WorkspaceClient()

# Count PDFs using dbutils.fs (works on serverless — no FUSE needed)
pdf_count = 0
try:
    volume_files = dbutils.fs.ls(VOLUME_PATH)
    pdf_count    = sum(1 for fi in volume_files if fi.name.lower().endswith(".pdf"))
except Exception as e:
    print(f"Warning: could not count PDFs in volume: {e}")

print(f"PDFs in {SCHEMA}.icd10_reference_pdfs: {pdf_count}")
if pdf_count == 0:
    print("WARNING: No PDFs found. KA will be created but knowledge source will be empty.")

# COMMAND ----------

print(f"Creating Knowledge Assistant: {KA_DISPLAY_NAME}")

try:
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
except Exception as e:
    if "ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
        print(f"KA already exists — locating existing agent...")
        ka = None
        for item in w.knowledge_assistants.list_knowledge_assistants():
            if item.display_name == KA_DISPLAY_NAME:
                ka = item
                break
        if ka is None:
            raise RuntimeError(f"KA '{KA_DISPLAY_NAME}' reported as existing but not found in list")
        ka_name = ka.name
        print(f"Using existing Knowledge Assistant: {ka_name}")
    else:
        raise

# COMMAND ----------

print(f"Attaching knowledge source: {VOLUME_PATH}")

try:
    source = w.knowledge_assistants.create_knowledge_source(
        parent=ka_name,
        knowledge_source=KnowledgeSource(
            display_name="ICD-10 Reference PDFs",
            description="ICD-10 coding reference PDFs used for clinical code suggestions.",
            source_type="files",
            files=FilesSpec(path=VOLUME_PATH),
        ),
    )
    print(f"Knowledge source attached: {source.name}")
    print("PDF sync triggered — runs asynchronously (30–60 min). App will show indexing banner until complete.")
except Exception as e:
    if "ALREADY_EXISTS" in str(e) or "already exists" in str(e).lower():
        print(f"Knowledge source already attached — skipping")
    else:
        raise

# COMMAND ----------

# Use the endpoint_name from the KA object (ka.endpoint_name is the serving endpoint name,
# e.g. "ka-0eccc75d-endpoint") — do NOT parse ka_name which gives the UUID only.
endpoint_name = getattr(ka, "endpoint_name", None) or (ka_name.split("/")[-1] if "/" in ka_name else ka_name)
max_wait      = 120
poll_interval = 10
elapsed       = 0
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
    print(f"Endpoint not ready after {max_wait}s — continuing. Check Serving UI.")

# COMMAND ----------

details = json.dumps({
    "ka_name":          ka_name,
    "endpoint_name":    endpoint_name,
    "volume_path":      VOLUME_PATH,
    "pdf_count":        pdf_count,
    "sync_started_at":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
})

spark.sql(f"""
    MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
    USING (SELECT 'create_knowledge_assistant' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{details}'
    WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
        VALUES ('create_knowledge_assistant', 'COMPLETED', current_timestamp(), '{details}')
""")

print(f"Step 4 complete — KA endpoint: {endpoint_name}")

