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
# Configuration
# ---------------------------------------------------------------------------
CATALOG           = os.getenv("UC_CATALOG", "icd10_gap_demo")
AI_GATEWAY_ROUTE  = os.getenv("AI_GATEWAY_ROUTE", "care-gap-advisor")
WAREHOUSE_ID      = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
BOOTSTRAP_JOB_NAME = os.getenv("BOOTSTRAP_JOB_NAME", "ICD-10 Gap Demo — Bootstrap")
BRAND_ORANGE      = "#E87722"

w = WorkspaceClient()

# ---------------------------------------------------------------------------
# Bootstrap step registry
# ---------------------------------------------------------------------------
BOOTSTRAP_STEPS = [
    {
        "step_id":     "create_catalog",
        "seq":         1,
        "label":       "Unity Catalog & Database Setup",
        "description": "Create catalog, schemas, Delta tables (patient_records, care_gap_rules, "
                       "bootstrap_status) and the icd10_reference UC Volume.",
        "icon":        "fa-database",
    },
    {
        "step_id":     "ingest_patient_data",
        "seq":         2,
        "label":       "Patient Clinical Notes Ingested",
        "description": "Load 25 synthetic SOAP-format patient records from "
                       "data/patient_records.json into clinical_data.patient_records Delta table.",
        "icon":        "fa-notes-medical",
    },
    {
        "step_id":     "load_icd10_pdfs",
        "seq":         3,
        "label":       "ICD-10 Reference PDFs Uploaded to Volume",
        "description": "Copy ICD-10 PDF reference files from the Git repo (data/icd10_pdfs/) "
                       "into the Unity Catalog Volume — prerequisite for Knowledge Assistant indexing.",
        "icon":        "fa-file-pdf",
    },
    {
        "step_id":     "create_knowledge_assistant",
        "seq":         4,
        "label":       "Knowledge Assistant Created",
        "description": "Create the ICD-10 Knowledge Assistant agent via Databricks SDK, "
                       "attach the UC Volume as a knowledge source, and trigger PDF indexing.",
        "icon":        "fa-robot",
    },
    {
        "step_id":     "ka_pdf_sync",
        "seq":         5,
        "label":       "Knowledge Assistant PDF Indexing",
        "description": "Asynchronous background process — the KA indexes all ICD-10 reference PDFs. "
                       "This typically takes 30–60 minutes. ICD-10 Analyzer works once this completes.",
        "icon":        "fa-brain",
        "is_async":    True,
    },
    {
        "step_id":     "configure_ai_gateway",
        "seq":         6,
        "label":       "AI Gateway Route Configured",
        "description": "Create the AI Gateway serving endpoint for the care gap foundation model "
                       "(Claude via Anthropic or DBRX Instruct). Required for Care Gap Advisor.",
        "icon":        "fa-network-wired",
    },
]

# Status visual config
STATUS_META = {
    "NOT_STARTED":     {"color": "secondary", "icon": "fa-circle-dot",              "label": "Not Started",      "row_bg": "#f8f9fa"},
    "IN_PROGRESS":     {"color": "warning",   "icon": "fa-spinner fa-spin",         "label": "In Progress",      "row_bg": "#fff8e1"},
    "COMPLETED":       {"color": "success",   "icon": "fa-circle-check",            "label": "Completed",        "row_bg": "#f0fff4"},
    "LIKELY_COMPLETE": {"color": "info",      "icon": "fa-circle-check",            "label": "Likely Complete",  "row_bg": "#e8f8ff"},
    "WARNING":         {"color": "warning",   "icon": "fa-triangle-exclamation",    "label": "Warning",          "row_bg": "#fff8e1"},
    "FAILED":          {"color": "danger",    "icon": "fa-circle-xmark",            "label": "Failed",           "row_bg": "#fff0f0"},
    "SKIPPED":         {"color": "secondary", "icon": "fa-forward",                 "label": "Skipped",          "row_bg": "#f8f9fa"},
    "UNKNOWN":         {"color": "secondary", "icon": "fa-question-circle",         "label": "Unknown",          "row_bg": "#f8f9fa"},
}

DONE_STATUSES = {"COMPLETED", "LIKELY_COMPLETE", "SKIPPED"}

# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def execute_sql(statement: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set.")
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="30s"
    )
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL error: {resp.status.error}")
    if not resp.manifest or not resp.manifest.schema:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (resp.result.data_array or [])]


def load_patients() -> list[dict]:
    return execute_sql(
        f"SELECT patient_id, mrn, dob, gender, message_datetime "
        f"FROM `{CATALOG}`.clinical_data.patient_records ORDER BY patient_id"
    )


def get_patient_record(patient_id: str) -> dict | None:
    rows = execute_sql(
        f"SELECT * FROM `{CATALOG}`.clinical_data.patient_records "
        f"WHERE patient_id = '{patient_id}' LIMIT 1"
    )
    return rows[0] if rows else None


def get_care_gap_rules() -> list[dict]:
    return execute_sql(
        f"SELECT * FROM `{CATALOG}`.care_gaps.care_gap_rules ORDER BY priority, condition"
    )


def get_ka_endpoint_name() -> str:
    try:
        rows = execute_sql(
            f"SELECT details FROM `{CATALOG}`.app_config.bootstrap_status "
            f"WHERE step = 'create_knowledge_assistant' AND status = 'COMPLETED' "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
        if rows:
            return json.loads(rows[0]["details"]).get("endpoint_name", "")
    except Exception as e:
        logger.warning(f"KA endpoint lookup: {e}")
    return ""


def get_bootstrap_job_id() -> int | None:
    try:
        for job in w.jobs.list(name=BOOTSTRAP_JOB_NAME):
            return job.job_id
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Bootstrap step status resolution
# ---------------------------------------------------------------------------
def _resolve_ka_sync(ka_row: dict) -> tuple[str, str]:
    """Check KA PDF sync status — SDK first, time-based fallback."""
    try:
        details = json.loads(ka_row.get("details", "{}"))
        ka_name         = details.get("ka_name", "")
        sync_started_at = details.get("sync_started_at", "")
        pdf_count       = details.get("pdf_count", 0)

        if pdf_count == 0:
            return "WARNING", "No PDFs were found in the volume when KA was created — upload PDFs and re-run bootstrap step 3."

        # Try SDK check
        if ka_name:
            try:
                sources = list(w.knowledge_assistants.list_knowledge_sources(parent=ka_name))
                for src in sources:
                    sync_state = getattr(src, "sync_status", None) or getattr(src, "status", None)
                    if sync_state:
                        s = str(sync_state).upper()
                        if any(x in s for x in ("COMPLET", "SUCCESS", "DONE")):
                            return "COMPLETED", f"All PDFs indexed successfully ({pdf_count} file(s))"
                        if any(x in s for x in ("FAIL", "ERROR")):
                            return "FAILED", f"Sync error: {sync_state}"
                        return "IN_PROGRESS", f"Indexing status: {sync_state}"
            except Exception as sdk_err:
                logger.debug(f"KA SDK sync check: {sdk_err}")

        # Time-based fallback
        if sync_started_at:
            try:
                sync_dt  = datetime.fromisoformat(sync_started_at.replace("Z", "+00:00"))
                elapsed  = (datetime.now(timezone.utc) - sync_dt).total_seconds() / 60
                if elapsed > 90:
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


def get_bootstrap_step_statuses() -> list[dict]:
    """Query bootstrap_status table and return enriched step list."""
    try:
        rows = execute_sql(
            f"SELECT step, status, updated_at, details "
            f"FROM `{CATALOG}`.app_config.bootstrap_status ORDER BY updated_at"
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

        # Pretty-print JSON detail blobs
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
# Home tab: step card builder
# ---------------------------------------------------------------------------
def _step_card(step: dict) -> html.Div:
    status = step.get("status", "NOT_STARTED")
    meta   = STATUS_META.get(status, STATUS_META["NOT_STARTED"])
    detail = step.get("detail", "")
    ts     = step.get("updated_at", "")
    is_async = step.get("is_async", False)

    left_border_color = {
        "COMPLETED":       "#198754",
        "LIKELY_COMPLETE": "#0dcaf0",
        "IN_PROGRESS":     "#ffc107",
        "WARNING":         "#ffc107",
        "FAILED":          "#dc3545",
        "NOT_STARTED":     "#dee2e6",
        "SKIPPED":         "#dee2e6",
    }.get(status, "#dee2e6")

    return html.Div(
        dbc.Row([
            # Sequence number circle
            dbc.Col(
                html.Div(
                    str(step["seq"]),
                    style={
                        "width": "32px", "height": "32px",
                        "borderRadius": "50%",
                        "background": left_border_color,
                        "color": "white" if status not in ("NOT_STARTED", "SKIPPED") else "#6c757d",
                        "fontWeight": "700", "fontSize": "14px",
                        "display": "flex", "alignItems": "center", "justifyContent": "center",
                        "flexShrink": "0",
                    }
                ),
                width="auto", className="pe-0",
            ),

            # Step content
            dbc.Col([
                dbc.Row([
                    dbc.Col(
                        html.Span([
                            html.I(className=f"fa-solid {step['icon']} me-2 text-muted"),
                            html.Strong(step["label"], className="fs-6"),
                            html.Span(" (async)", className="ms-1 text-muted small fst-italic")
                            if is_async else None,
                        ]),
                        width=8,
                    ),
                    dbc.Col(
                        dbc.Badge(
                            [html.I(className=f"fa-solid {meta['icon']} me-1"), meta["label"]],
                            color=meta["color"], pill=True,
                            style={"fontSize": "11px"},
                        ),
                        width=4, className="text-end",
                    ),
                ], align="center", className="mb-1"),

                html.P(step["description"], className="small text-muted mb-1"),

                html.Div([
                    html.I(className="fa-solid fa-circle-info me-1 text-secondary"),
                    html.Span(detail, className="small"),
                ], className="mb-1") if detail and detail not in ("Not yet started", "Not yet started") else None,

                html.Div(
                    html.Small(
                        [html.I(className="fa-regular fa-clock me-1"), f"Updated: {ts}"],
                        className="text-muted"
                    )
                ) if ts else None,
            ]),
        ], align="start", className="g-2"),
        style={
            "background": meta["row_bg"],
            "borderLeft": f"4px solid {left_border_color}",
            "borderRadius": "6px",
            "padding": "12px 14px",
            "marginBottom": "10px",
        }
    )


def build_home_tab_content(steps: list[dict], last_refreshed: str, all_done: bool) -> html.Div:
    completed   = sum(1 for s in steps if s["status"] in DONE_STATUSES)
    total       = len(steps)
    pct         = int(completed / total * 100)
    prog_color  = "success" if all_done else ("warning" if completed > 0 else "secondary")

    banner = dbc.Alert(
        [html.I(className="fa-solid fa-circle-check me-2 text-success"),
         html.Strong("All prerequisites ready — "),
         "ICD-10 Analyzer and Care Gap Advisor are fully operational."],
        color="success", className="mb-3 py-2"
    ) if all_done else dbc.Alert(
        [html.I(className="fa-solid fa-circle-info me-2"),
         html.Strong("Setup in progress. "),
         "This page auto-refreshes every 60 seconds. Tabs may be limited until setup completes."],
        color="info", className="mb-3 py-2"
    )

    return html.Div([
        banner,

        # Progress row
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
                html.Div([
                    html.Div(
                        [html.I(className="fa-solid fa-rotate me-1"),
                         f"Auto-refreshes every 60s"]
                        if not all_done else
                        [html.I(className="fa-solid fa-check me-1 text-success"),
                         "Auto-refresh stopped — setup complete"],
                        className="small text-muted text-end"
                    ),
                    html.Div(
                        html.Small(f"Last updated: {last_refreshed}", className="text-muted"),
                        className="text-end"
                    ),
                ]),
                width=4,
            ),
        ], align="center", className="mb-3"),

        # Step cards
        html.Div([_step_card(s) for s in steps]),

        # Bootstrap command hint if not started
        html.Div([
            html.Hr(),
            dbc.Alert([
                html.Strong("To start setup, run:  "),
                html.Code(
                    "databricks bundle deploy --profile fevm01  &&  "
                    "databricks bundle run bootstrap_workflow --profile fevm01",
                    style={"fontSize": "12px"}
                ),
            ], color="secondary", className="mb-0 py-2"),
        ]) if completed == 0 else None,
    ])

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
            dbc.Tab(
                label="Home",
                tab_id="tab-home",
                label_style={"fontWeight": "600"},
            ),
            dbc.Tab(label="ICD-10 Analyzer",  tab_id="tab-icd10"),
            dbc.Tab(label="Care Gap Advisor",  tab_id="tab-caregap"),
        ]
    ),
    html.Div(id="tab-content", className="p-3"),

    # Stores (shared across tabs)
    dcc.Store(id="ka-endpoint-store", data=""),
    dcc.Store(id="patient-store",     data=[]),
    dcc.Store(id="all-done-store",    data=False),

    # Auto-refresh interval for Home tab (60 s, disabled once all steps complete)
    dcc.Interval(id="home-interval", interval=60_000, n_intervals=0, disabled=False),
])

# ---------------------------------------------------------------------------
# Tab routing — renders content for whichever tab is active
# ---------------------------------------------------------------------------
@callback(
    Output("tab-content", "children"),
    Input("main-tabs",      "active_tab"),
    Input("patient-store",  "data"),
    Input("ka-endpoint-store", "data"),
    Input("all-done-store", "data"),
)
def render_tab(active_tab, patients, ka_endpoint, all_done):
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

    return html.Div()


def _home_shell() -> html.Div:
    """Returns the Home tab skeleton — content is filled by refresh_home callback."""
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("Demo Environment Status", className="mb-0 fw-bold"), width="auto"),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"), "Refresh Now"],
                    id="home-refresh-btn",
                    color="outline-secondary",
                    size="sm",
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
# Home tab refresh callback (fires on load, every 60s, and on manual click)
# ---------------------------------------------------------------------------
@callback(
    Output("home-step-content",  "children"),
    Output("home-interval",      "disabled"),
    Output("all-done-store",     "data"),
    Output("ka-endpoint-store",  "data"),
    Output("patient-store",      "data"),
    Input("home-interval",       "n_intervals"),
    Input("home-refresh-btn",    "n_clicks"),
    prevent_initial_call=False,
)
def refresh_home(n_intervals, n_clicks):
    steps    = get_bootstrap_step_statuses()
    all_done = all(s["status"] in DONE_STATUSES for s in steps)
    now      = datetime.now().strftime("%H:%M:%S")

    patients    = []
    ka_endpoint = ""
    if all_done:
        try:
            patients    = load_patients()
            ka_endpoint = get_ka_endpoint_name()
        except Exception as e:
            logger.warning(f"Post-completion data load failed: {e}")

    content = build_home_tab_content(steps, now, all_done)
    return content, all_done, all_done, ka_endpoint, patients

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
    prevent_initial_call=True,
)
def load_record_icd10(patient_id):
    if not patient_id:
        return "", True
    record = get_patient_record(patient_id)
    return (record["clinicalrecord"], False) if record else ("Record not found.", True)


@callback(
    Output("icd10-results",      "children"),
    Output("icd10-sync-banner",  "is_open"),
    Input("icd10-analyze-btn",   "n_clicks"),
    State("icd10-clinical-record", "value"),
    State("ka-endpoint-store",   "data"),
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
        result = call_ka_endpoint(text, ka_ep)
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
    Output("gap-results",        "children"),
    Input("gap-analyze-btn",     "n_clicks"),
    State("gap-patient-select",  "value"),
    prevent_initial_call=True,
)
def run_gaps(n_clicks, patient_id):
    if not n_clicks or not patient_id:
        return dash.no_update
    try:
        patient = get_patient_record(patient_id)
        rules   = get_care_gap_rules()
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
                    html.P([html.Strong("Finding: "),       g.get("finding", "—")],           className="mb-1 small"),
                    html.P([html.Strong("Action: "),        g.get("recommended_action", "—")], className="mb-1 small"),
                    html.P([html.Strong("Guideline: "),     g.get("guideline", "—")],          className="mb-0 small text-muted"),
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
