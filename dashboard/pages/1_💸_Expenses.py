"""
Expenses — monthly breakdown by category, trends, top merchants.
Click bar segments to drill into transactions. Inline tag editor for untagged rows.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sheets_client import load_transactions, load_tags, batch_update_tags, fmt_inr

st.set_page_config(page_title="Expenses", page_icon="💸", layout="wide")
st.title("💸 Expense Analytics")

DATE_COL_CFG = st.column_config.DateColumn("Date", format="DD MMM YYYY")

def show_txn_table(df, key=None):
    """Display a transaction drill-down table with proper date sorting."""
    cols = [c for c in ["date","description","amount","account_name","tags"] if c in df.columns]
    disp = df[cols].copy()
    disp["amount"] = disp["amount"].apply(fmt_inr)
    disp = disp.rename(columns={
        "date":"Date","description":"Description",
        "amount":"Amount","account_name":"Account","tags":"Tags",
    })
    st.dataframe(
        disp,
        column_config={"Date": DATE_COL_CFG},
        use_container_width=True, hide_index=True,
        **({"key": key} if key else {}),
    )

# ─── Load ─────────────────────────────────────────────────────────────────────
txn_df   = load_transactions()
all_tags = load_tags()

if txn_df.empty:
    st.warning("No transaction data loaded.")
    st.stop()

expenses = txn_df[txn_df["type"] == "expense"].copy()

# ─── Sidebar filters ──────────────────────────────────────────────────────────
st.sidebar.header("Filters")

min_date = expenses["date"].min().date()
max_date = expenses["date"].max().date()
date_range = st.sidebar.date_input(
    "Date range",
    value=[(pd.Timestamp.now() - pd.DateOffset(months=12)).date(), max_date],
    min_value=min_date, max_value=max_date,
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range[0], date_range[1]
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_date, end_date = date_range[0], max_date
else:
    start_date, end_date = date_range, max_date

tag_options  = sorted(expenses["primary_tag"].dropna().unique().tolist())
sel_tags     = st.sidebar.multiselect("Categories", tag_options, default=tag_options)
acct_opts    = sorted(expenses["account_name"].astype(str).dropna().unique().tolist())
sel_accounts = st.sidebar.multiselect("Accounts", acct_opts, default=acct_opts)

mask = (
    (expenses["date"].dt.date >= start_date) &
    (expenses["date"].dt.date <= end_date) &
    (expenses["primary_tag"].isin(sel_tags)) &
    (expenses["account_name"].astype(str).isin(sel_accounts))
)
filtered = expenses[mask].copy()

if filtered.empty:
    st.info("No expenses match current filters.")
    st.stop()

# ─── Summary metrics ──────────────────────────────────────────────────────────
total_spend = filtered["amount"].sum()
avg_monthly = filtered.groupby("month")["amount"].sum().mean()
n_txns      = len(filtered)

c1, c2, c3 = st.columns(3)
c1.metric("Total Spend",  fmt_inr(total_spend))
c2.metric("Avg Monthly",  fmt_inr(avg_monthly))
c3.metric("Transactions", f"{n_txns:,}")

st.divider()

# ─── Monthly spend by category (stacked bar) — CLICK TO DRILL DOWN ───────────
st.subheader("Monthly Spend by Category")
st.caption("👆 Click a bar segment to see those transactions below")

mo_cat = (
    filtered.groupby(["month", "primary_tag"])["amount"]
    .sum().reset_index().sort_values("month")
)
mo_cat["amount_L"] = mo_cat["amount"] / 1e5

fig_bar = px.bar(
    mo_cat, x="month", y="amount_L", color="primary_tag",
    labels={"amount_L": "₹ Lakhs", "month": "Month", "primary_tag": "Category"},
    color_discrete_sequence=px.colors.qualitative.Set3,
    custom_data=["primary_tag", "amount"],
)
fig_bar.update_traces(
    hovertemplate="<b>%{customdata[0]}</b><br>%{x}<br>₹%{customdata[1]:,.0f}<extra></extra>"
)
fig_bar.update_layout(
    height=380, barmode="stack",
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font_color="white", legend_title="Category",
    yaxis=dict(gridcolor="#333", ticksuffix=" L"),
    xaxis=dict(gridcolor="#333"),
    margin=dict(t=20, b=20),
)

bar_event = st.plotly_chart(fig_bar, use_container_width=True,
                             on_select="rerun", key="bar_chart")

# Drill-down from bar click
if bar_event and bar_event.selection and bar_event.selection.points:
    pt        = bar_event.selection.points[0]
    sel_month = pt.get("x")
    sel_cat   = None
    cd        = pt.get("customdata")
    if cd:
        sel_cat = cd[0] if isinstance(cd, (list, tuple)) else cd
    if sel_month and sel_cat:
        bar_drill = filtered[
            (filtered["month"] == sel_month) &
            (filtered["primary_tag"] == sel_cat)
        ]
        st.info(f"📂 **{sel_cat}** · {sel_month} — {len(bar_drill)} transactions · {fmt_inr(bar_drill['amount'].sum())}")
        show_txn_table(bar_drill, key="bar_drill")

st.divider()

# ─── Category breakdown (pie + table) ────────────────────────────────────────
st.subheader("Category Breakdown")
st.caption("Click a category in the table to see its transactions ↓")

cat_totals = filtered.groupby("primary_tag")["amount"].sum().reset_index()
cat_totals = cat_totals.sort_values("amount", ascending=False)

col_pie, col_cat = st.columns([1, 1])

with col_pie:
    fig_pie = px.pie(
        cat_totals, names="primary_tag", values="amount",
        color_discrete_sequence=px.colors.qualitative.Set3,
        hole=0.4,
    )
    fig_pie.update_traces(
        textposition="inside", textinfo="percent+label",
        hovertemplate="<b>%{label}</b><br>₹%{value:,.0f} (%{percent})<extra></extra>",
    )
    fig_pie.update_layout(
        height=340, paper_bgcolor="rgba(0,0,0,0)", font_color="white",
        showlegend=False, margin=dict(t=10, b=10),
    )
    st.plotly_chart(fig_pie, use_container_width=True)

with col_cat:
    # Category summary table — richer than pie alone
    total_spend = cat_totals["amount"].sum()
    n_months    = max(filtered["month"].nunique(), 1)
    cat_summary = cat_totals.copy()
    cat_summary["pct"]     = (cat_summary["amount"] / total_spend * 100).round(1)
    cat_summary["avg_mo"]  = (cat_summary["amount"] / n_months).apply(fmt_inr)
    cat_summary["txns"]    = cat_summary["primary_tag"].apply(
        lambda t: (filtered["primary_tag"] == t).sum()
    )
    cat_summary["amount"]  = cat_summary["amount"].apply(fmt_inr)
    cat_summary["pct"]     = cat_summary["pct"].apply(lambda x: f"{x}%")
    cat_summary = cat_summary.rename(columns={
        "primary_tag": "Category", "amount": "Total",
        "pct": "Share", "avg_mo": "Avg/Month", "txns": "Txns",
    })
    st.dataframe(cat_summary, use_container_width=True, hide_index=True, height=340)

# Drill-down — full width, prominent selectbox
drill_cat = st.selectbox(
    "🔍 Drill into a category to see transactions",
    ["— pick a category —"] + cat_totals["primary_tag"].tolist(),
    key="pie_drill",
)
if drill_cat != "— pick a category —":
    pie_drill = filtered[filtered["primary_tag"] == drill_cat]
    st.info(f"**{drill_cat}** — {len(pie_drill)} transactions · {fmt_inr(pie_drill['amount'].sum())}")
    show_txn_table(pie_drill, key="pie_drill_table")

st.divider()

# ─── Year-over-year comparison ────────────────────────────────────────────────
st.subheader("Year-over-Year Comparison")
filtered["year"]      = filtered["date"].dt.year
filtered["month_num"] = filtered["date"].dt.month
yoy = filtered.groupby(["year","month_num"])["amount"].sum().reset_index()
yoy["month_label"] = pd.to_datetime(yoy["month_num"], format="%m").dt.strftime("%b")

fig_yoy = px.line(
    yoy, x="month_label", y="amount", color="year", markers=True,
    labels={"amount":"Amount (₹)","month_label":"Month","year":"Year"},
    color_discrete_sequence=["#00c49f","#ffc107","#ff6b6b","#8884d8"],
    category_orders={"month_label":["Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec","Jan","Feb","Mar"]},
)
fig_yoy.update_layout(
    height=320, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font_color="white", yaxis=dict(gridcolor="#333"), xaxis=dict(gridcolor="#333"),
    margin=dict(t=20, b=20),
)
st.plotly_chart(fig_yoy, use_container_width=True)

st.divider()

# ─── Tag Missing Transactions (inline data editor) ────────────────────────────
st.subheader("🏷️ Tag Missing Transactions")

untagged = filtered[
    filtered["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"])
].sort_values("date", ascending=False).reset_index(drop=True).copy()

total_untagged = len(filtered[filtered["tags"].astype(str).str.strip().isin(["","nan","Untagged"])])
st.caption(f"{total_untagged} untagged expense transactions in current filter — edit the **Tags** column inline, then click **Save Changes**")

if untagged.empty:
    st.success("✅ All transactions in this view are tagged!")
else:
    # Build editable table
    editor_df = untagged[["id","date","description","amount","account_name","tags"]].copy()
    editor_df["amount"] = editor_df["amount"].apply(fmt_inr)
    editor_df["tags"]   = editor_df["tags"].astype(str).replace({"nan":"","Untagged":""})
    editor_df = editor_df.rename(columns={
        "date":"Date","description":"Description",
        "amount":"Amount","account_name":"Account","tags":"Tags",
    })

    # Track IDs already saved this render-cycle to prevent the infinite-loop:
    #   pick tag → rerun → editor re-renders → "change" detected again → rerun …
    # Fix: compare against the RAW dataframe value (not session state snapshot),
    # and gate saves on a per-ID "already saved" set that survives the rerun.
    saved_ids_key = "expense_saved_ids"
    if saved_ids_key not in st.session_state:
        st.session_state[saved_ids_key] = set()

    edited = st.data_editor(
        editor_df.drop(columns=["id"]),
        column_config={
            "Tags": st.column_config.SelectboxColumn(
                "Tags",
                options=all_tags,
                required=False,
                width="medium",
            ),
            "Date":        st.column_config.DateColumn("Date", format="DD MMM YYYY", disabled=True),
            "Description": st.column_config.TextColumn("Description", disabled=True, width="large"),
            "Amount":      st.column_config.TextColumn("Amount",      disabled=True),
            "Account":     st.column_config.TextColumn("Account",     disabled=True),
        },
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key="tag_editor",
    )

    # Build update dict: only rows where edited tag ≠ original tag in the sheet
    # AND the ID hasn't been saved yet this session.
    BLANK = {"", "nan", "Untagged", "None", "none"}
    updates = {}
    for i, row in edited.iterrows():
        new_tag = str(row.get("Tags", "")).strip()
        if not new_tag or new_tag in BLANK:
            continue
        txn_id  = str(editor_df.iloc[i]["id"])
        orig    = str(untagged.iloc[i].get("tags", "")).strip()
        orig    = "" if orig in BLANK else orig
        if new_tag != orig and txn_id not in st.session_state[saved_ids_key]:
            updates[txn_id] = new_tag

    if updates:
        with st.spinner(f"Saving {len(updates)} tag(s)…"):
            saved, failed = batch_update_tags(updates)
        if saved:
            st.session_state[saved_ids_key].update(updates.keys())
            st.success(f"✅ Saved {saved} tag(s)!")
        if failed:
            st.warning(f"⚠️ {len(failed)} ID(s) not found: {[f[:30] for f in failed[:3]]}")
        if saved:
            load_transactions.clear()           # bust cache so fresh data loads
            st.session_state[saved_ids_key] = set()  # clear; fresh data has no untagged
            st.rerun()
    else:
        if st.button("💾 Save Changes", type="primary"):
            # Manual save — picks up anything the auto-detect missed
            manual = {}
            for i, row in edited.iterrows():
                new_tag = str(row.get("Tags", "")).strip()
                if new_tag and new_tag not in BLANK:
                    manual[str(editor_df.iloc[i]["id"])] = new_tag
            if manual:
                with st.spinner(f"Saving {len(manual)} tag(s)…"):
                    saved, failed = batch_update_tags(manual)
                if saved:
                    load_transactions.clear()
                    st.session_state[saved_ids_key] = set()
                    st.rerun()
            else:
                st.info("No tags selected yet — pick from the Tags column.")
