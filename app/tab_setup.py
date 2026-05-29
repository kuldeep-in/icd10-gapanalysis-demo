from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import dash
from dash import dcc, html, callback, Input, Output, State
import dash_bootstrap_components as dbc

from config import (
    w, CATALOG, SCHEMA, WAREHOUSE_ID,
    KA_ENDPOINT_NAME, KA_NAME, FMAPI_ENDPOINT,
    VS_ENDPOINT_NAME,
    DATA_SETUP_JOB_NAME, AI_SETUP_JOB_NAME,
    BOOTSTRAP_STEPS, GROUP_META, STATUS_META, DONE_STATUSES,
    JOB1_STEPS, JOB2_STEPS, logger, _app_sp_name,
)
from db import load_patients, execute_sql, _sql_esc

# ---------------------------------------------------------------------------
# Job ID cache — populated lazily on first trigger button click, never on load
# ---------------------------------------------------------------------------
_job_ids: dict = {}


def _get_job_ids() -> tuple[int | None, int | None]:
    if not _job_ids:
        try:
            for job in w.jobs.list():
                name = job.settings.name or ""
                if DATA_SETUP_JOB_NAME in name:
                    _job_ids["job1_id"] = job.job_id
                elif AI_SETUP_JOB_NAME in name:
                    _job_ids["job2_id"] = job.job_id
                if "job1_id" in _job_ids and "job2_id" in _job_ids:
                    break
        except Exception as e:
            logger.warning(f"Job lookup failed: {e}")
    return _job_ids.get("job1_id"), _job_ids.get("job2_id")


# ---------------------------------------------------------------------------
# Bootstrap status — single SQL query, written by setup notebooks
# ---------------------------------------------------------------------------
def _load_bootstrap_statuses(catalog: str, schema: str) -> dict:
    try:
        rows = execute_sql(
            f"SELECT step, status, details, updated_at "
            f"FROM `{catalog}`.`{schema}`.bootstrap_status "
            f"WHERE status = 'COMPLETED'"
        )
        return {r["step"]: r for r in rows}
    except Exception as e:
        logger.warning(f"Could not load bootstrap_status: {e}")
        return {}


# ---------------------------------------------------------------------------
# KA checks — called only on page load and Refresh button click
# ---------------------------------------------------------------------------
def _chk_ka_endpoint(endpoint_name: str) -> dict:
    if not endpoint_name:
        return {"ok": False, "label": "KA_ENDPOINT_NAME not set — run setup_resources.py before deploy"}
    try:
        w.serving_endpoints.get(name=endpoint_name)
        return {"ok": True, "label": f"Endpoint `{endpoint_name}` ready"}
    except Exception as e:
        err = str(e)
        if any(x in err for x in ("NOT_FOUND", "404", "does not exist", "not found")):
            return {"ok": False, "label": f"Endpoint `{endpoint_name}` not found"}
        return {"ok": False, "label": err[:100]}


def _chk_vs_endpoint(endpoint_name: str) -> dict:
    if not endpoint_name:
        return {"ok": False, "label": "VS_ENDPOINT_NAME not set — run setup_resources.py before deploy"}
    try:
        data  = w.api_client.do("GET", f"/api/2.0/vector-search/endpoints/{endpoint_name}")
        state = data.get("endpoint_status", {}).get("state", "")
        if state == "ONLINE":
            return {"ok": True, "label": f"Endpoint `{endpoint_name}` ONLINE"}
        return {"ok": False, "label": f"Endpoint `{endpoint_name}` state: {state or 'unknown'}"}
    except Exception as e:
        err = str(e)
        if any(x in err for x in ("NOT_FOUND", "404", "does not exist", "not found")):
            return {"ok": False, "label": f"Endpoint `{endpoint_name}` not found"}
        return {"ok": False, "label": err[:100]}


def _chk_ka_sources(ka_name: str, volume_path: str) -> dict:
    if not ka_name:
        return {"source_found": False, "state": "", "ingestion": {}, "error": "KA_NAME not set"}
    try:
        src_data = w.api_client.do("GET", f"/api/2.1/{ka_name}/knowledge-sources")
        sources  = src_data.get("knowledge_sources", [])
        norm     = volume_path.rstrip("/")
        matched  = next(
            (s for s in sources if (s.get("files") or {}).get("path", "").rstrip("/") == norm),
            None,
        )
        if not matched:
            return {"source_found": False, "state": "", "ingestion": {},
                    "error": None, "source_count": len(sources)}
        return {
            "source_found": True,
            "state":        matched.get("state", ""),
            "ingestion":    matched.get("ingestion_details") or {},
            "cutoff_time":  matched.get("knowledge_cutoff_time", ""),
            "error":        None,
        }
    except Exception as e:
        return {"source_found": False, "state": "", "ingestion": {}, "error": str(e)[:120]}


# ---------------------------------------------------------------------------
# KA sync cache writer — called once when indexing first reaches UPDATED state
# ---------------------------------------------------------------------------
def _cache_ka_sync_status(catalog: str, schema: str, detail: str) -> None:
    try:
        execute_sql(f"""
            MERGE INTO `{catalog}`.`{schema}`.bootstrap_status AS t
            USING (SELECT 'ka_source_sync' AS step) AS s ON t.step = s.step
            WHEN MATCHED THEN UPDATE SET
                status = 'COMPLETED', updated_at = CURRENT_TIMESTAMP(),
                details = '{_sql_esc(detail)}'
            WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
                VALUES ('ka_source_sync', 'COMPLETED', CURRENT_TIMESTAMP(), '{_sql_esc(detail)}')
        """)
        logger.info("Cached ka_source_sync = COMPLETED in bootstrap_status")
    except Exception as e:
        logger.warning(f"Could not cache ka_source_sync status: {e}")


# ---------------------------------------------------------------------------
# Step status computation
# Happy path (setup complete): 1 SQL only, zero KA API calls
# During indexing: 1 SQL + 1 KA API; writes cache on first UPDATED result
# ---------------------------------------------------------------------------
def _check_step_statuses(catalog: str, schema: str) -> list[dict]:
    db_statuses = _load_bootstrap_statuses(catalog, schema)

    # Skip KA API entirely if sync result is already cached in bootstrap_status
    ka_src = None
    if "ka_source_sync" not in db_statuses:
        ka_src = _chk_ka_sources(KA_NAME, f"/Volumes/{catalog}/{schema}/icd10_reference_pdfs")

    result = []
    for step in BOOTSTRAP_STEPS:
        sid = step["step_id"]

        # Steps 1–5: trust bootstrap_status written by the setup notebooks
        if sid in db_statuses:
            row = db_statuses[sid]
            ts  = str(row.get("updated_at", ""))[:19].replace("T", " ")
            result.append({**step, "status": "COMPLETED",
                           "detail": row.get("details", ""), "checks": [], "updated_at": ts})
            continue

        # Step 6: KA sync state is dynamic — derived from the KA API response
        if sid == "ka_source_sync":
            state     = (ka_src or {}).get("state", "")
            ingestion = (ka_src or {}).get("ingestion", {})
            if not ka_src or not ka_src.get("source_found"):
                status = "NOT_STARTED"
                detail = "Volume not attached — complete step 5 first"
            elif state == "UPDATED":
                total   = ingestion.get("total_file_count",   "?")
                success = ingestion.get("success_file_count", "?")
                failed  = ingestion.get("failed_file_count",  "0")
                vectors = ingestion.get("vector_count",       "?")
                status  = "COMPLETED"
                detail  = f"{success}/{total} files indexed · {vectors} vectors"
                if str(failed) not in ("0", ""):
                    detail += f" · {failed} failed"
            elif state in ("UPDATING", "PENDING", "RUNNING"):
                success = ingestion.get("success_file_count", "0")
                total   = ingestion.get("total_file_count",   "?")
                status  = "IN_PROGRESS"
                detail  = f"Indexing in progress — {success}/{total} files done"
            elif state == "FAILED":
                status = "FAILED"
                detail = "Indexing failed — check KA sources in Databricks UI"
            else:
                status = "NOT_STARTED"
                detail = f"Sync state: {state or 'unknown'}"
            ts = datetime.now().strftime("%H:%M:%S") if status in DONE_STATUSES else ""
            if status == "COMPLETED":
                _cache_ka_sync_status(catalog, schema, detail)
            result.append({**step, "status": status, "detail": detail,
                           "checks": [], "updated_at": ts})
            continue

        result.append({**step, "status": "NOT_STARTED", "detail": "Not yet started",
                       "checks": [], "updated_at": ""})

    return result


# ---------------------------------------------------------------------------
# Layout builders
# ---------------------------------------------------------------------------
def _prereq_section(prereqs: list[dict]) -> html.Div:
    rows = []
    for p in prereqs:
        ok        = p.get("ok", False)
        value     = p.get("value") or ""
        fa_status = "fa-circle-check text-success" if ok else "fa-circle-xmark text-danger"
        rows.append(
            html.Div([
                html.I(className=f"fa-solid {fa_status} me-2", style={"width": "14px"}),
                html.I(className=f"fa-solid {p['icon']} me-2 text-muted", style={"width": "14px"}),
                html.Span(p["label"] + ":", className="small fw-semibold me-2"),
                html.Code(
                    value if value else "Not configured",
                    className="small",
                    style={"fontSize": "11px", "color": "#dc3545" if not value else "inherit"},
                ),
            ], className="d-flex align-items-center mb-1")
        )
    return html.Div([
        html.Div([
            html.I(className="fa-solid fa-shield-check me-1 text-muted"),
            html.Span("Prerequisites", className="small fw-semibold text-muted text-uppercase",
                      style={"letterSpacing": "0.5px", "fontSize": "11px"}),
        ], className="mb-2"),
        html.Div(rows, className="ps-2 border-start border-2", style={"borderColor": "#dee2e6"}),
    ], className="mb-3 p-2 rounded", style={"background": "#f8f9fa", "border": "1px solid #e9ecef"})


def _step_accordion_item(step: dict) -> dbc.AccordionItem:
    status   = step.get("status", "NOT_STARTED")
    meta     = STATUS_META.get(status, STATUS_META["NOT_STARTED"])
    detail   = step.get("detail", "")
    ts       = step.get("updated_at", "")
    is_async = step.get("is_async", False)

    left_color = {
        "COMPLETED":       "#198754",
        "LIKELY_COMPLETE": "#0dcaf0",
        "IN_PROGRESS":     "#ffc107",
        "WARNING":         "#ffc107",
        "FAILED":          "#dc3545",
        "NOT_STARTED":     "#dee2e6",
        "SKIPPED":         "#dee2e6",
    }.get(status, "#dee2e6")
    num_text_color = "white" if status not in ("NOT_STARTED", "SKIPPED") else "#6c757d"

    title = html.Span([
        html.Span(
            str(step["seq"]),
            style={
                "display": "inline-flex", "alignItems": "center", "justifyContent": "center",
                "width": "22px", "height": "22px", "borderRadius": "50%",
                "backgroundColor": left_color, "color": num_text_color,
                "fontSize": "11px", "fontWeight": "700", "flexShrink": "0", "marginRight": "8px",
            }
        ),
        html.I(className=f"fa-solid {step['icon']} me-2 text-muted"),
        html.Span(step["label"], style={"fontWeight": "600", "fontSize": "14px"}),
        html.Span(" (async)", className="ms-1 text-muted small fst-italic") if is_async else None,
        dbc.Badge(
            [html.I(className=f"fa-solid {meta['icon']} me-1"), meta["label"]],
            color=meta["color"], pill=True,
            style={"fontSize": "10px", "marginLeft": "auto", "flexShrink": "0"},
        ),
    ], style={"display": "flex", "alignItems": "center", "width": "100%", "gap": "0"})

    body_children = [html.P(step["description"], className="small text-muted mb-2")]

    checks = step.get("checks", [])
    if checks:
        check_rows = []
        for icon, label, c in checks:
            ok         = c.get("ok", False)
            detail_txt = c.get("label", "—")
            color      = "text-success" if ok else "text-danger"
            fa_icon    = "fa-circle-check" if ok else "fa-circle-xmark"
            check_rows.append(
                html.Div([
                    html.I(className=f"fa-solid {fa_icon} {color} me-2", style={"width": "14px"}),
                    html.I(className=f"fa-solid {icon} me-2 text-muted", style={"width": "14px"}),
                    html.Span(f"{label}: ", className="small fw-semibold me-1"),
                    html.Span(detail_txt, className="small text-muted"),
                ], className="d-flex align-items-center mb-1")
            )
        body_children.append(
            html.Div(check_rows, className="mt-2 mb-1 ps-2 border-start border-2",
                     style={"borderColor": "#dee2e6"})
        )

    if detail and detail != "Not yet started":
        body_children.append(
            html.Div([
                html.I(className="fa-solid fa-circle-info me-1 text-secondary"),
                html.Span(detail, className="small"),
            ], className="mb-1")
        )
    if ts:
        body_children.append(
            html.Small([html.I(className="fa-regular fa-clock me-1"), f"Updated: {ts}"],
                       className="text-muted")
        )

    return dbc.AccordionItem(
        html.Div(body_children),
        title=title,
        item_id=step["step_id"],
    )


def _group_badge(group_steps: list[dict]) -> dbc.Badge:
    completed = sum(1 for s in group_steps if s["status"] in DONE_STATUSES)
    total     = len(group_steps)
    running   = any(s["status"] == "IN_PROGRESS" for s in group_steps)
    failed    = any(s["status"] == "FAILED"       for s in group_steps)
    warning   = any(s["status"] == "WARNING"      for s in group_steps)

    if completed == total:
        return dbc.Badge(
            [html.I(className="fa-solid fa-circle-check me-1"), f"{completed}/{total} Complete"],
            color="success", pill=True, style={"fontSize": "11px"})
    if failed:
        return dbc.Badge(
            [html.I(className="fa-solid fa-circle-xmark me-1"), "Failed"],
            color="danger", pill=True, style={"fontSize": "11px"})
    if running:
        return dbc.Badge(
            [html.I(className="fa-solid fa-spinner fa-spin me-1"), "Running…"],
            color="warning", pill=True, style={"fontSize": "11px"})
    if warning:
        return dbc.Badge(
            [html.I(className="fa-solid fa-triangle-exclamation me-1"), f"{completed}/{total} Complete"],
            color="warning", pill=True, style={"fontSize": "11px"})
    return dbc.Badge(f"{completed}/{total} Complete", color="secondary", pill=True,
                     style={"fontSize": "11px"})


def _settings_column() -> dbc.Col:
    def _cfg_row(label: str, value: str, icon: str, warn: bool = False) -> dbc.Row:
        return dbc.Row([
            dbc.Col(
                html.Span([
                    html.I(className=f"fa-solid {icon} me-2 text-muted", style={"width": "14px"}),
                    html.Span(label, className="small fw-semibold text-muted"),
                ]),
                width=5,
            ),
            dbc.Col(
                dbc.Badge(
                    value or "—",
                    color="danger" if warn else "light",
                    text_color="white" if warn else "dark",
                    className="font-monospace text-wrap",
                    style={"fontSize": "11px", "fontWeight": "400"},
                ),
                width=7,
            ),
        ], className="mb-2 align-items-center")

    def _section(label: str) -> html.Small:
        return html.Small(
            label, className="text-muted text-uppercase fw-bold d-block mb-2",
            style={"fontSize": "10px", "letterSpacing": "0.5px"},
        )

    wh_missing = not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>"

    card = dbc.Card([
        dbc.CardHeader([
            html.I(className="fa-solid fa-gear me-2"),
            html.Strong("Configuration", style={"fontSize": "14px"}),
        ], style={"background": "#f8f9fa", "borderBottom": "2px solid #6c757d"}),
        dbc.CardBody([
            _section("Unity Catalog"),
            _cfg_row("Catalog", CATALOG or "—", "fa-layer-group"),
            _cfg_row("Schema",  SCHEMA  or "—", "fa-table"),
            html.Hr(className="my-2"),
            _section("Infrastructure"),
            _cfg_row("SQL Warehouse", WAREHOUSE_ID or "Not set", "fa-warehouse", warn=wh_missing),
            html.Hr(className="my-2"),
            _section("AI Configuration"),
            _cfg_row("Care Gap Model", FMAPI_ENDPOINT      or "Not set", "fa-microchip"),
            _cfg_row("KA Endpoint",    KA_ENDPOINT_NAME    or "Not set", "fa-robot",    warn=not KA_ENDPOINT_NAME),
            _cfg_row("Data Setup Job", DATA_SETUP_JOB_NAME or "—",       "fa-play"),
            _cfg_row("AI Setup Job",   AI_SETUP_JOB_NAME   or "—",       "fa-play"),
            html.Hr(className="my-2"),
            _section("App Identity"),
            _cfg_row("Service Principal", _app_sp_name or "Not resolved",
                     "fa-user-gear", warn=not _app_sp_name),
            html.Hr(className="my-2"),
            html.Small(
                [html.I(className="fa-solid fa-circle-info me-1"),
                 "Values set in ", html.Code("app.yaml", style={"fontSize": "10px"}), " at deploy time."],
                className="text-muted", style={"fontSize": "11px"},
            ),
        ], className="p-2"),
    ], style={"borderTop": "3px solid #6c757d", "height": "100%"}, className="shadow-sm")

    return dbc.Col(card, md=4, className="mb-3")


def _job_column(group: int, g_steps: list[dict], action: dict,
                prereqs: list[dict] | None = None,
                accordion_active=None) -> dbc.Col:
    meta         = GROUP_META[group]
    btn_id       = "job1-trigger-btn" if group == 1 else "job2-trigger-btn"
    result_id    = "job1-trigger-result" if group == 1 else "job2-trigger-result"
    btn_color    = "primary" if group == 1 else "success"
    border_color = meta["border"]

    a          = action.get("action", "none")
    prereqs_ok = all(p.get("ok", False) for p in (prereqs or []))

    default_active = [s["step_id"] for s in g_steps if s["status"] not in DONE_STATUSES]
    active_item    = accordion_active if accordion_active is not None else default_active
    accordion = dbc.Accordion(
        [_step_accordion_item(s) for s in g_steps],
        id=f"accordion-group{group}",
        always_open=True,
        active_item=active_item,
        className="mb-0",
    )

    if a == "done":
        run_btn  = dbc.Button(
            [html.I(className="fa-solid fa-circle-check me-2"), "Complete"],
            id=btn_id, color="success", outline=True,
            disabled=True, size="sm", style={"minWidth": "130px"},
        )
        run_hint = None

    else:
        icon    = "fa-rotate-right" if "Re-run" in action.get("label", "") else "fa-play"
        run_btn = dbc.Button(
            [html.I(className=f"fa-solid {icon} me-2"), action.get("label", "Run")],
            id=btn_id, color=btn_color,
            disabled=not prereqs_ok, size="sm", style={"minWidth": "130px"},
        )
        run_hint = (
            html.Small(
                [html.I(className="fa-solid fa-lock me-1 text-muted"), "Prerequisites not met"],
                className="d-block text-center text-muted mt-1",
            ) if not prereqs_ok else None
        )

    run_area = html.Div([run_btn, run_hint], className="d-flex flex-column align-items-center")

    card = dbc.Card([
        dbc.CardHeader(
            dbc.Row([
                dbc.Col([
                    html.I(className=f"fa-solid {meta['icon']} me-2"),
                    html.Strong(meta["label"], style={"fontSize": "14px"}),
                ], width="auto"),
                dbc.Col(_group_badge(g_steps), width="auto", className="ms-auto"),
            ], align="center"),
            style={"background": meta["bg"], "borderBottom": f"2px solid {border_color}"},
        ),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(_prereq_section(prereqs) if prereqs else None, className="pe-2"),
                dbc.Col(run_area, width="auto",
                        className="d-flex align-items-center border-start ps-3"),
            ], align="center", className="mb-3 g-0"),
            html.Div(id=result_id, className="mb-2"),
            html.Div(accordion),
        ], className="p-2"),
    ], style={"borderTop": f"3px solid {border_color}", "height": "100%"})
    return dbc.Col(card, md=4, className="mb-3")


def build_setup_tab_content(
    steps: list[dict],
    last_refreshed: str,
    all_done: bool,
    act1: dict | None = None,
    act2: dict | None = None,
    prereqs1: list[dict] | None = None,
    prereqs2: list[dict] | None = None,
    accordion1_active=None,
    accordion2_active=None,
) -> html.Div:
    completed  = sum(1 for s in steps if s["status"] in DONE_STATUSES)
    total      = len(steps)
    pct        = int(completed / total * 100)
    prog_color = "success" if all_done else ("warning" if completed > 0 else "secondary")

    banner = dbc.Alert(
        [html.I(className="fa-solid fa-circle-check me-2 text-success"),
         html.Strong("All prerequisites ready — "),
         "ICD-10 Analyzer and Care Gap Advisor are fully operational."],
        color="success", className="mb-3 py-2"
    ) if all_done else dbc.Alert(
        [html.I(className="fa-solid fa-circle-info me-2"),
         html.Strong("Setup in progress. "),
         "Tabs may be limited until setup completes. Click Refresh to update status."],
        color="info", className="mb-3 py-2"
    )

    groups: dict[int, list[dict]] = {}
    for step in steps:
        groups.setdefault(step.get("group", 1), []).append(step)

    return html.Div([
        banner,
        dbc.Progress(
            value=pct, color=prog_color,
            striped=not all_done, animated=not all_done,
            style={"height": "10px"}, className="mb-3",
        ),
        dbc.Row([
            _settings_column(),
            _job_column(1, groups.get(1, []), act1 or {"action": "none"},
                        prereqs=prereqs1, accordion_active=accordion1_active),
            _job_column(2, groups.get(2, []), act2 or {"action": "none"},
                        prereqs=prereqs2, accordion_active=accordion2_active),
        ], className="g-3"),
        html.Div([
            html.Hr(),
            dbc.Alert([
                html.Strong("To start setup, run:  "),
                html.Code(
                    "databricks bundle deploy --profile <your-profile>  &&  "
                    "databricks bundle run bootstrap_workflow --profile <your-profile>",
                    style={"fontSize": "12px"}
                ),
            ], color="secondary", className="mb-0 py-2"),
        ]) if completed == 0 else None,
    ])


# ---------------------------------------------------------------------------
# Shell — renders skeleton instantly (zero DB calls), interval triggers load
# ---------------------------------------------------------------------------
def setup_shell() -> html.Div:
    skeleton_steps = [
        {**s, "status": "NOT_STARTED", "detail": "Loading…", "checks": [], "updated_at": ""}
        for s in BOOTSTRAP_STEPS
    ]
    wh_ok    = bool(WAREHOUSE_ID)    and WAREHOUSE_ID    not in ("", "<your-warehouse-id>")
    vs_ep_ok = bool(VS_ENDPOINT_NAME) and VS_ENDPOINT_NAME not in ("", "<your-vs-endpoint>")
    ka_ok    = bool(KA_ENDPOINT_NAME) and KA_ENDPOINT_NAME not in ("", "<your-ka-endpoint>")
    prereqs1 = [
        {"icon": "fa-warehouse",    "label": "SQL Warehouse", "value": WAREHOUSE_ID     or "Not set", "ok": wh_ok},
        {"icon": "fa-circle-nodes", "label": "VS Endpoint",   "value": VS_ENDPOINT_NAME or "Not set", "ok": vs_ep_ok},
    ]
    prereqs2 = [{"icon": "fa-robot", "label": "KA Endpoint",
                 "value": KA_ENDPOINT_NAME or "Not set", "ok": ka_ok}]
    act_stub = {"action": "run_job1", "job_id": 1, "label": "—", "active_run": None, "description": ""}
    skeleton_content = build_setup_tab_content(
        skeleton_steps, "—", False,
        act1={**act_stub, "job_name": DATA_SETUP_JOB_NAME},
        act2={**act_stub, "action": "run_job2", "job_name": AI_SETUP_JOB_NAME},
        prereqs1=prereqs1,
        prereqs2=prereqs2,
        accordion1_active=[],
        accordion2_active=[],
    )

    loading_toast = dbc.Toast(
        [html.I(className="fa-solid fa-rotate fa-spin me-2"), "Fetching setup status…"],
        id="setup-loading-toast",
        header="Setup",
        icon="info",
        is_open=True,
        dismissable=False,
        style={
            "position": "fixed", "top": "70px", "right": "20px",
            "zIndex": 9999, "minWidth": "220px",
        },
    )

    return html.Div(className="px-4 py-3", children=[
        # Fires once per /setup visit to load real statuses into the skeleton
        dcc.Interval(id="setup-load-interval", interval=500, max_intervals=1, n_intervals=0),

        dbc.Row([
            dbc.Col(html.H5("Demo Environment Status", className="mb-0 fw-bold"), width="auto"),
            dbc.Col(
                html.Small(id="setup-last-updated", className="text-muted"),
                width="auto", className="ms-auto",
            ),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"), "Refresh Now"],
                    id="setup-refresh-btn", color="outline-secondary", size="sm",
                ),
                width="auto", className="ms-2",
            ),
        ], align="center", className="mb-3"),
        html.Div(id="setup-step-content", children=[loading_toast, skeleton_content]),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("setup-step-content",              "children"),
    Output("all-done-store",       "data",                allow_duplicate=True),
    Output("ka-endpoint-store",   "data"),
    Output("patient-store",       "data",                allow_duplicate=True),
    Output("setup-last-updated",  "children"),
    Output("setup-complete-store","data",                allow_duplicate=True),
    Input("setup-refresh-btn",    "n_clicks"),
    Input("setup-load-interval", "n_intervals"),
    State("catalog-store",       "data"),
    State("schema-store",        "data"),
    prevent_initial_call=True,
)
def refresh_setup(n_clicks, n_intervals, catalog, schema):
    cat = catalog or CATALOG
    sch = schema  or SCHEMA

    if not WAREHOUSE_ID or WAREHOUSE_ID == "<your-warehouse-id>":
        return (
            html.Div(dbc.Alert(
                [html.I(className="fa-solid fa-warehouse me-2"),
                 html.Strong("SQL Warehouse not configured. "),
                 "Set ", html.Code("DATABRICKS_WAREHOUSE_ID"), " in ",
                 html.Code("app.yaml"), " and redeploy. See the ",
                 html.Strong("Settings"), " tab for current configuration."],
                color="danger", className="py-2",
            )),
            False, "", [], "", False,
        )

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_steps = ex.submit(_check_step_statuses, cat, sch)
        f_ka_ep = ex.submit(_chk_ka_endpoint, KA_ENDPOINT_NAME)
        f_vs_ep = ex.submit(_chk_vs_endpoint, VS_ENDPOINT_NAME)
        steps  = f_steps.result()
        ka_chk = f_ka_ep.result()
        vs_chk = f_vs_ep.result()

    all_done  = all(s["status"] in DONE_STATUSES for s in steps)
    job1_done = all(s["status"] in DONE_STATUSES for s in steps if s["step_id"] in JOB1_STEPS)
    now = datetime.now().strftime("%H:%M:%S")

    patients = []
    if job1_done:
        try:
            patients = load_patients(cat, sch)
        except Exception as e:
            logger.warning(f"Patient load failed: {e}")

    wh_ok    = bool(WAREHOUSE_ID) and WAREHOUSE_ID not in ("", "<your-warehouse-id>")
    vs_ep_ok = bool(VS_ENDPOINT_NAME) and VS_ENDPOINT_NAME not in ("", "<your-vs-endpoint>")
    prereqs1 = [
        {"icon": "fa-warehouse",    "label": "SQL Warehouse", "value": WAREHOUSE_ID,     "ok": wh_ok},
        {"icon": "fa-circle-nodes", "label": "VS Endpoint",   "value": VS_ENDPOINT_NAME or "Not set",
         "ok": vs_chk["ok"]},
    ]
    prereqs2 = [{"icon": "fa-robot", "label": "KA Endpoint", "value": KA_ENDPOINT_NAME, "ok": ka_chk["ok"]}]

    job1_steps_done = all(s["status"] in DONE_STATUSES for s in steps if s["step_id"] in JOB1_STEPS)
    job2_steps_done = all(s["status"] in DONE_STATUSES for s in steps if s["step_id"] in JOB2_STEPS)
    act1 = {
        "action":      "done" if job1_steps_done else "run_job1",
        "job_id":      1,
        "label":       "Run Data Setup",
        "job_name":    DATA_SETUP_JOB_NAME,
        "active_run":  None,
        "description": "Creates catalog, ingests patient records, uploads ICD-10 PDFs",
    }
    act2 = {
        "action":      "done" if job2_steps_done else "run_job2",
        "job_id":      1,
        "label":       "Run KA Setup",
        "job_name":    AI_SETUP_JOB_NAME,
        "active_run":  None,
        "description": "Uploads ICD-10 PDFs and configures the Knowledge Assistant",
    }

    content = build_setup_tab_content(steps, now, all_done, act1, act2, prereqs1, prereqs2)
    last_updated = [html.I(className="fa-regular fa-clock me-1"), f"Updated {now}"]
    return content, all_done, KA_ENDPOINT_NAME, patients, last_updated, all_done


def _trigger_job(action: dict) -> tuple:
    job_id = action.get("job_id")
    if not job_id:
        return dbc.Alert("Job not found — deploy the bundle first.", color="danger",
                         className="py-2 small"), False
    try:
        waiter  = w.jobs.run_now(job_id=job_id)
        run_url = ""
        try:
            run_url = w.jobs.get_run(run_id=waiter.run_id).run_page_url or ""
        except Exception:
            pass
        return dbc.Alert(
            [html.I(className="fa-solid fa-circle-check me-2 text-success"),
             html.Strong(f"{action.get('job_name', 'Job')} triggered — "),
             html.A("view run →", href=run_url, target="_blank") if run_url
             else html.Span("check Workflows UI")],
            color="success", className="py-2 small d-flex align-items-center",
        ), True
    except Exception as e:
        logger.error(f"Job trigger failed: {e}")
        return dbc.Alert(f"Failed to trigger: {e}", color="danger", className="py-2 small"), False


@callback(
    Output("job1-trigger-result", "children"),
    Output("job1-trigger-btn",    "disabled"),
    Input("job1-trigger-btn",     "n_clicks"),
    prevent_initial_call=True,
)
def handle_job1_trigger(n_clicks):
    if not n_clicks:
        return dash.no_update, dash.no_update
    job1_id, _ = _get_job_ids()
    return _trigger_job({"job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME})


@callback(
    Output("job2-trigger-result", "children"),
    Output("job2-trigger-btn",    "disabled"),
    Input("job2-trigger-btn",     "n_clicks"),
    prevent_initial_call=True,
)
def handle_job2_trigger(n_clicks):
    if not n_clicks:
        return dash.no_update, dash.no_update
    _, job2_id = _get_job_ids()
    return _trigger_job({"job_id": job2_id, "job_name": AI_SETUP_JOB_NAME})
