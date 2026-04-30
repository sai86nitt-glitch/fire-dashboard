"""
FIRE Dashboard — Overview
Home page: Net Worth, FIRE progress, monthly spend snapshot, account health.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from sheets_client import (
    load_transactions, load_accounts, load_portfolio_config,
    net_worth, monthly_expense_avg, fmt_inr,
)

st.set_page_config(
    page_title="FIRE Dashboard",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .metric-card {
    background: #1e1e2e; border-radius: 12px;
    padding: 20px 24px; margin-bottom: 8px;
  }
  .stMetric label { font-size: 0.8rem !important; color: #aaa !important; }
  div[data-testid="metric-container"] { background: #1e1e2e; border-radius: 10px; padding: 16px; }
</style>
""", unsafe_allow_html=True)

st.title("🔥 FIRE Dashboard")
st.caption(f"Last refreshed: {date.today().strftime('%d %b %Y')}  •  Data from Google Sheets")

# ─── Load data ────────────────────────────────────────────────────────────────
with st.spinner("Loading…"):
    txn_df  = load_transactions()
    acc_df  = load_accounts()
    config  = load_portfolio_config()

epf_balance = config.get("epf", {}).get("total_balance", 0)

# ─── Key Metrics ──────────────────────────────────────────────────────────────
FIRE_MULTIPLIER   = 300   # 25× annual spend = 300× monthly
CAGR_SCENARIOS    = [0.12, 0.15, 0.18]
FIRE_TARGET_YEARS = [2036, 2038, 2041]

total_nw      = net_worth(acc_df, epf_balance)
monthly_spend = monthly_expense_avg(txn_df, months=12)
fire_target   = monthly_spend * FIRE_MULTIPLIER
fire_pct      = min((total_nw / fire_target * 100) if fire_target else 0, 100)

# Current month spend
today        = pd.Timestamp.now()
current_mo   = today.to_period("M").strftime("%Y-%m")
prev_mo      = (today - pd.DateOffset(months=1)).to_period("M").strftime("%Y-%m")

if not txn_df.empty:
    expenses      = txn_df[txn_df["type"] == "expense"]
    curr_mo_spend = expenses[expenses["month"] == current_mo]["amount"].sum()
    prev_mo_spend = expenses[expenses["month"] == prev_mo]["amount"].sum()
    mo_delta      = curr_mo_spend - prev_mo_spend
else:
    curr_mo_spend = prev_mo_spend = mo_delta = 0

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("💰 Total Net Worth", fmt_inr(total_nw))
with col2:
    st.metric("🎯 FIRE Target (25×)", fmt_inr(fire_target),
              help="25× annual spend = target retirement corpus")
with col3:
    st.metric(f"📅 This Month's Spend", fmt_inr(curr_mo_spend),
              delta=f"{fmt_inr(abs(mo_delta))} {'more' if mo_delta > 0 else 'less'} than last month",
              delta_color="inverse")
with col4:
    st.metric("📊 12-mo Avg Monthly Spend", fmt_inr(monthly_spend))

st.divider()

# ─── FIRE Progress Bar ────────────────────────────────────────────────────────
st.subheader("🎯 FIRE Progress")

col_prog, col_proj = st.columns([1, 1])

with col_prog:
    fig = go.Figure(go.Indicator(
        mode  = "gauge+number+delta",
        value = fire_pct,
        delta = {"reference": 100, "valueformat": ".1f", "suffix": "%"},
        number = {"suffix": "%", "valueformat": ".1f"},
        title  = {"text": f"Corpus vs Target<br><span style='font-size:0.8em;color:#aaa'>"
                          f"Current: {fmt_inr(total_nw)} / Target: {fmt_inr(fire_target)}</span>"},
        gauge  = {
            "axis":  {"range": [0, 100]},
            "bar":   {"color": "#00c49f" if fire_pct >= 80 else "#ffc107" if fire_pct >= 50 else "#ff6b6b"},
            "steps": [
                {"range": [0,  50], "color": "#2a2a3e"},
                {"range": [50, 80], "color": "#2a3a2a"},
                {"range": [80, 100], "color": "#1a3a1a"},
            ],
            "threshold": {"line": {"color": "white", "width": 3}, "value": 100},
        },
    ))
    fig.update_layout(height=280, margin=dict(t=60, b=20, l=20, r=20),
                      paper_bgcolor="rgba(0,0,0,0)", font_color="white")
    st.plotly_chart(fig, use_container_width=True)

with col_proj:
    st.markdown("**Corpus projections** *(current: " + fmt_inr(total_nw) + ")*")
    current_year = date.today().year
    rows = []
    for yr in FIRE_TARGET_YEARS:
        n = yr - current_year
        row = {"Year": str(yr), "Years left": n}
        for r in CAGR_SCENARIOS:
            fv = total_nw * (1 + r) ** n
            row[f"{int(r*100)}% CAGR"] = fmt_inr(fv)
        rows.append(row)
    proj_df = pd.DataFrame(rows).set_index("Year")
    st.dataframe(proj_df, use_container_width=True)
    st.caption(f"🎯 Target corpus: **{fmt_inr(fire_target)}** (25× monthly spend of {fmt_inr(monthly_spend)})")

st.divider()

# ─── Monthly Spend Trend (last 12 months) ─────────────────────────────────────
st.subheader("📈 Monthly Spend — Last 12 Months")

if not txn_df.empty:
    cutoff   = pd.Timestamp.now() - pd.DateOffset(months=12)
    exp12    = txn_df[(txn_df["type"] == "expense") & (txn_df["date"] >= cutoff)]
    mo_spend = exp12.groupby("month")["amount"].sum().reset_index().sort_values("month")

    mo_spend["amount_L"] = mo_spend["amount"] / 1e5   # convert to Lakhs

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=mo_spend["month"], y=mo_spend["amount_L"],
        marker_color=[
            "#ff6b6b" if v > monthly_spend * 1.1 else
            "#ffc107" if v > monthly_spend * 0.9 else "#00c49f"
            for v in mo_spend["amount"]
        ],
        name="Monthly Spend",
        hovertemplate="%{x}<br>₹%{customdata}",
        customdata=mo_spend["amount"].apply(fmt_inr),
    ))
    fig2.add_hline(y=monthly_spend / 1e5, line_dash="dash", line_color="white",
                   annotation_text=f"12-mo avg: {fmt_inr(monthly_spend)}",
                   annotation_position="top right")
    fig2.update_layout(
        height=300, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="white", margin=dict(t=20, b=20),
        yaxis=dict(gridcolor="#333", title="₹ Lakhs", ticksuffix=" L"),
        xaxis=dict(gridcolor="#333"),
    )
    st.plotly_chart(fig2, use_container_width=True)
else:
    st.info("No transaction data yet.")

st.divider()

# ─── Account Health ───────────────────────────────────────────────────────────
st.subheader("🏦 Account Health")

if not acc_df.empty:
    display_cols = ["name", "bank", "computed_balance", "days_stale", "last_txn_date"]
    disp = acc_df[[c for c in display_cols if c in acc_df.columns]].copy()
    disp = disp.rename(columns={
        "name": "Account", "bank": "Bank",
        "computed_balance": "Balance", "days_stale": "Days Stale",
        "last_txn_date": "Last Transaction",
    })
    disp["Balance"] = disp["Balance"].apply(fmt_inr)

    def highlight_stale(row):
        try:
            d = float(row.get("Days Stale", 0) or 0)
            if d > 60:  return ["background-color: #3a1a1a"] * len(row)
            if d > 30:  return ["background-color: #3a2a1a"] * len(row)
        except:
            pass
        return [""] * len(row)

    st.dataframe(
        disp.style.apply(highlight_stale, axis=1),
        use_container_width=True, hide_index=True,
    )
    st.caption("🔴 Red = >60 days stale  🟡 Amber = >30 days stale")
else:
    st.info("No account data yet.")
