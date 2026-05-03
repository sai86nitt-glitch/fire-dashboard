"""
Home / Overview — balances, portfolio, retirement targets, expense trends.
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

# ── Profile constants (keep in sync with portfolio.py) ───────────────────────
_FIRE_MULT          = 25
_FIRE_YEAR          = 2036
_EDU_YEAR           = 2041
_EDU_USD            = 400_000
_USD_INR_NOW        = 84
_USD_INFLATION      = 0.05
_INR_DEPRECIATION   = 0.03
_MONTHLY_EQUITY_SIP = 250_000
_MONTHLY_METALS_SIP =  30_000
_MONTHLY_EPF        =  72_000
_TOTAL_MONTHLY_SIP  = _MONTHLY_EQUITY_SIP + _MONTHLY_METALS_SIP + _MONTHLY_EPF
_POST_FIRE_CAGR     = 0.10
_TOP_CATS           = 7

# ── Helpers ───────────────────────────────────────────────────────────────────

def _blank():
    f = go.Figure()
    f.update_layout(paper_bgcolor="#1e1e2e", plot_bgcolor="#1e1e2e",
                    xaxis={"visible": False}, yaxis={"visible": False},
                    margin={"t": 10, "b": 10, "l": 10, "r": 10})
    return f

def _dark_fig(fig):
    fig.update_layout(
        paper_bgcolor="#1e1e2e", plot_bgcolor="#13131f",
        font={"color": "#e0e0e0", "size": 11},
        margin={"t": 40, "b": 36, "l": 12, "r": 12},
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

def _fv_corpus(current, monthly_sip, years, cagr):
    r = cagr / 12; n = years * 12
    sip_fv = monthly_sip * ((1 + r) ** n - 1) / r if r else monthly_sip * n
    return current * (1 + cagr) ** years + sip_fv

def _simple_buckets(acc_df):
    """Simplified account → bucket mapping for home page portfolio panel."""
    buckets = {}
    for _, row in acc_df.iterrows():
        nl  = str(row.get("name", "")).lower()
        tl  = str(row.get("type", "")).lower()
        bal = float(row.get("computed_balance", 0) or 0)
        if any(k in nl for k in ("loan", " cc", "credit card", "amex", "visa")) or \
           any(k in tl for k in ("credit", "loan")):
            continue  # exclude liabilities from investable
        elif any(k in nl for k in ("gold", "silver", "axisgold", "icicisilve",
                                   "digital gold", "sgb")):
            bucket = "Metals"
        elif any(k in nl for k in ("epf", "provident", " pf")) or \
             any(k in tl for k in ("epf", "pf", "provident")):
            bucket = "EPF"
        elif any(k in nl for k in ("fixed deposit", " fd ", "fd-", "swaritha")):
            bucket = "FD / Debt"
        elif any(k in nl for k in (
            "fund", "nifty", "sensex", " mf", "equity", "flexi", "elss",
            "nippon", "dsp", "motilal", "mirae", "franklin", "paytm money",
            "onetreehill", "one tree", "nasdaq", "parag parikh",
        )) or any(k in tl for k in ("mutual", "fund", "mf", "investment", "brokerage")):
            bucket = "Mutual Funds"
        elif "xxxx" in nl and 0 < bal < 1_000_000:
            bucket = "Mutual Funds"
        else:
            bucket = "Savings / Cash"
        if bal > 0:
            buckets[bucket] = buckets.get(bucket, 0) + bal
    return {k: v for k, v in sorted(buckets.items(), key=lambda x: -x[1])}

def _fy_bounds(ref: date):
    """Return (curr_fy_start, prev_fy_start, prev_fy_end) as pd.Timestamps."""
    if ref.month >= 4:
        curr = pd.Timestamp(ref.year, 4, 1)
        prev = pd.Timestamp(ref.year - 1, 4, 1)
        prev_end = pd.Timestamp(ref.year, 3, 31)
    else:
        curr = pd.Timestamp(ref.year - 1, 4, 1)
        prev = pd.Timestamp(ref.year - 2, 4, 1)
        prev_end = pd.Timestamp(ref.year - 1, 3, 31)
    return curr, prev, prev_end

# ── Column defs ───────────────────────────────────────────────────────────────

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
        # AI advice banner
        html.Div(id="home-ai-banner", className="mb-3"),

        # ── Top 3-panel row ───────────────────────────────────────────────────
        dbc.Row([
            dbc.Col(html.Div([
                html.Div("💳 Current Balances",
                         style={"fontSize": "12px", "fontWeight": "600",
                                "color": "#aaa", "marginBottom": "8px"}),
                html.Div(id="home-accounts-panel"),
            ], className="metric-card", style={"padding": "14px 16px", "height": "100%"}), md=4),

            dbc.Col(html.Div([
                html.Div("📈 Investment Portfolio",
                         style={"fontSize": "12px", "fontWeight": "600",
                                "color": "#aaa", "marginBottom": "8px"}),
                html.Div(id="home-portfolio-panel"),
            ], className="metric-card", style={"padding": "14px 16px", "height": "100%"}), md=4),

            dbc.Col(html.Div([
                html.Div("🎯 Retirement Targets",
                         style={"fontSize": "12px", "fontWeight": "600",
                                "color": "#aaa", "marginBottom": "8px"}),
                html.Div(id="home-retirement-panel"),
            ], className="metric-card", style={"padding": "14px 16px", "height": "100%"}), md=4),
        ], className="mb-3 g-2"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # ── Expense Trends table ──────────────────────────────────────────────
        html.Div(id="home-expense-table", className="mb-3"),

        # ── Monthly stacked bar ───────────────────────────────────────────────
        dbc.Row([
            dbc.Col(dcc.Graph(id="home-stacked-bar", figure=_blank(),
                              config={"displayModeBar": False}), md=12),
        ], className="mb-3"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # ── Transactions ──────────────────────────────────────────────────────
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
        html.Div(id="home-row-detail", style={"marginTop": "6px"}),

        dcc.Store(id="home-row-tag-opts", data=tag_opts),
        dcc.Interval(id="home-refresh", interval=5 * 60 * 1000, n_intervals=0),
    ])


# ── Main refresh callback ─────────────────────────────────────────────────────

@callback(
    Output("home-accounts-panel",   "children"),
    Output("home-portfolio-panel",  "children"),
    Output("home-retirement-panel", "children"),
    Output("home-expense-table",    "children"),
    Output("home-stacked-bar",      "figure"),
    Input("home-refresh",           "n_intervals"),
)
def refresh(_n):
    txn_df = load_transactions()
    acc_df = load_accounts()
    today  = date.today()

    nw      = net_worth(acc_df)
    avg_exp = monthly_expense_avg(txn_df, months=12)
    buckets = _simple_buckets(acc_df) if not acc_df.empty else {}
    total_inv = sum(buckets.values()) or max(nw, 1)

    # ── Accounts panel ────────────────────────────────────────────────────────
    if acc_df.empty:
        acct_panel = html.P("No data", style={"color": "#666", "fontSize": "12px"})
    else:
        rows = []
        for _, r in acc_df.sort_values("computed_balance", key=abs, ascending=False).iterrows():
            bal = float(r.get("computed_balance", 0) or 0)
            col = "#00c49f" if bal >= 0 else "#ff6b6b"
            rows.append(html.Tr([
                html.Td(str(r.get("name", "")),
                        style={"fontSize": "11px", "color": "#e0e0e0",
                               "maxWidth": "140px", "overflow": "hidden",
                               "textOverflow": "ellipsis", "whiteSpace": "nowrap"}),
                html.Td(fmt_inr(bal),
                        style={"fontSize": "12px", "color": col, "fontFamily": "monospace",
                               "textAlign": "right", "whiteSpace": "nowrap"}),
            ]))
        acct_panel = html.Div([
            dbc.Table([html.Tbody(rows)], bordered=False, size="sm",
                      style={"color": "#e0e0e0", "marginBottom": "4px"}),
            html.Div(f"Net worth: {fmt_inr(nw)}",
                     style={"fontSize": "11px", "color": "#555", "marginTop": "4px"}),
        ])

    # ── Portfolio panel ───────────────────────────────────────────────────────
    _BUCKET_COLOURS = {
        "Mutual Funds": "#6c63ff",
        "EPF":          "#ffc107",
        "Metals":       "#f0a500",
        "FD / Debt":    "#e0b840",
        "Savings / Cash": "#26a69a",
    }
    port_rows = []
    for bkt, val in buckets.items():
        pct = val / total_inv * 100
        col = _BUCKET_COLOURS.get(bkt, "#aaa")
        port_rows.append(html.Tr([
            html.Td(bkt,        style={"fontSize": "11px", "color": col}),
            html.Td(fmt_inr(val),
                    style={"fontSize": "12px", "fontFamily": "monospace",
                           "textAlign": "right", "color": "#e0e0e0"}),
            html.Td(f"{pct:.0f}%",
                    style={"fontSize": "10px", "color": "#666",
                           "textAlign": "right", "width": "36px"}),
        ]))
    port_rows.append(html.Tr([
        html.Td("Total", style={"fontSize": "12px", "fontWeight": "700",
                                "color": "#00c49f", "borderTop": "1px solid #2a2a3e"}),
        html.Td(fmt_inr(total_inv),
                style={"fontSize": "13px", "fontFamily": "monospace", "fontWeight": "700",
                       "textAlign": "right", "color": "#00c49f",
                       "borderTop": "1px solid #2a2a3e"}),
        html.Td("", style={"borderTop": "1px solid #2a2a3e"}),
    ]))
    port_panel = dbc.Table([html.Tbody(port_rows)], bordered=False, size="sm",
                           style={"color": "#e0e0e0"})

    # ── Retirement panel ──────────────────────────────────────────────────────
    yrs_fire      = _FIRE_YEAR - today.year
    yrs_edu       = _EDU_YEAR  - today.year
    yrs_post_fire = _EDU_YEAR  - _FIRE_YEAR

    fire_tgt  = _FIRE_MULT * avg_exp * 12
    edu_tgt   = _EDU_USD * (1 + _USD_INFLATION)**yrs_edu * _USD_INR_NOW * (1 + _INR_DEPRECIATION)**yrs_edu

    # Project at base 15% CAGR
    proj_fire = _fv_corpus(total_inv, _TOTAL_MONTHLY_SIP, yrs_fire, 0.15)
    proj_edu  = _fv_corpus(total_inv, _TOTAL_MONTHLY_SIP, yrs_fire, 0.15) * (1 + _POST_FIRE_CAGR)**yrs_post_fire

    def _ret_row(icon, label, year, tgt, proj):
        on_track = proj >= tgt
        col = "#00c49f" if on_track else "#ff6b6b"
        marker = "✓" if on_track else "▼"
        return html.Tr([
            html.Td(f"{icon} {label}", style={"fontSize": "11px", "color": "#e0e0e0"}),
            html.Td(str(year),         style={"fontSize": "11px", "color": "#888"}),
            html.Td(fmt_inr(tgt),      style={"fontSize": "11px", "fontFamily": "monospace",
                                               "textAlign": "right", "color": "#aaa"}),
            html.Td(f"{marker} {fmt_inr(proj)}",
                    style={"fontSize": "11px", "fontFamily": "monospace",
                           "textAlign": "right", "color": col}),
        ])

    ret_panel = html.Div([
        dbc.Table([
            html.Thead(html.Tr([
                html.Th("Goal",   style={"fontSize": "10px", "color": "#555"}),
                html.Th("Year",   style={"fontSize": "10px", "color": "#555"}),
                html.Th("Target", style={"fontSize": "10px", "color": "#555",
                                         "textAlign": "right"}),
                html.Th("@ 15%",  style={"fontSize": "10px", "color": "#555",
                                          "textAlign": "right"}),
            ])),
            html.Tbody([
                _ret_row("🔥", "FIRE",      _FIRE_YEAR, fire_tgt, proj_fire),
                _ret_row("🎓", "Education", _EDU_YEAR,  edu_tgt,  proj_edu),
            ]),
        ], bordered=False, size="sm", style={"color": "#e0e0e0"}),
        html.Div(
            f"Current corpus: {fmt_inr(total_inv)}  ·  SIPs: {fmt_inr(_TOTAL_MONTHLY_SIP)}/mo",
            style={"fontSize": "10px", "color": "#555"},
        ),
    ])

    # ── Expense Trends table ──────────────────────────────────────────────────
    if txn_df.empty:
        exp_table = html.P("No transaction data.", style={"color": "#666"})
    else:
        expenses = txn_df[txn_df["type"] == "expense"].copy()

        first_of_month = pd.Timestamp(today.year, today.month, 1)
        last_mo_end    = first_of_month - pd.Timedelta(days=1)
        last_mo_start  = pd.Timestamp(last_mo_end.year, last_mo_end.month, 1)
        cut3           = first_of_month - pd.DateOffset(months=3)
        curr_fy, prev_fy, prev_fy_end = _fy_bounds(today)

        curr_fy_months = max(
            (today.year - curr_fy.year) * 12 + (today.month - curr_fy.month), 1
        )
        prev_fy_months = 12

        def _cat_total(df, start, end=None):
            sub = df[df["date"] >= start]
            if end is not None:
                sub = sub[sub["date"] <= end]
            return sub.groupby("primary_tag")["amount"].sum()

        last_mo  = _cat_total(expenses, last_mo_start, last_mo_end)
        three_mo = _cat_total(expenses, cut3, first_of_month - pd.Timedelta(days=1))
        curr_fy_s = _cat_total(expenses, curr_fy)
        prev_fy_s = _cat_total(expenses, prev_fy, prev_fy_end)

        # All categories that appear in any period
        all_cats = sorted(
            set(last_mo.index) | set(three_mo.index) |
            set(curr_fy_s.index) | set(prev_fy_s.index),
            key=lambda c: last_mo.get(c, 0), reverse=True,
        )

        tbl_rows = []
        for cat in all_cats:
            lm  = last_mo.get(cat, 0)
            t3  = three_mo.get(cat, 0) / 3
            cfy = curr_fy_s.get(cat, 0) / curr_fy_months
            pfy = prev_fy_s.get(cat, 0) / prev_fy_months
            if lm == 0 and t3 == 0 and cfy == 0 and pfy == 0:
                continue

            def _cell(v, ref=None):
                col = "#e0e0e0"
                if ref and v > 0 and ref > 0:
                    col = "#ff6b6b" if v > ref * 1.15 else ("#00c49f" if v < ref * 0.85 else "#e0e0e0")
                return html.Td(fmt_inr(v) if v else "—",
                               style={"textAlign": "right", "fontSize": "12px",
                                      "fontFamily": "monospace", "color": col})

            tbl_rows.append(html.Tr([
                html.Td(cat, style={"fontSize": "12px", "color": "#e0e0e0"}),
                _cell(lm, t3),
                _cell(t3),
                _cell(cfy, pfy),
                _cell(pfy),
            ]))

        # Totals row
        def _tot(s, divisor=1):
            return s.sum() / divisor if len(s) else 0

        tbl_rows.append(html.Tr([
            html.Td("TOTAL", style={"fontSize": "12px", "fontWeight": "700",
                                    "color": "#aaa", "borderTop": "1px solid #2a2a3e"}),
            *[html.Td(fmt_inr(v),
                      style={"textAlign": "right", "fontSize": "12px",
                             "fontFamily": "monospace", "fontWeight": "600",
                             "color": "#aaa", "borderTop": "1px solid #2a2a3e"})
              for v in [
                  _tot(last_mo),
                  _tot(three_mo) / 3,
                  _tot(curr_fy_s) / curr_fy_months,
                  _tot(prev_fy_s) / prev_fy_months,
              ]],
        ]))

        last_mo_lbl = last_mo_end.strftime("%b %Y")
        exp_table = html.Div([
            html.H6("Expense Trends",
                    style={"color": "#aaa", "fontSize": "13px", "marginBottom": "8px"}),
            dbc.Table(
                [html.Thead(html.Tr([
                    html.Th("Category"),
                    html.Th(last_mo_lbl,      style={"textAlign": "right"}),
                    html.Th("3mo Avg",        style={"textAlign": "right"}),
                    html.Th("Current FY Avg", style={"textAlign": "right"}),
                    html.Th("Last FY Avg",    style={"textAlign": "right"}),
                ]))] + [html.Tbody(tbl_rows)],
                bordered=False, hover=True, responsive=True, size="sm",
                style={"color": "#e0e0e0"},
            ),
            html.Div(
                "Red = >15% above 3-month average · Green = >15% below",
                style={"fontSize": "10px", "color": "#555", "marginTop": "4px"},
            ),
        ])

    # ── Stacked bar — Monthly Expense Trend (12m) ─────────────────────────────
    if txn_df.empty:
        stacked_fig = _dark_fig(go.Figure())
    else:
        expenses12 = txn_df[
            (txn_df["type"] == "expense") &
            (txn_df["date"] >= pd.Timestamp.now() - pd.DateOffset(months=12))
        ].copy()

        # Top N categories by total spend
        top_cats = (
            expenses12.groupby("primary_tag")["amount"].sum()
            .sort_values(ascending=False)
            .head(_TOP_CATS).index.tolist()
        )
        expenses12["_cat"] = expenses12["primary_tag"].apply(
            lambda c: c if c in top_cats else "Other"
        )

        pivot = (
            expenses12.groupby(["month", "_cat"])["amount"].sum()
            .unstack(fill_value=0)
        )
        # Sort months chronologically
        pivot = pivot.sort_index()
        months = pivot.index.tolist()
        month_labels = [pd.to_datetime(m + "-01").strftime("%b %Y") for m in months]

        _COLOURS = [
            "#6c63ff", "#00c49f", "#ffc107", "#ff6b6b",
            "#4a90d9", "#f0a500", "#26a69a", "#888",
        ]
        cat_order = [c for c in top_cats if c in pivot.columns] + \
                    (["Other"] if "Other" in pivot.columns else [])

        traces = []
        for i, cat in enumerate(cat_order):
            vals = pivot.get(cat, pd.Series(0, index=months)).tolist()
            traces.append(go.Bar(
                name=cat, x=months, y=vals,
                marker_color=_COLOURS[i % len(_COLOURS)],
                customdata=month_labels,
                hovertemplate="%{customdata}<br>" + cat + ": ₹%{y:,.0f}<extra></extra>",
            ))

        stacked_fig = go.Figure(traces)
        stacked_fig.update_layout(
            title="Monthly Expense Trend — Last 12 Months (click bar to filter)",
            barmode="stack", showlegend=True,
            xaxis={"tickvals": months, "ticktext": month_labels, "tickangle": -30},
            legend=dict(orientation="h", yanchor="bottom", y=1.02,
                        xanchor="right", x=1),
        )
        _dark_fig(stacked_fig)

    return acct_panel, port_panel, ret_panel, exp_table, stacked_fig


# ── AI advice banner ──────────────────────────────────────────────────────────

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
    total   = max(nw, 1)

    equity_mf = epf = metals = cash = 0.0
    if not acc_df.empty:
        for _, r in acc_df.iterrows():
            nl  = str(r.get("name", "")).lower()
            tl  = str(r.get("type", "")).lower()
            bal = float(r.get("computed_balance", 0) or 0)
            if "epf" in nl or "epf" in tl:
                epf += bal
            elif any(k in nl for k in ("gold", "silver", "axisgold", "icicisilve")):
                metals += bal
            elif any(k in nl for k in ("arbitrage", "liquid", "overnight")):
                cash += bal
            else:
                equity_mf += bal

    fire_target = _FIRE_MULT * annual
    context = {
        "nw_cr":          round(nw / 1e7, 2),
        "fire_pct":       round(nw / fire_target * 100, 1) if fire_target else 0,
        "equity_pct":     round(equity_mf / total * 100, 1),
        "epf_pct":        round(epf        / total * 100, 1),
        "metals_pct":     round(metals     / total * 100, 1),
        "cash_pct":       round(cash       / total * 100, 1),
        "monthly_sip":    round(avg_exp * 0.4),
        "fire_target_cr": round(fire_target / 1e7, 2),
        "edu_target_cr":  round(_EDU_USD * (1.05**15) * 84 * (1.03**15) / 1e7, 2),
        "yrs_to_fire":    _FIRE_YEAR - date.today().year,
        "yrs_to_edu":     _EDU_YEAR  - date.today().year,
    }

    advice = get_portfolio_advice(context)
    if advice is None:
        msgs = []
        if context["equity_pct"] > 80:
            msgs.append(f"Equity at {context['equity_pct']}% — above 80% ceiling; consider rebalancing")
        if context["epf_pct"] + context["cash_pct"] < 15:
            msgs.append("Debt allocation below 15% — EPF + cash is low")
        if context["fire_pct"] < 15:
            msgs.append(f"FIRE progress {context['fire_pct']}% — early stage, stay the course")
        if not msgs:
            msgs = ["Allocation looks balanced. Keep SIPs running."]
        advice = {"color": "secondary", "headline": "Portfolio Snapshot", "points": msgs}

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
                html.Div(advice.get("headline", ""),
                         style={"fontWeight": "600", "fontSize": "13px",
                                "color": fg, "marginBottom": "4px"}),
                html.Ul([
                    html.Li(p, style={"fontSize": "12px", "color": "#ccc", "marginBottom": "2px"})
                    for p in advice.get("points", [])
                ], style={"paddingLeft": "18px", "margin": 0}),
            ]),
            dbc.Col(html.Div("🤖 AI Advisor",
                             style={"fontSize": "10px", "color": "#555", "textAlign": "right"}),
                    width="auto"),
        ], className="align-items-start"),
    ], style={"background": bg, "border": f"1px solid {fg}",
              "borderRadius": "8px", "padding": "12px 16px"})


# ── Transactions drill-down ───────────────────────────────────────────────────

@callback(
    Output("home-txn-grid",    "rowData"),
    Output("home-txn-label",   "children"),
    Output("home-txn-caption", "children"),
    Output("home-row-detail",  "children", allow_duplicate=True),
    Input("home-stacked-bar",  "clickData"),
    Input("home-txn-clear",    "n_clicks"),
    Input("home-refresh",      "n_intervals"),
    prevent_initial_call="initial_duplicate",
)
def drill_transactions(bar_click, _clear, _refresh):
    df       = load_transactions()
    txns     = df.copy() if not df.empty else df
    label    = "Recent transactions — click a bar segment to filter by month"
    triggered = ctx.triggered_id

    if triggered == "home-stacked-bar" and bar_click:
        pt    = bar_click["points"][0]
        month = pt["x"]
        cat   = pt.get("data", {}).get("name", None)
        txns  = txns[txns["month"] == month]
        try:
            month_lbl = pd.to_datetime(month + "-01").strftime("%b %Y")
        except Exception:
            month_lbl = month
        if cat and cat not in ("Other",):
            txns  = txns[txns["primary_tag"] == cat]
            label = f"{cat} · {month_lbl}"
        else:
            label = f"Expenses in {month_lbl}"
    else:
        cut3 = pd.Timestamp.now() - pd.DateOffset(months=3)
        txns = txns[txns["date"] >= cut3]

    rows    = _to_rows(txns)
    caption = f"{len(rows):,} transactions · click a row to view / edit tags"
    _EXPLICIT = {"home-stacked-bar", "home-txn-clear"}
    close_panel = triggered in _EXPLICIT
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
            dbc.Col(
                dbc.Button("✓ Save Tag", id="home-row-save-btn", color="primary",
                           size="sm", className="w-100"),
                md=3,
            ),
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
    updated = [{**r, "tags": tags_str} if r.get("id") == row_id else r
               for r in (row_data or [])]
    return dbc.Alert(f"✅ Saved: {tags_str}", color="success", duration=3000), updated


@callback(
    Output("home-row-detail",  "children", allow_duplicate=True),
    Output("home-txn-grid",    "selectedRows"),
    Input("home-row-close",    "n_clicks"),
    prevent_initial_call=True,
)
def home_close_detail(_):
    return None, []
