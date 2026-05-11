# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 3 — Load ICD-10 PDFs into Unity Catalog Volume
# MAGIC Copies PDF files from `data/icd10_pdfs/` in the Git repo
# MAGIC into `/Volumes/<catalog>/icd10_reference/pdfs/`.
# MAGIC
# MAGIC **Knowledge Assistant constraints:**
# MAGIC - Files larger than 50 MB are skipped by KA automatically
# MAGIC - Files beginning with `_` or `.` are skipped by KA automatically

# COMMAND ----------

import os
import shutil

dbutils.widgets.text("catalog", "icd10_gap_demo")
CATALOG = dbutils.widgets.get("catalog")

VOLUME_PATH = f"/Volumes/{CATALOG}/icd10_reference/pdfs"
MAX_FILE_SIZE_MB = 50

# COMMAND ----------

# Resolve source path relative to this notebook
notebook_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
if not notebook_path.startswith("/Workspace"):
    notebook_path = f"/Workspace{notebook_path}"

repo_root = os.path.dirname(os.path.dirname(notebook_path))
pdf_source_dir = os.path.join(repo_root, "data", "icd10_pdfs")

print(f"PDF source directory: {pdf_source_dir}")
print(f"Volume destination:   {VOLUME_PATH}")

# COMMAND ----------

if not os.path.exists(pdf_source_dir):
    print(f"WARNING: {pdf_source_dir} does not exist.")
    print("Please commit ICD-10 PDF files to data/icd10_pdfs/ in the GitHub repo before running this step.")
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
        USING (SELECT 'load_icd10_pdfs' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET status = 'WARNING', updated_at = current_timestamp(),
            details = 'data/icd10_pdfs/ directory not found — no PDFs loaded'
        WHEN NOT MATCHED THEN INSERT VALUES ('load_icd10_pdfs', 'WARNING', current_timestamp(),
            'data/icd10_pdfs/ directory not found — no PDFs loaded')
    """)
    dbutils.notebook.exit("WARNING: No PDF directory found")

pdf_files = [f for f in os.listdir(pdf_source_dir) if f.lower().endswith(".pdf")]
print(f"Found {len(pdf_files)} PDF file(s) in source directory")

# COMMAND ----------

copied = 0
skipped_size = []
skipped_name = []

for filename in pdf_files:
    # KA skips files starting with _ or .
    if filename.startswith("_") or filename.startswith("."):
        skipped_name.append(filename)
        print(f"  SKIP (name): {filename}")
        continue

    src = os.path.join(pdf_source_dir, filename)
    size_mb = os.path.getsize(src) / (1024 * 1024)

    if size_mb > MAX_FILE_SIZE_MB:
        skipped_size.append(f"{filename} ({size_mb:.1f} MB)")
        print(f"  SKIP (size): {filename} — {size_mb:.1f} MB exceeds 50 MB limit")
        continue

    dest = os.path.join(VOLUME_PATH, filename)
    shutil.copy2(src, dest)
    print(f"  COPIED: {filename} ({size_mb:.2f} MB)")
    copied += 1

print(f"\nSummary: {copied} copied, {len(skipped_size)} skipped (size), {len(skipped_name)} skipped (name)")

if skipped_size:
    print(f"Files exceeding 50 MB limit: {skipped_size}")

# COMMAND ----------

details = f"{copied} PDFs loaded to volume"
if skipped_size:
    details += f"; {len(skipped_size)} skipped (>50MB)"

spark.sql(f"""
    MERGE INTO `{CATALOG}`.app_config.bootstrap_status AS t
    USING (SELECT 'load_icd10_pdfs' AS step) AS s ON t.step = s.step
    WHEN MATCHED THEN UPDATE SET status = 'COMPLETED', updated_at = current_timestamp(),
        details = '{details}'
    WHEN NOT MATCHED THEN INSERT VALUES ('load_icd10_pdfs', 'COMPLETED', current_timestamp(), '{details}')
""")

print("Step 3 complete")

