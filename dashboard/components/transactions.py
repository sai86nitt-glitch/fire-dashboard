"""
TransactionTable sub-components for the FIRE Dashboard.

Subcomponents
─────────────
  fmt_raw(value)                   → Indian-grouped number, no ₹, drops .00
  amount_display(row)              → "← 1,23,456" / "+ 50,000" / "⇌ 10,000"
  build_tag_tree(tag_objects)      → {name: {display, parent, children}}
  resolve_tag_paths(tag_str, tree) → ["Food / Restaurants", "Travel"]
  tag_chips_text(tag_str, tree)    → "Food / Restaurants · Travel"
  render_bulk_bar(n, key)          → returns clicked action string or None
  render_pagination(total, key)    → renders controls, returns 0-based page index
  render_tag_editor(row, …, key)   → returns {"tags": str, "rule": dict|None} or None
  render_expanded_row(row, key)    → renders inline details + per-row toolbar
  inject_keyboard_nav(total, key)  → injects j/k keyboard JS
"""

import re
import streamlit as st
import pandas as pd
from typing import Optional
import streamlit.components.v1 as stc

# ─── Amount formatting ────────────────────────────────────────────────────────

# glyph, full CSS property:value string (pandas Styler requires this format)
_GLYPH: dict[str, tuple[str, str]] = {
    "income":   ("+ ",  "color: #00c49f"),
    "transfer": ("⇌ ",  "color: #ffa94d"),
    "expense":  ("← ",  "color: #ff6b6b"),
}


def fmt_raw(value: float) -> str:
    """
    Indian number grouping (last 3, then 2s), no currency symbol.
    Drops trailing .00; shows 2 dp otherwise.

    Examples: 1234567 → "12,34,567"  |  412.85 → "412.85"  |  50000 → "50,000"
    """
    n = abs(value)
    # Decimal part
    frac = round(n - int(n), 2)
    dec_str = f"{frac:.2f}"[1:] if frac else ""   # ".85" or ""

    # Integer grouping
    s = str(int(n))
    if len(s) <= 3:
        return s + dec_str
    last3 = s[-3:]
    rest   = s[:-3]
    groups: list[str] = []
    while rest:
        groups.append(rest[-2:] if len(rest) >= 2 else rest)
        rest = rest[:-2] if len(rest) >= 2 else ""
    return ",".join(reversed(groups)) + "," + last3 + dec_str


def amount_display(row: pd.Series) -> str:
    """Return glyph-prefixed amount string for the Amount column."""
    glyph, _ = _GLYPH.get(str(row.get("type", "")), ("  ", ""))
    return f"{glyph}{fmt_raw(row.get('amount', 0))}"


def amount_colour(txn_type: str) -> str:
    """Returns a full CSS property:value string, e.g. 'color: #ff6b6b'."""
    _, css = _GLYPH.get(str(txn_type), ("  ", "color: #ccc"))
    return css


def amount_css_from_glyph(display_str: str) -> str:
    """
    Infer CSS colour from the glyph prefix — used by pandas Styler .map()
    so we don't need to cross-reference page_df inside the styler function.
    """
    s = str(display_str)
    if s.startswith("+ "):  return "color: #00c49f"
    if s.startswith("⇌ "):  return "color: #ffa94d"
    if s.startswith("← "):  return "color: #ff6b6b"
    return "color: #ccc"


# ─── Tag tree + path resolution ───────────────────────────────────────────────

def build_tag_tree(tag_objects: list) -> dict:
    """
    Build a lookup dict from a list of tag dicts.
    Input: [{"name": "Restaurants", "parent": "Food"}, ...]
           OR slash-notation: [{"name": "Food / Restaurants"}, ...]
    Returns: {
        "Restaurants": {"display": "Food / Restaurants", "parent": "Food", "children": []},
        "Food":        {"display": "Food",               "parent": "",     "children": ["Restaurants"]},
    }
    """
    tree: dict = {}

    for tag in tag_objects:
        raw_name = str(tag.get("name", "")).strip()
        parent   = str(tag.get("parent", "")).strip()
        if not raw_name or raw_name in ("nan", ""):
            continue

        # Slash notation takes priority if no explicit parent
        if "/" in raw_name and not parent:
            parts  = [p.strip() for p in raw_name.split("/", 1)]
            parent = parts[0]
            name   = parts[1]
        else:
            name = raw_name

        display = f"{parent} / {name}" if parent else name
        tree[name] = {"display": display, "parent": parent, "children": []}

        # Ensure parent node exists
        if parent and parent not in tree:
            tree[parent] = {"display": parent, "parent": "", "children": []}

    # Wire children lists
    for name, info in tree.items():
        p = info["parent"]
        if p and p in tree and name not in tree[p]["children"]:
            tree[p]["children"].append(name)

    return tree


def resolve_tag_paths(tag_str: str, tag_tree: dict) -> list[str]:
    """
    Given "Food, Restaurants" returns ["Food", "Food / Restaurants"].
    Falls back to the raw tag name if not found in tree.
    """
    if not tag_str or str(tag_str).strip() in ("", "nan", "Untagged", "None", "none"):
        return []
    tags  = [t.strip() for t in str(tag_str).split(",") if t.strip()]
    paths = []
    for tag in tags:
        info = tag_tree.get(tag, {})
        paths.append(info.get("display", tag))
    return paths


def tag_chips_text(tag_str: str, tag_tree: dict) -> str:
    """Single-line tag display: 'Food / Restaurants · Travel'."""
    paths = resolve_tag_paths(tag_str, tag_tree)
    return " · ".join(paths) if paths else ""


# ─── Pagination ───────────────────────────────────────────────────────────────

def render_pagination(total: int, key: str = "txn_page", page_size: int = 100) -> int:
    """
    Renders  ⟪ ‹ X–Y of N › ⟫  controls.
    State lives in st.session_state[key].
    Returns current 0-based page index.
    """
    if key not in st.session_state:
        st.session_state[key] = 0

    page        = int(st.session_state[key])
    total_pages = max(1, (total + page_size - 1) // page_size)
    page        = min(page, total_pages - 1)
    st.session_state[key] = page

    start = page * page_size + 1
    end   = min((page + 1) * page_size, total)

    # Layout: [label][First][Prev][count][Next][Last]
    c = st.columns([3, 1, 1, 2, 1, 1])
    c[0].caption(f"Rows **{start}–{end}** of **{total:,}**")

    if c[1].button("⟪", key=f"{key}_first", disabled=(page == 0), use_container_width=True):
        st.session_state[key] = 0; st.rerun()
    if c[2].button("‹",  key=f"{key}_prev",  disabled=(page == 0), use_container_width=True):
        st.session_state[key] = page - 1; st.rerun()
    c[3].caption(f"Page **{page+1}** / {total_pages}")
    if c[4].button("›",  key=f"{key}_next",  disabled=(page >= total_pages-1), use_container_width=True):
        st.session_state[key] = page + 1; st.rerun()
    if c[5].button("⟫", key=f"{key}_last",  disabled=(page >= total_pages-1), use_container_width=True):
        st.session_state[key] = total_pages - 1; st.rerun()

    return page


# ─── Bulk action bar ──────────────────────────────────────────────────────────

def render_bulk_bar(n_selected: int, key: str = "bulk") -> Optional[str]:
    """
    Renders [n selected | Edit | Copy | Print | Delete] toolbar.
    Buttons are disabled while n_selected == 0.
    Returns the clicked action name ('edit'/'copy'/'print'/'delete') or None.
    """
    disabled = n_selected == 0
    c = st.columns([3, 1, 1, 1, 1])
    if n_selected:
        c[0].markdown(f"**{n_selected}** row{'s' if n_selected>1 else ''} selected")
    else:
        c[0].caption("Select rows below to act on them")

    action = None
    if c[1].button("✏️ Edit",    key=f"{key}_edit",   disabled=disabled, use_container_width=True):
        action = "edit"
    if c[2].button("📋 Copy",    key=f"{key}_copy",   disabled=disabled, use_container_width=True):
        action = "copy"
    if c[3].button("🖨️ Print",   key=f"{key}_print",  disabled=disabled, use_container_width=True):
        action = "print"
    if c[4].button("🗑️ Delete",  key=f"{key}_delete", disabled=disabled, use_container_width=True):
        action = "delete"
    return action


# ─── Tag editor ───────────────────────────────────────────────────────────────

def render_tag_editor(
    row: pd.Series,
    all_tag_objects: list,      # [{name, parent, display}, ...]
    tag_tree: dict,
    key: str = "tag_ed",
) -> Optional[dict]:
    """
    Inline tag editor. Supports multi-tag selection, new tag creation, and
    a "Create Rule" checkbox that auto-tags future matching transactions.

    Returns
    -------
    None          — no action yet (still editing, or Cancel clicked)
    {"tags": str, "rule": dict|None}  — Save clicked; caller should persist
    {"cancel": True}                  — Cancel clicked explicitly
    """
    current_str = str(row.get("tags", "")).strip()
    current_paths = resolve_tag_paths(current_str, tag_tree)

    all_displays = sorted({t["display"] for t in all_tag_objects})

    desc_short = str(row.get("description", ""))[:60]
    date_str   = row["date"].strftime("%d %b %Y") if hasattr(row.get("date"), "strftime") else str(row.get("date",""))

    st.markdown(
        f"<div style='font-size:12px;color:#aaa;margin-bottom:4px'>"
        f"🏷️ Tagging <b>{date_str}</b> · {desc_short}</div>",
        unsafe_allow_html=True,
    )

    # ── Multi-select (existing tags, fuzzy search built-in to multiselect) ──
    valid_defaults = [p for p in current_paths if p in all_displays]
    selected = st.multiselect(
        "Tags",
        options=all_displays,
        default=valid_defaults,
        key=f"{key}_ms",
        placeholder="Type to search or pick…",
        label_visibility="collapsed",
    )

    # ── Create new tag ──────────────────────────────────────────────────────
    new_tag = st.text_input(
        "New tag (optional, use 'Parent / Child' for hierarchy)",
        key=f"{key}_new",
        placeholder="e.g. Food / Dining Out",
        label_visibility="visible",
    )

    # ── Create Rule checkbox ────────────────────────────────────────────────
    # Extract a short merchant token from the description
    desc_clean = re.sub(r"[^a-zA-Z\s]", " ", str(row.get("description", ""))).strip()
    words = [w for w in desc_clean.split() if len(w) >= 3]
    merchant_token = words[0] if words else ""

    create_rule = False
    if merchant_token:
        create_rule = st.checkbox(
            f'🔁 Create rule — auto-tag future transactions containing **"{merchant_token}"**',
            key=f"{key}_rule_chk",
        )

    # ── Action buttons ──────────────────────────────────────────────────────
    bc1, bc2 = st.columns(2)
    save   = bc1.button("✓ Save",   key=f"{key}_save",   type="primary", use_container_width=True)
    cancel = bc2.button("✕ Cancel", key=f"{key}_cancel", use_container_width=True)

    if cancel:
        return {"cancel": True}

    if save:
        all_tags_chosen = list(selected)
        if new_tag.strip():
            all_tags_chosen.append(new_tag.strip())
        tags_str = ", ".join(all_tags_chosen)
        rule = None
        if create_rule and merchant_token and all_tags_chosen:
            rule = {
                "matcher_field":    "description",
                "matcher_contains": merchant_token,
                "tag_names":        all_tags_chosen[0],
            }
        return {"tags": tags_str, "rule": rule}

    return None   # still open, no action


# ─── Expanded row details ─────────────────────────────────────────────────────

def render_expanded_row(row: pd.Series, key: str = "exp") -> Optional[str]:
    """
    Renders the inline expanded panel beneath a selected row.
    Shows merchant, original description, transfer-to account, and a per-row toolbar.
    Returns clicked action string ('edit'/'copy'/'split'/'memo'/'repeat'/'rule'/'delete') or None.
    """
    desc     = str(row.get("description", ""))
    orig     = str(row.get("originalDescription", desc))   # TODO: add originalDescription to schema
    to_acct  = str(row.get("transfer_to", ""))
    txn_type = str(row.get("type", ""))

    transfer_line = ""
    if to_acct and to_acct not in ("", "nan"):
        transfer_line = (
            f"<div style='margin-top:6px'>"
            f"<span style='color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.05em'>Transfer To</span><br/>"
            f"<span style='color:#ffa94d'>→ {to_acct}</span>"
            f"</div>"
        )

    st.markdown(
        f"""<div style="background:#1a1a2e;border-radius:6px;padding:12px 16px;
                        margin:2px 0 8px 0;font-size:12px;border-left:3px solid #444;">
  <div style="display:flex;gap:40px;flex-wrap:wrap;align-items:flex-start;">
    <div>
      <span style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.05em">Merchant</span><br/>
      <span style="color:#58a6ff;cursor:pointer">{desc}</span>
    </div>
    <div>
      <span style="color:#888;font-size:10px;text-transform:uppercase;letter-spacing:.05em">Original Description</span><br/>
      <span style="color:#999;font-family:monospace;font-size:11px">{orig.upper()}</span>
    </div>
    {transfer_line}
  </div>
  <div style="margin-top:8px;color:#555;font-size:11px;font-style:italic">
    Description was automatically prettified. &nbsp;
    <span style="color:#58a6ff;cursor:pointer">Settings</span> ·
    <span style="color:#58a6ff;cursor:pointer">Learn more</span>
  </div>
</div>""",
        unsafe_allow_html=True,
    )

    # Per-row toolbar
    labels  = ["✏️ Edit", "📋 Copy", "✂️ Split", "📝 Memo", "🔁 Repeat", "➕ Rule", "🗑️ Delete"]
    actions = ["edit",   "copy",   "split",    "memo",   "repeat",   "rule",   "delete"]
    cols    = st.columns(len(labels))
    for col, label, action in zip(cols, labels, actions):
        is_del = action == "delete"
        if col.button(
            label,
            key=f"{key}_{action}",
            use_container_width=True,
            type="primary" if is_del else "secondary",
        ):
            return action
    return None


# ─── Forecast group row ───────────────────────────────────────────────────────

def render_forecast_group() -> None:
    """
    Placeholder for the 3-month forecast group row.
    TODO: populate once isForecast / scheduled transactions are in the data model.
    """
    with st.expander("▸ 3 MONTH FORECAST  ·  0 scheduled transactions", expanded=False):
        st.caption(
            "No scheduled (forecast) transactions yet. "
            "Add recurring rules to see projections here."
        )
        # TODO: when forecast data exists, render rows using the same display columns
        # as the main table, styled with opacity: 0.6 and isForecast=True marker.


# ─── Keyboard navigation JS ───────────────────────────────────────────────────

def inject_keyboard_nav(total_rows: int, number_input_key: str = "kb_row") -> None:
    """
    Injects JavaScript that maps j/k to ±1 on the focused-row number_input.
    Other shortcuts (x/e/t) require a proper Streamlit Component;
    they are documented here as TODOs.

    Keys active only when no input/textarea/select has focus.
    """
    stc.html(
        f"""
<script>
(function() {{
  if (window._txnKbRegistered) return;
  window._txnKbRegistered = true;

  function nudgeNumberInput(delta) {{
    // Find all number inputs in the Streamlit iframe parent
    const doc = window.parent.document;
    const inputs = doc.querySelectorAll('input[type="number"]');
    // Use the first one that visually corresponds to our row selector
    if (!inputs.length) return;
    const inp = inputs[0];
    const cur = parseInt(inp.value) || 1;
    const next = Math.max(1, Math.min({total_rows}, cur + delta));
    if (next === cur) return;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.parent.HTMLInputElement.prototype, 'value').set;
    nativeSetter.call(inp, String(next));
    inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
  }}

  document.addEventListener('keydown', function(ev) {{
    const active = window.parent.document.activeElement;
    if (active && ['INPUT','TEXTAREA','SELECT'].includes(active.tagName)) return;
    // j = down, k = up
    if (ev.key === 'j') {{ ev.preventDefault(); nudgeNumberInput(+1); }}
    if (ev.key === 'k') {{ ev.preventDefault(); nudgeNumberInput(-1); }}
    // TODO (needs React component): x=toggle-select, e=expand/collapse, t=open-tag-editor
    // TODO (needs React component): / = focus global search
  }});
}})();
</script>
""",
        height=0,
    )


# ─── Display-ready dataframe builder ─────────────────────────────────────────

def build_display_df(df: pd.DataFrame, tag_tree: dict) -> pd.DataFrame:
    """
    Derive display columns from the raw transaction dataframe.
    Returns a new df with columns:
      Date | Amount | Description | Tags | Account
    ready to pass to st.dataframe / st.data_editor.
    """
    d = df.copy()

    # Amount column: glyph-prefixed, right-aligned text
    d["_amount_disp"] = d.apply(amount_display, axis=1)

    # Tags column: resolved full paths
    d["_tags_disp"] = d["tags"].apply(lambda t: tag_chips_text(t, tag_tree))

    # Account column: "AccountName" or "AccountName\n→ ToAccount" for transfers
    def acct_display(row):
        name = str(row.get("account_name", ""))
        to   = str(row.get("transfer_to", ""))
        if to and to not in ("", "nan") and str(row.get("type","")) == "transfer":
            return f"{name} → {to}"
        return name

    d["_account_disp"] = d.apply(acct_display, axis=1)

    return d
