import json
import re

import dash
from dash import dcc, html, callback, clientside_callback, Input, Output, State, ALL, callback_context
import dash_bootstrap_components as dbc
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from config import w, CATALOG, SCHEMA, KA_ENDPOINT_NAME, KA_NAME, FMAPI_ENDPOINT, logger
from db import (get_patient_record, patient_options, save_icd10_code, get_saved_icd10_codes,
                delete_icd10_saved_code, execute_sql, _sql_esc)


# ---------------------------------------------------------------------------
# AI helper
# ---------------------------------------------------------------------------
def _normalise_ka_codes(codes: list[dict]) -> list[dict]:
    """
    Normalise KA response fields to the app schema.
    KA returns: Principal Diagnosis, Alternative Principal Diagnosis, Additional Diagnosis
    App expects: Primary Diagnosis | Secondary Diagnosis, HIGH | MEDIUM | LOW
    """
    _PRIMARY_KEYWORDS = ("principal", "primary", "main", "first")
    _CONFIDENCE_MAP   = {"high": "HIGH", "moderate": "MEDIUM", "medium": "MEDIUM", "low": "LOW"}
    out = []
    for c in codes:
        raw_type = str(c.get("type", ""))
        lower    = raw_type.lower()
        diag_type = (
            "Primary Diagnosis"
            if any(k in lower for k in _PRIMARY_KEYWORDS)
            else "Secondary Diagnosis"
        )
        raw_conf   = str(c.get("confidence", "")).lower()
        confidence = _CONFIDENCE_MAP.get(raw_conf, raw_conf.upper() or "MEDIUM")
        out.append({
            "code":        c.get("code", ""),
            "type":        diag_type,
            "description": c.get("description", ""),
            "confidence":  confidence,
        })
    return out


_ka_served_model: str = ""


def _get_ka_served_model() -> str:
    """Look up the KA foundation model name from the endpoint config (cached)."""
    global _ka_served_model
    if not _ka_served_model and KA_ENDPOINT_NAME:
        try:
            ep = w.api_client.do("GET", f"/api/2.0/serving-endpoints/{KA_ENDPOINT_NAME}")
            for entity in (ep.get("config") or {}).get("served_entities") or []:
                name = (entity.get("foundation_model") or {}).get("name", "")
                if name:
                    _ka_served_model = name
                    logger.info(f"KA served model: {_ka_served_model}")
                    break
        except Exception as e:
            logger.warning(f"Could not resolve KA served model: {e}")
    return _ka_served_model


def call_icd10_model(clinical_text: str) -> list[dict]:
    """
    Extract ICD-10 codes using the Knowledge Assistant (RAG over ICD-10 PDFs).

    Confirmed working path (validated via direct API test):
      POST /serving-endpoints/{endpoint}/served-models/{model}/invocations
      {"input": [{"role": "user", "content": "..."}]}

    Response structure:
      output[0].content[0].text → text containing a JSON array in a markdown block
    """
    _PROMPT = f"Identify all ICD-10 codes from the following clinical note:\n\n{clinical_text}"

    model = _get_ka_served_model()
    resp = w.api_client.do(
        "POST",
        f"/serving-endpoints/{KA_ENDPOINT_NAME}/served-models/{model}/invocations",
        body={"input": [{"role": "user", "content": _PROMPT}]},
    )

    # Extract text from the KA response structure
    output_items = resp.get("output") or []
    raw = ""
    for item in output_items:
        if item.get("type") == "message":
            for c in (item.get("content") or []):
                if c.get("type") == "output_text":
                    raw = c.get("text", "")
                    break
        if raw:
            break

    # Extract JSON array (may be inside a markdown ```json block)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            codes = json.loads(m.group())
            # Log the raw type values so we can see exactly what the KA returns
            types_seen = list({c.get("type", "") for c in codes})
            logger.info(f"KA type values in response: {types_seen}")
            return _normalise_ka_codes(codes)
        except json.JSONDecodeError:
            logger.warning(f"ICD-10 JSON parse failed: {raw[:200]}")

    logger.warning(f"ICD-10: no codes extracted. Raw (first 200): {raw[:200]}")
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
# Saved-codes panel renderer (Column 2)
# ---------------------------------------------------------------------------
def _render_icd10_saved_panel(codes: list[dict]) -> html.Div:
    if not codes:
        return html.Div(
            html.Small("No saved ICD-10 codes for this patient.",
                       className="text-muted fst-italic"),
            className="p-2",
        )
    items = []
    for i, c in enumerate(codes):
        code  = c.get("code", "")
        dtype = c.get("type", "")
        desc  = c.get("description", "")
        conf  = (c.get("confidence") or "").upper()
        items.append(html.Div([
            dbc.Badge(dtype.replace(" Diagnosis", "") or "?",
                      color=_TYPE_COLOR.get(dtype, "secondary"),
                      className="flex-shrink-0",
                      style={"fontSize": "9px", "minWidth": "50px"}),
            html.Code(code, className="ms-1 me-1 fw-bold",
                      style={"fontSize": "12px", "color": "#4FC3F7",
                             "background": "transparent"}),
            html.Small(desc, className="text-muted text-truncate me-auto",
                       style={"maxWidth": "130px"}),
            dbc.Badge(conf, color=_CONF_COLOR.get(conf, "secondary"), pill=True,
                      className="flex-shrink-0 mx-1", style={"fontSize": "9px"}),
            dbc.Button(
                html.I(className="fa-solid fa-trash-can"),
                id={"type": "icd10-panel-del-btn", "index": i},
                color="link", size="sm", n_clicks=0,
                className="text-danger p-0 flex-shrink-0",
                style={"lineHeight": "1"},
            ),
        ], className="d-flex align-items-center py-2 border-bottom gap-1"))
    return html.Div(items)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
_COL_H = {"height": "65vh", "overflowY": "auto"}

def _col_label(icon, text):
    return html.Div([
        html.I(className=f"fa-solid {icon} me-1"),
        html.Span(text, className="fw-semibold"),
    ], className="mb-1 small")


def _loading_modal_icd10():
    return dbc.Modal([
        dbc.ModalBody([html.Div([
            html.Div(html.I(className="fa-solid fa-heart-pulse fa-beat fa-3x text-danger"),
                     className="mb-3"),
            html.H5("Analyzing Clinical Record", className="fw-bold mb-1"),
            html.P("AI model is processing your clinical notes",
                   className="text-muted small mb-3"),
            dbc.Progress(value=100, striped=True, animated=True,
                         color="danger", style={"height": "6px"}, className="mb-4"),
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
        ], className="text-center py-2")]),
    ], id="icd10-loading-modal", is_open=False, centered=True,
       backdrop="static", keyboard=False, size="sm")


def icd10_layout(patients: list[dict],
                 restore_patient: str = None,
                 restore_codes: list = None,
                 restore_saved: list = None) -> dbc.Container:
    options   = patient_options(patients)
    saved_set = set(restore_saved or [])
    init_results = (
        _render_icd10_results(restore_codes, restore_patient or "patient", saved_set)
        if restore_codes
        else dbc.Alert("Analysis results will appear here after clicking Analyze.",
                       color="secondary")
    )
    return dbc.Container([
        # ── Title ──────────────────────────────────────────
        html.Div([
            html.H4(
                [html.I(className="fa-solid fa-file-medical me-2 text-info"),
                 "ICD-10 Analyzer"],
                className="mb-0 fw-bold me-3 d-inline",
            ),
            html.Small(
                "Identifies ICD-10 codes from clinical notes using the "
                "Knowledge Assistant, backed by indexed ICD-10 reference PDFs.",
                className="text-muted fw-normal",
            ),
        ], className="d-flex align-items-baseline flex-wrap mb-3"),
        _loading_modal_icd10(),

        # ── Row 1: Patient select + Analyze button ─────────
        dbc.Row([
            dbc.Col([
                dcc.Dropdown(id="icd10-patient-select", options=options,
                             value=restore_patient,
                             placeholder="Choose a patient...", className="mb-1"),
                dbc.Alert(
                    [html.I(className="fa-solid fa-clock-rotate-left me-2"),
                     "Knowledge Assistant is still indexing — ICD-10 results may be limited."],
                    id="icd10-sync-banner", color="info",
                    is_open=False, className="mb-0 py-2 small",
                ),
            ], width=8),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-magnifying-glass me-2"),
                     "Analyze ICD-10 Codes"],
                    id="icd10-analyze-btn", color="primary", size="lg",
                    disabled=not bool(restore_patient),
                    className="w-100 h-100",
                ),
                width=4,
            ),
        ], className="mb-3 g-3 align-items-center"),

        # ── Row 2: 2-column equal body ─────────────────────
        dbc.Row([
            # Col 1 — Clinical Record (50%)
            dbc.Col([
                _col_label("fa-file-waveform text-muted", "Clinical Record"),
                dcc.Textarea(
                    id="icd10-clinical-record",
                    style={"width": "100%", "height": "70vh",
                           "fontSize": "12px", "fontFamily": "monospace",
                           "resize": "none"},
                    readOnly=True,
                    placeholder="Select a patient to load their clinical note...",
                ),
            ], width=6),

            # Col 2 — Saved codes (top) + Analysis results (below), single scroll (50%)
            dbc.Col([
                html.Div([
                    _col_label("fa-bookmark text-info", "Saved ICD-10 Codes"),
                    html.Div(id="icd10-saved-panel",
                             children=dbc.Alert("Select a patient to see saved codes.",
                                                color="secondary")),
                    html.Hr(className="my-3"),
                    _col_label("fa-list-check text-primary", "Analysis Results"),
                    html.Div(id="icd10-results", children=init_results),
                ], style={"height": "70vh", "overflowY": "auto",
                          "paddingRight": "4px"}),
            ], width=6),
        ], className="g-3"),
    ], fluid=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_full_saved_codes(patient_id: str, catalog: str, schema: str) -> list[dict]:
    """Return saved ICD-10 codes as full objects (code, type, description, confidence)."""
    try:
        rows = execute_sql(
            f"SELECT code, diag_type, description, confidence "
            f"FROM `{catalog}`.`{schema}`.icd10_analysis_results "
            f"WHERE patient_id = '{_sql_esc(patient_id)}' AND code IS NOT NULL "
            f"ORDER BY analyzed_at DESC"
        )
        return [{"code": r["code"], "type": r["diag_type"] or "",
                 "description": r["description"] or "",
                 "confidence": r["confidence"] or ""}
                for r in rows]
    except Exception as e:
        logger.warning(f"Could not fetch full saved codes for {patient_id}: {e}")
        return []


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("icd10-clinical-record",   "value"),
    Output("icd10-analyze-btn",       "disabled"),
    Output("icd10-patient-store",     "data"),
    Output("icd10-saved-codes-store", "data"),                           # primary
    Output("icd10-results",           "children",   allow_duplicate=True),
    Output("icd10-codes-store",       "data",       allow_duplicate=True),
    Output("icd10-saved-panel",       "children"),                       # primary
    Input("icd10-patient-select",     "value"),
    State("catalog-store",            "data"),
    State("schema-store",             "data"),
    prevent_initial_call=True,
)
def load_record_icd10(patient_id, catalog, schema):
    no_panel = dbc.Alert("Select a patient to see saved codes.", color="secondary")
    if not patient_id:
        return "", True, "", [], dbc.Alert("Select a patient and click Analyze.",
                                            color="secondary"), [], no_panel
    cat    = catalog or CATALOG
    sch    = schema  or SCHEMA
    record = get_patient_record(patient_id, cat, sch)
    if not record:
        return "Record not found.", True, "", [], dash.no_update, dash.no_update, no_panel

    saved_set  = get_saved_icd10_codes(patient_id, cat, sch)
    full_codes = _get_full_saved_codes(patient_id, cat, sch)

    # Analysis results reset to default on patient select — only populated after Analyze click
    default_results = dbc.Alert(
        [html.I(className="fa-solid fa-magnifying-glass me-2"),
         "Click Analyze ICD-10 Codes to identify codes from this clinical note."],
        color="secondary",
    )
    return (record["clinicalrecord"], False, patient_id,
            list(saved_set), default_results, [],
            _render_icd10_saved_panel(full_codes))


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
    Output("icd10-results",           "children",   allow_duplicate=True),
    Output("icd10-codes-store",       "data",       allow_duplicate=True),
    Output("icd10-sync-banner",       "is_open"),
    Output("icd10-loading-modal",     "is_open", allow_duplicate=True),
    Input("icd10-analyze-btn",        "n_clicks"),
    State("icd10-clinical-record",    "value"),
    State("icd10-patient-store",      "data"),
    State("icd10-saved-codes-store",  "data"),
    State("all-done-store",           "data"),
    prevent_initial_call=True,
)
def run_icd10(n_clicks, text, patient_id, saved_codes_list, all_done):
    if not n_clicks or not text:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    # Show banner if KA PDF indexing is still in progress (not all setup steps complete)
    ka_still_indexing = not all_done
    try:
        saved_codes = set(saved_codes_list or [])
        codes       = call_icd10_model(text)
        return _render_icd10_results(codes, patient_id or "patient", saved_codes), codes, ka_still_indexing, False
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
    cat, sch = catalog or CATALOG, schema or SCHEMA
    try:
        save_icd10_code(patient_id, code, cat, sch)
        return (
            [html.I(className="fa-solid fa-circle-check me-1"), "Saved"],
            True, "success", None,
        )
    except Exception as e:
        logger.error(f"Save ICD-10 code: {e}")
        return (
            [html.I(className="fa-solid fa-floppy-disk me-1"), "Save"],
            False, "outline-secondary",
            dbc.Alert(str(e)[:120], color="danger", className="py-1 small mt-1"),
        )


@callback(
    Output("icd10-saved-panel",                                "children", allow_duplicate=True),
    Output("icd10-saved-codes-store",                          "data",     allow_duplicate=True),
    Input({"type": "icd10-panel-del-btn", "index": ALL},       "n_clicks"),
    State("icd10-patient-store",                               "data"),
    State("catalog-store",                                     "data"),
    State("schema-store",                                      "data"),
    prevent_initial_call=True,
)
def delete_icd10_panel_code(clicks, patient_id, catalog, schema):
    if not any(clicks) or not patient_id:
        return dash.no_update, dash.no_update
    triggered = callback_context.triggered_id
    if not isinstance(triggered, dict):
        return dash.no_update, dash.no_update
    cat, sch = catalog or CATALOG, schema or SCHEMA
    full_codes = _get_full_saved_codes(patient_id, cat, sch)
    idx = triggered["index"]
    if idx >= len(full_codes):
        return dash.no_update, dash.no_update
    code_to_del = full_codes[idx].get("code", "")
    try:
        delete_icd10_saved_code(patient_id, code_to_del, cat, sch)
    except Exception as e:
        logger.error(f"Delete ICD-10 code: {e}")
        return dash.no_update, dash.no_update
    updated      = _get_full_saved_codes(patient_id, cat, sch)
    updated_keys = [c["code"] for c in updated]
    return _render_icd10_saved_panel(updated), updated_keys
