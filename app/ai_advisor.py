"""
AI-powered portfolio advisor — calls Claude to analyse allocation vs targets
and surface rebalancing nudges. Response is cached for 1 hour to control cost.

Requires ANTHROPIC_API_KEY env var. Degrades gracefully when absent.
"""

import json
import os
import time

_CACHE_TTL = 3600  # seconds
_cache: dict = {"ts": 0, "key": None, "data": None}


def get_portfolio_advice(context: dict) -> dict | None:
    """
    Call Claude Haiku with a portfolio snapshot; return structured advice dict.
    Returns None when ANTHROPIC_API_KEY is not configured.

    context keys expected:
        nw_cr          float  — net worth in crores
        fire_pct       float  — FIRE progress %
        equity_pct     float  — current equity allocation %
        metals_pct     float  — gold+silver %
        epf_pct        float  — EPF/debt %
        cash_pct       float  — cash/arbitrage %
        monthly_sip    float  — detected monthly SIP ₹
        fire_target_cr float  — FIRE corpus needed (Cr)
        edu_target_cr  float  — education corpus needed (Cr)
        yrs_to_fire    int
        yrs_to_edu     int
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    # Cache keyed on rounded NW to avoid thrashing on minor data changes
    cache_key = round(context.get("nw_cr", 0), 1)
    now = time.time()
    if (_cache["data"] is not None
            and (now - _cache["ts"]) < _CACHE_TTL
            and _cache["key"] == cache_key):
        return _cache["data"]

    prompt = (
        "You are a concise FIRE-focused financial advisor for an Indian investor.\n"
        "Analyse the portfolio snapshot below against the investor profile and return "
        "actionable advice as JSON — no markdown, no explanation, just the JSON object.\n\n"
        f"Portfolio snapshot:\n{json.dumps(context, indent=2)}\n\n"
        "Investor profile:\n"
        "- FIRE target 2036 (10 yrs), 25× annual spend ≈ ₹16.56 Cr\n"
        "- Education goal 2041 (15 yrs), ~$4L USD\n"
        "- Monthly SIPs: ₹2.5L equity MF + ₹30K metals + ₹72K EPF\n"
        "- Target allocation: equity MF 70-75 %, international 10-15 % of equity, "
        "metals 5-8 %, EPF/debt 20-25 %, cash/arbitrage minimal\n\n"
        "Hard rules — always enforce:\n"
        "1. Never suggest lump-sum equity deployment; recommend STP for any sum > ₹2 L\n"
        "2. Flag overweight equity if equity_pct > 80\n"
        "3. Flag underweight EPF/debt if (epf_pct + cash_pct) < 15\n"
        "4. Flag if FIRE progress is > 6 months behind the 12 % CAGR straight-line path\n"
        "5. If everything looks fine, say so clearly\n\n"
        "Return exactly this JSON shape:\n"
        '{"status":"on_track"|"attention"|"action_needed",'
        '"headline":"single sentence ≤ 15 words",'
        '"points":["point 1 ≤ 12 words","point 2","point 3"],'
        '"color":"success"|"warning"|"danger"}'
    )

    try:
        import anthropic  # lazy import — not required if key absent
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        advice = json.loads(raw.strip())
        _cache.update(ts=now, key=cache_key, data=advice)
        return advice
    except Exception as exc:
        return {
            "status": "error",
            "headline": "Advisor temporarily unavailable",
            "points": [str(exc)[:80]],
            "color": "secondary",
        }
