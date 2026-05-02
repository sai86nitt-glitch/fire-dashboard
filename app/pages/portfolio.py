"""
Portfolio page — investment tracker, corpus goals, SIP summary.

Shows:
  • Where the money lives (by asset class)
  • FIRE 2036 + Education 2041 corpus progress
  • Projected corpus table (3 scenarios × 3 horizons)
  • SIP flow detected from recent transactions
"""

import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
from datetime import date

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import load_transactions, load_accounts, fmt_inr, net_worth, monthly_expense_avg

dash.register_page(__name__, path="/portfolio", title="Portfolio")

# ── Profile constants (from investor profile) ────────────────────────────────
_FIRE_MULT       = 25
_FIRE_YEAR       = 2036
_EDU_YEAR        = 2041
_EDU_USD         = 400_000      # $4 L today
_USD_INR_NOW     = 84           # current exchange rate
_USD_INFLATION   = 0.05         # 5% p.a. USD inflation
_INR_DEPRECIATION= 0.03         # 3% p.a. INR vs USD
_SPEND_INFLATION = 0.06         # 6% p.a. expense inflation
_MONTHLY_EQUITY_SIP  = 250_000  # ₹2.5 L/month equity SIPs
_MONTHLY_METALS_SIP  =  30_000  # ₹30 K/month metals SIPs
_MONTHLY_EPF     =  72_000      # ₹72 K/month EPF accrual
_TOTAL_MONTHLY_SIP = _MONTHLY_EQUITY_SIP + _MONTHLY_METALS_SIP + _MONTHLY_EPF

_CAGR = {"Conservative (12%)": 0.12, "Base (15%)": 0.15, "Optimistic (18%)": 0.18}


def _blank():
    f = go.Figure()
    f.update_layout(paper_bgcolor="#1e1e2e", plot_bgcolor="#1e1e2e",
                    xaxis={"visible": False}, yaxis={"visible": False},
                    margin={"t": 10, "b": 10, "l": 10, "r": 10})
    return f


def layout():
    return html.Div([
        html.H3("📊 Portfolio", style={"marginBottom": "16px"}),

        # ── Asset allocation ─────────────────────────────────────────────────
        dbc.Row(id="port-metrics", className="mb-3 g-2"),
        dbc.Row([
            dbc.Col(dcc.Graph(id="port-alloc-pie",  figure=_blank(),
                              config={"displayModeBar": False}), md=5),
            dbc.Col(dcc.Graph(id="port-alloc-bar",  figure=_blank(),
                              config={"displayModeBar": False}), md=7),
        ], className="mb-3 g-2"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # ── Corpus goals ─────────────────────────────────────────────────────
        html.H5("🎯 Corpus Goals", style={"color": "#aaa", "marginBottom": "12px"}),
        dbc.Row(id="port-goals", className="mb-3 g-3"),
        dbc.Row([
            dbc.Col(html.Div(id="port-projection-table"), md=12),
        ], className="mb-3"),

        html.Hr(style={"borderColor": "#2a2a3e"}),

        # ── SIP tracker ──────────────────────────────────────────────────────
        html.H5("📅 Monthly Deployment", style={"color": "#aaa", "marginBottom": "12px"}),
        dbc.Row(id="port-sip", className="mb-3 g-2"),
        dbc.Row([
            dbc.Col(dcc.Graph(id="port-sip-trend", figure=_blank(),
                              config={"displayModeBar": False}), md=12),
        ], className="mb-3"),

        dcc.Interval(id="port-refresh", interval=5 * 60 * 1000, n_intervals=0),
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dark_fig(fig):
    fig.update_layout(
        paper_bgcolor="#1e1e2e", plot_bgcolor="#13131f",
        font={"color": "#e0e0e0", "size": 11},
        margin={"t": 40, "b": 36, "l": 12, "r": 12},
    )
    return fig


def _card(label, value, colour="#e0e0e0", sub=None):
    children = [
        html.Div(label, className="metric-label"),
        html.Div(value, className="metric-value", style={"color": colour}),
    ]
    if sub:
        children.append(html.Div(sub, style={"fontSize": "11px", "color": "#666", "marginTop": "2px"}))
    return dbc.Col(html.Div(children, className="metric-card"), md=3)


def _categorise(acc_df: pd.DataFrame) -> dict:
    """Map account rows to asset-class buckets."""
    buckets: dict[str, float] = {
        "Equity MF": 0.0,
        "International": 0.0,
        "Gold": 0.0,
        "Silver": 0.0,
        "EPF": 0.0,
        "Cash / Arbitrage": 0.0,
        "Other": 0.0,
    }
    for _, row in acc_df.iterrows():
        name = str(row.get("name", "")).lower()
        typ  = str(row.get("type", "")).lower()
        bal  = float(row.get("computed_balance", 0) or 0)
        if bal <= 0:
            continue
        if "epf" in name or "epf" in typ:
            buckets["EPF"] += bal
        elif "silver" in name or "icicisilve" in name:
            buckets["Silver"] += bal
        elif "gold" in name or "axisgold" in name:
            buckets["Gold"] += bal
        elif any(k in name for k in ("nasdaq", "international", "parag parikh", "motilal")):
            buckets["International"] += bal
        elif any(k in name for k in ("arbitrage", "liquid", "overnight")):
            buckets["Cash / Arbitrage"] += bal
        elif any(k in typ for k in ("mutual", "equity", "fund", "mf", "etf")):
            buckets["Equity MF"] += bal
        else:
            buckets["Other"] += bal
    return {k: v for k, v in buckets.items() if v > 0}


def _fv_corpus(current: float, monthly_sip: float, years: int, cagr: float) -> float:
    """Future value of current corpus + level monthly SIPs."""
    r = cagr / 12
    n = years * 12
    sip_fv = monthly_sip * ((1 + r) ** n - 1) / r if r else monthly_sip * n
    return current * (1 + cagr) ** years + sip_fv


def _fire_target(avg_monthly_exp: float, years_away: int) -> float:
    """FIRE corpus needed in `years_away` years (inflation-adjusted spend × 25)."""
    annual_then = avg_monthly_exp * 12 * (1 + _SPEND_INFLATION) ** years_away
    return _FIRE_MULT * annual_then


def _edu_target_inr(years_away: int) -> float:
    """Education corpus in INR needed `years_away` years from now."""
    usd_then     = _EDU_USD * (1 + _USD_INFLATION) ** years_away
    inr_per_usd  = _USD_INR_NOW * (1 + _INR_DEPRECIATION) ** years_away
    return usd_then * inr_per_usd


def _progress_card(title: str, icon: str, current: float, target: float,
                   year: int, colour: str) -> dbc.Col:
    pct     = min(current / target * 100, 100) if target else 0
    gap     = max(target - current, 0)
    bar_col = "#00c49f" if pct >= 75 else ("#ffc107" if pct >= 40 else "#ff6b6b")
    return dbc.Col(html.Div([
        html.Div(f"{icon} {title}", style={"fontSize": "13px", "fontWeight": "600",
                                           "color": "#e0e0e0", "marginBottom": "8px"}),
        dbc.Progress(value=pct, label=f"{pct:.1f}%",
                     style={"height": "22px", "backgroundColor": "#2a2a3e"},
                     color=bar_col, className="mb-2"),
        dbc.Row([
            dbc.Col(html.Div([
                html.Div("Current", className="metric-label"),
                html.Div(fmt_inr(current), style={"fontSize": "14px", "fontWeight": "600",
                                                   "color": "#6c63ff"}),
            ])),
            dbc.Col(html.Div([
                html.Div(f"Target ({year})", className="metric-label"),
                html.Div(fmt_inr(target), style={"fontSize": "14px", "fontWeight": "600",
                                                  "color": colour}),
            ])),
            dbc.Col(html.Div([
                html.Div("Gap", className="metric-label"),
                html.Div(fmt_inr(gap), style={"fontSize": "14px", "fontWeight": "600",
                                               "color": "#ff6b6b" if gap > 0 else "#00c49f"}),
            ])),
        ]),
    ], className="metric-card", style={"padding": "16px 20px"}), md=6)


# ── Main callback ─────────────────────────────────────────────────────────────

@callback(
    Output("port-metrics",         "children"),
    Output("port-alloc-pie",       "figure"),
    Output("port-alloc-bar",       "figure"),
    Output("port-goals",           "children"),
    Output("port-projection-table","children"),
    Output("port-sip",             "children"),
    Output("port-sip-trend",       "figure"),
    Input("port-refresh",          "n_intervals"),
)
def refresh_portfolio(_n):
    txn_df = load_transactions()
    acc_df = load_accounts()
    today  = date.today()

    nw         = net_worth(acc_df)
    avg_exp    = monthly_expense_avg(txn_df, months=12)
    buckets    = _categorise(acc_df) if not acc_df.empty else {}
    total_inv  = sum(buckets.values()) or nw
    equity_mf  = buckets.get("Equity MF", 0) + buckets.get("International", 0)
    epf        = buckets.get("EPF", 0)
    metals     = buckets.get("Gold", 0) + buckets.get("Silver", 0)
    cash       = buckets.get("Cash / Arbitrage", 0)

    # ── Metric cards ─────────────────────────────────────────────────────────
    equity_pct = equity_mf / total_inv * 100 if total_inv else 0
    epf_pct    = epf       / total_inv * 100 if total_inv else 0
    metals_pct = metals    / total_inv * 100 if total_inv else 0

    metrics = [
        _card("Total Investable", fmt_inr(total_inv), "#00c49f"),
        _card("Equity MF",        fmt_inr(equity_mf), "#6c63ff",
              sub=f"{equity_pct:.1f}% of portfolio"),
        _card("EPF (Debt)",       fmt_inr(epf),        "#ffc107",
              sub=f"{epf_pct:.1f}% · target 20-25%"),
        _card("Metals",           fmt_inr(metals),     "#f0a500",
              sub=f"{metals_pct:.1f}% · target 5-8%"),
    ]

    # ── Allocation pie ────────────────────────────────────────────────────────
    colours = ["#6c63ff", "#4a90d9", "#f0a500", "#c0a030",
               "#ffc107", "#00c49f", "#888"]
    pie = go.Figure([go.Pie(
        labels=list(buckets.keys()),
        values=list(buckets.values()),
        hole=0.42,
        textinfo="percent+label",
        marker_colors=colours[:len(buckets)],
        hovertemplate="%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>",
    )])
    pie.update_layout(title="Asset Allocation", showlegend=False)
    _dark_fig(pie)

    # ── Actual vs target bar ──────────────────────────────────────────────────
    _TARGETS = {
        "Equity MF":      (70, 75),
        "International":  (10, 15),   # of equity
        "Gold":           (3,  5),
        "Silver":         (2,  3),
        "EPF":            (20, 25),
        "Cash / Arbitrage": (0, 5),
    }
    bar_cats, bar_actual, bar_lo, bar_hi = [], [], [], []
    for cat, (lo, hi) in _TARGETS.items():
        val = buckets.get(cat, 0)
        pct = val / total_inv * 100 if total_inv else 0
        bar_cats.append(cat)
        bar_actual.append(round(pct, 1))
        bar_lo.append(lo)
        bar_hi.append(hi)

    alloc_bar = go.Figure([
        go.Bar(name="Actual %",    x=bar_cats, y=bar_actual,
               marker_color="#6c63ff",
               hovertemplate="%{x}: %{y:.1f}%<extra></extra>"),
        go.Scatter(name="Target low",  x=bar_cats, y=bar_lo,
                   mode="markers", marker=dict(symbol="line-ew", size=14,
                   color="#00c49f", line=dict(width=2, color="#00c49f")),
                   hovertemplate="Min target: %{y}%<extra></extra>"),
        go.Scatter(name="Target high", x=bar_cats, y=bar_hi,
                   mode="markers", marker=dict(symbol="line-ew", size=14,
                   color="#ff6b6b", line=dict(width=2, color="#ff6b6b")),
                   hovertemplate="Max target: %{y}%<extra></extra>"),
    ])
    alloc_bar.update_layout(title="Allocation vs Target Band",
                            barmode="group", showlegend=True,
                            legend=dict(orientation="h", yanchor="bottom",
                                        y=1.02, xanchor="right", x=1))
    _dark_fig(alloc_bar)

    # ── Corpus goals ──────────────────────────────────────────────────────────
    yrs_fire = _FIRE_YEAR - today.year
    yrs_edu  = _EDU_YEAR  - today.year
    fire_tgt = _fire_target(avg_exp, yrs_fire)
    edu_tgt  = _edu_target_inr(yrs_edu)

    goals = [
        _progress_card("FIRE Corpus", "🔥", nw, fire_tgt, _FIRE_YEAR, "#6c63ff"),
        _progress_card("Education Corpus", "🎓", nw, edu_tgt, _EDU_YEAR, "#00c49f"),
    ]
    goals.append(dbc.Col(html.Div(
        f"Education target: ${_EDU_USD//1000}L USD today → "
        f"{fmt_inr(edu_tgt)} in {_EDU_YEAR} "
        f"(5% USD inflation + 3% INR/USD depreciation)",
        style={"fontSize": "11px", "color": "#666", "marginTop": "4px"},
    ), md=12))

    # ── Projection table ──────────────────────────────────────────────────────
    this_yr = today.year
    horizons = [
        (f"2036 ({_FIRE_YEAR - this_yr} yrs)",  _FIRE_YEAR - this_yr, fire_tgt,  "🔥 FIRE"),
        (f"2038 ({_FIRE_YEAR - this_yr + 2} yrs)", _FIRE_YEAR - this_yr + 2, None, ""),
        (f"2041 ({_EDU_YEAR  - this_yr} yrs)",  _EDU_YEAR  - this_yr, edu_tgt,   "🎓 Edu"),
    ]
    proj_header = html.Thead(html.Tr([
        html.Th("Year"),
        html.Th("Goal"),
        html.Th("Need", style={"textAlign": "right"}),
        *[html.Th(label, style={"textAlign": "right"}) for label in _CAGR],
    ]))
    proj_body_rows = []
    for label, yrs, tgt, goal_tag in horizons:
        cells = [
            html.Td(label,    style={"fontSize": "12px", "color": "#888"}),
            html.Td(goal_tag, style={"fontSize": "11px", "color": "#aaa"}),
            html.Td(fmt_inr(tgt) if tgt else "—",
                    style={"textAlign": "right", "fontSize": "12px", "color": "#ff6b6b",
                           "fontFamily": "monospace"}),
        ]
        cagr_colours = ["#6c63ff", "#00c49f", "#ffc107"]
        for i, cagr in enumerate(_CAGR.values()):
            proj = _fv_corpus(nw, _TOTAL_MONTHLY_SIP, yrs, cagr)
            on_track = tgt is None or proj >= tgt
            cells.append(html.Td(
                [fmt_inr(proj), " ✓" if on_track else " ✗"],
                style={"textAlign": "right", "fontSize": "12px",
                       "fontFamily": "monospace", "color": cagr_colours[i]},
            ))
        proj_body_rows.append(html.Tr(cells))

    proj_table = html.Div([
        html.Div("Projected corpus (current ₹ + ₹3.02 L/month SIPs)",
                 style={"color": "#aaa", "fontSize": "12px", "marginBottom": "8px",
                        "fontStyle": "italic"}),
        dbc.Table(
            [proj_header, html.Tbody(proj_body_rows)],
            bordered=False, size="sm",
            style={"color": "#e0e0e0"},
        ),
        html.Div("SIPs assumed: ₹2.5 L equity MF + ₹30 K metals + ₹72 K EPF = ₹3.02 L/month",
                 style={"fontSize": "11px", "color": "#555", "marginTop": "6px"}),
    ])

    # ── SIP tracker from transactions ─────────────────────────────────────────
    if txn_df.empty:
        sip_metrics = [_card("Monthly SIPs", "No data", "#888")]
        sip_trend   = _dark_fig(go.Figure())
    else:
        cut6 = pd.Timestamp.now() - pd.DateOffset(months=6)
        inv  = txn_df[
            txn_df["date"] >= cut6
        ].copy()
        # Detect investment outflows: SIP/fund/ETF in description or tags
        inv_mask = (
            inv["description"].str.contains(
                r"SIP|Systematic|Mutual|Nifty|Flexi|Emerging|Small Cap|Nasdaq|AXISGOLD|ICICISILVE",
                case=False, na=False, regex=True,
            ) |
            inv["tags"].str.contains(
                r"SIP|Investment|Mutual Fund|ETF|Equity",
                case=False, na=False, regex=True,
            )
        ) & inv["type"].isin(["expense", "transfer"])

        sip_df = inv[inv_mask]
        if sip_df.empty:
            sip_monthly = pd.DataFrame()
            detected_avg = 0.0
        else:
            sip_monthly  = sip_df.groupby("month")["amount"].sum().reset_index().sort_values("month")
            detected_avg = float(sip_monthly["amount"].mean())

        sip_metrics = [
            _card("Detected Avg SIP/mo",  fmt_inr(detected_avg), "#6c63ff"),
            _card("Profile Target SIP",   fmt_inr(_TOTAL_MONTHLY_SIP), "#00c49f"),
            _card("Coverage",
                  f"{detected_avg/_TOTAL_MONTHLY_SIP*100:.0f}%" if _TOTAL_MONTHLY_SIP else "—",
                  "#ffc107",
                  sub="of profiled ₹3.02 L/mo"),
        ]

        if sip_monthly.empty:
            sip_trend = _dark_fig(go.Figure())
        else:
            sip_monthly["label"] = sip_monthly["month"].apply(
                lambda v: pd.to_datetime(v + "-01").strftime("%b %Y") if v else v
            )
            sip_trend = go.Figure([
                go.Bar(x=sip_monthly["month"], y=sip_monthly["amount"],
                       marker_color="#6c63ff",
                       text=sip_monthly["label"], textposition="none",
                       hovertemplate="%{text}<br>₹%{y:,.0f}<extra></extra>"),
            ])
            sip_trend.add_hline(y=_TOTAL_MONTHLY_SIP, line_dash="dash",
                                line_color="#00c49f",
                                annotation_text=f"Target {fmt_inr(_TOTAL_MONTHLY_SIP)}",
                                annotation_position="top right")
            sip_trend.update_layout(title="Detected Investment Outflows (6 months)", showlegend=False)
            _dark_fig(sip_trend)

    return metrics, pie, alloc_bar, goals, proj_table, sip_metrics, sip_trend
