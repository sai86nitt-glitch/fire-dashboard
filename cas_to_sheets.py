#!/usr/bin/env python3
"""
Parse CAMS/KFintech CAS PDF → Google Sheets
Writes two tabs:
  - MF Transactions  : every SIP / purchase / redemption line
  - MF Holdings      : current holdings summary (non-zero balance funds only)
"""

import re, os
import pdfplumber
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# ── Config ─────────────────────────────────────────────────────────────────
SHEET_ID   = "1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY"
SCOPES     = ["https://www.googleapis.com/auth/spreadsheets"]
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CREDS_FILE = os.path.join(BASE_DIR, "client_secret_614987389155-eil9t6s23i031n5et9eh2g0agl3gv9ea.apps.googleusercontent.com.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
CAS_PDF    = "/Users/swaitales/Downloads/ALXXXXXX6P_01012015-22042026_CP209894556_22042026101346635-compressed.pdf"

# ── Auth ───────────────────────────────────────────────────────────────────
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

# ── Helpers ────────────────────────────────────────────────────────────────
def parse_amount(s):
    """Handle (1,234.56) as negative and 1,234.56 as positive."""
    s = str(s).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        v = float(s)
        return -v if negative else v
    except:
        return None

def norm_date(s):
    from datetime import datetime
    for fmt in ["%d-%b-%Y", "%d-%b-%y"]:
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except:
            pass
    return s.strip()

# ── CAS Parser ─────────────────────────────────────────────────────────────
def parse_cas(pdf_path):
    txn_rows     = []   # all transactions
    holding_rows = []   # current non-zero holdings

    fund_house   = ""
    fund_name    = ""
    isin         = ""
    folio        = ""
    holder       = "Swaritha Chennapragada"
    closing_bal  = None
    cost_val     = None
    market_val   = None
    nav_date     = None
    nav_price    = None

    # Regex patterns
    re_fund_house = re.compile(r'^(AXIS Mutual Fund|Kotak Mutual Fund|Edelweiss Mutual Fund|'
                                r'Bandhan Mutual Fund|DSP Mutual Fund|Invesco Mutual Fund|'
                                r'PGIM INDIA MUTUAL FUND|Quant MF)\s*$', re.IGNORECASE)
    re_isin       = re.compile(r'ISIN:\s*(IN[A-Z0-9]{10})')
    re_folio      = re.compile(r'Folio No:\s*([\d/\s]+)')
    re_txn        = re.compile(
        r'^(\d{2}-[A-Za-z]{3}-\d{4})\s+'       # date
        r'(.+?)\s+'                               # description
        r'([\d,]+\.\d+|\([\d,]+\.\d+\))\s+'      # amount
        r'([\d,]+\.\d+|\([\d,]+\.\d+\))\s+'      # units
        r'([\d,.]+)\s+'                           # price
        r'([\d,]+\.\d+|\([\d,]+\.\d+\))\s*$'     # unit balance
    )
    re_closing    = re.compile(
        r'Closing Unit Balance:\s*([\d,]+\.\d+)'
        r'.*?Total Cost Value:\s*([\d,]+\.\d+)'
        r'.*?Market Value on (\d{2}-[A-Za-z]{3}-\d{4}):\s*INR\s*([\d,]+\.\d+)',
        re.DOTALL
    )
    re_nav        = re.compile(r'NAV on (\d{2}-[A-Za-z]{3}-\d{4}):\s*INR\s*([\d,.]+)')

    # Fund name line: code-FUND NAME ... ISIN: xxx
    re_fund_line  = re.compile(r'^[A-Z0-9]+-(.+?)\s*-\s*ISIN:\s*(IN[A-Z0-9]{10})', re.IGNORECASE)

    def flush_holding():
        nonlocal closing_bal, cost_val, market_val, nav_date, nav_price
        if fund_name and closing_bal is not None and float(closing_bal.replace(",","")) > 0:
            holding_rows.append([
                fund_house, fund_name, isin, folio, holder,
                float(closing_bal.replace(",","")),
                float(cost_val.replace(",",""))    if cost_val    else "",
                float(market_val.replace(",",""))  if market_val  else "",
                nav_date or "",
                float(nav_price.replace(",",""))   if nav_price   else "",
            ])
        closing_bal = cost_val = market_val = nav_date = nav_price = None

    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Fund house header
        m = re_fund_house.match(line)
        if m:
            fund_house = m.group(1)
            continue

        # Fund name + ISIN line
        m = re_fund_line.match(line)
        if m:
            flush_holding()
            fund_name = m.group(1).strip()
            # clean up trailing advisor/registrar text
            fund_name = re.sub(r'\s*\(Advisor:.*', '', fund_name).strip()
            isin      = m.group(2)
            continue

        # Folio line
        m = re_folio.search(line)
        if m and "Folio No:" in line:
            folio = m.group(1).strip()
            continue

        # Closing balance line — flush immediately when we see it
        m = re_closing.search(line)
        if m:
            closing_bal = m.group(1)
            cost_val    = m.group(2)
            nav_date    = norm_date(m.group(3))
            market_val  = m.group(4)
            # Flush right now — don't wait for next fund line (avoids split-line bugs)
            flush_holding()
            continue

        # NAV line
        m = re_nav.search(line)
        if m:
            nav_price = m.group(2)

        # Transaction line
        m = re_txn.match(line)
        if m and fund_name:
            date     = norm_date(m.group(1))
            txn_type = m.group(2).strip()
            # skip stamp duty / STT / admin lines
            if any(x in txn_type for x in ["Stamp Duty", "STT Paid", "Address Updated",
                                             "Registration", "Invalid Purchase", "SIPCancelled",
                                             "Cancelled", "SIPSystem"]):
                continue
            amount   = parse_amount(m.group(3))
            units    = parse_amount(m.group(4))
            price    = parse_amount(m.group(5))
            unit_bal = parse_amount(m.group(6))
            if amount is None:
                continue

            txn_rows.append([
                date, fund_house, fund_name, isin, folio, holder,
                txn_type, round(amount, 2), round(units, 3) if units else "",
                round(price, 4) if price else "",
                round(unit_bal, 3) if unit_bal else ""
            ])

    flush_holding()
    return txn_rows, holding_rows

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("\n=== CAS → Google Sheets ===\n")

    print("• Parsing CAS PDF...")
    txn_rows, holding_rows = parse_cas(CAS_PDF)
    print(f"  {len(txn_rows)} transactions, {len(holding_rows)} active holdings")

    print("• Connecting to Google Sheets...")
    creds = get_creds()
    gc    = gspread.authorize(creds)
    ss    = gc.open_by_key(SHEET_ID)

    # ── MF Transactions tab ──
    txn_headers = ["date","fund_house","fund_name","isin","folio","holder",
                   "transaction_type","amount_inr","units","nav_price","unit_balance"]
    try:
        ws_txn = ss.worksheet("MF Transactions")
        ws_txn.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws_txn = ss.add_worksheet("MF Transactions", rows=5000, cols=len(txn_headers))
    ws_txn.update(range_name="A1", values=[txn_headers] + txn_rows)
    ws_txn.format("A1:K1", {"textFormat": {"bold": True}})
    print(f"  ✓ MF Transactions: {len(txn_rows)} rows")

    # ── MF Holdings tab ──
    hld_headers = ["fund_house","fund_name","isin","folio","holder",
                   "closing_units","cost_value_inr","market_value_inr",
                   "nav_date","nav_price"]
    try:
        ws_hld = ss.worksheet("MF Holdings")
        ws_hld.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws_hld = ss.add_worksheet("MF Holdings", rows=500, cols=len(hld_headers))
    ws_hld.update(range_name="A1", values=[hld_headers] + holding_rows)
    ws_hld.format("A1:J1", {"textFormat": {"bold": True}})

    total_cost   = sum(r[6] for r in holding_rows if r[6])
    total_market = sum(r[7] for r in holding_rows if r[7])
    print(f"  ✓ MF Holdings: {len(holding_rows)} active funds")
    print(f"     Cost:   ₹{total_cost:,.2f}")
    print(f"     Market: ₹{total_market:,.2f}")
    print(f"     Gain:   ₹{total_market-total_cost:,.2f} ({(total_market/total_cost-1)*100:.1f}%)")

    print(f"\n✓ Done!")
    print(f"  Note: OneTreeHill in Buxfer (₹1,02,10,000) was a stale manual snapshot.")
    print(f"  Actual MF portfolio market value as of Apr-2026: ₹{total_market:,.2f}")
    print(f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}\n")

if __name__ == "__main__":
    main()
