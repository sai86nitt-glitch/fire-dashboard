#!/usr/bin/env python3
"""
statements_to_buxfer.py — Push parsed bank statements TO Buxfer.

Flow:
  1. Login to Buxfer API
  2. Fetch your existing Buxfer transactions (for dedup)
  3. Parse all statements in bankstatements/ using the same parsers
     as statements_to_sheets.py
  4. Skip any transaction that already exists in Buxfer
     (matched on date + abs_amount + account)
  5. POST genuinely new transactions via /api/transaction_add

Run:
  python3 statements_to_buxfer.py

Flags:
  --dry-run   Print what would be uploaded without actually posting
  --limit N   Upload at most N transactions (useful for testing)
"""

import sys
import os
import argparse
import time
from getpass import getpass
from collections import defaultdict

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Re-use all the parsers and helpers from statements_to_sheets
from statements_to_sheets import (
    canonical_account, make_id, make_row, norm_date, clean_amount,
    parse_axis_cc_xlsx, parse_icici_cc_csv, parse_activity_csv,
    parse_icici_savings_csv, parse_axis_savings_csv,
    parse_auto_loan_pdf, parse_demat_pdf,
    TXN_HEADERS,
)

API_BASE = "https://www.buxfer.com/api"

# Column positions in TXN_HEADERS (0-based)
COL_ID      = TXN_HEADERS.index("id")
COL_DESC    = TXN_HEADERS.index("description")
COL_AMT     = TXN_HEADERS.index("amount")
COL_DATE    = TXN_HEADERS.index("date")
COL_TYPE    = TXN_HEADERS.index("type")
COL_ACCT    = TXN_HEADERS.index("account_name")
COL_TAGS    = TXN_HEADERS.index("tags")


# ─── Buxfer auth ──────────────────────────────────────────────────────────────

def buxfer_login(email: str, password: str) -> str:
    resp = requests.post(f"{API_BASE}/login",
                         data={"userid": email, "password": password}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("response", {}).get("status") != "OK":
        raise SystemExit(f"Login failed: {data}")
    print("✓ Logged in to Buxfer")
    return data["response"]["token"]


def fetch_accounts(token: str) -> dict:
    """Return {canonical_account_name: buxfer_account_id}."""
    resp = requests.get(f"{API_BASE}/accounts", params={"token": token}, timeout=15)
    resp.raise_for_status()
    accounts = resp.json().get("response", {}).get("accounts", [])
    return {a["name"]: a["id"] for a in accounts}


def fetch_existing_transactions(token: str) -> set:
    """
    Return a set of (date, abs_amount_str, account_name) tuples.
    Used to skip transactions already in Buxfer.
    """
    existing = set()
    page = 1
    total = None
    print("  Fetching existing Buxfer transactions for dedup…")
    while True:
        resp = requests.get(
            f"{API_BASE}/transactions",
            params={"token": token, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("response", {})
        if data.get("status") != "OK":
            break
        txns = data.get("transactions", [])
        if not txns:
            break
        for t in txns:
            key = (
                str(t.get("date", ""))[:10],
                str(round(abs(float(t.get("amount", 0))), 2)),
                str(t.get("accountName", "")),
            )
            existing.add(key)
        if total is None:
            total = int(data.get("numTransactions", 0))
        print(f"    fetched {len(existing)}/{total}…", end="\r")
        if len(existing) >= total:
            break
        page += 1
        time.sleep(0.3)   # be polite to the API
    print(f"\n  ✓ {len(existing)} existing transactions loaded")
    return existing


# ─── Statement discovery ──────────────────────────────────────────────────────

STMT_DIR = os.path.join(BASE_DIR, "bankstatements")

def _collect_statements():
    """
    Walk bankstatements/ and return list of (path, parser_fn) pairs.
    Mirrors the routing logic in statements_to_sheets.main().
    """
    if not os.path.isdir(STMT_DIR):
        raise SystemExit(f"bankstatements/ not found at {STMT_DIR}")

    files = []
    for fname in sorted(os.listdir(STMT_DIR)):
        path = os.path.join(STMT_DIR, fname)
        fl   = fname.lower()

        if fl.endswith(".xlsx"):
            files.append((path, parse_axis_cc_xlsx))
        elif fl.endswith(".csv"):
            name_upper = fname.upper()
            if "CREDITCARD" in name_upper or "CC_STATEMENT" in name_upper.replace(" ", ""):
                files.append((path, parse_icici_cc_csv))
            elif "ACTIVITY" in name_upper or "AXIS" in name_upper:
                files.append((path, parse_axis_savings_csv))
            elif "ICICI" in name_upper or "EStatement_M3" in fname:
                files.append((path, parse_icici_savings_csv))
            else:
                files.append((path, parse_activity_csv))
        elif fl.endswith(".pdf"):
            if "autoloan" in fl or "loan" in fl:
                files.append((path, parse_auto_loan_pdf))
            # Demat PDFs track unit quantities (ETF/shares), not ₹ amounts —
            # they don't map to Buxfer transactions so we skip them here.

    return files


def parse_all_statements() -> list:
    """Parse every statement file → list of row lists (TXN_HEADERS order)."""
    files   = _collect_statements()
    all_rows = []
    for path, parser in files:
        fname = os.path.basename(path)
        try:
            rows = parser(path)
            if rows:
                print(f"  ✓ {fname}: {len(rows)} transactions")
                all_rows.extend(rows)
        except Exception as e:
            print(f"  ✗ {fname}: {e}")
    return all_rows


# ─── Upload ───────────────────────────────────────────────────────────────────

def upload_transaction(token: str, row: list, acct_map: dict) -> bool:
    """
    POST one transaction row to Buxfer /api/transaction_add.
    Returns True on success.
    """
    account_name = str(row[COL_ACCT])
    account_id   = acct_map.get(account_name)

    if not account_id:
        # Try to find a close match (Buxfer names can have extra text)
        for bname, bid in acct_map.items():
            if account_name in bname or bname in account_name:
                account_id = bid
                break

    if not account_id:
        print(f"    ⚠ No Buxfer account ID for '{account_name}' — skipped")
        return False

    amount = abs(float(row[COL_AMT]))
    txn_type = str(row[COL_TYPE])
    # Buxfer expects positive amounts for expense; negative for income
    post_amount = amount if txn_type == "expense" else -amount

    payload = {
        "token":       token,
        "description": str(row[COL_DESC])[:200],
        "amount":      post_amount,
        "accountId":   account_id,
        "date":        str(row[COL_DATE])[:10],
        "type":        txn_type,
        "status":      "cleared",
    }
    tags = str(row[COL_TAGS]).strip()
    if tags and tags not in ("", "nan"):
        payload["tags"] = tags

    try:
        resp = requests.post(f"{API_BASE}/transaction_add", data=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("response", {}).get("status") == "OK":
            return True
        print(f"    ✗ API error: {data.get('response', {}).get('status')}")
        return False
    except Exception as e:
        print(f"    ✗ Request error: {e}")
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Push bank statements to Buxfer")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be uploaded, don't POST")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max transactions to upload (for testing)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip confirmation prompt and upload immediately")
    args = parser.parse_args()

    print("\n=== statements_to_buxfer ===\n")

    # ── Credentials ──────────────────────────────────────────────────────────
    email    = input("Buxfer email: ").strip()
    password = getpass("Buxfer password: ")

    token    = buxfer_login(email, password)
    acct_map = fetch_accounts(token)
    print(f"  {len(acct_map)} Buxfer accounts loaded: {list(acct_map.keys())}")

    # ── Existing transactions (for dedup) ─────────────────────────────────────
    existing = fetch_existing_transactions(token)

    # ── Parse statements ──────────────────────────────────────────────────────
    print("\nParsing statements…")
    all_rows = parse_all_statements()
    print(f"\n  {len(all_rows)} total parsed rows")

    # ── Dedup ─────────────────────────────────────────────────────────────────
    new_rows = []
    skipped  = 0
    for row in all_rows:
        key = (
            str(row[COL_DATE])[:10],
            str(round(abs(float(row[COL_AMT])), 2)),
            str(row[COL_ACCT]),
        )
        if key in existing:
            skipped += 1
        else:
            new_rows.append(row)

    print(f"  {skipped} already in Buxfer → skipped")
    print(f"  {len(new_rows)} new transactions to upload")

    if not new_rows:
        print("\n✓ Nothing new to upload. Buxfer is already up to date.")
        return

    if args.limit:
        new_rows = new_rows[:args.limit]
        print(f"  (capped at {args.limit} for this run)")

    # ── Preview ───────────────────────────────────────────────────────────────
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Uploading {len(new_rows)} transactions:\n")
    for i, row in enumerate(new_rows[:10]):
        print(f"  {row[COL_DATE]}  {str(row[COL_DESC])[:45]:<45}  "
              f"₹{abs(float(row[COL_AMT])):>10,.2f}  {row[COL_ACCT]}")
    if len(new_rows) > 10:
        print(f"  … and {len(new_rows) - 10} more")

    if args.dry_run:
        print("\n[dry-run] No changes made.")
        return

    if not args.yes:
        confirm = input(f"\nUpload {len(new_rows)} transactions to Buxfer? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    # ── Upload ────────────────────────────────────────────────────────────────
    succeeded_rows = []
    failed_rows    = []

    for i, row in enumerate(new_rows, 1):
        desc  = str(row[COL_DESC])[:40]
        date  = str(row[COL_DATE])[:10]
        amt   = abs(float(row[COL_AMT]))
        acct  = str(row[COL_ACCT])
        print(f"  [{i}/{len(new_rows)}] {date}  {desc:<40}  ₹{amt:>10,.2f}  {acct}",
              end="  ")
        ok = upload_transaction(token, row, acct_map)
        if ok:
            succeeded_rows.append(row)
            print("✓")
        else:
            failed_rows.append(row)
            print("✗")
        time.sleep(0.4)   # ~2.5 req/s — stay well within rate limits

    # ── Save upload log (for reverting if needed) ─────────────────────────────
    if succeeded_rows:
        import json as _json
        from datetime import datetime as _dt
        log_dir  = os.path.join(BASE_DIR, "outputs", "buxfer_uploads")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"uploaded_{_dt.now().strftime('%Y-%m-%d_%H%M%S')}.json")
        log_data = {
            "uploaded_at": _dt.now().isoformat(),
            "count": len(succeeded_rows),
            "transactions": [
                {
                    "id":      row[COL_ID],
                    "date":    row[COL_DATE],
                    "desc":    row[COL_DESC],
                    "amount":  row[COL_AMT],
                    "type":    row[COL_TYPE],
                    "account": row[COL_ACCT],
                    "tags":    row[COL_TAGS],
                }
                for row in succeeded_rows
            ],
        }
        with open(log_file, "w") as f:
            _json.dump(log_data, f, indent=2, default=str)
        print(f"\n  💾 Upload log saved → {log_file}")
        print(f"     (use this to identify and delete any incorrect uploads)")

    print(f"\n{'='*60}")
    print(f"  ✓ Uploaded:  {len(succeeded_rows)}")
    print(f"  ✗ Failed:    {len(failed_rows)}")
    print(f"  ⟳ Skipped:   {skipped} (already in Buxfer)")
    print(f"{'='*60}\n")
    if len(failed_rows):
        print(f"  Failed transactions written to log too — check {log_file}")
    print("Run sync_all.py next to pull everything back into the Google Sheet.\n")


if __name__ == "__main__":
    main()
