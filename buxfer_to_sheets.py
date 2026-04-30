#!/usr/bin/env python3

import json
import os
import re
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from txn_rules import apply_tagging_rules

SHEET_ID = "1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "client_secret_614987389155-eil9t6s23i031n5et9eh2g0agl3gv9ea.apps.googleusercontent.com.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
BACKUP_FILE = os.path.join(BASE_DIR, "outputs", "buxfer_backup_2026-04-28.json")

# Accounts not in Buxfer (e.g. Axis CC tracked only via statements).
# These are appended after Buxfer accounts on every sync.
# Update opening_balance when you have a verified statement closing figure.
SUPPLEMENTAL_ACCOUNTS = [
    {
        "id": "AXIS_CC",
        "name": "Axis Bank CC",
        "bank": "Axis Bank",
        "currency": "INR",
        "balance": 0,           # not in Buxfer — no snapshot available
        "lastSynced": "",
        "interestRate": "",
        "maturityDate": "",
        "availableCredit": "",
        "totalCreditLine": 400000,   # from statement: Total Credit Limit 4,00,000
    },
]

# Verified closing balances from latest downloaded bank statements.
# opening_balance for these accounts = stmt_closing − total_txn_effect
# so that computed_balance always reconciles to the actual statement balance.
# Update these figures whenever you download fresh statements.
STMT_CLOSING_BALANCES = {
    "ICICI Salary":                                  764_232.16,   # ICICI Savings xxxx2826, 31-Mar-2026
    "Swaritha ICICI":                                367_371.05,   # ICICI Savings xxxx0319, 31-Mar-2026
    "Axis Joint":                                     11_608.24,   # Axis Savings Joint, 31-Mar-2026
    "ANMAY SWARITHA KAMBHATLA (MINOR) xxxx4266":   1_499_220.00,  # ICICI Savings xxxx4266, 30-Mar-2026
}


# ── Transfer / expense classifier ────────────────────────────────────────────
# Rules are checked top-to-bottom; first match wins.
# Each entry: (regex, new_type, tag)
#   new_type = "transfer" → keep as inter-account transfer
#   new_type = "expense"  → reclassify as an expense with the given tag
_RULES = [
    # ── Keep as transfer ───────────────────────────────────────────────────
    # CC bill payments via CRED
    (r'cred\.club|cred/', "transfer", "Credit Card Bill"),
    # Outgoing CC / loan payments to banks
    (r'amexcc|amex.*bill|aebcxx.*amex', "transfer", "Credit Card Bill"),
    (r'payment.*american express|to.*american express', "transfer", "Credit Card Bill"),
    # Bank-to-bank (savings ↔ savings, salary ↔ savings, etc.)
    (r'\bhdfc\b|\byes bank\b|\baxis bank\b|\bicici bank\b|\bidbi\b'
     r'|\bindusind\b|\bsbi\b|\bkotak\b|\bpnb\b|\bcanara\b'
     r'|\bstandard chartered\b|\bcitibank\b|\brbl bank\b', "transfer", ""),
    # "for balance / for savings" top-up transfers
    (r'forbalance|for.*balance|forsavings|for.*savings|for.*invest', "transfer", ""),
    # SIP / MF / investment transfers to tracked investment accounts
    (r'no limmits|nolimmits|formfs|indian clearing|bse_y|bse_', "transfer", "Investment"),
    # Loan repayments via ACH / NACH
    (r'ach/tp|nach|rloan|auto.*debit.*loan', "transfer", "Loan EMI"),

    # ── Reclassify as expense ──────────────────────────────────────────────
    # Loan EMI payments (not ACH)
    (r'loan emi|emi|axis finance|car loan|home loan|mortgage', "expense", "Loan EMI"),
    # Food & dining
    (r'zomato|swiggy|starbucks|dominos|domino|mcdonalds|kfc|pizza|burger'
     r'|dunkin|subway|taco|restaurant|cafe|bistro|dhaba', "expense", "Food / Restaurants"),
    (r'blinkit|grofers|bigbasket|dmart|zepto|jiomart|supermarket|grocery|milkbasket'
     r'|nature.*basket|fresh.*mart', "expense", "Food / Grocery"),
    # Health
    (r'pharmacy|medicine|medic|apollo|wellness|practo|hospital|clinic|doctor'
     r'|1mg|tata.*1mg|netmeds|pharmeasy', "expense", "Health / Pharmacy"),
    (r'health.*delivery|wellness.*delivery', "expense", "Health / Delivery"),
    # Transport
    (r'\buber\b|\bola\b|\bmeru\b|\brapido\b|namma yatri|yatri', "expense", "Public Transport / Taxi"),
    (r'irctc|railway|train ticket', "expense", "Travel"),
    (r'parking|vyapar.*park', "expense", "Car / Toll"),
    (r'carwash|car wash|ubale.*car', "expense", "Car / Maintenance"),
    (r'fuel|petrol|hp.*petrol|indian oil|bharat petroleum|iocl', "expense", "Car / Fuel"),
    # Shopping
    (r'amazon|flipkart|myntra|ajio|nykaa|meesho|snapdeal|shopclues', "expense", "Shopping"),
    # Utilities & telecom
    (r'\bjio\b.*(?!bank)|reliance.*jio', "expense", "Utilities"),
    (r'airtel(?!.*bank)|vodafone|vi pay|bsnl|idea cellular', "expense", "Utilities"),
    (r'bescom|adani.*elec|electricity|power.*bill|tata.*power', "expense", "Utilities / Electricity"),
    # Software / subscriptions
    (r'apple inc|apple\.com|app store|itunes', "expense", "Software"),
    (r'google(?!.*pay)|google play|youtube premium|google.*storage', "expense", "Software"),
    (r'netflix|hotstar|prime video|disney|spotify|gaana|wynk', "expense", "Fun"),
    (r'adobe|microsoft|office 365|dropbox|notion|slack', "expense", "Software"),
    # Entertainment
    (r'pvr|inox|bookmyshow|movies|cinema', "expense", "Movies"),
    # Home services
    (r'maintenance|mjd.*c2904|society.*maint', "expense", "Home"),
    # Household / salary payments
    (r'jayshree.*salary|maid|cook salary|driver salary', "expense", "Family"),
    # Courier / shipping
    (r'courier|dtdc|bluedart|delhivery|ecomexpress|akilk.*courier', "expense", "Shipping"),
    # Paytm QR / BharatPe (merchant UPI scans — treat as misc expense)
    (r'paytmqr|phonepe.*qr', "expense", ""),
    (r'bharatpe', "expense", "Shopping"),
]

# Tags that indicate a genuine inter-account movement (not a lifestyle expense)
_TRANSFER_INTENT_TAGS = {
    "Investment", "Loan EMI", "Credit Card Bill", "Loan",
    "Income", "Interest", "Salary", "SWAI", "",
}

def classify_source_transfer(description, existing_tag=""):
    """
    For a Buxfer source-only transfer record decide whether it is truly an
    inter-account transfer or an expense that was mis-tagged.

    Priority:
    1. Description rules (handles bank names, merchant names, investment keywords)
    2. If no rule matches AND user already tagged it with a lifestyle/expense
       category (Food, Shopping, etc.) → treat as expense and keep the tag
    3. Default → transfer

    Returns (new_type, tag).
    """
    d = description.lower()
    for pattern, new_type, tag in _RULES:
        if re.search(pattern, d):
            return new_type, tag
    # No description rule matched — fall back to existing Buxfer tag
    if existing_tag and existing_tag not in _TRANSFER_INTENT_TAGS:
        return "expense", existing_tag
    return "transfer", ""

def classify_dest_transfer(description):
    """
    For dest-only inbound transfer records decide type and tag.
    Returns (new_type, tag).
    """
    d = description.lower()
    # MF redemptions / FD maturity / interest → income tagged Investment/Interest
    if re.search(r'redempt|fd clos|principal inflow|sovereign gold|sgb interest'
                 r'|kmmf|quant mutual|interest income', d):
        return "income", "Investment"
    if re.search(r'income tax refund|it refund|tax refund', d):
        return "income", "Tax"
    if re.search(r'dividend|reliance industries', d):
        return "income", "Income"
    # CC payment confirmed, UPI received, NEFT/RTGS from banks → transfer
    return "transfer", ""


def get_tags(t):
    raw = t.get("tagNames", [])
    if isinstance(raw, list):
        return ", ".join(raw)
    return raw or ""


def make_txn_row(txn_id, desc, signed_amt, expense_amt, income_amt,
                 date, txn_type, status, acct_id, acct_name,
                 tags, is_pending, xfer_from, xfer_to):
    return [txn_id, desc, signed_amt, expense_amt, income_amt,
            date, txn_type, status, acct_id, acct_name,
            tags, is_pending, xfer_from, xfer_to]


def get_google_creds():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def ensure_sheet(spreadsheet, name, headers):
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=10000, cols=len(headers))
    ws.clear()
    return ws


def write_sheet(ws, headers, rows):
    data = [headers] + rows
    ws.update(range_name="A1", values=data, value_input_option="USER_ENTERED")
    ws.format("A1:Z1", {"textFormat": {"bold": True}})
    print(f"  ✓ {ws.title}: {len(rows)} rows")


def main():
    print("\n=== Buxfer → Google Sheets ===\n")

    print("• Loading backup...")
    with open(BACKUP_FILE) as f:
        backup = json.load(f)

    print("• Authenticating with Google (browser will open once)...")
    creds = get_google_creds()
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(SHEET_ID)

    # ── Transactions ──────────────────────────────────────────────────────────
    print("• Writing Transactions...")
    txn_headers = [
        "id", "description", "amount", "expense_amount", "income_amount",
        "date", "type", "status", "account_id", "account_name",
        "tags", "is_pending", "transfer_from", "transfer_to"
    ]

    # Schema:
    #   amount         = signed: +ve = debit/expense/transfer-source, -ve = credit/income/transfer-dest
    #   expense_amount = always positive debit amount; 0 for credits and transfer-destination rows
    #   income_amount  = always positive credit amount; 0 for debits and transfer-source rows
    #   transfer_from  = source account name (transfers only)
    #   transfer_to    = destination account name (transfers only)

    txn_rows = []
    stats = {"expense": 0, "income": 0, "transfer": 0,
             "reclassified_expense": 0, "synthesized_dest": 0}

    for t in backup["transactions"]:
        raw_amt   = float(t.get("amount") or 0)
        txn_type  = t.get("type", "")
        desc      = t.get("description", "")
        date      = t.get("date", "")
        status    = t.get("status", "")
        tags      = get_tags(t)
        is_pend   = t.get("isPending", False)

        from_acct = t.get("fromAccount") or {}
        to_acct   = t.get("toAccount")   or {}
        from_name = from_acct.get("name", "")
        to_name   = to_acct.get("name",   "")
        acct_id   = t.get("accountId", "")
        acct_name = t.get("accountName", "") or from_name or to_name

        if txn_type == "expense":
            r_tags, _, _ = apply_tagging_rules(desc, tags)
            txn_rows.append(make_txn_row(
                t["id"], desc, raw_amt, raw_amt, 0,
                date, "expense", status, acct_id, acct_name, r_tags, is_pend, "", ""))
            stats["expense"] += 1

        elif txn_type == "income":
            r_tags, _, _ = apply_tagging_rules(desc, tags)
            txn_rows.append(make_txn_row(
                t["id"], desc, -raw_amt, 0, raw_amt,
                date, "income", status, acct_id, acct_name, r_tags, is_pend, "", ""))
            stats["income"] += 1

        else:  # transfer — three sub-cases
            has_from = bool(from_name)
            has_to   = bool(to_name)

            if has_from and has_to:
                # ── Both sides known: single Buxfer record for a full inter-account
                #    transfer.  Write the source row AND synthesize a destination row.
                r_tags, _, r_xfer_to = apply_tagging_rules(desc, tags, "transfer", to_name)
                eff_to = r_xfer_to or to_name
                # Source (debit from fromAccount)
                txn_rows.append(make_txn_row(
                    t["id"], desc, raw_amt, raw_amt, 0,
                    date, "transfer", status, acct_id, from_name,
                    r_tags, is_pend, from_name, eff_to))
                # Destination (credit to toAccount) — synthetic row
                txn_rows.append(make_txn_row(
                    f"{t['id']}_to", desc, -raw_amt, 0, raw_amt,
                    date, "transfer", status, "", to_name,
                    r_tags, is_pend, from_name, eff_to))
                stats["synthesized_dest"] += 1
                stats["transfer"] += 1

            elif has_from and not has_to:
                # ── Source-only: money left this account but destination is external
                #    or untracked.  Classify: is this really an expense?
                new_type, auto_tag = classify_source_transfer(desc, tags)
                base_tags = tags if tags else auto_tag
                r_tags, r_type, r_xfer_to = apply_tagging_rules(desc, base_tags, new_type)
                eff_type = r_type or new_type
                if eff_type == "expense":
                    txn_rows.append(make_txn_row(
                        t["id"], desc, raw_amt, raw_amt, 0,
                        date, "expense", status, acct_id, acct_name,
                        r_tags, is_pend, "", ""))
                    stats["reclassified_expense"] += 1
                else:
                    txn_rows.append(make_txn_row(
                        t["id"], desc, raw_amt, raw_amt, 0,
                        date, "transfer", status, acct_id, acct_name,
                        r_tags, is_pend, from_name, r_xfer_to))
                    stats["transfer"] += 1

            else:
                # ── Dest-only: money arrived into toAccount from external source.
                #    Classify: investment income, tax refund, or plain transfer.
                new_type, auto_tag = classify_dest_transfer(desc)
                base_tags = tags if tags else auto_tag
                r_tags, _, _ = apply_tagging_rules(desc, base_tags)
                txn_rows.append(make_txn_row(
                    t["id"], desc, -raw_amt, 0, raw_amt,
                    date, new_type, status, acct_id, acct_name,
                    r_tags, is_pend, "", to_name))
                if new_type == "income":
                    stats["income"] += 1
                else:
                    stats["transfer"] += 1

    ws_txn = ensure_sheet(ss, "Transactions", txn_headers)
    write_sheet(ws_txn, txn_headers, txn_rows)
    print(f"     expense={stats['expense']}  income={stats['income']}"
          f"  transfer={stats['transfer']}")
    print(f"     reclassified-as-expense={stats['reclassified_expense']}"
          f"  synthesized-dest-rows={stats['synthesized_dest']}")

    # ── Accounts ──────────────────────────────────────────────────────────────
    # Compute net transaction effect per account from this backup AND from the
    # statement-derived rows (to correctly seed opening_balance for accounts
    # where we have a verified statement closing balance).
    from collections import defaultdict
    from statements_to_sheets import (
        parse_axis_cc_xlsx, parse_icici_cc_csv, parse_activity_csv,
        parse_icici_savings_csv, parse_axis_savings_csv, parse_auto_loan_pdf,
        dedup_key, STMT_DIR,
    )

    txn_effect = defaultdict(float)
    buxfer_keys = set()

    for t in backup["transactions"]:
        amt       = float(t.get("amount") or 0)
        ttype     = t.get("type", "")
        from_name = (t.get("fromAccount") or {}).get("name", "")
        to_name   = (t.get("toAccount")   or {}).get("name", "")
        acct      = t.get("accountName", "") or from_name or to_name

        if ttype == "expense":
            txn_effect[acct] -= amt
        elif ttype == "income":
            txn_effect[acct] += amt
        else:  # transfer
            if from_name and to_name:
                txn_effect[from_name] -= amt
                txn_effect[to_name]   += amt
            elif from_name:
                txn_effect[from_name] -= amt
            else:
                txn_effect[to_name]   += amt
        buxfer_keys.add((t.get("date", ""), str(abs(amt))))

    # Also fold in net-new statement rows (those not already in Buxfer)
    all_stmt = []
    for f in sorted(os.listdir(STMT_DIR)):
        if f.startswith("CC_Statement") and f.endswith(".xlsx"):
            all_stmt.extend(parse_axis_cc_xlsx(os.path.join(STMT_DIR, f)))
    for f in ["CreditCardStatement.CSV", "CreditCardStatement-2.CSV"]:
        p = os.path.join(STMT_DIR, f)
        if os.path.exists(p): all_stmt.extend(parse_icici_cc_csv(p))
    for f in ["activity-5.csv", "activity-6.csv"]:
        p = os.path.join(STMT_DIR, f)
        if os.path.exists(p): all_stmt.extend(parse_activity_csv(p))
    for f in os.listdir(STMT_DIR):
        if f.startswith("EStatement_M3_") and f.upper().endswith(".CSV"):
            all_stmt.extend(parse_icici_savings_csv(os.path.join(STMT_DIR, f)))
    p = os.path.join(STMT_DIR, "914010022157893.csv")
    if os.path.exists(p): all_stmt.extend(parse_axis_savings_csv(p))
    p = os.path.join(STMT_DIR, "AURXXXXXX839773.pdf")
    if os.path.exists(p): all_stmt.extend(parse_auto_loan_pdf(p))

    for row in all_stmt:
        if dedup_key(row) in buxfer_keys:
            continue  # already counted via Buxfer
        acct   = row[9]
        amount = float(str(row[2]).replace(",", ""))
        txn_effect[acct] += -amount  # signed: +ve = debit = reduces balance

    print("• Writing Accounts...")
    # Columns:
    #   opening_balance  = buxfer_balance − txn_effect  (static seed written once)
    #   computed_balance = SUMIF formula (live, updates as transactions are added)
    #   buxfer_balance   = snapshot from API at backup time (reference)
    acc_headers = [
        "id", "name", "bank", "currency",
        "opening_balance", "computed_balance", "buxfer_balance",
        "last_txn_date", "days_stale",
        "last_synced", "interest_rate", "maturity_date",
        "available_credit", "total_credit_line",
    ]
    # Transactions tab: col J (index 9) = account_name, col C (index 2) = amount
    # computed_balance = opening_balance − SUMIF(amount by account_name)
    # Because: amount is +ve for debits (reduces balance) and −ve for credits
    # so −SUMIF gives the net balance change.
    # Row 2 in Accounts sheet → formula references B2 (name) and E2 (opening_balance)
    all_accounts = backup["accounts"] + SUPPLEMENTAL_ACCOUNTS
    acc_rows = []
    for row_idx, a in enumerate(all_accounts, start=2):
        name        = a.get("name", "")
        buxfer_bal  = float(a.get("balance") or 0)
        if name in STMT_CLOSING_BALANCES:
            # Use verified statement closing balance as the anchor
            opening_bal = round(STMT_CLOSING_BALANCES[name] - txn_effect.get(name, 0), 2)
        else:
            # Fall back to Buxfer's last-known balance
            opening_bal = round(buxfer_bal - txn_effect.get(name, 0), 2)
        # Formulas — all reference Transactions col J (account_name) and col F (date)
        bal_formula       = f'=E{row_idx}-SUMIF(Transactions!$J:$J,B{row_idx},Transactions!$C:$C)'
        last_date_formula = f'=IFERROR(IF(MAXIFS(Transactions!$F:$F,Transactions!$J:$J,B{row_idx})>DATE(2000,1,1),MAXIFS(Transactions!$F:$F,Transactions!$J:$J,B{row_idx}),""),"")'
        days_stale_formula= f'=IF(H{row_idx}<>"",TODAY()-H{row_idx},"")'
        acc_rows.append([
            a.get("id", ""),
            name,
            a.get("bank", ""),
            a.get("currency", ""),
            opening_bal,
            bal_formula,
            buxfer_bal,
            last_date_formula,
            days_stale_formula,
            a.get("lastSynced", ""),
            a.get("interestRate", ""),
            a.get("maturityDate", ""),
            a.get("availableCredit", ""),
            a.get("totalCreditLine", ""),
        ])

    ws_acc = ensure_sheet(ss, "Accounts", acc_headers)
    # Write headers + static data first, then update formula cells individually
    # so gspread doesn't escape the '=' sign
    data = [acc_headers] + acc_rows
    ws_acc.update(range_name="A1", values=data, value_input_option="USER_ENTERED")
    ws_acc.format("A1:N1", {"textFormat": {"bold": True}})
    print(f"  ✓ Accounts: {len(acc_rows)} rows (with live SUMIF balance formula)")

    # ── Budgets ───────────────────────────────────────────────────────────────
    print("• Writing Budgets...")
    bud_headers = ["id", "name", "limit", "current_amount", "period", "tags"]
    bud_rows = []
    for b in backup["budgets"]:
        bud_rows.append([
            b.get("id", ""),
            b.get("name", ""),
            b.get("limit", ""),
            b.get("currentAmount", ""),
            b.get("period", ""),
            ", ".join(b.get("tags", [])) if isinstance(b.get("tags"), list) else b.get("tags", ""),
        ])
    ws_bud = ensure_sheet(ss, "Budgets", bud_headers)
    write_sheet(ws_bud, bud_headers, bud_rows)

    # ── Tags ──────────────────────────────────────────────────────────────────
    print("• Writing Tags...")
    tag_headers = ["id", "name"]
    tag_rows = [[t.get("id", ""), t.get("name", "")] for t in backup["tags"]]
    ws_tag = ensure_sheet(ss, "Tags", tag_headers)
    write_sheet(ws_tag, tag_headers, tag_rows)

    print(f"\n✓ All data written to Google Sheet!")
    print(f"  https://docs.google.com/spreadsheets/d/{SHEET_ID}\n")


if __name__ == "__main__":
    main()
