"""
Expenses page — monthly breakdown with category drill-down.
"""

import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from datetime import date, timedelta

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import load_transactions, fmt_inr

dash.register_page(__name__, path="/expenses", title="Expenses")

# ── Layout ────────────────────────────────────────────────────────────────────

def layout():
    txn_df = load_transactions()
    if txn_df.empty:
        min_date, max_date = date(2020, 1, 1), date.today()
    else:
        min_date = txn_df["date"].min().date()
        max_date = txn_df["date"].max().date()

    default_start = max(min_date, date.today() - timedelta(days=365))

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
        dbc.Row(id="exp-metrics", className="mb-3 g-2"),

        # Charts
        dbc.Row([
            dbc.Col(dcc.Graph(id="exp-bar",   config={"displayModeBar": False}), md=8),
            dbc.Col(dcc.Graph(id="exp-pie",   config={"displayModeBar": False}), md=4),
        ], className="mb-3 g-2"),

        dbc.Row([
            dbc.Col(dcc.Graph(id="exp-treemap", config={"displayModeBar": False}), md=12),
        ], className="mb-2 g-2"),
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

# ── Callback ─────────────────────────────────────────────────────────────────

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

    # ── Bar chart ─────────────────────────────────────────────────────────────
    if group_by == "month":
        agg = expenses.groupby("month")["amount"].sum().reset_index().sort_values("month")
        bar = go.Figure([go.Bar(
            x=agg["month"], y=agg["amount"],
            marker_color="#6c63ff",
            hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>",
        )])
        bar.update_layout(title="Expenses by Month")
    elif group_by == "category":
        agg = (expenses.groupby("primary_tag")["amount"].sum()
               .sort_values(ascending=False).reset_index())
        bar = go.Figure([go.Bar(
            x=agg["primary_tag"], y=agg["amount"],
            marker_color="#6c63ff",
            hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>",
        )])
        bar.update_layout(title="Expenses by Category")
    else:
        agg = (expenses.groupby("account_name")["amount"].sum()
               .sort_values(ascending=False).reset_index())
        bar = go.Figure([go.Bar(
            x=agg["account_name"], y=agg["amount"],
            marker_color="#6c63ff",
            hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>",
        )])
        bar.update_layout(title="Expenses by Account")
    _dark_fig(bar)

    # ── Pie chart (always by category) ────────────────────────────────────────
    top8 = (expenses.groupby("primary_tag")["amount"].sum()
            .sort_values(ascending=False).head(8).reset_index())
    pie = go.Figure([go.Pie(
        labels=top8["primary_tag"], values=top8["amount"],
        hole=0.4, textinfo="percent",
        hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>",
    )])
    pie.update_layout(title="Category Share")
    _dark_fig(pie)

    # ── Treemap (category → month heatmap) ───────────────────────────────────
    treemap_data = (expenses.groupby(["primary_tag", "month"])["amount"]
                   .sum().reset_index())
    treemap_data["parent"] = "Expenses"

    if len(treemap_data) > 0:
        tm = px.treemap(
            treemap_data,
            path=["parent", "primary_tag", "month"],
            values="amount",
            color="amount",
            color_continuous_scale=[[0, "#1a1a2e"], [0.5, "#6c63ff"], [1, "#ff6b6b"]],
        )
        tm.update_layout(title="Spend Breakdown (Category → Month)")
        tm.update_traces(hovertemplate="%{label}<br>₹%{value:,.0f}<extra></extra>")
    else:
        tm = go.Figure()
    _dark_fig(tm)

    return metrics, bar, pie, tm
