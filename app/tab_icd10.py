import json
import re

import dash
from dash import dcc, html, callback, clientside_callback, Input, Output, State, ALL, callback_context
import dash_bootstrap_components as dbc
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from config import w, CATALOG, SCHEMA, KA_ENDPOINT_NAME, FMAPI_ENDPOINT, logger
from db import get_patient_record, patient_options, save_icd10_code, get_saved_icd10_codes


# ---------------------------------------------------------------------------
# AI helper
# ---------------------------------------------------------------------------
def call_icd10_model(clinical_text: str) -> list[dict]:
    """Extract ICD-10 codes from a clinical note using the FM (Claude) endpoint."""
    response = w.serving_endpoints.query(
        name=FMAPI_ENDPOINT,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=(
                "You are a clinical coding expert. Analyze the clinical note and identify "
                "all relevant ICD-10 codes. Return ONLY a JSON array — no prose, no markdown. "
                "Each object must have: code, type ('Primary Diagnosis' or 'Secondary Diagnosis'), "
                "description (full condition name), confidence (HIGH/MEDIUM/LOW)."
            )),
            ChatMessage(role=ChatMessageRole.USER, content=(
                f"Clinical Note:\n{clinical_text}"
            )),
        ],
    )
    raw = response.choices[0].message.content
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            logger.warning(f"ICD-10 model returned unparseable JSON: {raw[:200]}")
    return []


# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------
_TYPE_COLOR = {"Primary Diagnosis": "danger", "Secondary Diagnosis": "primary"}
_CONF_COLOR = {"HIGH": "success", "MEDIUM": "warning", "LOW": "secondary"}


def _render_icd10_results(codes: list[dict], patient_id: str,
                          saved_codes: set | None = None) -> html.Div:
    if not codes:
        return dbc.Alert(
            [html.I(className="fa-solid fa-circle-info me-2"),
             "No ICD-10 codes identified for this clinical note."],
            color="info",
        )

    saved_codes = saved_codes or set()
    rows = []
    for i, item in enumerate(codes):
        code       = item.get("code", "—")
        diag_type  = item.get("type", "—")
        desc       = item.get("description", "—")
        confidence = item.get("confidence", "").upper()
        already    = code in saved_codes

        save_btn = dbc.Button(
            [html.I(className=f"fa-solid {'fa-circle-check' if already else 'fa-floppy-disk'} me-1"),
             "Saved" if already else "Save"],
            id={"type": "icd10-save-btn", "index": i},
            color="success" if already else "outline-secondary",
            size="sm", disabled=already, n_clicks=0,
            className="text-nowrap",
        )
        rows.append(html.Tr([
            html.Td(html.Code(code, className="fw-bold fs-6"), className="text-nowrap align-middle"),
            html.Td(
                dbc.Badge(diag_type, color=_TYPE_COLOR.get(diag_type, "secondary"),
                          pill=True, className="text-nowrap"),
                className="align-middle",
            ),
            html.Td(desc, className="small align-middle"),
            html.Td(
                dbc.Badge(confidence, color=_CONF_COLOR.get(confidence, "secondary"), pill=True),
                className="align-middle text-nowrap",
            ),
            html.Td(
                html.Div([
                    save_btn,
                    html.Div(id={"type": "icd10-save-result", "index": i}),
                ]),
                className="align-middle text-nowrap",
            ),
        ]))

    table = dbc.Table(
        [
            html.Thead(html.Tr([
                html.Th("Code",       style={"width": "80px"}),
                html.Th("Type",       style={"width": "155px"}),
                html.Th("Description"),
                html.Th("Confidence", style={"width": "95px"}),
                html.Th("",           style={"width": "85px"}),
            ]), className="table-dark"),
            html.Tbody(rows),
        ],
        bordered=False, hover=True, responsive=True, size="sm", className="mb-0",
    )

    return html.Div([
        dbc.Alert(
            [html.I(className="fa-solid fa-file-medical me-2"),
             html.Strong(f"{len(codes)} ICD-10 code{'s' if len(codes) != 1 else ''} identified"),
             html.Span(f" for {patient_id}", className="text-muted ms-1")],
            color="success", className="mb-3 py-2",
        ),
        dbc.Card(dbc.CardBody(table, className="p-0"), className="shadow-sm"),
    ])


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def icd10_layout(patients: list[dict]) -> dbc.Container:
    options = patient_options(patients)
    return dbc.Container([
        dbc.Row([
            dbc.Col(html.H5(
                [html.I(className="fa-solid fa-file-medical me-2 text-info"), "ICD-10 Analyzer"],
                className="mb-0 fw-bold"), width="auto"),
        ], className="mb-3"),
        # Healthcare-themed loading modal — shown immediately on button click,
        # locked (backdrop=static) until the server callback closes it.
        dbc.Modal([
            dbc.ModalBody([
                html.Div([
                    html.Div(
                        html.I(className="fa-solid fa-heart-pulse fa-beat fa-3x text-danger"),
                        className="mb-3",
                    ),
                    html.H5("Analyzing Clinical Record", className="fw-bold mb-1"),
                    html.P("AI model is processing your clinical notes",
                           className="text-muted small mb-3"),
                    dbc.Progress(
                        value=100, striped=True, animated=True,
                        color="danger", style={"height": "6px"}, className="mb-4",
                    ),
                    html.Div([
                        html.Div([html.I(className="fa-solid fa-file-waveform me-2 text-primary"),
                                  "Reading clinical notes"],
                                 className="small text-muted mb-2 d-flex align-items-center justify-content-center"),
                        html.Div([html.I(className="fa-solid fa-brain me-2 text-warning"),
                                  "Processing with Claude AI"],
                                 className="small text-muted mb-2 d-flex align-items-center justify-content-center"),
                        html.Div([html.I(className="fa-solid fa-list-check me-2 text-success"),
                                  "Identifying ICD-10 codes"],
                                 className="small text-muted d-flex align-items-center justify-content-center"),
                    ]),
                ], className="text-center py-2"),
            ]),
        ], id="icd10-loading-modal", is_open=False, centered=True,
           backdrop="static", keyboard=False, size="sm"),
        dbc.Row([
            dbc.Col([
                html.Label("Select Patient", className="fw-semibold mb-1"),
                dcc.Dropdown(id="icd10-patient-select", options=options,
                             placeholder="Choose a patient...", className="mb-3"),
                dbc.Alert(
                    [html.I(className="fa-solid fa-clock-rotate-left me-2"),
                     "Knowledge Assistant is still indexing PDFs — ICD-10 results may be limited "
                     "until step 5 completes."],
                    id="icd10-sync-banner", color="info", is_open=False, className="mb-2 py-2 small",
                ),
                html.Label("Clinical Record", className="fw-semibold mb-1"),
                dcc.Textarea(
                    id="icd10-clinical-record",
                    style={"width": "100%", "height": "320px",
                           "fontSize": "12px", "fontFamily": "monospace"},
                    readOnly=True,
                    placeholder="Select a patient to load their clinical note...",
                    className="mb-3",
                ),
                dbc.Button(
                    [html.I(className="fa-solid fa-magnifying-glass me-2"), "Analyze ICD-10 Codes"],
                    id="icd10-analyze-btn", color="primary", disabled=True, className="w-100",
                ),
            ], width=5),
            dbc.Col([
                html.Label("ICD-10 Code Suggestions", className="fw-semibold mb-1"),
                html.Div(id="icd10-results",
                         children=dbc.Alert("Select a patient and click Analyze.",
                                            color="secondary")),
            ], width=7),
        ], className="mt-2 g-4"),
    ], fluid=True)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("icd10-clinical-record",   "value"),
    Output("icd10-analyze-btn",       "disabled"),
    Output("icd10-patient-store",     "data"),
    Output("icd10-saved-codes-store", "data"),
    Input("icd10-patient-select",     "value"),
    State("catalog-store",            "data"),
    State("schema-store",             "data"),
    prevent_initial_call=True,
)
def load_record_icd10(patient_id, catalog, schema):
    if not patient_id:
        return "", True, "", []
    cat    = catalog or CATALOG
    sch    = schema  or SCHEMA
    record = get_patient_record(patient_id, cat, sch)
    if not record:
        return "Record not found.", True, "", []
    saved = list(get_saved_icd10_codes(patient_id, cat, sch))
    return record["clinicalrecord"], False, patient_id, saved


# Open healthcare modal immediately on button click (no server round-trip needed)
clientside_callback(
    """function(n) {
        if (n > 0) return true;
        return window.dash_clientside.no_update;
    }""",
    Output("icd10-loading-modal", "is_open"),
    Input("icd10-analyze-btn",    "n_clicks"),
    prevent_initial_call=True,
)


@callback(
    Output("icd10-results",           "children"),
    Output("icd10-codes-store",       "data"),
    Output("icd10-sync-banner",       "is_open"),
    Output("icd10-loading-modal",     "is_open", allow_duplicate=True),
    Input("icd10-analyze-btn",        "n_clicks"),
    State("icd10-clinical-record",    "value"),
    State("icd10-patient-store",      "data"),
    State("icd10-saved-codes-store",  "data"),
    prevent_initial_call=True,
)
def run_icd10(n_clicks, text, patient_id, saved_codes_list):
    if not n_clicks or not text:
        return dash.no_update, dash.no_update, False, dash.no_update
    try:
        saved_codes = set(saved_codes_list or [])
        codes       = call_icd10_model(text)
        return _render_icd10_results(codes, patient_id or "patient", saved_codes), codes, False, False
    except Exception as e:
        logger.error(f"ICD-10 analysis: {e}")
        return dbc.Alert(f"Analysis failed: {e}", color="danger"), [], False, False


@callback(
    Output({"type": "icd10-save-btn",    "index": dash.MATCH}, "children"),
    Output({"type": "icd10-save-btn",    "index": dash.MATCH}, "disabled"),
    Output({"type": "icd10-save-btn",    "index": dash.MATCH}, "color"),
    Output({"type": "icd10-save-result", "index": dash.MATCH}, "children"),
    Input({"type": "icd10-save-btn",     "index": dash.MATCH}, "n_clicks"),
    State("icd10-codes-store",           "data"),
    State("icd10-patient-store",         "data"),
    State("catalog-store",               "data"),
    State("schema-store",                "data"),
    prevent_initial_call=True,
)
def save_icd10_finding(n_clicks, codes, patient_id, catalog, schema):
    no_upd = (dash.no_update,) * 4
    if not n_clicks or not codes or not patient_id:
        return no_upd
    idx = callback_context.triggered_id["index"]
    if idx >= len(codes):
        return no_upd
    code = codes[idx]
    try:
        save_icd10_code(patient_id, code, catalog or CATALOG, schema or SCHEMA)
        return (
            [html.I(className="fa-solid fa-circle-check me-1"), "Saved"],
            True,
            "success",
            None,
        )
    except Exception as e:
        logger.error(f"Save ICD-10 code: {e}")
        return (
            [html.I(className="fa-solid fa-floppy-disk me-1"), "Save"],
            False,
            "outline-secondary",
            dbc.Alert(str(e)[:120], color="danger", className="py-1 small mt-1"),
        )
