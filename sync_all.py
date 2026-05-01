#!/usr/bin/env python3
"""
Full sync: backup → Buxfer → Statements → balance correction.
Always run this instead of the individual scripts.
"""

import subprocess, sys, os, warnings
warnings.filterwarnings("ignore")

BASE = os.path.dirname(os.path.abspath(__file__))

def run(script):
    print(f"\n{'='*60}")
    print(f"  Running {script}")
    print(f"{'='*60}\n")
    subprocess.run([sys.executable, os.path.join(BASE, script)], check=True)

# ── Step 0: Back up current sheet before overwriting anything ─────────────────
print("\n• Backing up current sheet data before sync…")
run("backup_sheet.py")

run("buxfer_to_sheets.py")
run("statements_to_sheets.py")

# ── Step 3: Fix opening_balance so computed_balance = statement closing balance ──
# Now that BOTH Buxfer and statement rows are in the sheet, we can read the
# actual SUMIF per account and back-calculate the correct opening_balance.
# Formula in sheet: computed_balance = opening_balance - SUMIF(amount for account)
# Rearranged:       opening_balance  = target_balance  + SUMIF(amount for account)

print("\n• Correcting opening balances to match statement closing figures...")

import sys
sys.path.insert(0, BASE)
from buxfer_to_sheets import (
    SHEET_ID, STMT_CLOSING_BALANCES, get_google_creds
)
import gspread
from collections import defaultdict

creds = get_google_creds()
gc    = gspread.authorize(creds)
ss    = gc.open_by_key(SHEET_ID)

# Read all transactions to compute the real per-account SUMIF
print("  Reading Transactions tab...")
ws_txn = ss.worksheet("Transactions")
txn_data = ws_txn.get_all_values()
headers  = txn_data[0]
col_acct = headers.index("account_name")   # col J
col_amt  = headers.index("amount")         # col C

sumif = defaultdict(float)
for row in txn_data[1:]:
    if len(row) <= max(col_acct, col_amt):
        continue
    acct = row[col_acct].strip()
    try:
        sumif[acct] += float(str(row[col_amt]).replace(",", ""))
    except ValueError:
        pass

# Read Accounts tab to find row positions
ws_acc = ss.worksheet("Accounts")
acc_data = ws_acc.get_all_values()
acc_headers = acc_data[0]
col_name    = acc_headers.index("name")
col_open    = acc_headers.index("opening_balance")

updates = []
for i, row in enumerate(acc_data[1:], start=2):   # row 2 onwards (1-indexed)
    name = row[col_name].strip()
    if name not in STMT_CLOSING_BALANCES:
        continue
    target      = STMT_CLOSING_BALANCES[name]
    # computed = opening - SUMIF  →  opening = target + SUMIF
    new_opening = round(target + sumif.get(name, 0.0), 2)
    col_letter  = chr(ord("A") + col_open)          # e.g. "E"
    cell        = f"{col_letter}{i}"
    updates.append({"range": cell, "values": [[new_opening]]})
    print(f"  {name:<48}  opening → {new_opening:>14,.2f}  (target {target:>14,.2f})")

if updates:
    ws_acc.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"  ✓ Updated {len(updates)} opening balance(s)")
else:
    print("  Nothing to update")

print(f"\n✓ Full sync complete.")
print(f"  https://docs.google.com/spreadsheets/d/1vZgyStpG5XLjdMy9d3ycOQMD7T2QdBGaMMu689Ip8QY\n")
