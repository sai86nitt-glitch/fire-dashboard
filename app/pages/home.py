"""
Home / Overview page — FIRE metrics, corpus projections, spend trend, accounts.
"""

import re
import dash
from dash import html, dcc, callback, Input, Output, State, ctx, no_update
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
from datetime import date

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import (
    load_transactions, load_accounts, load_tags,
    batch_update_tags, save_rule,
    fmt_inr, net_worth, monthly_expense_avg,
)
from ai_advisor import get_portfolio_advice

dash.register_page(__name__, path="/", title="Dashboard")

_SAFE_WR   = 0.04
_FIRE_MULT = 25

def _blank():
    """Dark empty placeholder figure shown before data loads."""
    import plotly.graph_objects as _go
    f = _go.Figure()
    f.update_layout(paper_bgcolor="#1e1e2e", plot_bgcolor="#1e1e2e",
                    xaxis={"visible": False}, yaxis={"visible": False},
                    margin={"t": 10, "b": 10, "l": 10, "r": 10})
    return f

# ── Shared transaction column defs ────────────────────────────────────────────

_TXN_COLS = [
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
    tags     = load_tags()
    tag_opts = [{"label": t["display"], "value": t["display"]} for t in tags]

    return html.Div([
        html.H3("🏠 FIRE Dashboard", style={"marginBottom": "16px"}),

        # AI advice banner (hidden until loaded)
        html.Div(id="home-ai-banner", className="mb-3"),

        # Metric cards
        dbc.Row(id="home-metrics", className="mb-3 g-2"),
        html.Hr(style={"borderColor": "#2a2a3e"}),

        # FIRE progress + projections
        dbc.Row([
            dbc.Col(dcc.Graph(id="home-fire-gauge",  figure=_blank(), config={"displayModeBar": False}), md=5),
            dbc.Col(html.Div(id="home-projections"), md=7),
        ], className="mb-3 g-2"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # Spend trend + category pie
        dbc.Row([
            dbc.Col(dcc.Graph(id="home-spend-trend",  figure=_blank(), config={"displayModeBar": False}), md=8),
            dbc.Col(dcc.Graph(id="home-category-pie", figure=_blank(), config={"displayModeBar": False}), md=4),
        ], className="mb-3 g-2"),

        # Accounts table
        dbc.Row([
            dbc.Col(html.Div(id="home-accounts-table"), md=12),
        ], className="mb-3"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # Transactions at bottom
        dbc.Row([
            dbc.Col(
                html.Div(id="home-txn-label",
                         style={"color": "#aaa", "fontSize": "12px", "fontStyle": "italic"}),
                width="auto",
            ),
            dbc.Col(
                dbc.Button("✕ Clear filter", id="home-txn-clear", size="sm",
                           color="secondary", outline=True, style={"fontSize": "11px"}),
                width="auto", className="ms-auto",
            ),
        ], className="mb-2 align-items-center"),
        dag.AgGrid(
            id="home-txn-grid",
            columnDefs=_TXN_COLS,
            rowData=[],
            dashGridOptions={
                "domLayout": "autoHeight",
                "animateRows": True,
                "rowSelection": "single",
                "suppressRowClickSelection": False,
            },
            defaultColDef={"resizable": True, "sortable": True},
            className="ag-theme-alpine-dark",
            selectedRows=[],
        ),
        html.Small(id="home-txn-caption",
                   style={"color": "#666", "marginTop": "4px", "display": "block"}),

        # ── Inline row detail / tag editor ────────────────────────────────────
        html.Div(id="home-row-detail", style={"marginTop": "6px"}),

        # Hidden stores
        dcc.Store(id="home-row-tag-opts", data=tag_opts),
        dcc.Interval(id="home-refresh", interval=5 * 60 * 1000, n_intervals=0),
    ])

# ── Helpers ───────────────────────────────────────────────────────────────────

def _metric_card(label, value, colour="#e0e0e0", sub=None):
    children = [
        html.Div(label, className="metric-label"),
        html.Div(value, className="metric-value", style={"color": colour}),
    ]
    if sub:
        children.append(html.Div(sub, style={"fontSize": "11px", "color": "#666", "marginTop": "2px"}))
    return dbc.Col(html.Div(children, className="metric-card"), md=3)

def _dark_fig(fig):
    fig.update_layout(
        paper_bgcolor="#1e1e2e", plot_bgcolor="#13131f",
        font={"color": "#e0e0e0", "size": 11},
        margin={"t": 36, "b": 36, "l": 12, "r": 12},
    )
    return fig

def _to_rows(df):
    icon = {"expense": "←", "income": "+", "transfer": "⇌"}
    rows = []
    for _, r in df.sort_values("date", ascending=False).iterrows():
        rows.append({
            "id":          str(r.get("id", "")),
            "date_str":    r["date"].strftime("%d %b %Y") if pd.notna(r["date"]) else "",
            "amount_str":  f"{icon.get(r['type'], '←')} {fmt_inr(abs(r['amount']))}",
            "description": str(r.get("description", "")),
            "tags":        str(r.get("tags", "")),
            "account_name":str(r.get("account_name", "")),
            "type":        str(r.get("type", "expense")),
        })
    return rows

# ── Main refresh callback ─────────────────────────────────────────────────────

@callback(
    Output("home-metrics",       "children"),
    Output("home-fire-gauge",    "figure"),
    Output("home-projections",   "children"),
    Output("home-spend-trend",   "figure"),
    Output("home-category-pie",  "figure"),
    Output("home-accounts-table","children"),
    Input("home-refresh",        "n_intervals"),
)
def refresh(_n):
    txn_df  = load_transactions()
    acc_df  = load_accounts()

    nw      = net_worth(acc_df)
    avg_exp = monthly_expense_avg(txn_df, months=12)
    annual  = avg_exp * 12
    target  = _FIRE_MULT * annual   # dynamic: 25× annual spend
    pct     = min(nw / target * 100, 100) if target else 0
    fire_nr = nw / annual if annual else 0
    yrs_to  = (target - nw) / (annual * (1 - _SAFE_WR)) if annual and nw < target else 0

    metrics = [
        _metric_card("Net Worth",         fmt_inr(nw),       "#00c49f"),
        _metric_card("Monthly Avg Spend", fmt_inr(avg_exp),  "#ff6b6b",
                     sub=f"{fmt_inr(annual)} / yr"),
        _metric_card("FIRE Progress",     f"{pct:.1f}%",     "#6c63ff",
                     sub=f"Target {fmt_inr(target)}"),
        _metric_card("FIRE Number",       f"{fire_nr:.1f}×", "#ffc107",
                     sub=f"~{max(0, yrs_to):.0f} yrs at 4% SWR"),
    ]

    # ── FIRE gauge ────────────────────────────────────────────────────────────
    gauge = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=pct,
        number={"suffix": "%", "font": {"size": 36, "color": "#e0e0e0"}},
        delta={"reference": 100, "valueformat": ".1f", "suffix": "%",
               "increasing": {"color": "#00c49f"}, "decreasing": {"color": "#ff6b6b"}},
        gauge={
            "axis": {"range": [0, 100], "tickcolor": "#555"},
            "bar":  {"color": "#6c63ff"},
            "bgcolor": "#13131f",
            "steps": [
                {"range": [0,  25], "color": "#1a1a2e"},
                {"range": [25, 50], "color": "#1e1e30"},
                {"range": [50, 75], "color": "#222238"},
                {"range": [75,100], "color": "#262640"},
            ],
            "threshold": {"line": {"color": "#00c49f", "width": 3}, "value": 100},
        },
        title={"text": f"FIRE Progress<br><span style='font-size:12px;color:#888'>"
                       f"₹{nw/1e7:.2f} Cr / {fmt_inr(target)}</span>",
               "font": {"color": "#e0e0e0"}},
    ))
    gauge.update_layout(paper_bgcolor="#1e1e2e", font={"color": "#e0e0e0"},
                        margin={"t": 60, "b": 20, "l": 20, "r": 20}, height=280)

    # ── Corpus projections table ───────────────────────────────────────────────
    this_year = date.today().year
    proj_rows = []
    for yrs in [10, 12, 15]:
        yr = this_year + yrs
        proj_rows.append(html.Tr([
            html.Td(str(yr),                  style={"color": "#888",    "fontSize": "12px"}),
            html.Td(f"{yrs} yrs",             style={"color": "#888",    "fontSize": "11px"}),
            html.Td(fmt_inr(nw * 1.12 ** yrs),style={"color": "#6c63ff","fontSize": "12px",
                                                      "fontFamily": "monospace", "textAlign": "right"}),
            html.Td(fmt_inr(nw * 1.15 ** yrs),style={"color": "#00c49f","fontSize": "12px",
                                                      "fontFamily": "monospace", "textAlign": "right"}),
            html.Td(fmt_inr(nw * 1.18 ** yrs),style={"color": "#ffc107","fontSize": "12px",
                                                      "fontFamily": "monospace", "textAlign": "right"}),
        ]))
    projections = html.Div([
        html.Div("Corpus projections",
                 style={"color": "#aaa", "fontSize": "12px", "marginBottom": "8px",
                        "fontStyle": "italic"}),
        dbc.Table(
            [html.Thead(html.Tr([
                html.Th("Year"), html.Th(""), html.Th("12% CAGR"), html.Th("15% CAGR"), html.Th("18% CAGR"),
            ]))] + [html.Tbody(proj_rows)],
            bordered=False, size="sm", style={"color": "#e0e0e0"},
        ),
        html.Div(
            f"🎯 Target corpus: {fmt_inr(target)} ({_FIRE_MULT}× annual spend of {fmt_inr(annual)})",
            style={"fontSize": "11px", "color": "#888", "marginTop": "8px"},
        ),
    ])

    # ── Spend trend (last 12 months) ─────────────────────────────────────────
    if txn_df.empty:
        trend_fig = go.Figure()
    else:
        expenses = txn_df[txn_df["type"] == "expense"].copy()
        cutoff   = pd.Timestamp.now() - pd.DateOffset(months=12)
        expenses = expenses[expenses["date"] >= cutoff]
        monthly  = expenses.groupby("month")["amount"].sum().reset_index().sort_values("month")
        monthly["month_label"] = monthly["month"].apply(
            lambda v: pd.to_datetime(v + "-01").strftime("%b %Y") if v else v
        )
        trend_fig = go.Figure([
            go.Bar(x=monthly["month"], y=monthly["amount"],
                   marker_color="#6c63ff", name="Expenses",
                   text=monthly["month_label"], textposition="none",
                   hovertemplate="%{text}<br>₹%{y:,.0f}<extra></extra>")
        ])
        trend_fig.update_layout(title="Monthly Expenses (12m) — click a bar to filter",
                                showlegend=False)
        trend_fig.add_hline(y=avg_exp, line_dash="dash", line_color="#ff6b6b",
                            annotation_text=f"Avg {fmt_inr(avg_exp)}",
                            annotation_position="top right")
    _dark_fig(trend_fig)

    # ── Category pie (last 3 months) ─────────────────────────────────────────
    if txn_df.empty:
        pie_fig = go.Figure()
    else:
        cut3 = pd.Timestamp.now() - pd.DateOffset(months=3)
        top  = (txn_df[(txn_df["type"] == "expense") & (txn_df["date"] >= cut3)]
                .groupby("primary_tag")["amount"].sum()
                .sort_values(ascending=False).head(8).reset_index())
        pie_fig = go.Figure([go.Pie(
            labels=top["primary_tag"], values=top["amount"],
            hole=0.4, textinfo="percent",
            hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>",
        )])
        pie_fig.update_layout(title="Spend by Category (3m) — click to filter")
    _dark_fig(pie_fig)

    # ── Accounts table ────────────────────────────────────────────────────────
    if acc_df.empty:
        tbl = html.P("No account data.", style={"color": "#666"})
    else:
        rows = []
        for _, r in acc_df.iterrows():
            bal       = r.get("computed_balance", 0)
            stale     = r.get("days_stale", None)
            stale_str = f"{int(stale)}d ago" if pd.notna(stale) and stale else "—"
            colour    = "#00c49f" if bal >= 0 else "#ff6b6b"
            rows.append(html.Tr([
                html.Td(str(r.get("name", "")),  style={"color": "#e0e0e0", "fontSize": "12px"}),
                html.Td(str(r.get("type", "")),  style={"color": "#888",    "fontSize": "11px"}),
                html.Td(fmt_inr(bal),            style={"color": colour,    "fontSize": "12px",
                                                        "fontFamily": "monospace", "textAlign": "right"}),
                html.Td(stale_str,               style={"color": "#666",    "fontSize": "11px",
                                                        "textAlign": "right"}),
            ]))
        tbl = dbc.Table(
            [html.Thead(html.Tr([
                html.Th("Account"), html.Th("Type"),
                html.Th("Balance", style={"textAlign": "right"}),
                html.Th("Updated",  style={"textAlign": "right"}),
            ]))] + [html.Tbody(rows)],
            bordered=False, hover=True, responsive=True, size="sm",
            style={"color": "#e0e0e0"}, className="mb-0",
        )

    return metrics, gauge, projections, trend_fig, pie_fig, tbl

# ── AI advice banner callback ─────────────────────────────────────────────────

@callback(
    Output("home-ai-banner", "children"),
    Input("home-refresh",    "n_intervals"),
)
def update_ai_banner(_n):
    txn_df = load_transactions()
    acc_df = load_accounts()

    nw      = net_worth(acc_df)
    avg_exp = monthly_expense_avg(txn_df, months=12)
    annual  = avg_exp * 12

    # Build allocation percentages for the advisor
    total = max(nw, 1)
    equity_mf, epf, metals, cash = 0.0, 0.0, 0.0, 0.0
    if not acc_df.empty:
        for _, r in acc_df.iterrows():
            name = str(r.get("name", "")).lower()
            typ  = str(r.get("type", "")).lower()
            bal  = float(r.get("computed_balance", 0) or 0)
            if "epf" in name or "epf" in typ:
                epf += bal
            elif any(k in name for k in ("gold", "silver", "axisgold", "icicisilve")):
                metals += bal
            elif any(k in name for k in ("arbitrage", "liquid", "overnight")):
                cash += bal
            else:
                equity_mf += bal

    fire_target = _FIRE_MULT * annual * (1.06 ** 10)  # ~10yr inflation-adjusted

    context = {
        "nw_cr":          round(nw / 1e7, 2),
        "fire_pct":       round(nw / fire_target * 100, 1) if fire_target else 0,
        "equity_pct":     round(equity_mf / total * 100, 1),
        "epf_pct":        round(epf        / total * 100, 1),
        "metals_pct":     round(metals     / total * 100, 1),
        "cash_pct":       round(cash       / total * 100, 1),
        "monthly_sip":    round(avg_exp * 0.4),   # rough estimate if not tracked
        "fire_target_cr": round(fire_target / 1e7, 2),
        "edu_target_cr":  round(400_000 * (1.05**15) * 84 * (1.03**15) / 1e7, 2),
        "yrs_to_fire":    10,
        "yrs_to_edu":     15,
    }

    advice = get_portfolio_advice(context)

    if advice is None:
        # ANTHROPIC_API_KEY not set — show static rule-based nudge
        msgs = []
        if context["equity_pct"] > 80:
            msgs.append(f"Equity at {context['equity_pct']}% — above 80% target ceiling; consider rebalancing")
        if context["epf_pct"] + context["cash_pct"] < 15:
            msgs.append("Debt allocation below 15% — EPF + cash is low")
        if context["fire_pct"] < 15:
            msgs.append(f"FIRE progress {context['fire_pct']}% — early stage, stay the course")
        if not msgs:
            msgs = ["Allocation looks balanced. Keep SIPs running."]
        advice = {
            "color":    "secondary",
            "headline": "Portfolio Snapshot",
            "points":   msgs,
        }

    color_map = {
        "success":   ("#0d3320", "#00c49f"),
        "warning":   ("#332b00", "#ffc107"),
        "danger":    ("#330d0d", "#ff6b6b"),
        "secondary": ("#1e1e2e", "#888"),
    }
    bg, fg = color_map.get(advice.get("color", "secondary"), color_map["secondary"])

    return html.Div([
        dbc.Row([
            dbc.Col([
                html.Div(advice.get("headline", ""), style={
                    "fontWeight": "600", "fontSize": "13px",
                    "color": fg, "marginBottom": "4px",
                }),
                html.Ul([
                    html.Li(p, style={"fontSize": "12px", "color": "#ccc", "marginBottom": "2px"})
                    for p in advice.get("points", [])
                ], style={"paddingLeft": "18px", "margin": 0}),
            ]),
            dbc.Col(
                html.Div("🤖 AI Advisor", style={"fontSize": "10px", "color": "#555",
                                                  "textAlign": "right"}),
                width="auto",
            ),
        ], className="align-items-start"),
    ], style={
        "background":    bg,
        "border":        f"1px solid {fg}",
        "borderRadius":  "8px",
        "padding":       "12px 16px",
    })

# ── Transactions drill-down callback ──────────────────────────────────────────

@callback(
    Output("home-txn-grid",    "rowData"),
    Output("home-txn-label",   "children"),
    Output("home-txn-caption", "children"),
    Output("home-row-detail",  "children", allow_duplicate=True),
    Input("home-spend-trend",  "clickData"),
    Input("home-category-pie", "clickData"),
    Input("home-txn-clear",    "n_clicks"),
    Input("home-refresh",      "n_intervals"),
    prevent_initial_call="initial_duplicate",
)
def drill_transactions(trend_click, pie_click, _clear, _refresh):
    df    = load_transactions()
    txns  = df.copy() if not df.empty else df
    label = "Recent transactions — click a chart bar or slice to filter"
    triggered = ctx.triggered_id

    if triggered == "home-spend-trend" and trend_click:
        month = trend_click["points"][0]["x"]
        txns  = txns[txns["month"] == month]
        try:
            month_label = pd.to_datetime(month + "-01").strftime("%b %Y")
        except Exception:
            month_label = month
        label = f"Expenses in {month_label}"

    elif triggered == "home-category-pie" and pie_click:
        cat   = pie_click["points"][0]["label"]
        cut3  = pd.Timestamp.now() - pd.DateOffset(months=3)
        txns  = txns[(txns["primary_tag"] == cat) & (txns["date"] >= cut3)]
        label = f"Category: {cat} (last 3 months)"

    else:
        cut3 = pd.Timestamp.now() - pd.DateOffset(months=3)
        txns = txns[txns["date"] >= cut3]

    rows    = _to_rows(txns)
    caption = f"{len(rows):,} transactions · click a row to view / edit tags"
    _EXPLICIT_TRIGGERS = {"home-spend-trend", "home-category-pie", "home-txn-clear"}
    close_panel = triggered in _EXPLICIT_TRIGGERS
    return rows, label, caption, (None if close_panel else no_update)

# ── Row click → inline tag editor ────────────────────────────────────────────

@callback(
    Output("home-row-detail",  "children"),
    Input("home-txn-grid",     "selectedRows"),
    State("home-row-tag-opts", "data"),
    prevent_initial_call=True,
)
def home_show_detail(selected_rows, tag_opts):
    if not selected_rows:
        return no_update
    row = selected_rows[0]

    current_tags = [t.strip() for t in str(row.get("tags", "")).split(",")
                    if t.strip() and t.strip() not in ("nan", "Untagged", "None")]
    valid_defaults = [t for t in current_tags
                      if any(o["value"] == t for o in (tag_opts or []))]

    merchant_words = [w for w in re.sub(r"[^a-zA-Z\s]", " ",
                      row.get("description", "")).split() if len(w) >= 3]
    merchant_token = merchant_words[0] if merchant_words else ""

    return html.Div([
        dbc.Row([
            dbc.Col([
                html.Div(row["description"],
                         style={"fontWeight": "600", "fontSize": "13px",
                                "color": "#e0e0e0", "marginBottom": "2px"}),
                html.Div(
                    f"{row['account_name']}  ·  {row['date_str']}  ·  {row['amount_str']}",
                    style={"fontSize": "11px", "color": "#888"},
                ),
            ]),
            dbc.Col(
                dbc.Button("✕", id="home-row-close", size="sm", color="secondary",
                           outline=True, style={"float": "right"}),
                width="auto",
            ),
        ], className="mb-2 align-items-start"),

        html.Hr(style={"borderColor": "#2a2a3e", "margin": "8px 0"}),

        dbc.Row([
            dbc.Col(
                dcc.Dropdown(
                    id="home-row-tag-dropdown",
                    options=tag_opts or [],
                    value=valid_defaults,
                    multi=True,
                    placeholder="Search or pick a category…",
                    style={"fontSize": "13px"},
                ),
                md=9,
            ),
            dbc.Col([
                dbc.Button("✓ Save Tag", id="home-row-save-btn", color="primary",
                           size="sm", className="w-100"),
            ], md=3),
        ]),

        html.Div(id="home-row-save-feedback", className="mt-2"),

        dcc.Store(id="home-row-detail-id",       data=row.get("id")),
        dcc.Store(id="home-row-detail-merchant", data=merchant_token),

    ], style={
        "padding": "12px 18px",
        "background": "#1a1a2e",
        "borderLeft": "3px solid #6c63ff",
        "borderRadius": "0 6px 6px 0",
    })


@callback(
    Output("home-row-save-feedback",  "children"),
    Output("home-txn-grid",           "rowData", allow_duplicate=True),
    Input("home-row-save-btn",        "n_clicks"),
    State("home-row-tag-dropdown",    "value"),
    State("home-row-detail-id",       "data"),
    State("home-row-detail-merchant", "data"),
    State("home-txn-grid",            "rowData"),
    prevent_initial_call=True,
)
def home_save_tag(n_clicks, chosen_tags, row_id, merchant, row_data):
    if not n_clicks or not chosen_tags or not row_id:
        return no_update, no_update

    tags_str = ", ".join(chosen_tags)
    saved, _ = batch_update_tags({row_id: tags_str})

    if not saved:
        return dbc.Alert("❌ Save failed.", color="danger", duration=4000), no_update

    updated = []
    for r in (row_data or []):
        if r.get("id") == row_id:
            r = {**r, "tags": tags_str}
        updated.append(r)

    return dbc.Alert(f"✅ Saved: {tags_str}", color="success", duration=3000), updated


@callback(
    Output("home-row-detail",  "children", allow_duplicate=True),
    Output("home-txn-grid",    "selectedRows"),
    Input("home-row-close",    "n_clicks"),
    prevent_initial_call=True,
)
def home_close_detail(_):
    return None, []
