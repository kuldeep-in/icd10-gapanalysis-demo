import os
import json
import logging
import re
from datetime import datetime

import dash
from dash import dcc, html, callback, Input, Output, State
import dash_bootstrap_components as dbc
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all values come from app.yaml env vars at deploy time
# ---------------------------------------------------------------------------
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

w = WorkspaceClient()

_app_sp_name: str = ""
try:
    _me = w.current_user.me()
    _app_sp_name = getattr(_me, "user_name", "") or ""
    logger.info(f"App identity: {getattr(_me, 'display_name', '')} → {_app_sp_name}")
except Exception as _e:
    logger.warning(f"Could not detect app identity: {_e}")


# ---------------------------------------------------------------------------
# Bootstrap step registry  (group=1 → Job 1, group=2 → Job 2)
# ---------------------------------------------------------------------------
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
    1: {"label": "Job 1 — Data Setup",                  "icon": "fa-database", "border": "#0d6efd", "bg": "#f0f4ff"},
    2: {"label": "Job 2 — Knowledge Assistant Setup",   "icon": "fa-robot",    "border": "#198754", "bg": "#f0fff4"},
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

# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def execute_sql(statement: str) -> list[dict]:
    if not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>":
        raise RuntimeError(
            "SQL Warehouse not configured — set DATABRICKS_WAREHOUSE_ID in app.yaml and redeploy."
        )
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL error: {resp.status.error}")
    if not resp.manifest or not resp.manifest.schema:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (resp.result.data_array or [])]


def load_patients(catalog: str = CATALOG, schema: str = SCHEMA) -> list[dict]:
    return execute_sql(
        f"SELECT patient_id, mrn, dob, gender, message_datetime "
        f"FROM `{catalog}`.`{schema}`.patient_records ORDER BY patient_id"
    )


def get_patient_record(patient_id: str, catalog: str = CATALOG, schema: str = SCHEMA) -> dict | None:
    rows = execute_sql(
        f"SELECT * FROM `{catalog}`.`{schema}`.patient_records "
        f"WHERE patient_id = '{patient_id}' LIMIT 1"
    )
    return rows[0] if rows else None


def get_care_gap_rules(catalog: str = CATALOG, schema: str = SCHEMA) -> list[dict]:
    return execute_sql(
        f"SELECT * FROM `{catalog}`.`{schema}`.care_gap_rules ORDER BY priority, condition"
    )



def _chk_catalog(catalog: str) -> dict:
    try:
        rows = execute_sql(
            f"SELECT catalog_name FROM system.information_schema.catalogs "
            f"WHERE catalog_name = '{catalog}'"
        )
        ok = len(rows) > 0
        return {"ok": ok, "label": f"`{catalog}` found" if ok else f"`{catalog}` not found"}
    except Exception as e:
        return {"ok": False, "label": str(e)[:100]}


def _chk_schema(catalog: str, schema: str) -> dict:
    try:
        rows = execute_sql(
            f"SELECT schema_name FROM `{catalog}`.information_schema.schemata "
            f"WHERE schema_name = '{schema}'"
        )
        ok = len(rows) > 0
        return {"ok": ok, "label": f"`{schema}` found" if ok else f"`{schema}` not found"}
    except Exception as e:
        return {"ok": False, "label": str(e)[:100]}


def _chk_table_rows(catalog: str, schema: str, table: str) -> dict:
    try:
        r   = execute_sql(f"SELECT COUNT(*) as cnt FROM `{catalog}`.`{schema}`.`{table}`")
        cnt = int(r[0]["cnt"]) if r else 0
        return {"ok": cnt > 0, "cnt": cnt, "label": f"{cnt} row{'s' if cnt != 1 else ''}"}
    except Exception as e:
        return {"ok": False, "cnt": 0, "label": str(e)[:120]}


def _chk_volume_files(catalog: str, schema: str, volume: str) -> dict:
    try:
        entries = list(w.files.list_directory_contents(f"/Volumes/{catalog}/{schema}/{volume}"))
        cnt = len(entries)
        return {"ok": cnt > 0, "cnt": cnt, "label": f"{cnt} file{'s' if cnt != 1 else ''}"}
    except Exception as e:
        return {"ok": False, "cnt": 0, "label": str(e)[:120]}


def _chk_bootstrap_status(catalog: str, schema: str, step_id: str) -> dict:
    try:
        rows = execute_sql(
            f"SELECT status, details FROM `{catalog}`.`{schema}`.bootstrap_status "
            f"WHERE step = '{step_id}' ORDER BY updated_at DESC LIMIT 1"
        )
        if rows and rows[0].get("status") == "COMPLETED":
            return {"ok": True, "label": "Completed — knowledge source configured and sync triggered"}
        return {"ok": False, "label": "Not yet run — trigger Job 2 to configure knowledge source"}
    except Exception as e:
        return {"ok": False, "label": str(e)[:120]}


def _chk_ka_endpoint(endpoint_name: str) -> dict:
    if not endpoint_name:
        return {"ok": False, "label": "KA_ENDPOINT_NAME not set — run setup_resources.py before deploy"}
    try:
        w.serving_endpoints.get(name=endpoint_name)
        return {"ok": True, "label": f"Endpoint `{endpoint_name}` ready"}
    except Exception as e:
        err = str(e)
        if any(x in err for x in ("NOT_FOUND", "404", "does not exist", "not found")):
            return {"ok": False, "label": f"Endpoint `{endpoint_name}` not found"}
        return {"ok": False, "label": err[:100]}


def _chk_ka_sources(ka_name: str, volume_path: str) -> dict:
    """
    Single API call that returns source attachment + sync state.
    Uses KA_NAME directly (knowledge-assistants/<uuid>) — no list call needed,
    so the app SP does not need list-all-KAs permission.
    Result is shared between ka_source_configured and ka_source_sync steps.
    """
    if not ka_name:
        return {"source_found": False, "state": "", "ingestion": {},
                "error": "KA_NAME not set"}
    try:
        src_data = w.api_client.do("GET", f"/api/2.1/{ka_name}/knowledge-sources")
        sources  = src_data.get("knowledge_sources", [])

        norm = volume_path.rstrip("/")
        matched = next(
            (s for s in sources
             if (s.get("files") or {}).get("path", "").rstrip("/") == norm),
            None,
        )
        if not matched:
            return {"source_found": False, "state": "", "ingestion": {},
                    "error": None, "source_count": len(sources)}

        return {
            "source_found": True,
            "state":        matched.get("state", ""),
            "ingestion":    matched.get("ingestion_details") or {},
            "cutoff_time":  matched.get("knowledge_cutoff_time", ""),
            "error":        None,
        }
    except Exception as e:
        return {"source_found": False, "state": "", "ingestion": {}, "error": str(e)[:120]}



def find_job_ids() -> tuple[int | None, int | None]:
    try:
        job1_id = job2_id = None
        for job in w.jobs.list():
            name = job.settings.name or ""
            if DATA_SETUP_JOB_NAME in name:
                job1_id = job.job_id
            elif AI_SETUP_JOB_NAME in name:
                job2_id = job.job_id
            if job1_id and job2_id:
                break
        return job1_id, job2_id
    except Exception as e:
        logger.warning(f"Job lookup failed: {e}")
        return None, None


def get_active_run(job_id: int) -> dict | None:
    try:
        runs = list(w.jobs.list_runs(job_id=job_id, active_only=True))
        if runs:
            r = runs[0]
            return {"run_id": r.run_id, "url": r.run_page_url or ""}
    except Exception as e:
        logger.warning(f"Active run check failed: {e}")
    return None


def _job1_action(
    steps: list[dict], job1_id: int | None, job1_active: dict | None
) -> dict:
    by_id       = {s["step_id"]: s["status"] for s in steps}
    job1_done   = all(by_id.get(s) in DONE_STATUSES for s in JOB1_STEPS)
    job1_failed = any(by_id.get(s) == "FAILED"      for s in JOB1_STEPS)
    if job1_done:
        return {"action": "done", "job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME,
                "label": "Data Setup Complete", "description": "All data setup steps are complete.", "active_run": None}
    if job1_active:
        return {"action": "running", "job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME,
                "label": "Data Setup Running…", "description": "Job 1 is in progress.", "active_run": job1_active}
    return {
        "action":      "run_job1",
        "job_id":      job1_id,
        "job_name":    DATA_SETUP_JOB_NAME,
        "label":       "Re-run Data Setup" if job1_failed else "Run Data Setup",
        "description": "Creates catalog, ingests patient records, uploads ICD-10 PDFs",
        "active_run":  None,
    }


def _job2_action(
    steps: list[dict], job2_id: int | None, job2_active: dict | None
) -> dict:
    by_id       = {s["step_id"]: s["status"] for s in steps}
    job2_done   = all(by_id.get(s) in DONE_STATUSES for s in JOB2_STEPS)
    job2_failed = any(by_id.get(s) == "FAILED"      for s in JOB2_STEPS)
    if job2_done:
        return {"action": "done", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "AI Setup Complete", "description": "All AI setup steps are complete.", "active_run": None}
    if job2_active:
        return {"action": "running", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "AI Setup Running…", "description": "Job 2 is in progress.", "active_run": job2_active}
    return {
        "action":      "run_job2",
        "job_id":      job2_id,
        "job_name":    AI_SETUP_JOB_NAME,
        "label":       "Re-run AI Setup" if job2_failed else "Run AI Setup",
        "description": "Creates Knowledge Assistant and configures AI Gateway (serverless)",
        "active_run":  None,
    }

# ---------------------------------------------------------------------------
# Pure object-based step status resolution (no bootstrap_status table)
# ---------------------------------------------------------------------------
def _check_step_statuses(catalog: str, schema: str) -> list[dict]:
    # Pre-compute catalog/schema existence once to short-circuit dependent checks
    cat_chk     = _chk_catalog(catalog)
    sch_chk     = _chk_schema(catalog, schema) if cat_chk["ok"] else {"ok": False, "label": "skipped — catalog not found"}
    schema_ok   = sch_chk["ok"]
    volume_path = f"/Volumes/{catalog}/{schema}/icd10_reference_pdfs"
    ka_src      = None  # lazy — fetched once on first KA source step, shared with second

    result = []
    for step in BOOTSTRAP_STEPS:
        sid    = step["step_id"]
        status = "NOT_STARTED"
        detail = "Not yet started"
        checks: list = []

        if sid == "create_catalog":
            checks = [
                ("fa-layer-group", "Catalog", cat_chk),
                ("fa-table",       "Schema",  sch_chk),
            ]
            if cat_chk["ok"] and sch_chk["ok"]:
                status = "COMPLETED"
                detail = f"Catalog `{catalog}` and schema `{schema}` exist"
            else:
                missing = "catalog" if not cat_chk["ok"] else "schema"
                detail = f"`{missing}` not found — run Data Setup"

        elif sid == "setup_care_gap_rules":
            chk = _chk_table_rows(catalog, schema, "care_gap_rules") if schema_ok \
                else {"ok": False, "label": "skipped — schema not found"}
            checks = [("fa-list-check", "Care gap rules", chk)]
            status = "COMPLETED" if chk["ok"] else "NOT_STARTED"
            detail = chk["label"]

        elif sid == "ingest_patient_data":
            chk = _chk_table_rows(catalog, schema, "patient_records") if schema_ok \
                else {"ok": False, "label": "skipped — schema not found"}
            checks = [("fa-users", "Patient records", chk)]
            status = "COMPLETED" if chk["ok"] else "NOT_STARTED"
            detail = chk["label"]

        elif sid == "load_icd10_pdfs":
            chk = _chk_volume_files(catalog, schema, "icd10_reference_pdfs")
            checks = [("fa-file-pdf", "ICD-10 PDFs in volume", chk)]
            status = "COMPLETED" if chk["ok"] else "NOT_STARTED"
            detail = chk["label"]

        elif sid == "ka_source_configured":
            if ka_src is None:
                ka_src = _chk_ka_sources(KA_NAME, volume_path)
            pdf_chk = _chk_volume_files(catalog, schema, "icd10_reference_pdfs")
            src_ok  = ka_src.get("source_found", False)
            if ka_src.get("error"):
                src_label = ka_src["error"]
            elif src_ok:
                src_label = f"Volume attached (state: {ka_src.get('state') or 'unknown'})"
            else:
                src_label = "Volume not attached to KA — run Job 2"
            checks = [
                ("fa-file-pdf", "PDFs in volume",    pdf_chk),
                ("fa-plug",     "Volume attached to KA", {"ok": src_ok, "label": src_label}),
            ]
            status = "COMPLETED" if src_ok else "NOT_STARTED"
            detail = src_label

        elif sid == "ka_source_sync":
            if ka_src is None:
                ka_src = _chk_ka_sources(KA_NAME, volume_path)
            state     = ka_src.get("state", "")
            ingestion = ka_src.get("ingestion", {})
            if not ka_src.get("source_found"):
                status = "NOT_STARTED"
                detail = "Volume not attached — complete step 5 first"
                sync_chk = {"ok": False, "label": detail}
            elif state == "UPDATED":
                total   = ingestion.get("total_file_count",   "?")
                success = ingestion.get("success_file_count", "?")
                failed  = ingestion.get("failed_file_count",  "0")
                vectors = ingestion.get("vector_count",        "?")
                status  = "COMPLETED"
                detail  = f"{success}/{total} files indexed · {vectors} vectors"
                if str(failed) not in ("0", ""):
                    detail += f" · {failed} failed"
                sync_chk = {"ok": True, "label": detail}
            elif state in ("UPDATING", "PENDING", "RUNNING"):
                success = ingestion.get("success_file_count", "0")
                total   = ingestion.get("total_file_count",   "?")
                status  = "IN_PROGRESS"
                detail  = f"Indexing in progress — {success}/{total} files done"
                sync_chk = {"ok": False, "label": detail}
            elif state == "FAILED":
                status   = "FAILED"
                detail   = "Indexing failed — check KA sources in Databricks UI"
                sync_chk = {"ok": False, "label": detail}
            else:
                status   = "NOT_STARTED"
                detail   = f"Sync state: {state or 'unknown'}"
                sync_chk = {"ok": False, "label": detail}
            checks = [("fa-brain", "Indexing state", sync_chk)]

        result.append({**step, "status": status, "detail": detail, "checks": checks, "updated_at": ""})

    return result

# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------
def call_ka_endpoint(clinical_text: str, endpoint_name: str) -> str:
    prompt = (
        "Analyze the following clinical note and identify all relevant ICD-10 codes.\n\n"
        "For each code provide:\n"
        "1. ICD-10 code\n2. Full code description\n"
        "3. The specific text from the note that supports this code\n"
        "4. Confidence: HIGH / MEDIUM / LOW\n\n"
        f"Clinical Note:\n{clinical_text}"
    )
    response = w.serving_endpoints.query(
        name=endpoint_name, messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def call_care_gap_model(patient_record: dict, rules: list[dict]) -> list[dict]:
    rules_text = "\n".join(
        f"- [{r['rule_id']}] {r['gap_name']} ({r['condition']}): "
        f"{r['check_description']} [Priority: {r['priority']}] — Guideline: {r['guideline']}"
        for r in rules
    )
    response = w.serving_endpoints.query(
        name=FMAPI_ENDPOINT,
        messages=[
            {"role": "system", "content": (
                "You are a clinical care gap analyzer. Identify which care gaps apply "
                "to the patient. Return a JSON array only — each object must have: "
                "rule_id, gap_name, condition, priority (HIGH/MEDIUM/LOW), guideline, "
                "finding, recommended_action. No prose, no markdown."
            )},
            {"role": "user", "content": (
                f"Patient Record:\n{patient_record['clinicalrecord']}\n\n"
                f"Care Gap Rules:\n{rules_text}\n\nReturn applicable gaps as JSON array."
            )},
        ],
    )
    raw = response.choices[0].message.content
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        logger.warning(f"Care gap model returned unparseable JSON: {raw[:200]}")
        return []

# ---------------------------------------------------------------------------
# Home tab: accordion step and group header builders
# ---------------------------------------------------------------------------
def _prereq_section(prereqs: list[dict]) -> html.Div:
    """Render a compact prerequisites block shown above the job accordion."""
    rows = []
    for p in prereqs:
        ok        = p.get("ok", False)
        value     = p.get("value") or ""
        fa_status = "fa-circle-check text-success" if ok else "fa-circle-xmark text-danger"
        rows.append(
            html.Div([
                html.I(className=f"fa-solid {fa_status} me-2", style={"width": "14px"}),
                html.I(className=f"fa-solid {p['icon']} me-2 text-muted", style={"width": "14px"}),
                html.Span(p["label"] + ":", className="small fw-semibold me-2"),
                html.Code(
                    value if value else "Not configured",
                    className="small",
                    style={"fontSize": "11px",
                           "color": "#dc3545" if not value else "inherit"},
                ),
            ], className="d-flex align-items-center mb-1")
        )
    return html.Div([
        html.Div([
            html.I(className="fa-solid fa-shield-check me-1 text-muted"),
            html.Span("Prerequisites", className="small fw-semibold text-muted text-uppercase",
                      style={"letterSpacing": "0.5px", "fontSize": "11px"}),
        ], className="mb-2"),
        html.Div(rows, className="ps-2 border-start border-2", style={"borderColor": "#dee2e6"}),
    ], className="mb-3 p-2 rounded", style={"background": "#f8f9fa", "border": "1px solid #e9ecef"})


def _step_accordion_item(step: dict) -> dbc.AccordionItem:
    status   = step.get("status", "NOT_STARTED")
    meta     = STATUS_META.get(status, STATUS_META["NOT_STARTED"])
    detail   = step.get("detail", "")
    ts       = step.get("updated_at", "")
    is_async = step.get("is_async", False)

    left_color = {
        "COMPLETED":       "#198754",
        "LIKELY_COMPLETE": "#0dcaf0",
        "IN_PROGRESS":     "#ffc107",
        "WARNING":         "#ffc107",
        "FAILED":          "#dc3545",
        "NOT_STARTED":     "#dee2e6",
        "SKIPPED":         "#dee2e6",
    }.get(status, "#dee2e6")
    num_text_color = "white" if status not in ("NOT_STARTED", "SKIPPED") else "#6c757d"

    title = html.Span([
        html.Span(
            str(step["seq"]),
            style={
                "display": "inline-flex", "alignItems": "center", "justifyContent": "center",
                "width": "22px", "height": "22px", "borderRadius": "50%",
                "backgroundColor": left_color, "color": num_text_color,
                "fontSize": "11px", "fontWeight": "700", "flexShrink": "0", "marginRight": "8px",
            }
        ),
        html.I(className=f"fa-solid {step['icon']} me-2 text-muted"),
        html.Span(step["label"], style={"fontWeight": "600", "fontSize": "14px"}),
        html.Span(" (async)", className="ms-1 text-muted small fst-italic") if is_async else None,
        dbc.Badge(
            [html.I(className=f"fa-solid {meta['icon']} me-1"), meta["label"]],
            color=meta["color"], pill=True,
            style={"fontSize": "10px", "marginLeft": "auto", "flexShrink": "0"},
        ),
    ], style={"display": "flex", "alignItems": "center", "width": "100%", "gap": "0"})

    body_children = [html.P(step["description"], className="small text-muted mb-2")]

    # Inline validation checks — embedded per step from job1_check
    checks = step.get("checks", [])
    if checks:
        check_rows = []
        for icon, label, c in checks:
            ok      = c.get("ok", False)
            detail_txt = c.get("label", "—")
            color   = "text-success" if ok else "text-danger"
            fa_icon = "fa-circle-check" if ok else "fa-circle-xmark"
            check_rows.append(
                html.Div([
                    html.I(className=f"fa-solid {fa_icon} {color} me-2", style={"width": "14px"}),
                    html.I(className=f"fa-solid {icon} me-2 text-muted", style={"width": "14px"}),
                    html.Span(f"{label}: ", className="small fw-semibold me-1"),
                    html.Span(detail_txt, className="small text-muted"),
                ], className="d-flex align-items-center mb-1")
            )
        body_children.append(
            html.Div(
                check_rows,
                className="mt-2 mb-1 ps-2 border-start border-2",
                style={"borderColor": "#dee2e6"},
            )
        )

    if detail and detail != "Not yet started":
        body_children.append(
            html.Div([
                html.I(className="fa-solid fa-circle-info me-1 text-secondary"),
                html.Span(detail, className="small"),
            ], className="mb-1")
        )
    if ts:
        body_children.append(
            html.Small([html.I(className="fa-regular fa-clock me-1"), f"Updated: {ts}"],
                       className="text-muted")
        )

    return dbc.AccordionItem(
        html.Div(body_children),
        title=title,
        item_id=step["step_id"],
    )


def _group_badge(group_steps: list[dict]) -> dbc.Badge:
    completed = sum(1 for s in group_steps if s["status"] in DONE_STATUSES)
    total     = len(group_steps)
    running   = any(s["status"] == "IN_PROGRESS" for s in group_steps)
    failed    = any(s["status"] == "FAILED"       for s in group_steps)
    warning   = any(s["status"] == "WARNING"      for s in group_steps)

    if completed == total:
        return dbc.Badge([html.I(className="fa-solid fa-circle-check me-1"), f"{completed}/{total} Complete"],
                         color="success", pill=True, style={"fontSize": "11px"})
    if failed:
        return dbc.Badge([html.I(className="fa-solid fa-circle-xmark me-1"), "Failed"],
                         color="danger", pill=True, style={"fontSize": "11px"})
    if running:
        return dbc.Badge([html.I(className="fa-solid fa-spinner fa-spin me-1"), "Running…"],
                         color="warning", pill=True, style={"fontSize": "11px"})
    if warning:
        return dbc.Badge([html.I(className="fa-solid fa-triangle-exclamation me-1"), f"{completed}/{total} Complete"],
                         color="warning", pill=True, style={"fontSize": "11px"})
    return dbc.Badge(f"{completed}/{total} Complete", color="secondary", pill=True, style={"fontSize": "11px"})


def _job_column(group: int, g_steps: list[dict], action: dict,
                prereqs: list[dict] | None = None) -> dbc.Col:
    """Render one column (Data Setup or AI Setup) with prereqs + run button inline, then accordion."""
    meta         = GROUP_META[group]
    btn_id       = "job1-trigger-btn" if group == 1 else "job2-trigger-btn"
    result_id    = "job1-trigger-result" if group == 1 else "job2-trigger-result"
    btn_color    = "primary" if group == 1 else "success"
    border_color = meta["border"]

    a          = action.get("action", "none")
    job_id     = action.get("job_id")
    no_job     = job_id is None
    prereqs_ok = all(p.get("ok", False) for p in (prereqs or []))

    active_items = [s["step_id"] for s in g_steps if s["status"] not in DONE_STATUSES]
    accordion = dbc.Accordion(
        [_step_accordion_item(s) for s in g_steps],
        always_open=True,
        active_item=active_items,
        className="mb-0",
    )

    # ── Run button — always a button, disabled state varies ──────────────────
    if a == "done":
        run_btn = dbc.Button(
            [html.I(className="fa-solid fa-circle-check me-2"), "Complete"],
            id=btn_id, color="success", outline=True,
            disabled=True, size="sm", style={"minWidth": "130px"},
        )
        run_hint = None

    elif a == "running":
        run_url = (action.get("active_run") or {}).get("url", "")
        run_btn = dbc.Button(
            [dbc.Spinner(size="sm", className="me-2"), "Running…"],
            id=btn_id, color=btn_color,
            disabled=True, size="sm", style={"minWidth": "130px"},
        )
        run_hint = html.A(
            [html.I(className="fa-solid fa-arrow-up-right-from-square me-1"), "View run"],
            href=run_url, target="_blank", className="small d-block text-center mt-1",
        ) if run_url else None

    else:
        btn_disabled = no_job or not prereqs_ok
        icon  = "fa-rotate-right" if "Re-run" in action.get("label", "") else "fa-play"
        label = action.get("label", "Run")
        run_btn = dbc.Button(
            [html.I(className=f"fa-solid {icon} me-2"), label],
            id=btn_id, color=btn_color,
            disabled=btn_disabled, size="sm", style={"minWidth": "130px"},
        )
        if no_job:
            run_hint = html.Small(
                [html.I(className="fa-solid fa-triangle-exclamation me-1 text-danger"),
                 "Bundle not deployed"],
                className="d-block text-center text-danger mt-1",
            )
        elif not prereqs_ok:
            run_hint = html.Small(
                [html.I(className="fa-solid fa-lock me-1 text-muted"),
                 "Prerequisites not met"],
                className="d-block text-center text-muted mt-1",
            )
        else:
            run_hint = None

    run_area = html.Div(
        [run_btn, run_hint],
        className="d-flex flex-column align-items-center",
    )

    # ── Card layout: [prereq section | run button] then accordion ────────────
    card = dbc.Card([
        dbc.CardHeader(
            dbc.Row([
                dbc.Col([
                    html.I(className=f"fa-solid {meta['icon']} me-2"),
                    html.Strong(meta["label"], style={"fontSize": "14px"}),
                ], width="auto"),
                dbc.Col(_group_badge(g_steps), width="auto", className="ms-auto"),
            ], align="center"),
            style={"background": meta["bg"], "borderBottom": f"2px solid {border_color}"},
        ),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(_prereq_section(prereqs) if prereqs else None, className="pe-2"),
                dbc.Col(run_area, width="auto",
                        className="d-flex align-items-center border-start ps-3"),
            ], align="center", className="mb-3 g-0"),
            html.Div(id=result_id, className="mb-2"),
            html.Div(accordion),
        ], className="p-2"),
    ], style={"borderTop": f"3px solid {border_color}", "height": "100%"})
    return dbc.Col(card, md=6, className="mb-3")


def build_home_tab_content(
    steps: list[dict],
    last_refreshed: str,
    all_done: bool,
    action1: dict | None = None,
    action2: dict | None = None,
    prereqs1: list[dict] | None = None,
    prereqs2: list[dict] | None = None,
) -> html.Div:
    completed  = sum(1 for s in steps if s["status"] in DONE_STATUSES)
    total      = len(steps)
    pct        = int(completed / total * 100)
    prog_color = "success" if all_done else ("warning" if completed > 0 else "secondary")

    banner = dbc.Alert(
        [html.I(className="fa-solid fa-circle-check me-2 text-success"),
         html.Strong("All prerequisites ready — "),
         "ICD-10 Analyzer and Care Gap Advisor are fully operational."],
        color="success", className="mb-3 py-2"
    ) if all_done else dbc.Alert(
        [html.I(className="fa-solid fa-circle-info me-2"),
         html.Strong("Setup in progress. "),
         "Tabs may be limited until setup completes. Click Refresh to update status."],
        color="info", className="mb-3 py-2"
    )

    groups: dict[int, list[dict]] = {}
    for step in steps:
        groups.setdefault(step.get("group", 1), []).append(step)

    g1_steps = groups.get(1, [])
    g2_steps = groups.get(2, [])

    return html.Div([
        banner,

        dbc.Row([
            dbc.Col([
                html.Div(
                    [html.Strong(f"{completed}"), f" of {total} steps complete"],
                    className="small text-muted mb-1"
                ),
                dbc.Progress(
                    value=pct, color=prog_color,
                    striped=not all_done, animated=not all_done,
                    style={"height": "10px"},
                ),
            ], width=8),
            dbc.Col(
                html.Small(f"Last updated: {last_refreshed}", className="text-muted"),
                width=4, className="text-end",
            ),
        ], align="center", className="mb-3"),

        dbc.Row([
            _job_column(1, g1_steps, action1 or {"action": "none"}, prereqs=prereqs1),
            _job_column(2, g2_steps, action2 or {"action": "none"}, prereqs=prereqs2),
        ], className="g-3"),

        html.Div([
            html.Hr(),
            dbc.Alert([
                html.Strong("To start setup, run:  "),
                html.Code(
                    "databricks bundle deploy --profile <your-profile>  &&  "
                    "databricks bundle run bootstrap_workflow --profile <your-profile>",
                    style={"fontSize": "12px"}
                ),
            ], color="secondary", className="mb-0 py-2"),
        ]) if completed == 0 else None,
    ])

# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------
def _settings_layout() -> dbc.Container:
    def _row(label: str, value: str, icon: str, warn: bool = False) -> dbc.Row:
        return dbc.Row([
            dbc.Col(
                html.Span([
                    html.I(className=f"fa-solid {icon} me-2 text-muted"),
                    html.Span(label, className="small fw-semibold"),
                ]),
                width=5,
            ),
            dbc.Col(
                dbc.Badge(
                    value or "—",
                    color="danger" if warn else "light",
                    text_color="white" if warn else "dark",
                    className="font-monospace",
                    style={"fontSize": "12px", "fontWeight": "400"},
                ),
                width=7,
            ),
        ], className="mb-2 align-items-center")

    wh_missing = not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>"

    return dbc.Container([
        dbc.Alert(
            [
                html.I(className="fa-solid fa-circle-info me-2"),
                "These values are set in ",
                html.Code("app.yaml"),
                " at deploy time. To change them, update ",
                html.Code("app.yaml"),
                " and redeploy the app. See ",
                html.Strong("INSTALLATION.md"),
                " → Pre-Installation for the full list of values to edit.",
            ],
            color="info", className="mb-4 py-2 small",
        ),

        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-database me-2"),
                html.Strong("Unity Catalog"),
            ]),
            dbc.CardBody([
                _row("Catalog",  CATALOG or "—", "fa-layer-group"),
                _row("Schema",   SCHEMA  or "—", "fa-table"),
            ]),
        ], className="mb-3"),

        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-server me-2"),
                html.Strong("Infrastructure"),
            ]),
            dbc.CardBody([
                _row("SQL Warehouse ID", WAREHOUSE_ID or "Not set", "fa-warehouse", warn=wh_missing),
                dbc.Alert(
                    [
                        html.I(className="fa-solid fa-triangle-exclamation me-2"),
                        "Warehouse ID is not configured. Set ",
                        html.Code("DATABRICKS_WAREHOUSE_ID"),
                        " in ",
                        html.Code("app.yaml"),
                        " and redeploy.",
                    ],
                    color="danger", className="mt-2 mb-0 py-2 small",
                ) if wh_missing else None,
            ]),
        ], className="mb-3"),

        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-robot me-2"),
                html.Strong("AI Configuration"),
            ]),
            dbc.CardBody([
                _row("Care Gap Model",              FMAPI_ENDPOINT,                    "fa-microchip"),
                _row("KA Serving Endpoint",        KA_ENDPOINT_NAME    or "Not set",  "fa-robot",
                     warn=not KA_ENDPOINT_NAME),
                _row("Data Setup Job Name",        DATA_SETUP_JOB_NAME or "—",        "fa-play"),
                _row("AI Setup Job Name",          AI_SETUP_JOB_NAME   or "—",        "fa-play"),
            ]),
        ], className="mb-3"),

        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-id-badge me-2"),
                html.Strong("App Identity"),
            ]),
            dbc.CardBody(
                _row("Service Principal", _app_sp_name or "Not resolved", "fa-user-gear",
                     warn=not _app_sp_name)
            ),
        ], className="mb-3"),

    ], fluid=True, className="pt-2")

# ---------------------------------------------------------------------------
# Shared layout components
# ---------------------------------------------------------------------------
NAVBAR = dbc.Navbar(
    dbc.Container([
        dbc.NavbarBrand(
            [html.I(className="fa-solid fa-heart-pulse me-2", style={"color": BRAND_ORANGE}),
             "Clinical AI Demo"],
            className="fw-bold fs-5"
        ),
        dbc.Badge("Powered by Databricks", color="warning", pill=True, className="ms-auto"),
    ], fluid=True),
    color="dark", dark=True, className="mb-0 shadow-sm"
)

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css",
    ],
    suppress_callback_exceptions=True,
)
server = app.server

app.layout = html.Div([
    NAVBAR,
    dbc.Tabs(
        id="main-tabs",
        active_tab="tab-home",
        className="px-3 pt-2 border-bottom",
        children=[
            dbc.Tab(label="Home",             tab_id="tab-home",     label_style={"fontWeight": "600"}),
            dbc.Tab(label="ICD-10 Analyzer",  tab_id="tab-icd10"),
            dbc.Tab(label="Care Gap Advisor",  tab_id="tab-caregap"),
            dbc.Tab(label="Settings",          tab_id="tab-settings"),
        ]
    ),
    html.Div(id="tab-content", className="p-3"),

    dcc.Store(id="catalog-store",     data=CATALOG),
    dcc.Store(id="schema-store",      data=SCHEMA),
    dcc.Store(id="ka-endpoint-store", data=""),
    dcc.Store(id="patient-store",     data=[]),
    dcc.Store(id="all-done-store",    data=False),
    dcc.Store(id="job1-action-store", data={}),
    dcc.Store(id="job2-action-store", data={}),
])

# ---------------------------------------------------------------------------
# Tab routing
# ---------------------------------------------------------------------------
@callback(
    Output("tab-content", "children"),
    Input("main-tabs",         "active_tab"),
    State("patient-store",     "data"),
    State("ka-endpoint-store", "data"),
    State("all-done-store",    "data"),
    State("catalog-store",     "data"),
    State("schema-store",      "data"),
)
def render_tab(active_tab, patients, ka_endpoint, all_done, catalog, schema):
    if active_tab == "tab-home":
        return _home_shell()

    if active_tab == "tab-icd10":
        if not patients:
            return _not_ready_card("ICD-10 Analyzer", "patient records ingested (step 2)")
        return _icd10_layout(patients)

    if active_tab == "tab-caregap":
        if not patients:
            return _not_ready_card("Care Gap Advisor", "patient records ingested (step 2)")
        return _gap_layout(patients)

    if active_tab == "tab-settings":
        return _settings_layout()

    return html.Div()


def _home_shell() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("Demo Environment Status", className="mb-0 fw-bold"), width="auto"),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"), "Refresh Now"],
                    id="home-refresh-btn", color="outline-secondary", size="sm",
                ),
                width="auto", className="ms-auto",
            ),
        ], align="center", className="mb-3"),

        dbc.Spinner(
            html.Div(id="home-step-content"),
            color="primary",
            spinner_style={"width": "2rem", "height": "2rem"},
        ),
    ])


def _not_ready_card(tab_name: str, waiting_for: str) -> dbc.Container:
    return dbc.Container(
        dbc.Alert(
            [html.I(className="fa-solid fa-hourglass-half me-2"),
             html.Strong(f"{tab_name} not ready. "),
             f"Waiting for {waiting_for}. Check the Home tab for setup progress."],
            color="warning", className="mt-3"
        ), fluid=True
    )

# ---------------------------------------------------------------------------
# Home tab refresh callback
# ---------------------------------------------------------------------------
@callback(
    Output("home-step-content",  "children"),
    Output("all-done-store",     "data"),
    Output("ka-endpoint-store",  "data"),
    Output("patient-store",      "data"),
    Output("job1-action-store",  "data"),
    Output("job2-action-store",  "data"),
    Input("home-refresh-btn",    "n_clicks"),
    Input("catalog-store",       "data"),
    Input("schema-store",        "data"),
    prevent_initial_call=False,
)
def refresh_home(n_clicks, catalog, schema):
    cat = catalog or CATALOG
    sch = schema  or SCHEMA

    if not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>":
        return html.Div(
            dbc.Alert(
                [html.I(className="fa-solid fa-warehouse me-2"),
                 html.Strong("SQL Warehouse not configured. "),
                 "Set ", html.Code("DATABRICKS_WAREHOUSE_ID"), " in ",
                 html.Code("app.yaml"), " and redeploy. See the ",
                 html.Strong("Settings"), " tab for current configuration."],
                color="danger", className="py-2",
            )
        ), False, "", [], {}, {}

    steps = _check_step_statuses(cat, sch)

    job1_id, job2_id = find_job_ids()
    job1_active = get_active_run(job1_id) if job1_id else None
    job2_active = get_active_run(job2_id) if job2_id else None

    # Overlay IN_PROGRESS when a job is actively running for not-yet-complete steps
    for step in steps:
        if step["status"] == "NOT_STARTED":
            if step["step_id"] in JOB1_STEPS and job1_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 1 is running…"
            elif step["step_id"] in JOB2_STEPS and job2_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 2 is running…"

    all_done  = all(s["status"] in DONE_STATUSES for s in steps)
    job1_done = all(s["status"] in DONE_STATUSES for s in steps if s["step_id"] in JOB1_STEPS)
    now       = datetime.now().strftime("%H:%M:%S")

    # Load patients as soon as Job 1 (data setup) is complete — don't wait for Job 2
    patients = []
    if job1_done:
        try:
            patients = load_patients(cat, sch)
        except Exception as e:
            logger.warning(f"Patient load failed: {e}")

    # KA endpoint always comes from env var — set at deploy time by setup_resources.py
    ka_endpoint = KA_ENDPOINT_NAME

    # Prerequisite checks — shown above each job card, not inside the step accordion
    wh_ok = bool(WAREHOUSE_ID) and WAREHOUSE_ID not in ("", "<your-warehouse-id>")
    prereqs1 = [
        {"icon": "fa-warehouse", "label": "SQL Warehouse", "value": WAREHOUSE_ID, "ok": wh_ok},
    ]
    ka_chk = _chk_ka_endpoint(KA_ENDPOINT_NAME)
    prereqs2 = [
        {"icon": "fa-robot", "label": "KA Endpoint", "value": KA_ENDPOINT_NAME, "ok": ka_chk["ok"]},
    ]

    act1    = _job1_action(steps, job1_id, job1_active)
    act2    = _job2_action(steps, job2_id, job2_active)
    content = build_home_tab_content(steps, now, all_done, act1, act2, prereqs1, prereqs2)
    return content, all_done, ka_endpoint, patients, act1, act2

# ---------------------------------------------------------------------------
# Job trigger callback
# ---------------------------------------------------------------------------
def _trigger_job(action: dict) -> tuple:
    job_id = action.get("job_id")
    if not job_id:
        return dbc.Alert("Job not found — deploy the bundle first.", color="danger", className="py-2 small"), False
    try:
        waiter  = w.jobs.run_now(job_id=job_id)
        run_url = ""
        try:
            run_url = w.jobs.get_run(run_id=waiter.run_id).run_page_url or ""
        except Exception:
            pass
        return dbc.Alert(
            [html.I(className="fa-solid fa-circle-check me-2 text-success"),
             html.Strong(f"{action.get('job_name', 'Job')} triggered — "),
             html.A("view run →", href=run_url, target="_blank") if run_url else html.Span("check Workflows UI")],
            color="success", className="py-2 small d-flex align-items-center",
        ), True
    except Exception as e:
        logger.error(f"Job trigger failed: {e}")
        return dbc.Alert(f"Failed to trigger: {e}", color="danger", className="py-2 small"), False


@callback(
    Output("job1-trigger-result", "children"),
    Output("job1-trigger-btn",    "disabled"),
    Input("job1-trigger-btn",     "n_clicks"),
    State("job1-action-store",    "data"),
    prevent_initial_call=True,
)
def handle_job1_trigger(n_clicks, action):
    if not n_clicks or not action:
        return dash.no_update, dash.no_update
    return _trigger_job(action)


@callback(
    Output("job2-trigger-result", "children"),
    Output("job2-trigger-btn",    "disabled"),
    Input("job2-trigger-btn",     "n_clicks"),
    State("job2-action-store",    "data"),
    prevent_initial_call=True,
)
def handle_job2_trigger(n_clicks, action):
    if not n_clicks or not action:
        return dash.no_update, dash.no_update
    return _trigger_job(action)


# ---------------------------------------------------------------------------
# ICD-10 Analyzer tab
# ---------------------------------------------------------------------------
def _icd10_layout(patients: list[dict]) -> dbc.Container:
    options = _patient_options(patients)
    return dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Select Patient", className="fw-semibold mb-1"),
                dcc.Dropdown(id="icd10-patient-select", options=options,
                             placeholder="Choose a patient...", className="mb-3"),
                dbc.Alert(
                    [html.I(className="fa-solid fa-clock-rotate-left me-2"),
                     "Knowledge Assistant is still indexing PDFs — ICD-10 results may be limited "
                     "until step 5 completes."],
                    id="icd10-sync-banner", color="info", is_open=False, className="mb-2 py-2 small"
                ),
                html.Label("Clinical Record", className="fw-semibold mb-1"),
                dcc.Textarea(
                    id="icd10-clinical-record",
                    style={"width": "100%", "height": "320px",
                           "fontSize": "12px", "fontFamily": "monospace"},
                    readOnly=True,
                    placeholder="Select a patient to load their clinical note...",
                    className="mb-3"
                ),
                dbc.Button(
                    [html.I(className="fa-solid fa-magnifying-glass me-2"), "Analyze ICD-10 Codes"],
                    id="icd10-analyze-btn", color="primary", disabled=True, className="w-100"
                ),
            ], width=5),
            dbc.Col([
                html.Label("ICD-10 Code Suggestions", className="fw-semibold mb-1"),
                dbc.Spinner(
                    html.Div(id="icd10-results",
                             children=dbc.Alert("Select a patient and click Analyze.",
                                                color="secondary")),
                    color="primary",
                ),
            ], width=7),
        ], className="mt-2 g-4"),
    ], fluid=True)


@callback(
    Output("icd10-clinical-record", "value"),
    Output("icd10-analyze-btn",     "disabled"),
    Input("icd10-patient-select",   "value"),
    State("catalog-store",          "data"),
    State("schema-store",           "data"),
    prevent_initial_call=True,
)
def load_record_icd10(patient_id, catalog, schema):
    if not patient_id:
        return "", True
    record = get_patient_record(patient_id, catalog or CATALOG, schema or SCHEMA)
    return (record["clinicalrecord"], False) if record else ("Record not found.", True)


@callback(
    Output("icd10-results",       "children"),
    Output("icd10-sync-banner",   "is_open"),
    Input("icd10-analyze-btn",    "n_clicks"),
    State("icd10-clinical-record","value"),
    State("ka-endpoint-store",    "data"),
    prevent_initial_call=True,
)
def run_icd10(n_clicks, text, ka_ep):
    if not n_clicks or not text:
        return dash.no_update, False
    if not ka_ep:
        return dbc.Alert(
            "Knowledge Assistant endpoint not configured. Check Home tab step 4.", color="danger"
        ), False
    try:
        result  = call_ka_endpoint(text, ka_ep)
        syncing = any(p in result.lower() for p in
                      ("no documents", "not yet indexed", "indexing", "no relevant"))
        return dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-file-medical me-2"),
                html.Strong("ICD-10 Analysis Results"),
                dbc.Badge("Knowledge Assistant", color="primary", className="ms-2 float-end"),
            ]),
            dbc.CardBody(html.Pre(result, style={"whiteSpace": "pre-wrap",
                                                  "fontSize": "13px", "margin": 0})),
        ], className="shadow-sm"), syncing
    except Exception as e:
        logger.error(f"ICD-10 analysis: {e}")
        return dbc.Alert(f"Analysis failed: {e}", color="danger"), False

# ---------------------------------------------------------------------------
# Care Gap Advisor tab
# ---------------------------------------------------------------------------
def _gap_layout(patients: list[dict]) -> dbc.Container:
    options = _patient_options(patients)
    return dbc.Container([
        dbc.Row([
            dbc.Col([
                html.Label("Select Patient", className="fw-semibold mb-1"),
                dcc.Dropdown(id="gap-patient-select", options=options,
                             placeholder="Choose a patient...", className="mb-3"),
                dbc.Button(
                    [html.I(className="fa-solid fa-stethoscope me-2"), "Identify Care Gaps"],
                    id="gap-analyze-btn", color="success", disabled=True, className="w-100"
                ),
                html.Small(
                    "Compares patient record against ADA, ACC/AHA, GOLD, NCCN, KDIGO guidelines.",
                    className="text-muted d-block mt-2"
                ),
            ], width=4),
            dbc.Col([
                html.Label("Care Gap Findings", className="fw-semibold mb-1"),
                dbc.Spinner(
                    html.Div(id="gap-results",
                             children=dbc.Alert("Select a patient and click Identify Care Gaps.",
                                                color="secondary")),
                    color="success",
                ),
            ], width=8),
        ], className="mt-2 g-4"),
    ], fluid=True)


@callback(
    Output("gap-analyze-btn",   "disabled"),
    Input("gap-patient-select", "value"),
    prevent_initial_call=True,
)
def enable_gap_btn(patient_id):
    return not bool(patient_id)


@callback(
    Output("gap-results",       "children"),
    Input("gap-analyze-btn",    "n_clicks"),
    State("gap-patient-select", "value"),
    State("catalog-store",      "data"),
    State("schema-store",       "data"),
    prevent_initial_call=True,
)
def run_gaps(n_clicks, patient_id, catalog, schema):
    if not n_clicks or not patient_id:
        return dash.no_update
    cat = catalog or CATALOG
    sch = schema  or SCHEMA
    try:
        patient = get_patient_record(patient_id, cat, sch)
        rules   = get_care_gap_rules(cat, sch)
        gaps    = call_care_gap_model(patient, rules)

        if not gaps:
            return dbc.Alert(
                [html.I(className="fa-solid fa-circle-check me-2"),
                 f"No care gaps identified for {patient_id}."],
                color="success"
            )

        P = {"HIGH": "danger", "MEDIUM": "warning", "LOW": "info"}
        cards = [
            dbc.Card([
                dbc.CardHeader([
                    dbc.Badge(g.get("priority", "?"),
                               color=P.get(g.get("priority", ""), "secondary"), className="me-2"),
                    html.Strong(g.get("gap_name", "—")),
                    html.Small(f"  {g.get('condition', '')}", className="text-muted ms-1"),
                ]),
                dbc.CardBody([
                    html.P([html.Strong("Finding: "),   g.get("finding", "—")],           className="mb-1 small"),
                    html.P([html.Strong("Action: "),    g.get("recommended_action", "—")], className="mb-1 small"),
                    html.P([html.Strong("Guideline: "), g.get("guideline", "—")],          className="mb-0 small text-muted"),
                ]),
            ], outline=True, color=P.get(g.get("priority", ""), "secondary"), className="mb-2 shadow-sm")
            for g in gaps
        ]
        return html.Div([
            dbc.Alert(
                [html.I(className="fa-solid fa-triangle-exclamation me-2"),
                 f"{len(gaps)} care gap(s) identified for {patient_id}"],
                color="warning", className="mb-3"
            ),
            *cards,
        ])
    except Exception as e:
        logger.error(f"Care gap analysis: {e}")
        return dbc.Alert(f"Analysis failed: {e}", color="danger")

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _patient_options(patients: list[dict]) -> list[dict]:
    return [
        {"label": f"{p['patient_id']} — {p['gender']}, DOB {p['dob']}", "value": p["patient_id"]}
        for p in patients
    ]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASH_PORT", "8050")), debug=False)
