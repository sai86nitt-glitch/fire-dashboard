#!/usr/bin/env python3
"""
Shared transaction tagging rules engine.
Loads tagging_rules.json and applies rules to a transaction description.

Usage:
    from txn_rules import apply_tagging_rules

    new_tags, new_type, new_transfer_to = apply_tagging_rules(
        description   = "Indian Clearing Corporation Limited",
        existing_tags = "Investment",
        existing_type = "transfer",
    )
    # new_tags       → "Investment"        (merged, deduped)
    # new_type       → "transfer"          (set_type from rule)
    # new_transfer_to→ "OneTreeHill"       (transfer_to from rule)
"""

import json
import os
import re

_RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tagging_rules.json")
_rules_cache = None


def _load_rules():
    global _rules_cache
    if _rules_cache is None:
        with open(_RULES_FILE) as f:
            raw = json.load(f)
        # Strip comment-only entries (no "value" key)
        _rules_cache = [r for r in raw if "value" in r]
    return _rules_cache


def _matches(rule, description):
    """Return True if description satisfies the rule's match condition."""
    value = rule["value"]
    desc  = description
    match_type = rule.get("match", "contains")

    if match_type == "phrase":
        # Case-insensitive whole-word / phrase boundary match
        pattern = r'(?i)\b' + re.escape(value) + r'\b'
    else:
        # Case-insensitive simple substring
        pattern = r'(?i)' + re.escape(value)

    return bool(re.search(pattern, desc))


def apply_tagging_rules(description, existing_tags="", existing_type="", existing_transfer_to=""):
    """
    Apply all matching rules to a transaction.

    Returns:
        (merged_tags, effective_type, effective_transfer_to)
        - merged_tags        : comma-separated string, existing + rule-added (deduped, order preserved)
        - effective_type     : first set_type rule that fires, else existing_type
        - effective_transfer_to : first transfer_to rule that fires, else existing_transfer_to
    """
    rules = _load_rules()

    # Start from existing values
    tag_list   = [t.strip() for t in existing_tags.split(",") if t.strip()] if existing_tags else []
    eff_type   = existing_type
    eff_xfer_to = existing_transfer_to

    for rule in rules:
        if not _matches(rule, description):
            continue

        # Add new tags (dedup, preserve order)
        for tag in rule.get("add_tags", []):
            if tag not in tag_list:
                tag_list.append(tag)

        # set_type: first matching rule wins
        if "set_type" in rule and not eff_type:
            eff_type = rule["set_type"]

        # transfer_to: first matching rule wins
        if "transfer_to" in rule and not eff_xfer_to:
            eff_xfer_to = rule["transfer_to"]

    return ", ".join(tag_list), eff_type or existing_type, eff_xfer_to
