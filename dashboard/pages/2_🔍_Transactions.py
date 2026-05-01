"""
Transactions — AG Grid table with inline row expansion + tagging.

Click any row → detail + tag editor appears immediately below the table.
"""

import re
import streamlit as st
import pandas as pd
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, ColumnsAutoSizeMode
from sheets_client import (
    load_transactions, load_tag_objects,
    batch_update_tags, save_rule, fmt_inr,
)
from components.transactions import (
    build_tag_tree, build_display_df,
    resolve_tag_paths,
    render_pagination,
)

# ─── Page config ─────────────────────────────────────────────────────────────

st.set_page_config(page_title="Transactions", page_icon="🔍", layout="wide")

st.markdown("""
<style>
.stMetric label { font-size: 0.75rem !important; }
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
    "Date range", value=_default_range, min_value=min_date, max_value=max_date,
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
sel_types    = st.sidebar.multiselect("Type",     all_types,    default=all_types)
all_accounts = sorted(txn_df["account_name"].astype(str).dropna().unique().tolist())
sel_accounts = st.sidebar.multiselect("Account",  all_accounts, default=all_accounts)
all_cats     = sorted(txn_df["primary_tag"].dropna().unique().tolist())
sel_cats     = st.sidebar.multiselect("Category", all_cats,     default=all_cats)

min_amt  = float(txn_df["amount"].abs().min())
max_amt  = float(txn_df["amount"].abs().max())
amt_range = st.sidebar.slider("Amount range (₹)", min_amt, max_amt, (min_amt, max_amt))

untagged_only = st.sidebar.toggle("🏷️ Untagged only", value=False)

# ─── Search ───────────────────────────────────────────────────────────────────

search = st.text_input(
    "🔎 Search description", placeholder="Swiggy, Amazon, UPI…", key="txn_search",
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

disp_df = build_display_df(page_df, tag_tree)

grid_df = pd.DataFrame({
    "_idx":        range(len(page_df)),   # used to identify selected row reliably
    "Date":        disp_df["date"].apply(
                       lambda d: d.strftime("%d %b %Y") if hasattr(d, "strftime") else str(d)),
    "Amount":      disp_df["_amount_disp"].astype(str),
    "Description": page_df["description"].astype(str),
    "Tags":        disp_df["_tags_disp"].astype(str),
    "Account":     disp_df["_account_disp"].astype(str),
})

# ─── Grid config ─────────────────────────────────────────────────────────────

def _grid_options(df: pd.DataFrame) -> dict:
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_selection(selection_mode="single", use_checkbox=False)
    gb.configure_default_column(resizable=True, sortable=False, filter=False,
                                cellStyle={"fontSize": "12px"})
    gb.configure_column("_idx",        hide=True)
    gb.configure_column("Date",        width=110, pinned="left")
    gb.configure_column("Amount",      width=120,
                        cellStyle={"fontSize": "12px", "fontFamily": "monospace"})
    gb.configure_column("Description", flex=3, minWidth=180)
    gb.configure_column("Tags",        flex=1, minWidth=100,
                        cellStyle={"color": "#aaa", "fontSize": "11px"})
    gb.configure_column("Account",     flex=1, minWidth=100,
                        cellStyle={"color": "#aaa", "fontSize": "11px"})
    gb.configure_grid_options(rowHeight=36, headerHeight=32, domLayout="normal")
    return gb.build()

def _get_idx(resp) -> int | None:
    try:
        sel = resp.selected_rows
        if isinstance(sel, pd.DataFrame) and not sel.empty:
            return int(sel.iloc[0]["_idx"])
        if isinstance(sel, list) and sel:
            return int(sel[0]["_idx"])
    except Exception:
        pass
    return None

# ─── Session state ────────────────────────────────────────────────────────────

if "txn_sel_idx" not in st.session_state:
    st.session_state["txn_sel_idx"] = None

_page_sig = (page, total_rows)
if st.session_state.get("_txn_page_sig") != _page_sig:
    st.session_state["txn_sel_idx"] = None
    st.session_state["_txn_page_sig"] = _page_sig

sel = st.session_state["txn_sel_idx"]

# ─── Single AG Grid ───────────────────────────────────────────────────────────

resp = AgGrid(
    grid_df,
    gridOptions=_grid_options(grid_df),
    update_mode=GridUpdateMode.SELECTION_CHANGED,
    columns_auto_size_mode=ColumnsAutoSizeMode.NO_AUTOSIZE,
    height=min(36 * len(grid_df) + 36, 520),
    theme="streamlit",
    use_container_width=True,
    key=f"ag_{page}_{total_rows}",
)

new = _get_idx(resp)
if new is not None and new != sel:
    st.session_state["txn_sel_idx"] = new
    st.rerun()

# ─── Expansion panel + auto-scroll ───────────────────────────────────────────

if sel is not None and 0 <= sel < len(page_df):
    # Scroll the panel into view automatically
    import streamlit.components.v1 as components
    components.html(
        "<script>window.parent.document.querySelector('section.main')"
        ".scrollTo({top: 99999, behavior: 'smooth'});</script>",
        height=0,
    )

    abs_idx = page_start + sel
    row     = page_df.iloc[sel]

    current_tag_str = str(row.get("tags", "")).strip()
    if current_tag_str in ("", "nan", "Untagged", "None"):
        current_tag_str = ""
    current_paths  = resolve_tag_paths(current_tag_str, tag_tree)
    valid_defaults = [p for p in current_paths if p in all_displays]

    amount_str = fmt_inr(abs(float(row.get("amount", 0))))
    date_str   = row["date"].strftime("%d %b %Y") if hasattr(row.get("date"), "strftime") else str(row.get("date", ""))
    txn_type   = str(row.get("type", "expense"))
    type_icon  = {"expense": "←", "income": "+", "transfer": "⇌"}.get(txn_type, "←")
    to_acct    = str(row.get("transfer_to", ""))
    acct_line  = row["account_name"] + (f" → {to_acct}" if to_acct and to_acct not in ("", "nan") else "")

    with st.container(border=True):
        cl, cr = st.columns([8, 1])
        cl.markdown(
            f"**{row['description']}**  \n"
            f"<span style='color:#888;font-size:11px'>{acct_line} &nbsp;·&nbsp; "
            f"{date_str} &nbsp;·&nbsp; {type_icon} {amount_str} &nbsp;·&nbsp; "
            f"{txn_type.capitalize()}</span>",
            unsafe_allow_html=True,
        )
        if cr.button("✕", key=f"close_{abs_idx}"):
            st.session_state["txn_sel_idx"] = None
            st.rerun()

        st.divider()

        tc1, tc2 = st.columns([3, 1])
        with tc1:
            chosen_tags = st.multiselect(
                "🏷️ Tags", options=all_displays, default=valid_defaults,
                key=f"ms_{abs_idx}", placeholder="Type to search or pick a category…",
            )
            desc_clean     = re.sub(r"[^a-zA-Z\s]", " ", str(row.get("description", ""))).strip()
            words          = [w for w in desc_clean.split() if len(w) >= 3]
            merchant_token = words[0] if words else ""
            create_rule    = False
            if merchant_token and chosen_tags:
                create_rule = st.checkbox(
                    f'🔁 Auto-tag future "{merchant_token}" transactions',
                    key=f"rule_{abs_idx}",
                )
        with tc2:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("✓ Save", key=f"save_{abs_idx}",
                         type="primary", use_container_width=True,
                         disabled=not chosen_tags):
                tags_str = ", ".join(chosen_tags)
                with st.spinner("Saving…"):
                    saved, _ = batch_update_tags({str(row["id"]): tags_str})
                if saved:
                    if create_rule and merchant_token:
                        save_rule("description", merchant_token, tags_str)
                    st.success(f"✅ {row['description'][:35]} → {tags_str}")
                    load_transactions.clear()
                    st.session_state["txn_sel_idx"] = None
                    st.rerun()
                else:
                    st.error("❌ Save failed — ID not found in sheet.")

# ─── Pagination (bottom) ──────────────────────────────────────────────────────

render_pagination(total_rows, key="txn_page_bot", state_key="txn_page", page_size=PAGE_SIZE)
st.caption(f"Showing {page_start+1}–{page_end} of {total_rows:,} transactions")
