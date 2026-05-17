"""
Expenses page — monthly breakdown with category drill-down.
Click any chart element to filter the transactions list at the bottom.
"""

import re
import dash
from dash import html, dcc, callback, Input, Output, State, ctx, no_update
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import load_transactions, load_tags, batch_update_tags, save_rule, update_transaction, fmt_inr

_INPUT_STYLE = {
    "background": "#1a1a2e", "color": "#e0e0e0",
    "border": "1px solid #2a2a3e", "fontSize": "13px",
}

dash.register_page(__name__, path="/expenses", title="Expenses")

_TOP_N_CATS = 15

def _blank():
    f = go.Figure()
    f.update_layout(paper_bgcolor="#1e1e2e", plot_bgcolor="#1e1e2e",
                    xaxis={"visible": False}, yaxis={"visible": False},
                    margin={"t": 10, "b": 10, "l": 10, "r": 10})
    return f

def _top_cats_with_others(expenses, n=_TOP_N_CATS):
    """Return (DataFrame with top n cats + Others row, set of top cat names)."""
    totals = expenses.groupby("primary_tag")["amount"].sum().sort_values(ascending=False)
    top    = totals.head(n)
    top_names = set(top.index.tolist())
    if len(totals) > n:
        others_sum = totals.iloc[n:].sum()
        top = pd.concat([top, pd.Series({"Others": others_sum})])
    df = top.reset_index()
    df.columns = ["primary_tag", "amount"]
    return df, top_names

# ── Shared column defs for drill-down grid ────────────────────────────────────

_TXN_COLS = [
    {"field": "id",           "hide": True},
    {"field": "date_raw",     "hide": True},
    {"field": "amount_raw",   "hide": True},
    {"field": "date_str",     "headerName": "Date",        "width": 105,
     "filter": "agTextColumnFilter"},
    {"field": "amount_str",   "headerName": "Amount",      "width": 130,
     "cellStyle": {"fontFamily": "monospace"},
     "cellClassRules": {
         "amt-expense":  "params.data.type === 'expense'",
         "amt-income":   "params.data.type === 'income'",
     },
     "filter": "agTextColumnFilter"},
    {"field": "description",  "headerName": "Description", "flex": 3, "minWidth": 160,
     "filter": "agTextColumnFilter"},
    {"field": "tags",         "headerName": "Tags",        "flex": 1, "minWidth": 90,
     "cellStyle": {"color": "#888", "fontSize": "11px"},
     "filter": "agTextColumnFilter"},
    {"field": "account_name", "headerName": "Account",     "width": 110,
     "cellStyle": {"color": "#888", "fontSize": "11px"},
     "filter": "agTextColumnFilter"},
]

# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    txn_df = load_transactions()
    if txn_df.empty:
        min_date, max_date = date(2020, 1, 1), date.today()
    else:
        min_date = txn_df["date"].min().date()
        max_date = txn_df["date"].max().date()

    default_start = max(min_date, date.today() - timedelta(days=365))

    tags     = load_tags()
    tag_opts = [{"label": t["display"], "value": t["display"]} for t in tags]

    return html.Div([
        html.H3("💸 Expenses", style={"marginBottom": "16px"}),

        # Filters
        dbc.Row([
            dbc.Col([
                dbc.Label("Date range", size="sm", style={"color": "#888"}),
                dcc.DatePickerRange(
                    id="exp-date",
                    min_date_allowed=min_date,
                    max_date_allowed=max_date,
                    start_date=default_start,
                    end_date=max_date,
                    display_format="DD MMM YYYY",
                    style={"fontSize": "12px"},
                ),
            ], width="auto"),
            dbc.Col([
                dbc.Label("Group by", size="sm", style={"color": "#888"}),
                dcc.Dropdown(
                    id="exp-group",
                    options=[
                        {"label": "Month",    "value": "month"},
                        {"label": "Category", "value": "category"},
                        {"label": "Account",  "value": "account"},
                    ],
                    value="month",
                    clearable=False,
                    style={"minWidth": "160px"},
                ),
            ], width="auto"),
        ], className="mb-3 g-2 align-items-end"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # Metrics row
        dbc.Row(id="exp-metrics", className="mb-3 g-2 fade-up", **{"data-delay": "0"}),

        # Charts
        dbc.Row([
            dbc.Col(html.Div(dcc.Graph(id="exp-bar",  figure=_blank(),
                             config={"displayModeBar": False}), className="chart-wrap"), md=8),
            dbc.Col(html.Div(dcc.Graph(id="exp-pie",  figure=_blank(),
                             config={"displayModeBar": False}), className="chart-wrap"), md=4),
        ], className="mb-3 g-2 fade-up", **{"data-delay": "80"}),

        dbc.Row([
            dbc.Col(html.Div(dcc.Graph(id="exp-treemap", figure=_blank(),
                             config={"displayModeBar": False}), className="chart-wrap"), md=12),
        ], className="mb-3 g-2 fade-up", **{"data-delay": "0"}),

        # ── Transactions drill-down ───────────────────────────────────────────
        html.Hr(style={"borderColor": "#2a2a3e"}),

        # Filter bar
        dbc.Row([
            dbc.Col(
                dbc.InputGroup([
                    dbc.InputGroupText("🔍",
                        style={"background": "#1a1a2e", "border": "1px solid #2a2a3e",
                               "color": "#888", "fontSize": "13px"}),
                    dbc.Input(id="exp-txn-search", placeholder="Search transactions…",
                              debounce=True,
                              style={**_INPUT_STYLE, "fontSize": "12px"}),
                ]),
                md=5,
            ),
            dbc.Col(
                dbc.Select(
                    id="exp-txn-filter-field",
                    options=[
                        {"label": "All fields",  "value": "all"},
                        {"label": "Description", "value": "description"},
                        {"label": "Tags",        "value": "tags"},
                        {"label": "Account",     "value": "account_name"},
                        {"label": "Date",        "value": "date_str"},
                    ],
                    value="all",
                    style={**_INPUT_STYLE, "fontSize": "12px"},
                ),
                md=3,
            ),
            dbc.Col(
                html.Div(id="exp-txn-label",
                         style={"color": "#aaa", "fontSize": "12px", "fontStyle": "italic"}),
                width="auto",
            ),
            dbc.Col(
                dbc.Button("✕ Clear", id="exp-txn-clear", size="sm",
                           color="secondary", outline=True, style={"fontSize": "11px"}),
                width="auto", className="ms-auto",
            ),
        ], className="mb-2 align-items-center"),

        dag.AgGrid(
            id="exp-txn-grid",
            columnDefs=_TXN_COLS,
            rowData=[],
            dashGridOptions={
                "domLayout": "autoHeight",
                "animateRows": True,
                "rowSelection": "multiple",
                "rowMultiSelectWithClick": True,
                "suppressRowClickSelection": False,
                "suppressCellFocus": True,
                "suppressDoubleClickEdit": True,
            },
            defaultColDef={"resizable": True, "sortable": True},
            className="ag-theme-alpine-dark",
            selectedRows=[],
        ),
        html.Small(id="exp-txn-caption", style={"color": "#666", "marginTop": "4px", "display": "block"}),

        # ── Transaction editor modal ──────────────────────────────────────────
        dbc.Modal([
            dbc.ModalHeader([
                html.Div([
                    html.Div(id="exp-modal-desc",
                             style={"fontWeight": "600", "fontSize": "14px",
                                    "color": "#e0e0e0"}),
                    html.Div(id="exp-modal-meta",
                             style={"fontSize": "11px", "color": "#888", "marginTop": "2px"}),
                ]),
            ], close_button=True),
            dbc.ModalBody([
                dbc.Label("Description", size="sm",
                           style={"color": "#aaa", "marginBottom": "2px"}),
                dbc.Input(id="exp-modal-description", type="text",
                          className="mb-3", style=_INPUT_STYLE),

                dbc.Row([
                    dbc.Col([
                        dbc.Label("Date", size="sm",
                                  style={"color": "#aaa", "marginBottom": "2px"}),
                        dbc.Input(id="exp-modal-date", type="date",
                                  className="mb-3",
                                  style={**_INPUT_STYLE, "fontSize": "12px"}),
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Amount (₹)", size="sm",
                                  style={"color": "#aaa", "marginBottom": "2px"}),
                        dbc.Input(id="exp-modal-amount", type="number",
                                  min=0, step=1, className="mb-3",
                                  style={**_INPUT_STYLE, "fontSize": "12px"}),
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Type", size="sm",
                                  style={"color": "#aaa", "marginBottom": "4px"}),
                        dbc.RadioItems(
                            id="exp-modal-type",
                            options=[
                                {"label": "Expense",  "value": "expense"},
                                {"label": "Income",   "value": "income"},
                                {"label": "Transfer", "value": "transfer"},
                            ],
                            value="expense",
                            inline=True,
                            style={"fontSize": "12px"},
                        ),
                    ], md=4),
                ]),

                html.Hr(style={"borderColor": "#2a2a3e", "margin": "8px 0"}),

                dbc.Label("Tags", size="sm",
                           style={"color": "#aaa", "marginBottom": "2px"}),
                dbc.Row([
                    dbc.Col(
                        dcc.Dropdown(
                            id="exp-row-tag-dropdown",
                            options=tag_opts,
                            value=[],
                            multi=True,
                            placeholder="Search or pick a category…",
                            style={"fontSize": "13px"},
                        ),
                        md=9,
                    ),
                    dbc.Col(
                        dbc.Button("💾 Save", id="exp-row-save-btn",
                                   color="primary", size="sm", className="w-100"),
                        md=3,
                    ),
                ]),
                html.Div(id="exp-row-save-feedback", className="mt-2"),
            ]),
        ], id="exp-tag-modal", is_open=False, centered=True, size="lg"),

        # Stores
        dcc.Store(id="exp-row-tag-opts",        data=tag_opts),
        dcc.Store(id="exp-row-detail-id",       data=None),
        dcc.Store(id="exp-row-detail-merchant", data=None),
        dcc.Store(id="exp-modal-mode",          data="single"),
        dcc.Store(id="exp-edit-ids",            data=[]),
        dcc.Store(id="exp-txn-all-rows",        data=[]),
    ])

# ── Helpers ───────────────────────────────────────────────────────────────────

def _dark_fig(fig):
    fig.update_layout(
        paper_bgcolor="#1e1e2e", plot_bgcolor="#13131f",
        font={"color": "#e0e0e0", "size": 11},
        margin={"t": 40, "b": 36, "l": 12, "r": 12},
    )
    return fig

def _card(label, value, colour="#e0e0e0"):
    return dbc.Col(html.Div([
        html.Div(label, className="metric-label"),
        html.Div(value, className="metric-value", style={"color": colour}),
    ], className="metric-card"), md=3)

def _to_rows(df):
    icon = {"expense": "←", "income": "+", "transfer": "⇌"}
    rows = []
    for _, r in df.sort_values("date", ascending=False).iterrows():
        rows.append({
            "id":           str(r.get("id", "")),
            "date_str":     r["date"].strftime("%d %b %Y") if pd.notna(r["date"]) else "",
            "date_raw":     r["date"].strftime("%Y-%m-%d") if pd.notna(r["date"]) else "",
            "amount_str":   f"{icon.get(r['type'], '←')} {fmt_inr(abs(r['amount']))}",
            "amount_raw":   float(r.get("amount", 0)),
            "description":  str(r.get("description", "")),
            "tags":         str(r.get("tags", "")),
            "account_name": str(r.get("account_name", "")),
            "type":         str(r.get("type", "expense")),
        })
    return rows

def _fmt_month(val):
    try:
        return pd.to_datetime(val + "-01").strftime("%b %Y")
    except Exception:
        return val

# ── Charts callback ───────────────────────────────────────────────────────────

@callback(
    Output("exp-metrics", "children"),
    Output("exp-bar",     "figure"),
    Output("exp-pie",     "figure"),
    Output("exp-treemap", "figure"),
    Input("exp-date",     "start_date"),
    Input("exp-date",     "end_date"),
    Input("exp-group",    "value"),
)
def update_expenses(start, end, group_by):
    df = load_transactions()
    if df.empty:
        empty_fig = _dark_fig(go.Figure())
        return [], empty_fig, empty_fig, empty_fig

    expenses = df[df["type"] == "expense"].copy()
    if start:
        expenses = expenses[expenses["date"].dt.date >= pd.to_datetime(start).date()]
    if end:
        expenses = expenses[expenses["date"].dt.date <= pd.to_datetime(end).date()]

    if expenses.empty:
        empty_fig = _dark_fig(go.Figure())
        return [], empty_fig, empty_fig, empty_fig

    total     = expenses["amount"].sum()
    avg_month = expenses.groupby("month")["amount"].sum().mean()
    top_cat   = expenses.groupby("primary_tag")["amount"].sum().idxmax()

    metrics = [
        _card("Total Expenses",  fmt_inr(total),    "#ff6b6b"),
        _card("Monthly Average", fmt_inr(avg_month),"#ffc107"),
        _card("Transactions",    f"{len(expenses):,}"),
        _card("Top Category",    top_cat,            "#6c63ff"),
    ]

    def _bar_with_values(x_vals, y_vals, hover_x=None):
        """Bar trace that shows ₹ value labels on top of each bar."""
        return go.Bar(
            x=x_vals, y=y_vals,
            marker_color="#6c63ff",
            text=[f"₹{v/1e5:.1f}L" if v >= 1e5 else f"₹{v/1e3:.0f}K" for v in y_vals],
            textposition="outside",
            textfont={"size": 10, "color": "#aaa"},
            cliponaxis=False,
            hovertemplate=(
                "%{text}<extra></extra>" if hover_x is None
                else "%{x}<br>%{text}<extra></extra>"
            ),
        )

    if group_by == "month":
        agg = expenses.groupby("month")["amount"].sum().reset_index().sort_values("month")
        agg["month_label"] = agg["month"].apply(_fmt_month)
        bar = go.Figure([_bar_with_values(agg["month"], agg["amount"])])
        bar.update_xaxes(tickvals=agg["month"], ticktext=agg["month_label"])
        bar.update_layout(title="Expenses by Month — click a bar to drill down",
                          yaxis={"range": [0, agg["amount"].max() * 1.18]})
    elif group_by == "category":
        agg, _ = _top_cats_with_others(expenses)
        bar = go.Figure([_bar_with_values(agg["primary_tag"], agg["amount"])])
        bar.update_layout(title="Expenses by Category — click a bar to drill down",
                          yaxis={"range": [0, agg["amount"].max() * 1.18]})
    else:
        agg = expenses.groupby("account_name")["amount"].sum().sort_values(ascending=False).reset_index()
        bar = go.Figure([_bar_with_values(agg["account_name"], agg["amount"])])
        bar.update_layout(title="Expenses by Account — click a bar to drill down",
                          yaxis={"range": [0, agg["amount"].max() * 1.18]})
    _dark_fig(bar)

    top_df, _ = _top_cats_with_others(expenses)
    pie = go.Figure([go.Pie(
        labels=top_df["primary_tag"], values=top_df["amount"],
        hole=0.4, textinfo="percent+label",
        textfont={"size": 10},
        hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>",
    )])
    pie.update_layout(title="Category Share — click a slice", showlegend=False)
    _dark_fig(pie)

    treemap_data = expenses.groupby(["primary_tag", "month"])["amount"].sum().reset_index()
    treemap_data["parent"] = "Expenses"
    if len(treemap_data) > 0:
        tm = px.treemap(treemap_data, path=["parent", "primary_tag", "month"],
                        values="amount", color="amount",
                        color_continuous_scale=[[0, "#1a1a2e"], [0.5, "#6c63ff"], [1, "#ff6b6b"]])
        tm.update_layout(title="Spend Breakdown — click any cell to drill down")
        tm.update_traces(
            # Show label + short ₹ value inside each block
            texttemplate=(
                "<b>%{label}</b><br>"
                "<span style='font-size:10px'>₹%{value:,.0f}</span>"
            ),
            textfont={"size": 11, "color": "#ffffff"},
            hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>",
        )
    else:
        tm = go.Figure()
    _dark_fig(tm)

    return metrics, bar, pie, tm

# ── Drill-down callback ───────────────────────────────────────────────────────

@callback(
    Output("exp-txn-label",    "children"),
    Output("exp-txn-caption",  "children"),
    Output("exp-txn-all-rows", "data"),
    Input("exp-bar",           "clickData"),
    Input("exp-pie",           "clickData"),
    Input("exp-treemap",       "clickData"),
    Input("exp-txn-clear",     "n_clicks"),
    Input("exp-date",          "start_date"),
    Input("exp-date",          "end_date"),
    Input("exp-group",         "value"),
    prevent_initial_call="initial_duplicate",
)
def drill_transactions(bar_click, pie_click, treemap_click, clear_clicks,
                       start, end, group_by):
    df = load_transactions()
    expenses = df[df["type"] == "expense"].copy() if not df.empty else df
    if start:
        expenses = expenses[expenses["date"].dt.date >= pd.to_datetime(start).date()]
    if end:
        expenses = expenses[expenses["date"].dt.date <= pd.to_datetime(end).date()]

    label = "All expenses in selected range · click a chart to filter"
    triggered = ctx.triggered_id

    def _filter_by_cat(df, val):
        """Filter by category, handling the 'Others' bucket."""
        if val == "Others":
            _, top_names = _top_cats_with_others(df)
            return df[~df["primary_tag"].isin(top_names)], "Others"
        return df[df["primary_tag"] == val], val

    if triggered == "exp-bar" and bar_click:
        val = bar_click["points"][0]["x"]
        if group_by == "month":
            expenses = expenses[expenses["month"] == val]
            label = f"Month: {_fmt_month(val)}"
        elif group_by == "category":
            expenses, val = _filter_by_cat(expenses, val)
            label = f"Category: {val}"
        else:
            expenses = expenses[expenses["account_name"].astype(str) == val]
            label = f"Account: {val}"

    elif triggered == "exp-pie" and pie_click:
        val = pie_click["points"][0]["label"]
        expenses, val = _filter_by_cat(expenses, val)
        label = f"Category: {val}"

    elif triggered == "exp-treemap" and treemap_click:
        pt           = treemap_click["points"][0]
        click_label  = pt.get("label", "")
        click_parent = pt.get("parent", "")

        if click_parent and click_parent not in ("", "Expenses"):
            expenses = expenses[(expenses["primary_tag"] == click_parent) &
                                (expenses["month"] == click_label)]
            label = f"{click_parent} · {_fmt_month(click_label)}"
        elif click_label and click_label != "Expenses":
            expenses = expenses[expenses["primary_tag"] == click_label]
            label = f"Category: {click_label}"

    rows    = _to_rows(expenses)
    caption = f"{len(rows):,} transactions · double-tap a row to edit · shift-click for multi-select"
    return label, caption, rows


@callback(
    Output("exp-txn-grid",         "rowData"),
    Input("exp-txn-all-rows",      "data"),
    Input("exp-txn-search",        "value"),
    Input("exp-txn-filter-field",  "value"),
    prevent_initial_call="initial_duplicate",
)
def exp_apply_filter(all_rows, search, field):
    if not all_rows:
        return []
    if not search:
        return all_rows
    sl = search.lower()
    if field == "all":
        return [r for r in all_rows
                if any(sl in str(v).lower() for v in r.values())]
    return [r for r in all_rows
            if sl in str(r.get(field, "")).lower()]

# ── Row click → modal (single or bulk) ───────────────────────────────────────

@callback(
    Output("exp-tag-modal",           "is_open"),
    Output("exp-modal-desc",          "children"),
    Output("exp-modal-meta",          "children"),
    Output("exp-modal-description",   "value"),
    Output("exp-modal-description",   "disabled"),
    Output("exp-modal-date",          "value"),
    Output("exp-modal-date",          "disabled"),
    Output("exp-modal-amount",        "value"),
    Output("exp-modal-amount",        "disabled"),
    Output("exp-modal-type",          "value"),
    Output("exp-row-tag-dropdown",    "options"),
    Output("exp-row-tag-dropdown",    "value"),
    Output("exp-row-detail-id",       "data"),
    Output("exp-row-detail-merchant", "data"),
    Output("exp-modal-mode",          "data"),
    Output("exp-edit-ids",            "data"),
    Input("exp-txn-grid",             "cellDoubleClicked"),
    State("exp-txn-grid",             "selectedRows"),
    State("exp-row-tag-opts",         "data"),
    prevent_initial_call=True,
)
def exp_show_detail(cell_double_clicked, selected_rows, tag_opts):
    cell_clicked = cell_double_clicked
    _nu = no_update
    if not cell_clicked:
        return (_nu,) * 16

    # Bulk mode: multiple rows selected
    if selected_rows and len(selected_rows) > 1:
        count = len(selected_rows)
        ids   = [r.get("id") for r in selected_rows]
        return (True,
                f"Editing {count} transactions",
                "Type and Tags will be applied to all selected rows.",
                "", True,
                "", True,
                None, True,
                "expense",
                tag_opts or [], [],
                None, None,
                "bulk", ids)

    # Single row: use tapped cell's row data
    row = cell_clicked.get("data", {})
    if not row:
        return (_nu,) * 16

    current_tags = [t.strip() for t in str(row.get("tags", "")).split(",")
                    if t.strip() and t.strip() not in ("nan", "Untagged", "None")]
    valid_defaults = [t for t in current_tags
                      if any(o["value"] == t for o in (tag_opts or []))]
    merchant_words = [w for w in re.sub(r"[^a-zA-Z\s]", " ",
                      row.get("description", "")).split() if len(w) >= 3]
    merchant_token = merchant_words[0] if merchant_words else ""

    meta       = f"{row.get('account_name', '')}  ·  {row.get('date_str', '')}  ·  {row.get('amount_str', '')}"
    date_val   = row.get("date_raw", "")
    amount_val = abs(float(row.get("amount_raw", 0))) or None

    return (True,
            row.get("description", ""), meta,
            row.get("description", ""), False,
            date_val, False,
            amount_val, False,
            row.get("type", "expense"),
            tag_opts or [], valid_defaults,
            row.get("id"), merchant_token,
            "single", [row.get("id")])


@callback(
    Output("exp-row-save-feedback", "children"),
    Output("exp-txn-grid",          "rowData", allow_duplicate=True),
    Input("exp-row-save-btn",       "n_clicks"),
    State("exp-modal-mode",         "data"),
    State("exp-modal-description",  "value"),
    State("exp-modal-date",         "value"),
    State("exp-modal-amount",       "value"),
    State("exp-modal-type",         "value"),
    State("exp-row-tag-dropdown",   "value"),
    State("exp-row-detail-id",      "data"),
    State("exp-edit-ids",           "data"),
    State("exp-txn-grid",           "rowData"),
    prevent_initial_call=True,
)
def exp_save_transaction(n_clicks, mode, description, date_val, amount,
                          txn_type, chosen_tags, row_id, edit_ids, row_data):
    if not n_clicks:
        return no_update, no_update

    tags_str = ", ".join(chosen_tags) if chosen_tags else ""

    if mode == "single" and row_id:
        updates = {}
        if description is not None: updates["description"] = description
        if date_val:                 updates["date"]        = date_val
        if amount is not None:       updates["amount"]      = abs(float(amount))
        if txn_type:                 updates["type"]        = txn_type
        if tags_str:                 updates["tags"]        = tags_str

        ok = update_transaction(row_id, updates)
        if not ok:
            return dbc.Alert("❌ Save failed.", color="danger", duration=4000), no_update

        updated = []
        for r in (row_data or []):
            if r.get("id") == row_id:
                r = dict(r)
                if description is not None: r["description"] = description
                if tags_str:                r["tags"]        = tags_str
                if txn_type:                r["type"]        = txn_type
            updated.append(r)
        return dbc.Alert("✅ Saved!", color="success", duration=3000), updated

    elif mode == "bulk" and edit_ids:
        bulk_updates = {}
        if tags_str:  bulk_updates["tags"] = tags_str
        if txn_type:  bulk_updates["type"] = txn_type
        if not bulk_updates:
            return dbc.Alert("⚠️ Nothing selected to update.", color="warning", duration=3000), no_update

        errors = sum(
            0 if update_transaction(uid, bulk_updates) else 1
            for uid in edit_ids
        )
        updated = []
        for r in (row_data or []):
            if r.get("id") in set(edit_ids):
                r = dict(r)
                if tags_str:  r["tags"] = tags_str
                if txn_type:  r["type"] = txn_type
            updated.append(r)

        msg = (f"⚠️ Saved with {errors} error(s)." if errors
               else f"✅ Updated {len(edit_ids)} transactions!")
        color = "warning" if errors else "success"
        return dbc.Alert(msg, color=color, duration=4000), updated

    return dbc.Alert("⚠️ Nothing to save.", color="warning", duration=3000), no_update
