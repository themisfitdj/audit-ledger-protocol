"""Bear call spread P&L at expiry — deterministic three-branch math (BRK-126).

Mirror of the bull put spread on the call side. Sell the lower-strike call
(short), buy the higher-strike call (long); net credit received. For calls the
long strike is ABOVE the short strike, so spread_width = long - short.

Three branches:
    spot <= short_strike → full_win  → pnl = +net_credit * 100
    spot >= long_strike  → full_loss → pnl = -(width - net_credit) * 100
    otherwise            → partial   → pnl = (net_credit - (spot - short)) * 100

The partial formula is continuous with both endpoints: at spot == short_strike
it equals the full_win value, and at spot == long_strike it equals the
full_loss value.

Edge case: net_credit <= 0 is not a valid bear call (no credit received) →
returns outcome_class="data_unavailable" with pnl_pct_bp=None.

Returns dict with pnl_dollars, pnl_pct_bp, outcome_class.
"""
from __future__ import annotations


def bear_call_spread_pnl_at_expiry(
    expiry_spot: float,
    short_strike: float,
    long_strike: float,
    net_credit: float,
) -> dict:
    """Return P&L for a bear call spread held to expiry."""
    if net_credit <= 0:
        return {
            "pnl_dollars": None,
            "pnl_pct_bp": None,
            "outcome_class": "data_unavailable",
        }

    spread_width = long_strike - short_strike
    bp_required = (spread_width - net_credit) * 100.0

    if expiry_spot <= short_strike:
        pnl_dollars = net_credit * 100.0
        outcome_class = "full_win"
    elif expiry_spot >= long_strike:
        pnl_dollars = -(spread_width - net_credit) * 100.0
        outcome_class = "full_loss"
    else:
        pnl_dollars = (net_credit - (expiry_spot - short_strike)) * 100.0
        outcome_class = "partial"

    return {
        "pnl_dollars": pnl_dollars,
        "pnl_pct_bp": pnl_dollars / bp_required,
        "outcome_class": outcome_class,
    }
