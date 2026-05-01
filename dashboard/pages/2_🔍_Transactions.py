"""
Transactions — dense table with inline row expansion + tagging.

Click any row → compact detail panel appears immediately below the table.
Tag the transaction right there; no separate section, no button hunting.
"""

import re
import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sheets_client import (
    load_transactions, load_tag_objects, load_rules,
    batch_update_tags, save_rule, fmt_inr,
)
from components.transactions import (
    build_tag_tree, build_display_df,
    resolve_tag_paths,
    render_pagination,
    fmt_raw, amount_css_from_glyph,
)

# ─── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="Transactions", page_icon="🔍", layout="wide")

st.markdown("""
<style>
div[data-testid="stDataFrame"] table td {
    padding: 4px 8px !important;
    font-size: 12px !important;
}
div[data-testid="stDataFrame"] table th {
    padding: 4px 8px !important;
    font-size: 11px !important;
    text-transform: uppercase;
    letter-spacing: .04em;
}
/* Inline expansion panel */
.txn-panel {
    background: #1a1a2e;
    border-left: 3px solid #444;
    border-radius: 6px;
    padding: 14px 18px;
    margin: 4px 0 10px 0;
    font-size: 12px;
}
.txn-panel .label {
    color: #888;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .05em;
}
.txn-panel .value { color: #e0e0e0; }
</style>
""", unsafe_allow_html=True)

st.title("🔍 Transactions")

PAGE_SIZE = 100

# ─── Load data ───────────────────────────────────────────────────────────────

txn_df      = load_transactions()
tag_objects = load_tag_objects()
tag_tree    = build_tag_tree(tag_objects)
all_displays = sorted({t["display"] for t in tag_objects})

if txn_df.empty:
    st.warning("No transaction data loaded.")
    st.stop()

# ─── Session state ────────────────────────────────────────────────────────────

if "txn_page" not in st.session_state:
    st.session_state["txn_page"] = 0

# ─── Sidebar filters ──────────────────────────────────────────────────────────

st.sidebar.header("Filters")

min_date = txn_df["date"].min().date()
max_date = txn_df["date"].max().date()

# Pick up drill-through from dashboard bar click (consumed once)
_drill_start = st.session_state.pop("txn_drill_start", None)
_drill_end   = st.session_state.pop("txn_drill_end",   None)
_default_range = (
    (_drill_start, _drill_end)
    if _drill_start and _drill_end
    else [(pd.Timestamp.now() - pd.DateOffset(months=3)).date(), max_date]
)

if _drill_start:
    st.info(f"📅 Filtered to **{_drill_start.strftime('%b %Y')}** — adjust below to change.")

date_range = st.sidebar.date_input(
    "Date range",
    value=_default_range,
    min_value=min_date, max_value=max_date,
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_date, end_date = date_range[0], max_date
else:
    start_date, end_date = date_range, max_date

VALID_TYPES = {"expense", "income", "transfer"}
txn_df["type"] = txn_df["type"].apply(
    lambda t: t if str(t).strip() in VALID_TYPES else "expense"
)

all_types    = ["expense", "income", "transfer"]
sel_types    = st.sidebar.multiselect("Type",     all_types,   default=all_types)
all_accounts = sorted(txn_df["account_name"].astype(str).dropna().unique().tolist())
sel_accounts = st.sidebar.multiselect("Account",  all_accounts, default=all_accounts)
all_cats     = sorted(txn_df["primary_tag"].dropna().unique().tolist())
sel_cats     = st.sidebar.multiselect("Category", all_cats,    default=all_cats)

min_amt  = float(txn_df["amount"].abs().min())
max_amt  = float(txn_df["amount"].abs().max())
amt_range = st.sidebar.slider("Amount range (₹)", min_amt, max_amt, (min_amt, max_amt))

untagged_only = st.sidebar.toggle("🏷️ Untagged only", value=False)

# ─── Search ───────────────────────────────────────────────────────────────────

search = st.text_input(
    "🔎 Search description",
    placeholder="Swiggy, Amazon, UPI…",
    key="txn_search",
)

# ─── Filter ───────────────────────────────────────────────────────────────────

mask = (
    (txn_df["date"].dt.date >= start_date) &
    (txn_df["date"].dt.date <= end_date) &
    (txn_df["type"].isin(sel_types)) &
    (txn_df["account_name"].astype(str).isin(sel_accounts)) &
    (txn_df["primary_tag"].isin(sel_cats)) &
    (txn_df["amount"].abs() >= amt_range[0]) &
    (txn_df["amount"].abs() <= amt_range[1])
)
if search.strip():
    mask &= txn_df["description"].str.contains(search.strip(), case=False, na=False)
if untagged_only:
    mask &= txn_df["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"])

filtered = (
    txn_df[mask]
    .sort_values("date", ascending=False)
    .reset_index(drop=True)
    .copy()
)

total_rows = len(filtered)

# ─── Metrics ─────────────────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
exp_total  = filtered[filtered["type"] == "expense"]["amount"].sum()
inc_total  = filtered[filtered["type"] == "income"]["amount"].abs().sum()
net        = inc_total - exp_total
n_untagged = filtered["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"]).sum()

c1.metric("Transactions",   f"{total_rows:,}")
c2.metric("Total Expenses", fmt_inr(exp_total))
c3.metric("Total Income",   fmt_inr(inc_total))
c4.metric("Net",            fmt_inr(net),
          delta=f"{n_untagged} untagged" if n_untagged else "All tagged ✓",
          delta_color="inverse" if n_untagged else "normal")

st.divider()

# ─── Pagination (top) ─────────────────────────────────────────────────────────

# Reset page if out of range after filter change
max_page = max(0, (total_rows - 1) // PAGE_SIZE)
if st.session_state["txn_page"] > max_page:
    st.session_state["txn_page"] = 0

render_pagination(total_rows, key="txn_page_top", state_key="txn_page", page_size=PAGE_SIZE)

# ─── Page slice ───────────────────────────────────────────────────────────────

page       = st.session_state["txn_page"]
page_start = page * PAGE_SIZE
page_end   = min(page_start + PAGE_SIZE, total_rows)
page_df    = filtered.iloc[page_start:page_end].copy().reset_index(drop=True)

# ─── Build display dataframe ──────────────────────────────────────────────────

disp_df    = build_display_df(page_df, tag_tree)
TABLE_COLS = {
    "date":          st.column_config.DateColumn("Date",        format="DD MMM YYYY", width="small"),
    "_amount_disp":  st.column_config.TextColumn("Amount",      width="small"),
    "description":   st.column_config.TextColumn("Description", width="large"),
    "_tags_disp":    st.column_config.TextColumn("Tags",        width="medium"),
    "_account_disp": st.column_config.TextColumn("Account",     width="medium"),
}
show_cols  = list(TABLE_COLS.keys())
disp_clean = disp_df[show_cols].copy()

styled_table = disp_clean.style.map(amount_css_from_glyph, subset=["_amount_disp"])

# ─── Transaction table ────────────────────────────────────────────────────────

tbl_event = st.dataframe(
    styled_table,
    column_config=TABLE_COLS,
    use_container_width=True,
    hide_index=True,
    height=min(42 * len(disp_clean) + 42, 600),
    on_select="rerun",
    selection_mode=["single-row"],   # single for clean inline UX
    key="txn_table",
)

selected_rows: list[int] = []
if tbl_event and tbl_event.selection:
    selected_rows = tbl_event.selection.rows or []

# ─── Inline expansion panel ───────────────────────────────────────────────────
# Appears immediately below the table when a row is selected.
# Contains full transaction details + tag editor in one place.

if selected_rows:
    sel_page_idx = selected_rows[0]                    # 0-based within page
    abs_idx      = page_start + sel_page_idx           # 0-based within filtered
    row          = page_df.iloc[sel_page_idx]

    # Current tag state
    current_tag_str = str(row.get("tags", "")).strip()
    if current_tag_str in ("", "nan", "Untagged", "None"):
        current_tag_str = ""
    current_paths = resolve_tag_paths(current_tag_str, tag_tree)
    valid_defaults = [p for p in current_paths if p in all_displays]

    amount_str = fmt_inr(abs(float(row.get("amount", 0))))
    date_str   = row["date"].strftime("%d %b %Y") if hasattr(row.get("date"), "strftime") else str(row.get("date", ""))
    txn_type   = str(row.get("type", "expense"))
    type_icon  = {"expense": "←", "income": "+", "transfer": "⇌"}.get(txn_type, "←")
    to_acct    = str(row.get("transfer_to", ""))

    with st.container(border=True):
        # ── Details row ──────────────────────────────────────────────────────
        d1, d2, d3, d4 = st.columns([3, 1, 1, 1])
        d1.markdown(f"**{row['description']}**  \n"
                    f"<span style='color:#888;font-size:11px'>{row['account_name']}"
                    + (f" → {to_acct}" if to_acct and to_acct not in ("", "nan") else "")
                    + "</span>",
                    unsafe_allow_html=True)
        d2.metric("Date",   date_str)
        d3.metric("Amount", f"{type_icon} {amount_str}")
        d4.metric("Type",   txn_type.capitalize())

        st.divider()

        # ── Tag editor ───────────────────────────────────────────────────────
        tc1, tc2 = st.columns([3, 1])

        with tc1:
            chosen_tags = st.multiselect(
                "🏷️ Tags",
                options=all_displays,
                default=valid_defaults,
                key=f"inline_ms_{abs_idx}",
                placeholder="Type to search or pick a category…",
            )

            # Create Rule checkbox
            desc_clean    = re.sub(r"[^a-zA-Z\s]", " ", str(row.get("description", ""))).strip()
            words         = [w for w in desc_clean.split() if len(w) >= 3]
            merchant_token = words[0] if words else ""
            create_rule   = False
            if merchant_token and chosen_tags:
                create_rule = st.checkbox(
                    f'🔁 Auto-tag future transactions containing **"{merchant_token}"**',
                    key=f"inline_rule_{abs_idx}",
                )

        with tc2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)  # align with multiselect
            if st.button("✓ Save Tag", key=f"save_tag_{abs_idx}",
                         type="primary", use_container_width=True,
                         disabled=not chosen_tags):
                tags_str = ", ".join(chosen_tags)
                with st.spinner("Saving…"):
                    saved, failed = batch_update_tags({str(row["id"]): tags_str})
                if saved:
                    if create_rule and merchant_token:
                        save_rule("description", merchant_token, tags_str)
                    st.success(f"✅ Tagged **{row['description'][:40]}** → {tags_str}")
                    load_transactions.clear()
                    st.rerun()
                else:
                    st.error(f"❌ Save failed — ID not found in sheet.")

            if st.button("✕ Close", key=f"close_{abs_idx}", use_container_width=True):
                st.rerun()

# ─── Pagination (bottom) ──────────────────────────────────────────────────────

render_pagination(total_rows, key="txn_page_bot", state_key="txn_page", page_size=PAGE_SIZE)
st.caption(f"Showing {page_start+1}–{page_end} of {total_rows:,} transactions")
