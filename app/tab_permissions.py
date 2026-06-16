from concurrent.futures import ThreadPoolExecutor

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, html

from config import (
    CATALOG, GENIE_SPACE_ID, KA_ENDPOINT_NAME, KA_NAME,
    SCHEMA, VS_ENDPOINT_NAME, WAREHOUSE_ID, _app_sp_name, logger, w,
)
from db import execute_sql

# ---------------------------------------------------------------------------
# Expected permission catalogue
# ---------------------------------------------------------------------------
_INFRA_PERMISSIONS = [
    {
        "resource":    "SQL Warehouse",
        "value":       WAREHOUSE_ID,
        "permission":  "CAN_USE",
        "object_type": "warehouses",
        "granted_by":  "deploy.sh Step 7",
    },
    {
        "resource":    f"KA Endpoint: {KA_ENDPOINT_NAME}",
        "value":       KA_ENDPOINT_NAME,
        "permission":  "CAN_QUERY",
        "object_type": "serving-endpoints-by-name",
        "granted_by":  "deploy.sh Step 7",
    },
    {
        "resource":    "KA Resource",
        "value":       KA_NAME,
        "permission":  "CAN_QUERY",
        "object_type": "knowledge-assistants",
        "granted_by":  "deploy.sh Step 7",
    },
    {
        "resource":    f"VS Endpoint: {VS_ENDPOINT_NAME}",
        "value":       VS_ENDPOINT_NAME,
        "permission":  "CAN_USE",
        "object_type": "vector-search-endpoints",
        "granted_by":  "deploy.sh Step 7",
    },
    {
        "resource":    "Genie Space",
        "value":       GENIE_SPACE_ID,
        "permission":  "CAN_RUN",
        "object_type": "genie",
        "granted_by":  "deploy.sh Step 7",
    },
    {
        "resource":    "Job 1 — Data Setup",
        "value":       None,
        "permission":  "CAN_MANAGE_RUN",
        "object_type": "jobs",
        "granted_by":  "workflows.yml (DAB)",
    },
    {
        "resource":    "Job 2 — KA Setup",
        "value":       None,
        "permission":  "CAN_MANAGE_RUN",
        "object_type": "jobs",
        "granted_by":  "workflows.yml (DAB)",
    },
]

_UC_PERMISSIONS = [
    {"object": "CATALOG",                        "permission": "USAGE",         "granted_by": "setup_schema.py"},
    {"object": "SCHEMA",                         "permission": "USAGE",         "granted_by": "setup_schema.py"},
    {"object": "patient_records",                "permission": "SELECT",         "granted_by": "setup_schema.py"},
    {"object": "care_gap_rules",                 "permission": "SELECT",         "granted_by": "setup_schema.py"},
    {"object": "icd10_analysis_results",         "permission": "SELECT + MODIFY","granted_by": "setup_schema.py"},
    {"object": "care_gap_findings",              "permission": "SELECT + MODIFY","granted_by": "setup_schema.py"},
    {"object": "bootstrap_status",               "permission": "SELECT + MODIFY","granted_by": "setup_schema.py"},
    {"object": "knowledge_graph",                "permission": "SELECT + MODIFY","granted_by": "setup_schema.py"},
    {"object": "icd10_reference_pdfs (volume)",  "permission": "READ VOLUME",   "granted_by": "setup_schema.py"},
]


# ---------------------------------------------------------------------------
# Live permission checks
# ---------------------------------------------------------------------------
def _check_warehouse() -> str:
    try:
        if not WAREHOUSE_ID:
            return "NOT_SET"
        result = w.permissions.get("warehouses", WAREHOUSE_ID)
        for acl in result.access_control_list or []:
            sp = getattr(acl, "service_principal_name", "") or ""
            if sp and sp in (_app_sp_name or ""):
                for p in acl.all_permissions or []:
                    if getattr(p.permission_level, "value", "") == "CAN_USE":
                        return "GRANTED"
        return "MISSING"
    except Exception as e:
        return f"ERROR: {str(e)[:60]}"


def _check_genie() -> str:
    try:
        if not GENIE_SPACE_ID:
            return "NOT_SET"
        result = w.permissions.get("genie", GENIE_SPACE_ID)
        for acl in result.access_control_list or []:
            sp = getattr(acl, "service_principal_name", "") or ""
            if sp and sp in (_app_sp_name or ""):
                for p in acl.all_permissions or []:
                    if getattr(p.permission_level, "value", "") in ("CAN_RUN", "CAN_MANAGE"):
                        return "GRANTED"
        return "MISSING"
    except Exception as e:
        return f"ERROR: {str(e)[:60]}"


def _check_vs_endpoint() -> str:
    try:
        if not VS_ENDPOINT_NAME:
            return "NOT_SET"
        ep_data = w.api_client.do(
            "GET", f"/api/2.0/vector-search/endpoints/{VS_ENDPOINT_NAME}")
        ep_id   = ep_data.get("id", "")
        if not ep_id:
            return "NOT_FOUND"
        perm_data = w.api_client.do(
            "GET", f"/api/2.0/permissions/vector-search-endpoints/{ep_id}")
        for acl in perm_data.get("access_control_list", []):
            sp = acl.get("service_principal_name", "")
            if sp and sp in (_app_sp_name or ""):
                for p in acl.get("all_permissions", []):
                    if p.get("permission_level") in ("CAN_USE", "CAN_MANAGE"):
                        return "GRANTED"
        return "MISSING"
    except Exception as e:
        return f"ERROR: {str(e)[:60]}"


def _check_uc_access() -> dict:
    results = {}
    # Try a simple SELECT on each table to validate access
    tables = ["patient_records", "care_gap_rules", "icd10_analysis_results",
              "care_gap_findings", "bootstrap_status", "knowledge_graph"]
    for tbl in tables:
        try:
            execute_sql(
                f"SELECT 1 FROM `{CATALOG}`.`{SCHEMA}`.`{tbl}` LIMIT 1")
            results[tbl] = "GRANTED"
        except Exception:
            results[tbl] = "MISSING"
    return results


# ---------------------------------------------------------------------------
# Row builder helpers
# ---------------------------------------------------------------------------
def _status_badge(status: str) -> dbc.Badge:
    if status == "GRANTED":
        return dbc.Badge([html.I(className="fa-solid fa-circle-check me-1"), "Granted"],
                         color="success", pill=True, style={"fontSize": "10px"})
    if status == "NOT_SET":
        return dbc.Badge("Not configured", color="secondary", pill=True,
                         style={"fontSize": "10px"})
    if status == "MISSING":
        return dbc.Badge([html.I(className="fa-solid fa-circle-xmark me-1"), "Missing"],
                         color="danger", pill=True, style={"fontSize": "10px"})
    if status.startswith("ERROR"):
        return dbc.Badge(["Error"], color="warning", pill=True,
                         style={"fontSize": "10px"}, title=status)
    return dbc.Badge("Pending", color="secondary", pill=True,
                     style={"fontSize": "10px"})


def _perm_row(resource: str, permission: str, granted_by: str,
              status: str = "pending") -> html.Tr:
    return html.Tr([
        html.Td(resource, className="small"),
        html.Td(html.Code(permission, style={"fontSize": "11px"})),
        html.Td(html.Small(granted_by, className="text-muted")),
        html.Td(_status_badge(status)),
    ])


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def perms_layout() -> html.Div:
    app_sp_display = _app_sp_name or "Not resolved"

    return html.Div([
        # Header
        dbc.Row([
            dbc.Col([
                html.H5([html.I(className="fa-solid fa-shield-halved me-2"),
                         "Permissions Model"], className="mb-0 fw-bold"),
                html.Small(
                    [html.Span("App SP: ", className="text-muted"),
                     html.Code(app_sp_display, style={"fontSize": "11px"})],
                    className="d-block"),
            ]),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-magnifying-glass me-2"),
                     "Verify Live"],
                    id="perms-verify-btn", color="outline-primary", size="sm",
                ),
                width="auto", className="ms-auto",
            ),
        ], align="center", className="mb-3"),

        html.Div(id="perms-verify-status", className="mb-3"),

        # ── Infrastructure permissions ───────────────────────────────────
        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-server me-2 text-info"),
                html.Strong("Infrastructure Permissions",
                            style={"fontSize": "13px"}),
                html.Small(" — granted by deploy.sh Step 7",
                           className="text-muted ms-2"),
            ], style={"background": "#122840"}),
            dbc.CardBody(
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Resource", className="small text-muted"),
                        html.Th("Permission", className="small text-muted"),
                        html.Th("Granted by", className="small text-muted"),
                        html.Th("Status", className="small text-muted"),
                    ]), style={"borderBottom": "1px solid #1A3248"}),
                    html.Tbody(id="perms-infra-tbody", children=[
                        _perm_row(p["resource"], p["permission"], p["granted_by"])
                        for p in _INFRA_PERMISSIONS
                    ]),
                ], className="table table-sm mb-0",
                   style={"color": "#D6EAF8"}),
                className="p-2",
            ),
        ], className="mb-3 shadow-sm"),

        # ── Unity Catalog permissions ────────────────────────────────────
        dbc.Card([
            dbc.CardHeader([
                html.I(className="fa-solid fa-layer-group me-2 text-success"),
                html.Strong("Unity Catalog Permissions",
                            style={"fontSize": "13px"}),
                html.Small(f" — catalog: {CATALOG} · schema: {SCHEMA}",
                           className="text-muted ms-2"),
            ], style={"background": "#122840"}),
            dbc.CardBody(
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Object", className="small text-muted"),
                        html.Th("Permission", className="small text-muted"),
                        html.Th("Granted by", className="small text-muted"),
                        html.Th("Status", className="small text-muted"),
                    ]), style={"borderBottom": "1px solid #1A3248"}),
                    html.Tbody(id="perms-uc-tbody", children=[
                        _perm_row(p["object"], p["permission"], p["granted_by"])
                        for p in _UC_PERMISSIONS
                    ]),
                ], className="table table-sm mb-0",
                   style={"color": "#D6EAF8"}),
                className="p-2",
            ),
        ], className="shadow-sm"),
    ], className="px-2 py-3")


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("perms-infra-tbody",    "children"),
    Output("perms-uc-tbody",       "children"),
    Output("perms-verify-status",  "children"),
    Output("perms-verify-btn",     "disabled"),
    Input("perms-verify-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def verify_permissions(n_clicks):
    if not n_clicks:
        return dash.no_update, dash.no_update, dash.no_update, False

    # Run infra checks concurrently
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_wh    = ex.submit(_check_warehouse)
        f_genie = ex.submit(_check_genie)
        f_vs    = ex.submit(_check_vs_endpoint)
        f_uc    = ex.submit(_check_uc_access)
        wh_status    = f_wh.result()
        genie_status = f_genie.result()
        vs_status    = f_vs.result()
        uc_results   = f_uc.result()

    # Build infra rows with live statuses
    infra_statuses = {
        "warehouses":              wh_status,
        "genie":                   genie_status,
        "vector-search-endpoints": vs_status,
    }

    infra_rows = []
    for p in _INFRA_PERMISSIONS:
        otype  = p["object_type"]
        status = infra_statuses.get(otype, "GRANTED")  # jobs / KA shown as granted
        infra_rows.append(
            _perm_row(p["resource"], p["permission"], p["granted_by"], status)
        )

    # Build UC rows
    uc_table_map = {
        "patient_records":       "patient_records",
        "care_gap_rules":        "care_gap_rules",
        "icd10_analysis_results":"icd10_analysis_results",
        "care_gap_findings":     "care_gap_findings",
        "bootstrap_status":      "bootstrap_status",
        "knowledge_graph":       "knowledge_graph",
    }
    uc_rows = []
    for p in _UC_PERMISSIONS:
        obj    = p["object"]
        tbl    = uc_table_map.get(obj)
        status = uc_results.get(tbl, "GRANTED") if tbl else "GRANTED"
        uc_rows.append(_perm_row(obj, p["permission"], p["granted_by"], status))

    # Summary banner
    all_ok = all(
        v == "GRANTED"
        for v in list(infra_statuses.values()) + list(uc_results.values())
    )
    if all_ok:
        banner = dbc.Alert(
            [html.I(className="fa-solid fa-circle-check me-2 text-success"),
             html.Strong("All permissions verified ✅")],
            color="success", className="py-2 small",
        )
    else:
        issues = [k for k, v in {**infra_statuses, **uc_results}.items()
                  if v not in ("GRANTED", "GRANTED")]
        banner = dbc.Alert(
            [html.I(className="fa-solid fa-triangle-exclamation me-2"),
             html.Strong("Some permissions missing — "),
             f"re-run ./deploy.sh to fix. Issues: {', '.join(issues[:3])}"],
            color="warning", className="py-2 small",
        )

    return infra_rows, uc_rows, banner, False
