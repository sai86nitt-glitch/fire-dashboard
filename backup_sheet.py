#!/usr/bin/env python3
"""
Backup the Google Sheet (Transactions + Accounts + Tags tabs) to a local CSV.
Run manually or via cron. Keeps the last 30 backups; older ones are deleted.

Usage:
    python3 backup_sheet.py              # backup all tabs
    python3 backup_sheet.py --list       # list existing backups
"""

import os, sys, csv, argparse, warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

BASE       = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE, "outputs", "backups")
KEEP_LAST  = 30   # delete backups older than the 30 most recent

sys.path.insert(0, BASE)
from buxfer_to_sheets import get_google_creds, SHEET_ID
import gspread

TABS = ["Transactions", "Accounts", "Tags", "Budgets"]


def list_backups():
    p = Path(BACKUP_DIR)
    if not p.exists():
        print("No backups yet.")
        return
    files = sorted(p.glob("*.csv"), reverse=True)
    for f in files:
        size_kb = f.stat().st_size // 1024
        print(f"  {f.name}  ({size_kb} KB)")
    print(f"\n{len(files)} backup file(s) in {BACKUP_DIR}")


def run_backup():
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("• Connecting to Google Sheet…")
    creds = get_google_creds()
    gc    = gspread.authorize(creds)
    ss    = gc.open_by_key(SHEET_ID)

    total_rows = 0
    for tab in TABS:
        try:
            ws   = ss.worksheet(tab)
            data = ws.get_all_values()
            out  = os.path.join(BACKUP_DIR, f"{stamp}_{tab}.csv")
            with open(out, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(data)
            rows = len(data) - 1
            total_rows += rows
            print(f"  ✓ {tab}: {rows} rows → {os.path.basename(out)}")
        except Exception as e:
            print(f"  ✗ {tab}: {e}")

    # Prune old backups — keep the KEEP_LAST most recent per tab
    for tab in TABS:
        files = sorted(Path(BACKUP_DIR).glob(f"*_{tab}.csv"), reverse=True)
        for old in files[KEEP_LAST:]:
            old.unlink()
            print(f"  🗑  Deleted old backup: {old.name}")

    print(f"\n✓ Backup complete — {total_rows} total rows, stamp={stamp}")
    print(f"  Stored in: {BACKUP_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="List existing backups")
    args = ap.parse_args()

    if args.list:
        list_backups()
    else:
        run_backup()
