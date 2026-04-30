"""
Shared data loading layer — reads from Google Sheets with 5-minute cache.
Works both locally (OAuth token.json) and on Streamlit Cloud (service account secret).
"""

import os
import json
import gspread
import streamlit as st
import pandas as pd
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SHEET_ID = "1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]  # read+write (for tag editing)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # adhoccodes/


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _get_creds():
    # Cloud deployment: service account stored in Streamlit secrets
    try:
        if "gcp_service_account" in st.secrets:
            from google.oauth2.service_account import Credentials as SA
            return SA.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=SCOPES)
    except Exception:
        pass  # no secrets file — fall through to local OAuth

    # Local: reuse token.json from the sync scripts
    token_path = os.path.join(BASE_DIR, "token.json")
    if not os.path.exists(token_path):
        st.error(
            "**Google auth token not found.**\n\n"
            "Run this in your terminal to re-authorise:\n"
            "```\npython3 /Users/swaitales/adhoccodes/sync_all.py\n```\n"
            "Then refresh this page."
        )
        st.stop()
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _open_sheet():
    gc = gspread.authorize(_get_creds())
    return gc.open_by_key(SHEET_ID)


# ─── Data loaders (cached 5 min) ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="Loading transactions…")
def load_transactions() -> pd.DataFrame:
    ws   = _open_sheet().worksheet("Transactions")
    data = ws.get_all_records()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["date"]   = pd.to_datetime(df["date"],   errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"],  errors="coerce").fillna(0)
    df["month"]  = df["date"].dt.to_period("M").astype(str)
    # Primary tag = first item in comma-separated tags field
    df["primary_tag"] = (
        df["tags"].astype(str)
          .str.split(",").str[0]
          .str.strip()
          .replace("", "Untagged")
          .fillna("Untagged")
    )
    return df


@st.cache_data(ttl=300, show_spinner="Loading accounts…")
def load_accounts() -> pd.DataFrame:
    ws   = _open_sheet().worksheet("Accounts")
    data = ws.get_all_records()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    for col in ["computed_balance", "opening_balance", "buxfer_balance",
                "available_credit", "total_credit_line"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "days_stale" in df.columns:
        df["days_stale"] = pd.to_numeric(df["days_stale"], errors="coerce")
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_portfolio_config() -> dict:
    path = os.path.join(BASE_DIR, "portfolio_config.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


# ─── Derived helpers ──────────────────────────────────────────────────────────

def net_worth(accounts_df: pd.DataFrame, epf_balance: float = 0) -> float:
    """
    Sum of computed_balance for all non-credit-card accounts + EPF.
    CC accounts have negative balance (money owed) so they reduce net worth correctly.
    """
    if accounts_df.empty:
        return epf_balance
    total = accounts_df["computed_balance"].sum()
    return total + epf_balance


def monthly_expense_avg(txn_df: pd.DataFrame, months: int = 12) -> float:
    """Trailing N-month average monthly expense (expenses only, positive amounts)."""
    if txn_df.empty:
        return 0
    expenses = txn_df[txn_df["type"] == "expense"].copy()
    cutoff   = pd.Timestamp.now() - pd.DateOffset(months=months)
    expenses = expenses[expenses["date"] >= cutoff]
    if expenses.empty:
        return 0
    monthly  = expenses.groupby("month")["amount"].sum()
    return monthly.mean()


@st.cache_data(ttl=3600, show_spinner=False)
def load_tags() -> list:
    """Load all tag names from the Tags sheet."""
    try:
        ws   = _open_sheet().worksheet("Tags")
        data = ws.get_all_records()
        return sorted([r.get("name", "") for r in data if r.get("name")])
    except Exception:
        return []


def update_transaction_tags(txn_id: str, new_tags: str) -> bool:
    """
    Find the transaction row by id and update its tags cell (column E).
    Uses col_values() for reliable exact-match search (ws.find uses regex
    and breaks on IDs containing special chars like * / | ).
    Returns True on success.
    """
    try:
        ss      = _open_sheet()
        ws      = ss.worksheet("Transactions")
        all_ids = ws.col_values(1)          # read entire column A as plain list
        if txn_id not in all_ids:
            st.warning(f"ID not found in sheet: `{txn_id[:80]}`")
            return False
        row_num = all_ids.index(txn_id) + 1  # 1-based row number
        ws.update_cell(row_num, 5, new_tags)  # column E = tags
        load_transactions.clear()             # bust cache so next read is fresh
        return True
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False


def batch_update_tags(updates: dict) -> tuple:
    """
    Bulk-update tags for multiple transactions in a single API call.
    updates = {txn_id: new_tags_string, ...}
    Returns (saved_count, failed_ids).
    """
    if not updates:
        return 0, []
    try:
        ss      = _open_sheet()
        ws      = ss.worksheet("Transactions")
        all_ids = ws.col_values(1)
        cells   = []
        failed  = []
        for txn_id, new_tags in updates.items():
            if txn_id in all_ids:
                row_num = all_ids.index(txn_id) + 1
                cells.append(gspread.Cell(row_num, 5, new_tags))
            else:
                failed.append(txn_id)
        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")
        load_transactions.clear()
        return len(cells), failed
    except Exception as e:
        st.error(f"Batch save failed: {e}")
        return 0, list(updates.keys())


def fmt_inr(value: float, crore_threshold: float = 1e7) -> str:
    """Format a number as ₹X.XX Cr or ₹X,XX,XXX."""
    if abs(value) >= crore_threshold:
        return f"₹{value/1e7:.2f} Cr"
    elif abs(value) >= 1e5:
        return f"₹{value/1e5:.1f} L"
    else:
        return f"₹{value:,.0f}"
