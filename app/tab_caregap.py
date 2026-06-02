import json
import re

import dash
from dash import dcc, html, callback, clientside_callback, Input, Output, State, MATCH, ALL, callback_context
import dash_bootstrap_components as dbc
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from config import w, CATALOG, SCHEMA, FMAPI_ENDPOINT, logger
from db import (
    get_patient_record, get_care_gap_rules,
    save_care_gap_finding, delete_care_gap_finding,
    get_patient_care_gap_findings, patient_options,
)

# ---------------------------------------------------------------------------
# Rule retrieval — Vector Search primary, full-table fallback
# ---------------------------------------------------------------------------
_rules_cache: list[dict] = []
_VS_NUM_RESULTS = 15   # max rules retrieved per query; covers all 20 current rules
                        # scales to hundreds without changing this number


def _get_all_rules(cat: str, sch: str) -> list[dict]:
    """Fallback: return all rules from DB (cached)."""
    global _rules_cache
    if not _rules_cache:
        _rules_cache = get_care_gap_rules(cat, sch)
    return _rules_cache


def _retrieve_relevant_rules(clinical_text: str, cat: str, sch: str) -> list[dict]:
    """
    Use Vector Search (raw REST API) to retrieve the most semantically relevant
    care gap rules for this patient's clinical note.  The index embeds rich medical
    text so clinical abbreviations, medication names, and lab values all contribute
    to retrieval — no ICD-10 codes needed.

    Falls back to all rules if VS is unavailable or not yet configured.
    """
    index = f"{cat}.{sch}.care_gap_rules_vs_index"
    try:
        resp = w.api_client.do(
            "POST",
            f"/api/2.0/vector-search/indexes/{index}/query",
            body={
                "query_text": clinical_text[:3000],
                "columns":    ["rule_id", "gap_name", "condition",
                               "check_description", "priority", "guideline"],
                "num_results": _VS_NUM_RESULTS,
            },
        )
        # Response: {"result": {"data_array": [[...]], ...}, "manifest": {"schema": {"columns": [...]}}}
        data_array = (resp.get("result") or {}).get("data_array") or []
        if not data_array:
            logger.warning("VS returned no results — falling back to all rules")
            return _get_all_rules(cat, sch)
        cols  = [c["name"] for c in (resp.get("manifest") or {}).get("schema", {}).get("columns", [])]
        rules = [dict(zip(cols, row)) for row in data_array]
        logger.info(f"VS retrieved {len(rules)} relevant rules from {index}")
        return rules
    except Exception as e:
        logger.warning(f"VS query failed ({e}) — falling back to all rules")
        return _get_all_rules(cat, sch)


# ---------------------------------------------------------------------------
# AI helper
# ---------------------------------------------------------------------------
def call_care_gap_model(patient_record: dict, rules: list[dict]) -> list[dict]:
    rules_text = "\n".join(
        f"[{r['rule_id']}] {r['gap_name']} ({r['condition']}): "
        f"{r['check_description']} | {r['priority']} | {r['guideline']}"
        for r in rules
    )
    response = w.serving_endpoints.query(
        name=FMAPI_ENDPOINT,
        messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=(
                "You are a clinical care gap analyzer. Given a patient record and a set of "
                "care gap rules, identify ONLY the gaps that apply based on what is documented. "
                "Return a JSON array only — each object must have: "
                "rule_id, gap_name, condition, priority (HIGH/MEDIUM/LOW), guideline, "
                "finding (what in the record triggers this gap), recommended_action. "
                "No prose, no markdown."
            )),
            ChatMessage(role=ChatMessageRole.USER, content=(
                f"Patient Record:\n{patient_record['clinicalrecord']}\n\n"
                f"Care Gap Rules to evaluate:\n{rules_text}\n\n"
                f"Return applicable gaps as JSON array."
            )),
        ],
        max_tokens=2000,
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
# Render helper
# ---------------------------------------------------------------------------
def _render_saved_findings(findings: list[dict]) -> html.Div | None:
    if not findings:
        return None
    P_COLOR = {"HIGH": "danger", "MEDIUM": "warning", "LOW": "info"}
    rows = []
    for i, f in enumerate(findings):
        priority = f.get("priority", "")
        ts       = str(f.get("created_at") or "")[:16]
        rows.append(
            html.Div([
                dbc.Badge(
                    priority or "?",
                    color=P_COLOR.get(priority, "secondary"),
                    className="me-2 flex-shrink-0",
                    style={"minWidth": "60px", "textAlign": "center"},
                ),
                html.Span(f.get("gap_name", "—"), className="small fw-semibold me-1"),
                html.Span(f.get("condition", ""), className="small text-muted me-auto"),
                html.Small(ts, className="text-muted ms-2 text-nowrap flex-shrink-0"),
                dbc.Button(
                    html.I(className="fa-solid fa-trash-can"),
                    id={"type": "delete-gap-btn", "index": i},
                    color="link", size="sm", n_clicks=0,
                    className="text-danger p-0 ms-2 flex-shrink-0",
                    style={"lineHeight": "1"},
                ),
            ], className="d-flex align-items-center py-1 border-bottom")
        )
    count = len(findings)
    return html.Div([
        html.Div([
            html.I(className="fa-solid fa-bookmark me-2 text-success"),
            html.Span(
                f"Saved Findings — {count} record{'s' if count != 1 else ''}",
                className="small fw-semibold text-success",
            ),
        ], className="mb-2 mt-3 pt-2 border-top"),
        html.Div(rows),
    ])


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _build_gap_cards(gaps: list[dict], saved_rule_ids: set) -> html.Div:
    """Build the care gap result cards (shared by run_gaps and gap_layout restore)."""
    if not gaps:
        return dbc.Alert(
            [html.I(className="fa-solid fa-circle-check me-2"), "No care gaps identified."],
            color="success",
        )
    P = {"HIGH": "danger", "MEDIUM": "warning", "LOW": "info"}
    cards = []
    for i, g in enumerate(gaps):
        already_saved = g.get("rule_id") in saved_rule_ids
        save_btn = dbc.Button(
            [html.I(className=f"fa-solid {'fa-circle-check' if already_saved else 'fa-floppy-disk'} me-2"),
             "Saved" if already_saved else "Save Finding"],
            id={"type": "save-gap-btn", "index": i},
            color="success" if already_saved else "outline-secondary",
            disabled=already_saved, size="sm", className="mt-2", n_clicks=0,
        )
        cards.append(dbc.Card([
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
                html.Div([save_btn, html.Div(id={"type": "save-gap-result", "index": i})],
                         className="text-end"),
            ]),
        ], outline=True, color=P.get(g.get("priority", ""), "secondary"), className="mb-2 shadow-sm"))
    return html.Div([
        dbc.Alert(
            [html.I(className="fa-solid fa-triangle-exclamation me-2"),
             f"{len(gaps)} care gap(s) identified"],
            color="warning", className="mb-3",
        ),
        *cards,
    ])


def gap_layout(patients: list[dict],
               restore_patient: str = None,
               restore_gaps: list = None,
               restore_saved_findings: list = None) -> dbc.Container:
    options        = patient_options(patients)
    saved_rule_ids = {f.get("rule_id") for f in (restore_saved_findings or [])}
    init_results   = (
        _build_gap_cards(restore_gaps, saved_rule_ids)
        if restore_gaps
        else dbc.Alert("Analysis results will appear here after clicking Identify Care Gaps.",
                       color="secondary")
    )
    init_saved = (_render_saved_findings(restore_saved_findings)
                  if restore_saved_findings
                  else dbc.Alert("Select a patient to see saved findings.", color="secondary"))

    def _col_label(icon, text):
        return html.Div([
            html.I(className=f"fa-solid {icon} me-1"),
            html.Span(text, className="fw-semibold"),
        ], className="mb-1 small")

    return dbc.Container([
        # ── Title ───────────────────────────────────────
        html.Div([
            html.H4(
                [html.I(className="fa-solid fa-stethoscope me-2 text-success"),
                 "Care Gap Advisor"],
                className="mb-0 fw-bold me-3 d-inline",
            ),
            html.Small(
                "Compares against ADA, ACC/AHA, GOLD, NCCN, KDIGO guidelines.",
                className="text-muted fw-normal",
            ),
        ], className="d-flex align-items-baseline flex-wrap mb-3"),

        # Loading modal
        dbc.Modal([
            dbc.ModalBody([html.Div([
                html.Div(html.I(className="fa-solid fa-stethoscope fa-beat fa-3x text-success"),
                         className="mb-3"),
                html.H5("Identifying Care Gaps", className="fw-bold mb-1"),
                html.P("Comparing against clinical guidelines",
                       className="text-muted small mb-3"),
                dbc.Progress(value=100, striped=True, animated=True, color="success",
                             style={"height": "6px"}, className="mb-4"),
                html.Div([
                    html.Div([html.I(className="fa-solid fa-notes-medical me-2 text-info"),
                              "Reviewing patient history"],
                             className="small text-muted mb-2 d-flex align-items-center justify-content-center"),
                    html.Div([html.I(className="fa-solid fa-magnifying-glass me-2 text-warning"),
                              "Matching clinical guidelines"],
                             className="small text-muted mb-2 d-flex align-items-center justify-content-center"),
                    html.Div([html.I(className="fa-solid fa-shield-heart me-2 text-danger"),
                              "Assessing care gaps"],
                             className="small text-muted d-flex align-items-center justify-content-center"),
                ]),
            ], className="text-center py-2")]),
        ], id="gap-loading-modal", is_open=False, centered=True,
           backdrop="static", keyboard=False, size="sm"),

        # ── Row 1: Patient select + Identify button ─────
        dbc.Row([
            dbc.Col([
                dcc.Dropdown(id="gap-patient-select", options=options,
                             value=restore_patient,
                             placeholder="Choose a patient...", className="mb-1"),
            ], width=8),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-stethoscope me-2"), "Identify Care Gaps"],
                    id="gap-analyze-btn", color="primary", size="lg",
                    disabled=not bool(restore_patient),
                    className="w-100 h-100",
                ),
                width=4,
            ),
        ], className="mb-3 g-3 align-items-stretch"),

        # ── Row 2: 2-column equal body ───────────────────
        dbc.Row([
            # Col 1 — Clinical Record (50%)
            dbc.Col([
                _col_label("fa-file-waveform text-muted", "Clinical Record"),
                dcc.Textarea(
                    id="gap-clinical-record",
                    style={"width": "100%", "height": "70vh",
                           "fontSize": "12px", "fontFamily": "monospace",
                           "resize": "none"},
                    readOnly=True,
                    placeholder="Select a patient to load their clinical note...",
                ),
            ], width=6),

            # Col 2 — Saved findings (top) + Analysis results (below), single scroll (50%)
            dbc.Col([
                html.Div([
                    _col_label("fa-bookmark text-success", "Saved Care Gaps"),
                    html.Div(id="saved-findings-display", children=init_saved),
                    html.Hr(className="my-3"),
                    _col_label("fa-stethoscope text-success", "Analysis Results"),
                    html.Div(id="gap-results", children=init_results),
                ], style={"height": "70vh", "overflowY": "auto",
                          "paddingRight": "4px"}),
            ], width=6),
        ], className="g-3"),
    ], fluid=True)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("gap-analyze-btn",          "disabled"),
    Output("saved-findings-store",     "data"),
    Output("gap-patient-id-store",     "data", allow_duplicate=True),
    Output("gap-clinical-record",      "value"),
    Output("gap-results",              "children", allow_duplicate=True),
    Input("gap-patient-select",        "value"),
    State("catalog-store",             "data"),
    State("schema-store",              "data"),
    prevent_initial_call=True,
)
def on_patient_select(patient_id, catalog, schema):
    default_results = dbc.Alert(
        [html.I(className="fa-solid fa-stethoscope me-2"),
         "Click Identify Care Gaps to analyse this patient's clinical record."],
        color="secondary",
    )
    if not patient_id:
        return True, [], "", "", default_results
    cat = catalog or CATALOG
    sch = schema  or SCHEMA
    findings = get_patient_care_gap_findings(patient_id, cat, sch)
    record   = get_patient_record(patient_id, cat, sch)
    clinical = record["clinicalrecord"] if record else ""
    return False, findings, patient_id, clinical, default_results


# Open healthcare modal immediately on button click (no server round-trip needed)
clientside_callback(
    """function(n) {
        if (n > 0) return true;
        return window.dash_clientside.no_update;
    }""",
    Output("gap-loading-modal", "is_open"),
    Input("gap-analyze-btn",    "n_clicks"),
    prevent_initial_call=True,
)


@callback(
    Output("gap-results",          "children"),
    Output("gap-results-store",    "data"),
    Output("gap-patient-id-store", "data",   allow_duplicate=True),
    Output("gap-loading-modal",    "is_open", allow_duplicate=True),
    Input("gap-analyze-btn",       "n_clicks"),
    State("gap-patient-select",    "value"),
    State("catalog-store",         "data"),
    State("schema-store",          "data"),
    State("saved-findings-store",  "data"),
    prevent_initial_call=True,
)
def run_gaps(n_clicks, patient_id, catalog, schema, saved_findings):
    if not n_clicks or not patient_id:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    cat = catalog or CATALOG
    sch = schema  or SCHEMA

    saved_rule_ids = {f.get("rule_id") for f in (saved_findings or [])}

    try:
        patient = get_patient_record(patient_id, cat, sch)
        rules   = _retrieve_relevant_rules(patient["clinicalrecord"], cat, sch)
        gaps    = call_care_gap_model(patient, rules)

        if not gaps:
            return (
                dbc.Alert(
                    [html.I(className="fa-solid fa-circle-check me-2"),
                     f"No care gaps identified for {patient_id}."],
                    color="success"
                ),
                [], patient_id, False,
            )

        return _build_gap_cards(gaps, saved_rule_ids), gaps, patient_id, False
    except Exception as e:
        logger.error(f"Care gap analysis: {e}")
        return dbc.Alert(f"Analysis failed: {e}", color="danger"), [], "", False


@callback(
    Output({"type": "save-gap-btn",    "index": MATCH}, "children"),
    Output({"type": "save-gap-btn",    "index": MATCH}, "disabled"),
    Output({"type": "save-gap-btn",    "index": MATCH}, "color"),
    Output({"type": "save-gap-result", "index": MATCH}, "children"),
    Input({"type": "save-gap-btn",     "index": MATCH}, "n_clicks"),
    State("gap-results-store",    "data"),
    State("gap-patient-id-store", "data"),
    prevent_initial_call=True,
)
def save_gap_finding(n_clicks, gaps, patient_id):
    no_upd = (dash.no_update,) * 4
    if not n_clicks or not gaps or not patient_id:
        return no_upd
    idx = callback_context.triggered_id["index"]
    if idx >= len(gaps):
        return no_upd
    gap = gaps[idx]
    try:
        save_care_gap_finding(patient_id, gap)
        return (
            [html.I(className="fa-solid fa-circle-check me-2"), "Saved"],
            True,
            "success",
            None,
        )
    except Exception as e:
        logger.error(f"Save care gap finding: {e}")
        return (
            [html.I(className="fa-solid fa-floppy-disk me-2"), "Save Finding"],
            False,
            "outline-secondary",
            dbc.Alert(str(e)[:120], color="danger", className="py-1 small mt-1"),
        )


@callback(
    Output("saved-findings-display",              "children"),
    Input("saved-findings-store",                 "data"),
    Input({"type": "save-gap-btn", "index": ALL}, "disabled"),
    State("gap-patient-id-store",                 "data"),
    State("catalog-store",                        "data"),
    State("schema-store",                         "data"),
    prevent_initial_call=True,
)
def render_saved_findings_display(findings_store, btn_disabled, patient_id, catalog, schema):
    triggered = callback_context.triggered_id
    if isinstance(triggered, dict) and triggered.get("type") == "save-gap-btn":
        if not patient_id:
            return dash.no_update
        fresh = get_patient_care_gap_findings(
            patient_id, catalog or CATALOG, schema or SCHEMA
        )
        return _render_saved_findings(fresh)
    return _render_saved_findings(findings_store or [])


@callback(
    Output("delete-confirm-modal", "is_open"),
    Output("delete-modal-body",    "children"),
    Output("delete-target-store",  "data"),
    Input({"type": "delete-gap-btn", "index": ALL}, "n_clicks"),
    State("saved-findings-store",  "data"),
)
def open_delete_modal(btn_clicks, findings):
    if not any(btn_clicks):
        return dash.no_update, dash.no_update, dash.no_update
    triggered = callback_context.triggered_id
    if not isinstance(triggered, dict):
        return dash.no_update, dash.no_update, dash.no_update
    idx           = triggered["index"]
    findings_list = findings or []
    if idx >= len(findings_list):
        return dash.no_update, dash.no_update, dash.no_update
    finding   = findings_list[idx]
    rule_id   = finding.get("rule_id",   "")
    gap_name  = finding.get("gap_name",  rule_id)
    condition = finding.get("condition", "")
    body = html.Span([
        "Delete ",
        html.Strong(gap_name),
        f" ({condition})" if condition else "",
        "? This cannot be undone.",
    ])
    return True, body, rule_id


@callback(
    Output("delete-confirm-modal", "is_open",  allow_duplicate=True),
    Output("saved-findings-store", "data",     allow_duplicate=True),
    Input("delete-confirm-btn",    "n_clicks"),
    Input("delete-cancel-btn",     "n_clicks"),
    State("delete-target-store",   "data"),
    State("saved-findings-store",  "data"),
    State("gap-patient-id-store",  "data"),
    State("catalog-store",         "data"),
    State("schema-store",          "data"),
    prevent_initial_call=True,
)
def handle_delete_modal(confirm_n, cancel_n, rule_id, findings, patient_id, catalog, schema):
    triggered = callback_context.triggered_id
    if triggered == "delete-cancel-btn":
        return False, dash.no_update
    if not confirm_n or not rule_id or not patient_id:
        return False, dash.no_update
    try:
        delete_care_gap_finding(
            patient_id, rule_id, catalog or CATALOG, schema or SCHEMA
        )
    except Exception as e:
        logger.error(f"Delete care gap finding {rule_id}: {e}")
        return False, dash.no_update
    updated = [f for f in (findings or []) if f.get("rule_id") != rule_id]
    return False, updated
