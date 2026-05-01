"""
Home / Overview page — FIRE metrics, account balances, spending trend.
"""

import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import (
    load_transactions, load_accounts,
    fmt_inr, net_worth, monthly_expense_avg,
)

dash.register_page(__name__, path="/", title="Overview")

_FIRE_TARGET   = 5_00_00_000   # ₹5 Cr
_ANNUAL_SPEND  = None          # computed dynamically
_SAFE_WR       = 0.04

# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    return html.Div([
        html.H3("🏠 FIRE Overview", style={"marginBottom": "16px"}),
        dbc.Row(id="home-metrics", className="mb-3 g-2"),
        html.Hr(style={"borderColor": "#2a2a3e"}),
        dbc.Row([
            dbc.Col(dcc.Graph(id="home-spend-trend", config={"displayModeBar": False}), md=8),
            dbc.Col(dcc.Graph(id="home-category-pie", config={"displayModeBar": False}), md=4),
        ], className="mb-3 g-2"),
        dbc.Row([
            dbc.Col(html.Div(id="home-accounts-table"), md=12),
        ]),
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

# ── Callback ─────────────────────────────────────────────────────────────────

@callback(
    Output("home-metrics",       "children"),
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
    target  = _FIRE_TARGET
    pct     = min(nw / target * 100, 100) if target else 0
    fire_nr = nw / annual if annual else 0
    yrs_to  = (target - nw) / (annual * (1 - _SAFE_WR)) if annual and nw < target else 0

    metrics = [
        _metric_card("Net Worth",           fmt_inr(nw),    "#00c49f"),
        _metric_card("Monthly Avg Spend",   fmt_inr(avg_exp), "#ff6b6b",
                     sub=f"₹{annual/1e5:.1f}L / yr"),
        _metric_card("FIRE Progress",       f"{pct:.1f}%",  "#6c63ff",
                     sub=f"Target {fmt_inr(target)}"),
        _metric_card("FIRE Number",         f"{fire_nr:.1f}×", "#ffc107",
                     sub=f"~{max(0, yrs_to):.0f} yrs at 4% SWR"),
    ]

    # ── Spend trend (last 12 months) ─────────────────────────────────────────
    if txn_df.empty:
        trend_fig = go.Figure()
    else:
        expenses  = txn_df[txn_df["type"] == "expense"].copy()
        cutoff    = pd.Timestamp.now() - pd.DateOffset(months=12)
        expenses  = expenses[expenses["date"] >= cutoff]
        monthly   = expenses.groupby("month")["amount"].sum().reset_index()
        monthly   = monthly.sort_values("month")
        trend_fig = go.Figure([
            go.Bar(
                x=monthly["month"], y=monthly["amount"],
                marker_color="#6c63ff", name="Expenses",
                hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>",
            )
        ])
        trend_fig.update_layout(title="Monthly Expenses (12m)", showlegend=False)
        avg_line = avg_exp
        trend_fig.add_hline(y=avg_line, line_dash="dash", line_color="#ff6b6b",
                            annotation_text=f"Avg {fmt_inr(avg_line)}", annotation_position="top right")
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
        pie_fig.update_layout(title="Spend by Category (3m)")
    _dark_fig(pie_fig)

    # ── Accounts table ────────────────────────────────────────────────────────
    if acc_df.empty:
        tbl = html.P("No account data.", style={"color": "#666"})
    else:
        rows = []
        for _, r in acc_df.iterrows():
            bal      = r.get("computed_balance", 0)
            stale    = r.get("days_stale", None)
            stale_str = f"{int(stale)}d ago" if pd.notna(stale) and stale else "—"
            colour   = "#00c49f" if bal >= 0 else "#ff6b6b"
            rows.append(html.Tr([
                html.Td(str(r.get("name", "")),         style={"color": "#e0e0e0", "fontSize": "12px"}),
                html.Td(str(r.get("type", "")),         style={"color": "#888",    "fontSize": "11px"}),
                html.Td(fmt_inr(bal),                   style={"color": colour,    "fontSize": "12px",
                                                                "fontFamily": "monospace", "textAlign": "right"}),
                html.Td(stale_str,                      style={"color": "#666",    "fontSize": "11px",
                                                                "textAlign": "right"}),
            ]))
        tbl = dbc.Table(
            [html.Thead(html.Tr([
                html.Th("Account"),
                html.Th("Type"),
                html.Th("Balance", style={"textAlign": "right"}),
                html.Th("Updated",  style={"textAlign": "right"}),
            ]))] + [html.Tbody(rows)],
            bordered=False, hover=True, responsive=True, size="sm",
            style={"color": "#e0e0e0"},
            className="mb-0",
        )

    return metrics, trend_fig, pie_fig, tbl
