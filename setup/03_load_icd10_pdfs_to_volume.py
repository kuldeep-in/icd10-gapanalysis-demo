# Databricks notebook source

# COMMAND ----------
# MAGIC %md
# MAGIC ## Bootstrap Step 3 — Load ICD-10 PDFs into Unity Catalog Volume
# MAGIC Downloads PDF files directly from GitHub and writes them to the
# MAGIC `icd10_reference_pdfs` UC Volume.
# MAGIC
# MAGIC **Configuration:**
# MAGIC - `pdf_github_url` — GitHub tree URL of the PDF directory, e.g.
# MAGIC   `https://github.com/<owner>/<repo>/tree/<branch>/data/icd10_pdfs`
# MAGIC
# MAGIC **Knowledge Assistant constraints:**
# MAGIC - Files larger than 50 MB are skipped by KA automatically
# MAGIC - Files beginning with `_` or `.` are skipped by KA automatically

# COMMAND ----------

import os
import re
import requests

dbutils.widgets.text("catalog",         "my_catalog")
dbutils.widgets.text("schema",          "icd10_care_gap")
dbutils.widgets.text("pdf_github_url",  "https://github.com/kuldeep-in/icd10-gapanalysis-demo/tree/main/data/icd10_pdfs")

CATALOG        = dbutils.widgets.get("catalog")
SCHEMA         = dbutils.widgets.get("schema")
PDF_GITHUB_URL = dbutils.widgets.get("pdf_github_url").strip()

VOLUME_PATH    = f"/Volumes/{CATALOG}/{SCHEMA}/icd10_reference_pdfs"
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB — KA hard limit

print(f"PDF source (GitHub) : {PDF_GITHUB_URL}")
print(f"Destination (volume): {VOLUME_PATH}")

# COMMAND ----------

# Idempotency check
existing = spark.sql(f"""
    SELECT details FROM `{CATALOG}`.`{SCHEMA}`.bootstrap_status
    WHERE step = 'load_icd10_pdfs' AND status = 'COMPLETED'
    ORDER BY updated_at DESC LIMIT 1
""").collect()

if existing:
    print("ICD-10 PDFs already loaded to volume — skipping")
    dbutils.notebook.exit("SKIPPED")

# COMMAND ----------

# Parse GitHub tree URL → GitHub Contents API URL
# Input:  https://github.com/<owner>/<repo>/tree/<branch>/<path>
# Output: https://api.github.com/repos/<owner>/<repo>/contents/<path>?ref=<branch>
m = re.match(
    r"https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)",
    PDF_GITHUB_URL,
)
if not m:
    raise ValueError(
        f"pdf_github_url must be a GitHub tree URL of the form "
        f"https://github.com/<owner>/<repo>/tree/<branch>/<path>. Got: {PDF_GITHUB_URL}"
    )

owner, repo, branch, gh_path = m.group(1), m.group(2), m.group(3), m.group(4)
api_url  = f"https://api.github.com/repos/{owner}/{repo}/contents/{gh_path}?ref={branch}"
print(f"GitHub API: {api_url}")

# COMMAND ----------

# List files via GitHub Contents API
list_resp = requests.get(api_url, headers={"Accept": "application/vnd.github+json"}, timeout=30)
if list_resp.status_code != 200:
    raise RuntimeError(f"GitHub API returned HTTP {list_resp.status_code}: {list_resp.text[:300]}")

pdf_files = [
    f for f in list_resp.json()
    if f.get("type") == "file" and f["name"].lower().endswith(".pdf")
]
print(f"Found {len(pdf_files)} PDF file(s) in GitHub directory")

if not pdf_files:
    spark.sql(f"""
        MERGE INTO `{CATALOG}`.`{SCHEMA}`.bootstrap_status AS t
        USING (SELECT 'load_icd10_pdfs' AS step) AS s ON t.step = s.step
        WHEN MATCHED THEN UPDATE SET status = 'WARNING', updated_at = current_timestamp(),
            details = 'No PDFs found in GitHub directory'
        WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
            VALUES ('load_icd10_pdfs', 'WARNING', current_timestamp(), 'No PDFs found in GitHub directory')
    """)
    dbutils.notebook.exit("WARNING: No PDFs found in GitHub directory")

# COMMAND ----------

copied       = 0
skipped_size = []
skipped_name = []

for file_info in pdf_files:
    name     = file_info["name"]
    size     = file_info.get("size", 0)
    size_mb  = size / (1024 * 1024)
    dl_url   = file_info.get("download_url", "")

    if name.startswith("_") or name.startswith("."):
        skipped_name.append(name)
        print(f"  SKIP (name): {name}")
        continue

    if size > MAX_SIZE_BYTES:
        skipped_size.append(f"{name} ({size_mb:.1f} MB)")
        print(f"  SKIP (size): {name} — {size_mb:.1f} MB exceeds 50 MB KA limit")
        continue

    if not dl_url:
        print(f"  ERROR: no download_url for {name}")
        continue

    # Stream download directly to the UC Volume (FUSE path)
    print(f"  Downloading: {name} ({size_mb:.2f} MB)…")
    try:
        with requests.get(dl_url, stream=True, timeout=300) as r:
            r.raise_for_status()
            vol_path = f"{VOLUME_PATH}/{name}"
            with open(vol_path, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8 * 1024 * 1024):
                    fh.write(chunk)
        print(f"  COPIED: {name} ({size_mb:.2f} MB)")
        copied += 1
    except Exception as e:
        print(f"  ERROR copying {name}: {e}")

print(f"\nSummary: {copied} copied, {len(skipped_size)} skipped (size), {len(skipped_name)} skipped (name)")
if skipped_size:
    print(f"Files exceeding 50 MB: {skipped_size}")

# COMMAND ----------

if copied == 0 and not skipped_size:
    status  = "WARNING"
    details = "No PDFs were copied — check the pdf_github_url variable"
else:
    status  = "COMPLETED"
    details = f"{copied} PDFs downloaded from GitHub and loaded to {SCHEMA}.icd10_reference_pdfs volume"
    if skipped_size:
        details += f"; {len(skipped_size)} skipped (over 50 MB)"

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

