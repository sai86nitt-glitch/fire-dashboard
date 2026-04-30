"""
Transactions — searchable, filterable full transaction table with inline tag editor.
"""

import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sheets_client import load_transactions, load_tags, update_transaction_tags, batch_update_tags, fmt_inr

st.set_page_config(page_title="Transactions", page_icon="🔍", layout="wide")
st.title("🔍 Transactions")

# ─── Load ─────────────────────────────────────────────────────────────────────
txn_df   = load_transactions()
all_tags = load_tags()

if txn_df.empty:
    st.warning("No transaction data loaded.")
    st.stop()

# ─── Sidebar filters ──────────────────────────────────────────────────────────
st.sidebar.header("Filters")

min_date = txn_df["date"].min().date()
max_date = txn_df["date"].max().date()
date_range = st.sidebar.date_input(
    "Date range",
    value=[(pd.Timestamp.now() - pd.DateOffset(months=3)).date(), max_date],
    min_value=min_date, max_value=max_date,
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range[0], date_range[1]
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_date, end_date = date_range[0], max_date
else:
    start_date, end_date = date_range, max_date

all_types    = sorted(txn_df["type"].dropna().unique().tolist())
sel_types    = st.sidebar.multiselect("Type", all_types, default=all_types)
all_accounts = sorted(txn_df["account_name"].astype(str).dropna().unique().tolist())
sel_accounts = st.sidebar.multiselect("Account", all_accounts, default=all_accounts)
all_cats     = sorted(txn_df["primary_tag"].dropna().unique().tolist())
sel_tags     = st.sidebar.multiselect("Category", all_cats, default=all_cats)
min_amt      = float(txn_df["amount"].abs().min())
max_amt      = float(txn_df["amount"].abs().max())
amt_range    = st.sidebar.slider("Amount range (₹)", min_amt, max_amt, (min_amt, max_amt))

# Untagged only toggle
untagged_only = st.sidebar.toggle("🏷️ Untagged only", value=False)

# ─── Search + Apply filters ───────────────────────────────────────────────────
search = st.text_input("🔎 Search description", placeholder="e.g. Swiggy, Amazon, UPI...")

mask = (
    (txn_df["date"].dt.date >= start_date) &
    (txn_df["date"].dt.date <= end_date) &
    (txn_df["type"].isin(sel_types)) &
    (txn_df["account_name"].astype(str).isin(sel_accounts)) &
    (txn_df["primary_tag"].isin(sel_tags)) &
    (txn_df["amount"].abs() >= amt_range[0]) &
    (txn_df["amount"].abs() <= amt_range[1])
)
if search.strip():
    mask &= txn_df["description"].str.contains(search.strip(), case=False, na=False)
if untagged_only:
    mask &= txn_df["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"])

filtered = txn_df[mask].sort_values("date", ascending=False).reset_index(drop=True).copy()

# ─── Summary metrics ──────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Transactions",  f"{len(filtered):,}")
c2.metric("Total Expenses",fmt_inr(filtered[filtered["type"]=="expense"]["amount"].sum()))
c3.metric("Total Income",  fmt_inr(filtered[filtered["type"]=="income"]["amount"].abs().sum()))
c4.metric("Net",           fmt_inr(
    filtered[filtered["type"]=="expense"]["amount"].sum() -
    filtered[filtered["type"]=="income"]["amount"].abs().sum()
))

st.divider()

# ─── Two-panel layout: Table + Tag Editor ─────────────────────────────────────
col_table, col_tagger = st.columns([3, 1])

with col_table:
    want_cols  = ["date","description","amount","type","primary_tag",
                  "account_name","tags","transfer_from","transfer_to"]
    avail_cols = [c for c in want_cols if c in filtered.columns]
    display    = filtered[avail_cols].copy()
    display["amount"] = display["amount"].apply(fmt_inr)
    col_labels = {
        "date":"Date","description":"Description","amount":"Amount",
        "type":"Type","primary_tag":"Category","account_name":"Account",
        "tags":"Tags","transfer_from":"From","transfer_to":"To",
    }
    display.columns = [col_labels.get(c, c) for c in avail_cols]

    def colour_type(row):
        t = row.get("Type","")
        if t == "expense":  return ["color: #ff6b6b"] * len(row)
        if t == "income":   return ["color: #00c49f"] * len(row)
        if t == "transfer": return ["color: #8884d8"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display.style.apply(colour_type, axis=1),
        column_config={"Date": st.column_config.DateColumn("Date", format="DD MMM YYYY")},
        use_container_width=True, hide_index=True, height=580,
    )
    st.caption(f"Showing {len(filtered):,} of {len(txn_df):,} transactions")

with col_tagger:
    st.markdown("### 🏷️ Tag a transaction")
    st.caption("Pick a row number from the table (left), assign a tag, save.")

    row_num = st.number_input(
        "Row # (1-based)", min_value=1,
        max_value=max(len(filtered), 1), value=1, step=1,
    )
    idx = row_num - 1
    if 0 <= idx < len(filtered):
        row = filtered.iloc[idx]
        st.markdown(f"**{row['date'].strftime('%d %b %Y')}**")
        st.markdown(f"_{str(row['description'])[:60]}_")
        st.markdown(f"**{fmt_inr(row['amount'])}** · {row['account_name']}")
        current_tags = str(row.get("tags","")).strip()
        if current_tags and current_tags not in ("nan","Untagged",""):
            st.info(f"Current: {current_tags}")
        else:
            st.warning("No tags yet")

        new_tag = st.selectbox("Add tag", ["— pick —"] + all_tags, key="tag_pick")

        if new_tag != "— pick —":
            # Merge with existing tags
            existing = current_tags if current_tags not in ("","nan","Untagged") else ""
            merged   = new_tag if not existing else f"{existing}, {new_tag}"
            st.caption(f"Will save: **{merged}**")

            if st.button("💾 Save", type="primary", use_container_width=True):
                saved, failed = batch_update_tags({str(row["id"]): merged})
                if saved:
                    st.success("✅ Saved!")
                    st.rerun()
                else:
                    st.error(f"Save failed — ID not found: `{str(row['id'])[:60]}`")

        st.divider()
        st.caption("💡 Tip: Toggle **Untagged only** in the sidebar to focus on missing tags.")
