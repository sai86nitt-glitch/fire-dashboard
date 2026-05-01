"""
Transactions — Buxfer-style dense table with inline tagging, bulk actions, pagination.

Layout
──────
  Sidebar   : date, type, account, amount-range, untagged-only filters
  Main area :
    ① Summary metrics (4 cols)
    ② Forecast group row (collapsible stub)
    ③ Bulk action bar  |  Pagination (top)
    ④ Dense transaction table  (st.dataframe, multi-row select)
    ⑤ Pagination (bottom)
    ⑥ Focused-row expanded details + per-row toolbar
    ⑦ Inline tag editor  (opens when 't' pressed or ✏️ Tags clicked)
    ⑧ Keyboard navigation JS  (j/k move the focused-row counter)
"""

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
    tag_chips_text, resolve_tag_paths,
    render_bulk_bar, render_pagination,
    render_tag_editor, render_expanded_row,
    render_forecast_group, inject_keyboard_nav,
    fmt_raw, amount_colour, amount_css_from_glyph,
)

# ─── Page config + CSS ───────────────────────────────────────────────────────

st.set_page_config(page_title="Transactions", page_icon="🔍", layout="wide")

st.markdown("""
<style>
/* Dense table row height */
div[data-testid="stDataFrame"] table td { padding: 4px 8px !important; font-size: 12px !important; }
div[data-testid="stDataFrame"] table th { padding: 4px 8px !important; font-size: 11px !important;
                                          text-transform: uppercase; letter-spacing: .04em; }
/* Tag chip pill style (used in markdown areas) */
.tag-pill {
  display: inline-block; padding: 1px 7px; border-radius: 3px;
  background: #2a2a3e; color: #aaa; font-size: 11px;
  margin: 0 2px; border: 1px solid #3a3a5e;
}
/* Muted forecast rows */
.forecast-row { opacity: 0.55; }
</style>
""", unsafe_allow_html=True)

st.title("🔍 Transactions")

PAGE_SIZE = 100

# ─── Load data ───────────────────────────────────────────────────────────────

txn_df      = load_transactions()
tag_objects = load_tag_objects()
rules       = load_rules()
tag_tree    = build_tag_tree(tag_objects)
all_displays = sorted({t["display"] for t in tag_objects})  # for editor dropdown

if txn_df.empty:
    st.warning("No transaction data loaded.")
    st.stop()

# ─── Session state ────────────────────────────────────────────────────────────

for key, default in [
    ("txn_page",         0),
    ("expanded_row_idx", None),   # index into filtered df
    ("editing_row_idx",  None),   # index into filtered df
]:
    if key not in st.session_state:
        st.session_state[key] = default

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
    start_date, end_date = date_range
elif isinstance(date_range, (list, tuple)) and len(date_range) == 1:
    start_date, end_date = date_range[0], max_date
else:
    start_date, end_date = date_range, max_date

# Normalise type column — some rows from the Gmail script may have month strings
# or other garbage in the type column; clamp to known values.
VALID_TYPES = {"expense", "income", "transfer"}
txn_df["type"] = txn_df["type"].apply(
    lambda t: t if str(t).strip() in VALID_TYPES else "expense"
)

all_types    = ["expense", "income", "transfer"]   # fixed order, not from data
sel_types    = st.sidebar.multiselect("Type", all_types, default=all_types)
all_accounts = sorted(txn_df["account_name"].astype(str).dropna().unique().tolist())
sel_accounts = st.sidebar.multiselect("Account", all_accounts, default=all_accounts)
all_cats     = sorted(txn_df["primary_tag"].dropna().unique().tolist())
sel_cats     = st.sidebar.multiselect("Category", all_cats,   default=all_cats)

min_amt = float(txn_df["amount"].abs().min())
max_amt = float(txn_df["amount"].abs().max())
amt_range = st.sidebar.slider("Amount range (₹)", min_amt, max_amt, (min_amt, max_amt))

untagged_only = st.sidebar.toggle("🏷️ Untagged only", value=False)

# ─── Search ───────────────────────────────────────────────────────────────────

search = st.text_input(
    "🔎 Search description",
    placeholder="Swiggy, Amazon, UPI…  (press / to focus)",
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

# Reset page if filter changed and page is now out of range
max_page = max(0, (total_rows - 1) // PAGE_SIZE)
if st.session_state["txn_page"] > max_page:
    st.session_state["txn_page"] = 0

# ─── Summary metrics ──────────────────────────────────────────────────────────

c1, c2, c3, c4 = st.columns(4)
exp_total = filtered[filtered["type"] == "expense"]["amount"].sum()
inc_total = filtered[filtered["type"] == "income"]["amount"].abs().sum()
net       = inc_total - exp_total
n_untagged = filtered["tags"].astype(str).str.strip().isin(["", "nan", "Untagged"]).sum()

c1.metric("Transactions",    f"{total_rows:,}")
c2.metric("Total Expenses",  fmt_inr(exp_total))
c3.metric("Total Income",    fmt_inr(inc_total))
c4.metric("Net",             fmt_inr(net),
          delta=f"{n_untagged} untagged" if n_untagged else "All tagged ✓",
          delta_color="inverse" if n_untagged else "normal")

st.divider()

# ─── Forecast group ───────────────────────────────────────────────────────────

render_forecast_group()

# ─── Slice current page ───────────────────────────────────────────────────────

page       = st.session_state["txn_page"]
page_start = page * PAGE_SIZE
page_end   = min(page_start + PAGE_SIZE, total_rows)
page_df    = filtered.iloc[page_start:page_end].copy().reset_index(drop=True)

# ─── Build display dataframe ──────────────────────────────────────────────────

disp_df = build_display_df(page_df, tag_tree)

# Columns shown in the table
TABLE_COLS = {
    "date":         st.column_config.DateColumn("Date",        format="DD MMM YYYY", width="small"),
    "_amount_disp": st.column_config.TextColumn("Amount",      width="small"),
    "description":  st.column_config.TextColumn("Description", width="large"),
    "_tags_disp":   st.column_config.TextColumn("Tags",        width="medium"),
    "_account_disp":st.column_config.TextColumn("Account",     width="medium"),
}

show_cols  = list(TABLE_COLS.keys())
disp_clean = disp_df[show_cols].copy()

# Colour the Amount column via .map() — infers colour from the glyph prefix
# so we don't need to cross-reference page_df inside the styler.
styled_table = disp_clean.style.map(
    amount_css_from_glyph, subset=["_amount_disp"]
)

# ─── Bulk bar (top) + Pagination (top) ───────────────────────────────────────

bar_col, pag_col = st.columns([2, 3])

with bar_col:
    # We'll know selection count after the table renders, so prime with last known
    sel_count = len(st.session_state.get("txn_sel_rows", []))
    bulk_action = render_bulk_bar(sel_count, key="bulk_top")

with pag_col:
    _ = render_pagination(total_rows, key="txn_page_top", state_key="txn_page", page_size=PAGE_SIZE)

# ─── Main table ───────────────────────────────────────────────────────────────

tbl_event = st.dataframe(
    styled_table,
    column_config=TABLE_COLS,
    use_container_width=True,
    hide_index=True,
    height=min(42 * len(disp_clean) + 42, 600),   # ~40px rows + header
    on_select="rerun",
    selection_mode="single-row",
    key="txn_table",
)

selected_page_rows: list[int] = []
if tbl_event and tbl_event.selection:
    selected_page_rows = tbl_event.selection.rows or []

# Persist selection count for bulk bar update on next rerun
st.session_state["txn_sel_rows"] = selected_page_rows

# ─── Pagination (bottom) ──────────────────────────────────────────────────────

render_pagination(total_rows, key="txn_page_bot", state_key="txn_page", page_size=PAGE_SIZE)

st.caption(f"Showing {page_start+1}–{page_end} of {total_rows:,} transactions")

# ─── Focused-row navigator (keyboard nav target) ──────────────────────────────

st.divider()
st.markdown("#### Row inspector")

focus_col, open_col, tag_col = st.columns([2, 1, 1])

with focus_col:
    focused_row = st.number_input(
        "Row # (1-based, press j/k to navigate)",
        min_value=1,
        max_value=max(total_rows, 1),
        value=max(1, int(st.session_state.get("expanded_row_idx") or 1)),
        step=1,
        key="kb_row",
    )

with open_col:
    expand_clicked = st.button(
        "▾ Expand  (e)",
        key="btn_expand",
        use_container_width=True,
    )

with tag_col:
    tag_clicked = st.button(
        "🏷️ Tag  (t)",
        key="btn_tag",
        type="primary",
        use_container_width=True,
    )

row_idx = int(focused_row) - 1   # 0-based into filtered (not page_df)

# Sync expansion state
if expand_clicked:
    current = st.session_state["expanded_row_idx"]
    st.session_state["expanded_row_idx"] = None if current == row_idx else row_idx
    st.session_state["editing_row_idx"]  = None

if tag_clicked:
    current = st.session_state["editing_row_idx"]
    st.session_state["editing_row_idx"]  = None if current == row_idx else row_idx
    st.session_state["expanded_row_idx"] = None

# Expand immediately when a row is clicked in the table
if selected_page_rows:
    clicked_page_idx = selected_page_rows[0]            # single-row mode: always one
    actual_idx       = page_start + clicked_page_idx    # into filtered
    if st.session_state["expanded_row_idx"] != actual_idx:
        st.session_state["expanded_row_idx"] = actual_idx
        st.session_state["editing_row_idx"]  = None
        st.rerun()

# ─── Expanded row details ────────────────────────────────────────────────────

exp_idx = st.session_state.get("expanded_row_idx")
if exp_idx is not None and 0 <= exp_idx < total_rows:
    row_data = filtered.iloc[exp_idx]
    action   = render_expanded_row(row_data, key=f"exp_{exp_idx}")
    if action == "delete":
        st.warning("⚠️ Delete not yet wired — remove from sheet manually for now.")
        # TODO: implement delete_transaction(row_data["id"]) in sheets_client
    elif action in ("edit", "rule", "memo"):
        st.session_state["editing_row_idx"]  = exp_idx
        st.session_state["expanded_row_idx"] = None
        st.rerun()

# ─── Inline tag editor ────────────────────────────────────────────────────────

ed_idx = st.session_state.get("editing_row_idx")
if ed_idx is not None and 0 <= ed_idx < total_rows:
    row_data = filtered.iloc[ed_idx]

    with st.container(border=True):
        result = render_tag_editor(
            row_data,
            tag_objects,
            tag_tree,
            key=f"ted_{ed_idx}",
        )

    if result is not None:
        if result.get("cancel"):
            st.session_state["editing_row_idx"] = None
            st.rerun()
        else:
            tags_str = result.get("tags", "")
            rule     = result.get("rule")

            if tags_str:
                with st.spinner("Saving tag…"):
                    saved, failed = batch_update_tags({str(row_data["id"]): tags_str})
                if saved:
                    st.success(f"✅ Tag saved: **{tags_str}**")
                    # Save rule if requested
                    if rule:
                        ok = save_rule(
                            rule["matcher_field"],
                            rule["matcher_contains"],
                            rule["tag_names"],
                        )
                        if ok:
                            st.success(
                                f"🔁 Rule created — future transactions containing "
                                f"**\"{rule['matcher_contains']}\"** will be tagged "
                                f"**{rule['tag_names']}**"
                            )
                    st.session_state["editing_row_idx"] = None
                    st.rerun()
                elif failed:
                    st.error(f"❌ Save failed — ID not found: `{str(row_data['id'])[:60]}`")
            else:
                st.info("No tag chosen — pick or type one above.")

# ─── Bulk action handling ────────────────────────────────────────────────────

if bulk_action and selected_page_rows:
    selected_ids = [str(page_df.iloc[i]["id"]) for i in selected_page_rows if i < len(page_df)]
    if bulk_action == "copy":
        st.info(f"Copied {len(selected_ids)} transaction IDs to info panel (print not yet wired).")
    elif bulk_action == "delete":
        st.warning(f"⚠️ Bulk delete not yet wired ({len(selected_ids)} rows selected). "
                   "Remove from sheet manually for now.")
        # TODO: implement bulk_delete_transactions(ids) in sheets_client
    elif bulk_action == "edit":
        # Open tag editor for the first selected row
        st.session_state["editing_row_idx"] = page_start + selected_page_rows[0]
        st.rerun()

# ─── Keyboard navigation JS ──────────────────────────────────────────────────

inject_keyboard_nav(total_rows, number_input_key="kb_row")
