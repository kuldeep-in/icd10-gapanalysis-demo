import os

import dash
from dash import dcc, html, callback, Input, Output, State
import dash_bootstrap_components as dbc

from config import CATALOG, SCHEMA, BRAND_ORANGE

# ---------------------------------------------------------------------------
# App init — must come before tab module imports so @callback decorators
# register against an existing Dash instance.
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

# Import tab modules to register their callbacks
import tab_home      # noqa: E402
import tab_icd10     # noqa: E402
import tab_caregap   # noqa: E402
import tab_setup     # noqa: E402
import tab_settings  # noqa: E402

# ---------------------------------------------------------------------------
# Shared layout
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
            dbc.Tab(label="Setup",             tab_id="tab-setup"),
            dbc.Tab(label="Settings",          tab_id="tab-settings"),
        ]
    ),
    html.Div(id="tab-content", className="p-3"),

    # Shared setup/status stores
    dcc.Store(id="catalog-store",           data=CATALOG),
    dcc.Store(id="schema-store",            data=SCHEMA),
    dcc.Store(id="ka-endpoint-store",       data=""),
    dcc.Store(id="patient-store",           data=[]),
    dcc.Store(id="all-done-store",          data=False),
    dcc.Store(id="job1-action-store",       data={}),
    dcc.Store(id="job2-action-store",       data={}),
    dcc.Store(id="step-cache-store",        data={}),
    dcc.Store(id="job-ids-store",           data={}),
    dcc.Store(id="accordion1-active-store", data=None),
    dcc.Store(id="accordion2-active-store", data=None),

    # ICD-10 Analyzer stores
    dcc.Store(id="icd10-patient-store",     data=""),
    dcc.Store(id="icd10-codes-store",       data=[]),
    dcc.Store(id="icd10-saved-codes-store", data=[]),

    # Care Gap Advisor stores
    dcc.Store(id="gap-results-store",       data=[]),
    dcc.Store(id="gap-patient-id-store",    data=""),
    dcc.Store(id="saved-findings-store",    data=[]),
    dcc.Store(id="delete-target-store",     data=None),

    # Home overview stores
    dcc.Store(id="home-patients-store",     data=[]),
    dcc.Store(id="home-all-findings-store", data={}),
    dcc.Store(id="home-all-icd10-store",    data={}),
    dcc.Store(id="home-delete-target-store",data=None),

    # Care Gap Advisor delete modal
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle([
            html.I(className="fa-solid fa-trash-can me-2 text-danger"),
            "Delete Finding",
        ])),
        dbc.ModalBody(id="delete-modal-body",
                      children="Are you sure you want to delete this finding?"),
        dbc.ModalFooter([
            dbc.Button(
                [html.I(className="fa-solid fa-trash-can me-2"), "Delete"],
                id="delete-confirm-btn", color="danger", size="sm",
            ),
            dbc.Button(
                "Cancel", id="delete-cancel-btn",
                color="secondary", outline=True, size="sm", className="ms-2",
            ),
        ]),
    ], id="delete-confirm-modal", is_open=False, size="sm", centered=True),

    # Home overview delete modal
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle([
            html.I(className="fa-solid fa-trash-can me-2 text-danger"),
            "Delete Finding",
        ])),
        dbc.ModalBody(id="home-delete-modal-body",
                      children="Are you sure you want to delete this finding?"),
        dbc.ModalFooter([
            dbc.Button(
                [html.I(className="fa-solid fa-trash-can me-2"), "Delete"],
                id="home-delete-confirm-btn", color="danger", size="sm",
            ),
            dbc.Button(
                "Cancel", id="home-delete-cancel-btn",
                color="secondary", outline=True, size="sm", className="ms-2",
            ),
        ]),
    ], id="home-delete-confirm-modal", is_open=False, size="sm", centered=True),
])


# ---------------------------------------------------------------------------
# Tab routing
# ---------------------------------------------------------------------------
def _load_patients_direct(catalog, schema):
    """Load patients from DB directly — fallback when patient-store is not yet populated."""
    from db import load_patients
    try:
        return load_patients(catalog or CATALOG, schema or SCHEMA)
    except Exception:
        return []


@callback(
    Output("tab-content",       "children"),
    Input("main-tabs",          "active_tab"),
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
                 "Run the Data Setup job from the ",
                 html.Strong("Setup"), " tab."],
                color="warning", className="mt-3"
            ), fluid=True)
        return tab_icd10.icd10_layout(pts)

    if active_tab == "tab-caregap":
        pts = patients or _load_patients_direct(catalog, schema)
        if not pts:
            return dbc.Container(dbc.Alert(
                [html.I(className="fa-solid fa-hourglass-half me-2"),
                 html.Strong("No patient records found. "),
                 "Run the Data Setup job from the ",
                 html.Strong("Setup"), " tab."],
                color="warning", className="mt-3"
            ), fluid=True)
        return tab_caregap.gap_layout(pts)

    if active_tab == "tab-setup":
        return tab_setup.setup_shell()

    if active_tab == "tab-settings":
        return tab_settings.settings_layout()

    return html.Div()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASH_PORT", "8050")), debug=False)
