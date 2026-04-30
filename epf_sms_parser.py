#!/usr/bin/env python3
"""
EPF SMS Parser — recalibrate portfolio_config.json from EPFO SMS text.

Usage:
    python3 epf_sms_parser.py

    Paste the SMS text when prompted (end with a blank line or Ctrl-D).

Example SMS format:
    Dear Member,Your UAN:XXXXXXXX0632,...,LAST CONTRIBUTION:72806, TOTAL BALANCE:11618040. Team EPFO
"""

import json
import os
import re
import sys
from datetime import date

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_config.json")


def parse_epfo_sms(text: str) -> dict:
    """Extract LAST CONTRIBUTION and TOTAL BALANCE from EPFO SMS string."""
    text = text.replace("\n", " ").strip()

    balance_match = re.search(r'TOTAL\s+BALANCE\s*[:\-]\s*([\d,]+)', text, re.IGNORECASE)
    contrib_match = re.search(r'LAST\s+CONTRIBUTION\s*[:\-]\s*([\d,]+)', text, re.IGNORECASE)

    if not balance_match:
        raise ValueError("Could not find TOTAL BALANCE in SMS text.")
    if not contrib_match:
        raise ValueError("Could not find LAST CONTRIBUTION in SMS text.")

    balance = int(balance_match.group(1).replace(",", ""))
    contrib = int(contrib_match.group(1).replace(",", ""))
    return {"total_balance": balance, "last_monthly_contribution": contrib}


def update_config(parsed: dict):
    with open(CONFIG_FILE) as f:
        config = json.load(f)

    old_balance = config["epf"]["total_balance"]
    old_contrib = config["epf"]["last_monthly_contribution"]

    config["epf"]["total_balance"]            = parsed["total_balance"]
    config["epf"]["last_monthly_contribution"] = parsed["last_monthly_contribution"]
    config["epf"]["last_updated"]             = date.today().isoformat()

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n✓ EPF config updated ({CONFIG_FILE})")
    print(f"  Balance:      {old_balance:>14,.0f}  →  {parsed['total_balance']:>14,.0f}")
    print(f"  Contribution: {old_contrib:>14,.0f}  →  {parsed['last_monthly_contribution']:>14,.0f}")
    print(f"  Last updated: {date.today().isoformat()}")


def main():
    print("Paste the EPFO SMS text below.")
    print("Press Enter twice (or Ctrl-D on Mac) when done:\n")

    lines = []
    try:
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
    except EOFError:
        pass

    text = "\n".join(lines).strip()
    if not text:
        print("No input received. Exiting.")
        sys.exit(1)

    try:
        parsed = parse_epfo_sms(text)
    except ValueError as e:
        print(f"\n✗ Parse error: {e}")
        sys.exit(1)

    print(f"\nParsed:")
    print(f"  Total Balance:       ₹{parsed['total_balance']:,.0f}")
    print(f"  Last Contribution:   ₹{parsed['last_monthly_contribution']:,.0f}")

    confirm = input("\nUpdate portfolio_config.json with these values? [y/N] ").strip().lower()
    if confirm == "y":
        update_config(parsed)
    else:
        print("Aborted. No changes made.")


if __name__ == "__main__":
    main()
