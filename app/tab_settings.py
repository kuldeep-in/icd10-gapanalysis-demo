from dash import html
import dash_bootstrap_components as dbc

from config import (
    CATALOG, SCHEMA, WAREHOUSE_ID,
    KA_ENDPOINT_NAME, FMAPI_ENDPOINT,
    DATA_SETUP_JOB_NAME, AI_SETUP_JOB_NAME,
    _app_sp_name,
)


def settings_layout() -> dbc.Container:
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
            dbc.CardHeader([html.I(className="fa-solid fa-database me-2"), html.Strong("Unity Catalog")]),
            dbc.CardBody([
                _row("Catalog", CATALOG or "—", "fa-layer-group"),
                _row("Schema",  SCHEMA  or "—", "fa-table"),
            ]),
        ], className="mb-3"),

        dbc.Card([
            dbc.CardHeader([html.I(className="fa-solid fa-server me-2"), html.Strong("Infrastructure")]),
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
            dbc.CardHeader([html.I(className="fa-solid fa-robot me-2"), html.Strong("AI Configuration")]),
            dbc.CardBody([
                _row("Care Gap Model",       FMAPI_ENDPOINT,                   "fa-microchip"),
                _row("KA Serving Endpoint", KA_ENDPOINT_NAME   or "Not set",  "fa-robot",
                     warn=not KA_ENDPOINT_NAME),
                _row("Data Setup Job Name", DATA_SETUP_JOB_NAME or "—",       "fa-play"),
                _row("AI Setup Job Name",   AI_SETUP_JOB_NAME   or "—",       "fa-play"),
            ]),
        ], className="mb-3"),

        dbc.Card([
            dbc.CardHeader([html.I(className="fa-solid fa-id-badge me-2"), html.Strong("App Identity")]),
            dbc.CardBody(
                _row("Service Principal", _app_sp_name or "Not resolved", "fa-user-gear",
                     warn=not _app_sp_name)
            ),
        ], className="mb-3"),

    ], fluid=True, className="pt-2")
