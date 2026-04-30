#!/usr/bin/env python3

import requests
import json
import os
import sys
from datetime import datetime
from getpass import getpass

API_BASE = "https://www.buxfer.com/api"

def login(email, password):
    try:
        resp = requests.post(f"{API_BASE}/login", data={"userid": email, "password": password})
        resp.raise_for_status()
        data = resp.json()
        if data.get("response", {}).get("status") != "OK":
            raise Exception(f"Login failed: {data.get('response', {}).get('status')}")
        print("✓ Logged in successfully")
        return data["response"]["token"]
    except Exception as e:
        print(f"✗ Login error: {e}")
        sys.exit(1)

def fetch_data(endpoint, token):
    try:
        resp = requests.get(f"{API_BASE}/{endpoint}", params={"token": token})
        resp.raise_for_status()
        data = resp.json()
        if data.get("response", {}).get("status") != "OK":
            return {}
        return data.get("response", {})
    except Exception as e:
        print(f"✗ Error fetching {endpoint}: {e}")
        return {}

def fetch_all_transactions(token):
    all_transactions = []
    page = 1
    total = None

    while True:
        try:
            resp = requests.get(
                f"{API_BASE}/transactions",
                params={"token": token, "page": page}
            )
            resp.raise_for_status()
            data = resp.json().get("response", {})

            if data.get("status") != "OK":
                break

            page_txns = data.get("transactions", [])
            if not page_txns:
                break

            all_transactions.extend(page_txns)

            if total is None:
                total = int(data.get("numTransactions", 0))

            print(f"  fetched {len(all_transactions)}/{total}...", end="\r")

            if len(all_transactions) >= total:
                break

            page += 1

        except Exception as e:
            print(f"\n✗ Error on page {page}: {e}")
            break

    print()
    return all_transactions

def main():
    print("\n=== Buxfer Data Extractor ===\n")

    email = input("Buxfer email: ").strip()
    password = getpass("Buxfer password: ")

    if not email or not password:
        print("✗ Email and password required")
        sys.exit(1)

    print("\nExtracting data...\n")
    token = login(email, password)

    print("• Fetching accounts...")
    accounts_resp = fetch_data("accounts", token)
    accounts = accounts_resp.get("accounts", [])

    print("• Fetching all transactions (this may take a while)...")
    transactions = fetch_all_transactions(token)

    print("• Fetching budgets...")
    budgets_resp = fetch_data("budgets", token)
    budgets = budgets_resp.get("budgets", [])

    print("• Fetching tags...")
    tags_resp = fetch_data("tags", token)
    tags = tags_resp.get("tags", [])

    backup = {
        "exportDate": datetime.now().isoformat(),
        "accounts": accounts,
        "transactions": transactions,
        "budgets": budgets,
        "tags": tags
    }

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"buxfer_backup_{datetime.now().strftime('%Y-%m-%d')}.json")

    with open(filename, "w") as f:
        json.dump(backup, f, indent=2)

    print(f"\n✓ Backup complete!\n")
    print(f"📊 Summary:")
    print(f"   Accounts:     {len(accounts)}")
    print(f"   Transactions: {len(transactions)}")
    print(f"   Budgets:      {len(budgets)}")
    print(f"   Tags:         {len(tags)}")
    print(f"\n💾 Saved to: {filename}\n")

if __name__ == "__main__":
    main()
