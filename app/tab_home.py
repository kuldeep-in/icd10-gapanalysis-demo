import dash
from dash import dcc, html, callback, Input, Output, State, ALL, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from config import CATALOG, SCHEMA, logger
from db import (
    load_patients, get_all_care_gap_findings, get_all_icd10_saved_codes,
    delete_care_gap_finding, delete_icd10_saved_code,
)

_P_COLOR    = {"HIGH": "danger",  "MEDIUM": "warning", "LOW": "info"}
_TYPE_COLOR = {"Primary Diagnosis": "danger", "Secondary Diagnosis": "primary"}


# ---------------------------------------------------------------------------
# Expanded-row content helpers (no Td wrapper — used inside accordion body)
# ---------------------------------------------------------------------------
def _icd10_items(pid: str, codes: list[dict]) -> html.Div:
    if not codes:
        return html.Small("No ICD-10 codes saved yet.", className="text-muted fst-italic")
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
                           style={"maxWidth": "200px"}),
                dbc.Button(
                    html.I(className="fa-solid fa-trash-can"),
                    id={"type": "home-icd10-delete-btn", "patient": pid, "code": code},
                    color="link", size="sm", n_clicks=0,
                    className="text-danger p-0 ms-1 flex-shrink-0",
                    style={"lineHeight": "1"},
                ),
            ], className="d-flex align-items-center py-1 border-bottom gap-1")
        )
    return html.Div(items)


def _gap_items(pid: str, findings: list[dict]) -> html.Div:
    if not findings:
        return html.Small("No care gaps identified yet.", className="text-muted fst-italic")
    items = []
    for f in findings:
        priority  = f.get("priority", "")
        gap_name  = f.get("gap_name", "—")
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
                           style={"maxWidth": "200px"}),
                dbc.Button(
                    html.I(className="fa-solid fa-trash-can"),
                    id={"type": "home-gap-delete-btn", "patient": pid, "rule": rule_id},
                    color="link", size="sm", n_clicks=0,
                    className="text-danger p-0 ms-1 flex-shrink-0",
                    style={"lineHeight": "1"},
                ),
            ], className="d-flex align-items-center py-1 border-bottom gap-1")
        )
    return html.Div(items)


# ---------------------------------------------------------------------------
# Accordion item per patient
# ---------------------------------------------------------------------------
def _patient_accordion_item(p: dict, codes: list[dict], findings: list[dict]) -> dbc.AccordionItem:
    pid    = p["patient_id"]
    gender = p.get("gender", "")
    dob    = str(p.get("dob", ""))[:10]

    # ICD badge strip (collapsed title) — up to 4 codes then +N
    icd_badges = []
    for c in codes[:4]:
        diag_type = c.get("diag_type", "")
        color     = "danger" if "Primary" in diag_type else "primary"
        icd_badges.append(
            dbc.Badge(c.get("code", ""), color=color, pill=True,
                      className="font-monospace", style={"fontSize": "12px", "padding": "5px 10px"})
        )
    if len(codes) > 4:
        icd_badges.append(
            dbc.Badge(f"+{len(codes) - 4}", color="secondary", pill=True,
                      style={"fontSize": "12px", "padding": "5px 10px"})
        )

    n_gaps   = len(findings)
    has_high = any(f.get("priority") == "HIGH" for f in findings)
    gap_color      = "danger" if has_high else ("warning" if n_gaps > 0 else "light")
    gap_text_color = "white"  if (has_high or n_gaps > 0) else None
    gap_style      = {"fontSize": "10px"} if n_gaps > 0 else {"fontSize": "10px", "color": "#198754"}

    title = html.Div([
        html.Span([
            html.I(className="fa-solid fa-circle-user me-2",
                   style={"color": "var(--hc-primary)", "fontSize": "16px"}),
            html.Span(pid),
        ], className="fw-bold me-3 d-flex align-items-center gap-1",
           style={"minWidth": "90px"}),
        html.Small(f"{gender}, {dob}", className="text-muted me-3",
                   style={"width": "140px", "flexShrink": "0", "fontSize": "12px"}),
        dbc.Badge(
            f"{n_gaps} gap{'s' if n_gaps != 1 else ''}",
            color=gap_color, text_color=gap_text_color,
            pill=True,
            style={**gap_style, "width": "72px", "textAlign": "center",
                   "fontSize": "12px", "flexShrink": "0",
                   "padding": "6px 12px", "marginRight": "20px"},
        ),
        html.Div(
            icd_badges if icd_badges
            else [html.Small("No ICD-10 codes", className="text-muted")],
            className="d-flex flex-wrap gap-1",
        ),
    ], className="d-flex align-items-center flex-wrap gap-2 w-100")

    body = dbc.Row([
        dbc.Col([
            html.P([html.I(className="fa-solid fa-stethoscope me-1 text-success"),
                    "Care Gaps"],
                   className="fw-semibold small text-muted mb-2"),
            _gap_items(pid, findings),
        ], md=6, className="pe-3"),
        dbc.Col([
            html.P([html.I(className="fa-solid fa-file-medical me-1 text-info"),
                    "ICD-10 Codes"],
                   className="fw-semibold small text-muted mb-2"),
            _icd10_items(pid, codes),
        ], md=6, className="ps-3 border-start"),
    ], className="py-2 g-0")

    return dbc.AccordionItem(body, title=title, item_id=pid)


# ---------------------------------------------------------------------------
# ICD-10 top-5 bar chart
# ---------------------------------------------------------------------------
def _icd10_bar_chart(all_icd10: dict):
    """Horizontal bar chart of the top 5 most-saved ICD-10 codes."""
    code_counts: dict = {}
    for codes in all_icd10.values():
        for c in codes:
            code = c.get("code", "")
            if code:
                code_counts[code] = code_counts.get(code, 0) + 1

    if not code_counts:
        return html.Div()

    top5   = sorted(code_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    codes  = [t[0] for t in reversed(top5)]   # reversed → highest bar at top
    counts = [t[1] for t in reversed(top5)]

    fig = go.Figure(go.Bar(
        x=counts, y=codes, orientation="h",
        marker={"color": "#4FC3F7", "line": {"width": 0}},
        text=counts,
        textposition="outside",
        textfont={"color": "#D6EAF8", "size": 11},
        cliponaxis=False,
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin={"l": 10, "r": 30, "t": 4, "b": 4},
        height=175,
        xaxis={"showgrid": False, "showticklabels": False,
               "zeroline": False, "range": [0, max(counts) * 1.3]},
        yaxis={"tickfont": {"color": "#D6EAF8", "size": 11, "family": "monospace"},
               "showgrid": False, "automargin": True},
        showlegend=False,
    )
    return html.Div([
        html.Small("Top 5 Codes", className="text-muted",
                   style={"fontSize": "10px", "letterSpacing": "0.4px"}),
        dcc.Graph(figure=fig, config={"displayModeBar": False},
                  style={"height": "175px"}),
    ], className="mt-1")


# ---------------------------------------------------------------------------
# Overview builder
# ---------------------------------------------------------------------------
def _build_overview(patients: list[dict], all_findings: dict, all_icd10: dict) -> html.Div:
    if not patients:
        return dbc.Container(dbc.Alert(
            [html.I(className="fa-solid fa-hourglass-half me-2"),
             html.Strong("No patient records found. "),
             "Click the ", html.Strong("⚙ Setup"), " icon in the navbar and run the Data Setup job."],
            color="warning", className="mt-3",
        ), fluid=True)

    total_icd10        = sum(len(v) for v in all_icd10.values())
    missing_icd10      = sum(1 for p in patients if not all_icd10.get(p["patient_id"]))
    patients_with_gaps = sum(1 for v in all_findings.values() if v)
    patients_high_gaps = sum(1 for v in all_findings.values()
                             if any(f.get("priority") == "HIGH" for f in v))
    high_gaps          = sum(1 for v in all_findings.values()
                             for f in v if f.get("priority") == "HIGH")
    medium_gaps        = sum(1 for v in all_findings.values()
                             for f in v if f.get("priority") == "MEDIUM")

    def _stat(value, label, color, tall=False):
        return dbc.Card(dbc.CardBody([
            html.H4(str(value), className=f"mb-0 text-{color}"),
            html.Small(label, className="text-muted"),
        ], className="py-2 text-center d-flex flex-column justify-content-center",
           style={"minHeight": "90px"} if tall else {}),
        className="shadow-sm h-100")

    def _section(title, icon, accent, bg, rows_content):
        """Styled section card with coloured header band and tinted background."""
        return html.Div([
            # Section header
            html.Div([
                html.I(className=f"fa-solid {icon} me-2 fa-sm",
                       style={"color": accent}),
                html.Span(title, className="fw-bold",
                          style={"fontSize": "13px", "color": accent,
                                 "letterSpacing": "0.2px"}),
            ], className="px-2 py-2",
               style={"borderBottom": f"1px solid {accent}33",
                      "background": f"{accent}14",
                      "borderRadius": "8px 8px 0 0"}),
            # Content rows
            html.Div(rows_content, className="p-2"),
        ], className="rounded mb-3",
           style={"background": bg,
                  "border": f"1px solid {accent}30",
                  "borderRadius": "8px"})

    stats_col = html.Div([
        # ── Total Patients ──────────────────────────────
        dbc.Row([
            dbc.Col(_stat(len(patients), "Patients", "primary"), className="mb-3"),
        ]),

        # ── Care Gap Stats ──────────────────────────────
        _section("Care Gap Stats", "fa-stethoscope", "#4DD0E1",
                 "rgba(77,208,225,0.05)", [
            dbc.Row([
                dbc.Col(_stat(patients_with_gaps, "Pts with Gaps",    "secondary", tall=True), width=6),
                dbc.Col(_stat(patients_high_gaps, "Pts High Priority", "danger",   tall=True), width=6),
            ], className="mb-2 g-2"),
            dbc.Row([
                dbc.Col(_stat(high_gaps,   "High Gaps",   "danger",  tall=True), width=6),
                dbc.Col(_stat(medium_gaps, "Medium Gaps", "warning", tall=True), width=6),
            ], className="g-2"),
        ]),

        # ── ICD-10 Stats ────────────────────────────────
        _section("ICD-10 Stats", "fa-file-medical", "#4FC3F7",
                 "rgba(79,195,247,0.05)", [
            dbc.Row([
                dbc.Col(_stat(total_icd10,   "Total Codes",    "info",    tall=True), width=6),
                dbc.Col(_stat(missing_icd10, "Missing ICD-10", "warning", tall=True), width=6),
            ], className="g-2 mb-2"),
            _icd10_bar_chart(all_icd10),
        ]),
    ])

    accordion = dbc.Accordion(
        [_patient_accordion_item(
             p,
             all_icd10.get(p["patient_id"], []),
             all_findings.get(p["patient_id"], []),
         )
         for p in patients],
        id="home-patient-accordion",
        always_open=True,
        active_item=[],
        className="shadow-sm",
    )

    return dbc.Row([
        dbc.Col(stats_col, md=3),
        dbc.Col(accordion,  md=9),
    ], className="g-3")


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
    Input("home-overview-refresh-btn", "n_clicks"),
    Input("active-tab-store",          "data"),
    State("catalog-store",             "data"),
    State("schema-store",              "data"),
    prevent_initial_call=True,
)
def refresh_overview(n_clicks, active_tab, catalog, schema):
    if active_tab and active_tab != "tab-home":
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    cat = catalog or CATALOG
    sch = schema  or SCHEMA
    try:
        patients     = load_patients(cat, sch)
        all_findings = get_all_care_gap_findings(cat, sch)
        all_icd10    = get_all_icd10_saved_codes(cat, sch)
    except Exception as e:
        logger.error(f"Home overview load failed: {e}")
        if any(x in str(e) for x in ("TABLE_OR_VIEW_NOT_FOUND", "TABLE_NOT_FOUND",
                                      "does not exist", "SCHEMA_NOT_FOUND")):
            return dbc.Container(dbc.Alert(
                [html.I(className="fa-solid fa-hourglass-half me-2"),
                 html.Strong("No patient records found. "),
                 "Click the ", html.Strong("⚙ Setup"), " icon in the navbar and run the Data Setup job."],
                color="warning", className="mt-3",
            ), fluid=True), [], {}, {}
        return dbc.Container(
            dbc.Alert(f"Error loading data: {e}", color="danger", className="mt-3"),
            fluid=True,
        ), [], {}, {}
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
