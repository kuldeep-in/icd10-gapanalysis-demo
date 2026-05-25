import dash
from dash import html, callback, Input, Output, State, ALL, callback_context
import dash_bootstrap_components as dbc

from config import CATALOG, SCHEMA, logger
from db import (
    load_patients, get_all_care_gap_findings, get_all_icd10_saved_codes,
    delete_care_gap_finding, delete_icd10_saved_code,
)

_P_COLOR   = {"HIGH": "danger",  "MEDIUM": "warning", "LOW": "info"}
_TYPE_COLOR = {"Primary Diagnosis": "danger", "Secondary Diagnosis": "primary"}


# ---------------------------------------------------------------------------
# Cell renderers
# ---------------------------------------------------------------------------
def _icd10_cell(pid: str, codes: list[dict]) -> html.Td:
    if not codes:
        return html.Td(html.Small("—", className="text-muted"), className="align-top")
    items = []
    for c in codes:
        code      = c.get("code", "")
        diag_type = c.get("diag_type", "")
        desc      = c.get("description", "")
        items.append(
            html.Div([
                dbc.Badge(
                    diag_type.replace(" Diagnosis", "") if diag_type else "?",
                    color=_TYPE_COLOR.get(diag_type, "secondary"),
                    className="me-1 flex-shrink-0",
                    style={"fontSize": "9px", "minWidth": "50px"},
                ),
                html.Span(html.Strong(code), className="me-1 text-nowrap"),
                html.Small(desc, className="text-muted me-auto text-truncate",
                           style={"maxWidth": "180px"}),
                dbc.Button(
                    html.I(className="fa-solid fa-trash-can"),
                    id={"type": "home-icd10-delete-btn", "patient": pid, "code": code},
                    color="link", size="sm", n_clicks=0,
                    className="text-danger p-0 ms-1 flex-shrink-0",
                    style={"lineHeight": "1"},
                ),
            ], className="d-flex align-items-center py-1 border-bottom gap-1",
               style={"minWidth": "260px"})
        )
    return html.Td(html.Div(items), className="align-top")


def _gap_cell(pid: str, findings: list[dict]) -> html.Td:
    if not findings:
        return html.Td(html.Small("—", className="text-muted"), className="align-top")
    items = []
    for f in findings:
        priority = f.get("priority", "")
        gap_name = f.get("gap_name", "—")
        condition = f.get("condition", "")
        rule_id   = f.get("rule_id", "")
        items.append(
            html.Div([
                dbc.Badge(
                    priority or "?",
                    color=_P_COLOR.get(priority, "secondary"),
                    className="me-1 flex-shrink-0",
                    style={"fontSize": "9px", "minWidth": "38px"},
                ),
                html.Span(gap_name, className="small fw-semibold me-1 text-nowrap"),
                html.Small(condition, className="text-muted me-auto text-truncate",
                           style={"maxWidth": "160px"}),
                dbc.Button(
                    html.I(className="fa-solid fa-trash-can"),
                    id={"type": "home-gap-delete-btn", "patient": pid, "rule": rule_id},
                    color="link", size="sm", n_clicks=0,
                    className="text-danger p-0 ms-1 flex-shrink-0",
                    style={"lineHeight": "1"},
                ),
            ], className="d-flex align-items-center py-1 border-bottom gap-1",
               style={"minWidth": "240px"})
        )
    return html.Td(html.Div(items), className="align-top")


# ---------------------------------------------------------------------------
# Overview builder
# ---------------------------------------------------------------------------
def _build_overview(
    patients: list[dict], all_findings: dict, all_icd10: dict
) -> html.Div:
    if not patients:
        return dbc.Alert(
            [html.I(className="fa-solid fa-hourglass-half me-2"),
             "No patient records found. Run the Data Setup job from the ",
             html.Strong("Setup"), " tab."],
            color="warning",
        )

    total_icd10 = sum(len(v) for v in all_icd10.values())
    total_gaps  = sum(len(v) for v in all_findings.values())
    high_gaps   = sum(1 for v in all_findings.values()
                      for f in v if f.get("priority") == "HIGH")

    def _stat(value, label, color):
        return dbc.Col(
            dbc.Card(dbc.CardBody([
                html.H4(str(value), className=f"mb-0 text-{color}"),
                html.Small(label, className="text-muted"),
            ], className="py-2 text-center"), className="h-100 shadow-sm"),
            xs=6, md=True, className="mb-2",
        )

    stats = dbc.Row([
        _stat(len(patients), "Patients",          "primary"),
        _stat(total_icd10,   "ICD-10 Codes",      "info"),
        _stat(total_gaps,    "Care Gaps",          "secondary"),
        _stat(high_gaps,     "High Priority Gaps", "danger"),
    ], className="mb-3 g-2")

    tbody_rows = []
    for p in patients:
        pid      = p["patient_id"]
        gender   = p.get("gender", "")
        dob      = str(p.get("dob", ""))
        codes    = all_icd10.get(pid, [])
        findings = all_findings.get(pid, [])

        tbody_rows.append(html.Tr([
            html.Td(html.Strong(pid), className="align-top text-nowrap"),
            html.Td(html.Small(gender, className="text-muted"), className="align-top text-nowrap"),
            html.Td(html.Small(dob,    className="text-muted"), className="align-top text-nowrap"),
            _icd10_cell(pid, codes),
            _gap_cell(pid, findings),
        ]))

    table = dbc.Table(
        [
            html.Thead(html.Tr([
                html.Th("Patient ID"),
                html.Th("Gender"),
                html.Th("DOB"),
                html.Th("Saved ICD-10 Codes"),
                html.Th("Identified Care Gaps"),
            ]), className="table-dark"),
            html.Tbody(tbody_rows),
        ],
        bordered=False, hover=True, responsive=True, size="sm",
        className="mb-0 align-middle",
    )

    return html.Div([
        stats,
        dbc.Card(dbc.CardBody(table, className="p-0"), className="shadow-sm"),
    ])


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
def home_shell() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("Patient Records & Care Gaps", className="mb-0 fw-bold"), width="auto"),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"), "Refresh"],
                    id="home-overview-refresh-btn", color="outline-secondary", size="sm",
                ),
                width="auto", className="ms-auto",
            ),
        ], align="center", className="mb-3"),
        dbc.Spinner(
            html.Div(id="home-overview-content"),
            color="primary",
            spinner_style={"width": "2rem", "height": "2rem"},
        ),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("home-overview-content",   "children"),
    Output("home-patients-store",     "data"),
    Output("home-all-findings-store", "data"),
    Output("home-all-icd10-store",    "data"),
    Input("home-overview-refresh-btn","n_clicks"),
    Input("catalog-store",            "data"),
    Input("schema-store",             "data"),
    prevent_initial_call=False,
)
def refresh_overview(n_clicks, catalog, schema):
    cat = catalog or CATALOG
    sch = schema  or SCHEMA
    try:
        patients     = load_patients(cat, sch)
        all_findings = get_all_care_gap_findings(cat, sch)
        all_icd10    = get_all_icd10_saved_codes(cat, sch)
    except Exception as e:
        logger.error(f"Home overview load failed: {e}")
        return dbc.Alert(f"Error loading data: {e}", color="danger"), [], {}, {}
    return _build_overview(patients, all_findings, all_icd10), patients, all_findings, all_icd10


@callback(
    Output("home-delete-confirm-modal", "is_open"),
    Output("home-delete-modal-body",    "children"),
    Output("home-delete-target-store",  "data"),
    Input({"type": "home-icd10-delete-btn", "patient": ALL, "code": ALL}, "n_clicks"),
    Input({"type": "home-gap-delete-btn",   "patient": ALL, "rule": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def open_home_delete_modal(icd10_clicks, gap_clicks):
    if not any(icd10_clicks) and not any(gap_clicks):
        return dash.no_update, dash.no_update, dash.no_update
    triggered = callback_context.triggered_id
    if not isinstance(triggered, dict):
        return dash.no_update, dash.no_update, dash.no_update

    if triggered.get("type") == "home-icd10-delete-btn":
        pid  = triggered["patient"]
        code = triggered["code"]
        body = html.Span([
            "Delete ICD-10 code ", html.Strong(code),
            f" for patient {pid}? This cannot be undone.",
        ])
        return True, body, {"delete_type": "icd10", "patient_id": pid, "identifier": code}

    if triggered.get("type") == "home-gap-delete-btn":
        pid     = triggered["patient"]
        rule_id = triggered["rule"]
        body = html.Span([
            "Delete care gap ", html.Strong(rule_id),
            f" for patient {pid}? This cannot be undone.",
        ])
        return True, body, {"delete_type": "gap", "patient_id": pid, "identifier": rule_id}

    return dash.no_update, dash.no_update, dash.no_update


@callback(
    Output("home-delete-confirm-modal", "is_open",  allow_duplicate=True),
    Output("home-overview-content",     "children", allow_duplicate=True),
    Output("home-all-icd10-store",      "data",     allow_duplicate=True),
    Output("home-all-findings-store",   "data",     allow_duplicate=True),
    Input("home-delete-confirm-btn",    "n_clicks"),
    Input("home-delete-cancel-btn",     "n_clicks"),
    State("home-delete-target-store",   "data"),
    State("home-patients-store",        "data"),
    State("home-all-icd10-store",       "data"),
    State("home-all-findings-store",    "data"),
    State("catalog-store",              "data"),
    State("schema-store",               "data"),
    prevent_initial_call=True,
)
def handle_home_delete(confirm_n, cancel_n, target, patients,
                       all_icd10, all_findings, catalog, schema):
    triggered = callback_context.triggered_id
    if triggered == "home-delete-cancel-btn":
        return False, dash.no_update, dash.no_update, dash.no_update
    if not confirm_n or not target:
        return False, dash.no_update, dash.no_update, dash.no_update

    delete_type = target.get("delete_type")
    patient_id  = target.get("patient_id", "")
    identifier  = target.get("identifier", "")
    cat         = catalog or CATALOG
    sch         = schema  or SCHEMA

    updated_icd10    = dict(all_icd10    or {})
    updated_findings = dict(all_findings or {})

    try:
        if delete_type == "icd10":
            delete_icd10_saved_code(patient_id, identifier, cat, sch)
            if patient_id in updated_icd10:
                updated_icd10[patient_id] = [
                    c for c in updated_icd10[patient_id] if c.get("code") != identifier
                ]
        else:
            delete_care_gap_finding(patient_id, identifier, cat, sch)
            if patient_id in updated_findings:
                updated_findings[patient_id] = [
                    f for f in updated_findings[patient_id] if f.get("rule_id") != identifier
                ]
    except Exception as e:
        logger.error(f"Home delete ({delete_type}) failed: {e}")
        return False, dash.no_update, dash.no_update, dash.no_update

    content = _build_overview(patients or [], updated_findings, updated_icd10)
    return False, content, updated_icd10, updated_findings
