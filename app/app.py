import os

import dash
from dash import dcc, html, callback, Input, Output, State, callback_context
import dash_bootstrap_components as dbc

from config import CATALOG, SCHEMA, BRAND_ORANGE

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
_HC_CSS = """
/* ═══════════════════════════════════════════════════════
   Clinical AI Demo — Professional Dark Healthcare Theme
═══════════════════════════════════════════════════════ */
:root {
  --hc-page-bg:    #091420;
  --hc-surface:    #0D1F30;
  --hc-surface-2:  #122840;
  --hc-border:     #1A3248;
  --hc-primary:    #4FC3F7;
  --hc-primary-dk: #0288D1;
  --hc-primary-bg: #0A1E30;
  --hc-teal:       #4DD0E1;
  --hc-teal-dk:    #00ACC1;
  --hc-teal-bg:    #091C24;
  --hc-green:      #69F0AE;
  --hc-green-dk:   #00C853;
  --hc-green-bg:   #061A10;
  --hc-amber:      #FFCA28;
  --hc-amber-bg:   #1A1200;
  --hc-red:        #FF5252;
  --hc-red-bg:     #1A0808;
  --hc-text:       #D6EAF8;
  --hc-muted:      #6E93AD;
  --hc-shadow:     0 4px 20px rgba(0,0,0,0.5);
  --hc-shadow-sm:  0 2px 10px rgba(0,0,0,0.35);
  --hc-radius:     10px;
}
/* ── Base ───────────────────────────────── */
body, html {
  background-color: var(--hc-page-bg) !important;
  color: var(--hc-text) !important;
  font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif !important;
}
/* ── Navbar ─────────────────────────────── */
.navbar {
  background: linear-gradient(135deg, #030D18 0%, #061420 60%, #091E2E 100%) !important;
  box-shadow: 0 1px 0 var(--hc-border), 0 4px 20px rgba(0,0,0,0.6) !important;
  border-bottom: 1px solid var(--hc-border) !important;
  padding-top: 0.6rem !important;
  padding-bottom: 0.6rem !important;
}
.navbar .navbar-brand, .navbar button, .navbar span { color: rgba(255,255,255,0.9) !important; }
/* ── Nav buttons — active/inactive ──────── */
.nav-btn-active, .nav-btn-inactive {
  border: none !important;
  text-decoration: none !important;
  display: flex !important;
  align-items: center !important;
  gap: 5px !important;
  padding: 6px 13px !important;
  border-radius: 8px !important;
  transition: background 0.18s ease, opacity 0.18s ease;
  font-size: 0.875rem;
  color: #fff !important;
}
.nav-btn-active {
  background: rgba(79,195,247,0.18) !important;
  border-bottom: 2px solid var(--hc-primary) !important;
  border-radius: 8px 8px 0 0 !important;
  opacity: 1 !important;
  font-weight: 600 !important;
  color: var(--hc-primary) !important;
}
.nav-btn-active .fa-solid { color: var(--hc-primary) !important; }
.nav-btn-inactive {
  background: transparent !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 8px 8px 0 0 !important;
  opacity: 0.7 !important;
}
.nav-btn-inactive:hover {
  background: rgba(255,255,255,0.08) !important;
  opacity: 1 !important;
}
/* ── Cards ──────────────────────────────── */
.card {
  background: var(--hc-surface) !important;
  border: 1px solid var(--hc-border) !important;
  border-radius: var(--hc-radius) !important;
  box-shadow: var(--hc-shadow-sm) !important;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.card:hover { box-shadow: var(--hc-shadow) !important; border-color: #234060 !important; }
.card-header {
  background: var(--hc-surface-2) !important;
  border-bottom: 1px solid var(--hc-border) !important;
  border-radius: var(--hc-radius) var(--hc-radius) 0 0 !important;
  color: var(--hc-text) !important;
}
.card-body { color: var(--hc-text) !important; }
/* ── Buttons ────────────────────────────── */
.btn { border-radius: 6px !important; font-weight: 500 !important; }
.btn-primary {
  background: var(--hc-primary-dk) !important; border-color: var(--hc-primary-dk) !important;
  color: #fff !important;
}
.btn-primary:hover {
  background: #039BE5 !important; border-color: #039BE5 !important;
  box-shadow: 0 0 16px rgba(79,195,247,0.4) !important;
}
.btn-success {
  background: var(--hc-teal-dk) !important; border-color: var(--hc-teal-dk) !important;
  color: #fff !important;
}
.btn-success:hover {
  background: #00BCD4 !important; border-color: #00BCD4 !important;
  box-shadow: 0 0 16px rgba(77,208,225,0.4) !important;
}
.btn-outline-secondary {
  border-color: var(--hc-border) !important; color: var(--hc-muted) !important;
  background: transparent !important;
}
.btn-outline-secondary:hover {
  background: var(--hc-surface-2) !important; color: var(--hc-text) !important;
  border-color: var(--hc-primary) !important;
}
.btn-danger { background: #B71C1C !important; border-color: #B71C1C !important; }
.btn-link   { color: var(--hc-primary) !important; }
/* ── Tables ─────────────────────────────── */
.table { color: var(--hc-text) !important; }
.table thead th, .table-dark th {
  background: var(--hc-surface-2) !important;
  color: var(--hc-primary) !important;
  font-weight: 600 !important;
  border-color: var(--hc-border) !important;
  letter-spacing: 0.3px !important;
}
.table tbody tr { border-color: var(--hc-border) !important; }
.table tbody tr td { border-color: var(--hc-border) !important; color: var(--hc-text) !important; }
.table tbody tr:hover td { background-color: var(--hc-primary-bg) !important; }
/* ── Accordion ──────────────────────────── */
.accordion-item {
  background: var(--hc-surface) !important;
  border: 1px solid var(--hc-border) !important;
  border-radius: 8px !important; margin-bottom: 5px !important; overflow: hidden;
}
.accordion-button {
  background: var(--hc-surface) !important;
  color: var(--hc-text) !important; font-weight: 500 !important;
}
.accordion-button:not(.collapsed) {
  background: var(--hc-primary-bg) !important;
  color: var(--hc-primary) !important;
  border-bottom: 1px solid var(--hc-border);
  box-shadow: none !important;
}
.accordion-button::after { filter: invert(1) brightness(0.7); }
.accordion-body { background: var(--hc-surface) !important; color: var(--hc-text) !important; }
/* ── Alerts ─────────────────────────────── */
.alert { border-radius: 8px !important; font-size: 0.875rem !important; }
.alert-warning  { background: var(--hc-amber-bg) !important; border-color: #5A3E00 !important; color: var(--hc-amber) !important; }
.alert-success  { background: var(--hc-green-bg) !important; border-color: #1A4A2A !important; color: var(--hc-green) !important; }
.alert-info     { background: var(--hc-primary-bg) !important; border-color: #1A3A5A !important; color: var(--hc-primary) !important; }
.alert-danger   { background: var(--hc-red-bg) !important; border-color: #4A1010 !important; color: var(--hc-red) !important; }
.alert-secondary{ background: var(--hc-surface-2) !important; border-color: var(--hc-border) !important; color: var(--hc-muted) !important; }
/* ── Inputs ─────────────────────────────── */
.form-control, .form-select, textarea {
  background: var(--hc-surface-2) !important;
  border: 1px solid var(--hc-border) !important;
  border-radius: 6px !important;
  color: var(--hc-text) !important;
}
.form-control:focus, .form-select:focus, textarea:focus {
  border-color: var(--hc-primary) !important;
  box-shadow: 0 0 0 3px rgba(79,195,247,0.2) !important;
  background: var(--hc-surface-2) !important;
  color: var(--hc-text) !important;
}
.form-control::placeholder, textarea::placeholder { color: var(--hc-muted) !important; }
/* ── Dropdowns (Dash) ───────────────────── */
.Select-control, .Select-menu-outer {
  background: var(--hc-surface-2) !important;
  border-color: var(--hc-border) !important;
  color: var(--hc-text) !important;
}
.Select-value-label, .Select-placeholder { color: var(--hc-text) !important; }
.Select-option { background: var(--hc-surface-2) !important; color: var(--hc-text) !important; }
.Select-option.is-focused { background: var(--hc-primary-bg) !important; }
/* ── Modals ─────────────────────────────── */
.modal-content {
  background: var(--hc-surface) !important;
  border: 1px solid var(--hc-border) !important;
  border-radius: 12px !important;
  box-shadow: 0 12px 48px rgba(0,0,0,0.7) !important;
}
.modal-header {
  background: var(--hc-surface-2) !important;
  border-bottom: 1px solid var(--hc-border) !important;
  border-radius: 12px 12px 0 0 !important;
  color: var(--hc-text) !important;
}
.modal-body    { background: var(--hc-surface) !important; color: var(--hc-text) !important; }
.modal-footer  { background: var(--hc-surface) !important; border-top: 1px solid var(--hc-border) !important; }
/* ── Progress ───────────────────────────── */
.progress { border-radius: 6px !important; background: var(--hc-surface-2) !important; }
.progress-bar { border-radius: 6px !important; }
.progress-bar.bg-primary { background: var(--hc-primary-dk) !important; }
.progress-bar.bg-success { background: var(--hc-teal-dk) !important; }
.progress-bar.bg-warning { background: #FF8F00 !important; }
/* ── Toast ──────────────────────────────── */
.toast {
  background: var(--hc-surface) !important;
  border: 1px solid var(--hc-border) !important;
  border-radius: 10px !important;
  box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
  color: var(--hc-text) !important;
}
.toast-header {
  background: var(--hc-surface-2) !important;
  color: var(--hc-primary) !important;
  border-bottom: 1px solid var(--hc-border) !important;
  border-radius: 10px 10px 0 0 !important;
}
/* ── Badges ─────────────────────────────── */
.badge { font-weight: 600 !important; }
.badge.bg-light { background: var(--hc-surface-2) !important; color: var(--hc-text) !important; }
/* ── Text colors ────────────────────────── */
.text-muted, small.text-muted { color: var(--hc-muted) !important; }
.text-dark { color: var(--hc-text) !important; }
.fw-bold, .fw-semibold { color: var(--hc-text) !important; }
h5.fw-bold { color: var(--hc-primary) !important; }
/* ── Labels / small text ────────────────── */
label, .form-label, .small { color: var(--hc-text) !important; }
code { background: var(--hc-surface-2) !important; color: var(--hc-primary) !important; border-radius: 4px !important; padding: 1px 5px !important; }
/* ── Stat tiles ─────────────────────────── */
.card-body h4 { font-size: 2.1rem !important; font-weight: 700 !important; line-height: 1.1 !important; }
.text-primary { color: var(--hc-primary) !important; }
.text-info    { color: var(--hc-teal) !important; }
.text-success { color: var(--hc-green) !important; }
.text-danger  { color: var(--hc-red) !important; }
.text-warning { color: var(--hc-amber) !important; }
.text-secondary { color: var(--hc-muted) !important; }
/* ── Border colours ─────────────────────── */
.border-bottom, .border-top, .border-start, .border-end, .border { border-color: var(--hc-border) !important; }
/* ── Scrollbar ──────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--hc-page-bg); }
::-webkit-scrollbar-thumb { background: #1E3A52; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--hc-primary-dk); }
/* ── HR ─────────────────────────────────── */
hr { border-color: var(--hc-border) !important; opacity: 0.6; }
/* ── Shadow util ────────────────────────── */
.shadow-sm  { box-shadow: var(--hc-shadow-sm) !important; }
.h-100      { height: 100% !important; }
/* ── Navbar glow on active ──────────────── */
.navbar button.active, .navbar button:active { color: var(--hc-primary) !important; }
/* ── Home patient accordion ─────────────── */
#home-patient-accordion .accordion-item {
  border: 1px solid #1E4A7A !important;
  border-left: 3px solid var(--hc-primary) !important;
  border-radius: 8px !important;
  margin-bottom: 7px !important;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
#home-patient-accordion .accordion-item:hover {
  border-color: var(--hc-primary) !important;
  border-left-color: var(--hc-teal) !important;
  box-shadow: 0 0 0 1px rgba(79,195,247,0.15),
              0 4px 16px rgba(0,0,0,0.4) !important;
}
#home-patient-accordion .accordion-item:has(.accordion-button:not(.collapsed)) {
  border-left-color: var(--hc-teal) !important;
  border-color: var(--hc-teal) !important;
  box-shadow: 0 0 0 1px rgba(77,208,225,0.2),
              0 4px 20px rgba(0,0,0,0.45) !important;
}
/* ── Genie floating panel ───────────────── */
#genie-toggle-btn {
  position: fixed; right: 0; top: 50%;
  transform: translateY(-50%);
  z-index: 1050;
  border-radius: 10px 0 0 10px !important;
  background: linear-gradient(160deg, #0288D1, #00ACC1) !important;
  color: #fff !important; border: none !important;
  padding: 14px 9px !important;
  box-shadow: -3px 0 16px rgba(0,0,0,0.5) !important;
  transition: padding-right 0.2s, box-shadow 0.2s;
  writing-mode: vertical-rl; text-orientation: mixed;
  font-size: 18px; cursor: pointer;
}
#genie-toggle-btn:hover {
  padding-right: 14px !important;
  box-shadow: -4px 0 22px rgba(2,136,209,0.5) !important;
}
#genie-panel {
  position: fixed; right: 0; top: 70px;
  height: calc(100vh - 90px); width: 50vw;
  z-index: 1040;
  background: var(--hc-surface);
  border-left: 1px solid var(--hc-border);
  box-shadow: -4px 0 32px rgba(0,0,0,0.6);
  display: flex; flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.28s cubic-bezier(0.4,0,0.2,1);
}
#genie-panel.open { transform: translateX(0); }
.genie-typing-dots span {
  display: inline-block; color: var(--hc-primary);
  font-size: 18px; margin: 0 2px;
  animation: genie-bounce 1s infinite;
}
@keyframes genie-bounce {
  0%,80%,100% { transform: translateY(0); opacity:0.4; }
  40%         { transform: translateY(-6px); opacity:1; }
}
/* ── ICD-10 results table — dark theme contrast ── */
.table-sm td, .table-sm th {
  background-color: transparent !important;
  color: var(--hc-text) !important;
  border-color: var(--hc-border) !important;
}
.table-sm td code {
  background: var(--hc-surface-2) !important;
  color: var(--hc-primary) !important;
  border-radius: 4px !important;
  padding: 2px 6px !important;
  font-weight: 700 !important;
}
.table-sm small, .table-sm .small {
  color: var(--hc-text) !important;
}
/* ── Setup page — inline style overrides ── */
.hc-prereq-bg {
  background: var(--hc-surface-2) !important;
  border: 1px solid var(--hc-border) !important;
}
.hc-prereq-border { border-color: var(--hc-border) !important; }
.hc-card-header-config {
  background: var(--hc-surface-2) !important;
  border-bottom: 2px solid var(--hc-muted) !important;
}
"""

app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
        "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css",
    ],
    suppress_callback_exceptions=True,
)
app.index_string = f"""<!DOCTYPE html>
<html>
  <head>
    {{%metas%}}
    <title>Clinical AI Demo</title>
    {{%favicon%}}
    {{%css%}}
    <style>{_HC_CSS}</style>
  </head>
  <body>
    {{%app_entry%}}
    <footer>
      {{%config%}}
      {{%scripts%}}
      {{%renderer%}}
    </footer>
  </body>
</html>
"""
server = app.server

import tab_home             # noqa: E402
import tab_icd10            # noqa: E402
import tab_caregap          # noqa: E402
import tab_setup            # noqa: E402
import tab_genie            # noqa: E402
import tab_knowledge_graph  # noqa: E402
import tab_permissions      # noqa: E402

# ---------------------------------------------------------------------------
# Navbar
# ---------------------------------------------------------------------------
NAVBAR = dbc.Navbar(
    dbc.Container([
        dbc.NavbarBrand(
            [html.I(className="fa-solid fa-heart-pulse me-2",
                    style={"color": "#4FC3F7"}),   # bright clinical blue on dark navbar
             html.Span("Clinical AI Demo", style={"letterSpacing": "-0.3px"})],
            className="fw-bold fs-5 text-white"
        ),
        html.Div([
            html.Button(
                [html.I(className="fa-solid fa-house fa-xl"),
                 html.Span("Home")],
                id="nav-home-btn", n_clicks=0,
                className="nav-btn-active",   # home active by default
            ),
            html.Button(
                [html.I(className="fa-solid fa-file-medical fa-xl"),
                 html.Span("ICD-10")],
                id="nav-icd10-btn", n_clicks=0,
                className="nav-btn-inactive",
            ),
            html.Button(
                [html.I(className="fa-solid fa-stethoscope fa-xl"),
                 html.Span("Care Gap")],
                id="nav-caregap-btn", n_clicks=0,
                className="nav-btn-inactive",
            ),
            html.Button(
                [html.I(id="navbar-setup-icon", className="fa-solid fa-gear fa-xl"),
                 html.Span("Setup")],
                id="nav-setup-btn", n_clicks=0,
                className="nav-btn-inactive",
            ),
            html.Button(
                [html.I(className="fa-solid fa-diagram-project fa-xl"),
                 html.Span("Graph")],
                id="nav-kg-btn", n_clicks=0,
                className="nav-btn-inactive",
            ),
            html.Button(
                [html.I(className="fa-solid fa-shield-halved fa-xl"),
                 html.Span("Access")],
                id="nav-perms-btn", n_clicks=0,
                className="nav-btn-inactive",
            ),
        ], className="ms-auto d-flex align-items-center gap-1"),
    ], fluid=True),
    dark=True, className="mb-0"
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

    # ── Genie floating toggle button ─────────────────────────────────────────
    html.Button(
        html.I(className="fa-solid fa-comments"),
        id="genie-toggle-btn", n_clicks=0,
        title="Patient Data Assistant",
    ),

    # ── Genie side panel ─────────────────────────────────────────────────────
    html.Div(
        id="genie-panel",
        children=tab_genie.genie_panel(),
        className="",   # "open" class added/removed by callback
    ),

    # Genie open/closed state
    dcc.Store(id="genie-open-store", data=False),

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
    Input("nav-kg-btn",         "n_clicks"),
    Input("nav-perms-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def nav_to_tab(icd10_n, caregap_n, kg_n, perms_n):
    triggered = callback_context.triggered_id
    if triggered == "nav-icd10-btn"   and icd10_n:  return "demo", "tab-icd10"
    if triggered == "nav-caregap-btn" and caregap_n: return "demo", "tab-caregap"
    if triggered == "nav-kg-btn"      and kg_n:     return "demo", "tab-kg"
    if triggered == "nav-perms-btn"   and perms_n:  return "demo", "tab-perms"
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
    Input("nav-setup-btn",    "n_clicks"),
    Input("nav-home-btn",     "n_clicks"),
    Input("nav-icd10-btn",    "n_clicks"),
    Input("nav-caregap-btn",  "n_clicks"),
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
        return "fa-solid fa-circle-check fa-xl text-success"
    return "fa-solid fa-gear fa-xl"


@callback(
    Output("nav-home-btn",    "className"),
    Output("nav-icd10-btn",   "className"),
    Output("nav-caregap-btn", "className"),
    Output("nav-setup-btn",   "className"),
    Output("nav-kg-btn",      "className"),
    Output("nav-perms-btn",   "className"),
    Input("active-tab-store", "data"),
    Input("active-page-store","data"),
)
def update_nav_active(active_tab, active_page):
    a = "nav-btn-active"
    i = "nav-btn-inactive"
    on_setup = active_page == "setup"
    return (
        a if (active_tab == "tab-home"    and not on_setup) else i,
        a if (active_tab == "tab-icd10"   and not on_setup) else i,
        a if (active_tab == "tab-caregap" and not on_setup) else i,
        a if on_setup else i,
        a if (active_tab == "tab-kg"      and not on_setup) else i,
        a if (active_tab == "tab-perms"   and not on_setup) else i,
    )


# ---------------------------------------------------------------------------
# Root setup check — fires 300ms after every root page load
#
# Fast path  (localStorage True):  1 SQL → load patients only
# First-time path:                  1 SQL → bootstrap_status, set flag if done
# No KA API on root — that detail is only needed on /setup
# ---------------------------------------------------------------------------
_JOB1_STEPS = {"setup_care_gap_rules", "ingest_patient_data", "care_gap_vs_index", "genie_configured"}
_ALL_STEPS  = {"setup_care_gap_rules", "ingest_patient_data", "care_gap_vs_index", "genie_configured",
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
    State("patient-store",           "data"),
    State("ka-endpoint-store",       "data"),
    State("all-done-store",          "data"),
    State("catalog-store",           "data"),
    State("schema-store",            "data"),
    # ICD-10 persisted state
    State("icd10-patient-store",     "data"),
    State("icd10-codes-store",       "data"),
    State("icd10-saved-codes-store", "data"),
    # Care Gap persisted state
    State("gap-patient-id-store",    "data"),
    State("gap-results-store",       "data"),
    State("saved-findings-store",    "data"),
)
def render_tab(active_tab, patients, ka_endpoint, all_done, catalog, schema,
               icd10_patient, icd10_codes, icd10_saved,
               gap_patient, gap_results, gap_saved_findings):
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
        return tab_icd10.icd10_layout(pts,
            restore_patient=icd10_patient,
            restore_codes=icd10_codes,
            restore_saved=icd10_saved)

    if active_tab == "tab-caregap":
        pts = patients or _load_patients_direct(catalog, schema)
        if not pts:
            return dbc.Container(dbc.Alert(
                [html.I(className="fa-solid fa-hourglass-half me-2"),
                 html.Strong("No patient records found. "),
                 "Click the ", html.Strong("⚙ Setup"), " icon in the navbar and run the Data Setup job."],
                color="warning", className="mt-3"
            ), fluid=True)
        return tab_caregap.gap_layout(pts,
            restore_patient=gap_patient,
            restore_gaps=gap_results,
            restore_saved_findings=gap_saved_findings)

    if active_tab == "tab-kg":
        return tab_knowledge_graph.kg_layout()

    if active_tab == "tab-perms":
        return tab_permissions.perms_layout()

    return html.Div()


# ---------------------------------------------------------------------------
# Genie panel open / close
# ---------------------------------------------------------------------------
@callback(
    Output("genie-open-store", "data"),
    Input("genie-toggle-btn",  "n_clicks"),
    Input("genie-close-btn",   "n_clicks"),
    State("genie-open-store",  "data"),
    prevent_initial_call=True,
)
def toggle_genie(toggle_n, close_n, is_open):
    triggered = dash.callback_context.triggered_id
    if triggered == "genie-close-btn":
        return False
    return not is_open


@callback(
    Output("genie-panel", "className"),
    Input("genie-open-store", "data"),
)
def update_genie_panel_class(is_open):
    return "open" if is_open else ""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASH_PORT", "8050")), debug=False)
