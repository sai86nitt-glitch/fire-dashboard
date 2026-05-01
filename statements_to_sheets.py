#!/usr/bin/env python3
"""
Parse all bank statements (CSV, XLSX, PDF) and sync to Google Sheets Transactions tab.
Deduplicates against existing rows by (date, amount).
"""

import os, re, json, hashlib
import openpyxl
import pdfplumber
import gspread
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from txn_rules import apply_tagging_rules

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID      = "1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY"
SCOPES        = ["https://www.googleapis.com/auth/spreadsheets"]
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE    = os.path.join(BASE_DIR, "client_secret_614987389155-eil9t6s23i031n5et9eh2g0agl3gv9ea.apps.googleusercontent.com.json")
TOKEN_FILE    = os.path.join(BASE_DIR, "token.json")
STMT_DIR      = os.path.join(BASE_DIR, "bankstatements")
ACCOUNT_MAP_FILE = os.path.join(BASE_DIR, "account_map.json")

def _load_account_map():
    try:
        with open(ACCOUNT_MAP_FILE) as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except FileNotFoundError:
        return {}

ACCOUNT_MAP = _load_account_map()

def canonical_account(name):
    """Return the Buxfer-canonical account name, falling back to the original."""
    return ACCOUNT_MAP.get(name, name)

# Must match buxfer_to_sheets.py column order exactly so tags/type/date land
# in the correct columns and the dashboard's tag editor works uniformly.
# Must match buxfer_to_sheets.py txn_headers exactly — both scripts write to the
# same Transactions sheet and buxfer runs first, setting the column order.
# Buxfer schema: id | description | amount | expense_amount | income_amount |
#                date | type | status | account_id | account_name |
#                tags | is_pending | transfer_from | transfer_to
TXN_HEADERS = [
    "id", "description", "amount", "expense_amount", "income_amount",
    "date", "type", "status", "account_id", "account_name",
    "tags", "is_pending", "transfer_from", "transfer_to",
]

# Column index constants (0-based) — used by transfer-collapse logic below
COL_EXPENSE_AMT = 3
COL_INCOME_AMT  = 4
COL_DATE        = 5
COL_TYPE        = 6
COL_ACCT        = 9
COL_TAGS        = 10
COL_XFROM       = 12
COL_XTO         = 13

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_creds():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds

# ── Helpers ───────────────────────────────────────────────────────────────────
def make_id(date, description, amount, account):
    key = f"{date}|{description}|{amount}|{account}"
    return "STMT_" + hashlib.md5(key.encode()).hexdigest()[:12]

def clean_amount(val):
    """Strip ₹, commas, spaces → float."""
    if val is None: return 0.0
    s = str(val).replace("₹", "").replace(",", "").replace(" ", "").strip()
    try: return abs(float(s))
    except: return 0.0

def norm_date(s, fmt_hints=None):
    """Normalize various date formats to YYYY-MM-DD."""
    s = str(s).strip()
    formats = fmt_hints or [
        "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
        "%d-%b-%Y", "%d %b '%y", "%d-%b-%y",
        "%d-%b-%y", "%B %d, %Y",
    ]
    for fmt in formats:
        try: return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except: pass
    return s  # fallback

def make_row(date, desc, amount, is_debit, account_name):
    """
    Build a transaction row matching the BUXFER schema (14 columns):
    id | description | amount | expense_amount | income_amount |
    date | type | status | account_id | account_name |
    tags | is_pending | transfer_from | transfer_to

    This MUST stay aligned with buxfer_to_sheets.py txn_headers.
    """
    signed_amt     = round(amount, 2) if is_debit else round(-amount, 2)
    expense_amount = round(amount, 2) if is_debit else 0
    income_amount  = round(amount, 2) if not is_debit else 0
    txn_type       = "expense" if is_debit else "income"
    canon_name     = canonical_account(account_name)
    row_id         = make_id(date, desc, amount, canon_name)
    # Apply tagging rules — may add tags or override type
    tags, eff_type, _ = apply_tagging_rules(desc, "", txn_type)
    return [
        row_id, desc, signed_amt, expense_amount, income_amount,
        date, eff_type, "cleared", "", canon_name,
        tags, "false", "", "",
    ]

# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_axis_cc_xlsx(path):
    """Axis Bank CC XLSX — 10 monthly statement files."""
    rows = []
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    in_txn = False
    for r in ws.iter_rows(values_only=True):
        if r[0] == "Date" and r[1] == "Transaction Details":
            in_txn = True
            continue
        if not in_txn: continue
        if r[0] and str(r[0]).startswith("**"): break
        date_val, desc, _, amt_val, dr_cr, _ = (list(r) + [None]*6)[:6]
        if not date_val or not amt_val: continue
        date  = norm_date(date_val, ["%d %b '%y"])
        amt   = clean_amount(amt_val)
        is_dr = str(dr_cr).strip().lower() == "debit"
        rows.append(make_row(date, str(desc).strip(), amt, is_dr, "Axis Bank CC"))
    return rows

def parse_icici_cc_csv(path):
    """ICICI CC CSV — multiple card sections."""
    rows = []
    current_card = "ICICI CC"
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    in_txn = False
    for line in lines:
        line = line.strip().strip('"')
        # Card section header (e.g. "4501 XXXX XXXX 4000")
        m = re.match(r'^(\d{4} XXXX XXXX \d{4})$', line)
        if m:
            current_card = f"ICICI CC {m.group(1)[-4:]}"
            in_txn = True
            continue
        if '"Date","Sr.No."' in line or line.startswith("Date,Sr.No."):
            in_txn = True
            continue
        if not in_txn: continue
        # Parse: "17-APR-25","1","desc","pts","intl","amount","billing"
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) < 6: continue
        try:
            date = norm_date(parts[0], ["%d-%b-%y", "%d-%b-%Y"])
            desc = parts[2]
            amt_str = parts[5].replace(",", "")
            amt = float(amt_str)
            is_dr = amt >= 0
            rows.append(make_row(date, desc, abs(amt), is_dr, current_card))
        except: continue
    return rows

def parse_activity_csv(path):
    """Amex activity CSV — Date, Description, Amount."""
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    for line in lines[1:]:  # skip header
        parts = [p.strip() for p in line.strip().split(",", 2)]
        if len(parts) < 3: continue
        try:
            date = norm_date(parts[0], ["%m/%d/%Y", "%d/%m/%Y"])
            desc = parts[1]
            amt  = float(parts[2].replace(",", ""))
            is_dr = amt >= 0
            rows.append(make_row(date, desc, abs(amt), is_dr, "Amex CC"))
        except: continue
    return rows

def parse_icici_savings_csv(path):
    """ICICI Savings EStatement_M3 CSV."""
    rows = []
    # Derive account name from filename
    fname = os.path.basename(path)
    acct_match = re.search(r'\|(\d+)\.CSV', fname, re.IGNORECASE)
    acct_suffix = acct_match.group(1)[-4:] if acct_match else "????"
    acct_name = f"ICICI Savings {acct_suffix}"

    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    in_txn = False
    for line in lines:
        line = line.strip()
        if line.startswith("DATE,MODE,PARTICULARS"):
            in_txn = True
            continue
        if not in_txn: continue
        parts = line.split(",")
        if len(parts) < 5: continue
        date_str, _, desc = parts[0], parts[1], parts[2]
        deposits    = parts[3].strip()
        withdrawals = parts[4].strip()
        if date_str in ("DATE", "B/F", ""): continue
        try:
            date = norm_date(date_str, ["%d-%m-%Y"])
        except: continue
        # Skip non-transaction rows (reward points summary etc.)
        if not re.match(r'^\d{2}-\d{2}-\d{4}$', date_str): continue
        try:
            dep = float(deposits)    if deposits    and deposits    != "0" else 0.0
            wdl = float(withdrawals) if withdrawals and withdrawals != "0" else 0.0
        except ValueError: continue
        if dep > 0:
            rows.append(make_row(date, desc.strip(), dep, False, acct_name))
        if wdl > 0:
            rows.append(make_row(date, desc.strip(), wdl, True, acct_name))
    return rows

def parse_axis_savings_csv(path):
    """Axis Bank Savings CSV — 914010022157893."""
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    in_txn = False
    for line in lines:
        line = line.strip()
        if line.startswith("Tran Date,CHQNO,PARTICULARS"):
            in_txn = True
            continue
        if not in_txn: continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5: continue
        date_str, _, desc, dr, cr = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not re.match(r'^\d{2}-\d{2}-\d{4}$', date_str): continue
        date = norm_date(date_str, ["%d-%m-%Y"])
        try:
            dr_amt = float(dr.replace(",","").strip()) if dr.strip() else 0.0
            cr_amt = float(cr.replace(",","").strip()) if cr.strip() else 0.0
        except ValueError: continue
        if dr_amt > 0:
            rows.append(make_row(date, desc.strip(), dr_amt, True,  "Axis Savings Joint"))
        if cr_amt > 0:
            rows.append(make_row(date, desc.strip(), cr_amt, False, "Axis Savings Joint"))
    return rows

def parse_auto_loan_pdf(path):
    """Axis Finance auto loan PDF — EMI debit/credit rows."""
    rows = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                # Match: "10-04-2025 10-04-2025 Pmnt Rcvd ... 0.00 79,560.00"
                m = re.match(
                    r'(\d{2}-\d{2}-\d{4})\s+\d{2}-\d{2}-\d{4}\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$',
                    line.strip()
                )
                if not m: continue
                date     = norm_date(m.group(1), ["%d-%m-%Y"])
                desc     = m.group(2).strip()
                increased = float(m.group(3).replace(",",""))
                decreased = float(m.group(4).replace(",",""))
                if decreased > 0:
                    rows.append(make_row(date, desc, decreased, True,  "Q5 Car Loan"))
                elif increased > 0:
                    rows.append(make_row(date, desc, increased, False, "Q5 Car Loan"))
    return rows

# ── Dedup ─────────────────────────────────────────────────────────────────────
def norm_amount(v):
    """Normalize an amount to a canonical string: abs value, no trailing zeros.
    '200000' → '200000', '200000.0' → '200000', '1234.50' → '1234.5'
    """
    try:
        return f"{abs(float(str(v).replace(',', ''))):g}"
    except:
        return str(v).strip()

def norm_date_str(s):
    """Normalize any date string to YYYY-MM-DD regardless of how Sheets returns it.
    Handles: '2025-05-10', '5/10/2025', '10/05/2025', '2025-05-10 0:00:00' etc.
    """
    s = str(s).strip().split(" ")[0]   # drop time component
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d-%m-%Y",
                "%d-%b-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except:
            pass
    return s

def dedup_key(row):
    """(date, abs_amount) as dedup fingerprint — works regardless of sign convention."""
    return (norm_date_str(str(row[5])), norm_amount(row[2]))

# ── Demat PDF parser ─────────────────────────────────────────────────────────
def parse_demat_pdf(path):
    """ICICI Demat statement PDF → list of investment transaction rows."""
    rows = []
    isin, security = "", ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                # Security header: "INF846K01W80 AXIS MUTUAL FUND AXIS GOLD ETF (Rs. 125.99)"
                isin_m = re.match(r'^(IN[A-Z0-9]{10})\s+(.+?)(?:\s+\(Rs\.[\s\d.]+\))?$', line)
                if isin_m:
                    isin     = isin_m.group(1)
                    security = isin_m.group(2).strip()
                    continue
                # Opening/Closing balance
                bal_m = re.match(r'^(\d{2}-\w{3}-\d{4})\s+(Opening|Closing) Balance.+?(\d[\d,]*\.?\d*)?$', line)
                if bal_m and isin:
                    date = norm_date(bal_m.group(1), ["%d-%b-%Y"])
                    qty_val = bal_m.group(3) or "0"
                    amt = float(re.sub(r'[^\d.]', '', qty_val) or 0)
                    rows.append([date, isin, security, bal_m.group(2) + " Balance",
                                 "", bal_m.group(3) or "0", amt, "Swaritha Demat"])
                    continue
                # Transaction line: "02-Mar-2026 81000222250344 By CM ... Cr 37"
                txn_m = re.match(
                    r'^(\d{2}-\w{3}-\d{4})\s+(\d+)\s+(.*?)\s+(Dr|Cr)\s+([\d,]+\.?\d*)\s*$', line
                )
                if txn_m and isin:
                    date       = norm_date(txn_m.group(1), ["%d-%b-%Y"])
                    nsdl_ref   = txn_m.group(2)
                    particulars = txn_m.group(3).strip()
                    dr_cr      = txn_m.group(4)
                    qty        = txn_m.group(5)
                    rows.append([date, isin, security, particulars, dr_cr, qty, "", "Swaritha Demat"])
    return rows

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n=== Bank Statements → Google Sheets ===\n")

    # 1. Parse all statements
    all_new = []

    print("• Parsing Axis Bank CC (XLSX)...")
    for f in sorted(os.listdir(STMT_DIR)):
        if f.startswith("CC_Statement") and f.endswith(".xlsx"):
            r = parse_axis_cc_xlsx(os.path.join(STMT_DIR, f))
            all_new.extend(r)
    print(f"  {len(all_new)} rows so far")

    print("• Parsing ICICI CC (CSV)...")
    before = len(all_new)
    for f in ["CreditCardStatement.CSV", "CreditCardStatement-2.CSV"]:
        p = os.path.join(STMT_DIR, f)
        if os.path.exists(p):
            all_new.extend(parse_icici_cc_csv(p))
    print(f"  +{len(all_new)-before} rows")

    print("• Parsing Amex (activity CSV)...")
    before = len(all_new)
    for f in ["activity-5.csv", "activity-6.csv"]:
        p = os.path.join(STMT_DIR, f)
        if os.path.exists(p):
            all_new.extend(parse_activity_csv(p))
    print(f"  +{len(all_new)-before} rows")

    print("• Parsing ICICI Savings (EStatement CSV)...")
    before = len(all_new)
    for f in os.listdir(STMT_DIR):
        if f.startswith("EStatement_M3_") and f.upper().endswith(".CSV"):
            all_new.extend(parse_icici_savings_csv(os.path.join(STMT_DIR, f)))
    print(f"  +{len(all_new)-before} rows")

    print("• Parsing Axis Savings joint (CSV)...")
    before = len(all_new)
    p = os.path.join(STMT_DIR, "914010022157893.csv")
    if os.path.exists(p):
        all_new.extend(parse_axis_savings_csv(p))
    print(f"  +{len(all_new)-before} rows")

    print("• Parsing Auto Loan (PDF)...")
    before = len(all_new)
    p = os.path.join(STMT_DIR, "AURXXXXXX839773.pdf")
    if os.path.exists(p):
        all_new.extend(parse_auto_loan_pdf(p))
    print(f"  +{len(all_new)-before} rows")

    print(f"\n  Total parsed: {len(all_new)} rows")

    # 2. Connect to Sheets
    print("\n• Connecting to Google Sheets...")
    creds = get_creds()
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(SHEET_ID)
    ws = ss.worksheet("Transactions")

    # 3. Load existing rows for dedup
    print("• Loading existing transactions for dedup...")
    existing_data = ws.get_all_values()
    existing_keys = set()
    for row in existing_data[1:]:  # skip header
        if len(row) >= 6:
            existing_keys.add((norm_date_str(str(row[5])), norm_amount(row[2])))

    print(f"  Existing rows: {len(existing_data)-1}")

    # 4. Detect & collapse inter-account transfers (including CC payments with ±1 day lag)
    # Logic: if the same absolute amount appears as expense in one account AND income in
    # another on the same date OR ±1 day, it's an inter-account transfer.
    # Keep the expense side (debit from source), drop the income side (credit to destination),
    # and label the kept row as type=transfer / expense_amount=0 / income_amount=0.
    from collections import defaultdict
    from datetime import datetime, timedelta

    def date_variants(d):
        """Return date string and ±1 day variants for fuzzy matching."""
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            return {(dt + timedelta(days=x)).strftime("%Y-%m-%d") for x in (-1, 0, 1)}
        except:
            return {d}

    # Column indices — buxfer schema (defined at module level, repeated here for clarity):
    #   0=id, 1=desc, 2=amount, 3=expense_amount, 4=income_amount,
    #   5=date, 6=type, 7=status, 8=account_id, 9=account_name,
    #   10=tags, 11=is_pending, 12=transfer_from, 13=transfer_to
    # (using module-level COL_* constants defined with TXN_HEADERS)

    # Index by (amount) → list of (i, date, type)
    by_amount = defaultdict(list)
    for i, row in enumerate(all_new):
        amt = str(abs(float(str(row[2]).replace(",", "") or 0)))
        by_amount[amt].append((i, str(row[COL_DATE]), row[COL_TYPE]))

    transfer_indices = set()
    for amt, entries in by_amount.items():
        expenses = [(i, d) for i, d, t in entries if t == "expense"]
        incomes  = [(i, d) for i, d, t in entries if t == "income"]
        if not expenses or not incomes:
            continue
        for exp_i, exp_date in expenses:
            exp_variants = date_variants(exp_date)
            for inc_i, inc_date in incomes:
                if inc_date in exp_variants and inc_i not in transfer_indices:
                    transfer_indices.add(inc_i)                   # drop income side
                    exp_row = all_new[exp_i]
                    inc_row = all_new[inc_i]
                    exp_row[COL_TYPE]  = "transfer"               # relabel as transfer
                    exp_row[COL_XFROM] = exp_row[COL_ACCT]        # transfer_from = source account
                    exp_row[COL_XTO]   = inc_row[COL_ACCT]        # transfer_to   = dest account
                    break

    print(f"  Detected {len(transfer_indices)} inter-account transfers collapsed (±1 day)")

    # 5. Filter new rows
    to_add = []
    seen_keys = set()
    for i, row in enumerate(all_new):
        if i in transfer_indices:
            continue   # drop the income side of own-account transfers
        k = dedup_key(row)
        if k not in existing_keys and k not in seen_keys:
            to_add.append(row)
            seen_keys.add(k)

    print(f"  New (after dedup): {len(to_add)}")

    if not to_add:
        print("\n✓ Nothing new to add — sheet is already up to date.")
    else:
        # 5. Append to Transactions sheet
        print(f"\n• Appending {len(to_add)} new rows to Transactions...")
        ws.append_rows(to_add, value_input_option="USER_ENTERED")
        print(f"  ✓ Added {len(to_add)} new transactions.")

    # 6. Parse & write Demat PDFs → Investments tab
    print("\n• Parsing Demat PDFs...")
    demat_rows = []
    for f in sorted(os.listdir(STMT_DIR)):
        if f.startswith("EStatement_IN") and f.endswith(".pdf"):
            demat_rows.extend(parse_demat_pdf(os.path.join(STMT_DIR, f)))
    print(f"  {len(demat_rows)} demat rows parsed")

    if demat_rows:
        inv_headers = ["date","isin","security_name","particulars","dr_cr","quantity","value","account"]
        try:
            ws_inv = ss.worksheet("Investments")
            ws_inv.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws_inv = ss.add_worksheet(title="Investments", rows=5000, cols=len(inv_headers))
        ws_inv.update(range_name="A1", values=[inv_headers] + demat_rows)
        ws_inv.format("A1:H1", {"textFormat": {"bold": True}})
        print(f"  ✓ Investments tab written with {len(demat_rows)} rows")

    print(f"\n✓ All done!")
    print(f"  Rows with no tags are ready to categorize in the Transactions tab.")
    print(f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}\n")

if __name__ == "__main__":
    main()
