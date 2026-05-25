from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import dash
from dash import html, callback, Input, Output, State
import dash_bootstrap_components as dbc

from config import (
    w, CATALOG, SCHEMA, WAREHOUSE_ID,
    KA_ENDPOINT_NAME, KA_NAME,
    DATA_SETUP_JOB_NAME, AI_SETUP_JOB_NAME,
    BOOTSTRAP_STEPS, GROUP_META, STATUS_META, DONE_STATUSES,
    JOB1_STEPS, JOB2_STEPS, logger,
)
from db import load_patients, execute_sql, _sql_esc

_BOOTSTRAP_STABLE_STEPS = {
    "create_catalog", "setup_care_gap_rules", "ingest_patient_data",
    "load_icd10_pdfs", "ka_source_configured",
}


# ---------------------------------------------------------------------------
# Bootstrap table helpers
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


def _write_bootstrap_status(catalog: str, schema: str, step_id: str, detail: str) -> None:
    if step_id not in _BOOTSTRAP_STABLE_STEPS:
        return
    try:
        execute_sql(f"""
            MERGE INTO `{catalog}`.`{schema}`.bootstrap_status AS t
            USING (SELECT '{_sql_esc(step_id)}' AS step,
                          'COMPLETED'            AS status,
                          '{_sql_esc(detail)}'   AS details) AS s
            ON t.step = s.step
            WHEN MATCHED THEN UPDATE SET
                status     = s.status,
                updated_at = CURRENT_TIMESTAMP(),
                details    = s.details
            WHEN NOT MATCHED THEN INSERT (step, status, updated_at, details)
                VALUES (s.step, s.status, CURRENT_TIMESTAMP(), s.details)
        """)
    except Exception as e:
        logger.warning(f"Could not write bootstrap_status for {step_id}: {e}")


# ---------------------------------------------------------------------------
# Bootstrap step checks
# ---------------------------------------------------------------------------
def _chk_catalog(catalog: str) -> dict:
    try:
        rows = execute_sql_inner(
            f"SELECT catalog_name FROM system.information_schema.catalogs "
            f"WHERE catalog_name = '{catalog}'"
        )
        ok = len(rows) > 0
        return {"ok": ok, "label": f"`{catalog}` found" if ok else f"`{catalog}` not found"}
    except Exception as e:
        return {"ok": False, "label": str(e)[:100]}


def _chk_schema(catalog: str, schema: str) -> dict:
    try:
        rows = execute_sql_inner(
            f"SELECT schema_name FROM `{catalog}`.information_schema.schemata "
            f"WHERE schema_name = '{schema}'"
        )
        ok = len(rows) > 0
        return {"ok": ok, "label": f"`{schema}` found" if ok else f"`{schema}` not found"}
    except Exception as e:
        return {"ok": False, "label": str(e)[:100]}


def _chk_table_rows(catalog: str, schema: str, table: str) -> dict:
    try:
        from db import execute_sql
        r   = execute_sql(f"SELECT COUNT(*) as cnt FROM `{catalog}`.`{schema}`.`{table}`")
        cnt = int(r[0]["cnt"]) if r else 0
        return {"ok": cnt > 0, "cnt": cnt, "label": f"{cnt} row{'s' if cnt != 1 else ''}"}
    except Exception as e:
        return {"ok": False, "cnt": 0, "label": str(e)[:120]}


def _chk_volume_files(catalog: str, schema: str, volume: str) -> dict:
    try:
        entries = list(w.files.list_directory_contents(f"/Volumes/{catalog}/{schema}/{volume}"))
        cnt = len(entries)
        return {"ok": cnt > 0, "cnt": cnt, "label": f"{cnt} file{'s' if cnt != 1 else ''}"}
    except Exception as e:
        return {"ok": False, "cnt": 0, "label": str(e)[:120]}


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


def execute_sql_inner(statement: str) -> list[dict]:
    from db import execute_sql
    return execute_sql(statement)


# ---------------------------------------------------------------------------
# Job helpers
# ---------------------------------------------------------------------------
def find_job_ids() -> tuple[int | None, int | None]:
    try:
        job1_id = job2_id = None
        for job in w.jobs.list():
            name = job.settings.name or ""
            if DATA_SETUP_JOB_NAME in name:
                job1_id = job.job_id
            elif AI_SETUP_JOB_NAME in name:
                job2_id = job.job_id
            if job1_id and job2_id:
                break
        return job1_id, job2_id
    except Exception as e:
        logger.warning(f"Job lookup failed: {e}")
        return None, None


def get_active_run(job_id: int) -> dict | None:
    try:
        runs = list(w.jobs.list_runs(job_id=job_id, active_only=True))
        if runs:
            r = runs[0]
            return {"run_id": r.run_id, "url": r.run_page_url or ""}
    except Exception as e:
        logger.warning(f"Active run check failed: {e}")
    return None


def _job1_action(steps: list[dict], job1_id: int | None, job1_active: dict | None) -> dict:
    by_id     = {s["step_id"]: s["status"] for s in steps}
    job1_done = all(by_id.get(s) in DONE_STATUSES for s in JOB1_STEPS)
    job1_fail = any(by_id.get(s) == "FAILED"      for s in JOB1_STEPS)
    if job1_done:
        return {"action": "done", "job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME,
                "label": "Data Setup Complete", "description": "All data setup steps are complete.", "active_run": None}
    if job1_active:
        return {"action": "running", "job_id": job1_id, "job_name": DATA_SETUP_JOB_NAME,
                "label": "Data Setup Running…", "description": "Job 1 is in progress.", "active_run": job1_active}
    return {
        "action":      "run_job1",
        "job_id":      job1_id,
        "job_name":    DATA_SETUP_JOB_NAME,
        "label":       "Re-run Data Setup" if job1_fail else "Run Data Setup",
        "description": "Creates catalog, ingests patient records, uploads ICD-10 PDFs",
        "active_run":  None,
    }


def _job2_action(steps: list[dict], job2_id: int | None, job2_active: dict | None) -> dict:
    by_id        = {s["step_id"]: s["status"] for s in steps}
    job2_done    = all(by_id.get(s) in DONE_STATUSES for s in JOB2_STEPS)
    job2_fail    = any(by_id.get(s) == "FAILED"      for s in JOB2_STEPS)
    job2_syncing = any(by_id.get(s) == "IN_PROGRESS"  for s in JOB2_STEPS)
    if job2_done:
        return {"action": "done", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "KA Setup Complete", "description": "All Knowledge Assistant setup steps are complete.", "active_run": None}
    if job2_active:
        return {"action": "running", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "KA Setup Running…", "description": "Job 2 is in progress.", "active_run": job2_active}
    if job2_syncing:
        return {"action": "running", "job_id": job2_id, "job_name": AI_SETUP_JOB_NAME,
                "label": "Indexing in Progress…",
                "description": "Knowledge Assistant is indexing PDFs. Refresh to check progress.",
                "active_run": None}
    return {
        "action":      "run_job2",
        "job_id":      job2_id,
        "job_name":    AI_SETUP_JOB_NAME,
        "label":       "Re-run KA Setup" if job2_fail else "Run KA Setup",
        "description": "Uploads ICD-10 PDFs and configures the Knowledge Assistant",
        "active_run":  None,
    }


def _check_step_statuses(
    catalog: str, schema: str, cache: dict | None = None
) -> tuple[list[dict], dict]:
    cache     = cache or {}
    new_cache = {k: v for k, v in cache.items() if v.get("status") in DONE_STATUSES}

    stable_missing = _BOOTSTRAP_STABLE_STEPS - set(new_cache.keys())
    if stable_missing:
        for sid, row in _load_bootstrap_statuses(catalog, schema).items():
            if sid in stable_missing:
                ts = str(row.get("updated_at", ""))[:19].replace("T", " ")
                new_cache[sid] = {
                    "status":     "COMPLETED",
                    "detail":     row.get("details", ""),
                    "checks":     [],
                    "updated_at": ts,
                }

    needs_create = "create_catalog"        not in new_cache
    needs_rules  = "setup_care_gap_rules"  not in new_cache
    needs_pts    = "ingest_patient_data"   not in new_cache
    needs_vol    = "load_icd10_pdfs"       not in new_cache or "ka_source_configured" not in new_cache
    needs_ka     = True

    volume_path = f"/Volumes/{catalog}/{schema}/icd10_reference_pdfs"

    with ThreadPoolExecutor(max_workers=6) as ex:
        f_cat   = ex.submit(_chk_catalog, catalog)                               if needs_create else None
        f_sch   = ex.submit(_chk_schema,  catalog, schema)                       if needs_create else None
        f_rules = ex.submit(_chk_table_rows, catalog, schema, "care_gap_rules")  if needs_rules  else None
        f_pts   = ex.submit(_chk_table_rows, catalog, schema, "patient_records") if needs_pts    else None
        f_vol   = ex.submit(_chk_volume_files, catalog, schema, "icd10_reference_pdfs") if needs_vol else None
        f_ka    = ex.submit(_chk_ka_sources, KA_NAME, volume_path)               if needs_ka     else None

        cat_chk   = f_cat.result()   if f_cat   else {"ok": True, "label": f"`{catalog}` found"}
        sch_chk   = f_sch.result()   if f_sch   else {"ok": True, "label": f"`{schema}` found"}
        rules_chk = f_rules.result() if f_rules else None
        pts_chk   = f_pts.result()   if f_pts   else None
        vol_chk   = f_vol.result()   if f_vol   else None
        ka_src    = f_ka.result()    if f_ka    else None

    schema_ok = sch_chk["ok"]

    result = []
    for step in BOOTSTRAP_STEPS:
        sid    = step["step_id"]
        cached = new_cache.get(sid)
        if cached and cached.get("status") in DONE_STATUSES:
            result.append({**step, **cached})
            continue

        status = "NOT_STARTED"
        detail = "Not yet started"
        checks: list = []

        if sid == "create_catalog":
            checks = [
                ("fa-layer-group", "Catalog", cat_chk),
                ("fa-table",       "Schema",  sch_chk),
            ]
            if cat_chk["ok"] and sch_chk["ok"]:
                status = "COMPLETED"
                detail = f"Catalog `{catalog}` and schema `{schema}` exist"
            else:
                missing = "catalog" if not cat_chk["ok"] else "schema"
                detail  = f"`{missing}` not found — run Data Setup"

        elif sid == "setup_care_gap_rules":
            chk    = rules_chk if schema_ok else {"ok": False, "label": "skipped — schema not found"}
            checks = [("fa-list-check", "Care gap rules", chk)]
            status = "COMPLETED" if chk["ok"] else "NOT_STARTED"
            detail = chk["label"]

        elif sid == "ingest_patient_data":
            chk    = pts_chk if schema_ok else {"ok": False, "label": "skipped — schema not found"}
            checks = [("fa-users", "Patient records", chk)]
            status = "COMPLETED" if chk["ok"] else "NOT_STARTED"
            detail = chk["label"]

        elif sid == "load_icd10_pdfs":
            checks = [("fa-file-pdf", "ICD-10 PDFs in volume", vol_chk)]
            status = "COMPLETED" if vol_chk["ok"] else "NOT_STARTED"
            detail = vol_chk["label"]

        elif sid == "ka_source_configured":
            src_ok = ka_src.get("source_found", False) if ka_src else False
            if ka_src and ka_src.get("error"):
                src_label = ka_src["error"]
            elif src_ok:
                src_label = f"Volume attached (state: {ka_src.get('state') or 'unknown'})"
            else:
                src_label = "Volume not attached to KA — run Job 2"
            checks = [
                ("fa-file-pdf", "PDFs in volume",        vol_chk),
                ("fa-plug",     "Volume attached to KA", {"ok": src_ok, "label": src_label}),
            ]
            status = "COMPLETED" if src_ok else "NOT_STARTED"
            detail = src_label

        elif sid == "ka_source_sync":
            state     = (ka_src or {}).get("state", "")
            ingestion = (ka_src or {}).get("ingestion", {})
            if not ka_src or not ka_src.get("source_found"):
                status   = "NOT_STARTED"
                detail   = "Volume not attached — complete step 5 first"
                sync_chk = {"ok": False, "label": detail}
            elif state == "UPDATED":
                total   = ingestion.get("total_file_count",   "?")
                success = ingestion.get("success_file_count", "?")
                failed  = ingestion.get("failed_file_count",  "0")
                vectors = ingestion.get("vector_count",       "?")
                status  = "COMPLETED"
                detail  = f"{success}/{total} files indexed · {vectors} vectors"
                if str(failed) not in ("0", ""):
                    detail += f" · {failed} failed"
                sync_chk = {"ok": True, "label": detail}
            elif state in ("UPDATING", "PENDING", "RUNNING"):
                success  = ingestion.get("success_file_count", "0")
                total    = ingestion.get("total_file_count",   "?")
                status   = "IN_PROGRESS"
                detail   = f"Indexing in progress — {success}/{total} files done"
                sync_chk = {"ok": False, "label": detail}
            elif state == "FAILED":
                status   = "FAILED"
                detail   = "Indexing failed — check KA sources in Databricks UI"
                sync_chk = {"ok": False, "label": detail}
            else:
                status   = "NOT_STARTED"
                detail   = f"Sync state: {state or 'unknown'}"
                sync_chk = {"ok": False, "label": detail}
            checks = [("fa-brain", "Indexing state", sync_chk)]

        ts          = datetime.now().strftime("%H:%M:%S") if status in DONE_STATUSES else ""
        step_result = {**step, "status": status, "detail": detail, "checks": checks, "updated_at": ts}
        result.append(step_result)

        if status in DONE_STATUSES:
            new_cache[sid] = {"status": status, "detail": detail, "checks": checks, "updated_at": ts}
            if sid in _BOOTSTRAP_STABLE_STEPS:
                _write_bootstrap_status(catalog, schema, sid, detail)

    return result, new_cache


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


def _job_column(group: int, g_steps: list[dict], action: dict,
                prereqs: list[dict] | None = None,
                accordion_active=None) -> dbc.Col:
    meta         = GROUP_META[group]
    btn_id       = "job1-trigger-btn" if group == 1 else "job2-trigger-btn"
    result_id    = "job1-trigger-result" if group == 1 else "job2-trigger-result"
    btn_color    = "primary" if group == 1 else "success"
    border_color = meta["border"]

    a          = action.get("action", "none")
    job_id     = action.get("job_id")
    no_job     = job_id is None
    prereqs_ok = all(p.get("ok", False) for p in (prereqs or []))

    default_active = [s["step_id"] for s in g_steps if s["status"] not in DONE_STATUSES]
    acc_active     = accordion_active if accordion_active is not None else default_active
    accordion = dbc.Accordion(
        [_step_accordion_item(s) for s in g_steps],
        id=f"accordion-group{group}",
        always_open=True,
        active_item=acc_active,
        className="mb-0",
    )

    if a == "done":
        run_btn  = dbc.Button(
            [html.I(className="fa-solid fa-circle-check me-2"), "Complete"],
            id=btn_id, color="success", outline=True,
            disabled=True, size="sm", style={"minWidth": "130px"},
        )
        run_hint = None

    elif a == "running":
        run_url  = (action.get("active_run") or {}).get("url", "")
        run_btn  = dbc.Button(
            [dbc.Spinner(size="sm", className="me-2"), action.get("label", "Running…")],
            id=btn_id, color=btn_color,
            disabled=True, size="sm", style={"minWidth": "160px"},
        )
        run_hint = html.A(
            [html.I(className="fa-solid fa-arrow-up-right-from-square me-1"), "View run"],
            href=run_url, target="_blank", className="small d-block text-center mt-1",
        ) if run_url else None

    else:
        btn_disabled = no_job or not prereqs_ok
        icon  = "fa-rotate-right" if "Re-run" in action.get("label", "") else "fa-play"
        run_btn = dbc.Button(
            [html.I(className=f"fa-solid {icon} me-2"), action.get("label", "Run")],
            id=btn_id, color=btn_color,
            disabled=btn_disabled, size="sm", style={"minWidth": "130px"},
        )
        if no_job:
            run_hint = html.Small(
                [html.I(className="fa-solid fa-triangle-exclamation me-1 text-danger"),
                 "Bundle not deployed"],
                className="d-block text-center text-danger mt-1",
            )
        elif not prereqs_ok:
            run_hint = html.Small(
                [html.I(className="fa-solid fa-lock me-1 text-muted"), "Prerequisites not met"],
                className="d-block text-center text-muted mt-1",
            )
        else:
            run_hint = None

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
    return dbc.Col(card, md=6, className="mb-3")


def build_setup_tab_content(
    steps: list[dict],
    last_refreshed: str,
    all_done: bool,
    action1: dict | None = None,
    action2: dict | None = None,
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
        dbc.Row([
            dbc.Col([
                html.Div(
                    [html.Strong(f"{completed}"), f" of {total} steps complete"],
                    className="small text-muted mb-1"
                ),
                dbc.Progress(
                    value=pct, color=prog_color,
                    striped=not all_done, animated=not all_done,
                    style={"height": "10px"},
                ),
            ], width=8),
            dbc.Col(
                html.Small(f"Last updated: {last_refreshed}", className="text-muted"),
                width=4, className="text-end",
            ),
        ], align="center", className="mb-3"),
        dbc.Row([
            _job_column(1, groups.get(1, []), action1 or {"action": "none"},
                        prereqs=prereqs1, accordion_active=accordion1_active),
            _job_column(2, groups.get(2, []), action2 or {"action": "none"},
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


def setup_shell() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("Demo Environment Status", className="mb-0 fw-bold"), width="auto"),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"), "Refresh Now"],
                    id="setup-refresh-btn", color="outline-secondary", size="sm",
                ),
                width="auto", className="ms-auto",
            ),
        ], align="center", className="mb-3"),
        dbc.Spinner(
            html.Div(id="setup-step-content"),
            color="primary",
            spinner_style={"width": "2rem", "height": "2rem"},
        ),
    ])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("setup-step-content",       "children"),
    Output("all-done-store",           "data"),
    Output("ka-endpoint-store",        "data"),
    Output("patient-store",            "data"),
    Output("job1-action-store",        "data"),
    Output("job2-action-store",        "data"),
    Output("step-cache-store",         "data"),
    Output("job-ids-store",            "data"),
    Input("setup-refresh-btn",         "n_clicks"),
    Input("catalog-store",             "data"),
    Input("schema-store",              "data"),
    State("step-cache-store",          "data"),
    State("job-ids-store",             "data"),
    State("accordion1-active-store",   "data"),
    State("accordion2-active-store",   "data"),
    prevent_initial_call=False,
)
def refresh_setup(n_clicks, catalog, schema, step_cache, job_ids_data, acc1_active, acc2_active):
    cat        = catalog or CATALOG
    sch        = schema  or SCHEMA
    step_cache = step_cache or {}

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
            False, "", [], {}, {}, step_cache, job_ids_data or {},
        )

    job_ids_data = job_ids_data or {}
    job1_id      = job_ids_data.get("job1_id")
    job2_id      = job_ids_data.get("job2_id")

    if job1_id is None or job2_id is None:
        found1, found2 = find_job_ids()
        if job1_id is None:
            job1_id = found1
        if job2_id is None:
            job2_id = found2
    new_job_ids = {"job1_id": job1_id, "job2_id": job2_id}

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_steps = ex.submit(_check_step_statuses, cat, sch, step_cache)
        f_run1  = ex.submit(get_active_run, job1_id) if job1_id else None
        f_run2  = ex.submit(get_active_run, job2_id) if job2_id else None
        f_ka_ep = ex.submit(_chk_ka_endpoint, KA_ENDPOINT_NAME)

        steps, new_cache = f_steps.result()
        job1_active = f_run1.result() if f_run1 else None
        job2_active = f_run2.result() if f_run2 else None
        ka_chk      = f_ka_ep.result()

    for step in steps:
        if step["status"] == "NOT_STARTED":
            if step["step_id"] in JOB1_STEPS and job1_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 1 is running…"
            elif step["step_id"] in JOB2_STEPS and job2_active:
                step["status"] = "IN_PROGRESS"
                step["detail"] = "Job 2 is running…"

    all_done  = all(s["status"] in DONE_STATUSES for s in steps)
    job1_done = all(s["status"] in DONE_STATUSES for s in steps if s["step_id"] in JOB1_STEPS)
    now       = datetime.now().strftime("%H:%M:%S")

    patients = []
    if job1_done:
        try:
            patients = load_patients(cat, sch)
        except Exception as e:
            logger.warning(f"Patient load failed: {e}")

    ka_endpoint = KA_ENDPOINT_NAME

    wh_ok    = bool(WAREHOUSE_ID) and WAREHOUSE_ID not in ("", "<your-warehouse-id>")
    prereqs1 = [{"icon": "fa-warehouse", "label": "SQL Warehouse", "value": WAREHOUSE_ID, "ok": wh_ok}]
    prereqs2 = [{"icon": "fa-robot", "label": "KA Endpoint", "value": KA_ENDPOINT_NAME, "ok": ka_chk["ok"]}]

    act1    = _job1_action(steps, job1_id, job1_active)
    act2    = _job2_action(steps, job2_id, job2_active)
    content = build_setup_tab_content(
        steps, now, all_done, act1, act2, prereqs1, prereqs2,
        accordion1_active=acc1_active,
        accordion2_active=acc2_active,
    )
    return content, all_done, ka_endpoint, patients, act1, act2, new_cache, new_job_ids


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
    State("job1-action-store",    "data"),
    prevent_initial_call=True,
)
def handle_job1_trigger(n_clicks, action):
    if not n_clicks or not action:
        return dash.no_update, dash.no_update
    return _trigger_job(action)


@callback(
    Output("job2-trigger-result", "children"),
    Output("job2-trigger-btn",    "disabled"),
    Input("job2-trigger-btn",     "n_clicks"),
    State("job2-action-store",    "data"),
    prevent_initial_call=True,
)
def handle_job2_trigger(n_clicks, action):
    if not n_clicks or not action:
        return dash.no_update, dash.no_update
    return _trigger_job(action)


@callback(
    Output("accordion1-active-store", "data"),
    Input("accordion-group1",         "active_item"),
    prevent_initial_call=True,
)
def save_accordion1_state(active_item):
    return active_item


@callback(
    Output("accordion2-active-store", "data"),
    Input("accordion-group2",         "active_item"),
    prevent_initial_call=True,
)
def save_accordion2_state(active_item):
    return active_item
