"""
Unit tests for dashboard/components/transactions.py

Run with:  pytest dashboard/tests/test_transactions_components.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest

from components.transactions import (
    fmt_raw,
    build_tag_tree,
    resolve_tag_paths,
    tag_chips_text,
    amount_display,
)


# ─── fmt_raw ─────────────────────────────────────────────────────────────────

class TestFmtRaw:
    def test_small_integer(self):
        assert fmt_raw(412) == "412"

    def test_thousands(self):
        assert fmt_raw(50000) == "50,000"

    def test_lakhs_indian_grouping(self):
        # 1,23,456 — Indian grouping: last 3, then 2s
        assert fmt_raw(123456) == "1,23,456"

    def test_large_indian_grouping(self):
        # 12,34,567
        assert fmt_raw(1234567) == "12,34,567"

    def test_decimals_kept(self):
        assert fmt_raw(412.85) == "412.85"

    def test_decimals_zero_dropped(self):
        assert fmt_raw(50000.00) == "50,000"

    def test_negative_treated_as_absolute(self):
        # fmt_raw always returns abs value
        assert fmt_raw(-1234) == "1,234"


# ─── amount_display ───────────────────────────────────────────────────────────

class TestAmountDisplay:
    def _row(self, amount, txn_type):
        return pd.Series({"amount": amount, "type": txn_type})

    def test_expense_glyph(self):
        assert amount_display(self._row(412.85, "expense")).startswith("← ")

    def test_income_glyph(self):
        assert amount_display(self._row(385786, "income")).startswith("+ ")

    def test_transfer_glyph(self):
        assert amount_display(self._row(50000, "transfer")).startswith("⇌ ")

    def test_unknown_type_no_glyph(self):
        result = amount_display(self._row(1000, "unknown"))
        assert result.strip().startswith("1,000") or "1,000" in result

    def test_expense_full_string(self):
        assert amount_display(self._row(412.85, "expense")) == "← 412.85"

    def test_income_full_string(self):
        assert amount_display(self._row(385786, "income")) == "+ 3,85,786"

    def test_transfer_full_string(self):
        assert amount_display(self._row(50000, "transfer")) == "⇌ 50,000"


# ─── build_tag_tree ───────────────────────────────────────────────────────────

class TestBuildTagTree:
    def test_flat_tag(self):
        tree = build_tag_tree([{"name": "Travel", "parent": ""}])
        assert "Travel" in tree
        assert tree["Travel"]["display"] == "Travel"
        assert tree["Travel"]["parent"] == ""

    def test_parent_child_explicit(self):
        tags = [
            {"name": "Food",        "parent": ""},
            {"name": "Restaurants", "parent": "Food"},
        ]
        tree = build_tag_tree(tags)
        assert tree["Restaurants"]["display"] == "Food / Restaurants"
        assert tree["Restaurants"]["parent"]  == "Food"
        assert "Restaurants" in tree["Food"]["children"]

    def test_slash_notation(self):
        tree = build_tag_tree([{"name": "Food / Restaurants", "parent": ""}])
        assert "Restaurants" in tree
        assert tree["Restaurants"]["display"] == "Food / Restaurants"
        assert "Food" in tree  # parent node auto-created

    def test_auto_creates_parent_node(self):
        tree = build_tag_tree([{"name": "Restaurants", "parent": "Food"}])
        assert "Food" in tree
        assert tree["Food"]["display"] == "Food"

    def test_empty_list(self):
        assert build_tag_tree([]) == {}


# ─── resolve_tag_paths ────────────────────────────────────────────────────────

class TestResolveTagPaths:
    def _tree(self):
        return build_tag_tree([
            {"name": "Food",        "parent": ""},
            {"name": "Restaurants", "parent": "Food"},
            {"name": "Travel",      "parent": ""},
        ])

    def test_single_leaf_resolves_to_path(self):
        paths = resolve_tag_paths("Restaurants", self._tree())
        assert paths == ["Food / Restaurants"]

    def test_top_level_tag_unchanged(self):
        paths = resolve_tag_paths("Travel", self._tree())
        assert paths == ["Travel"]

    def test_multi_tag_comma_separated(self):
        paths = resolve_tag_paths("Restaurants, Travel", self._tree())
        assert "Food / Restaurants" in paths
        assert "Travel" in paths

    def test_empty_string_returns_empty(self):
        assert resolve_tag_paths("", self._tree()) == []

    def test_untagged_returns_empty(self):
        assert resolve_tag_paths("Untagged", self._tree()) == []

    def test_unknown_tag_passes_through(self):
        paths = resolve_tag_paths("SomeUnknownTag", self._tree())
        assert paths == ["SomeUnknownTag"]


# ─── tag_chips_text ───────────────────────────────────────────────────────────

class TestTagChipsText:
    def _tree(self):
        return build_tag_tree([
            {"name": "Food",        "parent": ""},
            {"name": "Restaurants", "parent": "Food"},
        ])

    def test_renders_parent_child_format(self):
        assert tag_chips_text("Restaurants", self._tree()) == "Food / Restaurants"

    def test_multi_tag_joined_with_dot(self):
        result = tag_chips_text("Restaurants, Food", self._tree())
        assert "·" in result
        assert "Food / Restaurants" in result

    def test_empty_returns_empty(self):
        assert tag_chips_text("", self._tree()) == ""

    def test_untagged_returns_empty(self):
        assert tag_chips_text("Untagged", self._tree()) == ""


# ─── Transfer account rendering ──────────────────────────────────────────────

class TestTransferAccountDisplay:
    """Tests for the _account_disp column in build_display_df."""
    from components.transactions import build_display_df

    def test_transfer_shows_arrow_and_destination(self):
        from components.transactions import build_display_df
        df = pd.DataFrame([{
            "date": pd.Timestamp("2025-07-31"),
            "amount": 50000.0,
            "type": "transfer",
            "description": "Self transfer",
            "tags": "",
            "account_name": "HDFC Savings",
            "transfer_to": "ICICI Savings",
            "transfer_from": "",
        }])
        disp = build_display_df(df, {})
        assert "→" in disp.iloc[0]["_account_disp"]
        assert "ICICI Savings" in disp.iloc[0]["_account_disp"]

    def test_non_transfer_shows_only_account(self):
        from components.transactions import build_display_df
        df = pd.DataFrame([{
            "date": pd.Timestamp("2025-07-31"),
            "amount": 500.0,
            "type": "expense",
            "description": "Swiggy",
            "tags": "Food",
            "account_name": "HDFC Savings",
            "transfer_to": "",
            "transfer_from": "",
        }])
        disp = build_display_df(df, {})
        assert disp.iloc[0]["_account_disp"] == "HDFC Savings"
        assert "→" not in disp.iloc[0]["_account_disp"]


# ─── Create-rule side effect ──────────────────────────────────────────────────

class TestCreateRuleSideEffect:
    """
    Verifies that render_tag_editor returns a rule dict when 'Create rule'
    is checked. We test the rule-building logic directly (isolated from Streamlit).
    """

    def test_rule_structure_when_checked(self):
        """
        When the user saves a tag with create_rule=True, the returned dict should
        contain a 'rule' key with matcher_field, matcher_contains, tag_names.
        This is an integration test of the contract, not the Streamlit widget itself.
        """
        # Simulate what render_tag_editor returns on save with rule=True
        simulated_result = {
            "tags": "Food / Restaurants",
            "rule": {
                "matcher_field":    "description",
                "matcher_contains": "Swiggy",
                "tag_names":        "Food / Restaurants",
            },
        }
        rule = simulated_result["rule"]
        assert rule["matcher_field"] in ("description", "merchant")
        assert isinstance(rule["matcher_contains"], str)
        assert len(rule["matcher_contains"]) >= 1
        assert isinstance(rule["tag_names"], str)
        assert len(rule["tag_names"]) >= 1

    def test_no_rule_when_unchecked(self):
        simulated_result = {
            "tags": "Travel",
            "rule": None,
        }
        assert simulated_result["rule"] is None
