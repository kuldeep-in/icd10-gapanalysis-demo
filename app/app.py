import os
import json
import logging
import re
from datetime import datetime, timezone

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
CATALOG             = os.getenv("UC_CATALOG",  "my_catalog")
SCHEMA              = os.getenv("UC_SCHEMA",   "icd10_care_gap")
AI_GATEWAY_ROUTE    = os.getenv("AI_GATEWAY_ROUTE", "databricks-claude-sonnet-4-6")
WAREHOUSE_ID        = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
DATA_SETUP_JOB_NAME = os.getenv("DATA_SETUP_JOB_NAME", "ICD-10 Gap Demo — Data Setup")
AI_SETUP_JOB_NAME   = os.getenv("AI_SETUP_JOB_NAME",   "ICD-10 Gap Demo — AI Setup")

BRAND_ORANGE = "#E87722"

JOB1_STEPS = {"create_catalog", "ingest_patient_data", "load_icd10_pdfs"}
JOB2_STEPS = {"create_knowledge_assistant", "configure_ai_gateway"}

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
        "label":       "Unity Catalog & Database Setup",
        "description": "Create catalog, schemas, Delta tables (patient_records, care_gap_rules, "
                       "bootstrap_status) and the icd10_reference UC Volume.",
        "icon":        "fa-database",
    },
    {
        "step_id":     "ingest_patient_data",
        "seq":         2,
        "group":       1,
        "label":       "Patient Clinical Notes Ingested",
        "description": "Load 25 synthetic SOAP-format patient records from "
                       "data/patient_records.json into the patient_records Delta table.",
        "icon":        "fa-notes-medical",
    },
    {
        "step_id":     "load_icd10_pdfs",
        "seq":         3,
        "group":       1,
        "label":       "ICD-10 Reference PDFs Uploaded to Volume",
        "description": "Copy ICD-10 PDF reference files from the Git repo (data/icd10_pdfs/) "
                       "into the Unity Catalog Volume — prerequisite for Knowledge Assistant indexing.",
        "icon":        "fa-file-pdf",
    },
    {
        "step_id":     "create_knowledge_assistant",
        "seq":         4,
        "group":       2,
        "label":       "Knowledge Assistant Created",
        "description": "Create the ICD-10 Knowledge Assistant agent via Databricks SDK, "
                       "attach the UC Volume as a knowledge source, and trigger PDF indexing.",
        "icon":        "fa-robot",
    },
    {
        "step_id":     "ka_pdf_sync",
        "seq":         5,
        "group":       2,
        "label":       "Knowledge Assistant PDF Indexing",
        "description": "Asynchronous background process — the KA indexes all ICD-10 reference PDFs. "
                       "This typically takes 30–60 minutes. ICD-10 Analyzer works once this completes.",
        "icon":        "fa-brain",
        "is_async":    True,
    },
    {
        "step_id":     "configure_ai_gateway",
        "seq":         6,
        "group":       2,
        "label":       "AI Gateway Route Configured",
        "description": "Create the AI Gateway serving endpoint for the care gap foundation model "
                       "(Claude via Anthropic or FMAPI). Required for Care Gap Advisor.",
        "icon":        "fa-network-wired",
    },
]

GROUP_META = {
    1: {"label": "Job 1 — Data Setup", "icon": "fa-database", "border": "#0d6efd", "bg": "#f0f4ff"},
    2: {"label": "Job 2 — AI Setup",   "icon": "fa-robot",    "border": "#198754", "bg": "#f0fff4"},
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


def get_ka_endpoint_name(catalog: str = CATALOG, schema: str = SCHEMA) -> str:
    try:
        rows = execute_sql(
            f"SELECT details FROM `{catalog}`.`{schema}`.bootstrap_status "
            f"WHERE step = 'create_knowledge_assistant' AND status = 'COMPLETED' "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
        if rows:
            return json.loads(rows[0]["details"]).get("endpoint_name", "")
    except Exception as e:
        logger.warning(f"KA endpoint lookup: {e}")
    return ""


def _check_job1_artifacts(catalog: str, schema: str) -> dict:
    result: dict = {}

    for table, key in [("patient_records", "patient_records"), ("care_gap_rules", "care_gap_rules")]:
        try:
            r   = execute_sql(f"SELECT COUNT(*) as cnt FROM `{catalog}`.`{schema}`.`{table}`")
            cnt = int(r[0]["cnt"]) if r else 0
            result[key] = {"ok": cnt > 0, "cnt": cnt,
                           "label": f"{cnt} row{'s' if cnt != 1 else ''}"}
        except Exception as e:
            result[key] = {"ok": False, "cnt": 0, "label": str(e)[:120]}

    try:
        entries = list(w.files.list_directory_contents(
            f"/Volumes/{catalog}/{schema}/icd10_reference_pdfs"
        ))
        cnt = len(entries)
        result["volume_pdfs"] = {"ok": cnt > 0, "cnt": cnt,
                                  "label": f"{cnt} file{'s' if cnt != 1 else ''}"}
    except Exception as e:
        result["volume_pdfs"] = {"ok": False, "cnt": 0, "label": str(e)[:120]}

    result["complete"] = (
        result.get("patient_records", {}).get("ok", False) and
        result.get("care_gap_rules",  {}).get("ok", False)
    )
    return result


def _validate_job2_complete(catalog: str, schema: str) -> bool:
    try:
        rows = execute_sql(
            f"SELECT step FROM `{catalog}`.`{schema}`.bootstrap_status "
            f"WHERE step IN ('create_knowledge_assistant', 'configure_ai_gateway') "
            f"AND status IN ('COMPLETED', 'SKIPPED')"
        )
        completed = {r["step"] for r in rows}
        return ("create_knowledge_assistant" in completed and
                "configure_ai_gateway" in completed)
    except Exception as e:
        logger.debug(f"Job 2 artifact check failed: {e}")
        return False


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


def determine_trigger_action(
    steps: list[dict],
    job1_id: int | None,
    job2_id: int | None,
    job1_active: dict | None,
    job2_active: dict | None,
) -> dict:
    by_id = {s["step_id"]: s["status"] for s in steps}

    job1_done   = all(by_id.get(s) in DONE_STATUSES for s in JOB1_STEPS)
    job2_done   = all(by_id.get(s) in DONE_STATUSES for s in JOB2_STEPS)
    job1_failed = any(by_id.get(s) == "FAILED" for s in JOB1_STEPS)
    job2_failed = any(by_id.get(s) == "FAILED" for s in JOB2_STEPS)

    if job1_done and job2_done:
        return {"action": "all_done", "job_id": None, "job_name": "", "label": "Setup Complete",
                "description": "All steps are done.", "active_run": None}

    if not job1_done:
        if job1_active:
            return {"action": "running", "job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME,
                    "label": "Data Setup Running…", "description": "Job 1 is in progress.",
                    "active_run": job1_active}
        return {
            "action":      "run_job1",
            "job_id":      job1_id,
            "job_name":    DATA_SETUP_JOB_NAME,
            "label":       "Re-run Data Setup (Job 1)" if job1_failed else "Run Data Setup (Job 1)",
            "description": "Creates catalog, ingests patient records, uploads ICD-10 PDFs",
            "active_run":  None,
        }

    if job2_active:
        return {"action": "running", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "AI Setup Running…", "description": "Job 2 is in progress.",
                "active_run": job2_active}
    return {
        "action":      "run_job2",
        "job_id":      job2_id,
        "job_name":    AI_SETUP_JOB_NAME,
        "label":       "Re-run AI Setup (Job 2)" if job2_failed else "Run AI Setup (Job 2)",
        "description": "Creates Knowledge Assistant and configures AI Gateway (serverless)",
        "active_run":  None,
    }

# ---------------------------------------------------------------------------
# Bootstrap step status resolution
# ---------------------------------------------------------------------------
def _resolve_ka_sync(ka_row: dict) -> tuple[str, str]:
    try:
        details         = json.loads(ka_row.get("details", "{}"))
        ka_name         = details.get("ka_name", "")
        sync_started_at = details.get("sync_started_at", "")
        pdf_count       = details.get("pdf_count", 0)

        if pdf_count == 0:
            return "WARNING", "No PDFs were found in the volume when KA was created — upload PDFs and re-run bootstrap step 3."

        if ka_name:
            try:
                sources = list(w.knowledge_assistants.list_knowledge_sources(parent=ka_name))
                for src in sources:
                    d      = src.as_dict() if hasattr(src, "as_dict") else {}
                    state  = str(d.get("state", "")).upper()
                    cutoff = d.get("knowledge_cutoff_time", "")
                    if state in ("UPDATED", "COMPLETED", "SUCCESS", "DONE") or cutoff:
                        return "COMPLETED", f"All PDFs indexed successfully ({pdf_count} file(s))"
                    if any(x in state for x in ("FAIL", "ERROR")):
                        return "FAILED", f"Sync failed: {state}"
                    if state:
                        return "IN_PROGRESS", f"Indexing status: {state}"
            except Exception as sdk_err:
                logger.warning(f"KA sync check: {sdk_err}")

        if sync_started_at:
            try:
                sync_dt = datetime.fromisoformat(sync_started_at.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - sync_dt).total_seconds() / 60
                if elapsed > 20:
                    return "LIKELY_COMPLETE", (
                        f"Sync started {int(elapsed)} min ago — likely complete. "
                        "Verify in Databricks Agents UI."
                    )
                return "IN_PROGRESS", (
                    f"Indexing in progress — {int(elapsed)} min elapsed "
                    f"(typically 30–60 min for {pdf_count} file(s))"
                )
            except Exception:
                pass

        return "IN_PROGRESS", "Indexing in progress — check Agents UI for live status"

    except Exception as e:
        return "UNKNOWN", str(e)


def get_bootstrap_step_statuses(catalog: str = CATALOG, schema: str = SCHEMA) -> list[dict]:
    try:
        rows = execute_sql(
            f"SELECT step, status, updated_at, details "
            f"FROM `{catalog}`.`{schema}`.bootstrap_status ORDER BY updated_at"
        )
        db = {r["step"]: r for r in rows}
    except Exception as e:
        logger.warning(f"bootstrap_status unavailable: {e}")
        db = {}

    result = []
    for step in BOOTSTRAP_STEPS:
        sid = step["step_id"]

        if sid == "ka_pdf_sync":
            ka_row = db.get("create_knowledge_assistant")
            if not ka_row or ka_row.get("status") != "COMPLETED":
                status, detail, ts = "NOT_STARTED", "Waiting for step 4 to complete", ""
            else:
                status, detail = _resolve_ka_sync(ka_row)
                ts = ka_row.get("updated_at", "")
        else:
            row = db.get(sid)
            if not row:
                status, detail, ts = "NOT_STARTED", "Not yet started", ""
            else:
                status = row.get("status", "UNKNOWN")
                detail = row.get("details", "")
                ts     = row.get("updated_at", "")

        if detail and detail.strip().startswith("{"):
            try:
                d = json.loads(detail)
                detail = "  |  ".join(
                    f"{k}: {v}" for k, v in d.items()
                    if v and k not in ("ka_name",)
                )
            except Exception:
                pass

        result.append({**step, "status": status, "detail": detail, "updated_at": ts})

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
        name=AI_GATEWAY_ROUTE,
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
    return json.loads(m.group()) if m else []

# ---------------------------------------------------------------------------
# Home tab: accordion step and group header builders
# ---------------------------------------------------------------------------
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


def _group_header(group_num: int, group_steps: list[dict]) -> html.Div:
    meta      = GROUP_META[group_num]
    completed = sum(1 for s in group_steps if s["status"] in DONE_STATUSES)
    total     = len(group_steps)
    running   = any(s["status"] == "IN_PROGRESS" for s in group_steps)
    failed    = any(s["status"] == "FAILED"       for s in group_steps)
    warning   = any(s["status"] == "WARNING"      for s in group_steps)

    if completed == total:
        badge = dbc.Badge(
            [html.I(className="fa-solid fa-circle-check me-1"), f"{completed}/{total} Complete"],
            color="success", pill=True, style={"fontSize": "11px"},
        )
    elif failed:
        badge = dbc.Badge(
            [html.I(className="fa-solid fa-circle-xmark me-1"), "Failed"],
            color="danger", pill=True, style={"fontSize": "11px"},
        )
    elif running:
        badge = dbc.Badge(
            [html.I(className="fa-solid fa-spinner fa-spin me-1"), "Running…"],
            color="warning", pill=True, style={"fontSize": "11px"},
        )
    elif warning:
        badge = dbc.Badge(
            [html.I(className="fa-solid fa-triangle-exclamation me-1"), f"{completed}/{total} Complete"],
            color="warning", pill=True, style={"fontSize": "11px"},
        )
    else:
        badge = dbc.Badge(
            f"{completed}/{total} Complete",
            color="secondary", pill=True, style={"fontSize": "11px"},
        )

    return html.Div(
        dbc.Row([
            dbc.Col([
                html.I(className=f"fa-solid {meta['icon']} me-2"),
                html.Strong(meta["label"], style={"fontSize": "14px"}),
            ], width="auto"),
            dbc.Col(badge, width="auto", className="ms-auto"),
        ], align="center"),
        style={
            "background":   meta["bg"],
            "borderLeft":   f"4px solid {meta['border']}",
            "borderRadius": "6px",
            "padding":      "10px 14px",
            "marginBottom": "8px",
        }
    )


def _job1_status_card(checks: dict) -> html.Div:
    complete = checks.get("complete", False)
    rows_cfg = [
        ("patient_records", "fa-users",     "patient_records table"),
        ("care_gap_rules",  "fa-list-check", "care_gap_rules table"),
        ("volume_pdfs",     "fa-file-pdf",   "icd10_reference_pdfs volume"),
    ]
    check_items = []
    for key, icon, label in rows_cfg:
        c       = checks.get(key, {})
        ok      = c.get("ok", False)
        detail  = c.get("label", "—")
        color   = "text-success" if ok else "text-danger"
        fa_icon = "fa-circle-check" if ok else "fa-circle-xmark"
        check_items.append(
            html.Div([
                html.I(className=f"fa-solid {fa_icon} {color} me-2"),
                html.Span(f"{label}: ", className="fw-semibold"),
                html.Span(detail, className="text-muted"),
            ], className="d-flex align-items-center mb-1 small")
        )

    headline = (
        "All required data confirmed — Job 1 is not needed."
        if complete else
        "Some data is missing — run Job 1 to complete setup."
    )
    return dbc.Alert(
        [
            html.Div([
                html.I(className=f"fa-solid {'fa-shield-check' if complete else 'fa-magnifying-glass'} me-2"),
                html.Strong("Job 1 Validation: "),
                headline,
            ], className="mb-2"),
            *check_items,
        ],
        color="success" if complete else "warning",
        className="py-2 mb-2 small",
    )


def _trigger_panel(action: dict) -> html.Div:
    a = action.get("action", "none")

    if a == "all_done":
        return html.Div()

    if a == "running":
        run_url = (action.get("active_run") or {}).get("url", "")
        return dbc.Alert(
            [
                dbc.Spinner(size="sm", color="warning", className="me-2"),
                html.Strong(f"{action['job_name']} is running — "),
                html.A("View live run →", href=run_url, target="_blank") if run_url else "check Workflows UI",
            ],
            color="warning", className="mb-3 py-2 d-flex align-items-center"
        )

    job_id = action.get("job_id")
    no_job = job_id is None
    icon   = "fa-play" if "Re-run" not in action["label"] else "fa-rotate-right"

    return dbc.Card(
        dbc.CardBody(
            dbc.Row([
                dbc.Col([
                    html.Div(html.Strong(action["label"]), className="mb-1"),
                    html.Div(action["description"], className="small text-muted"),
                    html.Div(
                        [html.I(className="fa-solid fa-triangle-exclamation me-1 text-danger"),
                         "Job not found — deploy the bundle first."],
                        className="small text-danger mt-1"
                    ) if no_job else None,
                ]),
                dbc.Col(
                    dbc.Button(
                        [html.I(className=f"fa-solid {icon} me-2"), action["label"]],
                        id="job-trigger-btn",
                        color="primary" if "job1" in a else "success",
                        disabled=no_job,
                        size="sm",
                    ),
                    width="auto", className="d-flex align-items-center",
                ),
            ], align="center", justify="between"),
        ),
        className="mb-3 border-primary" if "job1" in a else "mb-3 border-success",
        style={"borderLeft": "4px solid"},
    )


def build_home_tab_content(steps: list[dict], last_refreshed: str, all_done: bool,
                           action: dict | None = None,
                           job1_check: dict | None = None) -> html.Div:
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
        g = step.get("group", 1)
        groups.setdefault(g, []).append(step)

    group_sections = []
    for g in sorted(groups.keys()):
        g_steps = groups[g]
        active_items = [s["step_id"] for s in g_steps if s["status"] not in DONE_STATUSES]
        accordion = dbc.Accordion(
            [_step_accordion_item(s) for s in g_steps],
            always_open=True,
            active_item=active_items,
            className="mb-1",
        )
        group_body = [_group_header(g, g_steps)]
        if g == 1 and job1_check is not None:
            group_body.append(_job1_status_card(job1_check))
        group_body.append(html.Div(accordion, className="ps-2 pt-1"))
        group_sections.append(html.Div(group_body, className="mb-4"))

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
                html.Div(
                    html.Small(f"Last updated: {last_refreshed}", className="text-muted"),
                    className="text-end",
                ),
                width=4,
            ),
        ], align="center", className="mb-3"),

        _trigger_panel(action or {"action": "none"}),
        html.Div(id="job-trigger-result", className="mb-2"),
        html.Hr(className="mt-1 mb-3"),
        *group_sections,

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
                _row("Model Endpoint (Care Gap)",  AI_GATEWAY_ROUTE    or "—", "fa-network-wired"),
                _row("Data Setup Job Name",         DATA_SETUP_JOB_NAME or "—", "fa-play"),
                _row("AI Setup Job Name",           AI_SETUP_JOB_NAME   or "—", "fa-play"),
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
    dcc.Store(id="job-action-store",  data={}),
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
    Output("job-action-store",   "data"),
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
        ), False, "", [], {}

    steps = get_bootstrap_step_statuses(cat, sch)

    job1_id, job2_id = find_job_ids()
    job1_active = get_active_run(job1_id) if job1_id else None
    job2_active = get_active_run(job2_id) if job2_id else None

    job1_check = _check_job1_artifacts(cat, sch)
    by_id      = {s["step_id"]: s["status"] for s in steps}

    job1_db_done = all(by_id.get(s) in DONE_STATUSES for s in JOB1_STEPS)
    if not job1_db_done and job1_check["complete"]:
        pr  = job1_check.get("patient_records", {})
        vol = job1_check.get("volume_pdfs", {})
        for step in steps:
            if step["status"] in DONE_STATUSES:
                continue
            sid = step["step_id"]
            if sid == "create_catalog":
                step["status"] = "COMPLETED"
                step["detail"] = f"Catalog `{cat}.{sch}` confirmed"
            elif sid == "ingest_patient_data":
                step["status"] = "COMPLETED"
                step["detail"] = f"Validated — {pr.get('label', 'rows present')}"
            elif sid == "load_icd10_pdfs":
                if vol.get("ok"):
                    step["status"] = "COMPLETED"
                    step["detail"] = f"Validated — {vol.get('label', 'files present')}"
                else:
                    step["status"] = "COMPLETED"
                    step["detail"] = f"Core data present; {vol.get('label', 'volume empty')} — PDFs needed for KA"

    job2_db_done = all(by_id.get(s) in DONE_STATUSES for s in JOB2_STEPS)
    if not job2_db_done and _validate_job2_complete(cat, sch):
        for step in steps:
            if step["step_id"] in JOB2_STEPS and step["status"] not in DONE_STATUSES:
                step["status"] = "COMPLETED"
                step["detail"] = "Validated — endpoints confirmed"

    for step in steps:
        if step["status"] in ("NOT_STARTED", "UNKNOWN"):
            if step["step_id"] in JOB1_STEPS and job1_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 1 is running…"
            elif step["step_id"] in JOB2_STEPS and job2_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 2 is running…"

    all_done = all(s["status"] in DONE_STATUSES for s in steps)
    now      = datetime.now().strftime("%H:%M:%S")

    patients    = []
    ka_endpoint = ""
    if all_done:
        try:
            patients    = load_patients(cat, sch)
            ka_endpoint = get_ka_endpoint_name(cat, sch)
        except Exception as e:
            logger.warning(f"Post-completion data load failed: {e}")

    action  = determine_trigger_action(steps, job1_id, job2_id, job1_active, job2_active)
    content = build_home_tab_content(steps, now, all_done, action, job1_check=job1_check)
    return content, all_done, ka_endpoint, patients, action

# ---------------------------------------------------------------------------
# Job trigger callback
# ---------------------------------------------------------------------------
@callback(
    Output("job-trigger-result", "children"),
    Output("job-trigger-btn",    "disabled"),
    Input("job-trigger-btn",     "n_clicks"),
    State("job-action-store",    "data"),
    prevent_initial_call=True,
)
def handle_job_trigger(n_clicks, action):
    if not n_clicks or not action:
        return dash.no_update, dash.no_update
    job_id = action.get("job_id")
    if not job_id:
        return dbc.Alert("Job not found — deploy the bundle first.", color="danger", className="py-2"), False
    try:
        waiter  = w.jobs.run_now(job_id=job_id)
        run_url = ""
        try:
            run_details = w.jobs.get_run(run_id=waiter.run_id)
            run_url = run_details.run_page_url or ""
        except Exception:
            pass
        return dbc.Alert(
            [
                html.I(className="fa-solid fa-circle-check me-2 text-success"),
                html.Strong(f"{action.get('job_name', 'Job')} triggered — "),
                html.A("view run →", href=run_url, target="_blank") if run_url else html.Span("check Workflows UI"),
            ],
            color="success", className="py-2 d-flex align-items-center",
        ), True
    except Exception as e:
        logger.error(f"Job trigger failed: {e}")
        return dbc.Alert(f"Failed to trigger job: {e}", color="danger", className="py-2"), False


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
