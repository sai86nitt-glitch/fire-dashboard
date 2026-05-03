"""
Portfolio — holdings by asset class, allocation, corpus goals, SIP tracker.
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

# ── Profile constants ─────────────────────────────────────────────────────────
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
_CAGR               = {"Conservative (12%)": 0.12, "Base (15%)": 0.15, "Optimistic (18%)": 0.18}
_POST_FIRE_CAGR     = 0.10

# Asset-class display order and metadata
_ASSET_CLASSES = [
    ("Equity MF",        "📈", "#6c63ff", (65, 75)),
    ("International MF", "🌐", "#4a90d9", ( 8, 15)),
    ("Gold",             "🥇", "#f0a500", ( 3,  5)),
    ("Silver",           "🥈", "#c0a030", ( 2,  3)),
    ("EPF / Debt",       "🏦", "#ffc107", (20, 25)),
    ("FD / Debt",        "📄", "#e0b840", ( 0,  5)),
    ("Cash / Arbitrage", "💵", "#00c49f", ( 0,  5)),
    ("Savings / Cash",   "🏧", "#26a69a", ( 0,  5)),
    ("Other",            "❓", "#888",    ( 0,  0)),
]
_CLASS_META = {name: (icon, col, tgt) for name, icon, col, tgt in _ASSET_CLASSES}


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


def layout():
    return html.Div([
        html.H3("📊 Portfolio", style={"marginBottom": "16px"}),

        # ── Holdings by asset class ──────────────────────────────────────────
        html.H5("🗂️ Holdings", style={"color": "#aaa", "marginBottom": "12px"}),
        html.Div(id="port-holdings"),

        html.Hr(style={"borderColor": "#2a2a3e", "margin": "20px 0 16px"}),

        # ── Allocation overview ──────────────────────────────────────────────
        html.H5("📐 Allocation", style={"color": "#aaa", "marginBottom": "12px"}),
        dbc.Row(id="port-metrics", className="mb-3 g-2"),
        dbc.Row([
            dbc.Col(dcc.Graph(id="port-alloc-pie", figure=_blank(),
                              config={"displayModeBar": False}), md=5),
            dbc.Col(dcc.Graph(id="port-alloc-bar", figure=_blank(),
                              config={"displayModeBar": False}), md=7),
        ], className="mb-3 g-2"),

        html.Hr(style={"borderColor": "#2a2a3e", "margin": "4px 0 16px"}),

        # ── Corpus goals ─────────────────────────────────────────────────────
        html.H5("🎯 Goals & Projections", style={"color": "#aaa", "marginBottom": "12px"}),
        dbc.Row(id="port-goals", className="mb-3 g-3"),
        html.Div(id="port-projection-table", className="mb-3"),

        html.Hr(style={"borderColor": "#2a2a3e", "margin": "4px 0 16px"}),

        # ── SIP tracker ──────────────────────────────────────────────────────
        html.H5("📅 Monthly Deployment", style={"color": "#aaa", "marginBottom": "12px"}),
        dbc.Row(id="port-sip", className="mb-3 g-2"),
        dcc.Graph(id="port-sip-trend", figure=_blank(),
                  config={"displayModeBar": False}, className="mb-3"),

        dcc.Interval(id="port-refresh", interval=5 * 60 * 1000, n_intervals=0),
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card(label, value, colour="#e0e0e0", sub=None):
    children = [
        html.Div(label, className="metric-label"),
        html.Div(value, className="metric-value", style={"color": colour}),
    ]
    if sub:
        children.append(html.Div(sub, style={"fontSize": "11px", "color": "#666", "marginTop": "2px"}))
    return dbc.Col(html.Div(children, className="metric-card"), md=3)


def _categorise(acc_df: pd.DataFrame) -> tuple[dict, list[dict]]:
    buckets: dict[str, float] = {}
    account_rows: list[dict] = []
    for _, row in acc_df.iterrows():
        name = str(row.get("name", "")).strip()
        typ  = str(row.get("type", "")).strip()
        bal  = float(row.get("computed_balance", 0) or 0)
        nl   = name.lower(); tl = typ.lower()

        if any(k in nl for k in ("loan", " cc", "credit card", "amex", "visa", "mastercard")) or \
           any(k in tl for k in ("credit", "loan", "mortgage")):
            bucket = "Credit / Loans"
        elif any(k in nl for k in ("epf", "provident", " pf", "pension")) or \
             any(k in tl for k in ("epf", "pf", "provident", "pension")):
            bucket = "EPF / Debt"
        elif any(k in nl for k in ("gold", "axisgold", "sgb", "sovereign gold", "digital gold")) or \
             tl == "gold":
            bucket = "Gold"
        elif any(k in nl for k in ("silver", "icicisilve", "silv")) or tl == "silver":
            bucket = "Silver"
        elif any(k in nl for k in (
            "nasdaq", "s&p 500", "sp500", "global", "international",
            "overseas", "us equity", "world", "parag parikh",
        )) or "international" in tl:
            bucket = "International MF"
        elif any(k in nl for k in ("arbitrage", "liquid fund", "overnight", "money market")) or \
             any(k in tl for k in ("arbitrage", "liquid")):
            bucket = "Cash / Arbitrage"
        elif any(k in nl for k in ("fixed deposit", " fd ", "fd-", "-fd", "swaritha")) or \
             tl in ("fd", "fixed deposit"):
            bucket = "FD / Debt"
        elif "paytm money" in nl or "onetreehill" in nl or "one tree" in nl:
            bucket = "Equity MF"
        elif any(k in nl for k in (
            "fund", " mf", "nifty", "sensex", "midcap", "small cap", "large cap",
            "flexi", "equity", "bluechip", "balanced", "hybrid", "elss",
            "nippon", "dsp", "motilal", "tata mf", "invesco", "pgim", "franklin",
            "mirae", "uti", "icici pru", "sbi mf",
        )) or any(k in tl for k in ("mutual", "fund", "mf", "investment", "brokerage", "etf")):
            bucket = "Equity MF"
        elif "xxxx" in nl and 0 < bal < 1_000_000:
            bucket = "Equity MF"
        elif any(k in tl for k in ("savings", "checking", "current", "salary", "bank")) or \
             any(k in nl for k in (
                 "salary", "minor", "savings", "current a/c",
                 "hdfc bank", "sbi bank", "icici bank", "kotak bank",
                 "axis bank", "federal bank", "yes bank",
             )):
            bucket = "Savings / Cash"
        else:
            bucket = typ if (typ and typ not in ("", "nan", "None")) else "Other"

        account_rows.append({"name": name, "type": typ or "—", "bucket": bucket, "bal": bal})
        if bucket != "Credit / Loans":
            buckets[bucket] = buckets.get(bucket, 0) + bal

    return (
        {k: v for k, v in sorted(buckets.items(), key=lambda x: -x[1]) if v > 0},
        account_rows,
    )


def _fv_corpus(current, monthly_sip, years, cagr):
    r = cagr / 12; n = years * 12
    sip_fv = monthly_sip * ((1 + r) ** n - 1) / r if r else monthly_sip * n
    return current * (1 + cagr) ** years + sip_fv

def _fire_target(avg_monthly_exp):
    return _FIRE_MULT * avg_monthly_exp * 12

def _edu_target_inr(years_away):
    return _EDU_USD * (1 + _USD_INFLATION)**years_away * _USD_INR_NOW * (1 + _INR_DEPRECIATION)**years_away


# ── Main callback ─────────────────────────────────────────────────────────────

@callback(
    Output("port-holdings",          "children"),
    Output("port-metrics",           "children"),
    Output("port-alloc-pie",         "figure"),
    Output("port-alloc-bar",         "figure"),
    Output("port-goals",             "children"),
    Output("port-projection-table",  "children"),
    Output("port-sip",               "children"),
    Output("port-sip-trend",         "figure"),
    Input("port-refresh",            "n_intervals"),
)
def refresh_portfolio(_n):
    txn_df = load_transactions()
    acc_df = load_accounts()
    today  = date.today()

    nw      = net_worth(acc_df)
    avg_exp = monthly_expense_avg(txn_df, months=12)
    buckets, account_rows = _categorise(acc_df) if not acc_df.empty else ({}, [])
    total_inv = sum(buckets.values()) or max(nw, 1)
    equity_mf = buckets.get("Equity MF", 0) + buckets.get("International MF", 0)
    epf       = buckets.get("EPF / Debt", 0)
    metals    = buckets.get("Gold", 0) + buckets.get("Silver", 0)

    # ── Holdings grouped by asset class ──────────────────────────────────────
    # Group account_rows by bucket, ordered by asset class importance
    from collections import defaultdict
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in account_rows:
        by_bucket[r["bucket"]].append(r)

    holding_sections = []
    # Show all buckets in defined order, then any leftovers
    shown = set()
    ordered_buckets = [name for name, *_ in _ASSET_CLASSES]

    for bkt_name in ordered_buckets + sorted(set(by_bucket.keys()) - set(ordered_buckets)):
        rows_in_bucket = by_bucket.get(bkt_name, [])
        if not rows_in_bucket:
            continue
        shown.add(bkt_name)

        icon, col, (tgt_lo, tgt_hi) = _CLASS_META.get(bkt_name, ("•", "#888", (0, 0)))
        bkt_total = sum(r["bal"] for r in rows_in_bucket if r["bal"] > 0)
        bkt_pct   = bkt_total / total_inv * 100 if total_inv else 0
        is_liability = bkt_name == "Credit / Loans"

        # Header row for this bucket
        tgt_str = f"target {tgt_lo}–{tgt_hi}%" if tgt_lo or tgt_hi else ""
        pct_col = "#00c49f" if (not tgt_hi or bkt_pct <= tgt_hi) else "#ff6b6b"

        header = html.Div([
            html.Span(f"{icon} {bkt_name}",
                      style={"fontWeight": "600", "fontSize": "13px", "color": col}),
            html.Span(f"  {fmt_inr(bkt_total)}" if not is_liability else "",
                      style={"fontSize": "13px", "color": "#e0e0e0",
                             "fontFamily": "monospace", "marginLeft": "12px"}),
            html.Span(f"  {bkt_pct:.1f}%",
                      style={"fontSize": "11px", "color": pct_col, "marginLeft": "6px"}),
            html.Span(f"  ·  {tgt_str}" if tgt_str else "",
                      style={"fontSize": "10px", "color": "#555", "marginLeft": "6px"}),
        ], style={"marginBottom": "4px", "marginTop": "2px"})

        # Individual account rows
        acct_rows = []
        for r in sorted(rows_in_bucket, key=lambda x: -abs(x["bal"])):
            bal_col = col if r["bal"] > 0 else "#ff6b6b"
            acct_rows.append(html.Tr([
                html.Td(r["name"],
                        style={"fontSize": "12px", "color": "#ccc", "paddingLeft": "20px",
                               "maxWidth": "260px", "overflow": "hidden",
                               "textOverflow": "ellipsis", "whiteSpace": "nowrap"}),
                html.Td(r["type"],
                        style={"fontSize": "10px", "color": "#555"}),
                html.Td(fmt_inr(r["bal"]),
                        style={"textAlign": "right", "fontFamily": "monospace",
                               "fontSize": "12px", "color": bal_col}),
            ]))

        # Subtotal if multiple accounts
        if len(acct_rows) > 1 and not is_liability:
            acct_rows.append(html.Tr([
                html.Td("", style={"paddingLeft": "20px"}),
                html.Td("subtotal", style={"fontSize": "10px", "color": "#444"}),
                html.Td(fmt_inr(bkt_total),
                        style={"textAlign": "right", "fontFamily": "monospace",
                               "fontSize": "12px", "color": col, "fontWeight": "600",
                               "borderTop": "1px solid #2a2a3e"}),
            ]))

        # Note for Equity MF about embedded ETFs
        note = None
        if bkt_name == "Equity MF":
            note = html.Div(
                "ⓘ Gold ETF (AXISGOLD) and Silver ETF (ICICISILVE) are embedded inside "
                "your brokerage account — they count here, not in Metals.",
                style={"fontSize": "10px", "color": "#444", "marginTop": "4px",
                       "paddingLeft": "20px"},
            )

        holding_sections.append(html.Div([
            header,
            dbc.Table([html.Tbody(acct_rows)], bordered=False, size="sm",
                      style={"color": "#e0e0e0", "marginBottom": "2px"}),
            note or html.Div(),
        ], style={"marginBottom": "12px"}))

    if not holding_sections:
        holding_sections = [html.P("No account data.", style={"color": "#666"})]

    holdings_div = html.Div(holding_sections)

    # ── Metric cards ─────────────────────────────────────────────────────────
    equity_pct = equity_mf / total_inv * 100 if total_inv else 0
    epf_pct    = epf       / total_inv * 100 if total_inv else 0
    metals_pct = metals    / total_inv * 100 if total_inv else 0
    metrics = [
        _card("Total Investable", fmt_inr(total_inv), "#00c49f"),
        _card("Equity MF",        fmt_inr(equity_mf), "#6c63ff",
              sub=f"{equity_pct:.1f}% · target 70–75%"),
        _card("EPF / Debt",       fmt_inr(epf),       "#ffc107",
              sub=f"{epf_pct:.1f}% · target 20–25%"),
        _card("Metals",           fmt_inr(metals),    "#f0a500",
              sub=f"{metals_pct:.1f}% · target 5–8%"),
    ]

    # ── Allocation pie ────────────────────────────────────────────────────────
    _BUCKET_COLOURS = {name: col for name, _, col, _ in _ASSET_CLASSES}
    bucket_labels  = list(buckets.keys())
    bucket_vals    = list(buckets.values())
    bucket_colours = [_BUCKET_COLOURS.get(l, "#aaa") for l in bucket_labels]

    pie = go.Figure([go.Pie(
        labels=bucket_labels, values=bucket_vals,
        hole=0.42, textinfo="percent+label",
        textfont={"size": 10}, marker_colors=bucket_colours,
        hovertemplate="%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>",
    )])
    pie.update_layout(title="Asset Allocation", showlegend=False)
    _dark_fig(pie)

    # ── Allocation vs target bar ──────────────────────────────────────────────
    bar_cats, bar_actual, bar_lo, bar_hi = [], [], [], []
    for name, _, _, (lo, hi) in _ASSET_CLASSES:
        if not lo and not hi:
            continue
        val = buckets.get(name, 0)
        pct = val / total_inv * 100 if total_inv else 0
        bar_cats.append(name); bar_actual.append(round(pct, 1))
        bar_lo.append(lo); bar_hi.append(hi)

    alloc_bar = go.Figure([
        go.Bar(name="Actual %", x=bar_cats, y=bar_actual,
               marker_color="#6c63ff",
               text=[f"{v:.1f}%" for v in bar_actual],
               textposition="outside", textfont={"size": 10, "color": "#aaa"},
               cliponaxis=False,
               hovertemplate="%{x}: %{y:.1f}%<extra></extra>"),
        go.Scatter(name="Target min", x=bar_cats, y=bar_lo, mode="markers",
                   marker=dict(symbol="line-ew", size=16, color="#00c49f",
                               line=dict(width=2, color="#00c49f")),
                   hovertemplate="Min: %{y}%<extra></extra>"),
        go.Scatter(name="Target max", x=bar_cats, y=bar_hi, mode="markers",
                   marker=dict(symbol="line-ew", size=16, color="#ff6b6b",
                               line=dict(width=2, color="#ff6b6b")),
                   hovertemplate="Max: %{y}%<extra></extra>"),
    ])
    alloc_bar.update_layout(
        title="Allocation vs Target Band", barmode="group",
        yaxis={"range": [0, max(bar_hi) * 1.35 if bar_hi else 100]},
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    _dark_fig(alloc_bar)

    # ── Corpus goals — combined FIRE + Education ──────────────────────────────
    yrs_fire       = _FIRE_YEAR - today.year
    yrs_edu        = _EDU_YEAR  - today.year
    yrs_post_fire  = _EDU_YEAR  - _FIRE_YEAR
    fire_tgt       = _fire_target(avg_exp)
    edu_tgt        = _edu_target_inr(yrs_edu)
    edu_reserve    = edu_tgt / (1 + _POST_FIRE_CAGR) ** yrs_post_fire
    combined_tgt   = fire_tgt + edu_reserve
    current_corpus = total_inv

    pct = min(current_corpus / combined_tgt * 100, 100) if combined_tgt else 0
    bar_col_g = "#00c49f" if pct >= 75 else ("#ffc107" if pct >= 40 else "#ff6b6b")

    goals = [dbc.Col(html.Div([
        html.Div("🎯 Combined Goal by 2036",
                 style={"fontSize": "13px", "fontWeight": "600",
                        "color": "#e0e0e0", "marginBottom": "10px"}),
        dbc.Progress(value=pct, label=f"{pct:.1f}%",
                     style={"height": "22px", "backgroundColor": "#2a2a3e"},
                     color=bar_col_g, className="mb-3"),
        dbc.Row([
            dbc.Col(html.Div([
                html.Div("Current Corpus", className="metric-label"),
                html.Div(fmt_inr(current_corpus),
                         style={"fontSize": "15px", "fontWeight": "700", "color": "#6c63ff"}),
            ])),
            dbc.Col(html.Div([
                html.Div("🔥 FIRE", className="metric-label"),
                html.Div(fmt_inr(fire_tgt),
                         style={"fontSize": "14px", "fontWeight": "600", "color": "#e0e0e0"}),
                html.Div("25× annual spend",
                         style={"fontSize": "10px", "color": "#555"}),
            ])),
            dbc.Col(html.Div([
                html.Div("🎓 Edu reserve (2036)", className="metric-label"),
                html.Div(fmt_inr(edu_reserve),
                         style={"fontSize": "14px", "fontWeight": "600", "color": "#e0e0e0"}),
                html.Div(f"PV of {fmt_inr(edu_tgt)} in {_EDU_YEAR}",
                         style={"fontSize": "10px", "color": "#555"}),
            ])),
            dbc.Col(html.Div([
                html.Div("Combined target", className="metric-label"),
                html.Div(fmt_inr(combined_tgt),
                         style={"fontSize": "15px", "fontWeight": "700", "color": "#ff6b6b"}),
                html.Div("gap: " + fmt_inr(max(combined_tgt - current_corpus, 0)),
                         style={"fontSize": "10px", "color": "#aaa"}),
            ])),
        ]),
    ], className="metric-card", style={"padding": "16px 20px"}), md=12)]

    # ── Projection table + shortfall ──────────────────────────────────────────
    def _extra_sip(gap, years, cagr):
        if gap <= 0 or years <= 0: return 0.0
        r = cagr / 12; n = years * 12
        return gap * r / ((1 + r) ** n - 1) if r else gap / n

    cagr_colours = ["#6c63ff", "#00c49f", "#ffc107"]
    cagr_list    = list(_CAGR.values())
    cagr_labels  = list(_CAGR.keys())

    proj_header = html.Thead(html.Tr([
        html.Th("Year"), html.Th("Note"),
        html.Th("Target", style={"textAlign": "right"}),
        *[html.Th(lbl, style={"textAlign": "right"}) for lbl in cagr_labels],
    ]))

    proj_rows = []
    for label, yrs, tgt, goal_tag, post_fire in [
        (f"2036 ({yrs_fire} yrs)",   yrs_fire,   combined_tgt, "🎯 FIRE+Edu", False),
        (f"2038 ({yrs_fire+2} yrs)", yrs_fire+2, None,         "",            False),
        (f"2041 ({yrs_edu} yrs)",    yrs_edu,    edu_tgt,      "🎓 Edu need", True),
    ]:
        cells = [
            html.Td(label,    style={"fontSize": "12px", "color": "#888"}),
            html.Td(goal_tag, style={"fontSize": "11px", "color": "#aaa"}),
            html.Td(fmt_inr(tgt) if tgt else "—",
                    style={"textAlign": "right", "fontSize": "12px",
                           "color": "#ff6b6b", "fontFamily": "monospace"}),
        ]
        for i, cagr in enumerate(cagr_list):
            if post_fire:
                proj = _fv_corpus(current_corpus, _TOTAL_MONTHLY_SIP, yrs_fire, cagr) \
                       * (1 + _POST_FIRE_CAGR) ** yrs_post_fire
            else:
                proj = _fv_corpus(current_corpus, _TOTAL_MONTHLY_SIP, yrs, cagr)
            gap = proj - tgt if tgt else 0
            on_track = tgt is None or gap >= 0
            gap_str = "" if tgt is None else (
                f"  ▲ {fmt_inr(gap)}" if on_track else f"  ▼ {fmt_inr(-gap)}"
            )
            cells.append(html.Td(
                [html.Div(fmt_inr(proj)),
                 html.Div(gap_str, style={"fontSize": "10px",
                          "color": "#00c49f" if on_track else "#ff6b6b"})],
                style={"textAlign": "right", "fontSize": "12px",
                       "fontFamily": "monospace", "color": cagr_colours[i]},
            ))
        proj_rows.append(html.Tr(cells))

    shortfall_cols = []
    for i, (cagr_lbl, cagr) in enumerate(zip(cagr_labels, cagr_list)):
        proj    = _fv_corpus(current_corpus, _TOTAL_MONTHLY_SIP, yrs_fire, cagr)
        gap     = proj - combined_tgt
        surplus = gap >= 0
        extra   = _extra_sip(-gap, yrs_fire, cagr)
        shortfall_cols.append(dbc.Col(html.Div([
            html.Div(cagr_lbl, className="metric-label"),
            html.Div(fmt_inr(proj),
                     style={"fontWeight": "700", "fontSize": "15px", "color": cagr_colours[i]}),
            html.Div("✓ Covers FIRE + Education" if surplus
                     else f"▼ {fmt_inr(-gap)} short of {fmt_inr(combined_tgt)}",
                     style={"fontWeight": "600", "fontSize": "12px",
                            "color": "#00c49f" if surplus else "#ff6b6b", "marginTop": "4px"}),
            html.Div(f"Surplus {fmt_inr(gap)}" if surplus
                     else f"Extra SIP: +{fmt_inr(extra)}/mo",
                     style={"fontSize": "11px",
                            "color": "#aaa" if surplus else "#ffc107", "marginTop": "2px"}),
        ], className="metric-card", style={"padding": "14px 16px"})))

    proj_table = html.Div([
        dbc.Table([proj_header, html.Tbody(proj_rows)],
                  bordered=False, size="sm", style={"color": "#e0e0e0"}),
        html.Div(
            f"2036 target = FIRE {fmt_inr(fire_tgt)} + edu reserve {fmt_inr(edu_reserve)} = "
            f"{fmt_inr(combined_tgt)}  |  2041: post-FIRE at 10%/yr, no new SIPs",
            style={"fontSize": "10px", "color": "#555", "marginTop": "4px"},
        ),
        html.Hr(style={"borderColor": "#2a2a3e", "margin": "12px 0"}),
        html.H6(f"Corpus at 2036 vs {fmt_inr(combined_tgt)} combined goal",
                style={"color": "#aaa", "fontSize": "12px", "marginBottom": "8px"}),
        dbc.Row(shortfall_cols, className="g-2"),
        html.Div(
            f"SIPs to 2036: ₹2.5 L equity + ₹30 K metals + ₹72 K EPF = "
            f"{fmt_inr(_TOTAL_MONTHLY_SIP)}/month. "
            f"Post-FIRE: grows at {int(_POST_FIRE_CAGR*100)}%/yr.",
            style={"fontSize": "11px", "color": "#555", "marginTop": "12px"},
        ),
    ])

    # ── SIP tracker ───────────────────────────────────────────────────────────
    if txn_df.empty:
        sip_metrics = [_card("Monthly SIPs", "No data", "#888")]
        sip_trend   = _dark_fig(go.Figure())
    else:
        cut6 = pd.Timestamp.now() - pd.DateOffset(months=6)
        inv  = txn_df[txn_df["date"] >= cut6].copy()
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
        sip_monthly = (sip_df.groupby("month")["amount"].sum().reset_index()
                       .sort_values("month") if not sip_df.empty else pd.DataFrame())
        detected_avg = float(sip_monthly["amount"].mean()) if not sip_monthly.empty else 0.0

        sip_metrics = [
            _card("Detected Avg SIP/mo", fmt_inr(detected_avg), "#6c63ff"),
            _card("Profile Target SIP",  fmt_inr(_TOTAL_MONTHLY_SIP), "#00c49f"),
            _card("Coverage",
                  f"{detected_avg/_TOTAL_MONTHLY_SIP*100:.0f}%" if _TOTAL_MONTHLY_SIP else "—",
                  "#ffc107",
                  sub=f"of profiled {fmt_inr(_TOTAL_MONTHLY_SIP)}/mo"),
        ]

        if sip_monthly.empty:
            sip_trend = _dark_fig(go.Figure())
        else:
            sip_monthly["label"] = sip_monthly["month"].apply(
                lambda v: pd.to_datetime(v + "-01").strftime("%b %Y") if v else v
            )
            sip_trend = go.Figure([go.Bar(
                x=sip_monthly["month"], y=sip_monthly["amount"],
                marker_color="#6c63ff",
                text=sip_monthly["label"], textposition="none",
                hovertemplate="%{text}<br>₹%{y:,.0f}<extra></extra>",
            )])
            sip_trend.add_hline(y=_TOTAL_MONTHLY_SIP, line_dash="dash",
                                line_color="#00c49f",
                                annotation_text=f"Target {fmt_inr(_TOTAL_MONTHLY_SIP)}",
                                annotation_position="top right")
            sip_trend.update_layout(title="Detected Investment Outflows (6 months)",
                                    showlegend=False)
            _dark_fig(sip_trend)

    return (holdings_div, metrics, pie, alloc_bar,
            goals, proj_table, sip_metrics, sip_trend)
