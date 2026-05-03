"""
Data layer — reads from Google Sheets.
Credentials: GCP_SERVICE_ACCOUNT_JSON env var (production)
             or dashboard/.streamlit/secrets.toml (local dev)
"""

import os, json, time, re
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT    = os.path.dirname(_APP_DIR)

# ── Simple TTL cache ──────────────────────────────────────────────────────────

_cache      = {}
_cache_time = {}

def _cached(key, loader, ttl=300):
    now = time.time()
    if key not in _cache or now - _cache_time.get(key, 0) > ttl:
        _cache[key] = loader()
        _cache_time[key] = now
    return _cache[key]

def bust(key=None):
    if key:
        _cache.pop(key, None)
        _cache_time.pop(key, None)
    else:
        _cache.clear()
        _cache_time.clear()

# ── Auth ─────────────────────────────────────────────────────────────────────

def _get_creds():
    sa_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json:
        return Credentials.from_service_account_info(
            json.loads(sa_json), scopes=SCOPES
        )
    # Local dev: read from Streamlit secrets.toml
    secrets_path = os.path.join(_ROOT, "dashboard", ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        import toml
        cfg = toml.load(secrets_path)
        return Credentials.from_service_account_info(
            dict(cfg["gcp_service_account"]), scopes=SCOPES
        )
    raise RuntimeError(
        "No GCP credentials. Set GCP_SERVICE_ACCOUNT_JSON env var or place "
        "secrets.toml at dashboard/.streamlit/secrets.toml"
    )

def _sheet():
    return gspread.authorize(_get_creds()).open_by_key(SHEET_ID)

# ── Loaders ───────────────────────────────────────────────────────────────────

VALID_TYPES = {"expense", "income", "transfer"}

def load_transactions() -> pd.DataFrame:
    def _load():
        ws   = _sheet().worksheet("Transactions")
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["date"]   = pd.to_datetime(df["date"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        df["month"]  = df["date"].dt.to_period("M").astype(str)
        df["type"]   = df["type"].apply(
            lambda t: t if str(t).strip() in VALID_TYPES else "expense"
        )
        df["primary_tag"] = (
            df["tags"].astype(str).str.split(",").str[0]
              .str.strip().replace("", "Untagged").fillna("Untagged")
        )
        return df
    return _cached("transactions", _load)

def load_accounts() -> pd.DataFrame:
    def _load():
        ws   = _sheet().worksheet("Accounts")
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
    return _cached("accounts", _load)

def load_tags() -> list[dict]:
    def _load():
        ws   = _sheet().worksheet("Tags")
        data = ws.get_all_records()
        result = []
        for r in data:
            raw = str(r.get("name", "")).strip()
            par = str(r.get("parent", "")).strip()
            if not raw or raw in ("nan", ""):
                continue
            if par in ("nan", ""):
                par = ""
            if "/" in raw and not par:
                parts = [p.strip() for p in raw.split("/", 1)]
                par, name = parts[0], parts[1]
            else:
                name = raw
            display = f"{par} / {name}" if par else name
            result.append({"name": name, "parent": par, "display": display})
        return sorted(result, key=lambda x: x["display"])
    return _cached("tags", _load, ttl=600)

def load_rules() -> list[dict]:
    def _load():
        try:
            return _sheet().worksheet("Rules").get_all_records()
        except Exception:
            return []
    return _cached("rules", _load, ttl=600)

# ── Writes ────────────────────────────────────────────────────────────────────

def batch_update_tags(updates: dict) -> tuple[int, list]:
    if not updates:
        return 0, []
    try:
        ws      = _sheet().worksheet("Transactions")
        all_ids = ws.col_values(1)
        cells, failed = [], []
        for txn_id, new_tags in updates.items():
            if txn_id in all_ids:
                cells.append(gspread.Cell(all_ids.index(txn_id) + 1, 5, new_tags))
            else:
                failed.append(txn_id)
        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")
        bust("transactions")
        return len(cells), failed
    except Exception as e:
        return 0, list(updates.keys())

def update_transaction(txn_id: str, updates: dict) -> bool:
    """Update arbitrary fields of a single transaction row by header name."""
    if not updates or not txn_id:
        return False
    try:
        ws = _sheet().worksheet("Transactions")
        headers = [h.strip().lower() for h in ws.row_values(1)]
        all_ids = ws.col_values(1)
        if txn_id not in all_ids:
            return False
        row_num = all_ids.index(txn_id) + 1
        cells = []
        for field, value in updates.items():
            key = field.strip().lower()
            if key in headers:
                col = headers.index(key) + 1
                cells.append(gspread.Cell(row_num, col,
                                          str(value) if value is not None else ""))
        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")
        bust("transactions")
        return True
    except Exception:
        return False

def save_rule(matcher_field: str, matcher_contains: str, tag_names: str) -> bool:
    try:
        import uuid
        from datetime import datetime
        ss = _sheet()
        try:
            ws = ss.worksheet("Rules")
        except Exception:
            ws = ss.add_worksheet("Rules", rows=1000, cols=5)
            ws.append_row(["id", "matcher_field", "matcher_contains", "tag_names", "created_at"])
        ws.append_row([
            str(uuid.uuid4())[:8], matcher_field, matcher_contains,
            tag_names, datetime.now().strftime("%Y-%m-%d %H:%M"),
        ])
        bust("rules")
        return True
    except Exception:
        return False

# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_inr(value: float) -> str:
    if abs(value) >= 1e7:
        return f"₹{value/1e7:.2f} Cr"
    if abs(value) >= 1e5:
        return f"₹{value/1e5:.1f} L"
    return f"₹{value:,.0f}"

def net_worth(accounts_df: pd.DataFrame, epf_balance: float = 0) -> float:
    if accounts_df.empty:
        return epf_balance
    return accounts_df["computed_balance"].sum() + epf_balance

def monthly_expense_avg(txn_df: pd.DataFrame, months: int = 12) -> float:
    if txn_df.empty:
        return 0
    expenses = txn_df[txn_df["type"] == "expense"].copy()
    cutoff   = pd.Timestamp.now() - pd.DateOffset(months=months)
    expenses = expenses[expenses["date"] >= cutoff]
    if expenses.empty:
        return 0
    return expenses.groupby("month")["amount"].sum().mean()
