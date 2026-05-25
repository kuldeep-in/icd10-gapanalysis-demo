import os
import logging
from databricks.sdk import WorkspaceClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CATALOG             = os.getenv("UC_CATALOG")
SCHEMA              = os.getenv("UC_SCHEMA")
WAREHOUSE_ID        = os.getenv("DATABRICKS_WAREHOUSE_ID")
DATA_SETUP_JOB_NAME = os.getenv("DATA_SETUP_JOB_NAME")
AI_SETUP_JOB_NAME   = os.getenv("AI_SETUP_JOB_NAME")
KA_ENDPOINT_NAME    = os.getenv("KA_ENDPOINT_NAME")
KA_NAME             = os.getenv("KA_NAME")
FMAPI_ENDPOINT      = os.getenv("FMAPI_ENDPOINT")

BRAND_ORANGE = "#E87722"

JOB1_STEPS = {"create_catalog", "setup_care_gap_rules", "ingest_patient_data"}
JOB2_STEPS = {"load_icd10_pdfs", "ka_source_configured", "ka_source_sync"}

BOOTSTRAP_STEPS = [
    {
        "step_id":     "create_catalog",
        "seq":         1,
        "group":       1,
        "label":       "Catalog & Schema Setup",
        "description": "Create Unity Catalog, schema, all Delta tables (patient_records, "
                       "care_gap_rules, bootstrap_status), UC Volume, and grant app SP permissions.",
        "icon":        "fa-database",
    },
    {
        "step_id":     "setup_care_gap_rules",
        "seq":         2,
        "group":       1,
        "label":       "Care Gap Rules Loaded",
        "description": "Seed the care_gap_rules table with 20 evidence-based clinical rules "
                       "aligned to HEDIS, ACC/AHA, ADA, GOLD, KDIGO, and USPSTF guidelines.",
        "icon":        "fa-list-check",
    },
    {
        "step_id":     "ingest_patient_data",
        "seq":         3,
        "group":       1,
        "label":       "Patient Clinical Notes Ingested",
        "description": "Load 25 synthetic SOAP-format patient records from "
                       "data/patient_records.json into the patient_records Delta table.",
        "icon":        "fa-notes-medical",
    },
    {
        "step_id":     "load_icd10_pdfs",
        "seq":         4,
        "group":       2,
        "label":       "ICD-10 Reference PDFs Uploaded to Volume",
        "description": "Download ICD-10 PDF reference files from GitHub directly into the "
                       "Unity Catalog Volume — prerequisite for Knowledge Assistant indexing.",
        "icon":        "fa-file-pdf",
    },
    {
        "step_id":     "ka_source_configured",
        "seq":         5,
        "group":       2,
        "label":       "ICD-10 PDF Volume Configured",
        "description": "Checks that the icd10_reference_pdfs UC Volume is attached to the "
                       "Knowledge Assistant as a knowledge source. Run Job 2 to attach it.",
        "icon":        "fa-plug",
    },
    {
        "step_id":     "ka_source_sync",
        "seq":         6,
        "group":       2,
        "label":       "ICD-10 PDF Sync",
        "description": "Checks the Knowledge Assistant's indexing state for the attached volume. "
                       "Indexing runs asynchronously after the source is attached (30–60 min). "
                       "ICD-10 Analyzer improves as files are indexed.",
        "icon":        "fa-brain",
        "is_async":    True,
    },
]

GROUP_META = {
    1: {"label": "Job 1 — Data Setup",                "icon": "fa-database", "border": "#0d6efd", "bg": "#f0f4ff"},
    2: {"label": "Job 2 — Knowledge Assistant Setup", "icon": "fa-robot",    "border": "#198754", "bg": "#f0fff4"},
}

STATUS_META = {
    "NOT_STARTED":     {"color": "secondary", "icon": "fa-circle-dot",           "label": "Not Started",     "row_bg": "#f8f9fa"},
    "IN_PROGRESS":     {"color": "warning",   "icon": "fa-spinner fa-spin",      "label": "In Progress",     "row_bg": "#fff8e1"},
    "COMPLETED":       {"color": "success",   "icon": "fa-circle-check",         "label": "Completed",       "row_bg": "#f0fff4"},
    "LIKELY_COMPLETE": {"color": "info",      "icon": "fa-circle-check",         "label": "Likely Complete", "row_bg": "#e8f8ff"},
    "WARNING":         {"color": "warning",   "icon": "fa-triangle-exclamation", "label": "Warning",         "row_bg": "#fff8e1"},
    "FAILED":          {"color": "danger",    "icon": "fa-circle-xmark",         "label": "Failed",          "row_bg": "#fff0f0"},
    "SKIPPED":         {"color": "secondary", "icon": "fa-forward",              "label": "Skipped",         "row_bg": "#f8f9fa"},
    "UNKNOWN":         {"color": "secondary", "icon": "fa-question-circle",      "label": "Unknown",         "row_bg": "#f8f9fa"},
}

DONE_STATUSES = {"COMPLETED", "LIKELY_COMPLETE", "SKIPPED"}

w = WorkspaceClient()

_app_sp_name: str = ""
try:
    _me = w.current_user.me()
    _app_sp_name = getattr(_me, "user_name", "") or ""
    logger.info(f"App identity: {getattr(_me, 'display_name', '')} → {_app_sp_name}")
except Exception as _e:
    logger.warning(f"Could not detect app identity: {_e}")
