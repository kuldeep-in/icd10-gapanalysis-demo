import os

import dash
from dash import dcc, html, callback, Input, Output, State, callback_context
import dash_bootstrap_components as dbc

from config import CATALOG, SCHEMA, BRAND_ORANGE

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

import tab_home      # noqa: E402
import tab_icd10     # noqa: E402
import tab_caregap   # noqa: E402
import tab_setup     # noqa: E402

# ---------------------------------------------------------------------------
# Navbar
# ---------------------------------------------------------------------------
NAVBAR = dbc.Navbar(
    dbc.Container([
        dbc.NavbarBrand(
            [html.I(className="fa-solid fa-heart-pulse me-2", style={"color": BRAND_ORANGE}),
             "Clinical AI Demo"],
            className="fw-bold fs-5"
        ),
        html.Div([
            html.Button(
                [html.I(className="fa-solid fa-house fa-lg me-1"),
                 html.Span("Home", style={"fontSize": "13px"})],
                id="nav-home-btn", n_clicks=0,
                className="btn btn-link text-white text-decoration-none d-flex align-items-center gap-1 p-0",
                style={"opacity": "0.8", "background": "none", "border": "none"},
            ),
            html.Button(
                [html.I(className="fa-solid fa-file-medical fa-lg me-1"),
                 html.Span("ICD-10", style={"fontSize": "13px"})],
                id="nav-icd10-btn", n_clicks=0,
                className="btn btn-link text-white text-decoration-none d-flex align-items-center gap-1 p-0",
                style={"opacity": "0.8", "background": "none", "border": "none"},
            ),
            html.Button(
                [html.I(className="fa-solid fa-stethoscope fa-lg me-1"),
                 html.Span("Care Gap", style={"fontSize": "13px"})],
                id="nav-caregap-btn", n_clicks=0,
                className="btn btn-link text-white text-decoration-none d-flex align-items-center gap-1 p-0",
                style={"opacity": "0.8", "background": "none", "border": "none"},
            ),
            html.Button(
                [html.I(id="navbar-setup-icon", className="fa-solid fa-gear fa-lg me-1"),
                 html.Span("Setup", style={"fontSize": "13px"})],
                id="nav-setup-btn", n_clicks=0,
                className="btn btn-link text-white text-decoration-none d-flex align-items-center gap-1 p-0 pe-3",
                style={"opacity": "0.8", "background": "none", "border": "none"},
            ),
        ], className="ms-auto d-flex align-items-center gap-3"),
    ], fluid=True),
    color="dark", dark=True, className="mb-0 shadow-sm"
)

# ---------------------------------------------------------------------------
# Layout — tabs and tab-content stay in the initial layout so render_tab
# always has its Input available. Routing shows/hides sections via style.
# ---------------------------------------------------------------------------
app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    NAVBAR,

    # Dummy target for clientside URL-hash updater
    html.Div(id="url-updater", style={"display": "none"}),

    # /setup page — dynamically populated, empty on root
    html.Div(id="setup-page-content"),

    # Active tab tracker — replaces dbc.Tabs (navbar icons drive navigation)
    dcc.Store(id="active-tab-store", data="tab-home"),

    # Main demo — always in DOM, hidden when on /setup
    html.Div(id="main-demo-content", children=[
        # Lightweight check on root load: reads localStorage flag or does 1 SQL
        dcc.Interval(id="root-setup-check", interval=300, max_intervals=1, n_intervals=0),
        html.Div(id="tab-content", className="px-4 py-3"),
    ]),

    # Tracks which view is active: "demo" or "setup"
    dcc.Store(id="active-page-store", data="demo"),

    # Persistent flag — survives browser refresh, written once setup is confirmed complete
    dcc.Store(id="setup-complete-store", storage_type="local"),

    # Session stores
    dcc.Store(id="all-done-store",    data=False),
    dcc.Store(id="patient-store",     data=[]),
    dcc.Store(id="ka-endpoint-store", data=""),
    dcc.Store(id="catalog-store",     data=CATALOG),
    dcc.Store(id="schema-store",      data=SCHEMA),

    # ICD-10 Analyzer stores
    dcc.Store(id="icd10-patient-store",     data=""),
    dcc.Store(id="icd10-codes-store",       data=[]),
    dcc.Store(id="icd10-saved-codes-store", data=[]),

    # Care Gap Advisor stores
    dcc.Store(id="gap-results-store",    data=[]),
    dcc.Store(id="gap-patient-id-store", data=""),
    dcc.Store(id="saved-findings-store", data=[]),
    dcc.Store(id="delete-target-store",  data=None),

    # Home overview stores
    dcc.Store(id="home-patients-store",      data=[]),
    dcc.Store(id="home-all-findings-store",  data={}),
    dcc.Store(id="home-all-icd10-store",     data={}),
    dcc.Store(id="home-delete-target-store", data=None),

    # Care Gap delete modal
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle([
            html.I(className="fa-solid fa-trash-can me-2 text-danger"), "Delete Finding",
        ])),
        dbc.ModalBody(id="delete-modal-body",
                      children="Are you sure you want to delete this finding?"),
        dbc.ModalFooter([
            dbc.Button([html.I(className="fa-solid fa-trash-can me-2"), "Delete"],
                       id="delete-confirm-btn", color="danger", size="sm"),
            dbc.Button("Cancel", id="delete-cancel-btn",
                       color="secondary", outline=True, size="sm", className="ms-2"),
        ]),
    ], id="delete-confirm-modal", is_open=False, size="sm", centered=True),

    # Home delete modal
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle([
            html.I(className="fa-solid fa-trash-can me-2 text-danger"), "Delete Finding",
        ])),
        dbc.ModalBody(id="home-delete-modal-body",
                      children="Are you sure you want to delete this finding?"),
        dbc.ModalFooter([
            dbc.Button([html.I(className="fa-solid fa-trash-can me-2"), "Delete"],
                       id="home-delete-confirm-btn", color="danger", size="sm"),
            dbc.Button("Cancel", id="home-delete-cancel-btn",
                       color="secondary", outline=True, size="sm", className="ms-2"),
        ]),
    ], id="home-delete-confirm-modal", is_open=False, size="sm", centered=True),
])


# ---------------------------------------------------------------------------
# Navigation — store-based routing avoids Databricks proxy URL issues
# ---------------------------------------------------------------------------
@callback(
    Output("active-page-store", "data", allow_duplicate=True),
    Input("nav-setup-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def nav_to_setup(n):
    if not n:
        return dash.no_update
    return "setup"


@callback(
    Output("active-page-store", "data",  allow_duplicate=True),
    Output("active-tab-store",  "data",  allow_duplicate=True),
    Input("nav-home-btn",       "n_clicks"),
    prevent_initial_call=True,
)
def nav_to_demo(n):
    if not n:
        return dash.no_update, dash.no_update
    return "demo", "tab-home"


@callback(
    Output("active-page-store", "data",  allow_duplicate=True),
    Output("active-tab-store",  "data",  allow_duplicate=True),
    Input("nav-icd10-btn",      "n_clicks"),
    Input("nav-caregap-btn",    "n_clicks"),
    prevent_initial_call=True,
)
def nav_to_tab(icd10_n, caregap_n):
    triggered = callback_context.triggered_id
    if triggered == "nav-icd10-btn" and icd10_n:
        return "demo", "tab-icd10"
    if triggered == "nav-caregap-btn" and caregap_n:
        return "demo", "tab-caregap"
    return dash.no_update, dash.no_update


# Update URL hash client-side — pure JS, no Dash Output conflicts
app.clientside_callback(
    """
    function(setup_n, home_n, icd10_n, caregap_n) {
        var ctx = window.dash_clientside.callback_context;
        if (!ctx || !ctx.triggered || !ctx.triggered.length) return '';
        var prop = ctx.triggered[0].prop_id;
        if (prop === 'nav-setup-btn.n_clicks' && setup_n > 0) {
            window.history.pushState({}, '', '#setup');
        } else {
            window.history.pushState({}, '', window.location.pathname);
        }
        return '';
    }
    """,
    Output("url-updater",    "children"),
    Input("nav-setup-btn",   "n_clicks"),
    Input("nav-home-btn",    "n_clicks"),
    Input("nav-icd10-btn",   "n_clicks"),
    Input("nav-caregap-btn", "n_clicks"),
    prevent_initial_call=True,
)


@callback(
    Output("main-demo-content",  "style"),
    Output("setup-page-content", "children"),
    Input("active-page-store",   "data"),
)
def route(page):
    if page == "setup":
        return {"display": "none"}, tab_setup.setup_shell()
    return {}, None


# ---------------------------------------------------------------------------
# Navbar icon — green check when all done
# ---------------------------------------------------------------------------
@callback(
    Output("navbar-setup-icon", "className"),
    Input("all-done-store",     "data"),
)
def update_navbar_icon(all_done):
    if all_done:
        return "fa-solid fa-circle-check me-1 text-success"
    return "fa-solid fa-gear me-1"


# ---------------------------------------------------------------------------
# Root setup check — fires 300ms after every root page load
#
# Fast path  (localStorage True):  1 SQL → load patients only
# First-time path:                  1 SQL → bootstrap_status, set flag if done
# No KA API on root — that detail is only needed on /setup
# ---------------------------------------------------------------------------
_JOB1_STEPS = {"create_catalog", "setup_care_gap_rules", "ingest_patient_data", "care_gap_vs_index"}
_ALL_STEPS  = {"create_catalog", "setup_care_gap_rules", "ingest_patient_data", "care_gap_vs_index",
               "load_icd10_pdfs", "ka_source_configured", "ka_source_sync"}


@callback(
    Output("all-done-store",       "data",  allow_duplicate=True),
    Output("patient-store",        "data",  allow_duplicate=True),
    Output("setup-complete-store", "data",  allow_duplicate=True),
    Input("root-setup-check",      "n_intervals"),
    State("setup-complete-store",  "data"),
    State("catalog-store",         "data"),
    State("schema-store",          "data"),
    prevent_initial_call=True,
)
def root_setup_check(n_intervals, setup_complete, catalog, schema):
    from db import load_patients, execute_sql
    cat = catalog or CATALOG
    sch = schema  or SCHEMA

    if setup_complete:
        try:
            patients = load_patients(cat, sch)
        except Exception:
            patients = []
        return True, patients, True

    try:
        rows = execute_sql(
            f"SELECT step FROM `{cat}`.`{sch}`.bootstrap_status WHERE status = 'COMPLETED'"
        )
        done = {r["step"] for r in rows}
    except Exception:
        return False, [], False

    job1_done = _JOB1_STEPS.issubset(done)
    all_done  = _ALL_STEPS.issubset(done)

    patients = []
    if job1_done:
        try:
            patients = load_patients(cat, sch)
        except Exception:
            pass

    return all_done, patients, all_done


# ---------------------------------------------------------------------------
# Tab routing
# ---------------------------------------------------------------------------
def _load_patients_direct(catalog, schema):
    from db import load_patients
    try:
        return load_patients(catalog or CATALOG, schema or SCHEMA)
    except Exception:
        return []


@callback(
    Output("tab-content",       "children"),
    Input("active-tab-store",   "data"),
    State("patient-store",      "data"),
    State("ka-endpoint-store",  "data"),
    State("all-done-store",     "data"),
    State("catalog-store",      "data"),
    State("schema-store",       "data"),
)
def render_tab(active_tab, patients, ka_endpoint, all_done, catalog, schema):
    if active_tab == "tab-home":
        return tab_home.home_shell()

    if active_tab == "tab-icd10":
        pts = patients or _load_patients_direct(catalog, schema)
        if not pts:
            return dbc.Container(dbc.Alert(
                [html.I(className="fa-solid fa-hourglass-half me-2"),
                 html.Strong("No patient records found. "),
                 "Click the ", html.Strong("⚙ Setup"), " icon in the navbar and run the Data Setup job."],
                color="warning", className="mt-3"
            ), fluid=True)
        return tab_icd10.icd10_layout(pts)

    if active_tab == "tab-caregap":
        pts = patients or _load_patients_direct(catalog, schema)
        if not pts:
            return dbc.Container(dbc.Alert(
                [html.I(className="fa-solid fa-hourglass-half me-2"),
                 html.Strong("No patient records found. "),
                 "Click the ", html.Strong("⚙ Setup"), " icon in the navbar and run the Data Setup job."],
                color="warning", className="mt-3"
            ), fluid=True)
        return tab_caregap.gap_layout(pts)

    return html.Div()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASH_PORT", "8050")), debug=False)
