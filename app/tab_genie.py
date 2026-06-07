import time
from dash import dcc, html, callback, Input, Output, State
import dash
import dash_bootstrap_components as dbc

from config import GENIE_SPACE_ID, CATALOG, SCHEMA

_SUGGESTED = [
    "How many patients have HIGH priority care gaps?",
    "Which conditions have the most care gaps?",
    "Show top 5 saved ICD-10 codes",
    "How many patients have missing ICD-10 codes?",
]

# ---------------------------------------------------------------------------
# Layout — panel content (rendered inside the fixed drawer in app.py)
# ---------------------------------------------------------------------------
def genie_panel() -> html.Div:
    not_ready = not GENIE_SPACE_ID
    return html.Div([
        # ── Header ──────────────────────────────────────────────────────────
        html.Div([
            html.Div([
                html.I(className="fa-solid fa-comments me-2",
                       style={"color": "#4FC3F7"}),
                html.Span("Patient Data Assistant",
                          className="fw-bold",
                          style={"fontSize": "14px", "color": "#D6EAF8"}),
            ], className="d-flex align-items-center"),
            dbc.Button(
                html.I(className="fa-solid fa-xmark"),
                id="genie-close-btn", size="sm",
                style={"background": "transparent", "border": "none",
                       "color": "#6E93AD", "padding": "2px 6px"},
            ),
        ], className="d-flex align-items-center justify-content-between px-3 py-2",
           style={"borderBottom": "1px solid #1A3248",
                  "background": "#122840"}),

        # ── Setup warning ───────────────────────────────────────────────────
        html.Div(
            dbc.Alert([
                html.I(className="fa-solid fa-triangle-exclamation me-2"),
                "Genie Space not configured. Run Job 1 to enable.",
            ], color="warning", className="m-2 py-2 small"),
            id="genie-setup-warning",
            style={"display": "block" if not_ready else "none"},
        ),

        # ── Suggested questions (top) ────────────────────────────────────
        html.Div([
            html.Div("Try asking", className="text-muted mb-2",
                     style={"fontSize": "11px", "letterSpacing": "0.5px",
                            "textTransform": "uppercase"}),
            html.Div([
                dbc.Button(q, id={"type": "genie-suggestion", "index": i},
                           size="sm", outline=True, color="info",
                           className="mb-1 text-start",
                           style={"fontSize": "13px", "width": "100%",
                                  "textAlign": "left", "whiteSpace": "normal"})
                for i, q in enumerate(_SUGGESTED)
            ]),
        ], id="genie-suggestions", className="px-3 py-2",
           style={"borderBottom": "1px solid #1A3248"}),

        # ── Chat history ─────────────────────────────────────────────────
        html.Div(
            id="genie-chat-history",
            children=_welcome_msg(),
            style={
                "flex": "1",
                "overflowY": "auto",
                "padding": "12px",
                "display": "flex",
                "flexDirection": "column",
                "gap": "10px",
            },
        ),

        # ── Typing indicator ─────────────────────────────────────────────
        html.Div(
            html.Div([
                html.Span("●", style={"animationDelay": "0s"}),
                html.Span("●", style={"animationDelay": "0.2s"}),
                html.Span("●", style={"animationDelay": "0.4s"}),
            ], className="genie-typing-dots"),
            id="genie-typing",
            style={"display": "none", "padding": "4px 12px"},
        ),

        # ── Input area ───────────────────────────────────────────────────
        html.Div([
            dbc.InputGroup([
                dbc.Textarea(
                    id="genie-input",
                    placeholder="Ask about patients, ICD-10 codes, care gaps… (Enter to send)",
                    disabled=not_ready,
                    n_submit=0,
                    style={
                        "background": "#122840",
                        "border": "1px solid #1A3248",
                        "color": "#D6EAF8",
                        "fontSize": "14px",
                        "resize": "none",
                        "minHeight": "60px",
                        "maxHeight": "100px",
                        "borderRadius": "8px 0 0 8px",
                    },
                ),
                dbc.Button(
                    html.I(className="fa-solid fa-paper-plane"),
                    id="genie-send-btn",
                    color="primary",
                    disabled=not_ready,
                    style={"borderRadius": "0 8px 8px 0"},
                ),
            ]),
        ], className="px-3 pb-3 pt-2",
           style={"borderTop": "1px solid #1A3248"}),

        # ── Stores + interval ────────────────────────────────────────────
        dcc.Store(id="genie-msg-store", data={
            "history": [],
            "conversation_id": None,
            "pending_message_id": None,
        }),
        dcc.Interval(id="genie-poll-interval", interval=2500, disabled=True),
    ], style={"display": "flex", "flexDirection": "column", "height": "100%"})


def _welcome_msg():
    return [
        html.Div([
            html.I(className="fa-solid fa-robot me-2",
                   style={"color": "#4FC3F7"}),
            html.Span("Hello! I can answer questions about your patient data, "
                      "ICD-10 analysis results, and care gap findings. "
                      "Try a suggested question or ask your own.",
                      style={"fontSize": "14px", "color": "#D6EAF8"}),
        ], style={
            "background": "#0D1F30",
            "border": "1px solid #1A3248",
            "borderRadius": "8px",
            "padding": "10px 12px",
        }),
    ]


def _user_bubble(text: str) -> html.Div:
    return html.Div(
        html.Span(text, style={"fontSize": "14px", "color": "#D6EAF8"}),
        style={
            "background": "#0288D1",
            "borderRadius": "12px 12px 2px 12px",
            "padding": "8px 12px",
            "alignSelf": "flex-end",
            "maxWidth": "85%",
        },
    )


def _ai_bubble(text: str, is_error: bool = False) -> html.Div:
    colour = "#1A0808" if is_error else "#0D1F30"
    border = "#4A1010" if is_error else "#1A3248"
    content = (html.Span(text, style={"fontSize": "14px", "color": "#D6EAF8"})
               if is_error else
               dcc.Markdown(text, style={"fontSize": "14px", "color": "#D6EAF8",
                                         "marginBottom": "0"}))
    return html.Div([
        html.I(className="fa-solid fa-robot me-2 flex-shrink-0",
               style={"color": "#4FC3F7", "fontSize": "11px",
                      "marginTop": "3px"}),
        html.Div(content, style={"minWidth": "0", "flex": "1"}),
    ], style={
        "background": colour,
        "border": f"1px solid {border}",
        "borderRadius": "12px 12px 12px 2px",
        "padding": "8px 12px",
        "alignSelf": "flex-start",
        "maxWidth": "95%",
        "display": "flex",
        "alignItems": "flex-start",
        "gap": "6px",
    })


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
def _msg_id(result: dict) -> str:
    """Extract message ID — handles both flat and nested response formats."""
    return (result.get("message_id")
            or result.get("id")
            or (result.get("message") or {}).get("id", ""))


def _msg_status(result: dict) -> str:
    return (result.get("status")
            or (result.get("message") or {}).get("status", "EXECUTING"))


def _query_genie(space_id: str, question: str,
                 conversation_id: str | None) -> tuple[str, str, str]:
    """Send a message to Genie. Returns (conversation_id, message_id, status)."""
    from config import w as workspace_client
    if conversation_id:
        result = workspace_client.api_client.do(
            "POST",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages",
            body={"content": question},
        )
        return conversation_id, _msg_id(result), _msg_status(result)
    else:
        result = workspace_client.api_client.do(
            "POST",
            f"/api/2.0/genie/spaces/{space_id}/start-conversation",
            body={"content": question},
        )
        conv_id = result.get("conversation_id", "")
        msg     = result.get("message") or result
        return conv_id, _msg_id(msg), _msg_status(msg)


def _poll_genie(space_id: str, conversation_id: str, message_id: str) -> dict:
    """Poll Genie for message status. Returns the message dict."""
    from config import w as workspace_client
    return workspace_client.api_client.do(
        "GET",
        f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
    )


def _extract_answer(msg: dict) -> str:
    """Extract human-readable answer from a COMPLETED Genie message.
    Attachment type is implied by which key is present — there is no 'type' field.
    """
    attachments = msg.get("attachments", [])
    parts = []
    for att in attachments:
        if "text" in att:
            content = (att.get("text") or {}).get("content", "")
            if content:
                parts.append(content)
        elif "query" in att:
            desc = (att.get("query") or {}).get("description", "")
            if desc:
                parts.append(f"📊 {desc}")
        # "suggested_questions" attachments are skipped — not displayed in chat
    return "\n\n".join(parts) if parts else "No response received."


@callback(
    Output("genie-chat-history",   "children"),
    Output("genie-msg-store",      "data"),
    Output("genie-input",          "value"),
    Output("genie-poll-interval",  "disabled"),
    Output("genie-typing",         "style"),
    Output("genie-suggestions",    "style"),
    Input("genie-send-btn",        "n_clicks"),
    Input("genie-input",           "n_submit"),
    Input({"type": "genie-suggestion", "index": dash.ALL}, "n_clicks"),
    State("genie-input",           "value"),
    State("genie-msg-store",       "data"),
    prevent_initial_call=True,
)
def send_message(send_n, n_submit, suggestion_clicks, user_input, store):
    triggered = dash.callback_context.triggered_id

    question = ""
    if triggered in ("genie-send-btn", "genie-input"):
        question = (user_input or "").strip()
    elif isinstance(triggered, dict) and triggered.get("type") == "genie-suggestion":
        idx = triggered["index"]
        question = _SUGGESTED[idx]

    if not question or not GENIE_SPACE_ID:
        return (dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, dash.no_update, dash.no_update)

    store = store or {"history": [], "conversation_id": None, "pending_message_id": None}
    store["history"].append({"role": "user", "content": question})

    try:
        conv_id, msg_id, status = _query_genie(
            GENIE_SPACE_ID, question, store.get("conversation_id"))
        store["conversation_id"]    = conv_id
        store["pending_message_id"] = msg_id
    except Exception as e:
        store["history"].append({"role": "ai", "content": f"Error contacting Genie: {e}", "error": True})
        no_suggest = {"borderTop": "1px solid #1A3248", "padding": "8px 12px"}
        return _render_history(store["history"]), store, "", True, {"display": "none"}, no_suggest

    # Show user message immediately; typing indicator appears below history
    typing_style    = {"display": "block", "padding": "4px 12px"}
    suggestions_style = {"display": "none"}
    return _render_history(store["history"]), store, "", False, typing_style, suggestions_style


_DONE_SUGGESTIONS = {"borderTop": "1px solid #1A3248", "padding": "8px 12px"}


@callback(
    Output("genie-chat-history",  "children",  allow_duplicate=True),
    Output("genie-msg-store",     "data",      allow_duplicate=True),
    Output("genie-poll-interval", "disabled",  allow_duplicate=True),
    Output("genie-typing",        "style",     allow_duplicate=True),
    Output("genie-suggestions",   "style",     allow_duplicate=True),
    Input("genie-poll-interval",  "n_intervals"),
    State("genie-msg-store",      "data"),
    prevent_initial_call=True,
)
def poll_response(n_intervals, store):
    if not store or not store.get("pending_message_id"):
        return dash.no_update, dash.no_update, True, dash.no_update, dash.no_update

    space_id = GENIE_SPACE_ID
    conv_id  = store.get("conversation_id", "")
    msg_id   = store.get("pending_message_id", "")

    try:
        msg    = _poll_genie(space_id, conv_id, msg_id)
        status = (msg.get("status") or "EXECUTING").upper()
    except Exception as e:
        store["history"].append({"role": "ai", "content": f"Polling error: {e}", "error": True})
        store["pending_message_id"] = None
        return _render_history(store["history"]), store, True, {"display": "none"}, _DONE_SUGGESTIONS

    if status in ("COMPLETED", "COMPLETE"):
        answer = _extract_answer(msg)
        store["history"].append({"role": "ai", "content": answer})
        store["pending_message_id"] = None
        return _render_history(store["history"]), store, True, {"display": "none"}, _DONE_SUGGESTIONS

    if status in ("FAILED", "CANCELLED", "CANCELED", "ERROR"):
        store["history"].append({
            "role": "ai",
            "content": f"Genie could not answer this question (status: {status}). Please try rephrasing.",
            "error": True,
        })
        store["pending_message_id"] = None
        return _render_history(store["history"]), store, True, {"display": "none"}, _DONE_SUGGESTIONS

    # Still executing — keep polling
    return dash.no_update, dash.no_update, False, dash.no_update, dash.no_update


def _render_history(history: list) -> list:
    bubbles = []
    for msg in history:
        if msg["role"] == "user":
            bubbles.append(_user_bubble(msg["content"]))
        else:
            bubbles.append(_ai_bubble(msg["content"], msg.get("error", False)))
    return bubbles or _welcome_msg()
