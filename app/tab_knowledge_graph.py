import json
import re

import dash
import dash_cytoscape as cyto
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, dcc, html

from config import CATALOG, FMAPI_ENDPOINT, SCHEMA, logger, w
from db import execute_sql

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
_LAYER_COLORS = {
    "App UI":        "#4FC3F7",
    "Core":          "#69F0AE",
    "Infrastructure":"#FFCA28",
    "Setup Jobs":    "#FF8A65",
    "Configuration": "#CE93D8",
    "AI Services":   "#F48FB1",
    "Data Layer":    "#80CBC4",
}

# ---------------------------------------------------------------------------
# Static project manifest
# ---------------------------------------------------------------------------
_FILE_MANIFEST = [
    {"id": "app",           "name": "app.py",                          "layer": "App UI"},
    {"id": "tab_home",      "name": "tab_home.py",                     "layer": "App UI"},
    {"id": "tab_icd10",     "name": "tab_icd10.py",                    "layer": "App UI"},
    {"id": "tab_caregap",   "name": "tab_caregap.py",                  "layer": "App UI"},
    {"id": "tab_setup",     "name": "tab_setup.py",                    "layer": "App UI"},
    {"id": "tab_genie",     "name": "tab_genie.py",                    "layer": "App UI"},
    {"id": "tab_kg",        "name": "tab_knowledge_graph.py",          "layer": "App UI"},
    {"id": "tab_perms",     "name": "tab_permissions.py",              "layer": "App UI"},
    {"id": "config",        "name": "config.py",                       "layer": "Core"},
    {"id": "db",            "name": "db.py",                           "layer": "Core"},
    {"id": "deploy_sh",     "name": "deploy.sh",                       "layer": "Infrastructure"},
    {"id": "setup_res",     "name": "setup_resources.py",              "layer": "Infrastructure"},
    {"id": "setup_schema",  "name": "setup_schema.py",                 "layer": "Infrastructure"},
    {"id": "nb01",          "name": "01_create_catalog.py",            "layer": "Setup Jobs"},
    {"id": "nb02r",         "name": "02_setup_care_gap_rules.py",      "layer": "Setup Jobs"},
    {"id": "nb02p",         "name": "02_ingest_patient_json.py",       "layer": "Setup Jobs"},
    {"id": "nb03",          "name": "03_load_icd10_pdfs.py",           "layer": "Setup Jobs"},
    {"id": "nb04",          "name": "04_configure_knowledge_source.py","layer": "Setup Jobs"},
    {"id": "nb06",          "name": "06_create_care_gap_vs_index.py",  "layer": "Setup Jobs"},
    {"id": "nb07",          "name": "07_configure_genie_space.py",     "layer": "Setup Jobs"},
    {"id": "dby",           "name": "databricks.yml",                  "layer": "Configuration"},
    {"id": "wfy",           "name": "workflows.yml",                   "layer": "Configuration"},
    {"id": "ka_svc",        "name": "Knowledge Assistant",             "layer": "AI Services"},
    {"id": "vs_svc",        "name": "Vector Search",                   "layer": "AI Services"},
    {"id": "genie_svc",     "name": "Genie Space",                     "layer": "AI Services"},
    {"id": "fmapi_svc",     "name": "Foundation Model API",            "layer": "AI Services"},
    {"id": "tbl_pts",       "name": "patient_records",                 "layer": "Data Layer"},
    {"id": "tbl_rules",     "name": "care_gap_rules",                  "layer": "Data Layer"},
    {"id": "tbl_gaps",      "name": "care_gap_findings",               "layer": "Data Layer"},
    {"id": "tbl_icd10",     "name": "icd10_analysis_results",          "layer": "Data Layer"},
    {"id": "tbl_boot",      "name": "bootstrap_status",                "layer": "Data Layer"},
    {"id": "tbl_kg",        "name": "knowledge_graph",                 "layer": "Data Layer"},
]

# source, target, edge_type
_KNOWN_EDGES = [
    ("app","tab_home","imports"),   ("app","tab_icd10","imports"),
    ("app","tab_caregap","imports"),("app","tab_setup","imports"),
    ("app","tab_genie","imports"),  ("app","tab_kg","imports"),
    ("app","tab_perms","imports"),  ("app","config","imports"),
    ("tab_home","db","imports"),    ("tab_icd10","db","imports"),
    ("tab_caregap","db","imports"), ("tab_setup","db","imports"),
    ("tab_kg","db","imports"),      ("tab_genie","config","imports"),
    ("tab_kg","config","imports"),  ("tab_perms","config","imports"),
    ("deploy_sh","setup_res","calls"),
    ("deploy_sh","setup_schema","calls"),
    ("setup_schema","tbl_pts","creates"),  ("setup_schema","tbl_rules","creates"),
    ("setup_schema","tbl_gaps","creates"), ("setup_schema","tbl_icd10","creates"),
    ("setup_schema","tbl_boot","creates"), ("setup_schema","tbl_kg","creates"),
    ("setup_res","ka_svc","provisions"),   ("setup_res","vs_svc","provisions"),
    ("setup_res","genie_svc","provisions"),
    ("wfy","nb01","triggers"), ("wfy","nb02r","triggers"), ("wfy","nb02p","triggers"),
    ("wfy","nb03","triggers"), ("wfy","nb04","triggers"),
    ("wfy","nb06","triggers"), ("wfy","nb07","triggers"),
    ("nb02r","tbl_rules","writes_to"),  ("nb02p","tbl_pts","writes_to"),
    ("nb06","vs_svc","configures"),     ("nb07","genie_svc","configures"),
    ("nb04","ka_svc","configures"),
    ("db","tbl_pts","reads"),   ("db","tbl_rules","reads"),
    ("db","tbl_gaps","reads_writes"), ("db","tbl_icd10","reads_writes"),
    ("db","tbl_boot","reads"),
    ("tab_icd10","ka_svc","calls"),
    ("tab_caregap","vs_svc","calls"),  ("tab_caregap","fmapi_svc","calls"),
    ("tab_genie","genie_svc","calls"),
    ("tab_kg","fmapi_svc","calls"),    ("tab_kg","tbl_kg","reads_writes"),
]

# ---------------------------------------------------------------------------
# Cytoscape stylesheet
# ---------------------------------------------------------------------------
def _build_stylesheet() -> list:
    ss = [
        {"selector": "node", "style": {
            "background-color": "#0D1F30", "border-width": "2px",
            "border-color": "#1A3248", "color": "#D6EAF8",
            "content": "data(name)", "text-wrap": "wrap",
            "text-max-width": "110px", "font-size": "10px",
            "font-family": "Segoe UI, system-ui, sans-serif",
            "text-halign": "center", "text-valign": "center",
            "width": "130px", "height": "50px",
            "shape": "roundrectangle", "padding": "8px",
        }},
        {"selector": "node:selected", "style": {
            "border-width": "3px", "background-color": "#122840",
        }},
        {"selector": "edge", "style": {
            "line-color": "#2E6090", "target-arrow-color": "#4A85B5",
            "target-arrow-shape": "triangle", "curve-style": "bezier",
            "opacity": 0.8, "width": 1.5,
        }},
        {"selector": "edge:selected", "style": {
            "opacity": 1, "line-color": "#4FC3F7", "width": 2,
        }},
    ]
    for layer, color in _LAYER_COLORS.items():
        ss.append({"selector": f'[layer = "{layer}"]',
                   "style": {"border-color": color}})
    return ss


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def kg_layout() -> html.Div:
    # Load existing graph immediately so Cytoscape is pre-populated on first render
    graph    = _load_latest_graph()
    elements = _graph_to_elements(graph) if graph else []
    ts       = graph.pop("_created_at", "") if graph else ""
    last_upd = f"Last updated: {ts}" if ts else "No graph yet — click Update Knowledge Graph"

    return html.Div([
        dbc.Row([
            dbc.Col([
                html.H5([html.I(className="fa-solid fa-diagram-project me-2"),
                         "Knowledge Graph"], className="mb-0 fw-bold"),
                html.Small("Visual map of project components and their relationships",
                           className="text-muted"),
            ]),
            dbc.Col([
                dbc.Button(
                    [html.I(className="fa-solid fa-rotate me-2"),
                     "Update Knowledge Graph"],
                    id="kg-update-btn", color="primary", size="sm",
                ),
                html.Small(id="kg-last-updated", children=last_upd,
                           className="text-muted ms-3 d-block mt-1",
                           style={"fontSize": "11px"}),
            ], width="auto", className="ms-auto text-end"),
        ], align="center", className="mb-3"),

        # Progress bar — visible only while generating
        html.Div([
            html.Small([html.I(className="fa-solid fa-spinner fa-spin me-2"),
                        "Graph generation in progress — calling Claude Sonnet…"],
                       className="text-info d-block mb-1",
                       style={"fontSize": "12px"}),
            dbc.Progress(value=100, striped=True, animated=True,
                         color="info", style={"height": "6px"}),
        ], id="kg-progress-div", style={"display": "none"}, className="mb-2"),

        html.Div(id="kg-status-msg", className="mb-2"),

        # Filter bar + Auto Arrange
        dbc.Row([
            dbc.Col([
                html.Small("Filter by layer: ", className="text-muted fw-bold me-2",
                           style={"fontSize": "10px"}),
                dbc.Button(
                    "All", id="kg-filter-all-btn", size="sm", n_clicks=0,
                    color="secondary", outline=False,
                    className="me-1",
                    style={"fontSize": "9px", "padding": "2px 8px",
                           "borderRadius": "20px"},
                ),
                *[
                    dbc.Button(
                        layer,
                        id={"type": "kg-layer-btn", "index": layer},
                        size="sm", n_clicks=0,
                        outline=True, color="secondary",
                        className="me-1",
                        style={"fontSize": "9px", "padding": "2px 8px",
                               "borderRadius": "20px",
                               "borderColor": color, "color": color},
                    )
                    for layer, color in _LAYER_COLORS.items()
                ],
            ], width="auto"),
            dbc.Col(
                dbc.Button(
                    [html.I(className="fa-solid fa-wand-magic-sparkles me-1"),
                     "Auto Arrange"],
                    id="kg-arrange-btn", size="sm",
                    color="outline-secondary", n_clicks=0,
                    style={"fontSize": "11px"},
                ),
                width="auto", className="ms-auto",
            ),
        ], align="center", className="mb-2"),

        dbc.Card([
            cyto.Cytoscape(
                id="kg-cytoscape",
                layout={"name": "cose", "animate": True, "padding": 60,
                        "nodeRepulsion": 400000, "nodeOverlap": 20,
                        "idealEdgeLength": 120, "edgeElasticity": 0.45,
                        "gravity": 0.25, "numIter": 1000,
                        "initialTemp": 800, "coolingFactor": 0.95},
                style={"width": "100%", "height": "70vh", "background": "#091420"},
                elements=elements,
                stylesheet=_build_stylesheet(),
                responsive=True,
            ),
        ], style={"border": "1px solid #1A3248"}),

        html.Div(id="kg-node-detail", className="mt-2"),

        dcc.Store(id="kg-graph-store",             data=None),
        dcc.Store(id="kg-progress-store",          data=None),
        dcc.Store(id="kg-all-elements-store",      data=elements),
        dcc.Store(id="kg-selected-layers-store",   data=[]),
        dcc.Store(id="kg-arrange-store",           data=0),
    ], className="px-2 py-3")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_file_safe(rel_path: str, max_chars: int = 500) -> str:
    import os
    base = os.path.dirname(os.path.abspath(__file__))
    full = os.path.join(base, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()[:max_chars]
    except Exception:
        return ""


def _call_fmapi(prompt: str) -> str:
    result = w.api_client.do(
        "POST",
        f"/serving-endpoints/{FMAPI_ENDPOINT}/invocations",
        body={"messages": [{"role": "user", "content": prompt}],
              "max_tokens": 3500, "temperature": 0.1},
    )
    return result.get("choices", [{}])[0].get("message", {}).get("content", "")


def _extract_json(text: str) -> dict:
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No valid JSON found in LLM response. First 200 chars: {text[:200]}")


def _build_prompt() -> str:
    manifest = "\n".join(
        f"  [{f['layer']}] id={f['id']}  name={f['name']}"
        for f in _FILE_MANIFEST
    )
    edges = "\n".join(
        f"  {src} --{etype}--> {tgt}"
        for src, tgt, etype in _KNOWN_EDGES
    )
    config_snip    = _read_file_safe("config.py",                 400)
    workflows_snip = _read_file_safe("../resources/workflows.yml", 400)
    databricks_snip= _read_file_safe("../databricks.yml",          300)

    return f"""You are documenting the icd10-gapanalysis-demo project — a Databricks App for clinical ICD-10 coding and care gap analysis.

PROJECT FILES (id → name → layer):
{manifest}

KNOWN DEPENDENCY EDGES (source --type--> target):
{edges}

KEY FILE SNIPPETS:
=== config.py ===
{config_snip}
=== workflows.yml ===
{workflows_snip}
=== databricks.yml ===
{databricks_snip}

Return ONLY a valid JSON object with no extra text, no markdown, no explanation.
Use EXACTLY the node ids listed above. Include ALL nodes. Include ALL edges listed.

JSON schema (return this exact structure):
{{
  "nodes": [
    {{"id":"app","name":"app.py","layer":"App UI","description":"Dash app entry point — routing navbar and global layout","tags":["dash","routing"]}}
  ],
  "edges": [
    {{"source":"app","target":"tab_home","type":"imports","label":"imports"}}
  ]
}}
"""


def _graph_to_elements(graph: dict) -> list:
    elements = []
    for node in graph.get("nodes", []):
        elements.append({"data": {
            "id":          node["id"],
            "name":        node.get("name", node["id"]),
            "layer":       node.get("layer", ""),
            "description": node.get("description", ""),
            "tags":        ", ".join(node.get("tags", [])) if isinstance(node.get("tags"), list) else "",
        }})
    for edge in graph.get("edges", []):
        if edge.get("source") and edge.get("target"):
            elements.append({"data": {
                "source": edge["source"],
                "target": edge["target"],
                "label":  edge.get("label", edge.get("type", "")),
            }})
    return elements


def _load_latest_graph() -> dict | None:
    try:
        rows = execute_sql(
            f"SELECT graph_json, created_at FROM `{CATALOG}`.`{SCHEMA}`.`knowledge_graph` "
            f"ORDER BY version DESC LIMIT 1"
        )
        if rows:
            g = json.loads(rows[0]["graph_json"])
            g["_created_at"] = str(rows[0].get("created_at", ""))[:19]
            return g
    except Exception as e:
        logger.warning(f"Could not load knowledge graph: {e}")
    return None


def _save_graph(graph: dict) -> None:
    escaped = json.dumps(graph).replace("'", "''")
    execute_sql(f"""
        INSERT INTO `{CATALOG}`.`{SCHEMA}`.`knowledge_graph`
            (version, created_at, graph_json)
        SELECT COALESCE(MAX(version), 0) + 1, CURRENT_TIMESTAMP(), '{escaped}'
        FROM `{CATALOG}`.`{SCHEMA}`.`knowledge_graph`
    """)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

# Step 1: Button click → immediately show progress bar + disable button
@callback(
    Output("kg-update-btn",     "disabled"),
    Output("kg-progress-div",   "style"),
    Output("kg-status-msg",     "children",  allow_duplicate=True),
    Output("kg-progress-store", "data"),
    Input("kg-update-btn",      "n_clicks"),
    prevent_initial_call=True,
)
def start_generation(n_clicks):
    if not n_clicks:
        return False, {"display": "none"}, dash.no_update, dash.no_update
    return True, {"display": "block"}, None, "generating"


# Step 2: Progress store → call FMAPI, save, render
@callback(
    Output("kg-cytoscape",          "elements",   allow_duplicate=True),
    Output("kg-last-updated",       "children",   allow_duplicate=True),
    Output("kg-status-msg",         "children",   allow_duplicate=True),
    Output("kg-update-btn",         "disabled",   allow_duplicate=True),
    Output("kg-progress-div",       "style",      allow_duplicate=True),
    Output("kg-progress-store",     "data",       allow_duplicate=True),
    Output("kg-all-elements-store", "data",       allow_duplicate=True),
    Input("kg-progress-store",      "data"),
    prevent_initial_call=True,
)
def run_generation(store_data):
    _hidden = {"display": "none"}
    if store_data != "generating":
        return (dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, dash.no_update, dash.no_update)
    try:
        prompt   = _build_prompt()
        raw      = _call_fmapi(prompt)
        logger.info(f"FMAPI raw response length: {len(raw)}")
        graph    = _extract_json(raw)
        _save_graph(graph)
        elements = _graph_to_elements(graph)
        n_nodes  = len(graph.get("nodes", []))
        n_edges  = len(graph.get("edges", []))
        msg = dbc.Alert(
            [html.I(className="fa-solid fa-circle-check me-2 text-success"),
             f"Graph updated — {n_nodes} nodes, {n_edges} edges"],
            color="success", className="py-2 small",
        )
        return elements, "Just now", msg, False, _hidden, None, elements
    except Exception as e:
        logger.error(f"Knowledge graph generation failed: {e}")
        msg = dbc.Alert([
            html.I(className="fa-solid fa-circle-xmark me-2 text-danger"),
            html.Strong("Generation failed — "),
            html.Span(str(e)[:180], className="font-monospace",
                      style={"fontSize": "11px"}),
        ], color="danger", className="py-2 small")
        return (dash.no_update, dash.no_update, msg, False,
                _hidden, None, dash.no_update)


# Layer filter — multi-select: toggle layers in/out, show union of selected layers
@callback(
    Output("kg-cytoscape",               "elements",  allow_duplicate=True),
    Output("kg-filter-all-btn",          "color"),
    Output({"type": "kg-layer-btn", "index": ALL}, "outline"),
    Output("kg-selected-layers-store",   "data"),
    Input("kg-filter-all-btn",              "n_clicks"),
    Input({"type": "kg-layer-btn", "index": ALL}, "n_clicks"),
    State("kg-all-elements-store",          "data"),
    State("kg-selected-layers-store",       "data"),
    prevent_initial_call=True,
)
def filter_by_layer(all_n, layer_ns, all_elements, selected_layers):
    layers          = list(_LAYER_COLORS.keys())
    n_layers        = len(layers)
    selected_layers = selected_layers or []

    if not all_elements:
        return dash.no_update, "secondary", [True] * n_layers, []

    triggered = dash.callback_context.triggered_id

    # "All" → clear selection, show everything
    if triggered == "kg-filter-all-btn" or not isinstance(triggered, dict):
        return all_elements, "secondary", [True] * n_layers, []

    # Toggle the clicked layer in/out of the selection
    clicked = triggered.get("index", "")
    if clicked in selected_layers:
        selected_layers = [l for l in selected_layers if l != clicked]
    else:
        selected_layers = selected_layers + [clicked]

    # Nothing selected → show all
    if not selected_layers:
        return all_elements, "secondary", [True] * n_layers, []

    # Collect node ids across ALL selected layers
    selected_set  = set(selected_layers)
    visible_nodes = {
        el["data"]["id"]
        for el in all_elements
        if "source" not in el["data"]
           and el["data"].get("layer") in selected_set
    }

    # Nodes in selected layers + edges where both endpoints are visible
    filtered = [
        el for el in all_elements
        if ("source" not in el["data"] and el["data"].get("layer") in selected_set)
        or ("source" in el["data"]
            and el["data"]["source"] in visible_nodes
            and el["data"]["target"] in visible_nodes)
    ]

    outline_states = [layer not in selected_set for layer in layers]
    return filtered, "outline-secondary", outline_states, selected_layers


# Auto Arrange — re-run layout
@callback(
    Output("kg-cytoscape", "layout", allow_duplicate=True),
    Input("kg-arrange-btn", "n_clicks"),
    prevent_initial_call=True,
)
def auto_arrange(n_clicks):
    if not n_clicks:
        return dash.no_update
    # Vary initialTemp slightly each click to force Cytoscape to re-run layout
    return {"name": "cose", "animate": True, "padding": 60,
            "nodeRepulsion": 400000, "nodeOverlap": 20,
            "idealEdgeLength": 120, "edgeElasticity": 0.45,
            "gravity": 0.25, "numIter": 1000,
            "initialTemp": 800 + n_clicks,
            "coolingFactor": 0.95}


# Node click → show detail panel
@callback(
    Output("kg-node-detail", "children"),
    Input("kg-cytoscape",    "tapNodeData"),
    prevent_initial_call=True,
)
def show_node_detail(data):
    if not data:
        return None
    color = _LAYER_COLORS.get(data.get("layer", ""), "#4FC3F7")
    return dbc.Card(dbc.CardBody([
        html.Div([
            html.Strong(data.get("name", ""), style={"color": color, "fontSize": "14px"}),
            dbc.Badge(data.get("layer", ""), pill=True, className="ms-2",
                      style={"background": color, "fontSize": "10px"}),
        ], className="mb-1"),
        html.P(data.get("description", ""), className="small text-muted mb-1"),
        html.Small(f"Tags: {data.get('tags', '')}", className="text-muted",
                   style={"fontSize": "10px"}),
    ], className="py-2"),
        style={"background": "#0D1F30", "border": f"1px solid {color}",
               "borderRadius": "8px"})
