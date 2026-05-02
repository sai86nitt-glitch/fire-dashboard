"""
Transactions page — AG Grid + true inline expansion via Dash callbacks.

Click a row → only the detail panel updates (no page rerun).
Tag editor lives in Python/Dash, not JavaScript hacks.
"""

import re
import dash
from dash import html, dcc, callback, Input, Output, State, no_update, ctx
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import pandas as pd
from datetime import date, timedelta

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import (
    load_transactions, load_tags, batch_update_tags,
    save_rule, fmt_inr, bust,
)

dash.register_page(__name__, path="/transactions", title="Transactions")

# ── Column definitions ────────────────────────────────────────────────────────

COL_DEFS = [
    {"field": "id",          "hide": True},
    {"field": "date_str",    "headerName": "Date",        "width": 105},
    {"field": "amount_str",  "headerName": "Amount",      "width": 130,
     "cellStyle": {"fontFamily": "monospace"},
     "cellClassRules": {
         "amt-expense":  "params.data.type === 'expense'",
         "amt-income":   "params.data.type === 'income'",
         "amt-transfer": "params.data.type === 'transfer'",
     }},
    {"field": "description", "headerName": "Description", "flex": 3, "minWidth": 160},
    {"field": "tags",        "headerName": "Tags",        "flex": 1, "minWidth": 90,
     "cellStyle": {"color": "#888", "fontSize": "11px"}},
    {"field": "account_name","headerName": "Account",     "width": 110,
     "cellStyle": {"color": "#888", "fontSize": "11px"}},
]

# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    txn_df  = load_transactions()
    tags    = load_tags()
    tag_opts = [{"label": t["display"], "value": t["display"]} for t in tags]

    min_date = txn_df["date"].min().date() if not txn_df.empty else date(2020, 1, 1)
    max_date = txn_df["date"].max().date() if not txn_df.empty else date.today()
    default_start = max(min_date, date.today() - timedelta(days=90))

    all_accounts = sorted(txn_df["account_name"].astype(str).dropna().unique()) if not txn_df.empty else []

    return html.Div([
        # ── Header ───────────────────────────────────────────────────────────
        html.H3("🔍 Transactions", style={"marginBottom": "16px"}),

        # ── Metrics ──────────────────────────────────────────────────────────
        dbc.Row(id="txn-metrics", className="mb-3 g-2"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # ── Filters row ──────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                dbc.Label("Date range", size="sm", style={"color": "#888"}),
                dcc.DatePickerRange(
                    id="txn-date",
                    min_date_allowed=min_date,
                    max_date_allowed=max_date,
                    start_date=default_start,
                    end_date=max_date,
                    display_format="DD MMM YYYY",
                    style={"fontSize": "12px"},
                ),
            ], width="auto"),

            dbc.Col([
                dbc.Label("Type", size="sm", style={"color": "#888"}),
                dcc.Dropdown(
                    id="txn-type",
                    options=["expense", "income", "transfer"],
                    value=["expense", "income", "transfer"],
                    multi=True, clearable=False,
                    style={"minWidth": "200px"},
                ),
            ], width="auto"),

            dbc.Col([
                dbc.Label("Account", size="sm", style={"color": "#888"}),
                dcc.Dropdown(
                    id="txn-account",
                    options=all_accounts,
                    value=all_accounts,
                    multi=True, clearable=False,
                    style={"minWidth": "220px"},
                ),
            ], width="auto"),

            dbc.Col([
                dbc.Label("Search", size="sm", style={"color": "#888"}),
                dbc.Input(
                    id="txn-search",
                    placeholder="Swiggy, Amazon, UPI…",
                    debounce=True, size="sm",
                    style={"minWidth": "200px"},
                ),
            ], width="auto"),

            dbc.Col([
                dbc.Label(" ", size="sm"),
                dbc.Checklist(
                    id="txn-untagged",
                    options=[{"label": " Untagged only", "value": "yes"}],
                    value=[], switch=True,
                ),
            ], width="auto", className="d-flex align-items-end"),
        ], className="mb-3 g-2 align-items-end"),

        # ── Grid ─────────────────────────────────────────────────────────────
        dag.AgGrid(
            id="txn-grid",
            columnDefs=COL_DEFS,
            rowData=[],
            dashGridOptions={
                "rowSelection": "single",
                "suppressRowClickSelection": False,
                "animateRows": True,
                "domLayout": "normal",
            },
            defaultColDef={"resizable": True, "sortable": True, "filter": False},
            className="ag-theme-alpine-dark",
            style={"height": "520px"},
            selectedRows=[],
        ),

        # ── Detail panel ─────────────────────────────────────────────────────
        html.Div(id="txn-detail", style={"marginTop": "2px"}),

        # ── Pagination ───────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col(html.Small(id="txn-caption", style={"color": "#666"}), width="auto"),
            dbc.Col(
                dbc.Pagination(id="txn-pages", max_value=1, active_page=1,
                               size="sm", className="mb-0"),
                width="auto", className="ms-auto",
            ),
        ], className="mt-2 align-items-center"),

        # ── Hidden stores ─────────────────────────────────────────────────────
        dcc.Store(id="txn-store"),   # filtered+paginated data as JSON
        dcc.Store(id="txn-page-store", data=1),
        dcc.Store(id="tag-opts-store", data=tag_opts),
        dcc.Store(id="save-status"),
    ])

PAGE_SIZE = 100

# ── Callbacks ─────────────────────────────────────────────────────────────────

@callback(
    Output("txn-store",      "data"),
    Output("txn-metrics",    "children"),
    Output("txn-caption",    "children"),
    Output("txn-pages",      "max_value"),
    Output("txn-detail",     "children", allow_duplicate=True),
    Input("txn-date",        "start_date"),
    Input("txn-date",        "end_date"),
    Input("txn-type",        "value"),
    Input("txn-account",     "value"),
    Input("txn-search",      "value"),
    Input("txn-untagged",    "value"),
    Input("txn-page-store",  "data"),
    prevent_initial_call="initial_duplicate",
)
def filter_data(start, end, types, accounts, search, untagged, page):
    df = load_transactions()
    if df.empty:
        return {}, [], "No data", 1, None

    # Filter
    mask = pd.Series(True, index=df.index)
    if start:
        mask &= df["date"].dt.date >= pd.to_datetime(start).date()
    if end:
        mask &= df["date"].dt.date <= pd.to_datetime(end).date()
    if types:
        mask &= df["type"].isin(types)
    if accounts:
        mask &= df["account_name"].astype(str).isin(accounts)
    if search and search.strip():
        mask &= df["description"].str.contains(search.strip(), case=False, na=False)
    if untagged:
        mask &= df["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"])

    filtered = df[mask].sort_values("date", ascending=False).reset_index(drop=True)
    total    = len(filtered)

    # Metrics
    exp_total  = filtered[filtered["type"] == "expense"]["amount"].sum()
    inc_total  = filtered[filtered["type"] == "income"]["amount"].abs().sum()
    net        = inc_total - exp_total
    n_untagged = filtered["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"]).sum()

    def card(label, value, colour="#e0e0e0"):
        return dbc.Col(html.Div([
            html.Div(label, className="metric-label"),
            html.Div(value, className="metric-value", style={"color": colour}),
        ], className="metric-card"), md=3)

    metrics = [
        card("Transactions",   f"{total:,}"),
        card("Total Expenses", fmt_inr(exp_total), "#ff6b6b"),
        card("Total Income",   fmt_inr(inc_total), "#00c49f"),
        card("Net",            fmt_inr(net),
             "#00c49f" if net >= 0 else "#ff6b6b"),
    ]

    # Paginate
    page     = max(1, page or 1)
    max_page = max(1, -(-total // PAGE_SIZE))
    page     = min(page, max_page)
    start_i  = (page - 1) * PAGE_SIZE
    end_i    = min(start_i + PAGE_SIZE, total)
    page_df  = filtered.iloc[start_i:end_i].copy()

    # Build row data for AG Grid
    icon = {"expense": "←", "income": "+", "transfer": "⇌"}
    rows = []
    for _, r in page_df.iterrows():
        rows.append({
            "id":          str(r.get("id", "")),
            "date_str":    r["date"].strftime("%d %b %Y") if pd.notna(r["date"]) else "",
            "amount_str":  f"{icon.get(r['type'], '←')} {fmt_inr(abs(r['amount']))}",
            "description": str(r.get("description", "")),
            "tags":        str(r.get("tags", "")),
            "account_name":str(r.get("account_name", "")),
            "type":        str(r.get("type", "expense")),
            "raw_amount":  float(r.get("amount", 0)),
            "transfer_to": str(r.get("transfer_to", "")),
            "month":       str(r.get("month", "")),
        })

    caption = f"Showing {start_i+1}–{end_i} of {total:,} transactions"
    if n_untagged:
        caption += f" · {n_untagged} untagged"

    return rows, metrics, caption, max_page, None

@callback(
    Output("txn-grid", "rowData"),
    Input("txn-store",  "data"),
)
def update_grid(rows):
    return rows or []

@callback(
    Output("txn-page-store", "data"),
    Input("txn-pages",       "active_page"),
)
def set_page(p):
    return p or 1

@callback(
    Output("txn-detail", "children"),
    Input("txn-grid",    "selectedRows"),
    State("tag-opts-store", "data"),
    prevent_initial_call=True,
)
def show_detail(selected_rows, tag_opts):
    if not selected_rows:
        return no_update  # keep panel open; only X button closes it
    row = selected_rows[0]

    current_tags = [t.strip() for t in str(row.get("tags", "")).split(",")
                    if t.strip() and t.strip() not in ("nan", "Untagged", "None")]
    valid_defaults = [t for t in current_tags
                      if any(o["value"] == t for o in (tag_opts or []))]

    acct = row["account_name"]
    if row.get("transfer_to") and row["transfer_to"] not in ("", "nan"):
        acct += f" → {row['transfer_to']}"

    merchant_words = [w for w in re.sub(r"[^a-zA-Z\s]", " ",
                      row.get("description", "")).split() if len(w) >= 3]
    merchant_token = merchant_words[0] if merchant_words else ""

    return html.Div([
        # ── Detail header ─────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Div(row["description"],
                         style={"fontWeight": "600", "fontSize": "14px",
                                "color": "#e0e0e0", "marginBottom": "2px"}),
                html.Div(
                    f"{acct}  ·  {row['date_str']}  ·  "
                    f"{row['amount_str']}  ·  {row['type'].capitalize()}",
                    style={"fontSize": "11px", "color": "#888"},
                ),
            ]),
            dbc.Col(
                dbc.Button("✕", id="detail-close", size="sm", color="secondary",
                           outline=True, style={"float": "right"}),
                width="auto",
            ),
        ], className="mb-3 align-items-start"),

        html.Hr(style={"borderColor": "#2a2a3e", "margin": "8px 0"}),

        # ── Tag editor ────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                dcc.Dropdown(
                    id="tag-dropdown",
                    options=tag_opts or [],
                    value=valid_defaults,
                    multi=True,
                    placeholder="Search or pick a category…",
                    style={"fontSize": "13px"},
                ),
                dbc.Checklist(
                    id="rule-check",
                    options=[{"label": f' Auto-tag future "{merchant_token}" transactions',
                              "value": "yes"}] if merchant_token else [],
                    value=[],
                    className="mt-2",
                    style={"fontSize": "12px", "color": "#aaa"},
                ),
            ], md=9),
            dbc.Col([
                dbc.Button("✓ Save Tag", id="tag-save-btn", color="primary",
                           size="sm", className="w-100 mb-2",
                           style={"fontSize": "13px"}),
                dbc.Button("✕ Close", id="detail-close-2", color="secondary",
                           outline=True, size="sm", className="w-100",
                           style={"fontSize": "13px"}),
            ], md=3),
        ]),

        # ── Save feedback ─────────────────────────────────────────────────
        html.Div(id="save-feedback", className="mt-2"),

        # ── Hidden: current row id ────────────────────────────────────────
        dcc.Store(id="detail-row-id", data=row.get("id")),
        dcc.Store(id="detail-merchant", data=merchant_token),

    ], id="txn-detail-inner", style={
        "padding": "14px 18px",
        "background": "#1a1a2e",
        "borderLeft": "3px solid #6c63ff",
        "borderRadius": "0 6px 6px 0",
        "marginTop": "2px",
    })

@callback(
    Output("save-feedback",  "children"),
    Output("txn-grid",       "rowData", allow_duplicate=True),
    Output("txn-detail",     "children", allow_duplicate=True),
    Input("tag-save-btn",    "n_clicks"),
    State("tag-dropdown",    "value"),
    State("detail-row-id",   "data"),
    State("detail-merchant", "data"),
    State("rule-check",      "value"),
    State("txn-store",       "data"),
    prevent_initial_call=True,
)
def save_tag(n_clicks, chosen_tags, row_id, merchant, rule_check, row_data):
    if not n_clicks or not chosen_tags or not row_id:
        return no_update, no_update, no_update

    tags_str = ", ".join(chosen_tags)
    saved, failed = batch_update_tags({row_id: tags_str})

    if not saved:
        return dbc.Alert("❌ Save failed — ID not found in sheet.",
                         color="danger", duration=4000), no_update, no_update

    if rule_check and merchant:
        save_rule("description", merchant, tags_str)

    # Update the grid row in-place without refetching from Sheets
    updated = []
    for r in (row_data or []):
        if r.get("id") == row_id:
            r = {**r, "tags": tags_str}
        updated.append(r)

    feedback = dbc.Alert(
        f'✅ Saved: {tags_str}' + (f' + rule for "{merchant}"' if rule_check and merchant else ''),
        color="success", duration=3000,
    )
    return feedback, updated, no_update

@callback(
    Output("txn-detail", "children", allow_duplicate=True),
    Output("txn-grid",   "selectedRows"),
    Input("detail-close",   "n_clicks"),
    Input("detail-close-2", "n_clicks"),
    prevent_initial_call=True,
)
def close_detail(n1, n2):
    return None, []
